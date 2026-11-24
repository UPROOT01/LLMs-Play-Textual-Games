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
GPT_OSS = "openai/gpt-oss-20b" # tested apart: its reasoning is a message of its own, not a <think> block


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
            + profile.answer("go north") + profile.user("You are in a garden.") + profile.assistant_start)
    return text + (profile.think_prefill if think_turn else profile.nothink_prefill)


def test_profiles_write_the_official_templates():
    for name, model in EXAMPLE_MODELS.items():
        profile = PROFILES[name]()
        tokenizer = AutoTokenizer.from_pretrained(model)
        if name == "qwen3": # soft switch: the same prompt for both turns, the model decides
            assert ours(profile, True) == official(tokenizer, profile)
        elif name == "smollm3": # /think mode of the template + <think>\n prefill, and its /no_think start of the answer
            assert ours(profile, True) == official(tokenizer, profile) + profile.think_prefill
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
        self.think_prompts = [] # the prompt of every self-evaluation

    def __call__(self, prompt_ids, max_tokens, sampling):
        prompt = self.tokenizer.decode(prompt_ids)
        if max_tokens == 10000:
            self.n += 1
            self.think_prompts.append(prompt)
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
            # what the agent reads while it thinks: l1 its previous reasoning only, lr4 and the others none of them
            # (lr4 drops it before the next self-evaluation), r4 all of them
            for n, prompt in enumerate(generate.think_prompts, start=1):
                read = [k for k in range(1, n) if f"reasoning {k}\n</think>" in prompt]
                expected = {"l1": [n - 1] if n > 1 else [], "r4": list(range(1, n))}.get(mode, [])
                assert read == expected, (mode, n, read)
            stats = agent.token_stats()
            assert stats["think_turns"] == generate.n and stats["think_turns"] + stats["normal_turns"] == 40
        print(f"ok {name}: reasonings kept by r4 (all), lr4 and l1 (last), e1/er4/0 (none)")


class ScriptedGptOss:
    """gpt-oss as the agent calls it: a self-evaluation is two generations, the reasoning (stopped at <|end|>) and
    then the answer after the opening of the final message; a normal turn is the answer alone."""

    def __init__(self, tokenizer, profile, empty=()):
        self.tokenizer, self.profile, self.n = tokenizer, profile, 0
        self.empty = empty # the self-evaluations (1, 2, ...) whose first reasoning is empty
        self.think_prompts, self.answer_prompts, self.retries = [], [], 0

    def __call__(self, prompt_ids, max_tokens, sampling):
        prompt = self.tokenizer.decode(prompt_ids)
        ids = lambda text: self.tokenizer.encode(text, add_special_tokens=False)
        if max_tokens == 10000: # the reasoning
            if prompt.endswith(self.profile.think_prefill + "Okay, let's see."): # the retry of an empty reasoning
                self.retries += 1
                return ids(f" This is the reasoning number {self.n}<|end|>"), False # longer than an empty one
            assert prompt.endswith(self.profile.assistant_start + self.profile.think_prefill)
            self.n += 1
            self.think_prompts.append(prompt)
            return ids("<|end|>" if self.n in self.empty else f"reasoning {self.n}<|end|>"), False
        # the answer of a self-evaluation: the prompt ends as a normal turn's, after this turn's reasoning
        if "analysis" in prompt.rsplit("<|start|>user", 1)[-1]:
            assert prompt.endswith(self.profile.think_bridge)
            self.answer_prompts.append(prompt)
            return ids("go north  <|end|>"), False # closed as the answers of the history are, with spaces to tidy
        assert prompt.endswith(self.profile.assistant_start + self.profile.nothink_prefill)
        return ids("take key<|return|>"), False


