"""Plays many games at the same time with one model loaded in vLLM (same approach of Self-evaluation/parallel_play.py).

Each game runs in its own thread with its own agent. When the agent needs an answer, its generate() hands the request
to the main thread and waits: the main thread keeps adding the requests to the batch vLLM is generating
(continuous batching), so a game does not wait for the self-evaluations of the other games, and vLLM reuses
the context already read in the previous turns (prefix caching).
The times are wall-clock times of games played together: they are not comparable between runs.

How many games are played at the same time (n_parallel="auto", see ContextBudget): while a game waits for its answers,
which is almost all the time, its whole context is in vLLM's KV cache. A new game is started only if the contexts of
all the games, at the size they are expected to reach by their end, fit in the KV cache; when the next game is too big,
a smaller one after it can start instead. Too many games would not fit:
vLLM would pause some answers and compute their contexts again (preemption), and the tokens per minute would drop.
Once vLLM's own usage of the KV cache has been read for a minute, new games start on that measure (target 92%,
lowered when vLLM pauses too many requests); see ContextBudget.measure().
"""
import collections
import gc
import itertools
import os
import re
import sys
import threading
import time

import numpy as np

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
        cache_config = self.llm.llm_engine.vllm_config.cache_config
        self.kv_capacity = None # tokens that fit in the KV cache (None: unknown)
        if cache_config.num_gpu_blocks:
            self.kv_capacity = cache_config.num_gpu_blocks * cache_config.block_size
        self.preemptions = None # requests vLLM paused for lack of KV cache (None: cannot be counted in this vLLM)
        try:
            scheduler = self.backend.engine_core.engine_core.scheduler
            preempt = scheduler._preempt_request

            def counted_preempt(*args, **kwargs):
                self.preemptions += 1
                return preempt(*args, **kwargs)
            scheduler._preempt_request = counted_preempt
            self.preemptions = 0
        except Exception:
            pass
        print(f"{model}: KV cache of {self.kv_capacity} tokens" if self.kv_capacity else
              f"{model}: unknown KV cache size, n_parallel='auto' plays {FALLBACK_PARALLEL} games at a time")
        self.description = dict(backend=f"vllm {vllm.__version__}", gpu_memory_utilization=gpu_memory_utilization,
                                **llm_settings)

    def kv_usage(self):
        """Fraction of the KV cache held right now by the requests being generated (blocks that are free but still
        hold a cached prefix do not count), or None if this vLLM version does not let us read it."""
        try:
            return float(self.backend.engine_core.engine_core.scheduler.kv_cache_manager.usage)
        except Exception:
            return None

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

    def play(self, jobs, n_parallel="auto", on_result=None, verbose=True, group=None, max_parallel=64,
             play_function=None):
        """Plays the jobs, n_parallel games at a time ("auto": as many as fit in the KV cache, see ContextBudget).
        jobs: list of (key, make_agent, play_kwargs): make_agent(generate) returns a new agent that uses generate()
        as its model, play_kwargs are the arguments of play_function() (path, max_steps, loop_window, ...).
        play_function(agent, **play_kwargs): plays one game, play_game() (TextWorld) by default; it returns a dict
        with at least score, max_score, steps, loop_stopped and seconds (e.g. duel.play_duel() for two-player games).
        on_result(key, agent, result): called in the main thread as soon as a game ends, with the result of play_game().
        group(key): games of the same group have contexts of similar size (e.g. the mode); used by "auto".
        """
        group = group or (lambda key: None)
        budget = None
        if n_parallel == "auto":
            if self.kv_capacity:
                budget = ContextBudget(self.kv_capacity, max_parallel=max_parallel)
            else:
                n_parallel = FALLBACK_PARALLEL
        playing = {} # key -> agent of the games being played
        state = _State()
        jobs = list(jobs)
        in_vllm = {} # request id -> request being generated
        start = time.perf_counter()
        generated = 0
        ended = 0
        last_display = 0
        display_handle = None
        history = collections.deque() # (time, ended, generated, usage sum, usage samples) of the last RECENT seconds
        peaks = collections.deque() # (time, highest KV usage seen between two displays) of the last RECENT seconds
        usage_sum = usage_samples = 0 # actual KV usage, read at every step of vLLM
        usage_peak = 0.0
        history_preemptions = collections.deque() # (time, preemptions so far)

        def run(key, agent, play_kwargs):
            try:
                outcome = (play_function or play_game)(agent, **play_kwargs)
            except BaseException as e:
                outcome = e
            with state.condition:
                state.finished.append((key, agent, outcome))
                state.running -= 1
                state.condition.notify_all()

        try:
            while jobs or state.running > 0 or state.finished: # finished: the last games ended but are not saved yet
                while jobs:
                    if budget:
                        index = budget.choose([group(job[0]) for job in jobs],
                                              [(agent, group(key)) for key, agent in playing.items()])
                    else:
                        index = 0 if state.running < n_parallel else None
                    if index is None:
                        break
                    key, make_agent, play_kwargs = jobs.pop(index)
                    agent = make_agent(state.generate_function(self.max_model_len))
                    playing[key] = agent
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
                    playing.pop(key, None)
                    if budget:
                        budget.ended(agent, group(key))
                    ended += 1
                    if verbose:
                        print(f"{key}: {'loop, ' if outcome['loop_stopped'] else ''}steps {outcome['steps']}, "
                              f"score {outcome['score']}/{outcome['max_score']}, {outcome['seconds']:.0f} s")
                    if on_result is not None:
                        on_result(key, agent, outcome)

                if in_vllm:
                    usage = self.kv_usage() if verbose or budget else None # before the step
                    if usage is not None and budget:
                        budget.measure(usage, getattr(self, "preemptions", None))
                    if usage is not None:
                        usage_sum += usage
                        usage_samples += 1
                        usage_peak = max(usage_peak, usage)
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
                    history.append((last_display, ended, generated, usage_sum, usage_samples))
                    peaks.append((last_display, usage_peak))
                    usage_peak = 0.0
                    while last_display - history[0][0] > RECENT:
                        history.popleft()
                    while last_display - peaks[0][0] > RECENT:
                        peaks.popleft()
                    recent = last_display - history[0][0]
                    text = f"{state.running} games running, {len(jobs)} waiting, {ended} ended; " \
                         + f"since the start {ended / elapsed * 3600:.1f} games/hour, {generated / elapsed * 60:.0f} tokens/min"
                    if recent > 0:
                        text += f"; last {recent / 60:.0f} min {(ended - history[0][1]) / recent * 3600:.1f} games/hour, " \
                              + f"{(generated - history[0][2]) / recent * 60:.0f} tokens/min"
                    if budget and budget.measuring():
                        text += f"; KV cache: target {budget.target:.0%} (games started on the measured usage)"
                    elif budget:
                        text += f"; KV cache: expected {budget.planned([(agent, group(key)) for key, agent in playing.items()]) / budget.capacity:.0%}"
                    if usage_samples:
                        text += f"{', ' if budget else '; KV cache: '}actual {usage_sum / usage_samples:.0%} on average since the start"
                        if usage_samples > history[0][4]:
                            text += f", {(usage_sum - history[0][3]) / (usage_samples - history[0][4]):.0%} in the last {recent / 60:.0f} min" \
                                  + f" (peak {max(peak for _, peak in peaks):.0%})"
                    elif budget:
                        text += ", actual not readable in this vLLM"
                    if getattr(self, "preemptions", None) is not None:
                        history_preemptions.append((last_display, self.preemptions))
                        while last_display - history_preemptions[0][0] > RECENT:
                            history_preemptions.popleft()
                        text += f"; {self.preemptions - history_preemptions[0][1]} requests paused (preempted) in the last {recent / 60:.0f} min" \
                              + f", {self.preemptions} since the start"
                    display_handle = _show(text, display_handle)
        finally:
            if in_vllm:
                self.backend.abort_request(list(in_vllm))
            with state.condition:
                if state.running > 0 and state.error is None:
                    state.error = RuntimeError("stopped") # wakes up the game threads, which then stop
                state.condition.notify_all()


