# connect_four, Qwen/Qwen3-4B (plain): the agent (sr4) against greedy, seed 23

Result for the agent (X): loss (four_in_a_row)
Solver: each move of an agent is marked when it changes the game value (blunder: best class -> class of the move); gate modes: whether the gate fired and the fast move it checked.

1. 1  (blunder: win -> loss)
1... 2
2. 3
2... 6
3. 4
3... 6
4. 5
4... 6
5. 6
5... 6
6. 7  (blunder: win -> loss, fast move 6 (blunder))
6... 7
7. 1  (blunder: win -> loss)
7... 4
8. 5
8... 5
9. 1
9... 1
10. 3
10... 6
11. 4  (blunder: draw -> loss, fast move 5 (blunder))
11... 5
12. 7  (blunder: win -> loss)
12... 4
13. 3
13... 4
