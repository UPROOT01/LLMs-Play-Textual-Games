# connect_four, Qwen/Qwen3-4B (plain): the agent (sr4) against greedy, seed 7

Result for the agent (X): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 1  (blunder: win -> loss)
1... 4
2. 2
2... 2
3. 3
3... 3
4. 4  (fast move 4 (fine))
4... 4
5. 5
5... 6
6. 5
6... 1
7. 7  (blunder: draw -> loss, fast move 6 (blunder))
7... 7
8. 1  (blunder: draw -> loss)
8... 5
9. 4
9... 7
10. 4  (blunder: draw -> loss, fast move 4 (blunder))
10... 4
11. 5  (blunder: win -> loss)
11... 3
12. 5  (blunder: draw -> loss)
12... 2
