"""Adds Stockfish's adjudication (see duel.py) to the chess records already played, from their final position:
final_eval, adjudicated_result, adjudicated_score. The games are not played again.

    python adjudicate.py results_new                # only the records without an evaluation
    python adjudicate.py results_new --cp 500 --all # again for all of them, with another threshold
"""
import argparse
import json
import os

from duel import ADJUDICATION_CP, SCORES, adjudicate, evaluate
from records import save_record


def adjudicate_records(folder, threshold=ADJUDICATION_CP, redo=False):
    """Adjudicates the chess records of the two-player games in folder. redo: also those already evaluated."""
    changed = 0
    for root, _, files in os.walk(folder):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            with open(path) as f:
                record = json.load(f)
            if record.get("source") != "benchmark-duel" or record.get("game") != "chess":
                continue
            if record.get("final_eval") is not None and not redo:
                continue
            white_eval = evaluate(record["final_state"])
            record["final_eval"] = white_eval if record["color"] == "White" else -white_eval
            record["adjudicated_result"] = adjudicate(record["result"], record["termination"], record["final_eval"],
                                                      threshold)
            record["adjudicated_score"] = SCORES[record["adjudicated_result"]]
            record["adjudicate"], record["adjudicate_cp"] = "stockfish", threshold
            save_record(path, record)
            changed += 1
    print(f"{changed} chess records adjudicated in {folder}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="results folder")
    parser.add_argument("--cp", type=int, default=ADJUDICATION_CP, help="advantage in centipawns that wins a game")
    parser.add_argument("--all", action="store_true", help="also the records already evaluated")
    args = parser.parse_args()
    adjudicate_records(args.folder, args.cp, args.all)


if __name__ == "__main__":
    main()
