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

Modes: `0` never thinks; `e1` thinks every turn; `r4` / `er4` / `lr4` think about every 4 turns and keep all / none / the last of their reasonings in the context; `l1` thinks every turn like `e1` but keeps its last reasoning; `s1` / `sr4` keep a short summary of each reasoning instead of the reasoning (`ls1` / `lsr4`: only the last summary), written by the model without thinking in a context of its own.

Workflow: `colab.ipynb` (pilot with `limit = 8`, then the whole grid) → copy `MyDrive/LLMs-Play-Textual-Games/Benchmark/results` into `results_new/` → `analysis.ipynb`.
To add a model: an entry in `profiles.MODELS` (a new family also needs a profile and a check in `tests/test_profiles.py`), then add it to a grid.

Tests (only the tokenizers are downloaded, no GPU): `python tests/test_legacy_equivalence.py`, `python tests/test_profiles.py`, `python tests/test_pipeline.py`.

## Two-player games

The same agent and modes against an opponent, in chess and Connect Four. The levels of a game are its opponents:
`random`, `greedy` (one move ahead), Stockfish `sf<N>` (skill level) or `elo<N>` (chess only), or the model itself `self-<mode>`.
The records go in the same results folders, with the same path (`level<opponent>-seed<S>.json`), and `source: "benchmark-duel"`.

