"""Converts the games of the old experiments (pickles + logs in Code/Experiments) into benchmark records,
in Code/Benchmark/results. The old files are only read. Run it again after adding a source: existing records are kept.

    python import_legacy.py              # with the token counts (downloads the Qwen3 tokenizer)
    python import_legacy.py --no-tokens  # faster, without them

What it knows about the old files:
- a pickle holds the games of seeds 1, 2, 3, ... in this order (checked on the logs: every log whose scores match
  only one game of its pickle matches the game of its seed);
- the pickles of 0-think of experiment 7 are copies of experiment 6's, played with the default prompt,
  and its er4/er10-think pickles are copies of experiment 7e's: they are imported only once;
- the pickles of 0-think of Coin Collector levels 11-14 hold 49 games: the missing seed is unknown, so they are skipped;
- the logs are only linked (and their tokens counted) when their scores match the game of the pickle:
  some logs were overwritten by later runs of the same game (e.g. after a resume).
Token counts from the logs: the logged text of every answer, tokenized, plus the tokens that are not logged
(5 per normal turn, 2 per self-evaluation), and 10000 for a self-evaluation that reached the limit
(checked against the speed lines of the 7e logs). Normal turns that reached their limit of 100 cannot be recognized.
"""
import argparse
import glob
import json
import os
import pickle
import re

from records import LEGACY_RESULTS, record_path, save_record

REPOSITORY = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
EXPERIMENTS = os.path.join(REPOSITORY, "Code", "Experiments")
MODEL = "Qwen/Qwen3-4B"

# folder in Code/Experiments, prompt, loop stopping, and the modes not to import (with the reason)
SOURCES = [
    dict(folder="Experiment 6", prompt="default", loop_window=None, backend="transformers", skip={}),
    dict(folder="Experiment 7", prompt="CoT", loop_window=None, backend="transformers",
         skip={"0": "copy of experiment 6 (default prompt)", "er4": "copy of experiment 7e", "er10": "copy of experiment 7e"}),
    dict(folder="Experiment 7e", prompt="CoT", loop_window=20, backend=None, skip={}), # backend from the log
    dict(folder="Experiment 8/CoT", prompt="CoT", loop_window=20, backend="vllm", skip={}),
]
EXPECTED_LENGTHS = (1, 10, 20, 50) # games per pickle: any other number means a missing seed

MARKERS = ("GAME ++++", "AGENT ----", "SELF-EVALUATION: +-", "CONTEXT INITIALIZED", "SPEED:", "LOOP DETECTED")
OVERHEAD = {"normal": 5, "think": 2}
THINK_CAP = 10000


def log_scores(text):
    """Score shown in the status bar at every turn of the log (None when there is no status bar)."""
    text = text.split("CONTEXT INITIALIZED")[1] if "CONTEXT INITIALIZED" in text else text
    scores = []
    for block in re.split(r"GAME \+{10,}\n", text)[1:]:
        found = re.findall(r"-= .*? =-(\d+)/\d+", block)
        scores.append(int(found[-1]) if found else None)
    return scores


def log_matches(text, trajectory):
    """Whether the scores in the log are the ones of the game in the pickle."""
    pickle_scores = [score for _, score, _ in trajectory]
    scores = log_scores(text)
    return 0 < len(scores) <= len(pickle_scores) and all(a is None or a == b for a, b in zip(scores, pickle_scores))


def log_answers(text):
    """(kind, text) of every answer of the model in the log, kind is "normal" or "think"."""
    answers, kind, lines = [], None, []
    for line in text.split("\n") + ["CONTEXT INITIALIZED"]:
        if line.startswith(MARKERS):
            if kind is not None:
                answers.append((kind, "\n".join(lines).strip("\n")))
            kind = "normal" if line.startswith("AGENT") else "think" if line.startswith("SELF-EVALUATION") else None
            lines = []
        else:
            lines.append(line)
    return answers


