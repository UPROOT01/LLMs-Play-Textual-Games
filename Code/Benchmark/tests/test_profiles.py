"""Each profile against the official chat template of its model, on a simple conversation (only the tokenizers
are downloaded), and the agent with each profile: which reasonings stay in the context in every mode.

    python tests/test_profiles.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from transformers import AutoTokenizer

from agent import SelfEvalAgent
from profiles import MODELS, PROFILES

SYSTEM = "\nYou are an assistant playing a textual game.\nReply with a short command.\n"
EXAMPLE_MODELS = {"qwen3": "Qwen/Qwen3-4B", "smollm3": "HuggingFaceTB/SmolLM3-3B",
                  "deepseek_r1": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B"}


def official(tokenizer, profile, **kwargs):
    system = SYSTEM + ("/no_think\n" if profile.name == "qwen3" else "")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "You are in a kitchen."},
                {"role": "assistant", "content": "go north"}, {"role": "user", "content": "You are in a garden."}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, **kwargs)
    if profile.name == "smollm3": # the template writes today's date
        text = text.replace(text.split("Today Date: ")[1].split("\n")[0], profile.today)
    return text


def ours(profile, think_turn):
    text = (profile.system(SYSTEM) + profile.user("You are in a kitchen.") + profile.assistant_start
            + "go north" + profile.end_of_turn + profile.user("You are in a garden.") + profile.assistant_start)
    return text + (profile.think_prefill if think_turn else profile.nothink_prefill)


def test_profiles_write_the_official_templates():
    for name, model in EXAMPLE_MODELS.items():
        profile = PROFILES[name]()
        tokenizer = AutoTokenizer.from_pretrained(model)
        if name == "qwen3": # soft switch: the same prompt for both turns, the model decides
            assert ours(profile, True) == official(tokenizer, profile)
        elif name == "smollm3": # /think mode of the template, and its /no_think start of the answer
            assert ours(profile, True) == official(tokenizer, profile)
            no_think = official(tokenizer, profile, enable_thinking=False)
            assert no_think.endswith("<|im_start|>assistant\n" + profile.nothink_prefill)
        elif name == "deepseek_r1": # the template always prefills "<think>\n"
            assert ours(profile, True) == official(tokenizer, profile)
        # the special strings of the profile are read as single special tokens
        for text in (profile.assistant_start, profile.end_of_turn.strip("\n")):
            ids = tokenizer.encode(text, add_special_tokens=False)
            assert any(i in tokenizer.all_special_ids or i in tokenizer.added_tokens_decoder for i in ids), (name, text)
        assert len(tokenizer.encode("</think>", add_special_tokens=False)) == 1, name
        print(f"ok {name}: same text of the official template of {model}")
    for model, settings in MODELS.items():
        assert settings["profile"] in PROFILES, model


class ScriptedModel:
    """Every self-evaluation reasons "reasoning <n>", every normal turn answers a command."""

    def __init__(self, tokenizer, profile):
        self.tokenizer, self.profile, self.n = tokenizer, profile, 0

    def __call__(self, prompt_ids, max_tokens, sampling):
        prompt = self.tokenizer.decode(prompt_ids)
        if max_tokens == 10000:
            self.n += 1
            start = "" if self.profile.think_prefill else "<think>\n"
            text = f"{start}reasoning {self.n}\n</think>\n\ngo north"
            assert prompt.endswith(self.profile.assistant_start + self.profile.think_prefill)
        else:
            text = ("<think>\n\n</think>\n\n" if self.profile.switch == "soft" else "") + "take key"
            assert prompt.endswith(self.profile.assistant_start + self.profile.nothink_prefill)
        return self.tokenizer.encode(text, add_special_tokens=False) + [self.tokenizer.eos_token_id], False


def test_reasonings_in_the_context():
    for name, model in EXAMPLE_MODELS.items():
        profile = PROFILES[name]()
        tokenizer = AutoTokenizer.from_pretrained(model)
        for mode in ("0", "e1", "r4", "er4", "lr4", "l1"):
            generate = ScriptedModel(tokenizer, profile)
            agent = SelfEvalAgent(profile, tokenizer, generate, mode, "CoT", rng=random.Random(0))
            kept_counts = []
            for turn in range(40):
                agent.act(f"Room {turn}.")
                kept = [n for n in range(1, generate.n + 1) if f"reasoning {n}\n</think>" in agent.context]
                kept_counts.append(len(kept))
                if mode in ("r4",):
                    assert kept == list(range(1, generate.n + 1))
                elif mode in ("lr4", "l1"):
                    assert kept == ([generate.n] if generate.n else [])
                else:
                    assert kept == []
                # a kept reasoning is complete, with its opening tag
                assert agent.context.count("<think>\nreasoning") == len(kept)
                # the normal turns' empty reasonings never stay in the context
                assert "<think>\n\n</think>" not in agent.context
            assert (generate.n > 0) == (mode != "0")
            if mode in ("e1", "l1"): # every turn after the first
                assert generate.n == 39
            stats = agent.token_stats()
            assert stats["think_turns"] == generate.n and stats["think_turns"] + stats["normal_turns"] == 40
        print(f"ok {name}: reasonings kept by r4 (all), lr4 and l1 (last), e1/er4/0 (none)")


if __name__ == "__main__":
    test_profiles_write_the_official_templates()
    test_reasonings_in_the_context()
