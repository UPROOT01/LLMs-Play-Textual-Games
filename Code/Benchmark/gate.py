"""The gates: who decides, before each move, whether the agent thinks (modes "g<gate>" of agent.parse_mode()).

The fixed modes think on a clock (e1 every move, r4 about every 4). A gate mode thinks only when its gate says so:
    goracle   the agent first answers without thinking (the fast move); the solver (c4solver.py) checks it: if the fast
              move changes the game-theoretic value (a blunder) or is not legal, the agent thinks instead. The ceiling
              of every gate: it thinks exactly when thinking could repair something. Connect Four only.
    gcrit<k>  the solver says the position is critical: at most k moves (default 1) keep the game value (a forced win,
              a forced block, a single saving move). No model tokens: the gate is the solver. Connect Four only.
    gself     the same model, without thinking, reads the board and answers YES / NO to "does this position need
              careful thought?" (a few tokens). The gate that could be used in practice.
    grand<p>  thinks with probability p% at each move: at the firing rate of another gate, it controls for the
              frequency of thinking alone (our earlier result: thinking more often wins more).
A gate mode never thinks on a clock; "lg<gate>" also keeps the last reasoning in the context until the next one
(the "previous" memory of l1), "g<gate>" drops every reasoning (ephemeral, like e1).

Every decision is recorded (gate.py returns a dict per move, saved in the .moves.jsonl of the game by duel.py): the
fast move and its solver score, the solver's view of the position, the gate's answer and its tokens.
"""
import re

from c4solver import analyze_scores, move_quality

GATE_PATTERN = re.compile(r"^(?P<kind>oracle|crit|self|rand)(?P<n>\d*)$")
SELF_GATE_MAX_TOKENS = 12 # "<think>\n\n</think>\n\nYES" is 5 tokens for Qwen3
SELF_GATE_QUESTION = ("Before choosing a move: does this position need careful thought (a move that wins at once, "
                      "a threat that must be blocked, or a move that would give the other player a win), or is any "
                      "reasonable move fine? Answer only YES if careful thought is needed, or NO if it is not.")


def parse_gate(name):
    """(kind, parameter) of a gate name: "oracle", "crit" (k), "self", "rand" (percentage)."""
    match = GATE_PATTERN.match(name)
    if match is None:
        raise ValueError(f"unknown gate {name!r}: oracle, crit<k>, self or rand<p>")
    kind, n = match["kind"], match["n"]
    if kind == "rand":
        if not n:
            raise ValueError("grand needs a percentage: e.g. grand30")
        return kind, int(n)
    if kind == "crit":
        return kind, int(n) if n else 1
    if n:
        raise ValueError(f"gate {kind!r} takes no number")
    return kind, None


def needs_solver(name):
    return parse_gate(name)[0] in ("oracle", "crit")


class Gate:
    """decide(agent, obs) -> dict(think=bool, fast=the agent's preview of its fast answer or None, ...details).
    bind(game) is called by duel.py at the start of a game and before every move, so the gate reads the position."""
    name = None

    def __init__(self):
        self.game = None

    def bind(self, game):
        self.game = game

    def decide(self, agent, obs):
        raise NotImplementedError

    def _position(self):
        """The solver's view of the position (None when there is no solver or the game has no solver)."""
        return None


class SolverGate(Gate):
    def __init__(self, solver):
        super().__init__()
        self.solver = solver

    def _scores(self):
        if self.game is None:
            raise RuntimeError("the gate is not bound to a game")
        if self.game.name != "connect_four":
            raise ValueError(f"the {self.name} gate needs the Connect Four solver: {self.game.name} has none")
        return self.solver.scores(self.game.history)


class OracleGate(SolverGate):
    name = "oracle"

    def decide(self, agent, obs):
        fast = agent.preview(obs)
        move = self.game.parse(fast["command"])
        scores = self._scores()
        quality = move_quality(scores, move) if scores else dict(score=None, cls=None, blunder=None, lost=None)
        think = move is None or bool(quality["blunder"])
        return dict(think=think, fast=fast, fast_move=move, fast_quality=quality,
                    position=analyze_scores(scores) if scores else None,
                    reason="illegal fast answer" if move is None else ("blunder" if think else "fast move keeps the value"))


class CriticalGate(SolverGate):
    name = "crit"

    def __init__(self, solver, k=1):
        super().__init__(solver)
        self.k = k

    def decide(self, agent, obs):
        scores = self._scores()
        position = analyze_scores(scores) if scores else None
        think = position is not None and position["n_safe"] <= self.k
        return dict(think=think, fast=None, position=position,
                    reason=f"{position['n_safe']} safe moves" if position else "no solver scores")


class SelfGate(Gate):
    """The model itself, without thinking, says whether the position needs thought. It reads the board as the agent
    does (the game's observation), in a context of its own: the gate's answer never enters the agent's context."""
    name = "self"

    def decide(self, agent, obs):
        board = self.game.observation(self.game.turn) if self.game is not None else obs
        board = board.rsplit("\n", 1)[0] if self.game is not None else board # without "Your move:"
        profile = agent.profile
        prompt = (profile.system(agent.system_prompt) + profile.user(board + "\n" + SELF_GATE_QUESTION, think=False)
                  + profile.assistant_start + profile.nothink_prefill)
        output_ids, capped = agent._generate(think=False, prompt=prompt, kind="gate", max_tokens=SELF_GATE_MAX_TOKENS)
        answer = agent._clean(agent._decode(output_ids[agent._answer_start(output_ids):]))
        word = re.search(r"\b(yes|no)\b", answer, re.I)
        think = bool(word and word.group(1).lower() == "yes")
        return dict(think=think, fast=None, answer=answer, parsed=word.group(1).lower() if word else None,
                    gate_tokens=len(output_ids), reason="the model says " + (word.group(1).upper() if word else "nothing clear"))


class RandomGate(Gate):
    name = "rand"

    def __init__(self, percent):
        super().__init__()
        self.p = percent / 100

    def decide(self, agent, obs):
        draw = agent.rng.random()
        return dict(think=draw < self.p, fast=None, draw=round(draw, 4), reason=f"p = {self.p}")


def make_gate(name, solver=None):
    """The gate of a gate name ("oracle", "crit2", "self", "rand30"). solver: a c4solver.Solver for oracle / crit."""
    kind, n = parse_gate(name)
    if kind in ("oracle", "crit"):
        if solver is None:
            from c4solver import shared_solver
            solver = shared_solver()
        return OracleGate(solver) if kind == "oracle" else CriticalGate(solver, n)
    if kind == "self":
        return SelfGate()
    return RandomGate(n)
