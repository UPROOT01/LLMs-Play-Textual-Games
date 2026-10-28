"""The two-player games (duel_games.py, duel.py, run_duel.py), without GPU: the rules and the parsing of the answers,
the opponents, the game loop with illegal answers and self-play, and run_duel end to end with a fake vLLM backend
(the one of test_pipeline.py, answering moves read from the prompt). Stockfish is tested only if it is installed.

    python tests/test_duel.py
"""
import json
import os
import random
import re
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import duel
import engine
import run_duel
from duel_games import Chess, ConnectFour, make_game
from duel_prompts import DUEL_PROMPTS, DUEL_SAMPLING
from records import load_table, record_path
from test_pipeline import TOKENIZER, FakeEngine


def test_chess_rules():
    game = Chess()
    for move in ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6"]:
        game.play(game.parse(move))
    assert game.parse("Qxf7#") == "Qxf7#" and game.parse("Qxf7") == "Qxf7#" # the check sign is optional
    assert game.parse("h5f7") == "Qxf7#" # UCI
    assert game.parse("I play 4. Qxf7# mate.") == "Qxf7#"
    assert game.parse("Ke9") is None and game.parse("take the queen") is None
    assert game.greedy_move(random.Random(0)) == "Qxf7#" # it mates when it can
    game.play("Qxf7#")
    assert game.over() and game.winner() == 0 and game.termination() == "checkmate"
    assert game.summary(0)["pgn"].startswith("1. e4 e5 2. Bc4") and game.material(0) == 1

    game = Chess()
    assert game.parse("0-0") is None and game.parse("nf3") == "Nf3" and game.parse("1...e5") is None
    assert game.parse("1. e4") == "e4"
    obs = game.observation(0)
    assert "move first" in obs and "Your legal moves:" in obs and "FEN:" in obs and "8 r n b q k b n r" in obs
    obs = Chess(board="fen", legal_moves=False).observation(0)
    assert "FEN:" in obs and "legal moves" not in obs and "Board" not in obs
    game.play("e4")
    assert game.observation(1, last_move="e4").startswith("White played e4.")

    # greedy takes the most valuable piece: the queen on g4, not the pawn on e4 (defended by the queen)
    game = Chess()
    for move in ["e4", "e5", "Qg4", "d5", "a3"]:
        game.play(move)
    assert game.greedy_move(random.Random(0)) == "Bxg4"

    # a random opening is the same for the same seed
    assert Chess(rng=random.Random(3), opening_plies=4).history == Chess(rng=random.Random(3), opening_plies=4).history
    assert len(Chess(rng=random.Random(3), opening_plies=4).history) == 4
    print("ok chess: rules, parsing, observations, greedy, openings")


def test_connect_four_rules():
    game = ConnectFour()
    for move in ["1", "2", "1", "2", "1", "2"]:
        game.play(move)
    assert game.greedy_move(random.Random(0)) == "1" # X wins in column 1
    game.play("3") # X does not see it
    assert game.greedy_move(random.Random(0)) == "2" # O wins in column 2
    game.play("2")
    assert game.over() and game.winner() == 1 and game.termination() == "four_in_a_row"

    game = ConnectFour()
    for move in ["1", "2", "3"]: # X threatens 1-2-3-4? no: 1 and 3 are X, 2 is O
        game.play(move)
    assert game.parse("column 4") == "4" and game.parse("8") is None and game.parse("I'd say 5.") == "5"
    for move in ["4"] * 6:
        game.play(move)
    assert "4" not in game.legal_moves() and game.parse("4") is None

    # diagonal
    game = ConnectFour()
    for move in ["1", "2", "2", "3", "4", "3", "3", "4", "4", "7", "4"]:
        game.play(move)
    assert game.winner() == 0

    game = ConnectFour()
    obs = game.observation(0)
    assert "move first" in obs and "Columns you can play: 1, 2, 3, 4, 5, 6, 7" in obs and "| . . . . . . . |" in obs
    print("ok connect four: rules, parsing, observations, greedy")


