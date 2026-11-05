# connect_four, Qwen/Qwen3-4B (predict): the agent (gsurp) against greedy, seed 10

Result for the agent (O): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 2
1... 4  (blunder: win -> draw, gate fired: no expectation, fast move 1 (blunder), expects 3)
2. 7
2... 3  (blunder: win -> draw, gate fired: surprised: expected 3, got 7, fast move 4 (fine), expects 4)
3. 3
3... 5  (blunder: win -> loss, gate fired: surprised: expected 4, got 3, fast move 4 (fine), expects 5)
4. 6
4... 1  (gate fired: surprised: expected 5, got 6, fast move 5 (fine), expects 1)
5. 6
5... 5  (gate fired: surprised: expected 1, got 6, fast move 4 (fine), expects 5)
6. 4
6... 5  (blunder: draw -> loss, gate fired: surprised: expected 5, got 4, fast move 5 (blunder), expects 5)
7. 5
7... 5  (gate silent: as expected: 5, expects 5)
8. 4
