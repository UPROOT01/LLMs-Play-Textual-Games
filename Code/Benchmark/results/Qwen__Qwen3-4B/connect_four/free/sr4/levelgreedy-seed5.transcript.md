# connect_four, Qwen/Qwen3-4B (free): the agent (sr4) against greedy, seed 5

Result for the agent (X): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 1  (blunder: win -> loss)
1... 3
2. 2
2... 7
3. 3
3... 3
4. 4  (blunder: win -> loss)
4... 3
5. 5
5... 7
6. 6  (blunder: draw -> loss, fast move 6 (blunder))
6... 3
7. 6
7... 3