def test_opponents():
    assert duel.parse_opponent("random") == ("random", None)
    assert duel.parse_opponent("sf3", "chess") == ("stockfish", dict(skill=3))
    assert duel.parse_opponent("elo1400", "chess") == ("stockfish", dict(elo=1400))
    assert duel.parse_opponent("self-er4") == ("self", "er4")
    for bad, game in (("sf3", "connect_four"), ("self-x9", None), ("minimax", None)):
        try:
            duel.parse_opponent(bad, game)
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        duel.stockfish_path()
    except FileNotFoundError:
        print("skip stockfish: not installed")
        return
    bot = duel.make_bot("sf0", "chess", random.Random(0))
    try:
        game = Chess()
        assert bot.move(game) in game.legal_moves()
    finally:
        bot.close()
    print("ok opponents: parsing, stockfish")


def test_adjudication():
    # only the games stopped at max_steps are adjudicated, by the agent's evaluation of the final position
    assert duel.adjudicate("draw", "max_steps", 350) == "win"
    assert duel.adjudicate("draw", "max_steps", -duel.MATE_SCORE) == "loss"
    assert duel.adjudicate("draw", "max_steps", 120) == "draw"
    assert duel.adjudicate("draw", "max_steps", 120, threshold=100) == "win"
    assert duel.adjudicate("loss", "checkmate", 900) == "loss" and duel.adjudicate("draw", "max_steps", None) == "draw"

    # in play_duel, with a fake Stockfish that sees White 5 pawns ahead: the agent plays Black, so it loses
    evaluate, duel.evaluate = duel.evaluate, lambda fen: 500
    try:
        result = duel.play_duel(duel.Contestants(ScriptedAgent(first_legal)), "chess", "random", seed=1, color=1,
                                max_steps=5, adjudicate_with="stockfish")
        assert result["result"] == "draw" and result["score"] == 0.5 # on the board
        assert result["final_eval"] == -500 and result["adjudicated_result"] == "loss"
        assert result["adjudicated_score"] == 0
        # without adjudication, the adjudicated fields are the result on the board
        result = duel.play_duel(duel.Contestants(ScriptedAgent(first_legal)), "chess", "random", seed=1, color=1,
                                max_steps=5)
        assert result["final_eval"] is None and result["adjudicated_result"] == "draw"

        # the records already played: adjudicate.py
        import adjudicate
        folder = tempfile.mkdtemp()
        record = dict(result, source="benchmark-duel", game="chess")
        record.pop("thoughts")
        with open(os.path.join(folder, "levelrandom-seed1.json"), "w") as f:
            json.dump(record, f)
        adjudicate.adjudicate_records(folder)
        with open(os.path.join(folder, "levelrandom-seed1.json")) as f:
            record = json.load(f)
        assert record["final_eval"] == -500 and record["adjudicated_result"] == "loss" and record["adjudicate_cp"] == 300
    finally:
        duel.evaluate = evaluate
    try:
        duel.play_duel(duel.Contestants(ScriptedAgent(first_legal)), "connect_four", "random", seed=1,
                       adjudicate_with="stockfish")
        raise AssertionError("Connect Four cannot be adjudicated by Stockfish")
    except ValueError:
        pass

    try:
        duel.stockfish_path()
    except FileNotFoundError:
        print("ok adjudication (skip the real Stockfish: not installed)")
        return
    import chess
    board = chess.Board()
    for move in ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]:
        board.push_san(move)
    assert duel.evaluate(board.fen()) == duel.MATE_SCORE # White checkmated Black
    assert abs(duel.evaluate(chess.STARTING_FEN)) < 100
    print("ok adjudication, with the real Stockfish")


class ScriptedAgent:
    """Answers with the given function of the observation; counts the resets. think_every: it "reasons" every that
    many answers (the reasoning is the observation's first line), as a self-evaluation would."""

    def __init__(self, answer, think_every=0):
        self.answer, self.observations, self.resets, self.logs = answer, [], 0, []
        self.think_every, self.turns, self.last_thought = think_every, [], None

    def act(self, obs, done=False):
        if done:
            self.resets += 1
            return ":)"
        self.observations.append(obs)
        think = bool(self.think_every) and len(self.observations) % self.think_every == 0
        self.turns.append(dict(think=think))
        if think:
            self.last_thought = "I think about: " + obs.split("\n")[0]
        return self.answer(obs)

    def _write_log(self, text):
        self.logs.append(text)

    def context_tokens(self):
        return 0


