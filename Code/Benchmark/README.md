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

## Two-player games

The same agent and modes against an opponent, in chess and Connect Four. The levels of a game are its opponents:
`random`, `greedy` (one move ahead), Stockfish `sf<N>` (skill level) or `elo<N>` (chess only), or the model itself `self-<mode>`.
The records go in the same results folders, with the same path (`level<opponent>-seed<S>.json`), and `source: "benchmark-duel"`.

| File | What it does |
|---|---|
| `grids/duel_*.yaml` | `duel_tier1.yaml` (Qwen3 sizes against random/greedy/Stockfish), `duel_selfplay.yaml` (Qwen3-4B against itself, every pair of thinking modes, neutral prompt), `duel_free.yaml` (the same games with the free prompt) |
| `run_duel.py` | the `run.py` of the two-player games (`--dry-run` too) |
| `colab_duel.ipynb` | runs a duel grid on Colab (installs python-chess and Stockfish) |
| `duel.py` | the agent with the prompts of the game, the opponents, the game loop (illegal answers, forfeit, max_steps) |
| `duel_games.py` | the games: what a side sees, its legal moves, how an answer becomes a move |
| `duel_prompts.py` | the prompts of each game, as in `prompts.py` |
| `tests/test_duel.py` | rules and parsing, opponents, the game loop, `run_duel` end to end |

The score is the agent's: win 1, draw 0.5, loss 0 (`max_score` 1). An illegal answer is not played: the agent reads that it is not legal, with the legal moves (not the board again), and answers again;
after `max_illegal` wrong answers in a row a random legal move is played for it (or it loses, with `illegal_policy: forfeit`).
The agent plays first on the odd seeds and second on the even ones (`colors: alternate`).
A chess game stopped at `max_steps` is a draw on the board; with `adjudicate: stockfish` (both duel grids), Stockfish evaluates the final position and the side ahead by `adjudicate_cp` (300 = 3 pawns) wins it: `final_eval`, `adjudicated_result`, `adjudicated_score` (equal to the result on the board for the games that ended by themselves). `adjudicate.py` adds them to records already played, from their final position.
Next to every record: `.transcript.md`, the game move by move with the reasonings of both agents, and `.thoughts.jsonl`, the same reasonings as data.
In self-play they are what the experiment is about: does a model reason, unprompted, about what its opponent is doing, and is it right according to the opponent's own reasoning?
That is why `duel_selfplay.yaml` uses the `neutral` prompt: `CoT` asks about the opponent's threats and replies.
`neutral` still structures the reasoning (evaluate the position, compare candidate moves, be brief) and calls the player "the user"; `free` (`duel_free.yaml`) only says which game is played and asks to think. CoT, neutral and free go from the most to the least steered.
To add a game: a class in `duel_games.py` (see `DuelGame`) and its prompts in `duel_prompts.py`.
