# connect_four, Qwen/Qwen3-4B (plain): the agent (sr4) against greedy, seed 11

Result for the agent (X): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 1  (blunder: win -> loss)
1... 5
2. 3  (blunder: win -> loss)
2... 3
3. 5
3... 2
4. 6  (blunder: win -> loss)
4... 4
5. 1  (blunder: win -> draw)
5... 3
6. 2  (blunder: win -> loss, fast move 4 (blunder))
6... 5
7. 6  (blunder: win -> loss)
7... 7
8. 4  (blunder: win -> draw, fast move 4 (blunder))
8... 7
9. 4  (blunder: draw -> loss)
9... 3
10. 6
10... 3
