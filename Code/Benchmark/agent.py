"""The self-evaluating agent of Self-evaluation/self_evaluation.py (LLMAgentSelfEvaluate), for any model family.

The behaviour is the same, but:
- the conversation is written by a profile (profiles.py) instead of being written for Qwen3 only;
- the context is a list of pieces (system prompt, turns, reasonings, answers), so that removing a reasoning
  only means emptying its piece;
- the model is a function generate(prompt_ids, max_tokens, sampling) -> (output_ids, hit_limit),
  given by the engine (engine.py) or by a test;
- the random numbers come from the agent's own generator, so that the moments of the self-evaluations of a game
  can be reproduced;
- it records how many tokens it generates and reads at every turn.
For the Qwen3 profile the prompts are identical to the old agent's: see tests/test_legacy_equivalence.py.
"""
import math
import random
import re

from prompts import PROMPTS, SAMPLING

THINK_MAX_TOKENS = 10000 # a self-evaluation can be long
NORMAL_MAX_TOKENS = 100 # a normal turn is only a command
SUBSTITUTE_COMMANDS = ["help", "look"] # played when an answer reaches the token limit
LOG_GAME = "GAME ++++++++++++++++++++++++++++++++++++++++++++++++++\n"
LOG_AGENT = "AGENT -------------------------------------------------\n"
LOG_SELFEVAL = "SELF-EVALUATION: +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-\n"

MODE_PATTERN = re.compile(r"^(?P<kind>e|r|er|lr|l|)(?P<n>\d+)$")
GATE_MODE_PATTERN = re.compile(r"^(?P<memory>l?)g(?P<gate>[a-z]+\d*)$")


def parse_mode(mode):
    """The settings of a mode, i.e. how often the agent self-evaluates and which reasonings stay in its context:
    "0": it never self-evaluates (it never thinks);
    "eN": it self-evaluates every N turns, and its reasonings never stay in the context (ephemeral);
    "N": every N turns, all its reasonings stay in the context;
    "rN": about every N turns (a random interval between N/2 and 3N/2), all its reasonings stay in the context;
    "erN": about every N turns, its reasonings never stay in the context;
    "lrN": about every N turns, only its last reasoning stays in the context, until the next self-evaluation;
    "lN": every N turns, and its last reasoning stays in the context also during the next self-evaluation, which
        replaces it (l1: it thinks every turn like e1, but while thinking it reads what it thought on its previous
        move; with lrN the last reasoning leaves the context before the next self-evaluation, so it is never read
        while thinking).
    "g<gate>": it self-evaluates when its gate says so (gate.py: goracle, gcrit, gself, grand30...), never on a
        clock, and its reasonings never stay in the context; "lg<gate>": the same, keeping the last reasoning as lN.
        The settings have gate=<gate name> (None for the other modes); the agent needs a gate object (agent.gate).
    """
    match = GATE_MODE_PATTERN.match(mode)
    if match is not None:
        from gate import parse_gate
        parse_gate(match["gate"]) # raises if the gate does not exist
        return dict(selfeval_turns=0, random_turns=False, keep="previous" if match["memory"] else "none",
                    gate=match["gate"])
    match = MODE_PATTERN.match(mode)
    if match is None:
        raise ValueError(f"unknown mode {mode!r}")
    kind, n = match["kind"], int(match["n"])
    if n == 0:
        return dict(selfeval_turns=0, random_turns=False, keep="none", gate=None)
    return dict(selfeval_turns=n,
                random_turns=kind in ("r", "er", "lr"),
                keep={"": "all", "r": "all", "e": "none", "er": "none", "lr": "last", "l": "previous"}[kind],
                gate=None)


