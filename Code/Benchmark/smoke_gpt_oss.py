"""Does openai/gpt-oss-20b run on this GPU with this vLLM, and can its thinking be forced on and off by a prefill?

A check to run once before writing a profile for it (profiles.py); it plays no game and saves nothing.

    python smoke_gpt_oss.py

gpt-oss writes a conversation in the "harmony" format: an answer is one or two messages of the assistant, the
reasoning in the channel "analysis" and the answer in the channel "final":
    <|start|>assistant<|channel|>analysis<|message|>...<|end|><|start|>assistant<|channel|>final<|message|>...<|return|>
The three prompts below end where the benchmark would end them:
    1. thinking forced: the prompt ends with the opening of an analysis message;
    2. thinking forbidden: the prompt ends with the opening of the final message;
    3. as 1, with the reasoning of an earlier turn left in the history (what the modes lr4, l1 and r4 do).
What to read in the output: 1 and 3 must contain a reasoning, then "<|end|><|start|>assistant<|channel|>final
<|message|>" and a command; 2 must be a command alone. Generation stops at <|return|> or <|call|>, never at <|end|>,
which only closes the reasoning.
"""
import sys

MODEL = "openai/gpt-oss-20b"
RETURN, CALL = 200002, 200012 # <|return|>, <|call|>: the two ends of an answer

SYSTEM = ("<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.\n"
          "Knowledge cutoff: 2024-06\nCurrent date: 2026-11-17\n\nReasoning: medium\n\n"
          "# Valid channels: analysis, commentary, final. Channel must be included for every message.<|end|>")
DEVELOPER = ("<|start|>developer<|message|># Instructions\n\nYou are playing a text adventure. Answer with one "
             "command of the game and nothing else.\n\n<|end|>")
FIRST = ("<|start|>user<|message|>You are in a kitchen. There is a closed fridge and a cookbook on the table.<|end|>")
THINK, FINAL = "<|start|>assistant<|channel|>analysis<|message|>", "<|start|>assistant<|channel|>final<|message|>"
EARLIER = (THINK + "The recipe is probably in the cookbook, so I should read it before opening anything.<|end|>"
           + FINAL + "examine cookbook<|end|>"
           + "<|start|>user<|message|>The cookbook says: slice the red onion, fry it, then prepare the meal.<|end|>")

PROMPTS = {
    "1. thinking forced": SYSTEM + DEVELOPER + FIRST + THINK,
    "2. thinking forbidden": SYSTEM + DEVELOPER + FIRST + FINAL,
    "3. thinking forced, an earlier reasoning in the history": SYSTEM + DEVELOPER + FIRST + EARLIER + THINK,
}


def main():
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams
    import vllm
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    llm = LLM(model=MODEL, gpu_memory_utilization=0.9, enable_prefix_caching=True, max_model_len=40960, seed=46)
    cache = llm.llm_engine.vllm_config.cache_config
    print(f"\nvllm {vllm.__version__}: loaded. KV cache: "
          f"{cache.num_gpu_blocks * cache.block_size if cache.num_gpu_blocks else 'unknown'} tokens")
    sampling = SamplingParams(max_tokens=1500, temperature=1.0, top_p=1.0, stop_token_ids=[RETURN, CALL],
                              skip_special_tokens=False)
    names = list(PROMPTS)
    ids = [tokenizer.encode(PROMPTS[name], add_special_tokens=False) for name in names]
    for name, tokens in zip(names, ids): # each special token must be one id, or the prompt is not what it looks like
        assert tokenizer.decode(tokens) == PROMPTS[name], f"{name}: the prompt does not survive the tokenizer"
    outputs = llm.generate([{"prompt_token_ids": tokens} for tokens in ids], sampling)
    for name, output in zip(names, outputs):
        answer = output.outputs[0]
        print(f"\n===== {name} ({len(answer.token_ids)} tokens, finished by: {answer.finish_reason}) =====")
        print(tokenizer.decode(answer.token_ids, skip_special_tokens=False))


if __name__ == "__main__":
    sys.exit(main())
