"""Two-player games, as text: what a player sees, which moves it can play, how its answer becomes a move.

Unlike the TextWorld games, the environment is not a file: a game is created from its name and options, and the
opponent (duel.py) moves between the turns of the agent. The two sides are 0 (moves first: White, X) and 1.

Every game has:
    turn: the side to move;  legal_moves(): its moves, as text (SAN for chess, the column for Connect Four);
    parse(answer): the legal move written in the agent's answer, or None;  play(move);
    over(), winner() (0, 1 or None for a draw), termination() (why it ended);
    observation(side, last_move, feedback): what the agent of that side reads before moving;
    retry_observation(side): what it reads after an illegal answer (only the error and the legal moves: the position
        is already in its context, and repeating it at every wrong answer would fill the context);
    greedy_move(rng): the move of a simple one-move-ahead player, used as an opponent;
    summary(side): the final position and a few numbers, saved in the record.
"""
import re

PIECE_VALUES = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 0}


class DuelGame:
    name = None
    sides = ("first", "second")

    def __init__(self, rng=None):
        self.history = [] # the moves played, as text

    @property
    def turn(self):
        raise NotImplementedError

    def legal_moves(self):
        raise NotImplementedError

    def parse(self, answer):
        raise NotImplementedError

    def play(self, move):
        raise NotImplementedError

    def over(self):
        raise NotImplementedError

    def winner(self):
        raise NotImplementedError

    def termination(self):
        raise NotImplementedError

    def observation(self, side, last_move=None, feedback=None):
        raise NotImplementedError

    legal_label = "Your legal moves"
    move_request = "Your move:"

    def retry_observation(self, side):
        lines = ["That is not a legal move."]
        if self.show_legal:
            lines.append(f"{self.legal_label}: " + ", ".join(self.legal_moves()))
        lines.append(self.move_request)
        return "\n".join(lines)

    def greedy_move(self, rng):
        raise NotImplementedError

    def summary(self, side):
        return {}


def _words(answer):
    """The words of an answer, last first: a model that names more than one move usually ends with the one it plays."""
    return [word for word in re.split(r"[\s,;:()\[\]\"'`*]+", answer) if word][::-1]