FALLBACK_PARALLEL = 8 # games at a time with n_parallel="auto" when the size of the KV cache is unknown
RECENT = 600 # seconds of the recent rates in the status line


class ContextBudget:
    """Decides when to start another game, so that the games being played fit together in the KV cache.

    vLLM holds in the KV cache only the requests being generated: a game counts for the tokens it holds while one of
    its answers is generated (prompt + answer), not for its whole history. Between two answers its blocks are free
    (prefix caching may keep them, but they can be evicted), and in a two-player game only one player answers at a
    time. A game counts for the tokens it is expected to hold on average over the time it spends in vLLM: the
    `percentile` of the averages of the games of its group (e.g. its mode) that already ended (agent.kv_occupancy()),
    of all the games that ended while fewer than 3 of its group have, or `prior` tokens before 3 games have ended.
    A game whose last request was larger counts for that size. A game can start if all of them, plus the new one,
    take at most `fraction` of the KV cache (and there are fewer than max_parallel games); at least min_parallel
    games are always played. The sizes fluctuate around the averages: when the cache is full for a moment, vLLM
    preempts a request and computes it again later (slower, never wrong).

    The first waiting game starts as soon as it fits. While it does not fit, the first smaller game after it that fits
    starts instead (backfill): small games add little to the generation time of each step and keep the GPU busy.
    So that the small games do not keep the big one waiting forever, after max_wait seconds of waiting the backfill
    stops until the big one starts: the small games end quickly and make room for it.
    Measured mode (from 2026-11-11, see measure()): once vLLM's own KV cache usage has been read for `window` seconds,
    the room for new games comes from it instead of from the estimates above: the estimates were still about 1.6 times
    the real usage (games between two turns hold nothing, the 75th percentile is not the mean). A full cache does not
    slow the generation down, pausing requests does: the target goes down when vLLM pauses too many.

    (Until 2026-11-11 a game counted for its whole final context, and a two-player game for both players' contexts
    added: about 2-4 times more than vLLM holds, and 3 Connect Four games at a time on an L4.)
    """

    def __init__(self, capacity, fraction=0.9, prior=12000, min_parallel=2, max_parallel=64, max_wait=300,
                 percentile=75, target=0.92, window=60, min_target=0.6, max_preemptions=5):
        self.capacity = capacity
        self.fraction = fraction
        self.prior = prior
        self.min_parallel = min_parallel
        self.max_parallel = max_parallel
        self.max_wait = max_wait
        self.percentile = percentile
        self.occupancy = {} # group -> average tokens held in the KV cache by its games that ended
        self.first_waiting_since = None # when the first waiting game started to wait (None: it is not waiting)
        # measured mode (see measure())
        self.target = self.max_target = target
        self.window = window
        self.min_target = min_target
        self.max_preemptions = max_preemptions
        self.samples = collections.deque() # (time, usage, preemptions so far) of the last `window` seconds
        self.usage_sum = 0.0
        self.first_sample = None
        self.starts = collections.deque() # (time, expected tokens) of the games started in the last `window` seconds
        self.last_target_change = None

    def _estimate(self, values):
        return float(np.percentile(values, self.percentile))

    def expected(self, group):
        own = self.occupancy.get(group, [])
        if len(own) >= 3:
            return self._estimate(own)
        every = [value for values in self.occupancy.values() for value in values]
        return self._estimate(every) if len(every) >= 3 else self.prior

    def planned(self, playing):
        """Tokens the games being played (list of (agent, group)) are expected to hold in the KV cache."""
        return sum(max(agent.context_tokens(), self.expected(group)) for agent, group in playing)

    def measure(self, usage, preemptions=None, now=None):
        """Records the KV cache usage vLLM reports (fraction held by the requests being generated) and the number of
        requests it paused so far (None: unknown). Once `window` seconds are measured, the budget uses them (measured
        mode): a game can start if the mean usage of the last `window` seconds, plus the expected size of the games
        started within it (not yet fully in the mean) and of the new one, stays under `target`. The target goes down
        by 0.05 (at least min_target) when more than max_preemptions requests are paused within a window, and back up
        by 0.02 (at most its start value) after a window without pauses; at most one change per window."""
        now = time.perf_counter() if now is None else now
        if self.first_sample is None:
            self.first_sample = now
        self.samples.append((now, usage, preemptions))
        self.usage_sum += usage
        self._prune(now)
        if preemptions is None or self.samples[0][2] is None:
            return
        if now - self.first_sample < self.window:
            return
        if self.last_target_change is not None and now - self.last_target_change < self.window:
            return
        paused = preemptions - self.samples[0][2]
        if paused > self.max_preemptions and self.target > self.min_target:
            self.target = max(self.min_target, self.target - 0.05)
            self.last_target_change = now
        elif paused == 0 and self.target < self.max_target:
            self.target = min(self.max_target, self.target + 0.02)
            self.last_target_change = now

    def _prune(self, now):
        while self.samples and now - self.samples[0][0] > self.window:
            self.usage_sum -= self.samples.popleft()[1]
        while self.starts and now - self.starts[0][0] > self.window:
            self.starts.popleft()

    def measuring(self, now=None):
        """True if the budget uses the measured usage (a full window measured, and samples in the last window)."""
        now = time.perf_counter() if now is None else now
        self._prune(now)
        return self.first_sample is not None and now - self.first_sample >= self.window and bool(self.samples)

    def room(self, playing, now=None):
        """Tokens of the KV cache still free for new games."""
        if self.measuring(now):
            used = self.usage_sum / len(self.samples) * self.capacity + sum(tokens for _, tokens in self.starts)
            return self.target * self.capacity - used
        return self.fraction * self.capacity - self.planned(playing)

    def choose(self, waiting, playing, now=None):
        """Index in `waiting` (the groups of the waiting games, in order) of the game to start now, or None.
        playing: list of (agent, group) of the games being played."""
        if not waiting or len(playing) >= self.max_parallel:
            return None
        now = time.perf_counter() if now is None else now
        room = self.room(playing, now)
        if len(playing) < self.min_parallel or self.expected(waiting[0]) <= room:
            self.first_waiting_since = None
            self.starts.append((now, self.expected(waiting[0])))
            return 0
        if self.first_waiting_since is None:
            self.first_waiting_since = now
        if now - self.first_waiting_since > self.max_wait: # stop the backfill, to make room for the first game
            return None
        index = next((index for index, group in enumerate(waiting) if self.expected(group) <= room), None)
        if index is not None:
            self.starts.append((now, self.expected(waiting[index])))
        return index

    def ended(self, agent, group):
        occupancy = agent.kv_occupancy() if hasattr(agent, "kv_occupancy") else None
        value = occupancy["mean"] if occupancy else agent.context_tokens()
        self.occupancy.setdefault(group, []).append(value)


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
