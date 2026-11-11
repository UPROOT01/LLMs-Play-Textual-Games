"""The self-evaluating agent against an opponent, in a two-player game (duel_games.py).

The agent is the one of agent.py, with the prompts of the game (duel_prompts.py): it reads an observation and answers
a move, thinking as its mode says. Between its turns the opponent moves. The opponent is the "level" of the game:
    "random": a random legal move;
    "greedy": one move ahead (duel_games: mates or captures in chess, wins or blocks in Connect Four);
    "sf<N>": Stockfish at skill level N (0-20), "elo<N>": Stockfish limited to N Elo (chess only, needs the
        stockfish binary: on the PATH, in /usr/games, or in the STOCKFISH environment variable);
    "self-<mode>": another agent of the same model, with that mode (e.g. "self-0": does thinking beat not thinking?).
        Both agents ask the same vLLM engine, so the self-play costs about twice the tokens of a game.

An answer that is not a legal move is not played: the agent reads that it is not legal, with the legal moves, and
answers again (the answer is not quoted back: the models copied it). After max_illegal wrong
answers in a row a random legal move is played for it (illegal_policy="random") or it loses (illegal_policy="forfeit").
The score is the agent's: 1 for a win, 0.5 for a draw, 0 for a loss; a game that reaches max_steps moves of the agent
is a draw (its material balance is in the record).
With adjudicate="stockfish" (chess), Stockfish at full strength evaluates the final position of every game
(final_eval, centipawns for the agent, +-100000 for a forced mate), and a game stopped at max_steps is won by the side
ahead by at least adjudicate_cp (draw otherwise): adjudicated_result and adjudicated_score. result and score stay those
played on the board; for the games not adjudicated, the adjudicated fields are the same.
"""
import os
import random
import re
import shutil
import time

from agent import SelfEvalAgent, parse_mode
from duel_games import make_game
from duel_prompts import DUEL_PROMPTS, DUEL_SAMPLING

LOG_DUEL = "DUEL: +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-"
OPPONENT_PATTERN = re.compile(r"^(?:(?P<simple>random|greedy)|sf(?P<skill>\d+)|elo(?P<elo>\d+)|self-(?P<mode>\w+))$")
STOCKFISH_SECONDS = 0.05 # thinking time of Stockfish per move: with a skill or Elo limit, enough to play at that level
ADJUDICATION_DEPTH = 16 # depth of the evaluation of a final position: with one thread, the same evaluation every time
ADJUDICATION_CP = 300 # advantage (centipawns: 100 = a pawn) that wins a game stopped at max_steps
MATE_SCORE = 100000 # final_eval of a forced mate
SCORES = {"win": 1, "draw": 0.5, "loss": 0}


class DuelAgent(SelfEvalAgent):
    """SelfEvalAgent with the prompts of a two-player game instead of those of the textual games.
    A gate mode (g<gate>, see gate.py) gets its gate here, with the Connect Four solver when it needs one (solver:
    a c4solver.Solver, or None for the shared one). shadow: on every thinking turn, also answer without thinking first
    (the fast move, recorded and not played: what thinking changed)."""

    def __init__(self, profile, tokenizer, generate, mode, game, prompt="CoT", rng=None, log=None, solver=None,
                 shadow=False):
        super().__init__(profile, tokenizer, generate, mode, "default", rng=rng, log=None)
        self.prompt = prompt
        self.system_prompt = DUEL_PROMPTS[game][prompt]["system"]
        self.selfeval_prompt = DUEL_PROMPTS[game][prompt]["selfeval"]
        self.sampling = DUEL_SAMPLING[prompt]
        self.log = log
        self.shadow = shadow
        if self.gate_name is not None:
            from gate import make_gate, needs_solver, parse_gate
            kind = parse_gate(self.gate_name)[0]
            if (needs_solver(self.gate_name) or kind == "tactic") and game != "connect_four":
                raise ValueError(f"mode {mode} reads a Connect Four position: it cannot play {game}")
            if kind == "fail":
                raise ValueError(f"mode {mode} reads the reply of a textual game: it cannot play {game}")
            self.gate = make_gate(self.gate_name, solver)
        if log:
            open(log, "w").close()
        self.reset() # again, with the system prompt of the game