def first_legal(obs):
    line = [line for line in obs.split("\n") if line.startswith(("Your legal moves:", "Columns you can play:"))][-1]
    return line.split(": ", 1)[1].split(", ")[0]


def test_play_duel():
    # the agent always plays the first legal move, the opponent is random: the game ends, the score is the agent's
    agent = ScriptedAgent(first_legal)
    result = duel.play_duel(duel.Contestants(agent), "connect_four", "random", seed=1, color=0)
    assert result["termination"] in ("four_in_a_row", "full_board") and agent.resets == 1
    assert result["score"] == {"win": 1, "draw": 0.5, "loss": 0}[result["result"]] and result["max_score"] == 1
    assert result["color"] == "X" and result["illegal"] == 0 and result["moves"] == result["steps"]
    assert [ply for ply, *_ in result["trajectory"]] == list(range(1, result["plies"] + 1))
    # the same seed, the same game
    again = duel.play_duel(duel.Contestants(ScriptedAgent(first_legal)), "connect_four", "random", seed=1, color=0)
    assert again["history"] == result["history"]

    # always "4": after the column is full, 3+1 wrong answers, then a random move is played for it and it is told so
    agent = ScriptedAgent(lambda obs: "4")
    result = duel.play_duel(duel.Contestants(agent), "connect_four", "greedy", seed=2, color=1, max_illegal=3)
    if result["forced"]:
        assert result["illegal"] == 4 * result["forced"]
        retry = "That is not a legal move.\nColumns you can play: "
        assert any(obs.startswith(retry) and "Board" not in obs for obs in agent.observations)
        assert any(obs.startswith("Your answers were not legal moves") for obs in agent.observations)
    assert result["color"] == "O"
    # the forfeit: the agent that never answers a move loses at its first turn
    agent = ScriptedAgent(lambda obs: "pass")
    result = duel.play_duel(duel.Contestants(agent), "chess", "random", seed=1, color=0, max_illegal=2,
                            illegal_policy="forfeit")
    assert result["result"] == "loss" and result["termination"] == "forfeit_illegal" and result["steps"] == 3
    assert result["plies"] == 0

    # max_steps: a draw after 5 moves of the agent
    result = duel.play_duel(duel.Contestants(ScriptedAgent(first_legal)), "chess", "random", seed=1, color=1,
                            max_steps=5)
    assert result["termination"] == "max_steps" and result["result"] == "draw" and result["moves"] == 5
    assert result["plies"] == 11 and "material" in result and result["color"] == "Black"

    # self-play: the opponent agent plays the other side
    agent, opponent = ScriptedAgent(first_legal), ScriptedAgent(lambda obs: first_legal(obs))
    result = duel.play_duel(duel.Contestants(agent, opponent), "connect_four", "self-0", seed=1, color=1)
    assert agent.observations[0].startswith("X dropped") and opponent.observations[0].startswith("The game starts")
    assert result["opponent_counts"]["moves"] in (result["moves"], result["moves"] + 1)
    assert result["thoughts"] == [] # neither reasoned

    # both reason (every 2 and every 3 answers): the reasonings of both, at the right plies, and in the transcript
    agent, opponent = ScriptedAgent(first_legal, think_every=2), ScriptedAgent(first_legal, think_every=3)
    result = duel.play_duel(duel.Contestants(agent, opponent), "chess", "self-er4", seed=1, color=0, max_steps=12)
    plies = {thought["player"]: [t["ply"] for t in result["thoughts"] if t["player"] == thought["player"]]
             for thought in result["thoughts"]}
    # the agent (White) reasons at its moves 2, 4, 6, ... (plies 3, 7, 11, ...), the opponent at its moves 3, 6, ...
    # (plies 6, 12, ...), until the game ends (here by repetition: both always play their first legal move)
    assert result["termination"] == "threefold_repetition", result["termination"]
    assert plies == {"agent": list(range(3, result["plies"] + 1, 4)),
                     "opponent": list(range(6, result["plies"] + 1, 6))}, plies
    thought = result["thoughts"][1] # the opponent's at ply 6: it had read White's 3rd move
    assert thought["color"] == "Black" and thought["reasonings"][0]["answer"] == thought["move"]
    assert thought["reasonings"][0]["reasoning"] == f"I think about: White played {result['history'][4]}."
    path = os.path.join(tempfile.mkdtemp(), "game.transcript.md")
    duel.write_transcript(path, result, "test")
    with open(path) as f:
        transcript = f.read()
    assert "## 2. " + result["history"][2] + "  (White, agent)" in transcript
    assert "## 3... " + result["history"][5] + "  (Black, opponent)" in transcript
    assert "1. " + result["history"][0] + "\n1... " in transcript # no reasoning: only the move
    print("ok play_duel: results, seeds, illegal answers, forfeit, max_steps, self-play, reasonings and transcript")


