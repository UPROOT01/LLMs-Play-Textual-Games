# connect_four, Qwen/Qwen3-4B (plain): the agent (sr4) against greedy, seed 21

Result for the agent (X): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 1  (blunder: win -> loss)
1... 5
2. 3  (blunder: win -> loss)
2... 2
3. 4
3... 4
4. 5
4... 2
5. 6  (blunder: draw -> loss, fast move 6 (blunder))
5... 4
6. 1
6... 5
7. 3
7... 3
8. 6
8... 2
