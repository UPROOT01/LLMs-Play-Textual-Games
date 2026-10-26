"""The self-evaluating agent against an opponent, in a two-player game (duel_games.py).

The agent is the one of agent.py, with the prompts of the game (duel_prompts.py): it reads an observation and answers
a move, thinking as its mode says. Between its turns the opponent moves. The opponent is the "level" of the game:
    "random": a random legal move;
    "greedy": one move ahead (duel_games: mates or captures in chess, wins or blocks in Connect Four);
    "sf<N>": Stockfish at skill level N (0-20), "elo<N>": Stockfish limited to N Elo (chess only, needs the
        stockfish binary: on the PATH, in /usr/games, or in the STOCKFISH environment variable);
    "self-<mode>": another agent of the same model, with that mode (e.g. "self-0": does thinking beat not thinking?).
        Both agents ask the same vLLM engine, so the self-play costs about twice the tokens of a game.

An answer that is not a legal move is not played: the agent reads why and answers again. After max_illegal wrong
answers in a row a random legal move is played for it (illegal_policy="random") or it loses (illegal_policy="forfeit").
The score is the agent's: 1 for a win, 0.5 for a draw, 0 for a loss; a game that reaches max_steps moves of the agent
is a draw (its material balance is in the record).
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


class DuelAgent(SelfEvalAgent):
    """SelfEvalAgent with the prompts of a two-player game instead of those of the textual games."""

    def __init__(self, profile, tokenizer, generate, mode, game, prompt="CoT", rng=None, log=None):
        super().__init__(profile, tokenizer, generate, mode, "default", rng=rng, log=None)
        self.prompt = prompt
        self.system_prompt = DUEL_PROMPTS[game][prompt]["system"]
        self.selfeval_prompt = DUEL_PROMPTS[game][prompt]["selfeval"]
        self.sampling = DUEL_SAMPLING[prompt]
        self.log = log
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


def _agent_move(agent, game, side, last_move, note, counts, max_illegal, illegal_policy, rng):
    """The move of an agent, asking again after an illegal answer. None: it forfeits."""
    feedback = note
    for _ in range(max_illegal + 1):
        answer = agent.act(game.observation(side, last_move, feedback))
        counts["turns"] += 1
        move = game.parse(answer)
        if move is not None:
            counts["moves"] += 1
            return move
        counts["illegal"] += 1
        shown = answer if len(answer) <= 40 else answer[:40] + "..."
        feedback = f"Your answer {shown!r} is not a legal move. Answer again with one of your legal moves."
    if illegal_policy == "forfeit":
        agent._write_log(f"{LOG_DUEL}\n{max_illegal + 1} illegal answers: the agent forfeits")
        return None
    move = rng.choice(game.legal_moves())
    agent._write_log(f"{LOG_DUEL}\n{max_illegal + 1} illegal answers: the random move {move} is played")
    counts["forced"] += 1
    counts["moves"] += 1
    return move


def play_duel(contestants, game, level, seed, color=0, max_steps=100, max_illegal=3, illegal_policy="random",
              game_options=None):
    """One game of `game` (a name of duel_games.GAMES) between contestants.agent, playing side `color`
    (0: first), and the opponent `level` (see parse_opponent; for "self-<mode>", contestants.opponent).
    max_steps: moves of the agent before the game is called a draw. Returns a dict with the result (score, max_score,
    result, termination, ...), the moves, the counts of turns, illegal answers and forced moves of both sides."""
    if illegal_policy not in ("random", "forfeit"):
        raise ValueError(f"unknown illegal_policy {illegal_policy!r}")
    name = f"{game}|{level}|{seed}"
    state = make_game(game, rng=random.Random(name + "|opening"), **(game_options or {}))
    forced_rng = random.Random(name + "|forced") # the random moves played for an agent after illegal answers
    kind, _ = parse_opponent(level, game)
    bot = None if kind == "self" else make_bot(level, game, random.Random(name + "|opponent"))
    players = {color: contestants.agent, 1 - color: contestants.opponent}
    counts = {side: dict(turns=0, moves=0, illegal=0, forced=0) for side in (0, 1)}
    notes = {0: None, 1: None} # told to an agent at its next turn
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
                forced_before = counts[side]["forced"]
                move = _agent_move(player, state, side, last_move, notes[side], counts[side], max_illegal,
                                   illegal_policy, forced_rng)
                notes[side] = None
                if move is None:
                    forfeit = side
                    termination = "forfeit_illegal"
                    break
                if counts[side]["forced"] > forced_before:
                    notes[side] = f"Your answers were not legal moves, so the random move {move} was played for you."
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
    for agent in contestants.agents():
        agent._write_log(f"{LOG_DUEL}\ngame over: {termination}, the agent's result: {result}")
        agent.act("", done=True)
    agent_counts, opponent_counts = counts[color], counts[1 - color]
    return dict(score={"win": 1, "draw": 0.5, "loss": 0}[result], max_score=1, result=result, termination=termination,
                color=state.sides[color], steps=agent_counts["turns"], moves=agent_counts["moves"],
                illegal=agent_counts["illegal"], forced=agent_counts["forced"],
                opponent_counts=opponent_counts if kind == "self" else None,
                plies=len(state.history), history=list(state.history), trajectory=trajectory,
                loop_stopped=False, seconds=round(time.perf_counter() - start, 1), **state.summary(color))