| File | What it does |
|---|---|
| `grids/duel_*.yaml` | `duel_tier1.yaml` (Qwen3 sizes against random/greedy/Stockfish), `duel_selfplay.yaml` (Qwen3-4B against itself, every pair of thinking modes, neutral prompt), `duel_free.yaml` and `duel_plain.yaml` (the same games with the free and plain prompts), `duel_c4_prompts.yaml` (Connect Four self-play, 8 games for each of CoT, neutral, free, plain), `duel_c4_memory.yaml` (Connect Four, every pair of e1, l1, r4, er4, lr4: does remembering its reasonings change the opponent reasoning?), `duel_c4_l1.yaml` (the corrected l1 against every mode, 40 games), `duel_c4_gate.yaml` (when to think: the gate modes against e1 and r4, with the shadow fast moves and the solver), `duel_c4_summary.yaml` (keep, drop or summarize the reasonings: s1, ls1, sr4, lsr4 against l1 and e1), `duel_c4_surprise.yaml` (think when surprised: gsurp with the predict prompt, against e1, r4, gcrit and a random gate), `duel_c4_board_columns.yaml` and `duel_c4_board_both.yaml` (the gate grid's goracle, e1 and r4 with the board written column by column, or grid and columns: does the board text limit what thinking repairs? They need a results folder of their own, the record path has no game options) |
| `run_duel.py` | the `run.py` of the two-player games (`--dry-run` too) |
| `colab_duel.ipynb` | runs a duel grid on Colab (installs python-chess and Stockfish) |
| `duel.py` | the agent with the prompts of the game, the opponents, the game loop (illegal answers, forfeit, max_steps) |
| `duel_games.py` | the games: what a side sees, its legal moves, how an answer becomes a move |
| `duel_prompts.py` | the prompts of each game, as in `prompts.py` |
| `reasoning_analysis.py`, `reasoning_analysis.ipynb` | what the reasonings talk about (length, "the user" frame, board reading, intention guesses about the opponent), per game, prompt and mode; the guesses next to the opponent's own reasoning, exported to `labels/guess_pairs.csv` for labelling by hand |
| `c4solver.py` | the exact value of every Connect Four move (Pascal Pons's solver with its opening book, compiled by `python c4solver.py --setup` into `c4solver/`, not in git): the oracle of the gate modes; with it, every move of a Connect Four game is judged (a *blunder* changes the game-theoretic value) |
| `gate.py` | the gates: who decides, before each move, whether the agent thinks (modes `goracle`, `gcrit<k>`, `gself`, `gsurp`, `grand<p>`, `lg…` to keep the last reasoning) |
| `when_to_think.ipynb` | the three Connect Four experiments read together (the gate, the memory, the surprise) with the verdict of each, the tests and the graphs; run locally on `results/` |
| `gate_analysis.py` | the moves judged by the solver, per mode and gate: blunder rates on critical and quiet positions, what thinking changed (the shadow fast moves), the gates' precision and recall against the solver, strength against tokens; it also judges the old games from their histories |
| `tests/test_duel.py`, `tests/test_gate.py` | rules and parsing, opponents, the game loop, `run_duel` end to end; the solver, the gates, the shadow fast moves, the `.moves.jsonl` |

The score is the agent's: win 1, draw 0.5, loss 0 (`max_score` 1). An illegal answer is not played: the agent reads that it is not legal, with the legal moves (not the board again), and answers again;
after `max_illegal` wrong answers in a row a random legal move is played for it (or it loses, with `illegal_policy: forfeit`).
The agent plays first on the odd seeds and second on the even ones (`colors: alternate`).
A chess game stopped at `max_steps` is a draw on the board; with `adjudicate: stockfish` (both duel grids), Stockfish evaluates the final position and the side ahead by `adjudicate_cp` (300 = 3 pawns) wins it: `final_eval`, `adjudicated_result`, `adjudicated_score` (equal to the result on the board for the games that ended by themselves). `adjudicate.py` adds them to records already played, from their final position.
Next to every record: `.transcript.md`, the game move by move with the reasonings of both agents, and `.thoughts.jsonl`, the same reasonings as data.
In self-play they are what the experiment is about: does a model reason, unprompted, about what its opponent is doing, and is it right according to the opponent's own reasoning?
That is why `duel_selfplay.yaml` uses the `neutral` prompt: `CoT` asks about the opponent's threats and replies.
`neutral` still structures the reasoning (evaluate the position, compare candidate moves, be brief) and calls the player "the user"; `free` (`duel_free.yaml`) only says which game is played and asks to think; `plain` (`duel_plain.yaml`) adds nothing to the observation, only the switch that turns the thinking on. CoT, neutral, free and plain go from the most to the least steered.
`predict` is free plus one instruction: after the move, the column (or move) the model expects from the other player, like `4 (expecting 3)`; the move is parsed from the first part, the prediction from the second (`duel_games.split_prediction`), and the record counts the predictions and how many came true (`predictions`, `prediction_hits`).
To add a game: a class in `duel_games.py` (see `DuelGame`) and its prompts in `duel_prompts.py`.

### When to think: the gate modes

The fixed modes think on a clock (`e1` every move, `r4` about every 4). A gate mode (`gate.py`) thinks only when its gate says so:
`goracle` answers without thinking first and the solver checks that fast move: the agent thinks only if the fast move is a blunder or illegal (the ceiling of any gate);
`gcrit` thinks when the solver says only one move keeps the game value (no model tokens);
`gself` asks the model itself, without thinking, whether the position needs careful thought (YES / NO, a few tokens, in a context of its own);
`grand<p>` thinks at random with probability p% (the control for the thinking frequency alone);
`gsurp` (surprise, with the `predict` prompt) thinks when the other player did not play the move the agent expected, or when it expected nothing: after every answer a second context of the same model, without thinking, reads what the agent wrote (its reasoning when it thought, and its answer) and extracts the expected move; the regex reading of the answer is recorded next to it. `lg<gate>` keeps the last reasoning in the context like `l1`.
With `shadow_fast_move: true` in the grid, every agent also answers without thinking before each thinking turn: the fast move is recorded, not played and not counted as a cost, so for `e1` and `r4` we see when thinking changed the move and whether it repaired a blunder or spoiled a good fast move.
Every move of an agent goes to `.moves.jsonl` next to the record (the solver's view of the position, the move's quality, the gate's decision, the fast move, the tokens by kind) and the record gets `blunders`, `blunder_rate`, `think_moves`, `think_rate`, `repaired`, `spoiled` (and the `opponent_` versions in self-play).
The gate modes exist only in the two-player games; `goracle` and `gcrit` only in Connect Four. Tokens: `tokens.generated` is the cost of playing (thinking, answers, the summaries of the `s` modes in `generated_summary`, and the gate's own tokens: the discarded fast answers of `goracle`, the questions of `gself`, the extractions of `gsurp`); the shadow answers are in `generated_shadow` only.

### Keep, drop or summarize

The memory modes keep a reasoning whole (`1`, `l1`, `r4`, `lr4`) or drop it (`e1`, `er4`). The `s` modes (`s1`, `ls1`, `sr4`, `lsr4`, `grids/duel_c4_summary.yaml`) are the arm in between: after each reasoning the model, without thinking and in a context of its own that never shows the board, writes a note of at most three sentences (its plan, the dangers it saw, what it expects), and the note stands in the context in the place of the reasoning (`.log`: `SUMMARY`). `s` keeps every note, `ls` only the last, like `l1`.
