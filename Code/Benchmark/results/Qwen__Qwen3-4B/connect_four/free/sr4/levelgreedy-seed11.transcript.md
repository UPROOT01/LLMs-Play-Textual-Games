# connect_four, Qwen/Qwen3-4B (free): the agent (sr4) against greedy, seed 11

Result for the agent (X): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 1  (blunder: win -> loss)
1... 5
2. 3  (blunder: win -> loss)
2... 3
3. 2  (blunder: draw -> loss, fast move 5 (fine))
3... 4
4. 6
4... 3
5. 6
5... 6
6. 5
6... 3
7. 4  (fast move 5 (fine))
7... 3
