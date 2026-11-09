"""Prompts of the agent in the two-player games (duel.py), one set per game, with the same structure of prompts.py:
the system prompt, and the question of the self-evaluations. "CoT" follows the 3 steps of the CoT prompt of the
textual games, rewritten for an opponent who moves between the turns.
"neutral" has the same 3 steps and sampling of "CoT", but never mentions the opponent: CoT asks what the opponent's
last move threatens and what its best reply is, so a model that reasons about what its opponent is doing would only be
following the prompt. With "neutral", reasoning about the opponent can only come from the model (self-play experiments).
"free" does not structure the reasoning at all: neutral's steps ("evaluate the position", "compare 2 or 3 candidate
moves") still frame the task as finding the best move in a fixed position, and its system prompt ("The user shows you
the position") makes the model reason as an assistant advising a user ("Okay, let's see. The user is playing as
Black..."). So free says only which game is played and how to answer, and asks to think: no steps, no "concise", no
"how to win", no user, and no opponent. Same sampling of CoT and neutral, so that only the prompt changes.
"plain" is the model's own thinking: the system prompt of free, and nothing added to the observation on the turns
where it thinks (only the switch that turns the thinking on). The baseline with no instruction about the reasoning.
"note" is plain plus one instruction on the turns where it thinks: end the reasoning with a note for its next moves
(what to watch, what it plans to play). The reasonings of the other prompts say nothing to a later turn (THESIS_LOG.md,
2026-11-09: none of 60 Connect Four reasonings states a fact that outlives the move): does a reasoning written to be
read again get used by the modes that keep it (lrN, lN, rN)? The note is part of the reasoning, so the modes that
drop the reasoning drop it too.
"predict" is free plus one instruction: after its move, the model says which move it expects from the other player
("4 (expecting 3)", see duel_games.split_prediction()). It is the prompt of the surprise gate (gate.py: gsurp thinks
when the other player did not play the expected move) and it makes every mode's predictions measurable: the record
counts how often they came true (prediction_hits). Unlike free, it names the other player.
"""
from prompts import SAMPLING