class MoveBackend:
    """A fake vLLM backend that plays the first legal move of the last observation in the prompt; self-evaluations
    think first. Connect Four normal turns always answer "4", so that its illegal answers are tested too."""

    def __init__(self):
        self.pending = []

    def add_request(self, request_id, prompt, params):
        text = TOKENIZER.decode(prompt["prompt_token_ids"])
        move = first_legal(text[text.rindex("<|im_start|>user"):])
        if params["max_tokens"] > 100:
            answer = f"<think>\nthe first move is the best\n</think>\n\n{move}"
        else:
            answer = "<think>\n\n</think>\n\n" + ("4" if "Columns you can play" in text else move)
        self.pending.append((request_id, TOKENIZER.encode(answer, add_special_tokens=False) + [TOKENIZER.eos_token_id]))

    def step(self):
        outputs = [types.SimpleNamespace(request_id=request_id, finished=True,
                                         outputs=[types.SimpleNamespace(token_ids=ids, finish_reason="stop")])
                   for request_id, ids in self.pending]
        self.pending = []
        return outputs

    def abort_request(self, ids):
        pass


class DuelFakeEngine(FakeEngine):
    def __init__(self, model, gpu_memory_utilization=0.9, **settings):
        super().__init__(model, gpu_memory_utilization, **settings)
        self.backend = MoveBackend()


def test_run_duel_grid():
    engine.Engine = DuelFakeEngine
    DuelFakeEngine.loaded = []
    results = tempfile.mkdtemp()
    grid = dict(run_duel.DUEL_DEFAULTS, models=["Qwen/Qwen3-1.7B"], modes=["0", "er4"], prompts=["CoT"],
                games={"connect_four": ["random", "greedy", "self-0"], "chess": ["random"]}, seeds=[1, 2],
                max_steps=12, game_options={"chess": {"board": "ascii"}})
    run_duel.run_duel_grid(grid, results, read=[], n_parallel=4, verbose=False)
    table = load_table(results)
    assert len(table) == 2 * 4 * 2, len(table)
    assert set(table.result) <= {"win", "draw", "loss"} and (table.source == "benchmark-duel").all()
    assert set(table[table.game == "chess"].termination) <= {"max_steps", "checkmate", "stalemate"}

    with open(record_path(results, "Qwen/Qwen3-1.7B", "connect_four", "CoT", "er4", "self-0", 2)) as f:
        record = json.load(f)
    assert record["color"] == "O" and record["opponent"] == "self-0" and record["opponent_tokens"]["think_turns"] == 0
    assert record["tokens"]["think_turns"] > 0 and record["norm_score"] == record["score"]
    path = record_path(results, "Qwen/Qwen3-1.7B", "connect_four", "CoT", "er4", "self-0", 2)[:-5]
    with open(path + ".log") as f:
        log = f.read()
    assert "the first move is the best" in log and "connect_four against self-0" in log and "game over" in log
    assert os.path.exists(path + ".opponent.log")
    # the reasonings of the agent (er4 thinks, self-0 never does): in the thoughts and in the transcript
    with open(path + ".thoughts.jsonl") as f:
        thoughts = [json.loads(line) for line in f]
    assert thoughts and {thought["player"] for thought in thoughts} == {"agent"}
    assert sum(len(thought["reasonings"]) for thought in thoughts) == record["tokens"]["think_turns"]
    assert "the first move is the best" in thoughts[0]["reasonings"][0]["reasoning"]
    with open(path + ".transcript.md") as f:
        transcript = f.read()
    assert transcript.startswith("# connect_four, Qwen/Qwen3-1.7B (CoT): the agent (er4) against self-0, seed 2")
    assert "(O, agent" in transcript and "the first move is the best" in transcript and "<think>" not in transcript
    assert record["transcript"] == "levelself-0-seed2.transcript.md"
    with open(record_path(results, "Qwen/Qwen3-1.7B", "chess", "CoT", "0", "random", 1)) as f:
        record = json.load(f)
    assert record["color"] == "White" and record["pgn"].startswith("1. ") and record["game_options"] == {"board": "ascii"}

    # running again plays nothing
    DuelFakeEngine.loaded = []
    run_duel.run_duel_grid(grid, results, read=[], verbose=False)
    assert DuelFakeEngine.loaded == []
    print("ok run_duel_grid: records, logs, self-play, skipping")