def test_gpt_oss():
    profile = PROFILES["gpt_oss"]()
    tokenizer = AutoTokenizer.from_pretrained(GPT_OSS)
    # the same text of the official template, which opens no channel: the prefill opens the analysis or the final one
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "You are in a kitchen."},
                {"role": "assistant", "content": "go north"}, {"role": "user", "content": "You are in a garden."}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    text = text.replace(text.split("Current date: ")[1].split("\n")[0], profile.today)
    assert ours(profile, True) == text + profile.think_prefill
    assert ours(profile, False) == text + profile.nothink_prefill
    # a reasoning kept in the context is written as the template writes the one it keeps (before a tool call)
    kept = [{"role": "user", "content": "x"}, {"role": "assistant", "thinking": "reasoning 1", "content": "",
            "tool_calls": [{"function": {"name": "play", "arguments": {}}}]}]
    official = tokenizer.apply_chat_template(kept, tokenize=False)
    assert profile.assistant_start + profile.kept(profile.think_prefill + "reasoning 1") in official + "<|start|>assistant"
    # every special string is made of special tokens only, one id each
    for special in ("<|start|>", "<|end|>", "<|message|>", "<|channel|>", "<|return|>", "<|call|>"):
        assert len(tokenizer.encode(special, add_special_tokens=False)) == 1, special
    assert MODELS[GPT_OSS]["profile"] == "gpt_oss"
    from profiles import model_settings
    assert model_settings(GPT_OSS)[1]["stop_tokens"] == ["<|end|>", "<|return|>", "<|call|>"]

    block = lambda n: f"<|start|>assistant<|channel|>analysis<|message|>reasoning {n}<|end|>"
    for mode in ("0", "e1", "r4", "er4", "lr4", "l1"):
        generate = ScriptedGptOss(tokenizer, profile)
        agent = SelfEvalAgent(profile, tokenizer, generate, mode, "CoT", rng=random.Random(0))
        for turn in range(40):
            command = agent.act(f"Room {turn}.")
            assert command in ("go north", "take key"), repr(command) # tidied: no spaces around
            context = agent.context
            kept = [n for n in range(1, generate.n + 1) if block(n) in context]
            expected = {"r4": list(range(1, generate.n + 1)), "lr4": [generate.n] if generate.n else [],
                        "l1": [generate.n] if generate.n else []}.get(mode, [])
            assert kept == expected, (mode, turn, kept)
            assert context.count("<|channel|>analysis") == len(kept)
            # every turn of the assistant in the context is [reasoning] + a closed final message, and nothing else
            turns = context.split("<|start|>user<|message|>")[1:]
            for piece in turns:
                answer = piece.split("<|end|>", 1)[1]
                if "analysis" in answer:
                    assert answer.startswith(block(kept[0] if mode != "r4" else int(answer.split("reasoning ")[1].split("<")[0])))
                    answer = answer.split("<|end|>", 1)[1]
                assert answer in ("<|start|>assistant<|channel|>final<|message|>go north<|end|>",
                                  "<|start|>assistant<|channel|>final<|message|>take key<|end|>"), (mode, answer)
            assert "<think>" not in context and "<|return|>" not in context
        assert (generate.n > 0) == (mode != "0")
        if mode in ("e1", "l1"):
            assert generate.n == 39
        for n, prompt in enumerate(generate.think_prompts, start=1): # what the agent reads while it thinks
            read = [k for k in range(1, n) if block(k) in prompt]
            assert read == {"l1": [n - 1] if n > 1 else [], "r4": list(range(1, n))}.get(mode, []), (mode, n, read)
        for n, prompt in enumerate(generate.answer_prompts, start=1): # the answer is generated after its own reasoning
            assert prompt.endswith(block(n) + "<|start|>assistant<|channel|>final<|message|>"), (mode, n, prompt[-160:])
        stats = agent.token_stats()
        assert stats["think_turns"] == generate.n == len(generate.answer_prompts)
        assert stats["think_turns"] + stats["normal_turns"] == 40 and stats["empty_reasonings"] == 0
        if mode != "0": # logged and saved as the other models' reasonings
            assert agent.last_thought == f"<think>\nreasoning {generate.n}\n</think>"
    # an empty reasoning is counted, and with +nonempty asked again with its first words written
    for prompt, retries in (("CoT", 0), ("CoT+nonempty", 1)):
        generate = ScriptedGptOss(tokenizer, profile, empty=(1,))
        agent = SelfEvalAgent(profile, tokenizer, generate, "l1", prompt, rng=random.Random(0))
        agent.act("Room 0.")
        assert agent.act("Room 1.") == "go north"
        assert agent.empty_reasonings == 1 and agent.nonempty_retries == generate.retries == retries
        assert agent.last_thought == ("<think>\nOkay, let's see. This is the reasoning number 1\n</think>" if retries else "<think>\n\n</think>")
    print("ok gpt_oss: the official template, reasoning and answer as two messages, kept by r4 / lr4 / l1 only")


def test_no_fp8_kv_cache():
    """NO_FP8_KV_CACHE=1 drops kv_cache_dtype from the settings, and only that."""
    import os
    from profiles import NO_FP8_KV_CACHE, model_settings
    before = os.environ.pop(NO_FP8_KV_CACHE, None)
    try:
        _, fp8 = model_settings("Qwen/Qwen3-8B-FP8")
        assert fp8["kv_cache_dtype"] == "fp8"
        os.environ[NO_FP8_KV_CACHE] = "1"
        _, bf16 = model_settings("Qwen/Qwen3-8B-FP8")
        assert "kv_cache_dtype" not in bf16 and bf16 == {k: v for k, v in fp8.items() if k != "kv_cache_dtype"}
        assert model_settings("Qwen/Qwen3-4B")[1] == {"max_model_len": 40960}
    finally:
        os.environ.pop(NO_FP8_KV_CACHE, None)
        if before is not None:
            os.environ[NO_FP8_KV_CACHE] = before
    print("ok NO_FP8_KV_CACHE: kv_cache_dtype dropped, the rest unchanged")


