"""Labelling by hand whether the guesses about the opponent are right, in the terminal.

It takes a fixed random sample of the checkable guesses of labels/guess_pairs.csv (exported by
reasoning_analysis.ipynb: a reasoning with a guess, next to the opponent's reasoning on the move guessed about) and
shows them one at a time: the board the guesser saw, its guesses with a bit of the reasoning before them, and the end
of the opponent's reasoning, where it decides. The labels go to labels/human_labels.csv after every answer, so it can
be stopped and resumed. They are the reference to check an automatic judge against (Cohen's kappa).

    python label.py              # 50 guesses (the same 50 every time: --seed 0)
    python label.py -n 80        # a larger sample: the first 50 are the same, 30 more are added

Keys: r right, p partly, w wrong, u unclear, n not a guess, then an optional note (Enter to skip it);
      m more of the opponent's reasoning, g more of the guesser's, h the rubric, b back (relabel the previous one),
      s skip (not labelled, shown again next time), q quit.
"""
import argparse
import json
import os
import random
import re
import textwrap
from datetime import datetime, timezone

import pandas as pd

from duel_games import make_game
from records import LEGACY_RESULTS, record_path
from reasoning_analysis import GAME_KEY

PAIRS = "labels/guess_pairs.csv"
LABELS = "labels/human_labels.csv"
KEY = GAME_KEY + ["ply"]
LABEL_KEYS = {"r": "right", "p": "partly", "w": "wrong", "u": "unclear", "n": "not_a_guess"}

RUBRIC = """
RUBRIC. Judge the INTENTION the guesser attributes to the other player, against the reason the other player gives
for its move in its own reasoning (shown below the guess).

  right    the main guess matches the reason the opponent gives for its move.
           "they are trying to block me"  <->  "the best move is to block X's vertical line in column 3"
  partly   the kind of plan is right but a detail is wrong: attacking, but on another line or column; blocking,
           but a different threat; or the right intention is there, next to wrong ones, without being the main one.
  wrong    the opponent's reason is a different one: "O is trying to build a line" when O was blocking, or when O
           had no plan ("column 4 is the center", "it is available").
  unclear  the opponent's reasoning gives no readable reason, or the guess is too vague to judge
           ("White is trying to improve their position").
  not_a_guess  the sentence is not about the opponent's intention: the keyword pattern caught something else.
           Typically the model calls ITSELF "the user" ("the user's previous moves were in column 2, so maybe they
           are trying to..." written by the player who played column 2), or it describes the board ("X is trying
           to block" said of its own disc). These measure how precise the automatic count is.

Rules:
  - several hypotheses ("vertical, horizontal or diagonal"): judge the MAIN one (the first, or the one the
    guesser acts on). Listing every possibility does not make a guess right.
  - judge the intention, not the board reading: a guess can be right even if the guesser misreads a square.
  - only the opponent's move just before counts (the one the guess is about), not its plans for later moves.
  - if the guesser talks about several opponent moves, judge the sentence about the last one.
"""


def sample(pairs, n, seed):
    """A fixed sample: the checkable pairs shuffled with the seed, the first n. A larger n keeps the first ones."""
    checkable = pairs[pairs.checkable].reset_index(drop=True)
    order = list(range(len(checkable)))
    random.Random(seed).shuffle(order)
    return checkable.iloc[order[:n]].reset_index(drop=True)


def find_record(row, folders):
    for folder in folders:
        path = record_path(folder, *(row[name] for name in GAME_KEY))
        if os.path.exists(path):
            return path
    return None


def board_and_reasoning(row, folders):
    """The board the guesser saw (after the opponent's move) and the guesser's full reasoning, from the record."""
    path = find_record(row, folders)
    if path is None:
        return None, None
    with open(path) as f:
        record = json.load(f)
    game = make_game(row.game, rng=random.Random(0), **(record.get("game_options") or {}))
    for move in record["history"][:row.ply - 1]:
        game.play(move)
    board = game._ascii() if row.game == "chess" else game._text()
    reasoning = None
    with open(path[:-len(".json")] + ".thoughts.jsonl") as f:
        for line in f:
            thought = json.loads(line)
            if thought["ply"] == row.ply and thought["reasonings"]:
                reasoning = re.sub(r"</?think>", "", thought["reasonings"][-1]["reasoning"] or "")
    return board, reasoning


def wrap(text, indent="    "):
    return "\n".join(textwrap.fill(line, 110, initial_indent=indent, subsequent_indent=indent) if line.strip() else ""
                     for line in text.strip().split("\n"))


