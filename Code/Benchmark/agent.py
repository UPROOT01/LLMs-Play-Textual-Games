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

MODE_PATTERN = re.compile(r"^(?P<kind>e|r|er|lr|)(?P<n>\d+)$")


def parse_mode(mode):
    """The settings of a mode, i.e. how often the agent self-evaluates and which reasonings stay in its context:
    "0": it never self-evaluates (it never thinks);
    "eN": it self-evaluates every N turns, and its reasonings never stay in the context (ephemeral);
    "N": every N turns, all its reasonings stay in the context;
    "rN": about every N turns (a random interval between N/2 and 3N/2), all its reasonings stay in the context;
    "erN": about every N turns, its reasonings never stay in the context;
    "lrN": about every N turns, only its last reasoning stays in the context, until the next self-evaluation.
    """
    match = MODE_PATTERN.match(mode)
    if match is None:
        raise ValueError(f"unknown mode {mode!r}")
    kind, n = match["kind"], int(match["n"])
    if n == 0:
        return dict(selfeval_turns=0, random_turns=False, keep="none")
    return dict(selfeval_turns=n,
                random_turns=kind in ("r", "er", "lr"),
                keep={"": "all", "r": "all", "e": "none", "er": "none", "lr": "last"}[kind])


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
        self.last_reasoning = None # index of the piece of the reasoning kept by the "last" mode
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
        """The command to play after the observation obs. done: the game is over (the agent is reset)."""
        if done:
            self.reset()
            return ":)"
        if self.counter == self.selfeval_turns:
            self.counter = 0
            return self._self_evaluation(obs)

        self.pieces.append(self.profile.user(obs, think=False if self.after_selfeval else None)
                           + self.profile.assistant_start)
        self.after_selfeval = False
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
        if self.last_reasoning is not None: # "last" mode: the previous reasoning leaves the context
            self.pieces[self.last_reasoning] = ""
            self.last_reasoning = None
        self.pieces.append(self.profile.user(obs + self.selfeval_prompt, think=True) + self.profile.assistant_start)

        output_ids, capped = self._generate(think=True)
        if capped:
            reasoning, command = "", self.rng.choice(SUBSTITUTE_COMMANDS)
        else:
            start = self._answer_start(output_ids)
            reasoning = self._decode(output_ids[:start]).replace("/no_think", "").strip("\n")
            command = self._clean(self._decode(output_ids[start:]))
            if self.profile.think_prefill: # the start of the reasoning was in the prompt
                reasoning = self.profile.think_prefill + reasoning
        self._write_log(LOG_GAME + obs + self.selfeval_prompt + "\n" + LOG_SELFEVAL + reasoning + command)

        if self.keep != "none":
            self.pieces.append(reasoning)
            if self.keep == "last":
                self.last_reasoning = len(self.pieces) - 1
        self.pieces.append(command + self.profile.end_of_turn)

        if self.random_turns:
            self._draw_selfeval_turns()
        self.after_selfeval = True
        self.counter += 1
        return command

    # ---------------------------------------------------------------- generation

    def _generate(self, think):
        """Output ids of the model and whether the answer is too long (reached ~the limit of tokens)."""
        max_tokens = THINK_MAX_TOKENS if think else NORMAL_MAX_TOKENS
        prefill = self.profile.think_prefill if think else self.profile.nothink_prefill
        prompt_ids = self.tokenizer.encode(self.context + prefill, add_special_tokens=False)
        output_ids, hit_limit = self.generate(prompt_ids, max_tokens, self.sampling["think" if think else "normal"])
        capped = len(output_ids) >= 0.95 * max_tokens or hit_limit
        self.turns.append(dict(think=think, generated=len(output_ids), prompt=len(prompt_ids), capped=capped))
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

    def token_stats(self):
        """Generated and read tokens of the turns played so far."""
        think = [turn for turn in self.turns if turn["think"]]
        normal = [turn for turn in self.turns if not turn["think"]]
        return dict(generated=sum(turn["generated"] for turn in self.turns),
                    generated_thinking=sum(turn["generated"] for turn in think),
                    generated_normal=sum(turn["generated"] for turn in normal),
                    think_turns=len(think),
                    normal_turns=len(normal),
                    capped_think_turns=sum(turn["capped"] for turn in think),
                    capped_normal_turns=sum(turn["capped"] for turn in normal),
                    max_prompt=max((turn["prompt"] for turn in self.turns), default=0))