class Chess(DuelGame):
    """Chess with python-chess. Draws by threefold repetition and the fifty-move rule are claimed automatically.

    Options: board: "ascii", "fen" or "both" (how the position is written); legal_moves: whether the legal moves are
    listed (as the admissible commands, which the TextWorld observations do not show); history: whether the moves so
    far are written in PGN; opening_plies: random moves played at the start (by the game's rng), for varied openings.
    """
    name = "chess"
    sides = ("White", "Black")

    def __init__(self, rng=None, board="both", legal_moves=True, history=False, opening_plies=0):
        import chess
        super().__init__(rng)
        if board not in ("ascii", "fen", "both"):
            raise ValueError(f"unknown board format {board!r}")
        self.chess = chess
        self.board = chess.Board()
        self.show_board, self.show_legal, self.show_history = board, legal_moves, history
        for _ in range(opening_plies):
            if self.over():
                break
            self.play(rng.choice(self.legal_moves()))
        self.opening = list(self.history)

    @property
    def turn(self):
        return 0 if self.board.turn == self.chess.WHITE else 1

    def legal_moves(self):
        return [self.board.san(move) for move in self.board.legal_moves]

    def parse(self, answer):
        """SAN ("Nf3", "exd5", "O-O") or UCI ("g1f3"), forgiving move numbers ("12. Nf3", "12...Nf3"), final
        punctuation, "0-0" for "O-O" and lowercase pieces ("nf3")."""
        for word in [answer.strip()] + _words(answer):
            word = re.sub(r"^\d+\.+", "", word).strip(".!?")
            word = word.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
            if not word:
                continue
            candidates = [word]
            if word[0] in "nkqr" and len(word) > 2:
                candidates.append(word[0].upper() + word[1:])
            for candidate in candidates:
                try:
                    return self.board.san(self.board.parse_san(candidate))
                except ValueError:
                    pass
                try:
                    move = self.chess.Move.from_uci(candidate.lower())
                except ValueError:
                    continue
                if move in self.board.legal_moves:
                    return self.board.san(move)
        return None

    def play(self, move):
        self.history.append(move)
        self.board.push_san(move)

    def over(self):
        return self.board.is_game_over(claim_draw=True)

    def _outcome(self):
        return self.board.outcome(claim_draw=True)

    def winner(self):
        outcome = self._outcome()
        if outcome is None or outcome.winner is None:
            return None
        return 0 if outcome.winner == self.chess.WHITE else 1

    def termination(self):
        outcome = self._outcome()
        return outcome.termination.name.lower() if outcome else None

    def material(self, side):
        """Material of the side minus the material of its opponent (pawn = 1, knight and bishop = 3, ...)."""
        total = 0
        for piece in self.board.piece_map().values():
            value = PIECE_VALUES[piece.symbol().upper()]
            total += value if (piece.color == self.chess.WHITE) == (side == 0) else -value
        return total

    def _ascii(self):
        rows = str(self.board).split("\n")
        return "\n".join(f"{8 - i} {row}" for i, row in enumerate(rows)) + "\n  a b c d e f g h"

    def _pgn(self):
        return self.chess.Board().variation_san(self.board.move_stack) if self.history else ""

    def observation(self, side, last_move=None, feedback=None):
        lines = []
        if feedback:
            lines.append(feedback)
        if last_move is None:
            lines.append(f"The game starts. You play {self.sides[side]}"
                         + (" and move first." if side == self.turn and not self.history else "."))
        else:
            lines.append(f"{self.sides[1 - side]} played {last_move}.")
        if self.show_history and self.history:
            lines.append("Moves so far: " + self._pgn())
        if self.show_board in ("ascii", "both"):
            lines.append("Board (uppercase: White, lowercase: Black, dots: empty squares):\n" + self._ascii())
        if self.show_board in ("fen", "both"):
            lines.append("FEN: " + self.board.fen())
        if self.board.is_check():
            lines.append("You are in check.")
        if self.show_legal:
            lines.append("Your legal moves: " + ", ".join(self.legal_moves()))
        lines.append(f"You play {self.sides[side]}. Your move:")
        return "\n".join(lines)

    def greedy_move(self, rng):
        """Mates if it can, otherwise takes the most valuable piece it can (losing the capturing piece counts if the
        square is defended), otherwise a random move."""
        best, best_gain = [], None
        for move in self.board.legal_moves:
            self.board.push(move)
            mate = self.board.is_checkmate()
            self.board.pop()
            if mate:
                return self.board.san(move)
            gain = 0
            captured = self.board.piece_at(move.to_square)
            if self.board.is_en_passant(move):
                gain = 1
            elif captured is not None:
                gain = PIECE_VALUES[captured.symbol().upper()]
                if self.board.is_attacked_by(not self.board.turn, move.to_square):
                    gain -= PIECE_VALUES[self.board.piece_at(move.from_square).symbol().upper()]
            if best_gain is None or gain > best_gain:
                best, best_gain = [move], gain
            elif gain == best_gain:
                best.append(move)
        return self.board.san(rng.choice(best))

    def summary(self, side):
        return dict(final_state=self.board.fen(), pgn=self._pgn(), material=self.material(side),
                    opening=self.opening)


