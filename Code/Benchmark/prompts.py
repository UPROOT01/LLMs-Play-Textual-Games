"""Prompts of the agent, without the switches that turn thinking on and off (e.g. Qwen3's "/no_think"):
each model profile adds its own (see profiles.py).
The texts are the same of Self-evaluation/self_evaluation.py, so that the Qwen3 contexts are identical to the old ones.
"""

PROMPTS = {
    "default": {
        "system": """
You are an assistant playing a textual game.
The user gives you information on the environment and you reply with a short command, like \"go north\". Only output the action, nothing else.
""",
        "selfeval": """
Do you think you're making the right actions in the game so far? Do you think you're close to reaching the original goal?
Think about it, and then say your next action. Remember to only say the command and nothing else.
""",
    },
    "CoT": { # Chain-of-Thought
        "system": """
You are an expert assistant playing a textual game. You attentively analyze the game environment and find the best course of action.
The user gives you information on the environment and you reply with a short command, like \"go north\". Think thoroughly about how to reach the objective and then output the action, nothing else.
""",
        "selfeval": """
THINK about how to win the game, but be concise and brief in 3 steps.
FIRST STEP: State the goal of the game again, step by step.
SECOND STEP: State your past actions so far. Which steps of the goal have you already taken? And what is the next step?
THIRD STEP: Recognize if you find yourself in a loop. Your most important goal is to break the loop: if the previous actions didn't make any progress you have to say a different action, maybe by using a synonym. It is fundamental that you do NOT repeat the same action if it's not being recognized.
FINALLY, say your next action as a short command. Only output the command, nothing else.
""",
    },
}

# sampling parameters of the self-evaluations ("think") and of the normal turns.
# None: the defaults of the model (its generation_config.json), as model.generate() did with the default prompt
SAMPLING = {
    "default": {"think": None, "normal": None},
    "CoT": {"think": dict(temperature=0.65, top_p=0.9, min_p=0.05, top_k=25),
            "normal": dict(temperature=0.7, top_p=0.9, min_p=0.1, top_k=20)},
}
