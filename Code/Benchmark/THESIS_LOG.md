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

### Faster dry-run summary (2026-10-28)
- **Problem:** `summary()` (the "played / left" table of the notebooks and of `--dry-run`) took over 4 minutes on
  Colab. Counting played and left games is cheap (one file check per game); the token estimate opened and parsed
  every record of the whole results folder (about 8000, with their trajectories) to keep the ones of the grid's
  modes, and on Colab the folder is Google Drive, where every file open is a network request.
- **Fix:** `run.token_samples()` lists only the folders of the grid's models, games, prompts and modes, and reads at
  most 30 records per mode, evenly spaced over them (so all levels and models count). Locally 0.54 s → 0.03 s, with
  the same estimates (e.g. e1 2.3 against 2.5 Mtokens left).
- Difference: the estimate now uses only the grid's models and prompts, not any model; a mode with no games of the
  grid played yet shows no estimate.

### Related work found (2026-10-28)
- **Strat-Reasoner** (arXiv 2605.04906): the closest. Qwen3-4B, Tic-Tac-Toe / Kuhn Poker / Hanabi (Connect Four out of
  distribution). The prompt forces 4 fields (OpponentIntent, OpponentPrediction, MyIntent, MyPrediction) and an LLM
  judge scores them against the opponent's own reasoning at t-1 and its action at t+1; the score is an RL reward.
  So opponent modelling is imposed and trained, not emergent. Their alignment measure is the template for our
  Level A/B check; the difference is that we do not ask for it.
- **EMO** (NAACL 2025), **SOM** (2605.07301): explicit opponent models built by the agent; prompted by design.
- **Observer, Not Player** (2512.19210): the LLM only observes and predicts a player's strategy; argues win rate
  mixes opponent modelling, planning and luck.
- **Kosinski** (2302.02083): the "spontaneously emerged" ToM framing, on false-belief tests, not in agent traces.
- **TextArena** (2504.11442), **SPIRAL** (2506.24119, Qwen3-4B self-play on zero-sum games), **GTBench**
  (2402.12348, includes Connect Four), **GameBench** (2406.06613), **Game Reasoning Arena** (2508.03368),
  **LLM Chess** (github maxim-saplin/llm_chess): game benchmarks, scores and illegal moves, not reasoning content.
- **TALES** (2504.14128): text adventures incl. TextWorld; no study of when/how much reasoning models think.
- **The Danger of Overthinking** (2502.08235): long reasoning hurts agents (analysis paralysis); relevant to e1 vs
  r4 and to the free prompt's long board-reconstruction reasonings.
- Keeping reasoning in context: a tool-calling study (2606.00135) finds that keeping the thinking history helps
  Qwen3-4B/8B by about 3-5% on multi-turn BFCL; OpenAI drops it by default. Our r4/er4/lr4 comparison is a
  controlled ablation of exactly this, in games.
- **Gaps we fill:** nobody measures whether opponent modelling appears *unprompted* in the reasoning of thinking
  models, checked against the opponent's own reasoning (Strat-Reasoner asks for it and trains it); nobody varies
  how much the prompt structures the reasoning (CoT > neutral > free > plain) and measures the effect on it; nobody
  compares when-to-think schedules and keeping vs dropping old reasoning in games.

### Connect Four: the same prompts on a simpler game (2026-10-28)
- **Why:** in chess, Qwen3-4B spends much of its thinking working out where the pieces are, and the free pilot
  suggested that understanding the position, not the prompt, is what limits reasoning about the opponent. Connect
  Four has a board that is easy to read (7 columns) and explicit threats (three in a row), so if the bottleneck is the
  position, opponent reasoning should appear more often there, and the prompts may differ more.
- **New grid** `grids/duel_c4_prompts.yaml`: Qwen3-4B self-play, prompts CoT, neutral, free and plain (all already
  written for Connect Four in `duel_prompts.py`), 8 games each, 32 in total: modes e1 and r4 against self-e1 and
  self-r4 (the mode pairs of the chess pilots), seeds 1-2, so every pair plays once as X and once as O.
- `default` is left out: it asks "Do you think you're winning?", a different kind of question, with no reasoning
  steps but also no reason to compare it here.
- No adjudication: a Connect Four game always ends (four in a row or a full board, at most 21 moves per side), so
  there are no repetition draws and the results should separate the modes better than in chess. max_steps 30.
- CoT is included here as the upper bound: it explicitly asks whether the opponent can connect four and what its best
  reply is, so it shows how much opponent reasoning the model produces when asked.