def parse_opponent(level, game=None):
    """The kind of opponent of a level ("random", "greedy", "stockfish", "self") and its setting."""
    match = OPPONENT_PATTERN.match(str(level))
    if match is None:
        raise ValueError(f"unknown opponent {level!r}: random, greedy, sf<N>, elo<N> or self-<mode>")
    if match["simple"]:
        return match["simple"], None
    if match["mode"]:
        parse_mode(match["mode"]) # raises if the mode does not exist
        return "self", match["mode"]
    if game is not None and game != "chess":
        raise ValueError(f"opponent {level!r} only plays chess")
    if match["skill"]:
        return "stockfish", dict(skill=int(match["skill"]))
    return "stockfish", dict(elo=int(match["elo"]))


def stockfish_path():
    path = os.environ.get("STOCKFISH") or shutil.which("stockfish") or shutil.which("stockfish", path="/usr/games")
    if path is None:
        raise FileNotFoundError("Stockfish not found: install it (apt install stockfish) or set STOCKFISH")
    return path


class RandomOpponent:
    def __init__(self, rng):
        self.rng = rng

    def move(self, game):
        return self.rng.choice(game.legal_moves())

    def close(self):
        pass


class GreedyOpponent(RandomOpponent):
    def move(self, game):
        return game.greedy_move(self.rng)


class StockfishOpponent:
    def __init__(self, skill=None, elo=None, seconds=STOCKFISH_SECONDS):
        import chess.engine
        self.limit = chess.engine.Limit(time=seconds)
        self.engine = chess.engine.SimpleEngine.popen_uci(stockfish_path())
        options = {"Threads": 1, "Hash": 16}
        if skill is not None:
            options["Skill Level"] = skill
        if elo is not None:
            options.update({"UCI_LimitStrength": True, "UCI_Elo": elo})
        self.engine.configure(options)

    def move(self, game):
        move = self.engine.play(game.board, self.limit).move
        return game.board.san(move)

    def close(self):
        self.engine.quit()


def evaluate(fen, depth=ADJUDICATION_DEPTH):
    """Stockfish's evaluation of a chess position at full strength, in centipawns for White
    (+-MATE_SCORE for a forced mate, and for a checkmate on the board)."""
    import chess
    import chess.engine
    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path())
    try:
        engine.configure({"Threads": 1, "Hash": 16})
        info = engine.analyse(chess.Board(fen), chess.engine.Limit(depth=depth))
    finally:
        engine.quit()
    return info["score"].white().score(mate_score=MATE_SCORE)


def adjudicate(result, termination, agent_eval, threshold=ADJUDICATION_CP):
    """The result of a game for the agent, deciding the games stopped at max_steps by the evaluation of the final
    position (agent_eval: centipawns for the agent). The games that ended on the board keep their result."""
    if termination != "max_steps" or agent_eval is None:
        return result
    if agent_eval >= threshold:
        return "win"
    if agent_eval <= -threshold:
        return "loss"
    return "draw"


def make_bot(level, game, rng):
    """The opponent of a level that is not an agent."""
    kind, setting = parse_opponent(level, game)
    if kind == "random":
        return RandomOpponent(rng)
    if kind == "greedy":
        return GreedyOpponent(rng)
    if kind == "stockfish":
        return StockfishOpponent(**setting)
    raise ValueError(f"{level!r} is an agent, not a bot")


class Contestants:
    """The agent and, in self-play, the opponent agent: what engine.Engine.play() calls "the agent" of a game."""

    def __init__(self, agent, opponent=None):
        self.agent, self.opponent = agent, opponent

    def agents(self):
        return [agent for agent in (self.agent, self.opponent) if agent is not None]

    def context_tokens(self):
        """Both contexts are in the KV cache at the same time."""
        return sum(agent.context_tokens() for agent in self.agents())


def _agent_move(agent, game, side, last_move, note, counts, max_illegal, illegal_policy, rng, reasonings,
                attempts=None):
    """The move of an agent, asking again after an illegal answer. None: it forfeits.
    reasonings: the reasonings of its self-evaluations during this move are appended to it, with their answers;
    attempts: the agent's last_move_info of every answer (what the gate decided, the fast move) is appended to it."""
    observation = game.observation(side, last_move, note)
    gate = getattr(agent, "gate", None)
    if gate is not None:
        gate.bind(game)
    for _ in range(max_illegal + 1):
        answer = agent.act(observation)
        counts["turns"] += 1
        if attempts is not None and getattr(agent, "last_move_info", None) is not None:
            attempts.append(agent.last_move_info)
        if agent.turns[-1]["think"]:
            reasonings.append(dict(reasoning=agent.last_thought, answer=answer))
        move = game.parse(answer)
        if move is not None:
            counts["moves"] += 1
            return move
        counts["illegal"] += 1
        observation = game.retry_observation(side)
    if illegal_policy == "forfeit":
        agent._write_log(f"{LOG_DUEL}\n{max_illegal + 1} illegal answers: the agent forfeits")
        return None
    move = rng.choice(game.legal_moves())
    agent._write_log(f"{LOG_DUEL}\n{max_illegal + 1} illegal answers: the random move {move} is played")
    counts["forced"] += 1
    counts["moves"] += 1
    return move