class SelfEvalAgent:
    def __init__(self, profile, tokenizer, generate, mode, prompt="CoT", rng=None, log=None):
        """profile: a Profile of profiles.py; tokenizer: the model's tokenizer;
        generate(prompt_ids, max_tokens, sampling) -> (output_ids, hit_limit): the model;
        mode: see parse_mode(); prompt: a key of prompts.PROMPTS;
        rng: random.Random used for the intervals between self-evaluations and for the substitute commands;
        log: path of the log of the game (same format of the old agent), or None.
        """
        self.profile = profile
        self.tokenizer = tokenizer
        self.generate = generate
        self.mode = mode
        settings = parse_mode(mode)
        self.mean_turns = settings["selfeval_turns"]
        self.random_turns = settings["random_turns"]
        self.keep = settings["keep"]
        self.gate_name = settings["gate"]
        self.gate = None # a gate.Gate, set by the owner of the agent (duel.DuelAgent) for the gate modes
        self.shadow = False # also answer every thinking turn without thinking first (the fast move), as a measure
        self.last_move_info = None # what happened at the last act(): see act()
        self.prompt = prompt
        self.system_prompt = PROMPTS[prompt]["system"]
        self.selfeval_prompt = PROMPTS[prompt]["selfeval"]
        self.sampling = SAMPLING[prompt]
        self.rng = rng if rng is not None else random.Random()
        self.log = log
        self.close_think_id = tokenizer.convert_tokens_to_ids("</think>")
        if log:
            open(log, "w").close()
        self.turns = [] # statistics of every answer of the model: see _generate()
        self.reset()

    # ---------------------------------------------------------------- state

    def reset(self):
        """Back to the start of a game."""
        self.pieces = [self.profile.system(self.system_prompt)]
        self.selfeval_turns = self.mean_turns if self.mean_turns > 0 else -1 # -1: never
        self.counter = 0 # turns since the last self-evaluation
        if self.random_turns:
            self._draw_selfeval_turns()
        self.after_selfeval = False
        self.last_reasoning = None # index of the piece of the reasoning kept by the "last" and "previous" modes
        self.last_thought = None # text of the last reasoning (read by duel.py to save the reasonings of a game)
        self._write_log("CONTEXT INITIALIZED +-+-+-+-+-+-+-+-+-+-")

    def _draw_selfeval_turns(self):
        mean = self.mean_turns
        self.selfeval_turns = self.rng.randint(max(1, math.floor(mean / 2)), math.ceil(mean + mean / 2))

    @property
    def context(self):
        return "".join(self.pieces)

    def _write_log(self, text):
        if self.log:
            with open(self.log, "a") as f:
                f.write(text + "\n")

    # ---------------------------------------------------------------- turns

    def act(self, obs, done=False):
        """The command to play after the observation obs. done: the game is over (the agent is reset).
        last_move_info records the turn: think (did it think), gate (the gate's decision, see gate.py), fast (the
        answer without thinking when one was generated: by the oracle gate, or by shadow on a thinking turn) and
        command."""
        if done:
            self.reset()
            return ":)"
        info = self.last_move_info = dict(think=False, gate=None, fast=None, command=None)
        fast = None
        if self.gate_name is not None:
            if self.gate is None:
                raise RuntimeError(f"mode {self.mode} needs a gate: only the two-player games have them")
            decision = self.gate.decide(self, obs)
            info["gate"] = {key: value for key, value in decision.items() if key != "fast"}
            think, fast = decision["think"], decision.get("fast")
            if fast is not None and think: # the fast answer is not played: its tokens are the cost of the gate
                self.turns[fast["turn"]]["kind"] = "gate"
        else:
            think = self.counter == self.selfeval_turns
        if think and self.shadow and fast is None:
            fast = self.preview(obs)
            self.turns[fast["turn"]]["kind"] = "shadow"
        if fast is not None:
            info["fast"] = dict(command=fast["command"], generated=fast["generated"], capped=fast["capped"])
        info["think"] = think
        if think:
            self.counter = 0
            command = self._self_evaluation(obs)
        else:
            command = self._normal_turn(obs, fast)
        info["command"] = command
        return command

    def _user_piece(self, obs):
        """The piece of a normal turn before the answer."""
        return self.profile.user(obs, think=False if self.after_selfeval else None) + self.profile.assistant_start

    def preview(self, obs):
        """The answer the agent would give now without thinking, generated without touching the context (the gates and
        the shadow measure use it). The turn is recorded as "preview": _normal_turn() makes it the normal turn if the
        answer is played, act() marks it "gate" or "shadow" if it is not. Returns dict(command, generated, capped,
        turn: index in self.turns, obs)."""
        prompt = self.context + self._user_piece(obs) + self.profile.nothink_prefill
        output_ids, capped = self._generate(think=False, prompt=prompt, kind="preview")
        command = self.rng.choice(SUBSTITUTE_COMMANDS) if capped else \
            self._clean(self._decode(output_ids[self._answer_start(output_ids):]))
        return dict(command=command, generated=len(output_ids), capped=capped, turn=len(self.turns) - 1, obs=obs)

    def _normal_turn(self, obs, fast=None):
        """A turn without thinking: fast is a preview() of this same observation, played instead of generating again."""
        self.pieces.append(self._user_piece(obs))
        self.after_selfeval = False
        if fast is not None and fast["obs"] == obs:
            self.turns[fast["turn"]]["kind"] = "normal"
            command = fast["command"]
        else:
            command = self._normal_answer()
        self.pieces.append(command + self.profile.end_of_turn)
        self._write_log(LOG_GAME + obs + "\n" + LOG_AGENT + command)
        self.counter += 1
        return command

    def _normal_answer(self):
        output_ids, capped = self._generate(think=False)
        if capped:
            return self.rng.choice(SUBSTITUTE_COMMANDS)
        return self._clean(self._decode(output_ids[self._answer_start(output_ids):]))

    def _self_evaluation(self, obs):
        if self.keep == "last" and self.last_reasoning is not None: # the previous reasoning leaves the context
            self.pieces[self.last_reasoning] = ""
            self.last_reasoning = None
        self.pieces.append(self.profile.user(obs + self.selfeval_prompt, think=True) + self.profile.assistant_start)

        output_ids, capped = self._generate(think=True)
        if self.last_reasoning is not None: # "previous" mode: the previous reasoning was read, now it is replaced
            self.pieces[self.last_reasoning] = ""
            self.last_reasoning = None
        if capped:
            reasoning, command = "", self.rng.choice(SUBSTITUTE_COMMANDS)
        else:
            start = self._answer_start(output_ids)
            reasoning = self._decode(output_ids[:start]).replace("/no_think", "").strip("\n")
            command = self._clean(self._decode(output_ids[start:]))
            if self.profile.think_prefill: # the start of the reasoning was in the prompt
                reasoning = self.profile.think_prefill + reasoning
        self._write_log(LOG_GAME + obs + self.selfeval_prompt + "\n" + LOG_SELFEVAL + reasoning + command)
        self.last_thought = reasoning

        if self.keep != "none":
            self.pieces.append(reasoning)
            if self.keep in ("last", "previous"):
                self.last_reasoning = len(self.pieces) - 1
        self.pieces.append(command + self.profile.end_of_turn)

        if self.random_turns:
            self._draw_selfeval_turns()
        self.after_selfeval = True
        self.counter += 1
        return command

    # ---------------------------------------------------------------- generation

    def _generate(self, think, prompt=None, kind=None, max_tokens=None):
        """Output ids of the model and whether the answer is too long (reached ~the limit of tokens).
        prompt: the text to complete (default: the context with the prefill of the kind of turn); kind: what the
        turn is for in the statistics ("think", "normal", "preview", "gate", "shadow"; default from think)."""
        max_tokens = max_tokens or (THINK_MAX_TOKENS if think else NORMAL_MAX_TOKENS)
        if prompt is None:
            prompt = self.context + (self.profile.think_prefill if think else self.profile.nothink_prefill)
        prompt_ids = self.tokenizer.encode(prompt, add_special_tokens=False)
        output_ids, hit_limit = self.generate(prompt_ids, max_tokens, self.sampling["think" if think else "normal"])
        capped = len(output_ids) >= 0.95 * max_tokens or hit_limit
        self.turns.append(dict(think=think, kind=kind or ("think" if think else "normal"),
                               generated=len(output_ids), prompt=len(prompt_ids), capped=capped))
        return output_ids, capped

    def _answer_start(self, output_ids):
        """Index of the first token after the last </think> (0 if there is none)."""
        try:
            return len(output_ids) - output_ids[::-1].index(self.close_think_id)
        except ValueError:
            return 0

    def _decode(self, ids):
        return self.tokenizer.decode(ids, skip_special_tokens=True)

    @staticmethod
    def _clean(text):
        return text.replace("<think>", "").replace("</think>", "").replace("/think", "") \
                   .replace("/no_think", "").strip("\n")

    # ---------------------------------------------------------------- statistics

    def context_tokens(self):
        """Tokens in the context after the last answer (0 before the first one)."""
        return self.turns[-1]["prompt"] + self.turns[-1]["generated"] if self.turns else 0

    def token_stats(self):
        """Generated and read tokens of the turns played so far. generated: what playing cost (the thinking, normal
        and gate turns); the shadow answers (generated_shadow) are a measure, not a cost, and are not in it."""
        by_kind = {kind: [turn for turn in self.turns if turn["kind"] == kind]
                   for kind in ("think", "normal", "gate", "shadow")}
        think, normal = by_kind["think"], by_kind["normal"]
        return dict(generated=sum(turn["generated"] for turn in self.turns if turn["kind"] != "shadow"),
                    generated_thinking=sum(turn["generated"] for turn in think),
                    generated_normal=sum(turn["generated"] for turn in normal),
                    generated_gate=sum(turn["generated"] for turn in by_kind["gate"]),
                    generated_shadow=sum(turn["generated"] for turn in by_kind["shadow"]),
                    think_turns=len(think),
                    normal_turns=len(normal),
                    gate_turns=len(by_kind["gate"]),
                    shadow_turns=len(by_kind["shadow"]),
                    capped_think_turns=sum(turn["capped"] for turn in think),
                    capped_normal_turns=sum(turn["capped"] for turn in normal),
                    max_prompt=max((turn["prompt"] for turn in self.turns), default=0))
