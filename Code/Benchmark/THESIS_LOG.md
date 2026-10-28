# Thesis log

What was done in the Benchmark, why, and what came out of it, in order. For the thesis: the decisions and their
reasons, not the code (the code is described in README.md).
Newest entries at the bottom. Dates are those of the commits or of the work.

## Part 1: the Benchmark for the cooking games (October 2026)

From the commit history; the details of these steps are in README.md and in the commits.

- **2026-10-05/07: runs that do not stop.** Out-of-memory check, a stop for long loops, more logs for slow
  games, better parallel play. *Why:* the experiments of 6/7 had runs that crashed or never ended.
- **2026-10-08: lr4 mode** (think about every 4 turns, keep only the last reasoning in the context).
  *Why:* a middle ground between r4 (keeps all of them, the context grows) and er4 (keeps none, no memory of the
  plan).
- **2026-10-18: grid system** (models × modes × prompts × levels × seeds in a YAML, one JSON record per game, the
  games already played are skipped). Fixed the wrong attribution of some computations, new tests.
  *Why:* comparable results across models, and runs that can be interrupted and resumed on Colab.
- **2026-10-19: automatic engine** (vLLM, many games at once). Each game runs in its own thread. When its agent
  needs an answer, the request joins the batch vLLM is already generating (continuous batching), so a game does not
  wait for the other games' self-evaluations. vLLM also reuses the context already read in the previous turns
  (prefix caching).
  *Why:* speed: one game at a time left the GPU mostly idle.
  Side effect: the times are wall-clock times of games played together, so they cannot be compared between runs
  (the comparison uses tokens, not seconds).
- **2026-10-19: automatic number of parallel games** (`n_parallel="auto"`, `ContextBudget` in `engine.py`), refined
  in "Better automatic engine".
  - *The problem:* while a game waits for its answer, which is almost all the time, its whole context sits in vLLM's
    KV cache. With too many games at once the contexts no longer fit: vLLM pauses some requests and later computes
    their contexts again (preemption), and the tokens per minute drop. With too few games the GPU is underused. The
    right number is not fixed. It depends on the model (the bigger the model, the less memory is left for the cache),
    on the GPU, and on the mode: e1 contexts grow much larger than 0 contexts.
  - *How it works:* the KV cache capacity in tokens is read from vLLM (blocks × block size). A game counts for the
    size its context is expected to reach **by the end of the game**, not its current size, because contexts grow at
    every turn. The expected size is the 75th percentile of the final contexts of the games of the same group (the
    mode) that have already ended. Until 3 games of the group have ended, a prior of 20k tokens is used. A new game
    starts only if all the expected contexts fit in 90% of the cache. Limits: at least 2 and at most 32 games at a
    time.
  - *Backfill:* if the next game in the queue does not fit, a smaller game further back can start instead, which
    keeps the GPU busy. So that the big game does not wait forever, the backfill stops after 5 minutes of waiting:
    the small games end and make room for it.
  - If the cache size cannot be read, 8 games are played at a time.
  - In self-play the group is (mode, opponent), because two contexts grow inside one game.
- **2026-10-20/21: main tests on Qwen3-1.7B, lr4 added to the grids, last results.**

## Part 2: two-player games (from 2026-10-26)

### Why two-player games
The cooking games test planning against a fixed environment. A game against an opponent adds something the
environment does not have: another mind. It allows asking whether a model reasons about what its opponent is
doing, and whether the thinking modes (0, e1, r4, er4, lr4) play differently when the environment reacts.

### Chess and Connect Four (2026-10-26, commit "added chess")
- Same agent and same modes as cooking, against an opponent. The "levels" of a game are its opponents:
  `random`, `greedy` (one move ahead), Stockfish (`sf<N>`, `elo<N>`), later the model itself.
- Score: win 1, draw 0.5, loss 0. The agent plays first on odd seeds, second on even ones (fair colors).
- The legal moves are listed in the observation. *Why:* like the admissible commands in TextWorld; without them,
  small models mostly produce illegal moves and the experiment measures notation, not play.