def test_observation_tokens():
    """Tokens of a chess observation in the middle of a game, to size max_steps: the context grows by this much
    (plus the answer) at every move of the agent."""
    game = make_game("chess", rng=random.Random(0), opening_plies=20)
    for board in ("ascii", "fen", "both"):
        game.show_board = board
        tokens = len(TOKENIZER.encode(game.observation(game.turn, last_move=game.history[-1])))
        print(f"  chess observation, board={board}: {tokens} tokens")


def test_reasoning_features():
    """The counts of reasoning_analysis.py on a made-up reasoning of O in Connect Four."""
    from reasoning_analysis import features
    text = ("Okay, let's see. The user is playing as O. Wait, row 6 has X in column 3. "
            "Maybe X is trying to make a vertical line in column 3. The opponent could also be setting up a diagonal. "
            "O is trying to win too, but that is not a guess about X. I will block column 3.")
    found = features(text, "O")
    assert found["user_frame"] and found["waits"] == 1 and found["opponent_words"] == 1
    assert found["guesses"] == ["Maybe X is trying to make a vertical line in column 3.",
                                "The opponent could also be setting up a diagonal."], found["guesses"]
    assert features("White is trying to attack. Black is trying to defend.", "White")["guesses"] == \
        ["Black is trying to defend."]
    print("ok reasoning features: user frame, board, opponent, intention guesses")


def test_duel_prompts():
    """neutral and free never mention the opponent (reasoning about it must come from the model); free also has no
    steps, no user and no request to be brief (it does not structure the reasoning)."""
    for game, prompts in DUEL_PROMPTS.items():
        assert set(prompts) <= set(DUEL_SAMPLING)
        for prompt in ("neutral", "free", "plain"):
            text = (prompts[prompt]["system"] + prompts[prompt]["selfeval"]).lower()
            for word in ("opponent", "threat", "other player"):
                assert word not in text, (game, prompt, word)
        for prompt in ("free", "plain"):
            text = (prompts[prompt]["system"] + prompts[prompt]["selfeval"]).lower()
            for word in ("user", "step", "concise", "brief", "candidate", "evaluate", "how to win"):
                assert word not in text, (game, prompt, word)
        assert prompts["plain"]["system"] == prompts["free"]["system"] and not prompts["plain"]["selfeval"].strip()
    print("ok duel prompts: neutral, free and plain do not mention the opponent, free and plain do not structure the reasoning")


def test_duel_grid_files():
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "grids")
    from profiles import model_settings
    for name in sorted(os.listdir(folder)):
        if name.startswith("duel_"):
            grid = run_duel.load_duel_grid(os.path.join(folder, name))
            for model in grid["models"]:
                model_settings(model)
            for game, options in grid["game_options"].items():
                make_game(game, rng=random.Random(0), **options)
            for game in grid["games"]:
                for prompt in grid["prompts"]:
                    assert prompt in DUEL_PROMPTS[game] and prompt in DUEL_SAMPLING, (name, game, prompt)
            print(f"ok {name}: {len(list(run_duel.grid_games(grid)))} games")


if __name__ == "__main__":
    test_reasoning_features()
    test_duel_prompts()
    test_duel_grid_files()
    test_chess_rules()
    test_connect_four_rules()
    test_opponents()
    test_play_duel()
    test_adjudication()
    test_observation_tokens()
    test_run_duel_grid()
