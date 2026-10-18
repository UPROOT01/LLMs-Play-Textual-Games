"""Plays the games of a grid (grids/*.yaml) that have not been played yet, one model at a time.

    python run.py grids/tier1.yaml --results /content/drive/MyDrive/LLMs-Play-Textual-Games/Benchmark/results
    python run.py grids/tier1.yaml --dry-run          # only shows what is done and what is left
    python run.py grids/tier1.yaml --model Qwen/Qwen3-8B --limit 8   # a pilot: 8 games of one model

A game is skipped if its record exists in the results folder or in one of the folders to read
(by default the old games imported by import_legacy.py), whatever the settings it was played with.
The games are played seed by seed (all the levels and modes of seed 1, then seed 2, ...), so that when a run is
stopped every combination has the same seeds. Stopping and running again resumes from the games not yet saved.
"""
import argparse
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone

import yaml

from records import LEGACY_RESULTS, game_path, iter_records, record_path, save_record

DEFAULTS = dict(prompts=["CoT"], max_steps=100, loop_window=20, loop_max_distinct=2)


def load_grid(path):
    with open(path) as f:
        grid = {**DEFAULTS, **yaml.safe_load(f)}
    grid["modes"] = [str(mode) for mode in grid["modes"]] # 0 is read as a number
    seeds = grid["seeds"]
    if isinstance(seeds, str): # "1-10"
        first, last = (int(n) for n in seeds.split("-"))
        seeds = list(range(first, last + 1))
    grid["seeds"] = list(seeds)
    return grid


def grid_games(grid, models=None):
    """(model, game, prompt, mode, level, seed) of every game of the grid, model by model and seed by seed."""
    for model in grid["models"]:
        if models and model not in models:
            continue
        for seed in grid["seeds"]:
            for game, levels in grid["games"].items():
                for level in levels:
                    for prompt in grid["prompts"]:
                        for mode in grid["modes"]:
                            yield model, game, prompt, mode, level, seed


def is_played(folders, game_id):
    return any(os.path.exists(record_path(folder, *game_id)) for folder in folders)


def summary(grid, results, read, models=None):
    """Prints, for every model and mode, how many games are played and how many are left."""
    folders = [results, *read]
    done, left = Counter(), Counter()
    for game_id in grid_games(grid, models):
        (done if is_played(folders, game_id) else left)[game_id[0], game_id[3]] += 1
    # generated tokens per game of each mode in the games already played, as a rough estimate of the cost
    tokens = {}
    for record in iter_records(*folders):
        if record.get("tokens") and record["mode"] in grid["modes"] and record["game"] in grid["games"]:
            tokens.setdefault(record["mode"], []).append(record["tokens"]["generated"])
    print(f"{'model':<45}{'mode':>6}{'played':>8}{'left':>6}{'~Mtokens left':>15}")
    for model in grid["models"]:
        if models and model not in models:
            continue
        for mode in grid["modes"]:
            estimate = ""
            if mode in tokens:
                estimate = f"{left[model, mode] * sum(tokens[mode]) / len(tokens[mode]) / 1e6:.1f}"
            print(f"{model:<45}{mode:>6}{done[model, mode]:>8}{left[model, mode]:>6}{estimate:>15}")
    print(f"total: {sum(done.values())} played, {sum(left.values())} left "
          "(the token estimate uses the games already played with the same mode, any model)")


def run_grid(grid, results, read=(LEGACY_RESULTS,), models=None, limit=None, n_parallel=8,
             gpu_memory_utilization=0.9, verbose=True):
    """Plays the games of the grid (a path or a dict of load_grid()) not yet played, and saves their records in results.
    read: other results folders whose games are not played again. models: only these models of the grid.
    limit: at most this many games per model (e.g. for a pilot)."""
    from agent import SelfEvalAgent
    from engine import Engine
    from profiles import model_settings

    if isinstance(grid, str):
        grid = load_grid(grid)
    folders = [results, *read]
    for model in grid["models"]:
        if models and model not in models:
            continue
        to_play = [game_id for game_id in grid_games(grid, [model]) if not is_played(folders, game_id)]
        if limit is not None:
            to_play = to_play[:limit]
        print(f"{model}: {len(to_play)} games to play")
        if not to_play:
            continue
        profile, settings = model_settings(model)
        engine = Engine(model, gpu_memory_utilization=gpu_memory_utilization, **settings)

        def make_job(game_id):
            model, game, prompt, mode, level, seed = game_id
            path = record_path(results, *game_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)

            def make_agent(generate):
                return SelfEvalAgent(profile, engine.tokenizer, generate, mode, prompt,
                                     rng=random.Random("|".join(str(part) for part in game_id)),
                                     log=path[:-len(".json")] + ".log")
            play_kwargs = dict(path=game_path(game, level, seed), max_steps=grid["max_steps"],
                               loop_window=grid["loop_window"], loop_max_distinct=grid["loop_max_distinct"])
            return game_id, make_agent, play_kwargs

        def on_result(game_id, agent, result):
            model, game, prompt, mode, level, seed = game_id
            record = dict(model=model, mode=mode, prompt=prompt, game=game, level=level, seed=seed,
                          score=result["score"], max_score=result["max_score"],
                          norm_score=result["score"] / result["max_score"],
                          steps=result["steps"], loop_stopped=result["loop_stopped"], seconds=result["seconds"],
                          trajectory=result["trajectory"], max_steps=grid["max_steps"],
                          loop_window=grid["loop_window"], loop_max_distinct=grid["loop_max_distinct"],
                          tokens=agent.token_stats(), time_kind="wall", profile=profile.name,
                          settings=engine.description, source="benchmark",
                          log=f"level{level}-seed{seed}.log",
                          created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            save_record(record_path(results, *game_id), record)

        start = time.perf_counter()
        try:
            engine.play([make_job(game_id) for game_id in to_play], n_parallel=n_parallel,
                        on_result=on_result, verbose=verbose)
        finally:
            engine.close()
        print(f"{model}: done in {(time.perf_counter() - start) / 60:.0f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("grid", help="grid file, e.g. grids/tier1.yaml")
    parser.add_argument("--results", default="results_new", help="folder where the new records are saved")
    parser.add_argument("--read", nargs="*", default=[LEGACY_RESULTS],
                        help="other results folders whose games are not played again (default: the imported old games)")
    parser.add_argument("--model", nargs="*", help="only these models of the grid")
    parser.add_argument("--limit", type=int, help="at most this many games per model (e.g. a pilot)")
    parser.add_argument("--n-parallel", type=int, default=8, help="games played at the same time")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9)
    parser.add_argument("--dry-run", action="store_true", help="only show what is played and what is left")
    args = parser.parse_args()
    grid = load_grid(args.grid)
    if args.dry_run:
        summary(grid, args.results, args.read, args.model)
    else:
        run_grid(grid, args.results, args.read, args.model, args.limit, args.n_parallel, args.gpu_memory_utilization)


if __name__ == "__main__":
    main()
