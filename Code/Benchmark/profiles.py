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
import os


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

    # How the agent reads an answer of the model and writes it back in the context. The defaults are the models that
    # write their reasoning between <think> and </think>, in the same message of the answer (agent.py did this itself
    # before gpt-oss was added); GptOss replaces them.
    stop_tokens = None # tokens that end a generation (None: the tokenizer's end-of-sequence token)
    think_bridge = "" # if set, a self-evaluation is two generations: the reasoning, then this text and the answer

    def bind(self, tokenizer):
        """Called once by the agent: the ids of the tokens the methods below look for."""
        self.close_id = tokenizer.convert_tokens_to_ids("</think>")

    def answer_start(self, output_ids):
        """Index of the first token of the answer: after the last </think>, 0 if there is none."""
        try:
            return len(output_ids) - output_ids[::-1].index(self.close_id)
        except ValueError:
            return 0

    def reasoning_end(self, output_ids):
        """Index after the last token of the reasoning (here the reasoning includes its </think>)."""
        return self.answer_start(output_ids)

    def closed_within(self, output_ids, n):
        """Whether the reasoning is closed within the first n tokens (an empty reasoning)."""
        return self.close_id in output_ids[:n]

    def kept(self, reasoning):
        """A reasoning as it stands in the context, between the start of the assistant's turn and answer()."""
        return reasoning

    def readable(self, reasoning):
        """A reasoning as it is written in the logs and in the records: between <think> and </think>."""
        return reasoning

    def wrap_summary(self, summary):
        """The summary of a reasoning as it stands in the context, in the place of the reasoning (the "s" modes)."""
        return "<think>\n" + summary + "\n</think>\n\n"

    def answer(self, command):
        """An answer as it stands in the context, after the start of the assistant's turn."""
        return command + self.end_of_turn

    def tidy(self, command):
        return command


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


class GptOss(Profile):
    """gpt-oss (OpenAI), which writes a conversation in the "harmony" format. An answer is two messages of the
    assistant: the reasoning in the channel "analysis", closed by <|end|>, then the answer in the channel "final",
    closed by <|return|> when it is generated and by <|end|> when it is in the history:
        <|start|>assistant<|channel|>analysis<|message|>...<|end|><|start|>assistant<|channel|>final<|message|>...<|end|>
    The official template has no switch for the thinking (only "Reasoning: low / medium / high" in the system
    message, left at its default, medium): it is forced by a prefill, the opening of the analysis message, and
    forbidden by the opening of the final message. Checked on the GPU with smoke_gpt_oss.py.

    A self-evaluation is two generations (think_bridge): the reasoning, stopped at its <|end|>, then the answer after
    the opening of the final message written here. With one generation the only stop would be <|return|>, and a model
    that closes its answer with <|end|>, as the answers in its history are closed, would go on writing the game's
    turn until the limit of tokens.

    Like Qwen3's, the official template removes the reasonings of the earlier turns from the history (it keeps them
    only between the tool calls of a task with no final answer yet): the modes that keep a reasoning show this
    model too a context it was not trained on.
    The template writes today's date in the system message: here it is a fixed date, as for SmolLM3.
    """
    name = "gpt_oss"
    switch = "prefill"
    end_of_turn = "<|end|>"
    assistant_start = "<|start|>assistant"
    think_prefill = "<|channel|>analysis<|message|>"
    nothink_prefill = "<|channel|>final<|message|>"
    think_bridge = "<|end|><|start|>assistant<|channel|>final<|message|>"
    stop_tokens = ("<|end|>", "<|return|>", "<|call|>")
    today = "2026-10-18"

    def system(self, text):
        # the user's system prompt is the "developer" message of the format; the system message is the template's own
        return ("<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.\n"
                f"Knowledge cutoff: 2024-06\nCurrent date: {self.today}\n\nReasoning: medium\n\n"
                "# Valid channels: analysis, commentary, final. Channel must be included for every message.<|end|>"
                "<|start|>developer<|message|># Instructions\n\n" + text + "\n\n<|end|>")

    def user(self, text, think=None):
        return "<|start|>user<|message|>" + text + self.end_of_turn

    def bind(self, tokenizer):
        self.end_id = tokenizer.convert_tokens_to_ids("<|end|>")
        self.message_id = tokenizer.convert_tokens_to_ids("<|message|>")
        self.stop_ids = set(tokenizer.convert_tokens_to_ids(list(self.stop_tokens)))

    def without_stop(self, output_ids):
        """The output without the stop token it may end with."""
        return output_ids[:-1] if output_ids and output_ids[-1] in self.stop_ids else output_ids

    def answer_start(self, output_ids):
        """After the opening of the last message (a self-evaluation: of the final message written by the agent);
        an output with no message is all answer (a normal turn; the stop token it ends with is not text)."""
        if self.message_id in output_ids:
            return len(output_ids) - output_ids[::-1].index(self.message_id)
        return 0

    def reasoning_end(self, output_ids):
        """The reasoning ends before its <|end|>."""
        return output_ids.index(self.end_id) if self.end_id in output_ids else self.answer_start(output_ids)

    def closed_within(self, output_ids, n):
        return self.end_id in output_ids[:n]

    def kept(self, reasoning):
        return reasoning + self.end_of_turn + self.assistant_start if reasoning else ""

    def readable(self, reasoning):
        return "<think>\n" + reasoning[len(self.think_prefill):] + "\n</think>" if reasoning else ""

    def wrap_summary(self, summary):
        return self.kept(self.think_prefill + summary)

    def answer(self, command):
        return self.nothink_prefill + command + self.end_of_turn

    def tidy(self, command):
        return command.strip()


PROFILES = {profile.name: profile for profile in (Qwen3, SmolLM3, DeepSeekR1, GptOss)}

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
    # ~14 GB of weights (MXFP4 as published); on a 24 GB card a KV cache of ~160k tokens: four 40k games at a time
    "openai/gpt-oss-20b": dict(profile="gpt_oss", max_model_len=40960),
}

# NO_FP8_KV_CACHE=1 in the environment: the KV cache stays in bf16 also for the models above that ask for fp8.
# With an fp8 cache vLLM uses FlashInfer for the attention, which needs kernels compiled for the CUDA of the driver:
# on a machine with an old driver (535, CUDA 12.2) and no nvcc they cannot be built nor loaded. The bf16 cache uses
# vLLM's own kernels. It halves the context that fits (Qwen3-8B-FP8 on 24 GB: enough for Connect Four, tight for
# cooking); the record keeps the settings, so the games played this way can be told apart (no kv_cache_dtype).
NO_FP8_KV_CACHE = "NO_FP8_KV_CACHE"


def model_settings(model):
    """(profile, vLLM settings) of a model. A model that is not in MODELS can be used if it is a Qwen3."""
    settings = dict(MODELS.get(model) or {})
    if not settings:
        if not model.split("/")[-1].startswith("Qwen3-"):
            raise KeyError(f"{model} is not in profiles.MODELS: add it with its profile")
        settings = dict(profile="qwen3", max_model_len=40960)
    profile = PROFILES[settings.pop("profile")]()
    if os.environ.get(NO_FP8_KV_CACHE) == "1": # see NO_FP8_KV_CACHE
        settings.pop("kv_cache_dtype", None)
    if profile.stop_tokens: # read by engine.Engine, not by vLLM
        settings["stop_tokens"] = list(profile.stop_tokens)
    return profile, settings
