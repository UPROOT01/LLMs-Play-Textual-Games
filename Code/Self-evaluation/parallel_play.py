"""Parallel version of play() from self_evaluation.py: several games are played at the same time and the
LLM generates the answers of all of them in a single batch, which uses the GPU much better than one game at a time.

Each game runs in its own thread with an unchanged agent (e.g. LLMAgentSelfEvaluate): the agent's model is
replaced by a proxy whose generate() waits until all the running games have asked for a generation, and then
the main thread generates all the answers together. So the prompts, the parsing of the answers and the logs are
exactly the same as with play().

Differences with play():
- times are wall-clock times (time.perf_counter()) and not CPU times, because the CPU time is shared by all
  the games. They are not comparable with the times measured by play().
- the sampling is not seeded per game (play() calls torch.manual_seed(46) before each game), because the games
  share the batches: the answers are sampled from the same distribution, but they are not reproducible.
"""
import re
import threading
import time

import torch
import textworld
import textworld.gym

from self_evaluation import model, tokenizer

_env_lock = threading.Lock() # TextWorld is not thread-safe (global game registry and game parser): one game action at a time


class _BatchedModel:
    """Stands in for the model inside an agent: generate() hands the request to the batcher and waits for the answer."""
    def __init__(self, batcher):
        self.batcher = batcher

    def generate(self, input_ids, **kwargs):
        return self.batcher.request(input_ids, kwargs)


class _Batcher:
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = [] # requests waiting for a generation
        self.running = 0 # games that have not finished yet
        self.error = None # set when the main thread stops, to wake up and stop the games
        self.generated_tokens = 0
        self.generation_time = 0

    def request(self, input_ids, kwargs):
        """Called by the game threads."""
        request = {"ids": input_ids[0].tolist(), "kwargs": kwargs, "output": None}
        with self.condition:
            self.pending.append(request)
            self.condition.notify_all()
            self.condition.wait_for(lambda: request["output"] is not None or self.error is not None)
            if self.error is not None:
                raise RuntimeError("parallel play stopped") from self.error
        return torch.tensor([request["ids"] + request["output"]], device=model.device)

    def serve(self):
        """Called by the main thread when every running game is waiting for a generation: generates all the answers,
        grouped by generation parameters (normal turns and self-evaluations are generated apart)."""
        with self.condition:
            requests, self.pending = self.pending, []
        groups = {}
        for request in requests:
            groups.setdefault(repr(sorted(request["kwargs"].items())), []).append(request)
        # normal turns first: their games can go on while the self-evaluations are generated
        for group in sorted(groups.values(), key=lambda g: g[0]["kwargs"].get("max_new_tokens", 0)):
            self._generate(group)
            with self.condition:
                self.condition.notify_all()

    def _generate(self, requests):
        try:
            outputs = self._generate_batch(requests)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            if len(requests) == 1:
                raise
            half = len(requests) // 2 # too many long contexts together: generate them in two smaller batches
            self._generate(requests[:half])
            self._generate(requests[half:])
            return
        for request, output in zip(requests, outputs):
            request["output"] = output

    def _generate_batch(self, requests):
        kwargs = requests[0]["kwargs"]
        eos_token_id = kwargs.get("eos_token_id", tokenizer.eos_token_id)
        pad_token_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos_token_id
        length = max(len(request["ids"]) for request in requests)
        # left padding, so that all the answers start at the same position
        input_ids = torch.tensor([[pad_token_id] * (length - len(r["ids"])) + r["ids"] for r in requests], device=model.device)
        attention_mask = torch.tensor([[0] * (length - len(r["ids"])) + [1] * len(r["ids"]) for r in requests], device=model.device)
        start = time.perf_counter()
        with torch.no_grad():
            generated_ids = model.generate(input_ids, attention_mask=attention_mask, pad_token_id=pad_token_id, **kwargs)
        self.generation_time += time.perf_counter() - start
        outputs = []
        for row in generated_ids[:, length:].tolist():
            # the answers that ended earlier are followed by padding: cut them after their end-of-turn token,
            # like an unbatched generation would stop there
            if eos_token_id in row:
                row = row[:row.index(eos_token_id) + 1]
            outputs.append(row)
            self.generated_tokens += len(row)
        return outputs


