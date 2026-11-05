# connect_four, Qwen/Qwen3-4B (free): the agent (sr4) against greedy, seed 16

Result for the agent (O): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 3
1... 4
2. 5
2... 3
3. 6
3... 4
4. 6
4... 5
5. 4
5... 2  (blunder: win -> loss, fast move 5 (fine))
6. 2
6... 6
7. 5
7... 6  (blunder: win -> loss)
8. 4
8... 6
9. 7