def play_duel(contestants, game, level, seed, color=0, max_steps=100, max_illegal=3, illegal_policy="random",
              game_options=None, adjudicate_with=None, adjudicate_cp=ADJUDICATION_CP, solver=None):
    """One game of `game` (a name of duel_games.GAMES) between contestants.agent, playing side `color`
    (0: first), and the opponent `level` (see parse_opponent; for "self-<mode>", contestants.opponent).
    max_steps: moves of the agent before the game is called a draw. Returns a dict with the result (score, max_score,
    result, termination, ...), the moves, the counts of turns, illegal answers and forced moves of both sides, and
    the reasonings of both agents (thoughts: see write_transcript()).
    adjudicate_with: "stockfish" (chess only) to evaluate the final position and adjudicate the games stopped at
    max_steps (see adjudicate()), or None.
    solver: a c4solver.Solver (Connect Four): every move of an agent is recorded in `moves` with the solver's view
    of the position before it (position: scores of the columns, best class, safe moves) and the quality of the move
    played (quality: score, class, blunder), with the gate's decision and the fast move when there are any
    (see agent.act()); and the record gets the blunder counts (blunders, opponent_blunders) and the thinking
    frequency of the gate modes (think_moves / think_rate). Without a solver, `moves` has the gate decisions only.
    Every move also has the agent's prediction of the reply (prediction: the surprise gate's extractor, or the answer
    with the "predict" prompt) and the record counts them (predictions, prediction_hits)."""
    if illegal_policy not in ("random", "forfeit"):
        raise ValueError(f"unknown illegal_policy {illegal_policy!r}")
    if adjudicate_with not in (None, "stockfish") or (adjudicate_with and game != "chess"):
        raise ValueError(f"cannot adjudicate {game} with {adjudicate_with!r}: only chess, with stockfish")
    if solver is not None and game != "connect_four":
        solver = None # the solver only knows Connect Four
    name = f"{game}|{level}|{seed}"
    state = make_game(game, rng=random.Random(name + "|opening"), **(game_options or {}))
    forced_rng = random.Random(name + "|forced") # the random moves played for an agent after illegal answers
    kind, _ = parse_opponent(level, game)
    bot = None if kind == "self" else make_bot(level, game, random.Random(name + "|opponent"))
    players = {color: contestants.agent, 1 - color: contestants.opponent}
    counts = {side: dict(turns=0, moves=0, illegal=0, forced=0) for side in (0, 1)}
    notes = {0: None, 1: None} # told to an agent at its next turn
    roles = {color: "agent", 1 - color: "opponent"}
    thoughts = [] # the moves where an agent reasoned: ply, side, player, move, and its reasonings with their answers
    moves = [] # every move of an agent: the solver's view, the gate's decision, the fast move (see the docstring)
    last_move, forfeit, termination = None, None, None
    trajectory = [] # (ply, side, move, seconds)
    start = time.perf_counter()
    for agent in contestants.agents():
        agent._write_log(f"{LOG_DUEL}\n{game} against {level}, seed {seed}, the agent plays {state.sides[color]}")
    try:
        while not state.over():
            side = state.turn
            player = players[side]
            if player is None:
                move = bot.move(state)
            else:
                if side == color and counts[side]["moves"] >= max_steps:
                    termination = "max_steps"
                    break
                forced_before, illegal_before = counts[side]["forced"], counts[side]["illegal"]
                reasonings, attempts = [], []
                scores = solver.scores(state.history) if solver is not None else None
                generated_before = len(player.turns) if hasattr(player, "turns") else 0
                move = _agent_move(player, state, side, last_move, notes[side], counts[side], max_illegal,
                                   illegal_policy, forced_rng, reasonings, attempts)
                notes[side] = None
                forced = counts[side]["forced"] > forced_before
                if forced:
                    notes[side] = f"Your answers were not legal moves, so the random move {move} was played for you."
                if reasonings or move is None:
                    thoughts.append(dict(ply=len(state.history) + 1, side=side, color=state.sides[side],
                                         player=roles[side], move=move, forced=forced,
                                         illegal=counts[side]["illegal"] - illegal_before, reasonings=reasonings))
                moves.append(_move_record(state, side, roles[side], move, forced, counts[side]["illegal"] - illegal_before,
                                          attempts, scores, player, generated_before))
                if move is None:
                    forfeit = side
                    termination = "forfeit_illegal"
                    break
            state.play(move)
            trajectory.append((len(state.history), side, move, round(time.perf_counter() - start, 3)))
            last_move = move
    finally:
        if bot is not None:
            bot.close()
    if forfeit is not None:
        winner = 1 - forfeit
    elif termination == "max_steps":
        winner = None
    else:
        winner, termination = state.winner(), state.termination()
    result = "draw" if winner is None else ("win" if winner == color else "loss")
    agent_eval = None
    if adjudicate_with == "stockfish":
        white_eval = evaluate(state.board.fen())
        agent_eval = white_eval if color == 0 else -white_eval
    adjudicated = adjudicate(result, termination, agent_eval, adjudicate_cp)
    for agent in contestants.agents():
        agent._write_log(f"{LOG_DUEL}\ngame over: {termination}, the agent's result: {result}"
                         + (f", evaluation for the agent {agent_eval}, adjudicated: {adjudicated}"
                            if agent_eval is not None else ""))
        agent.act("", done=True)
    agent_counts, opponent_counts = counts[color], counts[1 - color]
    return dict(score=SCORES[result], max_score=1, result=result, termination=termination,
                final_eval=agent_eval, adjudicated_result=adjudicated, adjudicated_score=SCORES[adjudicated],
                color=state.sides[color], steps=agent_counts["turns"], moves=agent_counts["moves"],
                illegal=agent_counts["illegal"], forced=agent_counts["forced"],
                opponent_counts=opponent_counts if kind == "self" else None,
                plies=len(state.history), history=list(state.history), trajectory=trajectory, thoughts=thoughts,
                move_records=moves, **moves_summary(moves, solver is not None, state.history),
                loop_stopped=False, seconds=round(time.perf_counter() - start, 1), **state.summary(color))


