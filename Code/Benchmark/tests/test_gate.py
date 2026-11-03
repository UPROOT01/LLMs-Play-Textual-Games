"""The gates (gate.py), the solver wrapper (c4solver.py) and the agent's gate / shadow flow (agent.py), without GPU:
the model is a function that answers from the prompt. The parts that need the compiled solver are skipped when it is
not there (python c4solver.py --setup).

    python tests/test_gate.py
"""
import json
import os
import random
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import agent as agent_module
import c4solver
import duel
import engine
import gate
import run_duel
from agent import parse_mode
from c4solver import ILLEGAL, analyze_scores, move_quality
from duel_games import ConnectFour, split_prediction
from profiles import Qwen3
from records import load_table, record_path
from test_duel import DuelFakeEngine, first_legal
from test_pipeline import TOKENIZER

PROFILE = Qwen3()


def test_parse():
    assert parse_mode("goracle") == dict(selfeval_turns=0, random_turns=False, keep="none", gate="oracle", summarize=False)
    assert parse_mode("lgcrit2") == dict(selfeval_turns=0, random_turns=False, keep="previous", gate="crit2", summarize=False)
    assert parse_mode("grand30")["gate"] == "rand30" and parse_mode("e1")["gate"] is None
    assert gate.parse_gate("crit") == ("crit", 1) and gate.parse_gate("rand5") == ("rand", 5)
    assert gate.parse_gate("self") == ("self", None) and gate.needs_solver("oracle") and not gate.needs_solver("self")
    for bad in ("gx", "grand", "goracle2", "gself1"):
        try:
            parse_mode(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert duel.parse_opponent("self-goracle") == ("self", "goracle")
    # the summary modes
    assert parse_mode("s1") == dict(selfeval_turns=1, random_turns=False, keep="all", gate=None, summarize=True)
    assert parse_mode("ls1")["keep"] == "previous" and parse_mode("ls1")["summarize"]
    assert parse_mode("sr4") == dict(selfeval_turns=4, random_turns=True, keep="all", gate=None, summarize=True)
    assert parse_mode("lsr4")["keep"] == "previous" and parse_mode("lsr4")["random_turns"]
    assert not parse_mode("l1")["summarize"] and not parse_mode("goracle")["summarize"]
    assert gate.parse_gate("surp") == ("surp", None) and not gate.needs_solver("surp")
    assert parse_mode("gsurp")["gate"] == "surp" and parse_mode("lgsurp")["keep"] == "previous"
    print("ok parse: gate modes, gates, opponents, summary modes")


def test_predictions():
    game = ConnectFour()
    assert split_prediction("4 (expecting 3)") == ("4", "3)")
    assert split_prediction("4, I expect 3") == ("4", "3")
    assert split_prediction("4") == ("4", None)
    assert game.parse("4 (expecting 3)") == "4" and game.parse_prediction("4 (expecting 3)") == "3"
    assert game.parse("Column 4, expecting the other player to play 3") == "4"
    assert game.parse_prediction("Column 4, expecting the other player to play 3") == "3"
    assert game.parse("4") == "4" and game.parse_prediction("4") is None
    assert game.parse_prediction("I expect nothing") is None
    assert game.parse("8 (expecting 3)") is None # 8 is not a column: the prediction is not taken as the move
    from duel_games import Chess
    chess = Chess()
    assert chess.parse("e4 (expecting e5)") == "e4" and chess.parse_prediction("e4 (expecting e5)") == "e5"
    assert chess.parse_prediction("Nf3 (expecting Nc6+)") == "Nc6" and chess.parse_prediction("Nf3") is None
    assert chess.parse_prediction("e4, expecting 0-0") == "O-O"
    print("ok predictions: the move and the expected reply of an answer")


def test_summary_modes():
    # s1: thinks every turn after the first; the reasoning is replaced in the context by its summary
    script = Script(fast=["2"], think=["4", "3"], summaries=["I plan the centre.", "I block column 3."])
    agent = make_agent("s1", script)
    obs = ConnectFour().observation(0)
    assert agent.act(obs) == "2"
    assert agent.act(obs) == "4"
    assert "let me think" not in agent.context and "<think>\nI plan the centre.\n</think>" in agent.context
    assert agent.last_summary == "I plan the centre." and "let me think" in agent.last_thought
    assert [turn["kind"] for turn in agent.turns] == ["normal", "think", "summary"]
    # the summary is written in a context of its own: the reasoning, then the question; the game is not in it
    assert agent_module.SUMMARY_QUESTION[:30] in script.prompts[-1] and "let me think" in script.prompts[-1]
    assert "Board" not in script.full[-1]
    assert agent.act(obs) == "3"
    assert "I plan the centre." in agent.context and "I block column 3." in agent.context # all the summaries stay
    stats = agent.token_stats()
    assert stats["summary_turns"] == 2 and stats["generated_summary"] > 0
    assert stats["generated"] == stats["generated_thinking"] + stats["generated_normal"] + stats["generated_summary"]
    # ls1: only the last summary stays, and it is read while thinking the next move
    script = Script(fast=["2"], think=["4", "3"], summaries=["first note", "second note"])
    agent = make_agent("ls1", script)
    agent.act(obs); agent.act(obs)
    assert "first note" in agent.context
    agent.act(obs)
    assert "first note" in script.full[-2] # the thinking prompt of the third move read the first note
    assert "first note" not in agent.context and "second note" in agent.context
    # e1 and l1 do not summarize
    script = Script(fast=["2"], think=["4"])
    agent = make_agent("l1", script)
    agent.act(obs); agent.act(obs)
    assert "let me think" in agent.context and agent.token_stats()["summary_turns"] == 0
    print("ok summary modes: the summaries replace the reasonings (all / last), in a context of their own")


def test_surprise_gate():
    game = ConnectFour()
    # the agent plays X: first move, no surprise possible; it answers with a prediction, read by the extractor
    script = Script(fast=["4 (expecting 3)", "4 (expecting 4)"], think=["5 (expecting 2)"], extractions=["3", "NONE", "2"])
    agent = make_agent("gsurp", script, prompt="predict")
    agent.gate.bind(game)
    obs = game.observation(0)
    assert agent.act(obs) == "4 (expecting 3)"
    info = agent.last_move_info
    assert not info["think"] and info["gate"]["reason"] == "first move of the game"
    assert info["after"] == dict(expected="3", raw="3", regex="3", gate_tokens=info["after"]["gate_tokens"])
    assert info["after"]["gate_tokens"] > 0 and agent.turns[-1]["kind"] == "gate"
    game.play("4"); game.play("3") # the other player plays as expected
    assert agent.act(game.observation(0, last_move="3")) == "4 (expecting 4)"
    info = agent.last_move_info
    assert not info["think"] and info["gate"]["surprised"] is False and info["gate"]["reason"] == "as expected: 3"
    assert info["after"]["expected"] is None and info["after"]["regex"] == "4" # the extractor said NONE
    game.play("4"); game.play("5")
    assert agent.act(game.observation(0, last_move="5")) == "5 (expecting 2)"
    info = agent.last_move_info
    assert info["think"] and info["gate"]["reason"] == "no expectation" and info["gate"]["surprised"] is None
    assert info["after"]["expected"] == "2" and "let me think" in script.prompts[-1] # the extractor read the reasoning
    game.play("5"); game.play("1") # a surprise
    script.fast.append("4"); script.think.append("3 (expecting 6)"); script.extractions.append("6")
    assert agent.act(game.observation(0, last_move="1")) == "3 (expecting 6)"
    info = agent.last_move_info
    assert info["think"] and info["gate"]["surprised"] and info["gate"]["reason"] == "surprised: expected 2, got 1"
    # a bare answer without reasoning is not sent to the extractor
    game.play("3"); game.play("6")
    assert agent.act(game.observation(0, last_move="6")) == "4" and agent.last_move_info["after"]["raw"] is None
    assert not script.extractions
    print("ok surprise gate: first move, as expected, no expectation, surprised, the extractor's reading")


def test_play_duel_surprise():
    def model(prompt_ids, max_tokens, sampling):
        text = TOKENIZER.decode(prompt_ids)
        last_user = text[text.rindex("<|im_start|>user"):]
        if "Which column does the player expect" in last_user:
            answer = re.search(r"expecting (\d)", last_user).group(1)
        elif "/think" in last_user:
            answer = "<think>\nthinking\n</think>\n\n" + first_legal(last_user) + " (expecting 4)"
        else:
            answer = "<think>\n\n</think>\n\n" + first_legal(last_user) + " (expecting 4)"
        return TOKENIZER.encode(answer, add_special_tokens=False), False

    agent = make_agent("gsurp", model, prompt="predict")
    result = duel.play_duel(duel.Contestants(agent), "connect_four", "greedy", seed=2, color=1)
    moves = result["move_records"]
    assert all(move["prediction"] == "4" and move["prediction_regex"] == "4" for move in moves)
    assert moves[0]["gate"]["reason"] == "no expectation" and moves[0]["think"]
    assert all(move["gate"]["reason"].startswith(("as expected", "surprised")) for move in moves[1:])
    assert result["predictions"] == len([move for move in moves if move["ply"] < result["plies"]])
    assert 0 <= result["prediction_hits"] <= result["predictions"]
    assert result["think_moves"] == 1 + sum(move["gate"]["surprised"] is True for move in moves[1:])
    assert all(move["tokens"]["gate"] > 0 for move in moves)
    # e1 with the predict prompt: the predictions are parsed from the answers, no extractor
    agent = make_agent("e1", model, prompt="predict")
    result = duel.play_duel(duel.Contestants(agent), "connect_four", "greedy", seed=2, color=1)
    assert all(move["prediction"] == "4" and move["extracted"] is None for move in result["move_records"])
    assert result["predictions"] > 0 and agent.token_stats()["gate_turns"] == 0
    path = os.path.join(tempfile.mkdtemp(), "game.transcript.md")
    duel.write_transcript(path, result, "test")
    with open(path) as f:
        assert "expects 4" in f.read()
    print("ok play_duel with the surprise gate: predictions, hits, the gate's reasons")



def test_scores():
    empty = [-2, -1, 0, 1, 0, -1, -2] # the first player wins only in the middle
    info = analyze_scores(empty)
    assert info["best"] == 1 and info["best_class"] == "win" and info["safe"] == ["4"] and info["forced"]
    assert move_quality(empty, "4") == dict(score=1, cls="win", blunder=False, lost=0)
    assert move_quality(empty, "3") == dict(score=0, cls="draw", blunder=True, lost=1)
    assert move_quality(empty, "1")["lost"] == 2
    full = [ILLEGAL, ILLEGAL, 18, -17, 0, 0, ILLEGAL]
    info = analyze_scores(full)
    assert info["legal"] == ["3", "4", "5", "6"] and info["immediate_win"] == ["3"] and info["n_safe"] == 1
    assert move_quality(full, "1")["score"] is None and move_quality(full, None)["blunder"] is None
    print("ok scores: classes, safe moves, blunders")


def test_solver():
    if not c4solver.available():
        print("skip solver: not compiled (python c4solver.py --setup)")
        return
    solver = c4solver.Solver()
    try:
        assert solver.scores("") == [-2, -1, 0, 1, 0, -1, -2]
        assert solver.scores(["4"]) == [-4, -2, -2, -1, -2, -2, -4]
        assert solver.scores("4453")[2] == -2 and solver.scores("4453") is solver.scores("4453") # cached
        assert solver.scores("1111111") is None # a full column: invalid
        game = ConnectFour()
        for move in ["1", "2", "1", "2", "1", "2"]:
            game.play(move)
        info = solver.analyze(game.history) # X wins at once in column 1
        assert info["immediate_win"] == ["1"] and info["best"] == 18 and "1" in info["safe"]
        game.play("3") # X misses it: O must block
        info = solver.analyze(game.history)
        assert info["immediate_win"] == ["2"] and move_quality(info["scores"], "2")["cls"] == "win"
        game.play("2")
        assert solver.scores(game.history) is None # the game is won: nothing to play
        assert solver.calls >= 5 and c4solver.shared_solver() is c4solver.shared_solver()
    finally:
        solver.close()
    print("ok solver: scores, cache, invalid positions, wins and blocks")


class Script:
    """The model: answers the fast moves and the thinking moves of a list, in order, with the kind of each prompt
    (a thinking prompt ends with "/think" in the last user turn; the gate question has its own text)."""

    def __init__(self, fast, think, gate_answers=(), summaries=(), extractions=()):
        self.fast, self.think, self.gate_answers = list(fast), list(think), list(gate_answers)
        self.summaries, self.extractions = list(summaries), list(extractions)
        self.prompts, self.full = [], [] # the last user turn of every prompt, and the whole prompts

    def __call__(self, prompt_ids, max_tokens, sampling):
        text = TOKENIZER.decode(prompt_ids)
        last_user = text[text.rindex("<|im_start|>user"):]
        self.prompts.append(last_user)
        self.full.append(text)
        if gate.SELF_GATE_QUESTION[:30] in last_user:
            answer = self.gate_answers.pop(0)
        elif agent_module.SUMMARY_QUESTION[:30] in last_user:
            answer = "<think>\n\n</think>\n\n" + self.summaries.pop(0)
        elif "Which column does the player expect" in last_user:
            answer = "<think>\n\n</think>\n\n" + self.extractions.pop(0)
        elif "/think" in last_user:
            answer = f"<think>\nlet me think\n</think>\n\n{self.think.pop(0)}"
        else:
            answer = "<think>\n\n</think>\n\n" + self.fast.pop(0)
        return TOKENIZER.encode(answer, add_special_tokens=False), False


def make_agent(mode, script, solver=None, shadow=False, prompt="plain"):
    return duel.DuelAgent(PROFILE, TOKENIZER, script, mode, "connect_four", prompt, rng=random.Random(0),
                          solver=solver, shadow=shadow)


def test_gates():
    if not c4solver.available():
        print("skip gates: the solver is not compiled")
        return
    solver = c4solver.shared_solver()
    game = ConnectFour()
    obs = game.observation(0)

    # oracle: the fast move 4 keeps the value (X wins with 4): no thinking, the preview is the normal turn
    script = Script(fast=["4"], think=[])
    agent = make_agent("goracle", script, solver)
    agent.gate.bind(game)
    assert agent.act(obs) == "4"
    info = agent.last_move_info
    assert not info["think"] and info["gate"]["reason"] == "fast move keeps the value" and info["fast"]["command"] == "4"
    assert [turn["kind"] for turn in agent.turns] == ["normal"] and agent.token_stats()["gate_turns"] == 0
    assert agent.context.endswith("4<|im_end|>\n") and len(script.prompts) == 1

    # oracle: the fast move 1 loses the win: it thinks, the fast answer is a gate cost, the thinking move is played
    script = Script(fast=["1"], think=["4"])
    agent = make_agent("goracle", script, solver)
    agent.gate.bind(game)
    assert agent.act(obs) == "4"
    info = agent.last_move_info
    assert info["think"] and info["gate"]["reason"] == "blunder" and info["gate"]["fast_quality"]["lost"] == 2
    assert [turn["kind"] for turn in agent.turns] == ["gate", "think"]
    stats = agent.token_stats()
    assert stats["gate_turns"] == 1 and stats["think_turns"] == 1 and stats["normal_turns"] == 0
    assert stats["generated"] == stats["generated_gate"] + stats["generated_thinking"]
    assert "let me think" not in agent.context # ephemeral
    assert "/think" in script.prompts[1] and "/think" not in script.prompts[0]

    # oracle: an illegal fast answer makes it think too
    script = Script(fast=["column eight"], think=["4"])
    agent = make_agent("goracle", script, solver)
    agent.gate.bind(game)
    assert agent.act(obs) == "4" and agent.last_move_info["gate"]["reason"] == "illegal fast answer"

    # crit: the empty board is critical (only 4 keeps the win); after 4, O loses anyway: not critical
    script = Script(fast=["3"], think=["4"])
    agent = make_agent("gcrit", script, solver)
    agent.gate.bind(game)
    assert agent.act(obs) == "4" and agent.last_move_info["gate"]["reason"] == "1 safe moves"
    game.play("4")
    agent.gate.bind(game)
    assert agent.act(game.observation(1, "4")) == "3" and not agent.last_move_info["think"]
    assert agent.last_move_info["gate"]["position"]["n_safe"] == 7
    assert [turn["kind"] for turn in agent.turns] == ["think", "normal"]

    # crit2: with 2 safe moves it fires too
    script = Script(fast=["1"], think=["4"])
    agent = make_agent("gcrit2", script, solver)
    two = ConnectFour()
    for move in ["4", "4", "3", "3", "5"]:
        two.play(move)
    safe = solver.analyze(two.history)["n_safe"]
    agent.gate.bind(two)
    agent.act(two.observation(1, "5"))
    assert agent.last_move_info["think"] == (safe <= 2)

    # self: the model's answer decides; its question is asked in a context of its own, with the board
    script = Script(fast=["3"], think=["4"], gate_answers=["YES", "No."])
    agent = make_agent("gself", script)
    agent.gate.bind(ConnectFour())
    assert agent.act(obs) == "4" and agent.last_move_info["think"] and agent.last_move_info["gate"]["parsed"] == "yes"
    assert "| . . . . . . . |" in script.prompts[0] and "Your move" not in script.prompts[0]
    assert [turn["kind"] for turn in agent.turns] == ["gate", "think"]
    assert "YES" not in agent.context # the gate's answer never enters the agent's context
    assert agent.act(obs) == "3" and not agent.last_move_info["think"]
    assert agent.token_stats()["gate_turns"] == 2 and agent.token_stats()["normal_turns"] == 1

    # rand: with the agent's rng
    agent = make_agent("grand100", Script(fast=[], think=["4"]))
    assert agent.act(obs) == "4" and agent.last_move_info["think"]
    agent = make_agent("grand0", Script(fast=["4"], think=[]))
    assert agent.act(obs) == "4" and not agent.last_move_info["think"]

    # lg: the previous reasoning stays in the context and is read at the next thinking turn, then replaced
    script = Script(fast=["1", "1"], think=["4", "4"])
    agent = make_agent("lgoracle", script, solver)
    agent.gate.bind(ConnectFour())
    agent.act(obs)
    assert "let me think" in agent.context
    agent.act(obs)
    assert "let me think" in script.full[-1] and "/think" in script.prompts[-1] # read while thinking the 2nd time
    assert agent.context.count("let me think") == 1 # and then replaced by the new one

    # a gate mode without a gate (the textual games) fails clearly
    from agent import SelfEvalAgent
    plain = SelfEvalAgent(PROFILE, TOKENIZER, Script(fast=["look"], think=[]), "goracle", "CoT")
    try:
        plain.act("You are in a kitchen.")
    except RuntimeError:
        pass
    else:
        raise AssertionError("a gate mode needs a gate")
    print("ok gates: oracle, crit, self, rand, memory, kinds and tokens")


def test_shadow():
    # e1 with shadow (e1 answers its first move without thinking, then thinks at every move): on the thinking turn the
    # fast move is generated and recorded, not played, and not counted as a cost
    script = Script(fast=["2", "1"], think=["4"])
    agent = make_agent("e1", script, shadow=True)
    obs = ConnectFour().observation(0)
    assert agent.act(obs) == "2" and not agent.last_move_info["think"] and agent.last_move_info["fast"] is None
    assert agent.act(obs) == "4"
    info = agent.last_move_info
    assert info["think"] and info["fast"]["command"] == "1" and info["gate"] is None
    assert [turn["kind"] for turn in agent.turns] == ["normal", "shadow", "think"]
    stats = agent.token_stats()
    assert stats["shadow_turns"] == 1 and stats["generated"] == stats["generated_thinking"] + stats["generated_normal"]
    assert stats["generated_shadow"] > 0 and "1<|im_end|>" not in agent.context
    # r4 with shadow: the normal turns have no shadow (their answer is the fast move)
    script = Script(fast=["1", "2", "3", "4", "5", "6"], think=["4", "4"])
    agent = make_agent("r4", script, shadow=True)
    for _ in range(6):
        agent.act(ConnectFour().observation(0))
    kinds = [turn["kind"] for turn in agent.turns]
    assert kinds.count("shadow") == kinds.count("think") and kinds.count("normal") == 6 - kinds.count("think")
    print("ok shadow: fast moves recorded on the thinking turns, not played, not a cost")


def test_play_duel_with_solver():
    if not c4solver.available():
        print("skip play_duel with the solver: not compiled")
        return
    solver = c4solver.shared_solver()

    def model(prompt_ids, max_tokens, sampling):
        text = TOKENIZER.decode(prompt_ids)
        last_user = text[text.rindex("<|im_start|>user"):]
        if gate.SELF_GATE_QUESTION[:30] in last_user:
            answer = "NO"
        elif "/think" in last_user: # thinking: the last legal column (often a bad move)
            answer = "<think>\nthinking\n</think>\n\n" + first_legal(last_user).replace("1", "7")
        else:
            answer = "<think>\n\n</think>\n\n" + first_legal(last_user)
        return TOKENIZER.encode(answer, add_special_tokens=False), False

    agent = make_agent("goracle", model, solver)
    result = duel.play_duel(duel.Contestants(agent), "connect_four", "greedy", seed=3, color=0, solver=solver)
    moves = result["move_records"]
    assert len(moves) == result["moves"] and all(move["player"] == "agent" for move in moves)
    first = moves[0]
    assert first["ply"] == 1 and first["position"]["safe"] == ["4"] and first["gate"]["reason"] in ("blunder", "fast move keeps the value")
    assert first["fast_move"] == "1" and first["fast_quality"]["blunder"] and first["think"] # 1 loses the win: it thought
    assert first["quality"] is not None and set(first["tokens"]) == {"think", "normal", "gate", "shadow", "summary"}
    assert first["tokens"]["gate"] > 0 and first["tokens"]["think"] > 0
    assert result["think_moves"] == sum(move["think"] for move in moves) and 0 <= result["think_rate"] <= 1
    assert result["blunders"] == sum(bool(move["quality"]["blunder"]) for move in moves if move["quality"])
    assert result["fast_blunders"] >= result["repaired"] and "opponent_blunders" not in result # the bot is not judged
    assert result["termination"] in ("four_in_a_row", "full_board")

    # self-play: the opponent's moves are judged too; e1 with shadow records the fast moves
    agent = make_agent("e1", model, solver, shadow=True)
    opponent = make_agent("0", model, solver)
    result = duel.play_duel(duel.Contestants(agent, opponent), "connect_four", "self-0", seed=1, color=1, solver=solver)
    moves = result["move_records"]
    assert {move["player"] for move in moves} == {"agent", "opponent"}
    mine = [move for move in moves if move["player"] == "agent"]
    assert not mine[0]["think"] and mine[0]["fast"] is None # e1 does not think on its first move
    assert all(move["think"] and move["fast"] is not None and move["gate"] is None for move in mine[1:])
    assert all(move["tokens"]["shadow"] > 0 for move in mine[1:])
    assert "opponent_blunder_rate" in result and result["opponent_think_moves"] == 0
    assert result["think_moves"] == len(mine) - 1 and 0 < result["think_rate"] < 1
    assert "spoiled" in result and "repaired" in result
    path = os.path.join(tempfile.mkdtemp(), "game.transcript.md")
    duel.write_transcript(path, result, "test")
    with open(path) as f:
        transcript = f.read()
    assert "Solver:" in transcript and ("fast move" in transcript)
    # without a solver nothing is judged, the gate decisions stay
    agent = make_agent("grand100", model)
    result = duel.play_duel(duel.Contestants(agent), "connect_four", "random", seed=1, color=0)
    assert result["move_records"][0]["position"] is None and result["move_records"][0]["gate"]["reason"] == "p = 1.0"
    assert "blunders" not in result and result["think_rate"] == 1
    print("ok play_duel with the solver: move records, summary, transcript")


def test_run_duel_grid_with_gates():
    if not c4solver.available():
        print("skip run_duel_grid with gates: the solver is not compiled")
        return
    engine.Engine = DuelFakeEngine
    results = tempfile.mkdtemp()
    grid = dict(run_duel.DUEL_DEFAULTS, models=["Qwen/Qwen3-1.7B"], modes=["goracle", "gcrit", "e1"], prompts=["plain"],
                games={"connect_four": ["greedy", "self-grand50"]}, seeds=[11], max_steps=30, shadow_fast_move=True,
                solver=True, game_options={"connect_four": {"legal_moves": True}})
    assert run_duel.grid_needs_solver(grid)
    run_duel.run_duel_grid(grid, results, read=[], n_parallel=2, verbose=False)
    table = load_table(results)
    assert len(table) == 6 and (table.solver == True).all() and (table.shadow_fast_move == True).all()
    assert "blunder_rate" in table and "think_rate" in table and table.tokens_shadow_turns.notna().all()
    path = record_path(results, "Qwen/Qwen3-1.7B", "connect_four", "plain", "goracle", "self-grand50", 11)[:-5]
    with open(path + ".moves.jsonl") as f:
        moves = [json.loads(line) for line in f]
    assert moves and {move["player"] for move in moves} == {"agent", "opponent"}
    assert all(move["position"] is not None for move in moves)
    assert all(move["gate"]["reason"].startswith("p = ") for move in moves if move["player"] == "opponent")
    with open(path + ".json") as f:
        record = json.load(f)
    assert "move_records" not in record and record["think_moves"] == sum(m["think"] for m in moves if m["player"] == "agent")
    assert record["tokens"]["gate_turns"] == sum(m["think"] for m in moves if m["player"] == "agent") # the discarded fast answers
    with open(path + ".transcript.md") as f:
        assert "gate" in f.read()
    # a grid with a solver gate and solver: false fails before loading the model
    try:
        run_duel.grid_solver(dict(grid, solver=False))
    except ValueError:
        pass
    else:
        raise AssertionError("solver: false with gate modes must fail")
    assert run_duel.grid_solver(dict(grid, modes=["e1"], games={"chess": ["random"]})) is None
    print("ok run_duel_grid with gates: records, moves.jsonl, solver requirement")


if __name__ == "__main__":
    test_parse()
    test_predictions()
    test_summary_modes()
    test_surprise_gate()
    test_play_duel_surprise()
    test_scores()
    test_solver()
    test_gates()
    test_shadow()
    test_play_duel_with_solver()
    test_run_duel_grid_with_gates()
