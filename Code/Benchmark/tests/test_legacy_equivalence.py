"""The new agent (agent.py with the Qwen3 profile) against the old one (Self-evaluation/self_evaluation.py):
both play the same game with a fake model that gives them the same answers, and at every turn they must send
the model the same prompt (token by token) with the same sampling parameters, and play the same command.
Their contexts and logs at the end must be identical too.

The fake answers cover the special cases: empty and real reasonings, answers without </think>, answers that reach
the token limit, switches written by the model. Only the tokenizer of Qwen3-4B is needed (no torch, no GPU):

    python tests/test_legacy_equivalence.py
"""
import os
import random
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.append(os.path.join(HERE, "..", "..", "Self-evaluation")) # after the benchmark: it has an agent.py too

import transformers

TOKENIZER = transformers.AutoTokenizer.from_pretrained("Qwen/Qwen3-4B")
EOS = TOKENIZER.eos_token_id


# ------------------------------------------------------------------ the old module, without torch and without the model

class _Row:
    def __init__(self, ids):
        self.ids = list(ids)

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, index):
        return _Row(self.ids[index]) if isinstance(index, slice) else self.ids[index]

    def tolist(self):
        return list(self.ids)


class _Tensor:
    def __init__(self, rows):
        self.rows = [_Row(row) for row in rows]

    def __getitem__(self, index):
        return self.rows[index]

    def to(self, device):
        return self


class _OldTokenizer:
    """The real tokenizer, with encode() returning a fake tensor as the old agent expects."""
    eos_token_id = EOS

    def encode(self, text, return_tensors=None):
        return _Tensor([TOKENIZER.encode(text)])


def import_old_module():
    torch = types.ModuleType("torch")
    torch.set_default_device = lambda device: None
    torch.manual_seed = lambda seed: None
    torch.cuda = types.SimpleNamespace(device=lambda name: None, OutOfMemoryError=MemoryError)
    textworld = types.ModuleType("textworld")
    textworld.EnvInfos = lambda **kwargs: types.SimpleNamespace(**kwargs)
    textworld_gym = types.ModuleType("textworld.gym")
    textworld_gym.Agent = object
    textworld.gym = textworld_gym
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoTokenizer = transformers.AutoTokenizer
    fake_transformers.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=lambda *args, **kwargs: None)
    saved = {name: sys.modules.get(name) for name in ("torch", "textworld", "textworld.gym", "transformers")}
    sys.modules.update({"torch": torch, "textworld": textworld, "textworld.gym": textworld_gym,
                        "transformers": fake_transformers})
    try:
        import self_evaluation
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return self_evaluation


# ------------------------------------------------------------------ the fake model

THINK_ANSWERS = [
    "<think>\nThe goal is to cook. I should go north. /no_think\n</think>\n\ngo north",
    None, # reaches the limit
    "take knife", # no reasoning at all
    "<think>\nFirst the pepper, then the oven.\n</think>\n\nput green pepper in oven\nput red pepper in oven\nuse oven",
    "<think>\n\n</think>\n\n/think open fridge",
    "<think>\nhalf a reasoning without the end",
]
NORMAL_ANSWERS = [
    "<think>\n\n</think>\n\ntake key",
    "open door",
    None, # reaches the limit
    "<think>\n\n</think>\n\ngo west\n",
    "<think>\nshort</think>examine table",
]


class FakeModel:
    """Answers in order from the lists above, and records what it is asked."""

    def __init__(self):
        self.calls = 0
        self.requests = [] # (prompt ids, max tokens, sampling)

    def answer(self, prompt_ids, max_tokens, sampling):
        self.requests.append((list(prompt_ids), max_tokens, sampling))
        answers = THINK_ANSWERS if max_tokens == 10000 else NORMAL_ANSWERS
        text = answers[self.calls % len(answers)]
        self.calls += 1
        if text is None:
            return [TOKENIZER.encode("loop", add_special_tokens=False)[0]] * int(max_tokens * 0.96)
        return TOKENIZER.encode(text, add_special_tokens=False) + [EOS]

    # the old agent calls model.generate()
    def generate(self, input_ids, max_new_tokens, eos_token_id, **sampling):
        prompt_ids = input_ids[0].tolist()
        output = self.answer(prompt_ids, max_new_tokens, sampling or None)
        return _Tensor([prompt_ids + output])

    # the new agent calls generate(prompt_ids, max_tokens, sampling)
    def __call__(self, prompt_ids, max_tokens, sampling):
        return self.answer(prompt_ids, max_tokens, sampling), False


def observation(turn):
    return (f"\n-= Kitchen =-\nYou are in a kitchen. A fridge is here (turn {turn}). Exits: north, west.\n\n"
            f">                                -= Kitchen =-{turn // 7}/{turn}")


OLD_ARGUMENTS = { # mode -> (selfeval_turns, reads_own_reasoning, random_selfeval) of the old agent
    "0": (0, False, False),
    "e1": (1, False, False),
    "e3": (3, False, False),
    "1": (1, True, False),
    "r4": (4, True, True),
    "er4": (4, False, True),
    "lr4": (4, "last", True),
    "r10": (10, True, True),
    "lr2": (2, "last", True),
}


def compare(old_module, mode, prompt, seed, turns=60):
    from agent import SelfEvalAgent
    from profiles import Qwen3

    folder = tempfile.mkdtemp()
    old_model, new_model = FakeModel(), FakeModel()
    selfeval_turns, reads, random_turns = OLD_ARGUMENTS[mode]
    old_module.random = random.Random(seed) # the old agent uses the module random: give it the same numbers
    old = old_module.LLMAgentSelfEvaluate(model=old_model, tokenizer=_OldTokenizer(), prompt_version=prompt,
                                          selfeval_turns=selfeval_turns, random_selfeval=random_turns,
                                          reads_own_reasoning=reads, log=os.path.join(folder, "old.log"))
    new = SelfEvalAgent(Qwen3(), TOKENIZER, new_model, mode, prompt, rng=random.Random(seed),
                        log=os.path.join(folder, "new.log"))

    for turn in range(turns):
        obs = observation(turn)
        old_command = old.act(obs, 0, False, {})
        new_command = new.act(obs)
        assert old_command == new_command, (mode, prompt, turn, old_command, new_command)
        assert old_model.requests == new_model.requests, (mode, prompt, turn)
        assert old.context == new.context, (mode, prompt, turn)
    old.act(observation(turns), 0, True, {})
    new.act(observation(turns), done=True)
    with open(os.path.join(folder, "old.log")) as f_old, open(os.path.join(folder, "new.log")) as f_new:
        assert f_old.read() == f_new.read(), (mode, prompt)
    stats = new.token_stats()
    assert stats["think_turns"] + stats["normal_turns"] == len(new_model.requests)
    return stats


def test_same_prompts_as_the_old_agent():
    old_module = import_old_module()
    for prompt in ("default", "CoT"):
        for mode in OLD_ARGUMENTS:
            for seed in (1, 2, 3):
                stats = compare(old_module, mode, prompt, seed)
            print(f"ok {prompt:<8}{mode:<5} last game: {stats['think_turns']} self-evaluations, "
                  f"{stats['capped_think_turns'] + stats['capped_normal_turns']} capped answers")


if __name__ == "__main__":
    test_same_prompts_as_the_old_agent()
    print("the new agent sends the same prompts as the old one")
