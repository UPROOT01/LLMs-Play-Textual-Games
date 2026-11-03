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
    gsurp     surprise: with the "predict" prompt (duel_prompts.py) the agent says, after each move, which move it
              expects from the other player. After every answer the same model, without thinking, in a context of its
              own, reads what the agent wrote (its reasoning when it thought, and its answer) and extracts the
              expected move (the second context window: the prediction is not parsed by hand only). Before the next
              move the gate compares it with the move the other player actually made: the agent thinks when it is
              surprised (the expected move was not played) or when it had no expectation (nothing extracted). The
              first move of a game is never a surprise (there is nothing to compare).
              Any game: no solver. The regex reading of the answer ("4 (expecting 3)") is recorded too, to measure
              how often the extractor and the answer agree.
A gate mode never thinks on a clock; "lg<gate>" also keeps the last reasoning in the context until the next one
(the "previous" memory of l1), "g<gate>" drops every reasoning (ephemeral, like e1).

Every decision is recorded (gate.py returns a dict per move, saved in the .moves.jsonl of the game by duel.py): the
fast move and its solver score, the solver's view of the position, the gate's answer and its tokens.
"""
import re

from c4solver import analyze_scores, move_quality

GATE_PATTERN = re.compile(r"^(?P<kind>oracle|crit|self|rand|surp)(?P<n>\d*)$")
EXTRACT_MAX_TOKENS = 12 # the extractor answers with a move or NONE
EXTRACT_TAIL = 8000 # characters of the end of a reasoning read by the extractor (the expectation is usually at the end)
EXTRACT_QUESTION = {
    "connect_four": "The text above was written by a Connect Four player who then dropped a disc in column {move}. "
                    "Which column does the player expect the other player to play next? Answer only with the column "
                    "number, or NONE if the text does not say.",
    "chess": "The text above was written by a chess player who then played {move}. Which move does the player expect "
             "the other player to make next? Answer only with the move in standard algebraic notation, or NONE if the "
             "text does not say.",
}
SELF_GATE_MAX_TOKENS = 12 # "<think>\n\n</think>\n\nYES" is 5 tokens for Qwen3
SELF_GATE_QUESTION = ("Before choosing a move: does this position need careful thought (a move that wins at once, "
                      "a threat that must be blocked, or a move that would give the other player a win), or is any "
                      "reasonable move fine? Answer only YES if careful thought is needed, or NO if it is not.")


def parse_gate(name):
    """(kind, parameter) of a gate name: "oracle", "crit" (k), "self", "rand" (percentage), "surp"."""
    match = GATE_PATTERN.match(name)
    if match is None:
        raise ValueError(f"unknown gate {name!r}: oracle, crit<k>, self, surp or rand<p>")
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
    bind(game) is called by duel.py at the start of a game and before every move, so the gate reads the position;
    after(agent, command) by the agent after every answer (what the gate keeps from it: None for most gates);
    reset() by the agent at the start of a game."""
    name = None

    def __init__(self):
        self.game = None

    def bind(self, game):
        self.game = game

    def reset(self):
        pass

    def decide(self, agent, obs):
        raise NotImplementedError

    def after(self, agent, command):
        return None

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


class SurpriseGate(Gate):
    """Thinks when the other player did not play the move the agent expected (see the module docstring).
    expected: the move extracted from the agent's last answer (normalized as the game writes its moves), pending: the
    one extracted during the current move (it becomes expected at the next ply: after an illegal answer the agent
    answers again at the same ply, and its expectation must not change under it)."""
    name = "surp"

    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self.expected = None
        self.pending = None
        self.ply = None

    def bind(self, game):
        if game is not self.game:
            self.reset()
        super().bind(game)

    def decide(self, agent, obs):
        if self.game is None:
            raise RuntimeError("the gate is not bound to a game")
        ply = len(self.game.history)
        if ply != self.ply: # a new move: what was extracted during the last one is now the expectation
            self.expected, self.pending, self.ply = self.pending, None, ply
        actual = self.game.history[-1] if self.game.history else None
        if actual is None:
            think, surprised, reason = False, None, "first move of the game"
        elif self.expected is None:
            think, surprised, reason = True, None, "no expectation"
        elif self.expected == actual:
            think, surprised, reason = False, False, f"as expected: {actual}"
        else:
            think, surprised, reason = True, True, f"surprised: expected {self.expected}, got {actual}"
        return dict(think=think, fast=None, expected=self.expected, actual=actual, surprised=surprised, reason=reason)

    def after(self, agent, command):
        """Reads the agent's answer (and its reasoning when it thought) with the extractor and keeps the expected
        move. Returns dict(expected, raw: the extractor's answer, regex: the prediction parsed from the answer by hand,
        gate_tokens)."""
        game = self.game
        regex = game.parse_prediction(command) if game is not None else None
        thought = bool(agent.last_move_info and agent.last_move_info.get("think"))
        text = ((agent.last_thought or "") if thought else "").replace("<think>", "").replace("</think>", "").strip()
        text = text[-EXTRACT_TAIL:]
        move = game.parse(command) if game is not None else None
        if not text and (regex is None and len(command.split()) <= 1):
            # a bare move without reasoning: there is nothing to read
            self.pending = None
            return dict(expected=None, raw=None, regex=regex, gate_tokens=0)
        question = EXTRACT_QUESTION.get(game.name if game is not None else "", EXTRACT_QUESTION["connect_four"])
        profile = agent.profile
        body = (text + "\n\n" if text else "") + f"Answer: {command}\n\n" + question.format(move=move or command)
        prompt = (profile.system(agent.system_prompt) + profile.user(body, think=False) + profile.assistant_start
                  + profile.nothink_prefill)
        output_ids, _ = agent._generate(think=False, prompt=prompt, kind="gate", max_tokens=EXTRACT_MAX_TOKENS)
        raw = agent._clean(agent._decode(output_ids[agent._answer_start(output_ids):])).strip()
        expected = None if re.search(r"\bnone\b", raw, re.I) else (game._prediction(raw) if game is not None else None)
        self.pending = expected
        return dict(expected=expected, raw=raw, regex=regex, gate_tokens=len(output_ids))


def make_gate(name, solver=None):
    """The gate of a gate name ("oracle", "crit2", "self", "rand30", "surp"). solver: a c4solver.Solver for oracle /
    crit."""
    kind, n = parse_gate(name)
    if kind in ("oracle", "crit"):
        if solver is None:
            from c4solver import shared_solver
            solver = shared_solver()
        return OracleGate(solver) if kind == "oracle" else CriticalGate(solver, n)
    if kind == "self":
        return SelfGate()
    if kind == "surp":
        return SurpriseGate()
    return RandomGate(n)
