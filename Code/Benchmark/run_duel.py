"""Plays the two-player games of a grid (grids/duel_*.yaml) that have not been played yet, one model at a time.
The same of run.py, for duel.py: the levels of a game are its opponents.

    python run_duel.py grids/duel_tier1.yaml --dry-run
    python run_duel.py grids/duel_tier1.yaml --model Qwen/Qwen3-1.7B --limit 8   # a pilot

The records are in the same results folders and with the same path of the textual games
(<results>/<model>/<game>/<prompt>/<mode>/level<opponent>-seed<S>.json), so records.load_table() reads both.
Extra fields of a duel record: opponent, color, result ("win", "draw", "loss"), termination, moves (legal moves of
the agent), illegal (its illegal answers), forced (random moves played for it), plies, history (all the moves),
final_state, opponent_counts and opponent_tokens (self-play), and for chess pgn, material, opening.
Next to the record and its log (and, in self-play, the opponent's log .opponent.log):
    .transcript.md: the game move by move, with the reasonings of both agents where they reasoned, to read side by side;
    .thoughts.jsonl: the same reasonings, one line per move (ply, side, color, player, move, forced, illegal,
        reasonings: [{reasoning, answer}]), for an automatic analysis.

Grid keys besides those of run.py: max_steps (moves of the agent before a draw), max_illegal, illegal_policy
("random" or "forfeit"), colors ("alternate": first player on the odd seeds, second on the even ones; "first";
"second"), game_options (per game, the options of duel_games: e.g. chess: {board: fen, legal_moves: false}),
prompts ("<prompt>" of duel_prompts, or "<prompt>+<board>": the same prompt with that board text, e.g.
"plain+columns" in Connect Four; a prompt of its own in the records and their paths, see split_prompt()),
adjudicate ("stockfish": the chess games stopped at max_steps are won by the side ahead by adjudicate_cp centipawns
in Stockfish's evaluation of the final position, see duel.py; the other games are not affected),
shadow_fast_move (true: on every thinking turn the agents also answer without thinking first, a fast move that is
recorded and not played: what thinking changed), solver ("auto": the Connect Four solver judges every move when it is
compiled, see c4solver.py; true: required; false: never; the gate modes goracle / gcrit always need it).
Next to the record, .moves.jsonl: one line per move of an agent (ply, side, player, move, think, gate decision, fast
move, the solver's view of the position and the quality of the move, tokens by kind: see duel.play_duel()).
"""
import argparse
import json
import os
import random
import time
from datetime import datetime, timezone

from records import LEGACY_RESULTS, record_path, save_record
from run import grid_games, is_played, load_grid, summary

DUEL_DEFAULTS = dict(max_steps=100, max_illegal=3, illegal_policy="random", colors="alternate", game_options={},
                     adjudicate=None, adjudicate_cp=300, shadow_fast_move=False, solver="auto")


def split_prompt(prompt):
    """A prompt of a duel grid is "<prompt>" or "<prompt>+<board>": the second shows the game with that board text
    (the option board of duel_games: Connect Four columns / both, chess fen / both / ascii) and is a prompt of its
    own in the records and in their paths, so its games are never taken for those of the plain board.
    Returns (prompt of duel_prompts, the game options it sets)."""
    base, _, board = prompt.partition("+")
    return base, ({"board": board} if board else {})


def prompt_game_options(grid, game, prompt):
    """The options of the game for this prompt: those of the grid, then the board of the prompt."""
    return {**grid["game_options"].get(game, {}), **split_prompt(prompt)[1]}


def grid_needs_solver(grid):
    """Whether a mode of the grid (agent or self-play opponent) is a gate that asks the Connect Four solver."""
    from agent import parse_mode
    from duel import parse_opponent
    from gate import needs_solver
    modes = set(grid["modes"])
    for levels in grid["games"].values():
        for level in levels:
            kind, mode = parse_opponent(level)
            if kind == "self":
                modes.add(mode)
    return any(parse_mode(mode)["gate"] and needs_solver(parse_mode(mode)["gate"]) for mode in modes)