DUEL_PROMPTS = {
    "chess": {
        "default": {
            "system": """
You are an assistant playing chess against an opponent.
The user shows you the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\". Only output the move, nothing else.
""",
            "selfeval": """
Do you think you're playing well so far? Do you think you're winning?
Think about it, and then say your next move. Remember to only say the move and nothing else.
""",
        },
        "CoT": {
            "system": """
You are an expert chess player. You attentively analyze the position and find the best move.
The user shows you the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\". Think thoroughly about how to win the game and then output the move, nothing else.
""",
            "selfeval": """
THINK about how to win the game, but be concise and brief in 3 steps.
FIRST STEP: Evaluate the position: material, king safety, and what your opponent's last move threatens.
SECOND STEP: List 2 or 3 candidate moves. For each one, check your opponent's best reply: does it lose a piece or allow a checkmate?
THIRD STEP: Choose the best candidate. It is fundamental that your move is legal: if it was not accepted before, choose a different one from your legal moves.
FINALLY, say your move in standard algebraic notation. Only output the move, nothing else.
""",
        },
        "neutral": {
            "system": """
You are an expert chess player. You attentively analyze the position and find the best move.
The user shows you the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\". Think thoroughly about how to win the game and then output the move, nothing else.
""",
            "selfeval": """
THINK about how to win the game, but be concise and brief in 3 steps.
FIRST STEP: Evaluate the position.
SECOND STEP: List 2 or 3 candidate moves and compare them.
THIRD STEP: Choose the best candidate. It is fundamental that your move is legal: if it was not accepted before, choose a different one from your legal moves.
FINALLY, say your move in standard algebraic notation. Only output the move, nothing else.
""",
        },
        "free": {
            "system": """
You are playing a game of chess.
You are shown the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\". Only output the move, nothing else.
""",
            "selfeval": """
Think about the game, then say your move in standard algebraic notation. Only output the move, nothing else.
""",
        },
        "plain": {
            "system": """
You are playing a game of chess.
You are shown the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\". Only output the move, nothing else.
""",
            "selfeval": "\n", # nothing: the observation, then the switch that turns the thinking on
        },
        "note": {
            "system": """
You are playing a game of chess.
You are shown the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\". Only output the move, nothing else.
""",
            "selfeval": """
Think about your move. End your thinking with a short note to yourself for your next moves, starting with \"NOTE FOR MY NEXT MOVES:\": what you must keep an eye on, and what you plan to play next and why. Then say your move in standard algebraic notation. Only output the move, nothing else.
""",
        },
        "predict": {
            "system": """
You are playing a game of chess.
You are shown the position and you reply with your move in standard algebraic notation, like \"Nf3\" or \"e4\", followed by the move you expect the other player to make next, in parentheses, like \"Nf3 (expecting d5)\". Only output this, nothing else.
""",
            "selfeval": """
Think about the game, then say your move in standard algebraic notation and, in parentheses, the move you expect the other player to make next, like \"Nf3 (expecting d5)\". Nothing else.
""",
        },
    },
    "connect_four": {
        "default": {
            "system": """
You are an assistant playing Connect Four against an opponent. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
The user shows you the board and you reply with the column where you drop your disc, like \"4\". Only output the column number, nothing else.
""",
            "selfeval": """
Do you think you're playing well so far? Do you think you're winning?
Think about it, and then say your next move. Remember to only say the column number and nothing else.
""",
        },
        "CoT": {
            "system": """
You are an expert Connect Four player. You attentively analyze the board and find the best move. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
The user shows you the board and you reply with the column where you drop your disc, like \"4\". Think thoroughly about how to win the game and then output the column number, nothing else.
""",
            "selfeval": """
THINK about how to win the game, but be concise and brief in 3 steps.
FIRST STEP: Can you connect four with this move? Can your opponent connect four with their next move, so that you have to block it?
SECOND STEP: List 2 or 3 candidate columns. For each one, check your opponent's best reply: does it let them connect four on top of your disc?
THIRD STEP: Choose the best candidate. It is fundamental that the column is not full: if your move was not accepted before, choose a different column.
FINALLY, say your move as a column number. Only output the number, nothing else.
""",
        },
        "neutral": {
            "system": """
You are an expert Connect Four player. You attentively analyze the board and find the best move. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
The user shows you the board and you reply with the column where you drop your disc, like \"4\". Think thoroughly about how to win the game and then output the column number, nothing else.
""",
            "selfeval": """
THINK about how to win the game, but be concise and brief in 3 steps.
FIRST STEP: Evaluate the board.
SECOND STEP: List 2 or 3 candidate columns and compare them.
THIRD STEP: Choose the best candidate. It is fundamental that the column is not full: if your move was not accepted before, choose a different column.
FINALLY, say your move as a column number. Only output the number, nothing else.
""",
        },
        "free": {
            "system": """
You are playing a game of Connect Four. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
You are shown the board and you reply with the column where you drop your disc, like \"4\". Only output the column number, nothing else.
""",
            "selfeval": """
Think about the game, then say your move as a column number. Only output the number, nothing else.
""",
        },
        "plain": {
            "system": """
You are playing a game of Connect Four. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
You are shown the board and you reply with the column where you drop your disc, like \"4\". Only output the column number, nothing else.
""",
            "selfeval": "\n", # nothing: the observation, then the switch that turns the thinking on
        },
        "note": {
            "system": """
You are playing a game of Connect Four. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
You are shown the board and you reply with the column where you drop your disc, like \"4\". Only output the column number, nothing else.
""",
            "selfeval": """
Think about your move. End your thinking with a short note to yourself for your next moves, starting with \"NOTE FOR MY NEXT MOVES:\": the lines you must keep an eye on, and what you plan to play next and why. Then say your move as a column number. Only output the number, nothing else.
""",
        },
        "predict": {
            "system": """
You are playing a game of Connect Four. You win by connecting four of your discs in a row, horizontally, vertically or diagonally.
You are shown the board and you reply with the column where you drop your disc, followed by the column where you expect the other player to drop their next disc, in parentheses, like \"4 (expecting 3)\". Only output this, nothing else.
""",
            "selfeval": """
Think about the game, then say your move as a column number and, in parentheses, the column you expect the other player to play next, like \"4 (expecting 3)\". Nothing else.
""",
        },
    },
}

# same sampling of the textual games, so that the only difference is the game
DUEL_SAMPLING = {**SAMPLING, "neutral": SAMPLING["CoT"], "free": SAMPLING["CoT"], "plain": SAMPLING["CoT"],
                 "predict": SAMPLING["CoT"], "note": SAMPLING["CoT"]}