def _move_record(state, side, player, move, forced, illegal, attempts, scores, agent, turns_before):
    """One line of the .moves.jsonl of a game: the move of an agent with the solver's view of the position before it
    (position, quality: see c4solver.analyze_scores() and move_quality()), the agent's decisions (attempts: the
    last_move_info of every answer, see agent.act()), and the tokens it generated for this move, by kind."""
    from c4solver import analyze_scores, move_quality
    command = attempts[-1]["command"] if attempts else None
    after = attempts[-1].get("after") if attempts else None
    record = dict(ply=len(state.history) + 1, side=side, color=state.sides[side], player=player, move=move,
                  forced=forced, illegal=illegal, think=any(attempt["think"] for attempt in attempts),
                  gate=attempts[0]["gate"] if attempts and attempts[0]["gate"] is not None else None,
                  fast=next((attempt["fast"] for attempt in attempts if attempt["fast"] is not None), None),
                  attempts=len(attempts), position=None, quality=None, fast_quality=None,
                  # the move the agent expects from the other player: by the extractor of the surprise gate (after)
                  # or, for every mode with the "predict" prompt, parsed from the answer
                  prediction=(after or {}).get("expected") if after else (state.parse_prediction(command) if command else None),
                  prediction_regex=(after or {}).get("regex") if after else (state.parse_prediction(command) if command else None),
                  extracted=after)
    if scores is not None:
        record["position"] = analyze_scores(scores)
        record["quality"] = move_quality(scores, move) if move is not None else None
        if record["fast"] is not None:
            fast_move = state.parse(record["fast"]["command"])
            record["fast_move"] = fast_move
            record["fast_quality"] = move_quality(scores, fast_move) if fast_move is not None else None
    turns = agent.turns[turns_before:] if hasattr(agent, "turns") else []
    record["tokens"] = {kind: sum(turn.get("generated", 0) for turn in turns
                                  if turn.get("kind", "think" if turn["think"] else "normal") == kind)
                        for kind in ("think", "normal", "gate", "shadow", "summary")}
    return record


