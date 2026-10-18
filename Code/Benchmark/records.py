"""One JSON file per played game: the record.

Path: <results>/<model>/<game>/<prompt>/<mode>/level<L>-seed<S>.json (the "/" of the model name becomes "__"),
with the log of the game next to it (same name, .log). The path identifies the game, so a game whose file exists
is never played again, and the records of different runs, machines and old experiments can be put together
by copying the folders.

Fields of a record:
    model, mode, prompt, game, level, seed: what was played
    score, max_score, norm_score, steps, loop_stopped: the result (norm_score = score / max_score)
    trajectory: [(moves, score, seconds)] after every step
    max_steps, loop_window, loop_max_distinct: the rules of the episode (loop_window None: never stopped early)
    tokens: generated (total, thinking, normal), think/normal turns, capped turns, max_prompt (tokens read by the
        longest turn). None when unknown (old games without a log)
    time_kind: "wall" (games played together, not comparable) or "cpu" (old sequential games)
    profile, settings: how the model was run (see profiles.py and engine.py)
    source: "benchmark" for the games played by run.py, "legacy: <folder>" for the imported ones (import_legacy.py)
    log: path of the log, relative to the record's folder (or to the repository for the old games), or None
"""
import json
import os

GAMES_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Games")
LEGACY_RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results") # imported old games


def model_folder(model):
    return model.replace("/", "__")


def record_path(results, model, game, prompt, mode, level, seed):
    return os.path.join(results, model_folder(model), game, prompt, mode, f"level{level}-seed{seed}.json")


def game_path(game, level, seed, games_folder=GAMES_FOLDER):
    return os.path.join(games_folder, game, f"level{level}-seed{seed}.z8")


def save_record(path, record):
    """Writes the record through a temporary file, so that an interrupted run never leaves half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(record, f, separators=(",", ":"))
    os.replace(temporary, path)


def iter_records(*folders):
    """All the records in the folders (each one is a results folder, e.g. LEGACY_RESULTS or the one on Drive)."""
    for folder in folders:
        for root, _, files in os.walk(folder):
            for name in sorted(files):
                if name.endswith(".json"):
                    with open(os.path.join(root, name)) as f:
                        yield json.load(f)


def load_table(*folders):
    """A pandas DataFrame with one row per game, with the token statistics as columns (tokens_generated, ...).
    If the same game is in more than one folder, the first folder wins."""
    import pandas as pd
    rows, seen = [], set()
    for record in iter_records(*folders):
        key = tuple(record[name] for name in ("model", "game", "prompt", "mode", "level", "seed"))
        if key in seen:
            continue
        seen.add(key)
        row = {name: value for name, value in record.items() if name not in ("trajectory", "tokens", "settings")}
        for name, value in (record.get("tokens") or {}).items():
            row[f"tokens_{name}"] = value
        rows.append(row)
    return pd.DataFrame(rows)