- Connect Four as well. *Why:* a simpler game, so the small models are not all at zero, and a check that the
  results are not chess-specific.
- An illegal answer is not played: the agent is told and answers again; after `max_illegal` wrong answers a random
  legal move is played for it (the `forced` column).
- Grid: `grids/duel_tier1.yaml` (Qwen3 1.7B/4B/8B, all modes, CoT prompt, random/greedy/sf0).

### Pilot results and the forced moves
- Connect Four pilot: few forced moves. Chess pilot: many illegal answers and forced moves.
- From the logs: the move parser was fine. The cause was **context overflow**: every retry repeated the whole
  position, the prompt grew to 80-97k tokens (over the 40k limit), the model lost track and repeated the quoted error.
- **Fix: short retries.** After an illegal answer the agent reads only "That is not a legal move", the legal moves
  and "Your move:", not the board again (it is already in its context).
- Possible later changes, not applied: `max_illegal: 1`, `max_steps: 50`.

### Model against model (self-play), 2026-10-27 (commit "added real duel system")
- **The research question:** does a model, unprompted, reason about what its opponent is doing (its plan, its
  threats, its next move)? And is it right, judged by the opponent's own reasoning?
- Same model (Qwen3-4B) on both sides, two separate contexts, different modes (every pair of e1, r4, er4, lr4).
  The two players see only the moves, never the other's reasoning.
- **Not forced on purpose:** no question about the opponent in the prompt. The hope is that it is an emergent
  behaviour. A new `neutral` prompt was written for this: the same three steps as CoT (evaluate the position, compare
  2-3 candidate moves, check legality) without any mention of the opponent, because CoT asks about the opponent's
  threats and replies, which would make the behaviour prompted.
- Mode `0` is left out of self-play: it never reasons, so there is nothing to read.
- Saved next to every game: `.transcript.md` (the game move by move with both reasonings, to read side by side) and
  `.thoughts.jsonl` (the same as data, for the automatic analysis).
- Grid: `grids/duel_selfplay.yaml` (160 games).
- **Idea for a later experiment:** a side question ("what do you think your opponent will play?") asked at some
  turns and then removed from the context, so it does not influence the game. Postponed so that the first
  experiment stays unprompted.

### Stockfish adjudication (2026-10-27)
- **Problem:** with `max_steps` a game can stop before it is decided; on the board it is a draw, even if one side is
  far ahead.
- **Fix:** Stockfish (full strength, depth 16) evaluates the final position of the chess games stopped at
  `max_steps`; the side ahead by 300 centipawns (3 pawns) or more wins. The board result is kept too
  (`result` and `adjudicated_result`). `adjudicate.py` adds it to games already played.
- The final evaluation is saved for every game (`final_eval`), also the finished ones. *Why:* first pilot game
  (e1 vs r4) was a draw by threefold repetition with White about 15 pawns down: win/draw/loss alone hides how a game
  went.

### Analysis: which mode plays better (2026-10-27)
- New section in `analysis.ipynb`. Self-play: a matrix of the score of each mode against each other mode;
  wins/draws/losses per mode; the average Stockfish advantage at the end. Against the bots: the score of each mode
  against random → greedy → sf0, one panel per model.
- In self-play each game counts for both players (the opponent's mode gets 1 - score); games of a mode against
  itself are left out of the totals (they always average 0.5).
- First 8 self-play games of Qwen3-4B: all draws.

### Planned: checking whether a model guesses its opponent correctly
- Step 0: how often a reasoning mentions the opponent at all (per mode, neutral vs CoT): the emergent behaviour.
- Level A (automatic): when a reasoning names an opponent move, is it the move actually played next? Compared with
  chance (1 / number of legal moves) and with Stockfish's best move (predicting the obvious move is not modelling the
  opponent).
