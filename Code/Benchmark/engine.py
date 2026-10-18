"""Plays many games at the same time with one model loaded in vLLM (same approach of Self-evaluation/parallel_play.py).

Each game runs in its own thread with its own agent. When the agent needs an answer, its generate() hands the request
to the main thread and waits: the main thread keeps adding the requests to the batch vLLM is generating
(continuous batching), so a game does not wait for the self-evaluations of the other games, and vLLM reuses
the context already read in the previous turns (prefix caching).
The times are wall-clock times of games played together: they are not comparable between runs.
"""
import gc
import itertools
import os
import re
import sys
import threading
import time

_env_lock = threading.Lock() # TextWorld is not thread-safe: one game action at a time
_request_ids = itertools.count()


def make_env(path, max_steps):
    """The TextWorld environment of a game (replaced by a fake one in the tests)."""
    import textworld
    import textworld.gym
    infos = textworld.EnvInfos(admissible_commands=True, max_score=True)
    env_id = textworld.gym.register_games([path], request_infos=infos, max_episode_steps=max_steps)
    return textworld.gym.make(env_id)


def play_game(agent, path, max_steps=100, loop_window=None, loop_max_distinct=2):
    """One episode of the game in path. loop_window: if set, the episode is stopped when its last loop_window turns
    contain at most loop_max_distinct different (observation, command) pairs (the agent is stuck in a loop:
    the game is deterministic, so it would almost never score again).
    Returns a dict with the trajectory [(moves, score, seconds)], the final score, max score, steps and loop flag."""
    with _env_lock:
        env = make_env(path, max_steps)
    try:
        start = time.perf_counter()
        with _env_lock:
            obs, infos = env.reset()
        score, done, moves = 0, False, 0
        trajectory = [(0, 0, 0.0)]
        turns = [] # (observation, command) pairs, for the loop detection
        loop = False
        while not done:
            command = agent.act(obs)
            # without the status bar ("-= Room =-score/moves"), whose move counter changes at every turn
            turns.append((re.sub(r">\s+-=.*", "", obs).strip(), command))
            timestamp = time.perf_counter()
            with _env_lock:
                obs, score, done, infos = env.step(command)
            moves += 1
            trajectory.append((moves, score, round(timestamp - start, 3)))
            if not done and loop_window and len(turns) >= loop_window \
                    and len(set(turns[-loop_window:])) <= loop_max_distinct:
                agent._write_log(f"LOOP DETECTED: episode stopped at step {moves}")
                loop = True
                break
        agent.act(obs, done=True)
        return dict(trajectory=trajectory, score=score, max_score=infos["max_score"], steps=moves,
                    loop_stopped=loop, seconds=round(time.perf_counter() - start, 1))
    finally:
        with _env_lock:
            env.close()


