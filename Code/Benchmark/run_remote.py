"""Plays a grid on a remote machine: python run_remote.py <grid.yaml> [model,model...]
A grid of two-player games (chess, connect_four) is played with run_duel, a grid of textual games with run."""
import os, sys
os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")  # no nvcc on this machine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from duel_prompts import DUEL_PROMPTS
from records import LEGACY_RESULTS
from run import load_grid, run_grid, summary


def is_duel_grid(path):
    """True when the games of the grid are two-player games. A grid that mixes the two kinds is refused."""
    games = load_grid(path)["games"]
    duel = [game in DUEL_PROMPTS for game in games]
    if any(duel) and not all(duel):
        raise ValueError(f"{path} mixes two-player and textual games: {sorted(games)}")
    return all(duel)


if __name__ == "__main__":
    grid_path = sys.argv[1] if len(sys.argv) > 1 else "grids/duel_c4_gate.yaml"
    models = sys.argv[2].split(",") if len(sys.argv) > 2 else None   # e.g. Qwen/Qwen3-4B
    results, read = "results", [LEGACY_RESULTS]

    if is_duel_grid(grid_path):
        import c4solver
        from run_duel import load_duel_grid, run_duel_grid
        if not c4solver.available():
            c4solver.setup()
        grid = load_duel_grid(grid_path)
        play = run_duel_grid
    else:
        grid = load_grid(grid_path)
        play = run_grid

    summary(grid, results, read, models)
    play(grid, results, read, models=models, limit=None, n_parallel="auto")
    summary(grid, results, read, models)
