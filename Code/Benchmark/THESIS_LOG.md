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
**Superseded:** the l1 of this run was buggy and played exactly like e1 (see the next entry; renamed e1b). The
conclusions below about memory ("l1 guesses as often as e1", "memory does not help") do not hold: they compare e1
with itself. The rest (who wins by thinking frequency, prompts, board reading) stands.

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

### Labelling the guesses by hand: label.py (2026-10-29)
- **Why:** we know how often the model guesses the opponent's intention, not how often it is right. A sample
  labelled by hand is the reference; an automatic judge (a larger model with the same rubric) can label the rest
  only if it agrees with the hand labels (Cohen's kappa).
- `python label.py` shows, in the terminal, a fixed random sample (seed 0) of 50 checkable guesses from
  labels/guess_pairs.csv, one at a time: the board the guesser saw (rebuilt from the record), its guess sentences
  with some of the reasoning before them, and the end of the opponent's reasoning on the move guessed about. Keys:
  right / partly / wrong / unclear / not_a_guess, an optional note; more context on demand; undo; resumable. The
  labels are saved after every answer in labels/human_labels.csv. `-n 80` extends the sample, keeping the first 50.
- **Rubric** (in label.py, shown with `h`): judge the main intention attributed to the opponent against the reason
  the opponent gives for its move; several hypotheses -> the main one; the intention, not the board reading; only
  the move just before.
- **New label not_a_guess,** found in the first sampled pair: O writes "the user's previous moves were in column 2,
  so maybe they are trying to create a vertical line in column 2", but column 2 is O's own: the model calls itself
  "the user", and the pattern counts it as a guess about X. The share of not_a_guess measures the precision of the
  automatic counts (the "with_guess_%" of the tables are an upper bound until then).

### First labels, by Claude: labels/claude_labels.csv (2026-10-29)
- The same 50 pairs of label.py (seed 0: 47 Connect Four, 3 chess), labelled by Claude with the rubric of label.py,
  each with a short note giving the reason. To be checked by hand; then they are the first "judge" to compare with
  the human labels (Cohen's kappa).
- Counts: wrong 25, partly 8, right 7, not_a_guess 6, unclear 4. Among the 44 real guesses: right 16%, partly 18%,
  wrong 57%, unclear 9%.
- **not_a_guess 6 of 50 (12%):** 4 are the model talking about itself ("the user" = its own colour, or "X is trying
  to block" written by X), 1 about its own captures (chess), 1 a negation. So about 1 in 8 of the automatic
  "guesses" are not about the opponent.
- **Why the guesses are wrong** (25): the opponent was blocking and the guess said attacking (10); the opponent had
  no plan (a pattern of columns, "the central column", "it is available") and the guess gave it one (9); the right
  kind of plan but another line (6).
- **The right ones** (7): 5 are attacking plans when the opponent was attacking, 2 are "O is blocking", one of them
  with the CoT prompt. The model attributes attacking plans by default; it rarely imagines the opponent defending.
- Two hard cases for the rubric: guesses that list several hypotheses (partly when the right one is not the main
  one) and hedged conditionals ("if O is trying to block") judged as the guess they state.

### Our labels against Strat-Reasoner (2026-10-29)
- Checked again on the paper: it reports **no alignment score** (base or trained, start or end of training) and no
  accuracy of the opponent's next-move prediction. Only game returns (Tables 1-4) and, for the judge's robustness,
  six judges scoring one OpponentIntent pair 0.85-1.00 (Table 5). So no number of theirs can be set against ours.
- Our labels in their bands (right 0.8-1 -> 0.9, partly 0.3-0.6 -> 0.45, wrong 0-0.3 -> 0.15, unclear left out):
  untrained, unprompted Qwen3-4B, Connect Four, average about 0.34 ("partial", close to "inconsistent"). Rough:
  midpoints of the bands, 40 guesses, labels by Claude still to be checked by hand.
- What we can say: this is the baseline they assume but do not measure. The base model does model the opponent
  without being asked (45-54% of reasonings) but is right in about 1 of 6 guesses (16% right, 34% at least partly),
  with a systematic attack bias; 12% of the pattern's "guesses" are self/other confusion, which their structured
  fields avoid by construction.
- To make it comparable: give the untrained model their recursive block (OpponentIntent, MyPrediction...) and score
  it the same way (prompted vs unprompted on the same games); an MCTS opponent for Connect Four for their game scores.

### Guessing, winning and tokens: new section of reasoning_analysis.ipynb (2026-10-29)
- **Why:** does modelling the opponent help to win? Is the number of guesses just a matter of length? How much do
  the prompts cost?
- **Code:** reasoning_analysis.py gets `player_result` (each reasoning's player won / drew / lost, after
  adjudication), `add_tokens()` (tokens of each reasoning, Qwen3 tokenizer), `game_tokens()` (tokens generated per
  player per game, from the records), `labelled_outcomes()` (labels joined with the guesser's result). Graphs saved
  in graphs/: labels_vs_winning, guessing_vs_winning, tokens_vs_guesses, tokens_per_prompt (.pdf).
- **Right guesses and winning** (Claude's 50 labels, Connect Four): guessers with right guesses won 4 of 7, partly
  6 of 8, wrong 16 of 24; right+partly vs wrong, Fisher p = 1.0. No sign that guessing right goes with winning; the
  sample is far too small to say more.
- **Guessing often and winning** (all Connect Four self-play, 212 player-games): share of reasonings with a guess,
  winners 42% vs losers 43% (Mann-Whitney p = 0.97); no significant difference within any mode either. Win rate by
  guess share: 0-25% 50%, 25-50% 59%, 50-75% 70%, 75-100% 40%: no trend; the 75-100% group is mostly every-4
  players with one or two reasonings, which lose more because of their mode. **Guessing the opponent, as the model
  does it, does not predict winning**; consistent with guesses being mostly wrong.
- **Length and guesses:** Connect Four, Spearman ρ = 0.27 (p < 1e-13): longer reasonings contain more guesses. But
  the guesses per 1000 tokens stay roughly flat with length (plain about 0.17-0.34; free decreasing): the guesses
  come with length, longer reasonings are not more "opponent-minded". Chess: ρ = 0.05, not significant.
- **Tokens per prompt:** in Connect Four a reasoning has a similar length with every prompt (medians about 3300-3900
  tokens; neutral shortest, plain longest). In chess neutral is much shorter (median about 800 tokens vs about 1900
  for free and plain): the steps of neutral cut the board reconstruction. Per game (modes e1 and r4 only, which
  every prompt played): Connect Four about 7-9k tokens per player with every prompt; chess plain about twice the
  others (median about 19k).

### Presentation updated, and a planned experiment: friend + player (2026-10-29)
- presentation/index.html (22 slides): new slides with the four graphs of reasoning_analysis.ipynb (labels vs
  winning, guessing vs winning, tokens vs guesses, tokens per prompt), each with a "how to read it" box; the l1 bug
  is left out (only the result matters there).
- **Planned: a two-agent player.** The model often reasons in the third person, as an advisor to "the user" (about
  60% of plain and a third of free Connect Four reasonings), and sometimes reads its own moves as someone else's plan
  (12% of the counted guesses). Is that outside view useful, or does it blur who is who? Experiment: an external
  agent ("the friend"), which watches the game in the third person and comments on both sides, and an internal agent
  ("the player"), first person, which reads the friend's comment and chooses the move. Same model, two contexts.
  Measure wins, guess rate and accuracy, whether the self/other confusion disappears; compare with e1 alone at a
  similar token cost.

### Literature: detecting overthinking and deciding when to think with a second model (2026-10-30)
Search done to find what is missing around our ephemerality / thinking-frequency results. Relevant lines of work:
- **Overthinking score for agents** (The Danger of Overthinking, 2502.08235): Claude 3.5 Sonnet as judge, 0-10, on
  SWE-bench trajectories; "overthinking" = internal reasoning instead of interacting with the environment; validated
  on only 20 traces, no agreement coefficient. The definition does not transfer to games (a move is irreversible,
  there is nothing to "try").
- **Monitors that stop the reasoning**: Behavior Cue Reasoning (2605.07021) uses Qwen3-8B no-think as monitor that
  says continue/stop at answer-change points, tested on AIME and a TextWorld cooking game; requires fine-tuning the
  actor to emit cues. ROM (2603.22016), LYNX (2512.05325), TERMINATOR, EAT (2509.26522): probes or entropy on the
  actor's own hidden states, math only.
- **Routers that decide before generating**: Route to Reason (2505.19435, Qwen3-4B think vs no-think per query),
  Think When Needed (2601.18146), Sonata (Apple). All per query, none per move of a game.
- **Step-level depth in agents**: CogRouter (2602.12662, four depths per step, RL-trained, ALFWorld/ScienceWorld);
  HybridAgent (2610.18861); LaRMA (2503.11074: Act-only sometimes beats ReAct); Thinking vs Doing (2506.07976:
  more interaction beats more reasoning per step). Cooperative single-agent tasks, nothing adversarial.
- **Games**: entropy-guided adaptive CoT on Tic-Tac-Toe (2601.10775, own-token entropy, minimax as oracle); Game
  Reasoning Arena (reasoning categories per move, no cost analysis). No per-move thinking trigger in Connect Four
  or chess.
- **Memory of the reasoning across turns**: only vendor docs (Qwen preserve_thinking, Fireworks, MiniMax) with
  qualitative claims; no published keep / drop / summarize ablation. Our e1 / l1 / r4 / er4 / lr4 design is the
  only controlled one found.
- **"Wait" tokens**: NoWait (2506.08343), thinking trap (2506.23840), hedging density (2607.00482): reflection
  tokens as a signal of unproductive reasoning, math only. Our Connect Four reasonings have 17-22 "Wait" each.
Gaps for the thesis are listed in the answer of 2026-10-30 (summary: no per-move trigger from a second model in
adversarial games; no solver-grounded definition of overthinking in games; no surprise-triggered thinking from
failed opponent predictions; no "summarize" arm between keep and drop; no human-agreement study for an
overthinking judge).

## Part 3: when to think (November 2026)

### The gate: a second decision before each move, and the solver as oracle (2026-11-02)
**Question.** Our modes decide *when* to think with a clock (e1 every move, r4 about every 4), and thinking more often
won. Which moves deserve the thinking? Option 1 of the plan of 2026-10-30: a gate that decides before each move,
compared with the clock, with a solver as ground truth (the literature has per-query routers and RL-trained per-step
depth in cooperative tasks, nothing per move in an adversarial game, and no exact oracle).

**The oracle: c4solver.py.** Pascal Pons's Connect Four solver (C++, AGPL, with its 34 MB opening book; cloned,
compiled and downloaded by `python c4solver.py --setup`, also in the Colab setup cell), run as a subprocess shared by
all the games. For every position it gives the exact value of each column for the player to move (win / draw / loss,
and how soon). From 518 positions of our records: 0.9 s in total, worst 0.13 s, so it costs nothing next to a
reasoning. Definitions: a move is a **blunder** when its class (win / draw / loss) is worse than the best move's
(it changes the game-theoretic value); a position is **critical** when a single move keeps the value (n_safe = 1).
Invalid positions (a won game, a full column) get no answer from the solver, so they are checked before asking (the
first version waited forever on them).

**The gates (gate.py, modes g<gate>, lg<gate> keeps the last reasoning like l1):**
- `goracle`: the agent answers fast first (no thinking); if the solver says the fast move is a blunder or it is not
  legal, the agent thinks instead and the fast answer is discarded (its tokens count as the gate's cost). It thinks
  exactly when thinking could repair something: the ceiling of every gate.
- `gcrit<k>`: thinks when at most k moves (default 1) keep the value. The solver is the gate: no model tokens.
- `gself`: the same model, without thinking, reads the board in a context of its own and answers YES / NO to "does
  this position need careful thought?" (8 tokens at most). The gate one could use in practice.
- `grand<p>`: thinks with probability p%: at the firing rate of another gate, it controls for frequency alone.
  To run after the others, at their measured rates.
A gate mode never thinks on a clock. The e1 habit of not thinking on the very first move stays as it was.

**The shadow fast move (grid key shadow_fast_move).** On every thinking turn, every agent (the opponent too) first
answers without thinking; that answer is recorded and not played, and its tokens are kept apart (not a cost). For e1
and r4 this gives, move by move, what thinking changed: the fast move and the thought move, both judged by the
solver (repaired: fast blunder, final fine; spoiled: fast fine, final blunder). This is the solver-grounded
measurement of where thinking is wasted that option 2 (the overthinking rubric) will build on.

**Records.** Every move of an agent goes to `.moves.jsonl` next to the record (position: scores, best class, safe
moves; quality of the move; the gate's decision and reason; the fast move and its quality; tokens by kind: think,
normal, gate, shadow), and the record gets blunders, blunder_rate, think_moves, think_rate, fast_blunders, repaired,
spoiled (opponent_ versions in self-play). The transcript marks the blunders and the gate's decisions. The
token statistics get generated_gate / gate_turns / generated_shadow / shadow_turns; `generated` stays the cost of
playing (gate tokens included, shadow excluded). Tests: tests/test_gate.py (solver, gates, memory, shadow,
play_duel, run_duel end to end with the fake engine).

**Grid: grids/duel_c4_gate.yaml.** Qwen3-4B, Connect Four, modes goracle, gcrit, gself, e1, r4 (grand later), against
greedy and self-e1, plain and free, seeds 11-14 (new seeds so that e1 and r4 are replayed with the shadow fast
moves): 80 games. Analysis: `python gate_analysis.py <results>`: per mode the blunder rate on all / critical /
quiet moves and on the moves thought / not thought, what thinking changed, the gates' precision and recall against
"critical" and against "the fast move was a blunder", strength vs tokens (graphs/strength_vs_tokens.pdf,
blunders_by_mode.pdf).

**What the solver already says about the 116 old Connect Four games (judged from their histories, 1640 moves):**
- Blunder rate by mode: e1 27.6%, e1b 28.7%, r4 34.1%, lr4 41.1%, er4 42.0%. Thinking every move blunders less,
  as the scores said; about 1 move in 4 of the best mode still changes the game value.
- About a quarter of the positions are critical (one move keeps the value). There the model blunders in 61% (e1) to
  91% (er4) of the moves; on the quiet positions 16-24%. Nearly all the damage is on the critical moves: a gate that
  finds them is the whole question.
- In the every-4 modes, the moves that were thought are about as often blunders as the moves that were not
  (r4 33% vs 34%, er4 36% vs 43%, lr4 47% vs 40%). A reasoning on a random move repairs little; with e1 the
  difference is 26% vs 36% (few unthought moves). This is the first direct sign that *which* moves are thought
  matters more than how many, the premise of the gate.
- The every-4 modes think on 16-19% of the critical moves and as often on the quiet ones: the clock cannot tell them
  apart, as expected.
Note: the old e1 numbers mix prompts (CoT, neutral, free, plain) and opponents; the gate grid fixes them.

## 2026-11-03 — Results of the gate grid (duel_c4_gate.yaml, 80 games, Qwen3-4B, Connect Four, plain + free, seeds 11-14)

Analysis on the agent's own moves (`gate_analysis.move_table("results")`, the moves of the gate grid only). Fixed a bug of
`mode_summary` / `thinking_value` / `gate_quality`: `by="player_mode"` (a string) was iterated as letters.

| mode | score | vs greedy | vs self-e1 | think % | blunder % | critical | quiet | ktokens |
|---|---|---|---|---|---|---|---|---|
| e1 | 0.50 | 0/8 | 8/8 | 87 | 34 | 79 | 14 | 26.7 |
| goracle | 0.38 | 0/8 | 6/8 | 42 | 21 | 43 | 12 | 13.2 |
| gcrit | 0.25 | 0/8 | 4/8 | 28 | 31 | 45 | 25 | 6.1 |
| gself | 0.12 | 0/8 | 2/8 | 55 | 43 | 90 | 18 | 23.1 |
| r4 | 0.12 | 0/8 | 2/8 | 19 | 46 | 92 | 22 | 4.8 |

- **Nobody beats greedy** (0/40): one-ply lookahead is enough against Qwen3-4B in Connect Four whatever the mode. The
  strength signal comes from self-e1 and from the solver's blunder counts.
- **goracle** (think only when the fast move is a blunder, the ceiling of a gate) blunders least (21%, e1 34%) at half
  the tokens of e1. But when told its fast move is a blunder, thinking repairs it only 21 times out of 49 (43%): on the
  critical positions it still blunders 43% after thinking. The ceiling of gating is bounded by the quality of the
  thinking itself, not by the gate.
- **gcrit** (think when one move keeps the value, no model tokens) reaches e1's blunder rate on the critical moves
  (45%) with a quarter of the tokens; its blunder rate on the quiet moves (25%) is that of never thinking, as designed.
  It blunders 31% overall, better than r4 (46%) with similar cost. Illegal first answers 0.38 per game (others ~0).
- **gself** (the model asks itself YES/NO without thinking) is useless: it fires on 55% of the moves, *less* often on
  the critical ones (46%) than on the quiet ones (59%); precision for "critical" 28.6%, below the base rate (34%).
  Its blunder rate on critical moves (90%) equals r4's; it costs almost as much as e1 (23k tokens) and scores like r4.
  A no-think self-assessment of difficulty carries no information here; this is the main negative result of option 1.
- **Shadow fast moves of e1**: thinking changed the move 56% of the time; it repaired 12 blunders and spoiled 7 good
  fast moves out of 106 thinking turns. The net value of thinking per move is small (+5 moves in 106); the
  unrepaired blunders (25) dominate. r4: 1 repaired, 1 spoiled.
- **Critical vs quiet**: as in the old games, nearly all the damage is on the critical positions (79-92% blunder rate
  for e1, gself, r4 against 14-22% on the quiet ones). The fast answer on a quiet position is fine 75-88% of the time:
  thinking there is mostly wasted (e1 thinks on 93% of quiet moves).
- Oddity to check: e1 beat self-e1 8/8 (both colours), the old e1 vs self-e1 games were 3-4; the two agents are
  identical except the rng. Possibly chance (p=1/256), possibly an asymmetry introduced with the shadow fast moves
  (both sides have them) or in the order of the generations; worth re-checking before using the self-e1 scores.

Next: `grand<p>` at p = 28 (gcrit's rate), 42 (goracle's), 55 (gself's), to separate the frequency of thinking from
the choice of the moves; a gate that reads the board for threats (a cheap heuristic, no solver) between gcrit and
gself; more seeds (8 games per cell is thin for scores, fine for blunder rates: 105-141 moves per mode).

## 2026-11-03 — Part 4 (summarize arm) and Part 5 (surprise gate): built, to run

Both from the gap list of 2026-10-30 (gaps 3 and 4). Tests: tests/test_gate.py (test_summary_modes, test_predictions,
test_surprise_gate, test_play_duel_surprise); test_duel, test_profiles, test_legacy_equivalence still pass.

**Summarize (agent.py, modes s1 / ls1 / sr4 / lsr4).** The memory ablation had keep (1, l1, r4, lr4) and drop (e1,
er4) and nothing in between. After each reasoning the model, without thinking and in a context of its own (system
prompt + the reasoning + SUMMARY_QUESTION: at most three sentences, first person, plan / dangers / what it expects;
the board is never shown), writes a note that stands in the context in the place of the reasoning, wrapped as a
short `<think>…</think>`. `s` keeps every note, `ls` only the last (read during the next reasoning, then replaced,
as l1). Cost: SUMMARY_MAX_TOKENS = 160 per thinking turn (tokens.generated_summary, inside generated). Why this
design: the note is produced by the same model from its own reasoning, so the only variable against l1 is the
compression; and "1" (keep everything) cannot be played in Connect Four (context overflow), so s1 is the only way to
keep *all* the reasonings. Grid grids/duel_c4_summary.yaml: s1, ls1, l1, e1, sr4, lsr4 against greedy and self-e1,
plain and free, seeds 11-14 (96 games, 16 of e1 already played). Questions: does a summary carry what l1 gets from the
whole reasoning (l1 vs ls1), does keeping all the notes beat the last one (s1 vs ls1), does any memory beat none
(vs e1), and the same at every-4 (sr4 / lsr4 vs r4 / lr4 / er4 of the memory grid).

**Surprise gate (gate.py gsurp, prompt "predict").** The design discussed with the user: (1) the prompt asks the model
to say, after its move, which move it expects from the other player ("4 (expecting 3)"; duel_games.split_prediction
keeps the prediction out of the move parsing: without it "4, expecting 3" would have been played as 3, because
parse() reads the last word first); (2) after every answer a second context of the same model, without thinking,
reads what the agent wrote (the last 8000 characters of its reasoning when it thought, and its answer) and extracts
the expected move or NONE (EXTRACT_QUESTION, 12 tokens; the regex reading of the answer is stored next to it, to
measure their agreement); (3) before the next move the gate compares the expectation with the move actually played:
the agent thinks when surprised, or when it had no expectation; never on the first move of a game. After an illegal
answer the expectation does not change under the retry (pending / expected). Every move record has prediction,
prediction_regex and extracted; the record counts predictions and prediction_hits for every mode played with the
predict prompt (e1 and r4 too: the regex reading), so the prediction accuracy of the model is measured by itself.
Grid grids/duel_c4_surprise.yaml: gsurp, e1, r4, gcrit, grand35 with the predict prompt, against greedy and self-e1,
seeds 11-14 (40 games). Analysis: gate_analysis.surprise_quality(): predicted / hit / agreement rates, and the
blunder and critical rates when surprised vs as expected vs no expectation (is surprise a proxy for criticality?).
Caveats to keep in mind when reading it: the predict prompt names the other player (free does not), so its e1 is
not the e1 of the gate grid; a model that predicts badly is surprised often and gsurp degenerates towards e1 (the
firing rate tells); a prediction that comes true can still be a critical position (surprise is about the opponent's
plan, criticality about the board), which is exactly what the critical_when_expected column measures.

**Also.** grand28 / grand42 / grand55 added to duel_c4_gate.yaml (the firing rates of gcrit / goracle / gself):
48 more games. gate_analysis.mode_summary & co. fixed for by="<one column>".

## 2026-11-04 — The three grids read together: when_to_think.ipynb

The user ran the gate, summary and surprise grids with more seeds than planned (gate: seeds 1-15, 60 games per mode;
summary and surprise: seeds 1-25, 50 games per mode and opponent): 1832 Connect Four duel games, 12.7k judged moves
of the agent. `when_to_think.ipynb` (Code/Benchmark, runs locally on results/) has the tables, the Fisher tests, the
graphs and a verdict per experiment. What it says:

**A. Gate.** goracle (the oracle): blunder rate 23% vs e1 33% (p<0.05) at 40% of the tokens, score vs self-e1 0.63
vs 0.57. grand42 (random, same rate) is not distinguishable from goracle on score (0.53), only on blunders. gcrit:
score 0.43 at 30% of e1's tokens, within e1's interval. gself: precision for "critical" 22% against a base rate of
28%, below chance; it plays like r4 at e1's cost. Thinking repairs about 45% of the fast blunders it is pointed at
(goracle 78 of 179): the ceiling is the reasoning, not the gate. Against greedy: 22 wins in 635 games, all modes.

**B. Summary arm.** Blunder rates of e1 / l1 / ls1 / s1 identical (32-34%, same on critical: 80-82%), same for r4 /
lsr4 / sr4 (36-40%). On score vs self-e1 s1 (all the notes) is worse than e1 (0.28 vs 0.52, p=0.02); l1 and ls1 in
between, not distinguishable from e1. Third time the memory of the reasoning changes nothing; more memory is if
anything harmful (s1 also has more capped reasonings and illegal answers).

**C. Surprise.** Prediction hit rate 18-23% for every mode = chance (the most common reply is played 21% of the
time). gsurp fires on 74% of its moves: e1 with a few moves skipped. Faint signal: after a missed prediction the
position is critical 23% vs 17% (p=0.02), the next move a blunder 33% vs 28% (p=0.08); unusable as a gate since 4
replies out of 5 are "unexpected". Extractor and regex agree on 81% of the moves.

**D. Across.** (1) greedy unbeaten; (2) 27% of positions critical, fast answer wrong there 86%, thought answer 67%;
quiet positions: 18% vs 17%; (3) a selection effect we nearly reported as a finding: on thought critical moves the
gate modes blunder ~50% and the clock modes ~75%, but only because the gates think in the opening (56% of gcrit's
thought critical moves are at ply <= 6, where critical = "play the centre" and thinking gets it right half the
time); from ply 7 on every family blunders 70-80% of its thought critical moves (p=0.14 / 0.17). When the position
is hard, the reasoning fails three times out of four whoever decides that it should reason.

## 2026-11-05 - The oracle read closely, and a board-format test

**Questions asked about the gate results, and what the data says.**
- goracle is a ceiling, not a method: the solver filters the fast answers, so every move played without thinking is
  right by construction (250 of 450 moves, 0 blunders) and all its blunders are thought moves (104 of 200). It beats
  e1 on blunders also because in e1 the thinking spoils about 10% of the fast answers that were right (48 of 476).
  The thinking turn sees nothing of the solver nor the rejected fast answer.
- e1 against self-e1 "wins 57%": noise. Over all the mirror games it is 68 of 125 (0.54, binomial p=0.37). Use it as
  the yardstick: at 30-60 games a score gap under ~0.15 means nothing. The second player won 75 of 125 (p=0.03):
  colours alternate with the seed, so no bias, and too weak to build on.
- grand42 is not special: the scores of the random gates are not monotonic (grand28 0.33, grand35 0.60, grand42
  0.53, grand55 0.40 at 25-30 games). The blunder rate per move is the steadier metric (random gates 27-35%, e1
  26%, goracle 20%). To claim "random 40% = e1 at 40% of the tokens" it takes ~100 seeds per mode.
- Open: thinking repaired 48% of the fast blunders in the goracle games and 26% in e1's (53 of 201). Different
  prompts and positions; to check before quoting the 45%.

**Why a board-format test.** The model always gets the whole board (row grid, last move, playable columns), also
on the thinking turns. Yet in the reasoning of goracle / greedy / seed 14, ply 8, it copies the grid right and then
reads the discs of column 4 as column 3. If the failures of the thinking on the hard positions are misreadings of
the row grid, a text where a column is one line should raise the ceiling; if they are not, the ceiling is the
reasoning itself. One example is not a rate: this test measures it.

**What changed.** `duel_games.ConnectFour` has the option `board`: "grid" (default, unchanged), "columns" (one line
per column, discs from the bottom up, "(full)" when full) or "both". `gate_analysis.move_table` has the column
`board`. Tests: `tests/test_gate.py` (test_board_formats, test_board_prompts).

**How the board is chosen: the prompt name (second design).** First the board was a game option of the grid, with
one grid and one results folder per board. It broke the rule that a game is never played twice: the path of a
record (model / game / prompt / mode / opponent-seed) has no game options, so the first launch found "0 games to
play" (the gate games in results/ have the same path), and the fix (own folder, no read folders) depended on
remembering it at every launch. Now the board is a suffix of the prompt, "plain+columns" / "plain+both"
(`run_duel.split_prompt`): the agent still gets the plain prompt, the game gets the board, and the record and its
path say "plain+columns". So the games live in the normal results folder, are skipped when relaunched, and every
table that groups by prompt separates them without changes. The random choices (r4's thinking turns) are seeded
with the base prompt: the same as in the baseline games. Unknown prompts or boards fail when the grid is loaded.
One grid: `grids/duel_c4_board.yaml`, goracle / e1 / r4, plain+both and plain+columns, greedy and self-e1, seeds
1-15 (180 games); baseline: prompt plain in the gate grid.
**Caveats.** "columns" alone may make rows and diagonals harder to see, which is why "both" is there. In self-e1
the opponent reads the new board too, so read the blunder rates (per move) before the score. Not run yet (GPU).

## 2026-11-05 - Board-format grid, first read (147 of 180 games)

Seeds 1-15, greedy and self-e1, against the plain (row grid) games of the same modes and seeds. Per move, any
player of the model by its own mode (so e1 includes the self-e1 opponents). Missing: 33 games, mostly self-e1.

| | plain | plain+both | plain+columns |
|---|---|---|---|
| e1: fast answer a blunder | 34% (155/458) | 35% (136/389) | 33% (122/365) |
| e1: thought move a blunder | 29% (132/459) | 31% (122/392) | 31% (112/366) |
| e1: fast blunder repaired by thinking | 29% (45/155) | 21% (28/136) | 25% (31/122) |
| e1: thought, critical, ply >= 7, blunder | 71% (60/84) | 78% (46/59) | 65% (40/62) |
| goracle: gate fired (= fast answer wrong) | 47% (99/210) | 45% (106/234) | 38% (83/219), p=0.06 |
| goracle: fast blunder repaired | 42% (39/92) | 43% (43/100) | 51% (41/81), p=0.29 |
| goracle: blunder, all moves | 26% (54/210) | 24% (57/234) | 18% (40/219), p=0.08 |
| r4: blunder, all moves | 40% | 39% | 39% |
| think tokens per thought move, e1 | 4263 | 5037 | 4200 |

**Reading.** The board text does not change what the model sees or what its thinking repairs: no comparison is
significant, and e1, the largest sample (about 400 thought moves per board), is flat on every line, hard positions
included. The only hints are goracle with columns (fewer fast blunders, fewer blunders overall, p=0.06-0.08), not
confirmed by e1's fast answers on the same board (33% vs 34%) and found among about thirty comparisons: to recheck
when the grid is complete, not to report. "both" costs about 20% more thinking tokens for nothing. So the misread
grid of the example is not the bottleneck, or the model misreads the columns as well: the ceiling stays with the
reasoning. Scores per game are too few to read (9-15 games per cell).

## 2026-11-05 - when_to_think.ipynb: the surprise gate in figure A1

Figure A1 (score, blunder rate, cost of the gate modes) now has the bar of gsurp, hatched and starred: it plays with
the predict prompt and on 25 seeds, the other bars with plain + free on 15, so it is there for comparison, not as a
like-for-like test. It thinks on 72% of its moves, costs 22k tokens per game against e1's 30k, and against e1 it is
not distinguishable on score (0.36 vs 0.57, p=0.18) nor on blunders (32% vs 33%, p=0.82). The notebook now leaves
out the games of the board grid (prompts with "+"), which are read apart, so its other numbers did not change.

## 2026-11-05 - when_to_think.ipynb: the words explained

New section after the introduction, "The words used": blunder (class of the move worse than the best class; a slower
win is not one; none possible in a lost position), critical position (exactly one column keeps the value), quiet
position (all the others), with a worked example (goracle vs greedy, seed 14, plies 8 and 10) and thought / fast /
shadow / repaired / spoiled. Base rates quoted there: 26% of the positions critical (85% won, 15% drawn), 30% lost,
37% where no blunder is possible, blunder rate 18% on quiet and 76% on critical positions (all modes, 20193 moves).
Why: the figures use these words everywhere and they were defined only in one line of the introduction.

## 2026-11-09 - Why lr4 works in the cooking games and not in Connect Four (why_lr4.ipynb)

**Question.** In cooking lr4 plays about like e1 for a fifth of the tokens; in Connect Four it plays like the other
every-4 modes, far below e1. Read the reasonings of both games to see why, and classify them.

**What was built.** `memory_analysis.py`: a parser of the textual-game logs (turns, reasonings, score before and
after, commands the game did not understand), the same turn table for Connect Four from the solver's move table,
`since` (turns since the last reasoning) and `visible` (reasonings in the context) for every turn, and a fixed
blind sample of reasonings with what followed them. `why_lr4.ipynb`: four figures and a verdict. Test:
`tests/test_memory_analysis.py`. The 4B cooking games are those of Experiments 7, 7e and 8 (logs in
Code/Experiments); the 1.7B and 8B ones are the benchmark's.

**1. The contrast is three effects, and it depends on the model** (cooking: matched levels and seeds, Wilcoxon).

| | cooking 1.7B | cooking 4B | cooking 8B | Connect Four 4B |
|---|---|---|---|---|
| e1 / er4 / lr4 / r4 | 0.21 / 0.04 / 0.06 / 0.05 | 0.47 / 0.40 / 0.42 / 0.25 | 0.55 / 0.41 / 0.58 / 0.48 | 0.61 / 0.34 / 0.38 / 0.43 |
| thinking less often (er4 - e1) | -83% | -16% (p=0.008) | -24% (p=0.011) | -44% (p<0.001) |
| keeping the last (lr4 - er4) | +0.02 (p=0.20) | +0.02 (p=0.28) | +0.17 (p=0.008) | +0.03 (p=0.85) |
| keeping all (r4 - lr4) | 0.00 | -0.17 (p<0.001) | -0.10 (p=0.11) | +0.05 (p=0.56) |

So: thinking every fourth turn costs much more in Connect Four; the last reasoning helps only the 8B model; with
the 4B, the model of Connect Four, lr4 = er4 in cooking too, and lr4 looks good there because r4 collapses.
**The two games were not played with the same models**: the memory effect is an 8B result, untested in Connect Four.

**2. What there is to remember.** Connect Four: about 8 moves per player per game, 1.3-1.6 reasonings in the
every-4 modes, a reasoning in view on 24-30% of the moves, more than half of the moves before the first reasoning;
"last" and "all" are the same thing. Cooking: 45-90 turns, 10-23 reasonings, 64-71% of lr4's turns with a reasoning
in view. **Empty reasonings** (the thinking block opened and closed at once), found by reading the logs: in the
every-4 modes 77-93% of the thinking turns with the 1.7B (e1: 8%), about 20% with the 4B (3% in the first five
thinking turns, 25-30% later), 2-5% with the 8B, never in Connect Four. The every-4 modes of the 1.7B almost never
reason: this is why only e1 plays. Likely cause (not tested): the turns without thinking fill the context with
bare answers and the model continues the pattern.

**3. The turns after a reasoning** (turns 1-3, reasoning visible against erased). Cooking 8B: a point on 16% of the
turns with lr4, 12% with er4 (paired by game, p=0.03). Cooking 4B: 8% vs 6% (p=0.12). In cooking the turn right
after a reasoning scores more than the fourth also when the reasoning is erased: the action it chose changed the
game. Connect Four: the next moves replay the column of the reasoning 42% of the time when it is visible (lr4 and
r4), 20% when erased (p<0.001; lr4 alone 35%, p=0.04); blunders 32% vs 36% (p=0.45). A first version of this
analysis used "the command is announced in the last reasoning": pooled over turns it separated lr4 from er4
(82% vs 73%), paired by game it did not (p=0.48), so it was dropped.

**4. 180 reasonings classified** (40 lr4 + 20 er4 for each of cooking 4B, cooking 8B, Connect Four 4B; the reader
does not see the mode; rubric in memory_analysis.RUBRIC; labels by Claude, six readers of 30 items; 30 items read
twice: kappa 0.85 type, 0.72 future steps, 0.90 fallbacks, 1.00 durable facts, 0.70 state error, 0.80 carried,
0.73 validity).

| | cooking 8B | cooking 4B | Connect Four 4B |
|---|---|---|---|
| type: PLAN / TRACK / REPAIR / EVALUATE / LOST | 10 / 12 / 52 / 0 / 27 % | 13 / 7 / 47 / 0 / 33 % | 0 / 0 / 0 / 60 / 40 % |
| states durable facts | 100% | 100% | 0% |
| names a later step of its own | 47% | 28% | 17% |
| names fallbacks | 33% | 28% | 0% |
| misreads the state (checkable) | 10% | 25% | 53% |
| conclusion VALID / STALE / WRONG | 40 / 0 / 60 % | 33 / 0 / 67 % | 28 / 13 / 58 % |
| next turns FOLLOWED it, visible vs erased | 78% vs 60% | 52% vs 50% | 15% vs 10% |
| next turns REPEATED its action, visible vs erased | 10% vs 10% | 32% vs 30% | 38% vs 15% |

In cooking a VALID reasoning is followed by a point 62% of the time (8B) and 30% (4B), a WRONG one 6% and 2%; in
Connect Four what follows does not depend on the validity. The visible-vs-erased differences of the labels are not
significant on 40 vs 20 items (p 0.08-1): part 3 is the test.

**Verdict.** (1) Connect Four needs a fresh look at every move, cooking does not: most of the gap is the frequency.
(2) This Connect Four design gives the memory one or two reasonings per game: a limit of the experiment. (3) A
cooking reasoning is a note on a task that does not move (what is done, what failed, what else to try); a Connect
Four reasoning evaluates one position, misreading it half of the time, and says nothing to a later turn: the only
thing the next move takes from it is the column. (4) The benefit of the last reasoning in cooking is the 8B's.

**Open.** Games and models are confounded. The labels are a judge's: a human check on a subset is to do
(labels/memory_sample.jsonl). **Next tests that would settle it:** Connect Four with the 8B (e1 / er4 / lr4 / r4);
a longer two-player game or a larger board (ten reasonings per game); a prompt asking the Connect Four reasoning to
end with a note for the next moves; a minimum of thinking tokens to forbid the empty reasonings, then the every-4
modes of cooking again with the 1.7B and 4B.

## 2026-11-09 - Three grids for the open questions of why_lr4.ipynb

Of the four tests proposed, three are grids now; the fourth (a longer game or a larger board) needs a game of its
own, without the solver, and is not done. Not run yet (GPU).

**1. `grids/duel_c4_8b.yaml`: Connect Four with Qwen3-8B.** e1, er4, lr4, r4, and er2 / lr2, plain prompt, against
self-e1, seeds 1-20 (120 games). *Why:* the benefit of the last reasoning in cooking is an 8B result (+0.17) and
Connect Four was only played with the 4B: games and models were confounded. *Why every 2 too:* at every 4 a game
has 1-2 reasonings and one in view on a quarter of the moves; at every 2 about four. Read lr against er at the same
frequency, and er4 against e1 (the cost of thinking less often).

**2. `grids/duel_c4_note.yaml`: a reasoning written to be read again.** New prompt `note` (duel_prompts.py, both
games): plain, plus on the thinking turns "end your thinking with a short note to yourself for your next moves,
starting with NOTE FOR MY NEXT MOVES". lr4 / lr2 keep it, er4 / er2 erase it (the control); prompts note and plain,
Qwen3-4B, self-e1, seeds 1-20 (160 games, 140 to play). *Why:* the plain reasonings say nothing to a later move
(0 of 60 with a durable fact); the only thing the next move took from them was the column. Read (lr - er) with
note against (lr - er) with plain, and the share of reasonings that actually contain the note. The note is inside
the reasoning, so the modes that drop the reasoning drop it too.

**3. `grids/cooking_nonempty.yaml`: the every-4 modes without the empty reasonings.** er4, lr4, r4 with the prompt
`CoT+nonempty`, Qwen3-1.7B and 4B, cooking levels 1, 3, 5, 7, 9, seeds 1-10 (300 games); baseline: the CoT games
already played. *What changed in agent.py:* a thinking turn is empty when the thinking closes within 4 tokens; it
is now counted in every mode (`empty_reasonings` in the token statistics of the record); with the prompt option
`+nonempty` it is generated again, up to 2 times, with the thinking block opened and its first words written
("Okay, let's see."). `nonempty_retries` counts the generations spent. As for the board, the option is part of the
prompt name, so of the record path: these games are never taken for the CoT ones. *Why a retry with a prefill and
not a minimum of tokens:* a minimum of tokens only forbids the end of the answer, not the end of the thinking; the
model would close the thinking and then be forced to write after its command. *Limit:* the nudge puts words in the
model's mouth; the reasonings that needed it are counted per game (`nonempty_retries`), but they cannot be told apart by
their text: the model's own reasonings often start with the same words (corrected 2026-11-10).

Tests: `tests/test_gate.py` (test_nonempty, test_note_prompt). All suites pass.

### run_remote.py plays textual grids too (2026-11-09)

`run_remote.py` loaded every grid with `load_duel_grid`, so `grids/cooking_nonempty.yaml` failed with
`KeyError: 'cooking'` (cooking is not a two-player game). *Change:* the script now looks at the games of the grid:
two-player games (those of `duel_prompts.DUEL_PROMPTS`) go to `run_duel_grid`, textual games to `run_grid`; a grid
that mixes the two kinds is refused. *Why here and not in the loader:* the two runners save different records and
take different options, so the choice belongs to the script that launches them. Same folders as before
(`results`, read `results`), so the games already played are still skipped. Checked on all 17 grids of `grids/`.

### Results of the three grids and of the board format (2026-11-10)

Read from `results/` after the runs of 2026-11-09. The Connect Four grid with the 8B model (`duel_c4_8b.yaml`) is
not played yet.

**Cooking without empty reasonings (`cooking_nonempty.yaml`, 300 games).** The retry works: every empty reasoning
was replaced at the first retry (`nonempty_retries` = `empty_reasonings` in all games), and the reasonings written
have the usual length (1.7B: median 1500 characters, 1941 with e1).
- *1.7B* (baseline: the same benchmark, same levels and seeds). Thinking turns with a reasoning: 7% -> 99% (er4),
  8% -> 99% (lr4). Score: er4 0.035 -> 0.120 (p=0.003, Wilcoxon paired by game), lr4 0.056 -> 0.165 (p=0.002),
  r4 0.053 -> 0.054 (p=0.89). lr4 is then not distinguishable from e1 (0.207, p=0.25) with a quarter of the thinking
  tokens (10.6k against 42.9k per game). So "only e1 works with the 1.7B" was for a large part an artefact: in the
  every-4 modes the model did not reason at all.
- *4B* (baseline: the legacy experiments 7, 7e and 8, another pipeline: a weaker comparison). er4 0.346 -> 0.403
  (p=0.12), lr4 0.425 -> 0.426 (p=0.93), r4 0.237 -> 0.339 (p=0.054).
- *Memory.* lr4 - er4: +0.045 (1.7B, p=0.06), +0.023 (4B, p=0.61), +0.034 pooled (p=0.13, 100 pairs): a small
  effect, not established. r4 is the worst: r4 - er4 = -0.065 (p=0.015), r4 - lr4 = -0.099 (p<0.001). Keeping every
  reasoning hurts, at both sizes.
- *A new failure of r4 at 1.7B.* In 18% of its thinking turns the model writes a reasoning and stops without closing
  it and without a command; the text of the reasoning goes to the game as the command. Under 0.2% in er4 and lr4.

**The note prompt (`duel_c4_note.yaml`, 160 games): the test did not test what it was written for.** Two flaws,
found in the logs:
1. The model writes the note after closing its thinking, in the answer (62% of the thinking turns; inside the
   thinking only 5%; no note 33%). The answer stays in the context in every mode, so the erased modes keep the note
   too. lr against er is no longer "note kept against note erased".
2. The opponent (self-e1) plays with the note prompt too, so note against plain also changes the opponent.
What the numbers say, with 20 games per cell: lr - er with the note -0.05 (p=0.62), without +0.15 (p=0.23); no
difference is established. The blunder rate of the erased modes is lower with the note (0.31 against 0.43, Fisher
p=0.003), but the moves played before any note is in the context are already lower (0.34 against 0.45), so it is
not the note being read. One result repeats: with the plain prompt the model replays the column of its last
reasoning more often when the reasoning is visible (36% against 16%, p=0.002; 42% against 20% in why_lr4.ipynb).
*To do before running it again:* move the note from the answer into the reasoning (so that er erases it and lr
keeps it), and leave the opponent on the plain prompt.

**Board format (`duel_c4_board.yaml`, complete, 180 games): no effect.** Score: no difference in any mode
(all p > 0.1). e1 and r4: blunder rates unchanged. goracle with the columns: blunder rate 0.20 against 0.28 (p=0.03
paired by game, p=0.11 on the moves), gate fired on 38% against 47% of the moves (p=0.07), but the score does not
follow (0.33 against 0.40), "both" (which contains the columns) shows nothing, and the fast moves of e1 go the other
way (36% against 33% blunders). One p=0.03 among about ten comparisons: taken as chance. The repair rate of the
thinking is the same in the three formats (about 50%). The way the board is written is not what limits the model.

### NO_FP8_KV_CACHE: the 8B without FlashInfer, for the remote 4090 (2026-11-10)

On the remote machine (RTX 4090, driver 535 = CUDA 12.2, no root, no nvcc) Qwen3-8B-FP8 does not start. Its
`kv_cache_dtype="fp8"` makes vLLM use FlashInfer for the attention; FlashInfer compiles its kernels at the first use
(no nvcc: "Could not find nvcc"), and its precompiled kernels (`flashinfer-jit-cache`, built with CUDA 12.9) are
refused by the old driver ("device kernel image is invalid"). The 1.7B and 4B use vLLM's own kernels and run.
*Change:* `NO_FP8_KV_CACHE=1` in the environment makes `profiles.model_settings` drop `kv_cache_dtype`, so the
cache stays in bf16 and FlashInfer is not used. *Cost:* half the context fits; on 24 GB the 8B still holds tens of
thousands of tokens, enough for Connect Four (prompts of ~3k tokens), tight for cooking. *Traceability:* the record
saves the vLLM settings, so these games have no `kv_cache_dtype` and can be told apart from the fp8 ones (the 8B
cooking games). An fp8 cache rounds the stored keys and values: the two settings are not bit-identical, but the
grid `duel_c4_8b.yaml` compares its modes with each other, all played the same way.
Test: `tests/test_profiles.py` (test_no_fp8_kv_cache).

### Connect Four with the 8B (`duel_c4_8b.yaml`, 120 games): results (2026-11-10)

Played on the remote 4090 with `NO_FP8_KV_CACHE=1` (bf16 cache; the records have no `kv_cache_dtype`). Against
self-e1, seeds 1-20, plain prompt; compared with the same modes and seeds of the 4B.

| mode | 8B score | 8B blunder rate | 4B score | 4B blunder rate |
|---|---|---|---|---|
| e1 | 0.50 | 0.17 | 0.65 | 0.32 |
| er4 | 0.40 | 0.09 | 0.25 | 0.46 |
| lr4 | 0.45 | 0.09 | 0.35 | 0.39 |
| r4 | 0.35 | 0.10 | 0.20 | 0.42 |
| er2 | 0.45 | 0.09 | 0.20 | 0.42 |
| lr2 | 0.40 | 0.15 | 0.40 | 0.34 |

- *No memory effect with the 8B.* lr - er: +0.05 (every 4), -0.05 (every 2), 0.00 over both (p=1.0, Wilcoxon paired
  by seed); blunder rate +0.03 (p=0.43). On the moves after a reasoning: 31% blunders with the reasoning visible, 29%
  erased (p=0.83). The model that gains +0.17 from lr4 in cooking gains nothing in Connect Four: the difference
  between the two games is not the size of the model. Limit: 20 games per cell, and 0.6-0.75 reasonings per game in
  the every-4 modes (the games last 12 plies).
- *Thinking does not help the 8B here.* On the 384 thinking moves, the fast move (shadow) is a blunder 91 times, the
  move played after thinking 88 times: 27 repaired, 24 spoiled (McNemar p=0.78). With the 4B: 137 -> 117, 39 repaired,
  19 spoiled (p=0.012). So with the 8B there is no benefit of the reasoning for the memory to carry.
- *The modes that think less are not worse than e1 with the 8B* (0.35-0.45 against 0.50, all p > 0.4), while with
  the 4B they are (er4 - e1 = -0.40, p=0.02). The 8B plays its moves without thinking well: 7% blunders on the normal
  moves before any reasoning, 49% for the 4B.
- The 4B result "the model replays the column of its last reasoning when it sees it" (36% against 16%) is not there
  with the 8B (60% against 52%, p=0.44): it replays the column in both cases.
- Missing: mode 0 for the 8B, the direct test of "the 8B does not need to think in Connect Four".

### Mode 0 added to `duel_c4_8b.yaml` (2026-11-10)

The 8B games showed that thinking repairs as many moves as it spoils (27 against 24) and that the modes that think
rarely are not worse than e1. Mode `0` (never thinking) against the same self-e1 opponent is the direct test of
"the 8B does not need to think in Connect Four". Added to the existing grid and not to a new one: same opponent,
seeds and settings, and the 120 games already played are skipped. 20 games to play.

### Mode 0 of the 8B in Connect Four, and a correction: the blunder measure misses the one-move tactics (2026-11-11)

**Mode 0 (20 games against self-e1).** 5 wins, 15 losses: 0.25 (95% interval 0.09-0.49; against 50%, p=0.041).
The games are degenerate. As second player the agent loses 8 games of 10 in 7 plies: the opponent stacks four discs
in column 4 while the agent plays 1, 1, 1. As first player it stacks column 4 itself and wins 5 games of 10 in
7 plies because the thinking opponent does not block. Its blunder rate is low (0.10 per game) only because of how
a blunder is defined (below). All six modes that think less than e1 together win 46 games of 120 against e1
(0.38, p=0.013), so "thinking less costs the 8B nothing" (entry of 2026-11-10) was wrong: no single mode was
significant with 20 games, the six together are.

**The correction.** A blunder is a move that changes the solver's class of the position (win/draw/loss). A position
that is already lost for a perfect opponent cannot be blundered, and the second player is lost from the first move.
Of the single four-in-a-row threats left unblocked, the solver counted as blunders 32 of 139 (8B) and 41 of 115 (4B):
the others were in positions already lost. So the blunder rate does not see most of what decides these games.
New measure, from the moves alone (no solver): when the player has a win in one, does it play it; when the other
side has exactly one win in one (and the player has none), does it block it.

| | thought | not thought | same position: fast move -> move after thinking |
|---|---|---|---|
| 8B, threat blocked | 51/127 = 0.40 | 2/65 = 0.03 | 11/127 -> 51/127 (40 fixed, 0 lost, McNemar p<0.0001) |
| 8B, win taken | 114/139 = 0.82 | 26/42 = 0.62 | 91/138 -> 113/138 (34 fixed, 12 lost, p=0.002) |
| 4B, threat blocked | 40/103 = 0.39 | 3/55 = 0.05 | 13/94 -> 36/94 (28 fixed, 5 lost, p=0.0001) |
| 4B, win taken | 99/125 = 0.79 | 13/21 = 0.62 | 63/104 -> 83/104 (27 fixed, 7 lost, p=0.0008) |

(All players of the plain games against self-e1, seeds 1-20, all modes; the last column uses the shadow fast move.)
- *Thinking does help the 8B*, exactly as much as the 4B, on the moves that decide the game. The entry of
  2026-11-10 ("27 repaired, 24 spoiled: thinking does not help the 8B") measured the wrong thing and is withdrawn.
- *Without thinking a threat is almost never blocked* (3-5%), at both sizes. With thinking, 40%: still a majority
  missed, also for three discs stacked in a column (8B: 32 of 88 blocked).
- *Memory.* On the normal moves of the every-N modes the threats blocked are 2 of 65 (8B) and 3 of 55 (4B),
  whatever the mode keeps: a reasoning in view does not make the next move see a threat. The "no memory effect in
  Connect Four" stands, with this measure too.
- *Board format, rechecked with this measure:* threats blocked when thinking 44% (grid), 39% (columns), 35% (both),
  all p > 0.3. Still no effect.
- *What it touches.* The solver-based numbers of when_to_think.ipynb and why_lr4.ipynb (blunders, repaired/spoiled,
  the oracle gate, which fires only on a solver blunder) undercount the errors in lost positions. Not recomputed yet.
- *An idea it opens.* The one-move tactics are 23% of the moves and can be detected by a few lines of code: a gate
  that thinks exactly there ("gtactic") is a cheap and legitimate candidate, between the random gates and the oracle.

### Two gates that think on an event, not on a clock: gtactic and gfail (2026-11-11)

**Why.** The previous thesis (Dimaglie, "Don't Overthink It") motivates n-think with an uneven need for reasoning
during a game, but never measures it, and leaves "adaptive n-think" and "ask-to-think" as future work. Our gates so
far: the model cannot gate itself (gself, gsurp), and the gates that work read the solver (goracle, gcrit), which
no real use has. The measures of this week show where the need is, with a signal that costs nothing:
- Connect Four: a threat is blocked 3-5% of the time without thinking, 40% with it; these positions are 23% of the
  moves.
- Cooking (er4 + lr4 games, levels 1-9, seeds 1-10; new measure): after a command the game refused, a thinking turn
  scores 16.1% of the time against 6.6% for a normal turn (8B), 6.3% against 2.0% (4B), 2.0% against 0.9% (1.7B),
  all p <= 0.001; after an accepted command 9.9% against 7.5% (8B, p=0.054), 9.4% against 7.3% (4B, p=0.11),
  4.8% against 1.7% (1.7B). Refused commands are 37% (8B), 63% (4B), 75% (1.7B) of the turns.
  Other signals tried on the same games (the same command repeated, no point for 8 turns) do not separate the turns
  where thinking helps more: thinking helps as much or more outside them. The refusal was kept.

**What was added.**
- `gate.TacticGate` (mode `gtactic` / `lgtactic`): thinks when `ConnectFour._winning_moves` finds a win in one
  for either side. No solver, no tokens. It only switches the thinking on: the model is not told what the rule saw
  (telling it would test the hint, not the timing).
- `gate.FailGate` (mode `gfail` / `lgfail`): thinks when the reply of a textual game matches `REJECTED` (moved from
  memory_analysis.py to gate.py: the gate and the analysis read the logs with the same pattern). Never on the first
  turn. `SelfEvalAgent` now makes the gates that need no board (`gfail`, `grand<p>`), so the textual games have gate
  modes; `run.check_modes` refuses the others before the model is loaded.
- `tokens.selfevaluations` in every record: the thinking turns (think_turns also counts the retries of `+nonempty`).
- `gate_analysis.one_move_tactics / tactics_summary` and the columns win_in_one, took_win, threat, blocked,
  fast_took_win, fast_blocked of `move_table`: the measure of 2026-11-11 as part of the analysis (it reproduces the
  numbers of that entry: 8B 51/127 blocked when thinking, 2/65 when not).
- Grids: `duel_c4_tactic.yaml` (4B and 8B; gtactic, lgtactic, grand28, e1, 0 against self-e1; 50 seeds, 420 games to
  play) and `cooking_fail.yaml` (1.7B, 4B, 8B; gfail, lgfail, grand25, grand50; CoT+nonempty; 600 games).
  *Choices:* 50 seeds because gaps under 0.15 were noise at 20-30 games; only self-e1 because nobody beats the greedy
  bot; random gates as the control for the thinking frequency; `CoT+nonempty` so that a thinking turn of a gate
  always has a reasoning.
- *What would count as a result:* gtactic above grand28 at about the same tokens, and near e1 at a fraction of them;
  gfail above the line drawn by 0, er4, grand25, grand50 and e1 in score against thinking tokens.
- *Limits known in advance:* both rules are specific to their game (the general gate would be the model itself,
  which failed); gfail's rate is high for the weak models, so for them it may just be e1 with some turns skipped;
  the 8B gates in cooking use CoT+nonempty and its baselines CoT (2-5% empty reasonings).
Tests: `tests/test_gate.py` (test_tactic_gate, test_fail_gate). All suites pass.

### Three Colab notebooks for the event gates (2026-11-11)

`colab_c4_tactic_4b.ipynb`, `colab_c4_tactic_8b.ipynb`, `colab_cooking_fail.ipynb`: one per run, in the order to
run them, built from colab_duel.ipynb / colab.ipynb with the grid and the models fixed. *Skipping:* a game is skipped
when its record is on Drive or in the git clone's `results/` (`read = [LEGACY_RESULTS]`, as the old notebooks
already did; the setup cell pulls first, so the clone has every result pushed from the remote machine). New:
`run.played_by_folder` prints, before running, how many games of the grid each folder holds and how many only it
holds, so the check is visible. *The 8B notebook* sets `NO_FP8_KV_CACHE=1`: the 8B Connect Four games already played
(e1, 0) used the bf16 cache on the remote machine, and the new modes are compared with them. The cooking notebook
keeps the fp8 cache, as the 8B cooking games already played. Checked locally on the cells that need no GPU (with an
empty folder in place of Drive): 40 of 250 games found in git for each Connect Four notebook, 0 of 600 for cooking.

### Parallel games: the budget counted 2-4 times what vLLM holds (2026-11-11)

Seen on the first runs of the event gates: Connect Four 3 games at a time (4B on an L4, 8B on the 4090), cooking
1.7B 10 at a time, the status line at 76-86% "of the KV cache". *Cause* (engine.ContextBudget): a game counted for
its whole final context, a two-player game for both players' contexts added, and every group (mode, opponent)
started from a prior of 20000 tokens until 3 of its own games ended (5 groups in duel_c4_tactic.yaml: hours of
warm-up at 5-15 games/hour). But vLLM holds in the KV cache only the requests being generated: between two answers a
game's blocks are free, and in a duel the two players answer one at a time.
*Change:* a game counts for the average size of its requests over the time spent generating them
(`agent.kv_occupancy()`: a request of g tokens on a prompt of p lasts g steps and holds p + g/2 on average; the
long reasonings dominate), or for its last request if larger; a duel for the larger of its two players
(`duel.Contestants`); a group with fewer than 3 ended games uses the games of all the groups; prior 12000. Still 75th
percentile and 90% of the cache. *Risk:* the sizes fluctuate around the averages; when the cache is full for a moment
vLLM preempts a request and recomputes it later: slower, never a wrong result. The new games are identical to the old
ones (the budget only decides when a game starts). The status line now says "expected X% of the KV cache in use".
Tests: tests/test_pipeline.py (test_context_budget, test_auto_parallel). test_run_grid fails, before and after this
change: it assumes the 1.7B cooking games of seeds 1-3 are not in results/, and they now are.