- Level B (judgment): guesses about intentions ("they are attacking my king") compared with the opponent's next
  reasoning, only where the opponent reasoned. Labelled by hand on a sample, then by a larger model as judge, with the
  agreement measured (Cohen's kappa).
- Then: does the mode that models its opponent more (or better) also win more?

### First manual check of opponent modelling (2026-10-27)
- Read the 8 self-play pilot games (Qwen3-4B, chess, neutral prompt, about 130 reasonings).
- Almost every reasoning mentions the opponent's pieces, but nearly always to read the board ("Black's knight on f6
  is attacking e4") or to plan its own move. Attributing an intention to the opponent is rare: about 6 cases.
- Checked against the opponent's own reasoning:
  - correct: er4 vs e1, plies 8-9. Black plays Nd5 "to attack the white bishop on c4"; White: "Black's knight on d5 is
    threatening to take the bishop", and takes the knight (Bxd5);
  - correct but generic: Nc6 played "for development, a central square"; read as "probably aiming to control the
    center";
  - wrong: Qh5 played "to develop the queen, a more active piece"; read as "White is trying to attack the king" (the
    textbook idea of Qh5, not this player's). Nf5 played "to attack the knight"; read as "control the center";
  - not checkable: two guesses about an r4/lr4 opponent that had not reasoned on that move.
- Explicit predictions of the opponent's next move: practically none (only the king's escape squares after a check),
  so Level A will have little data with this model and prompt.
- Obstacles: frequent board hallucinations (pieces where there are none), and the model calls itself "the user".

### Is the neutral prompt suppressing it? (2026-10-27)
- **Question:** neutral avoids the word "opponent", but it still tells the model how to think: "be concise and brief
  in 3 steps", "Evaluate the position", "List 2 or 3 candidate moves and compare them". That frames the task as
  finding the best move in a fixed position, which may itself steer the model away from thinking about the other
  player.
- **What the pilot reasonings show** (167 reasonings, sentence counts with keyword patterns, so rough):
  - the model follows the outline of the prompt ("First, I need to evaluate the position... Next, candidate moves..."),
    and judges each candidate by its own merits ("central", "develops", "active"): 9% of the sentences are about
    the value of its own moves, 7.5% about checking the legal moves, 0.6% about the opponent's intentions;
  - it ignores "concise and brief": median 570 words, mean 890, max about 5000;
  - 148 of the 167 reasonings start "Okay, let's see. The user is playing as Black...": the model reasons as an
    assistant advising a user, and even calls its own earlier moves "the user's moves". The other side is only
    "White", never "the opponent". Partly a Qwen3 habit, partly our system prompt ("The user shows you the position").
    This frame is a second, less visible way the prompt steers the reasoning;
  - reading the board dominates only 8 of 167 reasonings (over 25% of the sentences), with a median of 3 "Wait" per
    reasoning, so it does not explain the absence of opponent reasoning by itself.
- **Change:** a third prompt, `free` (`duel_prompts.py`, chess and Connect Four). System: "You are playing a game of
  chess. You are shown the position and you reply with your move in standard algebraic notation... Only output the
  move, nothing else." Self-evaluation: "Think about the game, then say your move in standard algebraic notation.
  Only output the move, nothing else." No steps, no "concise", no "how to win", no "user", no opponent. Same
  sampling as CoT and neutral, so only the prompt changes. The legality reminder of neutral is left out too: after an
  illegal answer the game already says it is not legal and lists the legal moves.
- **Why not "after each move of the other player":** even that names the opponent; the observations already say
  "White played e4", which is a fact of the game, not an instruction.
- **New grid** `grids/duel_free.yaml`: the same 160 games as `duel_selfplay.yaml` (Qwen3-4B, modes e1/r4/er4/lr4,
  opponents self-e1/r4/er4/lr4, seeds 1-10, same colours), prompt free. With `limit = 8` it plays seed 1 against
  self-e1 and self-r4 with every mode: the same 8 games as the neutral pilot, so the two can be compared directly.
- **The comparison:** CoT (asked to think about the opponent) > neutral (structured, not asked) > free (not
  structured, not asked). If free produces clearly more opponent modelling than neutral, the structure of the prompt
  was suppressing it, which is a finding in itself. Things to check in the free pilot: the rate of intention
  guesses, whether "The user is playing as..." disappears, the length of the reasonings (no "brief") and the illegal
  answers (no legality reminder).
- `tests/test_duel.py`: every prompt of a grid must exist; neutral and free must not contain "opponent", "threat" or
  "other player"; free must not contain "user", "step", "concise", "brief", "candidate", "evaluate", "how to win".

### Free pilot: less structure did not bring more opponent reasoning (2026-10-28)
- 10 games (the 8 of the neutral pilot, plus e1 and r4 against self-er4), Qwen3-4B, 228 reasonings. Same sentence
  counts as for neutral (keyword patterns, so rough):

  | | neutral | free |
  |---|---|---|
  | words per reasoning (median / mean) | 569 / 890 | 1357 / 1528 |
  | starts with "The user is playing as..." | 91% | 28% |
  | sentences reading the board | 18% | 22% |
  | reasonings dominated by reading the board (>25% of the sentences) | 8 / 167 | 43 / 228 |
  | "Wait" per reasoning (median) | 3 | 14 |
  | sentences judging its own candidate moves | 9.1% | 4.6% |
  | sentences attributing an intention or threat to the opponent | 2.2 per 1000 | 1.1 per 1000 |
  | illegal answers per game (both sides) | 2.5 | 3.9 |

- The "user" frame mostly disappears ("Okay, let's see. The board position..."), and the reasonings get more than
  twice as long, but the extra length goes into reconstructing the board, square by square, with many more "Wait".
  Without the steps, the model spends its thinking on working out where the pieces are, and judges its own moves
  less, not the opponent's more.
- Real intention guesses: still a handful, about as rare as with neutral. The best one: er4 vs e1, ply 18. Black
  asks itself "But why would White play Be6? Maybe to attack the Black king or to develop." White's own reasoning at
  ply 17: "Be6 ... a strong move to develop the Bishop and support the center ... attacking the Black knight on f6".
  The second guess is right. (Be6 also left the bishop to be taken by dxe6 or fxe6, and Black did not take it.)
- **Reading:** at 4B the bottleneck looks like understanding the position, not the prompt: neutral's structure was
  not suppressing opponent reasoning, if anything it pushed the model past the board and into comparing moves.
  The prompt does change the frame (the "user" disappears), which is worth reporting by itself.
- Cost: the e1 games take up to 4 times longer (82 min against 18 for e1 vs self-e1).

### All the self-play games end in threefold repetition (2026-10-28)
- All 18 self-play games so far (neutral and free) are draws by threefold repetition, also with huge advantages: in
  free lr4 vs self-e1 Black is 14 points of material ahead (Stockfish -42 pawns for White) and the game ends when
  both kings and queens shuffle back and forth (Ke3-Ke4-Ke3, Qd7-Qe8-Qd7). The winning side does not know how to
  convert, and the draw is claimed automatically (`claim_draw=True`). So the self-play results are all 0.5 and say
  nothing about which mode plays better; the Stockfish advantage at the end is the only measure that separates them.
- Not changed yet. Options: end the game only on the automatic rules (fivefold repetition, 75 moves), so that more
  games reach max_steps and are adjudicated; or adjudicate the repetition draws too; or show the move history,
  since the models only see the current board and may not notice they are repeating.

### Plain prompt: the model's own thinking (2026-10-28)
- **Why:** free still adds an instruction ("Think about the game, then say your move"). The real baseline is the
  model thinking as it does by default, with no instruction about the reasoning at all.
- **Change:** `plain` in `duel_prompts.py`: the system prompt of free, and on the turns where the model thinks
  nothing is added to the observation, only the switch that turns the thinking on (`/think` for Qwen3). Same
  sampling as CoT, neutral and free, so only the prompt changes. New grid `grids/duel_plain.yaml`, the same 160
  games; `limit = 8` is again the same 8 games of the other pilots.
- The scale is now CoT (asks about the opponent) > neutral (steps) > free ("Think about the game") > plain
  (nothing). Given the free pilot, plain will probably behave like free; it is still the baseline the other prompts
  have to be compared with.
- Tests: plain has the system prompt of free, an empty self-evaluation, and none of the banned words.
