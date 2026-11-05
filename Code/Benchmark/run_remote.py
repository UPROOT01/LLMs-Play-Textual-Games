import os, sys
os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")  # no nvcc on this machine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import c4solver
if not c4solver.available():
    c4solver.setup()

from records import LEGACY_RESULTS
from run import summary
from run_duel import load_duel_grid, run_duel_grid

grid_path = sys.argv[1] if len(sys.argv) > 1 else "grids/duel_c4_gate.yaml"
models = sys.argv[2].split(",") if len(sys.argv) > 2 else None   # e.g. Qwen/Qwen3-4B
grid = load_duel_grid(grid_path)
results, read = "results", [LEGACY_RESULTS]

summary(grid, results, read, models)
run_duel_grid(grid, results, read, models=models, limit=None, n_parallel="auto")
summary(grid, results, read, models)
