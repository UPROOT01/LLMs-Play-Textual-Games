"""run.py and engine.py end to end, without GPU and without TextWorld: a fake vLLM backend answers the requests,
and fake games give a point for every "go north". Checks the records, the skipping of the games already played
(new ones and imported old ones) and the dry run.

    python tests/test_pipeline.py
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from transformers import AutoTokenizer

import engine
import run
from records import LEGACY_RESULTS, game_path, load_table, record_path

TOKENIZER = AutoTokenizer.from_pretrained("Qwen/Qwen3-4B")


class FakeEnv:
    def __init__(self, path, max_steps):
        self.max_steps, self.steps, self.score = max_steps, 0, 0

    def _obs(self):
        return f"-= Kitchen =-\nA kitchen, {self.score} doors open.\n\n>   -= Kitchen =-{self.score}/{self.steps}"

    def reset(self):
        return self._obs(), {"max_score": 3}

    def step(self, command):
        self.steps += 1
        self.score += command == "go north"
        done = self.score == 3 or self.steps >= self.max_steps
        return self._obs(), self.score, done, {"max_score": 3}

    def close(self):
        pass


class FakeBackend:
    """Answers every request at the next step: self-evaluations go north, normal turns take the key."""

    def __init__(self):
        self.pending = []
        self.requests = 0

    def add_request(self, request_id, prompt, params):
        self.requests += 1
        think = params["max_tokens"] > 100
        text = "<think>\nnorth is the way\n</think>\n\ngo north" if think else "<think>\n\n</think>\n\ntake key"
        self.pending.append((request_id, TOKENIZER.encode(text, add_special_tokens=False) + [TOKENIZER.eos_token_id]))

    def step(self):
        outputs = [types.SimpleNamespace(request_id=request_id, finished=True,
                                         outputs=[types.SimpleNamespace(token_ids=ids, finish_reason="stop")])
                   for request_id, ids in self.pending]
        self.pending = []
        return outputs

    def abort_request(self, ids):
        pass


class FakeEngine(engine.Engine):
    loaded = []

    def __init__(self, model, gpu_memory_utilization=0.9, **settings):
        FakeEngine.loaded.append(model)
        self.model, self.tokenizer, self.backend = model, TOKENIZER, FakeBackend()
        self.max_model_len = settings["max_model_len"]
        self.description = dict(backend="fake", **settings)

    def _sampling_params(self, max_tokens, sampling):
        return dict(max_tokens=max_tokens, **(sampling or {}))

    def close(self):
        pass


def test_run_grid():
    engine.make_env = FakeEnv
    engine.Engine = FakeEngine
    results = tempfile.mkdtemp()
    grid = dict(run.DEFAULTS, models=["Qwen/Qwen3-4B", "Qwen/Qwen3-1.7B"], modes=["0", "er4", "e1"],
                prompts=["CoT"], games={"cooking": [1, 3]}, seeds=[1, 2, 3], max_steps=30, loop_window=10)

    # Qwen3-4B e1 and er4 with CoT on cooking 1/3 seeds 1-3 are old games (experiments 7 and 7e): not played again
    run.run_grid(grid, results, read=[LEGACY_RESULTS], n_parallel=4, verbose=False)
    table = load_table(results)
    assert len(table) == 2 * 3 * 2 * 3 - 12, len(table)
    assert set(table[table.model == "Qwen/Qwen3-4B"]["mode"]) == {"0"}
    assert FakeEngine.loaded == ["Qwen/Qwen3-4B", "Qwen/Qwen3-1.7B"]

    with open(record_path(results, "Qwen/Qwen3-1.7B", "cooking", "CoT", "er4", 3, 2)) as f:
        record = json.load(f)
    assert record["score"] == 3 and record["norm_score"] == 1 and record["source"] == "benchmark"
    assert record["tokens"]["think_turns"] == 3 and record["tokens"]["generated"] > 0
    assert len(record["trajectory"]) == record["steps"] + 1
    assert os.path.exists(record_path(results, "Qwen/Qwen3-1.7B", "cooking", "CoT", "er4", 3, 2)[:-5] + ".log")
    # 0-think never self-evaluates: it takes the key forever, and is stopped as a loop
    zero = table[table["mode"] == "0"]
    assert zero.loop_stopped.all() and (zero.steps == 10).all() and (zero.tokens_think_turns == 0).all()

    # running again plays nothing; a missing record is played again
    os.remove(record_path(results, "Qwen/Qwen3-1.7B", "cooking", "CoT", "0", 1, 1))
    FakeEngine.loaded = []
    run.run_grid(grid, results, read=[LEGACY_RESULTS], verbose=False)
    assert FakeEngine.loaded == ["Qwen/Qwen3-1.7B"] and len(load_table(results)) == 24

    # limit: a pilot of 2 games per model
    other = tempfile.mkdtemp()
    run.run_grid(grid, other, read=[], limit=2, verbose=False)
    assert len(load_table(other)) == 4
    run.summary(grid, results, [LEGACY_RESULTS])
    print("ok run_grid: records, skipping, resuming, limit")


def test_grid_files():
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "grids")
    from profiles import model_settings
    for name in sorted(os.listdir(folder)):
        grid = run.load_grid(os.path.join(folder, name))
        for model in grid["models"]:
            model_settings(model)
        for game, levels in grid["games"].items():
            for level in levels:
                for seed in grid["seeds"]:
                    assert os.path.exists(game_path(game, level, seed)), (game, level, seed)
        print(f"ok {name}: {len(list(run.grid_games(grid)))} games")


if __name__ == "__main__":
    test_grid_files()
    test_run_grid()
