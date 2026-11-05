# connect_four, Qwen/Qwen3-4B (free): the agent (sr4) against greedy, seed 14

Result for the agent (O): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 3
1... 4
2. 2
2... 4
3. 4
3... 4
4. 2
4... 4  (blunder: win -> loss, fast move 4 (blunder))
5. 6
5... 5  (blunder: win -> loss)
6. 6
6... 5  (fast move 5 (fine))
7. 6
7... 5
8. 6
