# connect_four, Qwen/Qwen3-4B (free): the agent (sr4) against greedy, seed 20

Result for the agent (O): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 4
1... 4
2. 6
2... 3  (blunder: win -> loss)
3. 4
3... 5
4. 7
4... 5
5. 4
5... 5
6. 5
6... 5  (blunder: win -> loss)
7. 4
7... 5  (fast move 5 (fine))
8. 4