def grid_solver(grid):
    """The shared Connect Four solver for the grid: always when a gate needs it (an error if it is not compiled),
    when it is available and the grid plays Connect Four (solver: "auto": every move is judged), never with
    solver: false. None when there is none."""
    import c4solver
    setting = grid.get("solver", "auto")
    needed = grid_needs_solver(grid)
    if setting is False and needed:
        raise ValueError("the grid has gate modes that need the solver, but solver: false")
    if setting is False or "connect_four" not in grid["games"]:
        return None
    if not c4solver.available():
        if needed or setting is True:
            raise FileNotFoundError("the Connect Four solver is not compiled: python c4solver.py --setup")
        print("note: the Connect Four solver is not compiled (python c4solver.py --setup): the moves are not judged")
        return None
    return c4solver.shared_solver()


def load_duel_grid(path):
    from duel import parse_opponent
    grid = {**DUEL_DEFAULTS, **load_grid(path)}
    grid["games"] = {game: [str(level) for level in levels] for game, levels in grid["games"].items()}
    from duel_games import make_game
    from duel_prompts import DUEL_PROMPTS
    for game in grid["games"]:
        for prompt in grid["prompts"]: # unknown prompts and boards fail now, not after loading the model
            if split_prompt(prompt)[0] not in DUEL_PROMPTS[game]:
                raise ValueError(f"unknown prompt {prompt!r} for {game}: one of {sorted(DUEL_PROMPTS[game])}")
            make_game(game, **prompt_game_options(grid, game, prompt))
    for game, levels in grid["games"].items():
        for level in levels:
            parse_opponent(level, game) # unknown opponents fail now, not after loading the model
    return grid


def agent_color(seed, colors):
    """0 if the agent moves first, 1 otherwise."""
    if colors == "alternate":
        return 0 if seed % 2 == 1 else 1
    return {"first": 0, "second": 1}[colors]