class Engine:
    """A model loaded in vLLM, that plays games with play()."""

    def __init__(self, model, gpu_memory_utilization=0.9, seed=46, **llm_settings):
        """llm_settings: other arguments of vllm.LLM (e.g. max_model_len, kv_cache_dtype, quantization)."""
        os.environ.setdefault("VLLM_ENABLE_V1_MULTIPROCESSING", "0") # engine in this process: no subprocess to spawn from a notebook
        os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")
        from transformers import AutoTokenizer, GenerationConfig
        from vllm import LLM
        import vllm
        self.model = model
        self.tokenizer = AutoTokenizer.from_pretrained(model)
        try:
            config = GenerationConfig.from_pretrained(model)
        except OSError:
            config = None
        # defaults of the sampling, used when the prompt does not set them (as model.generate() did)
        self.default_sampling = {name: getattr(config, name, None) for name in ("temperature", "top_p", "top_k", "min_p")}
        self.default_sampling = {name: value for name, value in self.default_sampling.items() if value is not None}
        self.stop_token_ids = [self.tokenizer.eos_token_id]
        # vLLM silences some messages by redirecting the file descriptor of sys.stdout, which a notebook's output
        # does not have: during the start-up, use the real standard output of the process
        notebook_stdout = sys.stdout
        sys.stdout = sys.__stdout__ if sys.__stdout__ is not None else open(os.devnull, "w")
        try:
            self.llm = LLM(model=model, gpu_memory_utilization=gpu_memory_utilization, enable_prefix_caching=True,
                           seed=seed, **llm_settings)
        finally:
            sys.stdout = notebook_stdout
        self.backend = self.llm.llm_engine
        self.max_model_len = self.llm.llm_engine.model_config.max_model_len
        self.description = dict(backend=f"vllm {vllm.__version__}", gpu_memory_utilization=gpu_memory_utilization,
                                **llm_settings)

    def close(self):
        """Frees the GPU memory, to load another model. If it is not enough (out of memory when loading the next
        model), restart the runtime: the games already played are not played again."""
        import torch
        del self.llm, self.backend
        try:
            from vllm.distributed.parallel_state import destroy_model_parallel, destroy_distributed_environment
            destroy_model_parallel()
            destroy_distributed_environment()
        except Exception:
            pass
        gc.collect()
        torch.cuda.empty_cache()

    def _sampling_params(self, max_tokens, sampling):
        from vllm import SamplingParams
        params = dict(self.default_sampling)
        params.update(sampling or {})
        return SamplingParams(max_tokens=max_tokens, stop_token_ids=self.stop_token_ids, **params)

    def play(self, jobs, n_parallel=8, on_result=None, verbose=True):
        """Plays the jobs, n_parallel games at a time.
        jobs: list of (key, make_agent, play_kwargs): make_agent(generate) returns a new agent that uses generate()
        as its model, play_kwargs are the arguments of play_game() (path, max_steps, loop_window, ...).
        on_result(key, agent, result): called in the main thread as soon as a game ends, with the result of play_game().
        """
        state = _State()
        jobs = list(jobs)
        in_vllm = {} # request id -> request being generated
        start = time.perf_counter()
        generated = 0
        ended = 0
        last_display = 0
        display_handle = None

        def run(key, agent, play_kwargs):
            try:
                outcome = play_game(agent, **play_kwargs)
            except BaseException as e:
                outcome = e
            with state.condition:
                state.finished.append((key, agent, outcome))
                state.running -= 1
                state.condition.notify_all()

        try:
            while jobs or state.running > 0 or state.finished: # finished: the last games ended but are not saved yet
                while jobs and state.running < n_parallel:
                    key, make_agent, play_kwargs = jobs.pop(0)
                    agent = make_agent(state.generate_function(self.max_model_len))
                    with state.condition:
                        state.running += 1
                    threading.Thread(target=run, args=(key, agent, play_kwargs), daemon=True).start()

                with state.condition:
                    if not in_vllm: # nothing to generate: wait until a game asks for an answer or ends
                        state.condition.wait_for(lambda: state.pending or state.finished, timeout=1)
                    new_requests, state.pending = state.pending, []
                    done_now, state.finished = state.finished, []

                for request in new_requests:
                    request_id = str(next(_request_ids))
                    in_vllm[request_id] = request
                    self.backend.add_request(request_id, {"prompt_token_ids": request["ids"]},
                                             self._sampling_params(request["max_tokens"], request["sampling"]))

                for key, agent, outcome in done_now:
                    if isinstance(outcome, BaseException):
                        raise outcome
                    ended += 1
                    if verbose:
                        print(f"{key}: {'loop, ' if outcome['loop_stopped'] else ''}steps {outcome['steps']}, "
                              f"score {outcome['score']}/{outcome['max_score']}, {outcome['seconds']:.0f} s")
                    if on_result is not None:
                        on_result(key, agent, outcome)

                if in_vllm:
                    for output in self.backend.step(): # one generation step of all the requests in the batch
                        if output.finished:
                            request = in_vllm.pop(output.request_id)
                            completion = output.outputs[0]
                            generated += len(completion.token_ids)
                            with state.condition:
                                request["output"] = (list(completion.token_ids), completion.finish_reason == "length")
                                state.condition.notify_all()

                if verbose and time.perf_counter() - last_display > 2: # the loop runs at every generated token
                    last_display = time.perf_counter()
                    elapsed = last_display - start
                    text = f"{state.running} games running, {len(jobs)} waiting, {ended} ended " \
                         + f"({ended / elapsed * 3600:.1f} games/hour); {generated / elapsed * 60:.0f} tokens/min"
                    display_handle = _show(text, display_handle)
        finally:
            if in_vllm:
                self.backend.abort_request(list(in_vllm))
            with state.condition:
                if state.running > 0 and state.error is None:
                    state.error = RuntimeError("stopped") # wakes up the game threads, which then stop
                state.condition.notify_all()


class _State:
    """Requests and results exchanged between the game threads and the main thread."""

    def __init__(self):
        self.condition = threading.Condition()
        self.pending = [] # requests not yet handed to vLLM
        self.finished = [] # (key, agent, result or exception) of the games that ended
        self.running = 0
        self.error = None # set when the main thread stops, to wake up and stop the games

    def generate_function(self, max_model_len):
        def generate(prompt_ids, max_tokens, sampling):
            max_tokens = min(max_tokens, max_model_len - len(prompt_ids))
            if max_tokens < 1: # the context is full: same as an answer that reaches the limit
                return [], True
            request = {"ids": list(prompt_ids), "max_tokens": max_tokens, "sampling": sampling, "output": None}
            with self.condition:
                self.pending.append(request)
                self.condition.notify_all()
                self.condition.wait_for(lambda: request["output"] is not None or self.error is not None)
                if request["output"] is None:
                    raise RuntimeError("play stopped") from self.error
            return request["output"]
        return generate


def _show(text, handle):
    """Shows text on a single line of the notebook, updated in place (printed outside a notebook)."""
    try:
        from IPython.display import display
    except ImportError:
        print(text)
        return None
    if handle is None:
        return display({"text/plain": text}, raw=True, display_id=True)
    handle.update({"text/plain": text}, raw=True)
    return handle
