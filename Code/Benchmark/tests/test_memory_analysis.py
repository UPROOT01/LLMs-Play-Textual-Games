"""The log parser and the turn bookkeeping of memory_analysis.py, without the results.

    python tests/test_memory_analysis.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import memory_analysis as ma

STATUS = ">" + " " * 40 + "-= Kitchen =-{}/{}\n"
LOG = ("CONTEXT INITIALIZED +-+-+-+-+-+-+-+-+-+-\n"
       "GAME ++++++++++++++++++++++++++++++++++++++++++++++++++\nYou are hungry!\n\n" + STATUS.format(0, 0)
       + "AGENT -------------------------------------------------\nchop apple\n"
       "GAME ++++++++++++++++++++++++++++++++++++++++++++++++++\nThat's not a verb I recognise.\n\n" + STATUS.format(0, 1)
       + "THINK about how to win the game, but be concise.\nFINALLY, say your next action.\n\n"
       "SELF-EVALUATION: +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-\n<think>\nI should take the knife, then slice the apple.\n</think>take knife\n"
       "GAME ++++++++++++++++++++++++++++++++++++++++++++++++++\nYou take the knife.\n\nYour score has just gone up by one point.\n\n" + STATUS.format(1, 2)
       + "AGENT -------------------------------------------------\nSlice  apple\n"
       "GAME ++++++++++++++++++++++++++++++++++++++++++++++++++\nYou slice the apple.\n\n" + STATUS.format(2, 3)
       + "SELF-EVALUATION: +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-\n<think>\n\n</think>eat meal\n"
       "CONTEXT INITIALIZED +-+-+-+-+-+-+-+-+-+-\n"
       "GAME ++++++++++++++++++++++++++++++++++++++++++++++++++\nanother game\n" + STATUS.format(0, 0)
       + "AGENT -------------------------------------------------\nlook\n")


def test_parse():
    turns = ma.parse_cooking_log(LOG)
    assert [turn["command"] for turn in turns] == ["chop apple", "take knife", "slice apple", "eat meal"] # first game only
    assert [turn["think"] for turn in turns] == [False, True, False, True]
    assert turns[1]["reasoning"] == "I should take the knife, then slice the apple." and turns[3]["reasoning"] == ""
    assert "THINK about" not in turns[1]["obs"] and turns[1]["obs"].startswith("That's not a verb")
    assert [turn["score"] for turn in turns] == [0, 0, 1, 2] and [turn["gain"] for turn in turns] == [0, 1, 1, None]
    assert [turn["rejected"] for turn in turns] == [True, False, False, None]
    print("ok parse: turns, reasonings, scores, rejected commands, only the first game of a log")


def test_bookkeeping():
    turns = ma._annotate(ma.parse_cooking_log(LOG), "last")
    assert [turn["since"] for turn in turns] == [None, 0, 1, 0]
    assert [turn["visible"] for turn in turns] == [0, 0, 1, 0] # lr4 does not read its last reasoning while thinking
    assert [t["visible"] for t in ma._annotate(ma.parse_cooking_log(LOG), "all")] == [0, 0, 1, 1]
    assert [t["visible"] for t in ma._annotate(ma.parse_cooking_log(LOG), "none")] == [0, 0, 0, 0]
    assert ma.announced("slice apple", turns[1]["reasoning"]) and not ma.announced("roast apple", turns[1]["reasoning"])
    assert ma.keep_of("lr4") == "last" and ma.keep_of("er4") == "none" and ma.keep_of("r4") == "all" and ma.keep_of("0") == "none"
    print("ok bookkeeping: turns since the last reasoning, reasonings visible per memory, announced commands")


if __name__ == "__main__":
    test_parse()
    test_bookkeeping()
