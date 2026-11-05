# connect_four, Qwen/Qwen3-4B (free): the agent (sr4) against greedy, seed 10

Result for the agent (O): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 2
1... 3
2. 7
2... 4  (blunder: win -> draw)
3. 3
3... 5  (blunder: win -> loss)
4. 6
4... 5
5. 6
5... 5
6. 5
6... 5
7. 6
7... 4  (blunder: draw -> loss, fast move 5 (blunder))
8. 6
