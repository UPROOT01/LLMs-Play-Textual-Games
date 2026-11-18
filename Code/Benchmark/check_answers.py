"""What do the answers of the played games look like: one command, or something the game cannot read?

    python check_answers.py results/openai__gpt-oss-20b/cooking/CoT+nonempty+oneline

The folder is the one of a prompt (it holds a folder per mode, with the logs of the games). Made for gpt-oss-20b, which
answers some turns with a whole plan or with a sentence (THESIS_LOG.md, 2026-11-18). Per mode, the share of the
answers of each shape, and in how many games more than 20% of the answers are not commands ("bad games"):
    lines      several lines (a plan, or a command and a note)
    list       several commands in one line, separated by commas or semicolons
    marks      a bracket, a quote or a full stop around the command ("take knife)")
    sentence   a sentence and not a command ("We are stuck.")
    long       a command of more than 7 words, which the game does not know
The prompt option "+oneline" (agent.one_line) repairs the first three, so with it they should be near zero.
"""
import collections
import glob
import json
import os
import re
import sys

ANSWER = re.compile(r"(?:AGENT -+\n|</think>)(.*?)\n(?:GAME \++|CONTEXT INITIALIZED)", re.S)
SENTENCE = re.compile(r"(we |i |i’m|i'm|the goal|sorry|no |there |you |it |this |try |let)", re.I)
SHAPES = ("command", "lines", "list", "marks", "sentence", "long")


def shape(answer):
    if "\n" in answer:
        return "lines"
    if SENTENCE.match(answer):
        return "sentence"
    if "," in answer or ";" in answer:
        return "list"
    if answer != answer.strip(" .()\"'"):
        return "marks"
    return "long" if len(answer.split()) > 7 else "command"


def main(folder):
    print(f"{'mode':<10}{'games':>6}{'score':>7}{'answers':>9}" + "".join(f"{name:>10}" for name in SHAPES) + f"{'bad games':>11}")
    for mode in sorted(os.listdir(folder)):
        counts, scores, bad = collections.Counter(), [], 0
        for log in glob.glob(os.path.join(folder, mode, "*.log")):
            with open(log) as file:
                answers = [re.sub(r"\nLOOP DETECTED.*", "", answer.strip()) for answer in ANSWER.findall(file.read())]
            shapes = [shape(answer) for answer in answers]
            counts.update(shapes)
            bad += sum(name != "command" for name in shapes) > 0.2 * len(shapes)
            if os.path.exists(log[:-4] + ".json"):
                with open(log[:-4] + ".json") as file:
                    scores.append(json.load(file)["norm_score"])
        total = sum(counts.values())
        if total:
            print(f"{mode:<10}{len(scores):>6}{sum(scores) / max(len(scores), 1):>7.3f}{total:>9}"
                  + "".join(f"{100 * counts[name] / total:>9.1f}%" for name in SHAPES) + f"{bad:>11}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
