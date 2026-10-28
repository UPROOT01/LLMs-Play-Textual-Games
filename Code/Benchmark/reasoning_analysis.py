"""What the reasonings of the two-player games talk about, counted on the words: the tables of reasoning_analysis.ipynb.

It reads the .thoughts.jsonl next to every duel record (one line per move where an agent reasoned, see run_duel.py),
and gives one row per reasoning with:
    words, sentences;  user_frame: the first lines call the player "the user" ("The user is playing as Black");
    board_sentences: sentences spent reading the board ("row 3", "rank", "Wait", "let me check"...);
    waits: how many "Wait";  opponent_words: how many "opponent";
    guesses: the sentences that attribute an intention to the other player ("O is trying to...", "the opponent
        might be setting up...", "they are aiming..."), the behaviour the self-play experiments look for.
The patterns are keyword patterns, so the counts are rough: they compare prompts and games, they do not label a
single sentence. guess_pairs() puts every guess next to the opponent's own reasoning on the move it guessed about,
for labelling by hand (right / partly / wrong).
"""
import json
import os
import re

GAME_KEY = ["model", "game", "prompt", "mode", "level", "seed"]
OTHER = {"White": "Black", "Black": "White", "X": "O", "O": "X"}
INTENTION_VERBS = r"(trying|aiming|planning|going for|setting up|building)"
BOARD = re.compile(r"\b(row \d|rank|the board|let me (check|visualize|parse|look)|wait\b)", re.I)
USER = re.compile(r"\bthe user\b", re.I)
OPPONENT = re.compile(r"\bopponent", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def intention_pattern(color):
    """Sentences where the other side (its name, "opponent" or "they") is, might be, seems to be... trying / aiming /
    planning / going for / setting up / building something."""
    return re.compile(rf"(\b{OTHER[color]}\b|\bopponent|\bthey\b)[^.]{{0,40}}"
                      rf"\b(is|are|was|(?:might|may|could|would|seems to)(?: [a-z]+)? be)\b[^.]{{0,15}}\b{INTENTION_VERBS}", re.I)


def clean(reasoning):
    return re.sub(r"</?think>", "", reasoning or "").strip()


def sentences(text):
    return [sentence.strip() for sentence in SENTENCE_END.split(text) if sentence.strip()]


def features(text, color):
    """The counts of one reasoning (text without the think tags; color: the side of the player who wrote it)."""
    parts = sentences(text)
    pattern = intention_pattern(color)
    guesses = list(dict.fromkeys(sentence for sentence in parts if pattern.search(sentence)))
    return dict(words=len(text.split()), sentences=len(parts), user_frame=bool(USER.search(text[:300])),
                board_sentences=sum(bool(BOARD.search(sentence)) for sentence in parts),
                waits=len(re.findall(r"\bWait\b", text)), opponent_words=len(OPPONENT.findall(text)),
                guesses=guesses)


def iter_games(*folders):
    """(record, thoughts) of every duel game with a .thoughts.jsonl, in the results folders."""
    for folder in folders:
        for root, _, files in os.walk(folder):
            for name in sorted(files):
                if not name.endswith(".thoughts.jsonl"):
                    continue
                record_file = os.path.join(root, name[:-len(".thoughts.jsonl")] + ".json")
                if not os.path.exists(record_file): # a game still running
                    continue
                with open(record_file) as f:
                    record = json.load(f)
                with open(os.path.join(root, name)) as f:
                    thoughts = [json.loads(line) for line in f if line.strip()]
                yield record, thoughts


def reasoning_table(*folders):
    """A pandas DataFrame with one row per reasoning: the game, who wrote it (player "agent" or "opponent",
    player_mode: its thinking mode), the move, the reasoning and its features()."""
    import pandas as pd
    rows = []
    for record, thoughts in iter_games(*folders):
        opponent_mode = record["level"][len("self-"):] if str(record["level"]).startswith("self-") else None
        for thought in thoughts:
            for number, entry in enumerate(thought["reasonings"]):
                text = clean(entry["reasoning"])
                rows.append(dict(model=record["model"], game=record["game"], prompt=record["prompt"],
                                 mode=record["mode"], level=record["level"], seed=record["seed"],
                                 ply=thought["ply"], color=thought["color"], player=thought["player"],
                                 player_mode=record["mode"] if thought["player"] == "agent" else opponent_mode,
                                 move=thought["move"], retry=number > 0, reasoning=text,
                                 **features(text, thought["color"])))
    return pd.DataFrame(rows)


def summary(table, by=("game", "prompt")):
    """The comparison table: per group, the reasonings and the averages of their features."""
    by = list(by)
    groups = table.groupby(by)
    result = groups.agg(reasonings=("words", "size"), words_median=("words", "median"))
    games = table.drop_duplicates(GAME_KEY + [name for name in by if name not in GAME_KEY]) # a game counts for each player
    result.insert(0, "games", games.groupby(by).size())
    result["user_frame_%"] = groups.user_frame.mean() * 100
    result["board_sentences_%"] = groups.board_sentences.sum() / groups.sentences.sum() * 100
    result["waits_median"] = groups.waits.median()
    has_guess = table.guesses.str.len() > 0
    result["with_guess_%"] = has_guess.groupby([table[name] for name in by]).mean() * 100
    result["guesses_per_reasoning"] = table.guesses.str.len().groupby([table[name] for name in by]).mean()
    result["opponent_word_per_reasoning"] = groups.opponent_words.mean()
    return result.round(2)


def guess_pairs(table, context=800):
    """One row per reasoning with at least one guess, next to the reasoning of the opponent on the move before
    (the move the guess is about), when the opponent reasoned on it: checkable is False otherwise (e.g. an r4
    opponent that did not think). opponent_reasoning_end is the last `context` characters, where the opponent decides.
    label is empty, to be filled by hand: right, partly, wrong, or unclear."""
    import pandas as pd
    key = GAME_KEY
    last = table[~table.duplicated(key + ["ply"], keep="last")] # after an illegal answer, the last reasoning
    previous = last.set_index(key + ["ply"])
    rows = []
    for _, row in table[table.guesses.str.len() > 0].iterrows():
        index = tuple(row[name] for name in key) + (row.ply - 1,)
        before = previous.loc[index] if index in previous.index else None
        rows.append(dict(**{name: row[name] for name in key}, ply=row.ply, color=row.color,
                         player_mode=row.player_mode, move=row.move, guesses=" | ".join(row.guesses),
                         checkable=before is not None,
                         opponent_move=None if before is None else before.move,
                         opponent_mode=None if before is None else before.player_mode,
                         opponent_reasoning_end=None if before is None else before.reasoning[-context:],
                         label=""))
    return pd.DataFrame(rows)


def outcome_table(*folders, by=("game", "prompt")):
    """The results of the duel games with reasonings: games, wins, draws, losses of the agent (after the
    adjudication, if any), how they ended, illegal answers per game (both players)."""
    import pandas as pd
    rows = []
    for record, _ in iter_games(*folders):
        result = record.get("adjudicated_result") or record["result"]
        rows.append(dict(game=record["game"], prompt=record["prompt"], mode=record["mode"], level=record["level"],
                         win=result == "win", draw=result == "draw", loss=result == "loss",
                         termination=record["termination"],
                         illegal=record["illegal"] + (record.get("opponent_counts") or {}).get("illegal", 0)))
    games = pd.DataFrame(rows)
    if games.empty:
        return games
    by = list(by)
    result = games.groupby(by).agg(games=("win", "size"), wins=("win", "sum"), draws=("draw", "sum"),
                                   losses=("loss", "sum"), illegal_per_game=("illegal", "mean"))
    endings = games.groupby(by).termination.agg(lambda t: ", ".join(f"{k} {v}" for k, v in t.value_counts().items()))
    return result.assign(endings=endings).round(2)