def log_tokens(text, tokenizer):
    stats = dict(generated=0, generated_thinking=0, generated_normal=0, think_turns=0, normal_turns=0,
                 capped_think_turns=0, capped_normal_turns=None, max_prompt=None)
    for kind, answer in log_answers(text):
        if kind == "think" and "</think>" not in answer: # reached the limit: the reasoning is not logged
            n = THINK_CAP
            stats["capped_think_turns"] += 1
        else:
            n = len(tokenizer.encode(answer)) + OVERHEAD[kind]
        stats["generated"] += n
        stats["generated_thinking" if kind == "think" else "generated_normal"] += n
        stats["think_turns" if kind == "think" else "normal_turns"] += 1
    return stats


def import_source(source, tokenizer, max_scores_cooking, overwrite=False):
    folder = os.path.join(EXPERIMENTS, source["folder"])
    counts = dict(imported=0, existing=0, conflicts=0, with_log=0)
    for path in sorted(glob.glob(os.path.join(folder, "*.pickle"))):
        match = re.match(r"(\w+?)think_(\w+)_level(\d+)\.pickle$", os.path.basename(path))
        if match is None:
            continue
        mode, game, level = match[1], match[2], int(match[3])
        if mode in source["skip"]:
            continue
        with open(path, "rb") as f:
            games = pickle.load(f)
        if len(games) not in EXPECTED_LENGTHS:
            print(f"  skipped {source['folder']}/{os.path.basename(path)}: {len(games)} games, the missing seed is unknown")
            continue
        max_score = max_scores_cooking[level - 1] if game == "cooking" else 1
        for index, trajectory in enumerate(games):
            seed = index + 1
            log_path = os.path.join(folder, "logs", game, f"{mode}think_level{level}-seed{seed}.log")
            log_text = None
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    log_text = f.read()
                if not log_matches(log_text, trajectory):
                    log_text = None
            backend = source["backend"]
            if backend is None and log_text is not None: # 7e: played one at a time (with speed lines) or in parallel
                backend = "transformers" if "SPEED:" in log_text else "vllm"
            loop_stopped = False
            if source["loop_window"] is not None:
                loop_stopped = None if log_text is None else "LOOP DETECTED" in log_text
            score = trajectory[-1][1]
            record = dict(model=MODEL, mode=mode, prompt=source["prompt"], game=game, level=level, seed=seed,
                          score=score, max_score=max_score, norm_score=score / max_score,
                          steps=trajectory[-1][0], loop_stopped=loop_stopped, seconds=round(trajectory[-1][2], 1),
                          trajectory=[(moves, s, round(t, 3)) for moves, s, t in trajectory],
                          max_steps=100, loop_window=source["loop_window"],
                          loop_max_distinct=2 if source["loop_window"] else None,
                          tokens=log_tokens(log_text, tokenizer) if log_text is not None and tokenizer else None,
                          time_kind={"transformers": "cpu", "vllm": "wall"}.get(backend),
                          profile="qwen3", settings=dict(backend=backend), source=f"legacy: {source['folder']}",
                          log=os.path.relpath(log_path, REPOSITORY) if log_text is not None else None)
            destination = record_path(LEGACY_RESULTS, MODEL, game, source["prompt"], mode, level, seed)
            if os.path.exists(destination) and not overwrite:
                with open(destination) as f:
                    existing = json.load(f)
                if existing["trajectory"] != json.loads(json.dumps(record["trajectory"])):
                    counts["conflicts"] += 1
                    print(f"  conflict: {destination} exists with a different game, kept")
                else:
                    counts["existing"] += 1
                continue
            save_record(destination, record)
            counts["imported"] += 1
            counts["with_log"] += log_text is not None
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-tokens", action="store_true", help="do not count the tokens in the logs")
    parser.add_argument("--overwrite", action="store_true", help="replace the records that already exist")
    args = parser.parse_args()
    tokenizer = None
    if not args.no_tokens:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(MODEL) # only the tokenizer is downloaded
    with open(os.path.join(REPOSITORY, "Code", "Games", "max_scores_cooking.pickle"), "rb") as f:
        max_scores_cooking = pickle.load(f)
    for source in SOURCES:
        print(source["folder"])
        counts = import_source(source, tokenizer, max_scores_cooking, args.overwrite)
        print(f"  {counts['imported']} imported ({counts['with_log']} with their log), "
              f"{counts['existing']} already imported, {counts['conflicts']} conflicts")


if __name__ == "__main__":
    main()