def show(i, total, row, folders, opponent_chars=800, guesser_chars=400):
    board, reasoning = board_and_reasoning(row, folders)
    guesses = [g.strip() for g in str(row.guesses).split(" | ") if g.strip()]
    print("\n" + "=" * 110)
    print(f"[{i + 1}/{total}]  {row.game}, prompt {row.prompt}, seed {row.seed}  |  guesser: {row.color} "
          f"({row.player_mode}), ply {row.ply}  |  opponent: {row.opponent_mode}, just played {row.opponent_move}")
    if board:
        print("\nBoard the guesser saw (after the opponent's move):\n" + wrap(board, "    "))
    print(f"\nGUESSER ({row.color}) - its guesses about the opponent:")
    for guess in guesses:
        if reasoning and guess in reasoning and guesser_chars:
            start = reasoning.index(guess)
            before = reasoning[max(0, start - guesser_chars):start].strip()
            print(wrap("... " + before, "      ") if before else "")
        print(wrap(">>> " + guess))
    print(f"\nOPPONENT ({row.opponent_mode}) - end of its reasoning when it played {row.opponent_move}:")
    print(wrap("..." + str(row.opponent_reasoning_end)[-opponent_chars:]))
    return reasoning


def load_labels():
    if os.path.exists(LABELS):
        labels = pd.read_csv(LABELS, dtype={"level": str, "label": str, "note": str}).fillna({"note": ""})
        return labels
    return pd.DataFrame(columns=KEY + ["color", "player_mode", "opponent_mode", "guesses", "label", "note",
                                       "labelled_at"])


def key_of(row):
    return tuple(str(row[name]) for name in KEY)


def save(labels):
    os.makedirs(os.path.dirname(LABELS), exist_ok=True)
    labels.to_csv(LABELS, index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", type=int, default=50, help="size of the sample")
    parser.add_argument("--seed", type=int, default=0, help="seed of the sample (keep it fixed)")
    parser.add_argument("--results", nargs="*", default=[LEGACY_RESULTS, "results_new"], help="results folders")
    args = parser.parse_args()

    if not os.path.exists(PAIRS):
        raise SystemExit(f"{PAIRS} not found: run the labelling cell of reasoning_analysis.ipynb first")
    pairs = pd.read_csv(PAIRS, dtype={"level": str})
    todo = sample(pairs, args.n, args.seed)
    labels = load_labels()
    print(RUBRIC)
    i = 0
    while i < len(todo):
        row = todo.iloc[i]
        done = {key_of(r) for _, r in labels.iterrows()}
        if key_of(row) in done:
            i += 1
            continue
        opponent_chars, guesser_chars = 800, 400
        while True:
            show(i, len(todo), row, args.results, opponent_chars, guesser_chars)
            answer = input("\nlabel [r]ight [p]artly [w]rong [u]nclear [n]ot a guess | [m]ore opponent, [g]uesser, [h]elp, "
                           "[b]ack, [s]kip, [q]uit: ").strip().lower()
            if answer == "m":
                opponent_chars += 1500
            elif answer == "g":
                guesser_chars += 1500
            elif answer == "h":
                print(RUBRIC)
                input("(Enter to go on)")
            else:
                break
        if answer == "q":
            break
        if answer == "s":
            i += 1
            continue
        if answer == "b":
            if len(labels):
                last = labels.iloc[-1]
                labels = labels.iloc[:-1]
                save(labels)
                i = next((j for j in range(len(todo)) if key_of(todo.iloc[j]) == key_of(last)), i)
                print(f"removed the last label: label [{i + 1}] again")
            continue
        if answer not in LABEL_KEYS:
            print("unknown key")
            continue
        note = input("note (optional, Enter to skip): ").strip()
        labels = pd.concat([labels, pd.DataFrame([dict(
            **{name: row[name] for name in KEY}, color=row.color, player_mode=row.player_mode,
            opponent_mode=row.opponent_mode, guesses=row.guesses, label=LABEL_KEYS[answer], note=note,
            labelled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))])], ignore_index=True)
        save(labels)
        i += 1

    sampled = {key_of(r) for _, r in todo.iterrows()}
    mine = labels[[key_of(r) in sampled for _, r in labels.iterrows()]]
    print(f"\n{len(mine)} of {len(todo)} labelled, saved in {LABELS}")
    if len(mine):
        print(mine.label.value_counts().to_string())


if __name__ == "__main__":
    main()