def test_one_line():
    """The prompt option +oneline: one line of an answer is the command, played and kept in the context."""
    from agent import one_line
    assert one_line("take pepper") == "take pepper"
    assert one_line("take pepper\n\ntake knife\n\nslice pepper") == "take pepper"
    assert one_line("take red onion\n\n(Note: the user did not say which.)") == "take red onion"
    assert one_line("Goal: cook the meal.  \nPast actions: none.  \nNo loop detected.  \nread cookbook") == "read cookbook"
    assert one_line("FIRST STEP: the goal is to cook.\nTHIRD STEP: no loop.\ntake carrot") == "take carrot"
    assert one_line("dice green apple, slice orange bell pepper, roast green apple") == "dice green apple"
    assert one_line("take green hot pepper; take red onion; dice green hot pepper") == "take green hot pepper"
    assert one_line("take knife)") == "take knife" and one_line("go west.") == "go west"
    assert one_line("take raw pork chop from fridge") == "take raw pork chop from fridge"
    assert one_line("We are stuck.") == "We are stuck" # a sentence is not repaired
    assert one_line("") == "" and one_line("...") == "..."
    tokenizer = AutoTokenizer.from_pretrained(GPT_OSS)
    for prompt, command in (("CoT+nonempty", "take pepper\n\ntake knife"), ("CoT+nonempty+oneline", "take pepper"),
                            ("CoT+oneline", "take pepper")):
        def generate(prompt_ids, max_tokens, sampling):
            return tokenizer.encode("take pepper\n\ntake knife<|end|>", add_special_tokens=False), False
        agent = SelfEvalAgent(PROFILES["gpt_oss"](), tokenizer, generate, "0", prompt=prompt, rng=random.Random(0))
        assert agent.act("You are in a kitchen.") == command
        assert agent.context.endswith("<|channel|>final<|message|>" + command + "<|end|>"), agent.context[-80:]
    try:
        SelfEvalAgent(PROFILES["gpt_oss"](), tokenizer, None, "0", prompt="CoT+online")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown prompt option was accepted")


def test_cot_grounded_and_lsr4():
    """CoT_grounded is game-agnostic and stripped by memory_analysis; lsr4 keeps only the last summary across profiles."""
    import re
    import memory_analysis as ma
    from prompts import PROMPTS, SAMPLING
    text = (PROMPTS["CoT_grounded"]["system"] + PROMPTS["CoT_grounded"]["selfeval"]).lower()
    for banned in ("cook", "recipe", "ingredient", "meal", "eat", "kitchen", "stove", "oven", "knife"):
        assert not re.search(rf"\b{banned}\b", text), banned
    assert SAMPLING["CoT_grounded"] == SAMPLING["CoT"]
    assert ma.THINK_PROMPT.split("Room.\n" + PROMPTS["CoT_grounded"]["selfeval"])[0] == "Room.\n"

    for name, model in list(EXAMPLE_MODELS.items()) + [("gpt_oss", GPT_OSS)]:
        profile = PROFILES[name]()
        tokenizer = AutoTokenizer.from_pretrained(model)
        calls = {"summary": 0}

        def generate(prompt_ids, max_tokens, sampling, _tok=tokenizer, _prof=profile, _calls=calls):
            ids = lambda s: _tok.encode(s, add_special_tokens=False)
            if max_tokens == 10000:
                if _prof.name == "gpt_oss":
                    return ids("long thought<|end|>"), False
                start = "" if _prof.think_prefill else "<think>\n"
                return ids(f"{start}long thought\n</think>\n\ngo north") + [_tok.eos_token_id], False
            if max_tokens == 160:
                _calls["summary"] += 1
                prefix = "<think>\n\n</think>\n\n" if _prof.switch == "soft" else ""
                suffix = "<|return|>" if _prof.name == "gpt_oss" else ""
                end = [] if _prof.name == "gpt_oss" else [_tok.eos_token_id]
                return ids(f"{prefix}note {_calls['summary']}{suffix}") + end, False
            if _prof.name == "gpt_oss":
                return ids("take key<|return|>"), False
            prefix = "<think>\n\n</think>\n\n" if _prof.switch == "soft" else ""
            return ids(f"{prefix}take key") + [_tok.eos_token_id], False

        prompt = "CoT_grounded+nonempty+oneline" if name == "gpt_oss" else "CoT_grounded+nonempty"
        agent = SelfEvalAgent(profile, tokenizer, generate, "lsr4", prompt=prompt, rng=random.Random(0))
        for turn in range(25):
            agent.act(f"Room {turn}.")
        assert calls["summary"] >= 3
        assert f"note {calls['summary']}" in agent.context
        assert "note 1" not in agent.context and "long thought" not in agent.context
        assert agent.token_stats()["summary_turns"] == calls["summary"]
    print("ok CoT_grounded + lsr4 across qwen3, smollm3, deepseek_r1, gpt_oss")


if __name__ == "__main__":
    test_one_line()
    test_profiles_write_the_official_templates()
    test_reasonings_in_the_context()
    test_gpt_oss()
    test_no_fp8_kv_cache()
    test_cot_grounded_and_lsr4()