- To look at the results in `analysis.ipynb`: `duel_game = "connect_four"` and `duel_prompt` = the prompt (the
  advantage panel stays empty: Stockfish is only for chess).

### Plain (chess) and Connect Four results (2026-10-28)
Qwen3-4B self-play. Chess: neutral 8 games, free 17, plain 8. Connect Four: 8 games per prompt (CoT, neutral, free,
plain).

**Outcomes**
- Chess: all 33 self-play games are draws by threefold repetition, now with every prompt. Two plain games end in a
  repetition although one side had a forced mate (Stockfish ±99995). The chess win/draw/loss results say nothing
  until the repetition rule is changed (see "All the self-play games end in threefold repetition").
- Connect Four: every game ends with four in a row, no draws, almost no illegal answers (0-0.4 per game, against
  2-3 in chess). **e1 beats r4 in 14 of the 16 games between them** (7 of 8 with e1 as the agent, 7 of 8 with e1 as
  the opponent), with every prompt: thinking at every move clearly wins in Connect Four. First player wins 4 of 8
  with CoT, so no strong first-move advantage at this level.

**Reasoning about the opponent.** Intention guesses counted with a strict pattern ("O / the opponent / they is
trying to / aiming / planning / going for / setting up / building ..."):

| | reasonings with an intention guess | guesses per reasoning |
|---|---|---|
| chess neutral / free / plain | 7% / 3% / 5% | 0.07 / 0.04 / 0.09 |
| Connect Four CoT | 27% | 0.80 |
| Connect Four neutral | 28% | 0.47 |
| Connect Four free | 42% | 1.11 |
| Connect Four plain | 45% | 1.20 |

- **In Connect Four the behaviour appears, and without being asked.** About 6-10 times more intention guesses than
  in chess. The free reasonings use the word "opponent" 1.7 times per reasoning, more than CoT (1.3), although the
  free prompt never mentions it.
- **The less the prompt steers, the more the model guesses intentions**: plain and free about 45%, neutral and CoT
  about 27%. CoT asks a concrete question ("Can your opponent connect four with their next move?"), and the model
  answers it by checking the board; it does not ask itself what the opponent wants. So the prompt that asks about
  the opponent produces threat checking, the prompts that do not ask produce more mind reading.
- This supports the reading of the free pilot: in chess the bottleneck is understanding the position; in a game the
  model can read, opponent modelling emerges.
- The prompt also changes the frame again: "the user" in the first lines in 91% of the neutral reasonings, 73% CoT,
  56% plain, 34% free.

**Are the guesses right?** Checked by hand in the e1 vs self-e1 games (both sides reason at every move), each guess
against the opponent's own reasoning on the move before:
- right: plain seed 1, ply 5. X: "since the opponent just played in column 3, maybe they are trying to block me".
  O at ply 4: "the best move is to block X's potential vertical line in column 3". Unprompted, with the word
  "opponent", and exactly right;
- right: free seed 2, ply 6 (O: "X is trying to create a line in those columns"; X: "That way, I can start building a
  vertical line"), and ply 10 (O: "X is trying to create a vertical line"; X: column 5 "connects to existing Xs");
- wrong: plain seed 1, ply 11 (X: "O is trying to connect... a vertical line in column 5... a horizontal line in row
  5"; O was blocking X's vertical line in column 4), and ply 14 (O: "X is trying to complete a line"; X was blocking
  O in column 5);
- wrong: free seed 2, ply 7 (X lists vertical, horizontal and diagonal plans for O; O had no plan, it played column 6
  "to continue the pattern" of even columns), ply 9 (X: "O is trying to build a vertical line in column 4"; O chose
  column 4 because it "is the center"), ply 11 (several plans; O played column 6 because it "is available").
- **Pattern:** the model almost always attributes an attacking plan to the opponent ("trying to make a line"). It is
  right when the opponent was in fact attacking, and wrong when the opponent was blocking or had no plan. It also
  often lists several hypotheses (vertical, horizontal, diagonal), so "right" has to be judged on the first or main
  guess, not on any of them. Both points matter for the labelling rubric.
- Checking needs the opponent to reason on that move: in e1 vs r4 games many guesses are right on the board (e.g. "O
  is trying to complete a vertical line in column 5", O had three in column 5) but the intention cannot be checked.

**Next:** more Connect Four games (the effect is clear, the sample is small), e1 vs e1 for the most checkable pairs;
the semi-automatic labelling of the guesses against the opponent's reasoning.

### Automatic reasoning analysis: reasoning_analysis.ipynb (2026-10-28)
- **Why:** the tables of the last entries were computed with one-off scripts; they have to be reproducible and
  re-run on every new batch of results.
- `reasoning_analysis.py` reads the `.thoughts.jsonl` of every duel game and counts, per reasoning: words, "the
  user" frame, sentences reading the board, "Wait", the word "opponent", and the intention guesses (the other side,
  "opponent" or "they" + is / might be / could also be... + trying / aiming / planning / going for / setting up /
  building). The patterns are rough on purpose: they compare groups, they do not judge sentences.
- `reasoning_analysis.ipynb`: the comparison table per game and prompt, the same per thinking mode, the outcomes
  (wins, draws, losses, endings, illegal answers), a sample of guesses, and the export of every guess next to the
  end of the opponent's reasoning on the move before to `labels/guess_pairs.csv`, with an empty `label` column to
  fill by hand (right / partly / wrong / unclear). Re-running keeps the labels already written; the last cell gives
  the share of right guesses per game and prompt.
- The pattern was widened to accept an adverb ("could also be setting up"); the counts barely change (chess plain
  4.5% -> 5.1% of reasonings, the other groups unchanged).
- First export: 102 reasonings with guesses, 55 checkable (the opponent reasoned on the move guessed about).

### Related work: Strat-Reasoner (He et al., arXiv 2605.04906, 2026)
**What it does.** RL fine-tuning (GRPO-style, LoRA) of Qwen3-4B for strategic reasoning in two-player games. Every
turn the model must fill a structured "recursive reasoning" block after its free thinking: OpponentIntent (what the
opponent meant with its last move), OpponentPrediction (what the opponent expects me to do), MyIntent, MyPrediction
(the opponent's next move). During training the two players' blocks are compared ("centralized CoT"): e.g. my
OpponentIntent against the opponent's own MyIntent; an LLM judge (the base model itself) scores the agreement
between 0 and 1 (bands: 0-0.3 inconsistent, 0.3-0.6 partial, 0.6-0.8 mostly accurate, 0.8-1 highly consistent),
and that score is added to the game reward. Trained on Tic-Tac-Toe, Kuhn Poker, MiniHanabi; tested also on Connect
Four, Leduc Hold'em, SimpleHanabi, against MCTS / Nash-equilibrium bots. No chess. They report +22.1% on average over
the games and results close to GPT-5-mini and Gemini-2.5-Flash. (Numbers read from the HTML version, whose tables
are partly garbled: to be checked on the PDF before citing.)

**Same measurement idea.** Their alignment score is exactly our check: one player's guess about the opponent's
intention, compared with the opponent's own reasoning. This supports our method; their judge bands can be our
labelling rubric (right ≈ 0.8-1, partly ≈ 0.3-0.8, wrong ≈ 0-0.3). Their evidence that the judge is reliable is thin
(one Tic-Tac-Toe example scored by six judges); we label by hand first and only then a model judge, measuring the
agreement (Cohen's kappa).

**What we add / where we differ.**
- They *force* opponent modelling (a mandatory structured block) and *train* it; we ask whether it *appears by itself*
  in the untrained model. Their premise is that current LLMs reason "non-strategically" in multi-agent games, but
  they do not measure it: no alignment score of the base model is reported. Our results are that missing baseline,
  on the same model (Qwen3-4B): without being asked, it attributes intentions to the opponent in about 45% of its
  Connect Four reasonings (plain, free) but in only about 5% in chess. "Non-strategic" depends on the game.
- They keep free thinking before the structured block to avoid "cognitive degradation caused by rigid formatting",
  but do not test it. Our Connect Four results are evidence on that choice: the structured or opponent-asking prompts
  (CoT, neutral) give fewer intention guesses (about 27%) than the unstructured ones (about 45%). Asking about the
  opponent (CoT: "can your opponent connect four?") produces threat checking, not mind reading.
- Recursive reasoning (their OpponentPrediction, "what does the opponent expect me to do") also appears unprompted
  in our games: plain, Connect Four, seed 1, ply 5, X: "since the opponent just played in column 3, maybe they are
  trying to block me", which matches O's own reasoning ("block X's potential vertical line in column 3").
- Their games are small and easy to read (Tic-Tac-Toe, Kuhn Poker), and they leave out chess. Our chess results
  suggest why: when the model cannot read the position, it does not get to the opponent.
- Our error pattern (the model assumes the opponent attacks, and is wrong when the opponent blocks or has no plan)
  is the kind of bias their alignment reward should correct; worth saying in the thesis as a reason why training
  helps.
- Setting: they train two separate agents and evaluate against bots; we use one untrained model with two separate
  contexts and self-evaluation modes (how often it thinks), which they do not vary.

**Possible link with our numbers:** their Connect Four opponent is MCTS (10 and 100 simulations). An MCTS opponent
for Connect Four in duel.py would make our base-model scores directly comparable with their Qwen3-4B baseline.

### Strat-Reasoner, checked on the PDF: what supports "LLMs are bad at this"? (2026-10-28)

Read on the PDF (v2, ICML 2026). The table numbers in the entry above are confirmed (Table 2: base Qwen3-4B on
Connect Four 63.20/57.01 vs MCTS-10, 62.77/58.39 vs MCTS-100; Strat-Reasoner 70.39/70.26, 75.93/69.26).

The claim that LLMs are bad at strategic reasoning is made, but nothing cited shows it:
- Intro: "Current LLMs, even Large Reasoning Models (LRMs), perform poorly in multi-agent environments because
  single-agent reasoning paradigm is non-strategic." That sentence has no citation. The one before it cites Zhang et
  al. 2024 ("LLM as a Mastermind", arXiv 2404.01230), a survey from April 2024. It is older than reasoning models and
  gives no verdict on performance (checked on the abstract).
- "LLM agents are more unpredictable as their decision making involves a complex reasoning process (Zhao et al., 2024)".
  That reference is a survey on explainability of LLMs, not about games or opponents.
- "The opponent's thought process contains rich strategic information that outcome-based signals fail to capture
  (Lightman et al., 2023)". Lightman is "Let's verify step by step" (process reward on math), with no opponents.
- SPIRAL and MARSHAL are said to "lack explicit modeling of opponents' reasoning". This is a design claim about those
  training methods (MARSHAL's abstract measures game scores, not whether the model reasons about the opponent).
- Figure 1 ("unstructured reasoning fails to handle strategic traps") is a drawing, not data.
- Their own evidence is the base model's game score against bots (Tables 1-2). That measures playing strength, not
  whether the model thinks about the opponent. The normalisation of the scores is not defined ("computed from the raw
  OpenSpiel returns"), and the prompt used for the baselines is not stated.
- No ablation gives the untrained model the recursive prompt, and no alignment score of the base model is reported.
  The "w/o CoT-adv" ablation is still trained, and only on MiniHanabi.

Their own case study (Appendix H, Tic-Tac-Toe) shows unprompted opponent reasoning in the free thinking, highlighted
in blue by the authors: "maybe they're trying to prevent X from completing a line", "the opponent might be trying to
create a line", "they might be aiming for a diagonal". These are the sentences our intention pattern counts. The same
case also shows errors: O's move at (0,2) does not block X's row, and the final MyPrediction, "Blocking by placing O
in (1,2)", is impossible because X has just won on (1,2).

Their prompt (Listing E.1) asks for "CONCISE" thinking, "No self-correction loops", plus a length penalty. That is the
kind of instruction that, in our neutral prompt, went with fewer spontaneous intention guesses.

For the thesis: their premise is asserted, not measured. Our data (Qwen3-4B, about 45% of Connect Four reasonings
contain an intention guess without being asked) is a direct measurement of what they assume is absent. The
literature that does test LLMs' strategic play against solvers (e.g. GTBench, Duan et al. 2024) measures play
strength; the references must be checked before citing.

### Is it because the reasonings are ephemeral? New mode l1 and grid duel_c4_memory.yaml (2026-10-28)

**Question.** Most self-play reasonings come from e1, which drops every reasoning from the context. Could the
opponent reasoning we see (or don't see) depend on that? A model that remembers what it thought could, for example,
check whether the opponent did what it expected.

**What "ephemeral" removes.** Only the agent's own reasonings. The boards, the opponent's moves ("O played 3.") and
the agent's answers stay in the context in every mode. So e1 does not forget the game; it forgets its own thoughts.

**What the existing data says (and why it is not enough).** The r4 modes keep all their reasonings. They have fewer
intention guesses than e1, not more (Connect Four: plain 23% vs 51%, neutral 12% vs 31%; free 40% vs 42%). But r4
also thinks less often (about every 4 turns, after normal turns without thinking) and with a longer context, so
memory and thinking frequency are mixed. Also, Qwen3 is trained with the earlier thinking removed from the
conversation (its chat template strips it), so a kept reasoning is unusual input for the model.

**Change.** New mode "lN" in agent.py (parse_mode): it thinks every N turns, at fixed intervals, and keeps only its
last reasoning in the context until the next one (like lrN, but without the random intervals). l1 is e1 with
memory of the previous reasoning: same thinking frequency, only the memory changes. Test in
tests/test_profiles.py (l1 keeps exactly the last reasoning and thinks on the same turns as e1).
"1" (think every turn, keep everything) already exists but is not usable here: a Connect Four reasoning is ~2400
words, so after ~10 moves the context passes the 40k tokens of the engine, and when the context is full the
answers are replaced by illegal ones.

**Grid.** grids/duel_c4_memory.yaml: Qwen3-4B, Connect Four, every pair of the modes of the chess self-play plus l1
(e1, l1, r4, er4, lr4, each against self-e1 ... self-lr4), plain and free prompts, seeds 1-2: 100 games (the e1/r4
pairs repeat games of duel_c4_prompts.yaml). e1 vs l1 separates memory at the same frequency; er4 / lr4 / r4 give
none / last / all at the lower frequency, so frequency and memory can be told apart. Compare with
summary(table, by=["game", "prompt", "player_mode"]) in reasoning_analysis.ipynb. Things to look at: the share of
reasonings with guesses, whether l1 reasonings refer back to their previous expectation ("I expected O to block…"),
and whether their guesses are more often right.

### Memory run results: duel_c4_memory.yaml (2026-10-29)
100 games (Qwen3-4B, Connect Four, modes e1/l1/r4/er4/lr4 against each other, plain and free, seeds 1-2); with
the earlier Connect Four games, 50 games each for plain and free. All ended in four in a row; illegal answers
almost zero (0.06-0.12 per game). First player won 45 of 100: no first-move advantage at this level.

**Who wins** (self-play, each game counted for both players, a mode against itself left out; 32 games per mode,
8 per pair):

| mode | e1 | l1 | lr4 | r4 | er4 |
|---|---|---|---|---|---|
| average score | 0.78 | 0.69 | 0.38 | 0.38 | 0.28 |

- Thinking at every move wins clearly: e1 and l1 beat every "about every 4 moves" mode (e1 vs r4 8 of 8; l1 0.62-0.88
  against the r4 family). Confirms the e1 > r4 result of duel_c4_prompts.
- Memory does not help: e1 vs l1 0.62 for e1 (8 games, not significant), and among the r4 family keeping all (r4),
  the last (lr4) or none (er4) of the reasonings gives 0.38 / 0.38 / 0.28. **Frequency of thinking matters, memory
  of the reasoning does not** (at this sample size).

**Reasoning about the opponent** (reasonings with an intention guess):

| player mode | free | plain |
|---|---|---|
| e1 | 54% | 45% |
| l1 | 59% | 47% |
| lr4 | 52% | 57% (28 reasonings) |
| er4 | 44% | 40% |
| r4 | 42% | 21% |

- l1 guesses as often as e1 (59 vs 54%, 47 vs 45%): keeping the previous reasoning does not increase opponent
  modelling. So the ephemeral reasonings of e1 were not what limited it.
  **Correction (same day): not valid. l1 had a bug and never read its previous reasoning while thinking, so it
  played exactly like e1 (see the next entry). The e1/l1 comparisons here only measure run-to-run noise.**
- l1 never uses its memory for the opponent: 0 of 254 l1 reasonings refer back to a previous expectation ("I
  expected O to...", "as predicted", "O did not block as..."); same for e1 (0 / 324). The phrases "earlier I
  thought" / "my previous analysis" appear about 10 times, but they are self-corrections inside the same reasoning
  about the board ("earlier I thought column 1 was full"), not memory of the previous turn. The model does not
  track whether its predictions came true.
- r4 (all reasonings kept, thinks rarely) has the fewest guesses, especially with plain (21%); with the frequency
  mixed in, it is not clear if the long context or the rare thinking causes it.
- Prompts, now with 50 games each: free 54% > plain 45% > neutral 28% ≈ CoT 27% (8 games each). Same ordering as
  before: the prompts that do not mention the opponent produce more intention guesses than the one that asks about it.
- Board reading is still heavy in Connect Four: about 35% of the sentences, median 17-22 "Wait" per reasoning,
  with real misreadings (e.g. "X O X O X O X" read as four X in a row).

**Labels:** labels/guess_pairs.csv has 375 reasonings with guesses, 202 checkable; none labelled yet. The accuracy
of the guesses is now the missing piece.

### Results of duel_c4_memory.yaml, and a bug in l1 (2026-10-29)

100 games, all ended by four in a row, 0.09 illegal answers per game, no answer hit the token limit. First player
(X) won 45 of 100 (23 as agent X, 22 as opponent X): no first-move advantage to correct for.

**Bug: l1 never read its previous reasoning while thinking.** The "last" mode removes the kept reasoning at the
start of the next self-evaluation, before the model generates (that is how lrN worked in the old agent too). With
l1 the next self-evaluation is the next turn, so the reasoning was gone every time the model could have used it:
l1 played exactly like e1. The token counts show it: largest prompt e1 996, l1 1165 tokens (a kept reasoning is
~3500), while r4 reaches 5518. The test only checked the context after each turn, not the prompt at generation.
Fix: lN now has its own keep mode "previous": the last reasoning stays during the next self-evaluation and is
removed after the new one is generated. The test now checks what the model reads at each self-evaluation (l1: its
previous reasoning only; r4: all; lr4, er4, e1: none), and fails on the old code. lrN is unchanged.
**Note on lr4:** lr4 never reads its last reasoning while thinking either; it reads it only during the normal
(non-thinking) turns in between. This is intended (lr4 is meant to work like that), unlike l1. So the only mode in our data that thinks with its own past
reasonings in view is r4.
The l1 games must be played again (see the renaming below). The old l1 games are an accidental replicate of e1, useful as a noise
estimate.

**Strength: how often it thinks matters, memory does not show.** Score of each mode over its 40 game sides (both
as agent and as opponent): e1 0.73, l1 (= e1) 0.65, r4 0.40, lr4 0.40, er4 0.33. Thinking every turn beats thinking
about every 4 turns; among the every-4 modes, keeping none / the last / all reasonings gives about the same.

**Opponent reasoning (share of reasonings with an intention guess, plain + free):**
e1 49% (243 reasonings), l1 = e1 replicate 53% (254), er4 42% (50), lr4 55% (51), r4 31% (54).
- e1 vs its replicate differ by ~4 points: differences of that size are noise.
- r4, the only mode that reads all its past reasonings while thinking, has the fewest guesses (31%, vs 42% for er4
  which reads none). In the earlier grid too (r4 < e1). Remembering past reasonings, at least in this form, does not
  increase opponent reasoning; if anything it reduces it. Only ~50 reasonings per every-4 mode, so this is a hint.
- r4 almost never refers back to what it thought: 2 sentences in 54 reasonings ("Earlier, I thought that column 4
  was blocked..."), both about the board, none about an expectation of the opponent. The kept reasonings are read
  as context, not used as a plan or a prediction to check. This is consistent with Qwen3 being trained with the
  earlier thinking removed from the conversation.
- Ephemerality is therefore not what limits the opponent reasoning in our results. The proper test, l1 with the
  fix (every turn, previous reasoning in view), is still to be run.

**Renamed the buggy l1 runs to e1b (2026-10-29).** The 36 Connect Four games with the old l1 (20 as agent, 16 as
opponent "self-l1") were played with e1's behaviour, so they are now called e1b ("a second e1"): folders
connect_four/{plain,free}/l1 -> e1b, files levelself-l1-* -> levelself-e1b-*, and in the records mode "l1" -> "e1b",
level/opponent "self-l1" -> "self-e1b", plus the first line of the transcripts and the mode columns of
labels/guess_pairs.csv. Nothing else changed in the games. In the tables e1b is a replicate of e1 (noise estimate).
Since no l1 result exists any more, running duel_c4_memory.yaml again plays only the 36 l1 games, with the fixed
mode (the same files must be renamed or removed in the Colab/Drive copy of the results, otherwise they are skipped
there).

**Grid for the corrected l1: grids/duel_c4_l1.yaml (2026-10-29).** l1 as agent against self-e1, self-l1, self-r4,
self-er4, self-lr4, plain and free, seeds 1-4 (colors alternate: two games as X, two as O per opponent and prompt).
40 games; every pair gets 8 games, like the pairs of duel_c4_memory.yaml (there 4 with each mode as agent, here all 8
with l1 as agent). Run this grid instead of duel_c4_memory.yaml, which would also play the 16 games with l1 as the
opponent.