def run_duel_grid(grid, results, read=(LEGACY_RESULTS,), models=None, limit=None, n_parallel="auto",
                  gpu_memory_utilization=0.9, verbose=True):
    """Plays the games of the grid (a path or a dict of load_duel_grid()) not yet played, and saves their records in
    results. The other arguments are those of run.run_grid()."""
    from duel import Contestants, DuelAgent, parse_opponent, play_duel, stockfish_path, write_transcript
    from engine import Engine
    from profiles import model_settings

    if isinstance(grid, str):
        grid = load_duel_grid(grid)
    if any(parse_opponent(level)[0] == "stockfish" for levels in grid["games"].values() for level in levels) \
            or (grid["adjudicate"] == "stockfish" and "chess" in grid["games"]):
        stockfish_path() # missing Stockfish fails now, not in the middle of the games
    folders = [results, *read]
    solver = grid_solver(grid) # missing solver fails now, not in the middle of the games
    for model in grid["models"]:
        if models and model not in models:
            continue
        to_play = [game_id for game_id in grid_games(grid, [model]) if not is_played(folders, game_id)]
        if limit is not None:
            to_play = to_play[:limit]
        print(f"{model}: {len(to_play)} games to play")
        if not to_play:
            continue
        profile, settings = model_settings(model)
        engine = Engine(model, gpu_memory_utilization=gpu_memory_utilization, **settings)

        def make_job(game_id):
            model, game, prompt, mode, level, seed = game_id
            path = record_path(results, *game_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            kind, opponent_mode = parse_opponent(level, game)
            agent_prompt = split_prompt(prompt)[0]
            # the seeds of the random choices do not depend on the board: the same thinking turns for r4, the same
            # opening, as in the game with the plain board
            name = "|".join(str(part) for part in (model, game, agent_prompt, mode, level, seed))

            game_solver = solver if game == "connect_four" else None
            shadow = bool(grid["shadow_fast_move"])

            def make_agent(generate):
                agent = DuelAgent(profile, engine.tokenizer, generate, mode, game, agent_prompt,
                                  rng=random.Random(name),
                                  log=path[:-len(".json")] + ".log", solver=game_solver, shadow=shadow)
                opponent = None
                if kind == "self":
                    opponent = DuelAgent(profile, engine.tokenizer, generate, opponent_mode, game, agent_prompt,
                                         rng=random.Random(name + "|opponent"),
                                         log=path[:-len(".json")] + ".opponent.log", solver=game_solver, shadow=shadow)
                return Contestants(agent, opponent)
            play_kwargs = dict(game=game, level=level, seed=seed, color=agent_color(seed, grid["colors"]),
                               max_steps=grid["max_steps"], max_illegal=grid["max_illegal"],
                               illegal_policy=grid["illegal_policy"],
                               game_options=prompt_game_options(grid, game, prompt),
                               adjudicate_with=grid["adjudicate"] if game == "chess" else None,
                               adjudicate_cp=grid["adjudicate_cp"], solver=game_solver)
            return game_id, make_agent, play_kwargs

        def on_result(game_id, contestants, result):
            model, game, prompt, mode, level, seed = game_id
            path = record_path(results, *game_id)[:-len(".json")]
            thoughts = result.pop("thoughts") # next to the record, not in it: with e1 they are long
            with open(path + ".thoughts.jsonl", "w") as f:
                f.writelines(json.dumps(thought) + "\n" for thought in thoughts)
            move_records = result.pop("move_records") # one line per move of an agent: solver, gate, fast move
            with open(path + ".moves.jsonl", "w") as f:
                f.writelines(json.dumps(move) + "\n" for move in move_records)
            write_transcript(path + ".transcript.md", {**result, "thoughts": thoughts, "move_records": move_records},
                             f"{game}, {model} ({prompt}): the agent ({mode}) against {level}, seed {seed}")
            record = dict(model=model, mode=mode, prompt=prompt, game=game, level=level, seed=seed, opponent=level,
                          norm_score=result["score"] / result["max_score"], **result,
                          max_steps=grid["max_steps"], max_illegal=grid["max_illegal"],
                          illegal_policy=grid["illegal_policy"], game_options=prompt_game_options(grid, game, prompt),
                          adjudicate=grid["adjudicate"] if game == "chess" else None,
                          adjudicate_cp=grid["adjudicate_cp"], shadow_fast_move=grid["shadow_fast_move"],
                          solver=solver is not None and game == "connect_four",
                          loop_window=None, tokens=contestants.agent.token_stats(),
                          opponent_tokens=contestants.opponent.token_stats() if contestants.opponent else None,
                          time_kind="wall", profile=profile.name, settings=engine.description,
                          source="benchmark-duel", log=f"level{level}-seed{seed}.log",
                          transcript=f"level{level}-seed{seed}.transcript.md",
                          created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            save_record(record_path(results, *game_id), record)

        start = time.perf_counter()
        try:
            # the contexts of the games of a mode grow alike; in self-play there are two of them
            engine.play([make_job(game_id) for game_id in to_play], n_parallel=n_parallel,
                        on_result=on_result, verbose=verbose, play_function=play_duel,
                        group=lambda game_id: (game_id[3], game_id[4]) if game_id[4].startswith("self-") else game_id[3])
        finally:
            engine.close()
        print(f"{model}: done in {(time.perf_counter() - start) / 60:.0f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("grid", help="grid file, e.g. grids/duel_tier1.yaml")
    parser.add_argument("--results", default="results_new", help="folder where the new records are saved")
    parser.add_argument("--read", nargs="*", default=[LEGACY_RESULTS],
                        help="other results folders whose games are not played again")
    parser.add_argument("--model", nargs="*", help="only these models of the grid")
    parser.add_argument("--limit", type=int, help="at most this many games per model (e.g. a pilot)")
    parser.add_argument("--n-parallel", default="auto",
                        help="games played at the same time, or auto: as many as fit in the GPU memory")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9)
    parser.add_argument("--dry-run", action="store_true", help="only show what is played and what is left")
    args = parser.parse_args()
    grid = load_duel_grid(args.grid)
    if args.dry_run:
        summary(grid, args.results, args.read, args.model)
    else:
        n_parallel = args.n_parallel if args.n_parallel == "auto" else int(args.n_parallel)
        run_duel_grid(grid, args.results, args.read, args.model, args.limit, n_parallel, args.gpu_memory_utilization)


if __name__ == "__main__":
    main()
