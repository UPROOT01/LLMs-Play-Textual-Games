# Benchmark

The experiments as a grid: models × modes × prompts × games/levels × seeds, one JSON record per game.
Nothing in `Code/Experiments` or `Code/Self-evaluation` is changed: the old games are imported here.

| File | What it does |
|---|---|
| `grids/*.yaml` | what to play: `tier1.yaml` (Qwen3 sizes), `tier1_families.yaml` (other families), `tier2.yaml` (lr4 + Coin Collector) |
| `run.py` | plays the games of a grid not yet played (`--dry-run` shows what is done and what is left) |
| `colab.ipynb` | runs a grid on Colab, saving on Drive |
| `analysis.ipynb` | all the games in one table, graphs by model and mode |
| `import_legacy.py` | converts the old experiments (6, 7, 7e, 8) into records in `results/` (already done) |
| `agent.py` | the self-evaluating agent of `Self-evaluation/self_evaluation.py`, for any model family |
| `profiles.py` | how each family writes the conversation and turns thinking on/off; the models and their vLLM settings |
| `engine.py` | many games at the same time with vLLM |
| `records.py` | where the records are and what they contain |
| `tests/` | the new agent sends the same prompts as the old one (Qwen3); the profiles match the official templates; the runner end to end |

Modes: `0` never thinks; `e1` thinks every turn; `r4` / `er4` / `lr4` think about every 4 turns and keep all / none / the last of their reasonings in the context.

Workflow: `colab.ipynb` (pilot with `limit = 8`, then the whole grid) → copy `MyDrive/LLMs-Play-Textual-Games/Benchmark/results` into `results_new/` → `analysis.ipynb`.
To add a model: an entry in `profiles.MODELS` (a new family also needs a profile and a check in `tests/test_profiles.py`), then add it to a grid.

Tests (only the tokenizers are downloaded, no GPU): `python tests/test_legacy_equivalence.py`, `python tests/test_profiles.py`, `python tests/test_pipeline.py`.