class ConnectFour(DuelGame):
    """Connect Four on a 6x7 board: the moves are the columns, 1 to 7. X moves first.
    Options: legal_moves: whether the playable columns are listed."""
    name = "connect_four"
    sides = ("X", "O")
    rows, columns = 6, 7
    legal_label = "Columns you can play"
    move_request = "Your move (a column number):"

    def __init__(self, rng=None, legal_moves=True):
        super().__init__(rng)
        self.grid = [[None] * self.columns for _ in range(self.rows)] # grid[0] is the bottom row
        self.show_legal = legal_moves
        self._winner = None

    @property
    def turn(self):
        return len(self.history) % 2

    def legal_moves(self):
        return [str(column + 1) for column in range(self.columns) if self.grid[-1][column] is None]

    def parse(self, answer):
        legal = self.legal_moves()
        for word in [answer.strip()] + _words(answer):
            word = word.strip(".!?")
            if word in legal:
                return word
        return None

    def _drop(self, column, side):
        row = next(row for row in range(self.rows) if self.grid[row][column] is None)
        self.grid[row][column] = side
        return row

    def _wins(self, row, column):
        side = self.grid[row][column]
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            count = 1
            for sign in (1, -1):
                r, c = row + sign * dr, column + sign * dc
                while 0 <= r < self.rows and 0 <= c < self.columns and self.grid[r][c] == side:
                    count += 1
                    r, c = r + sign * dr, c + sign * dc
            if count >= 4:
                return True
        return False

    def play(self, move):
        side = self.turn
        column = int(move) - 1
        row = self._drop(column, side)
        self.history.append(move)
        if self._wins(row, column):
            self._winner = side

    def over(self):
        return self._winner is not None or not self.legal_moves()

    def winner(self):
        return self._winner

    def termination(self):
        if self._winner is not None:
            return "four_in_a_row"
        return "full_board" if not self.legal_moves() else None

    def _text(self):
        symbol = {None: ".", 0: "X", 1: "O"}
        rows = ["| " + " ".join(symbol[cell] for cell in self.grid[row]) + " |" for row in reversed(range(self.rows))]
        return "\n".join(rows) + "\n  " + " ".join(str(column + 1) for column in range(self.columns))

    def observation(self, side, last_move=None, feedback=None):
        lines = []
        if feedback:
            lines.append(feedback)
        if last_move is None:
            lines.append(f"The game starts. You play {self.sides[side]}"
                         + (" and move first." if not self.history else "."))
        else:
            lines.append(f"{self.sides[1 - side]} dropped a disc in column {last_move}.")
        lines.append("Board (the discs fall to the bottom row):\n" + self._text())
        if self.show_legal:
            lines.append(f"{self.legal_label}: " + ", ".join(self.legal_moves()))
        lines.append(f"You play {self.sides[side]}. {self.move_request}")
        return "\n".join(lines)

    def _winning_moves(self, side):
        moves = []
        for move in self.legal_moves():
            column = int(move) - 1
            row = self._drop(column, side)
            if self._wins(row, column):
                moves.append(move)
            self.grid[row][column] = None
        return moves

    def greedy_move(self, rng):
        """Wins if it can, otherwise blocks a win of the opponent, otherwise avoids giving the opponent a win on top of
        its disc, preferring the central columns."""
        side = self.turn
        for moves in (self._winning_moves(side), self._winning_moves(1 - side)):
            if moves:
                return rng.choice(moves)
        safe = []
        for move in self.legal_moves():
            column = int(move) - 1
            row = self._drop(column, side)
            gives_win = row + 1 < self.rows and move in self._winning_moves(1 - side)
            self.grid[row][column] = None
            if not gives_win:
                safe.append(move)
        moves = safe or self.legal_moves()
        weights = [4 - abs(int(move) - 4) for move in moves] # center 4, edges 1
        return rng.choices(moves, weights=weights)[0]

    def summary(self, side):
        return dict(final_state=self._text())


GAMES = {game.name: game for game in (Chess, ConnectFour)}


def make_game(name, rng=None, **options):
    if name not in GAMES:
        raise KeyError(f"unknown two-player game {name!r}: one of {sorted(GAMES)}")
    return GAMES[name](rng=rng, **options)