def moves_summary(moves, with_solver, history=()):
    """The numbers of the record about the moves of both players: how many times each thought (think_moves,
    think_rate: the thinking frequency, what a gate mode chooses by itself), and with the solver how many of their
    moves were blunders (blunders, blunder_rate; the first move of the game is counted too) and how often the fast
    move of a thinking turn (oracle gate or shadow) was a blunder that thinking then repaired or not.
    With the moves of the game (history): how many moves came with a prediction of the other player's reply
    (predictions) and how many came true (prediction_hits; the reply is the next move of the history)."""
    summary = {}
    history = list(history)
    for player in ("agent", "opponent"):
        own = [move for move in moves if move["player"] == player]
        prefix = "" if player == "agent" else "opponent_"
        if not own:
            continue
        summary[prefix + "think_moves"] = sum(move["think"] for move in own)
        summary[prefix + "think_rate"] = round(summary[prefix + "think_moves"] / len(own), 3)
        predicted = [move for move in own if move.get("prediction") is not None and move["ply"] < len(history)]
        summary[prefix + "predictions"] = len(predicted)
        summary[prefix + "prediction_hits"] = sum(str(move["prediction"]) == str(history[move["ply"]]) for move in predicted)
        if with_solver:
            judged = [move for move in own if move["quality"] is not None]
            summary[prefix + "blunders"] = sum(bool(move["quality"]["blunder"]) for move in judged)
            summary[prefix + "blunder_rate"] = round(summary[prefix + "blunders"] / len(judged), 3) if judged else None
            fast = [move for move in own if move["think"] and move["fast_quality"] is not None and move["quality"] is not None]
            summary[prefix + "fast_blunders"] = sum(bool(move["fast_quality"]["blunder"]) for move in fast)
            summary[prefix + "repaired"] = sum(bool(move["fast_quality"]["blunder"]) and not move["quality"]["blunder"]
                                               for move in fast)
            summary[prefix + "spoiled"] = sum(not move["fast_quality"]["blunder"] and bool(move["quality"]["blunder"])
                                              for move in fast)
    return summary


def write_transcript(path, result, title):
    """The game as text, move by move, with the reasonings of both agents where they reasoned: to read what each one
    thought the other was doing, and check it against what the other was actually thinking.
    result: what play_duel() returned; title: the first line (e.g. the model, the modes, the opponent and the seed)."""
    sides = {thought["ply"]: thought for thought in result["thoughts"]}
    judged = {move["ply"]: move for move in result.get("move_records") or []}
    lines = [f"# {title}", "", f"Result for the agent ({result['color']}): {result['result']} ({result['termination']})"]
    if any(move["quality"] is not None for move in judged.values()):
        lines.append("Solver: each move of an agent is marked when it changes the game value (blunder: best class -> "
                     "class of the move); gate modes: whether the gate fired and the fast move it checked.")
    if result.get("final_eval") is not None:
        lines.append(f"Stockfish's evaluation of the final position for the agent: {result['final_eval']} centipawns, "
                     f"adjudicated result: {result['adjudicated_result']}")
    lines.append("")
    history = result["history"] + ([None] if result["termination"] == "forfeit_illegal" else [])
    for ply, move in enumerate(history, start=1):
        number = f"{(ply + 1) // 2}." if ply % 2 == 1 else f"{ply // 2}..."
        thought = sides.get(ply)
        verdict = _move_notes(judged.get(ply))
        if thought is None:
            lines.append(f"{number} {move}" + (f"  ({', '.join(verdict)})" if verdict else ""))
            continue
        notes = verdict
        if thought["illegal"]:
            notes.append(f"{thought['illegal']} illegal answers")
        if thought["forced"]:
            notes.append("random move played for it")
        if move is None:
            notes.append("forfeit")
        lines += ["", f"## {number} {move or '-'}  ({thought['color']}, {thought['player']}"
                  + (", " + ", ".join(notes) if notes else "") + ")", ""]
        for reasoning in thought["reasonings"]:
            text = reasoning["reasoning"].replace("<think>", "").replace("</think>", "").strip()
            lines += [text or "(reasoning cut off)", "", f"-> answer: {reasoning['answer']}", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _move_notes(record):
    """What the transcript says about a move from its .moves.jsonl record: the solver's verdict and the gate."""
    if record is None:
        return []
    notes = []
    quality = record.get("quality")
    if quality is not None and quality["blunder"]:
        notes.append(f"blunder: {record['position']['best_class']} -> {quality['cls']}")
    if record.get("gate") is not None:
        notes.append(("gate fired: " if record["think"] else "gate silent: ") + str(record["gate"].get("reason")))
    if record.get("fast") is not None and record["think"]:
        fast = f"fast move {record.get('fast_move') or record['fast']['command']!s}"
        if record.get("fast_quality"):
            fast += " (blunder)" if record["fast_quality"]["blunder"] else " (fine)"
        notes.append(fast)
    if record.get("prediction") is not None:
        notes.append(f"expects {record['prediction']}")
    return notes
