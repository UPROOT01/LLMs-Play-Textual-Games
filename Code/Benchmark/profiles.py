"""How each model family writes a conversation and how its thinking is turned on and off.

The agent builds the context by hand, with these strings, instead of using tokenizer.apply_chat_template():
the official templates decide by themselves which reasonings stay in the history (e.g. Qwen3's template removes them),
while here this is exactly what the experiments vary (see the modes in agent.py).
tests/test_profiles.py checks that, for a simple conversation, each profile writes the same text of the official template.

Two ways of turning thinking on and off:
- "soft": the model reads a switch in the prompt (Qwen3: "/think" and "/no_think") and decides by itself.
  This is how all the experiments before the benchmark were run.
- "prefill": the start of the answer is written in the prompt: an empty "<think>\n\n</think>\n\n" for a normal turn,
  so the model cannot think, and "<think>\n" for a self-evaluation, so it has to.
"""


class Profile:
    name = None
    switch = None # "soft" or "prefill"
    end_of_turn = None
    assistant_start = None
    think_prefill = "" # written at the start of a self-evaluation's answer: it is part of the reasoning
    nothink_prefill = "" # written at the start of a normal turn's answer: it does not stay in the context

    def system(self, text):
        raise NotImplementedError

    def user(self, text, think=None):
        """think: True for a self-evaluation, False for the first normal turn after a self-evaluation, None otherwise."""
        raise NotImplementedError


class Qwen3(Profile):
    """Qwen3 with the soft switches, written exactly as in Self-evaluation/self_evaluation.py."""
    name = "qwen3"
    switch = "soft"
    end_of_turn = "<|im_end|>\n"
    assistant_start = "<|im_start|>assistant\n"

    def system(self, text):
        return "<|im_start|>system\n" + text + "/no_think\n" + self.end_of_turn

    def user(self, text, think=None):
        switch = {True: "/think", False: "/no_think", None: ""}[think]
        return "<|im_start|>user\n" + text + switch + self.end_of_turn


class SmolLM3(Profile):
    """SmolLM3 (Hugging Face): the official template only has a global reasoning mode, in the system prompt.
    It is set to /think, and the normal turns are prefilled with an empty reasoning, as the template does in /no_think mode.
    The template writes today's date in the system prompt: here it is a fixed date, so that all the runs see the same prompt.
    """
    name = "smollm3"
    switch = "prefill"
    end_of_turn = "<|im_end|>\n"
    assistant_start = "<|im_start|>assistant\n"
    nothink_prefill = "<think>\n\n</think>\n"
    today = "18 October 2026"

    def system(self, text):
        # no <|im_end|> after the system prompt: the official template only writes it when there are tools
        return ("<|im_start|>system\n## Metadata\n\nKnowledge Cutoff Date: June 2025\n"
                f"Today Date: {self.today}\nReasoning Mode: /think\n\n## Custom Instructions\n\n"
                + text.rstrip() + "\n\n")

    def user(self, text, think=None):
        return "<|im_start|>user\n" + text + self.end_of_turn


class DeepSeekR1(Profile):
    """DeepSeek-R1 distillations (e.g. DeepSeek-R1-Distill-Llama-8B): they always think, the official template
    prefills "<think>\n". The normal turns are prefilled with an empty reasoning."""
    name = "deepseek_r1"
    switch = "prefill"
    end_of_turn = "<｜end▁of▁sentence｜>"
    assistant_start = "<｜Assistant｜>"
    think_prefill = "<think>\n"
    nothink_prefill = "<think>\n\n</think>\n\n"

    def system(self, text):
        return "<｜begin▁of▁sentence｜>" + text

    def user(self, text, think=None):
        return "<｜User｜>" + text


PROFILES = {profile.name: profile for profile in (Qwen3, SmolLM3, DeepSeekR1)}

# models that can be used in a grid, with their profile and the vLLM settings that fit them on an L4 (24 GB).
# max_model_len: the e1 contexts reach ~30k tokens before the 10k of a self-evaluation, so ~40k are needed.
# kv_cache_dtype="fp8" halves the memory of the context, for the models whose weights leave little room on the L4
# The 8B models in bf16 (~16 GB) leave only ~2 GB for the context, less than one 40k game: their weights are in fp8
# (~8.5 GB), which leaves ~10 GB for the context. Qwen3-8B-FP8 is Qwen's own fp8 checkpoint; DeepSeek has none,
# so quantization="fp8" makes vLLM quantize its weights while loading them
MODELS = {
    "Qwen/Qwen3-0.6B": dict(profile="qwen3", max_model_len=40960),
    "Qwen/Qwen3-1.7B": dict(profile="qwen3", max_model_len=40960),
    "Qwen/Qwen3-4B": dict(profile="qwen3", max_model_len=40960),
    "Qwen/Qwen3-8B-FP8": dict(profile="qwen3", max_model_len=40960, kv_cache_dtype="fp8"),
    "Qwen/Qwen3-14B-AWQ": dict(profile="qwen3", max_model_len=40960, kv_cache_dtype="fp8"),
    "HuggingFaceTB/SmolLM3-3B": dict(profile="smollm3", max_model_len=40960),
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B": dict(profile="deepseek_r1", max_model_len=40960,
                                                     quantization="fp8", kv_cache_dtype="fp8"),
}


def model_settings(model):
    """(profile, vLLM settings) of a model. A model that is not in MODELS can be used if it is a Qwen3."""
    settings = dict(MODELS.get(model) or {})
    if not settings:
        if not model.split("/")[-1].startswith("Qwen3-"):
            raise KeyError(f"{model} is not in profiles.MODELS: add it with its profile")
        settings = dict(profile="qwen3", max_model_len=40960)
    profile = PROFILES[settings.pop("profile")]()
    return profile, settings