def _play_game(agent, path, max_steps, loop_window, loop_max_distinct):
    """One episode of play(), with wall-clock times. Returns the list of (moves, score, time) of the episode."""
    infos_to_request = agent.infos_to_request
    infos_to_request.max_score = True
    with _env_lock:
        env_id = textworld.gym.register_games([path], request_infos=infos_to_request, max_episode_steps=max_steps)
        env = textworld.gym.make(env_id)
    try:
        episode_start = time.perf_counter()
        with _env_lock:
            obs, infos = env.reset()
        score = 0
        done = False
        nb_moves = 0
        moves_scores_times = [(0, 0, 0)]
        turns = []
        loop = False
        while not done:
            command = agent.act(obs, score, done, infos)
            turns.append((re.sub(r">\s+-=.*", "", obs).strip(), command))
            timestamp = time.perf_counter()
            with _env_lock:
                obs, score, done, infos = env.step(command)
            nb_moves += 1
            moves_scores_times.append((nb_moves, score, timestamp - episode_start))
            if not done and loop_window and len(turns) >= loop_window \
                    and len(set(turns[-loop_window:])) <= loop_max_distinct:
                if hasattr(agent, "write_on_log"):
                    agent.write_on_log(f"LOOP DETECTED: episode stopped at step {nb_moves}")
                loop = True
                break
        agent.act(obs, score, True, infos)
        return moves_scores_times, infos["max_score"], loop, time.perf_counter() - episode_start
    finally:
        with _env_lock:
            env.close()


def play_parallel(jobs, n_parallel=4, max_steps=100, loop_window=None, loop_max_distinct=2, on_result=None, verbose=True):
    """Plays one episode of every job, n_parallel games at a time.
    jobs: list of (key, make_agent, path): make_agent() returns a new agent (e.g. LLMAgentSelfEvaluate), path is the game.
    on_result(key, moves_scores_times): called in the main thread as soon as a game ends, in the order the games end.
    Returns {key: moves_scores_times}, where moves_scores_times is the same list play() returns for one episode.
    """
    torch.manual_seed(46)
    batcher = _Batcher()
    jobs = list(jobs)
    results = {}
    finished = [] # (key, path, result or exception), filled by the game threads
    display_handle = None

    def run(key, agent, path):
        try:
            outcome = _play_game(agent, path, max_steps, loop_window, loop_max_distinct)
        except BaseException as e:
            outcome = e
        with batcher.condition:
            finished.append((key, path, outcome))
            batcher.running -= 1
            batcher.condition.notify_all()

    try:
        while jobs or batcher.running > 0:
            while jobs and batcher.running < n_parallel:
                key, make_agent, path = jobs.pop(0)
                agent = make_agent()
                agent.model = _BatchedModel(batcher)
                with batcher.condition:
                    batcher.running += 1
                threading.Thread(target=run, args=(key, agent, path), daemon=True).start()

            # wait until a game ends (so that a new one can start) or all the running games wait for a generation
            with batcher.condition:
                while not finished and not (batcher.pending and len(batcher.pending) >= batcher.running):
                    batcher.condition.wait(timeout=1)
                done_now, finished[:] = finished[:], []
                # all the running games are waiting; if a game ended and others are queued, start them first
                ready = batcher.pending and len(batcher.pending) >= batcher.running and not (done_now and jobs)
            for key, path, outcome in done_now: # saved before the next generation, which can take minutes
                if isinstance(outcome, BaseException):
                    raise outcome
                moves_scores_times, max_score, loop, seconds = outcome
                results[key] = moves_scores_times
                if verbose:
                    print(f"{path} {'loop detected, ' if loop else ''}steps: {moves_scores_times[-1][0]}; "
                          f"score: {moves_scores_times[-1][1]} / {max_score}; time: {seconds:.0f} s")
                if on_result is not None:
                    on_result(key, moves_scores_times)
            if ready:
                batcher.serve()

            if verbose and batcher.generation_time > 0:
                text = f"{batcher.running} games running, {len(jobs)} waiting; " \
                     + f"{batcher.generated_tokens / batcher.generation_time * 60:.0f} tokens/min overall"
                try:
                    from IPython.display import display
                    if display_handle is None:
                        display_handle = display({"text/plain": text}, raw=True, display_id=True)
                    else:
                        display_handle.update({"text/plain": text}, raw=True)
                except ImportError:
                    pass
    finally:
        with batcher.condition:
            if batcher.running > 0 and batcher.error is None:
                batcher.error = RuntimeError("stopped") # wakes up the game threads, which then stop
            batcher.condition.notify_all()
    return results
