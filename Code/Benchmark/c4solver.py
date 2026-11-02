"""The exact value of every Connect Four move: Pascal Pons's solver (https://github.com/PascalPons/connect4, AGPL v3)
with its opening book, run as a subprocess. It is the oracle of the gate experiments (gate.py): which positions are
critical, which moves lose the game, and how good each move the agent played was.

Setup (locally or on Colab): `python c4solver.py --setup` clones and compiles the solver and downloads the book into
the folder c4solver/ next to this file (git, g++ and make are needed; also C4SOLVER / C4BOOK environment variables
can point to the binary and the book). `python c4solver.py 4453` prints the scores of a position.

A position is the sequence of the columns played, as a string ("4453": 4, 4, 5, 3). The solver gives, for the player
to move, the score of each of the 7 columns: positive = that move wins (the bigger, the sooner: 1 = wins with the
last disc of the board, 18 = wins at once), 0 = draws, negative = loses (the smaller, the sooner), ILLEGAL (-1000)
for a full column. Positions already won are invalid (nothing to play).
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "c4solver")
REPOSITORY = "https://github.com/PascalPons/connect4.git"
BOOK_URL = "https://github.com/PascalPons/connect4/releases/download/book/7x6.book"
ILLEGAL = -1000
COLUMNS = 7


def solver_path():
    """The compiled solver, or None."""
    path = os.environ.get("C4SOLVER") or os.path.join(FOLDER, "c4solver")
    return path if os.path.isfile(path) else shutil.which("c4solver")


def book_path():
    path = os.environ.get("C4BOOK") or os.path.join(FOLDER, "7x6.book")
    return path if os.path.isfile(path) else None


def available():
    return solver_path() is not None


def setup(folder=FOLDER, verbose=True):
    """Clones, compiles and downloads what is missing into folder. Returns the path of the solver."""
    os.makedirs(folder, exist_ok=True)
    source = os.path.join(folder, "src")
    binary = os.path.join(folder, "c4solver")
    book = os.path.join(folder, "7x6.book")
    if not os.path.isfile(binary):
        if not os.path.isdir(source):
            if verbose:
                print("cloning the solver...")
            subprocess.run(["git", "clone", "-q", "--depth", "1", REPOSITORY, source], check=True)
        if verbose:
            print("compiling the solver...")
        subprocess.run(["make", "-s", "-C", source, "c4solver"], check=True)
        shutil.copy(os.path.join(source, "c4solver"), binary)
    if not os.path.isfile(book):
        if verbose:
            print("downloading the opening book (34 MB)...")
        subprocess.run(["curl", "-sL", "-o", book, BOOK_URL], check=True)
        if os.path.getsize(book) < 1_000_000:
            os.remove(book)
            raise RuntimeError(f"the opening book could not be downloaded from {BOOK_URL}")
    if verbose:
        print(f"solver: {binary}\nbook: {book}")
    return binary


ROWS = 6


def valid_position(position):
    """Whether a sequence of columns ("4453") can be asked to the solver: columns 1-7, none played more than 6 times,
    and no four in a row before the end (the solver has nothing to say on a won game)."""
    heights = [0] * COLUMNS
    grid = [[None] * COLUMNS for _ in range(ROWS)]
    for index, char in enumerate(position):
        if char not in "1234567":
            return False
        column = int(char) - 1
        row = heights[column]
        if row >= ROWS:
            return False
        if index > 0 and _won(grid): # won before this move: this move could not be played
            return False
        grid[row][column] = index % 2
        heights[column] += 1
    return not _won(grid)


def _won(grid):
    for row in range(ROWS):
        for column in range(COLUMNS):
            side = grid[row][column]
            if side is None:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                r, c, count = row, column, 0
                while 0 <= r < ROWS and 0 <= c < COLUMNS and grid[r][c] == side:
                    count += 1
                    if count == 4:
                        return True
                    r, c = r + dr, c + dc
    return False


def value_class(score):
    """"win", "draw" or "loss" of a score for the player to move (None for an illegal move)."""
    if score == ILLEGAL or score is None:
        return None
    return "win" if score > 0 else "draw" if score == 0 else "loss"


CLASS_RANK = {"loss": 0, "draw": 1, "win": 2}


def analyze_scores(scores):
    """What the scores of a position say: the best score and class, the legal moves (columns "1".."7"), those that
    keep the game value (`safe`: same class as the best), the winning moves at once, and the moves of the opponent
    that must be blocked (a loss at once if not played: score -17 means the opponent wins on the next move)."""
    legal = [str(column + 1) for column, score in enumerate(scores) if score != ILLEGAL]
    best = max(score for score in scores if score != ILLEGAL)
    best_class = value_class(best)
    safe = [str(column + 1) for column, score in enumerate(scores)
            if score != ILLEGAL and value_class(score) == best_class]
    return dict(scores=list(scores), best=best, best_class=best_class, legal=legal, safe=safe,
                n_safe=len(safe), n_legal=len(legal),
                immediate_win=[str(column + 1) for column, score in enumerate(scores) if score == 18],
                forced=len(safe) == 1)


def move_quality(scores, move):
    """How good a move is in a position with these scores: its score and class, and `blunder` (its class is worse
    than the best one, i.e. the move changes the game-theoretic value) and `lost` (ranks of class lost, 0-2)."""
    if move is None:
        return dict(score=None, cls=None, blunder=None, lost=None)
    score = scores[int(move) - 1]
    if score == ILLEGAL:
        return dict(score=None, cls=None, blunder=None, lost=None)
    best_class = value_class(max(s for s in scores if s != ILLEGAL))
    cls = value_class(score)
    return dict(score=score, cls=cls, blunder=cls != best_class, lost=CLASS_RANK[best_class] - CLASS_RANK[cls])


class Solver:
    """The solver as a process that stays open; thread-safe (one position at a time). With `weak=True` the scores
    are only +1 / 0 / -1 (faster on hard positions): enough for the classes, not for the speed of the win."""

    def __init__(self, binary=None, book=None, weak=False):
        self.binary = binary or solver_path()
        if self.binary is None:
            raise FileNotFoundError("the Connect Four solver is not compiled: python c4solver.py --setup")
        self.book = book or book_path()
        if self.book is None:
            raise FileNotFoundError("the opening book 7x6.book is missing: python c4solver.py --setup")
        self.weak = weak
        self.lock = threading.Lock()
        self.cache = {}
        self.calls, self.seconds = 0, 0.0
        self._start()

    def _start(self):
        command = [self.binary, "-a", "-b", self.book] + (["-w"] if self.weak else [])
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def scores(self, position):
        """The 7 scores of a position (a string of columns, or a list of moves). None if the position is invalid
        (an illegal sequence, or a game already won)."""
        position = "".join(str(move) for move in position)
        if position in self.cache:
            return self.cache[position]
        if not valid_position(position): # the solver writes nothing on stdout for these: asking would wait forever
            return None
        with self.lock:
            if self.process.poll() is not None:
                self._start()
            start = time.perf_counter()
            self.process.stdin.write(position + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
            self.seconds += time.perf_counter() - start
            self.calls += 1
        if not line.strip(): # an invalid position: the solver writes on stderr and nothing on stdout...
            return None
        parts = line.split()
        if position: # the line is the position followed by the 7 scores (only the scores for the empty board)
            if not parts or parts[0] != position:
                return None
            parts = parts[1:]
        if len(parts) != COLUMNS: # the process died, or wrote something else
            return None
        result = [int(part) for part in parts]
        self.cache[position] = result
        return result

    def analyze(self, position):
        """analyze_scores() of a position, or None if it is invalid."""
        scores = self.scores(position)
        return None if scores is None else analyze_scores(scores)

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


_shared = None
_shared_lock = threading.Lock()


def shared_solver():
    """One solver for the whole process (the games played together share it)."""
    global _shared
    with _shared_lock:
        if _shared is None or _shared.process.poll() is not None:
            _shared = Solver()
        return _shared


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("positions", nargs="*", help="positions as sequences of columns, e.g. 4453")
    parser.add_argument("--setup", action="store_true", help="clone, compile and download the book")
    args = parser.parse_args()
    if args.setup:
        setup()
    if args.positions:
        solver = Solver()
        for position in args.positions:
            scores = solver.scores(position)
            if scores is None:
                print(f"{position}: invalid position")
                continue
            info = analyze_scores(scores)
            print(f"{position}: {scores}  best {info['best']} ({info['best_class']}), safe moves {info['safe']}")
        solver.close()
    elif not args.setup:
        print("solver:", solver_path(), "book:", book_path())


if __name__ == "__main__":
    sys.exit(main())
