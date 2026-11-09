"""Why keeping the last reasoning (lr4) helps in the cooking games and not in Connect Four: the turns of both games
read side by side, from the logs.

A turn is: what the game said, whether the agent reasoned, the reasoning, the action, and how good the action was
(cooking: the score before and after, whether the game understood the command; Connect Four: the solver's verdict).
From the turns:
    turn_table()        one row per turn of an agent, with `since`: turns since its last reasoning (0: it reasons now;
                        None: no reasoning yet) and `visible`: reasonings it can read in its context on this turn.
    reasoning_table()   one row per reasoning, with the actions that follow it until the next reasoning, and how
                        many of them it had announced (cooking: `announced`, see plan_overlap()).
    sample_reasonings() a fixed random sample of reasonings with what happened after, to label by hand or with a
                        judge (labels/memory_sample.jsonl; the rubric: RUBRIC).

    python memory_analysis.py [results folder]      # the tables
"""
import json
import os
import random
import re
import sys

from records import iter_records

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
LOG_MARK = re.compile(r"^(GAME \+{10,}|AGENT -{10,}|SELF-EVALUATION: [+-]{10,}|CONTEXT INITIALIZED [+-]{10,})\s*$", re.M)
STATUS = re.compile(r">\s+-= (?P<room>[^=]+?) =-(?P<score>\d+)/(?P<moves>\d+)\s*$", re.M)
THINK_PROMPT = re.compile(r"\n(THINK about how to win|Think about|THINK )", re.S) # the self-evaluation request
# what the game answers when it did not understand or could not do the command
REJECTED = re.compile(r"(?i)that's not a verb i recognise|i didn't understand|you can't see any such thing|"
                      r"i only understood you as far as|you can't go that way|not something you can|"
                      r"you need to take|you can't|can't see|i beg your pardon|that's not something|"
                      r"is already|but you aren't|you haven't got|you seem to want|nothing happens|what do you want")
STOPWORDS = set("the a an with from on in to of and at into up down using use it some".split())


def log_path(record, path):
    """The log of a record: next to it (benchmark) or where the old experiment left it (legacy, from the repo)."""
    log = record.get("log")
    if not log:
        return None
    for candidate in (os.path.join(os.path.dirname(path), log), os.path.join(REPO, log)):
        if os.path.exists(candidate):
            return candidate
    return None


def parse_cooking_log(text):
    """The turns of the first game of a textual-game log: dicts with obs (what the game said, without the
    self-evaluation request), think, reasoning (None on normal turns), command, room, score (before the action)."""
    parts = LOG_MARK.split(text)
    turns, obs, started = [], None, 0
    for mark, body in zip(parts[1::2], parts[2::2]):
        kind = mark.split()[0]
        if kind == "CONTEXT":
            started += 1
            if started > 1:
                break
        elif kind == "GAME":
            obs = body
        elif obs is not None:
            think = kind == "SELF-EVALUATION:"
            reasoning = None
            if think:
                reasoning, _, body = body.rpartition("</think>")
                reasoning = reasoning.replace("<think>", "").strip()
            status = list(STATUS.finditer(obs))
            shown = THINK_PROMPT.split(obs)[0] if think else obs
            command = " ".join(body.split("\n")[0].split()) if body.strip() else ""
            command = command or " ".join(body.split())
            turns.append(dict(obs=STATUS.sub("", shown).strip(), think=think, reasoning=reasoning,
                              command=command.strip().lower(), answer=body.strip(),
                              room=status[-1]["room"] if status else None,
                              score=int(status[-1]["score"]) if status else None))
            obs = None
    for turn, following in zip(turns, turns[1:] + [None]):
        turn["reply"] = following["obs"] if following else None
        after = following["score"] if following else None
        turn["gain"] = (after - turn["score"]) if after is not None and turn["score"] is not None else None
        turn["rejected"] = bool(REJECTED.search(turn["reply"][:300])) if turn["reply"] else None
    return turns


def content_words(command):
    return [word for word in re.findall(r"[a-z]+", command.lower()) if word not in STOPWORDS]


def announced(command, reasoning):
    """Whether a reasoning had announced this command: every content word of the command is in the reasoning
    ("slice green apple with knife": slice, green, apple, knife). A proxy: it does not read the order of a plan."""
    words = content_words(command)
    text = reasoning.lower()
    return bool(words) and all(re.search(rf"\b{re.escape(word)}", text) for word in words)


def _annotate(turns, keep):
    """since / visible / last (the last reasoning written, whether or not it is still in the context)."""
    since, count, last = None, 0, None
    for index, turn in enumerate(turns):
        if turn["think"]:
            turn["since"] = 0
            turn["visible"] = {"all": count, "none": 0, "last": 0, "previous": min(count, 1)}[keep]
            count += 1
            since, last = 0, index
        else:
            since = None if since is None else since + 1
            turn["since"] = since
            turn["visible"] = {"all": count, "none": 0, "last": min(count, 1), "previous": min(count, 1)}[keep]
        turn["last_reasoning"] = last if not turn["think"] else None
    return turns


def keep_of(mode):
    from agent import parse_mode
    return parse_mode(mode)["keep"] if mode != "0" else "none"


def iter_cooking(*folders, game="cooking"):
    """(record, turns) of the textual games that have a log."""
    for folder in folders:
        for root, _, files in os.walk(folder):
            for name in sorted(files):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(root, name)
                with open(path) as f:
                    record = json.load(f)
                if record.get("game") != game:
                    continue
                log = log_path(record, path)
                if log is None:
                    continue
                with open(log, errors="replace") as f:
                    turns = parse_cooking_log(f.read())
                if turns:
                    yield record, _annotate(turns, keep_of(record["mode"]))


def cooking_turns(*folders):
    import pandas as pd
    rows = []
    for record, turns in iter_cooking(*folders):
        seen = {}
        for index, turn in enumerate(turns):
            last = turns[turn["last_reasoning"]]["reasoning"] if turn["last_reasoning"] is not None else None
            repeated = seen.get(turn["command"])
            rows.append(dict(game="cooking", model=record["model"], prompt=record["prompt"], mode=record["mode"],
                             level=str(record["level"]), seed=record["seed"], turn=index, think=turn["think"],
                             since=turn["since"], visible=turn["visible"], command=turn["command"],
                             gain=turn["gain"], good=(turn["gain"] or 0) > 0 if turn["gain"] is not None else None,
                             rejected=turn["rejected"],
                             repeat=repeated is not None and index - repeated <= 6, # same command within 6 turns
                             announced=announced(turn["command"], last) if last else None,
                             reasoning_chars=len(turn["reasoning"]) if turn["think"] else None))
            seen[turn["command"]] = index
    return pd.DataFrame(rows)


def connect_four_turns(*folders):
    """The same table for Connect Four, from gate_analysis.move_table(): good = the move is not a blunder."""
    import gate_analysis as ga
    moves = ga.move_table(*folders, rebuild=False)
    moves = moves[moves.judged & ~moves.prompt.str.contains("+", regex=False)].copy()
    moves = moves.sort_values(["model", "prompt", "mode", "level", "seed", "player", "ply"])
    rows = []
    for key, game in moves.groupby(["model", "prompt", "mode", "level", "seed", "player"], sort=False):
        mode = game.player_mode.iloc[0]
        if mode is None or re.match(r"l?g", str(mode)):
            continue
        keep = keep_of(mode)
        turns = [dict(think=bool(row.think), command=row.move, blunder=bool(row.blunder),
                      critical=bool(row.critical)) for row in game.itertuples()]
        _annotate(turns, keep)
        for index, turn in enumerate(turns):
            last = turns[turn["last_reasoning"]]["command"] if turn["last_reasoning"] is not None else None
            rows.append(dict(game="connect_four", model=key[0], prompt=key[1], mode=mode, level=key[3], seed=key[4],
                             player=key[5], turn=index, think=turn["think"], since=turn["since"],
                             visible=turn["visible"], command=turn["command"], good=not turn["blunder"],
                             critical=turn["critical"],
                             repeat=last is not None and last == turn["command"], # the column of its last reasoning
                             announced=None, reasoning_chars=None))
    import pandas as pd
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import pandas as pd
    pd.set_option("display.width", 200)
    folder = sys.argv[1] if len(sys.argv) > 1 else "results"
    table = cooking_turns(folder)
    print(table.groupby(["model", "mode"]).agg(games=("seed", lambda s: len(set(zip(table.loc[s.index, "level"], s)))),
                                               turns=("turn", "size"), thinks=("think", "sum"),
                                               good=("good", "mean"), rejected=("rejected", "mean"),
                                               repeat=("repeat", "mean")).round(3).to_string())


# --- the sample to label ---------------------------------------------------------------------------------------

RUBRIC = """
You read one reasoning of a language model playing a game, with what it saw before and what happened after, and you
describe WHAT THE REASONING CONTAINS THAT COULD STILL BE USEFUL ON THE NEXT TURNS, and whether it was.
The model calls itself "the user" or "I": both are the player. Judge only what is written.

A. About the reasoning itself
  type            the ONE main thing the reasoning does:
      PLAN      lays out its own next actions in order, at least two beyond the one it plays now
                ("take the knife, then slice the pepper, then fry it").
      TRACK     mostly re-establishes where it stands on a known task: what is done, what is left, what is next
                (one next step, no further sequence).
      REPAIR    mostly works out why its recent actions failed (a command the game did not understand, a missing
                object, a loop) and chooses a different way to do the SAME thing.
      EVALUATE  weighs the candidate actions of THIS turn only (threats, lines, options) and picks one; nothing
                in it is about the turns after.
      LOST      reaches its action without a usable argument: it contradicts itself, goes in circles, or decides
                on a ground that is plainly false.
  future_steps    how many of its OWN later actions it names beyond the action it plays now: 0, 1 or 2 (2 = two or
                  more).
  fallbacks       true if it names other things to try in case the chosen action fails ("if slice does not work,
                  try dice").
  durable_facts   true if it states facts that stay true on the next turns whatever happens: where things are,
                  which commands the game accepts, what the recipe asks, what is already done. A description of a
                  board position that the very next move changes is NOT durable.
  anticipates     true if it says what the other side (the opponent, or the game) will do next AND what it will
                  do in answer ("if O plays 3, I win with 4"). Only for a concrete reply, not "O may block".
  state_error     true if it misreads the current state in a way you can check against what the game showed
                  (wrong column or row of a disc, an object it does not hold, a room it is not in);
                  false if what you can check is right; "unclear" if you cannot check.

B. About what happened after (the actions listed under THEN were played WITHOUT reasoning, before the next one)
  carried         FOLLOWED  the next actions pursue what the reasoning concluded (the same sub-goal, plan or
                            option it named, also with other words);
                  DIVERGED  they do something the reasoning did not point to;
                  REPEATED  they just repeat the action of the reasoning turn (same command / same column) as if
                            nothing had happened.
  validity        VALID   what the reasoning concluded still fitted the situation on the next turns;
                  STALE   it fitted when written, but the situation changed (the other side moved, the state
                          changed) so that it no longer applied;
                  WRONG   it was wrong from the start: a false fact, or a plan that could not work.
  helped          true if the actions after it made progress (cooking: a point scored or a new command accepted
                  that moves the recipe on; Connect Four: no blunder among them), false otherwise.
  note            one short sentence: the reason for `type` and `validity`.

Answer for every item with one JSON object per line:
{"id": "...", "type": "...", "future_steps": 0, "fallbacks": false, "durable_facts": false, "anticipates": false,
 "state_error": false, "carried": "...", "validity": "...", "helped": false, "note": "..."}
"""


def _flat(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def cooking_items(*folders, min_chars=200):
    """Every non-empty reasoning of the cooking games followed by at least one turn without reasoning."""
    for record, turns in iter_cooking(*folders):
        if record["prompt"] != "CoT":
            continue
        for index, turn in enumerate(turns):
            if not turn["think"] or len(turn["reasoning"]) < min_chars:
                continue
            after = []
            for following in turns[index + 1:]:
                if following["think"]:
                    break
                after.append(following)
            if not after:
                continue
            lines = ["GAME: a text adventure. The player must find a cookbook, read the recipe, collect and prepare "
                     "the ingredients, and eat the meal. Score now: "
                     f"{turn['score']} of {record['max_score']}.",
                     "LAST ACTIONS BEFORE (action -> what the game answered):"]
            lines += [f"  {t['command']} -> {_flat(t['reply'], 160)}" for t in turns[max(0, index - 5):index]]
            lines += ["WHAT THE GAME SHOWS NOW:", "  " + _flat(turn["obs"], 900), "REASONING:", turn["reasoning"][:7000],
                      f"ACTION PLAYED: {turn['command']} -> {_flat(turn['reply'], 220)}"
                      + (" [point scored]" if (turn["gain"] or 0) > 0 else ""), "THEN (without reasoning):"]
            lines += [f"  {t['command']} -> {_flat(t['reply'], 160)}" + (" [point scored]" if (t["gain"] or 0) > 0 else "")
                      for t in after[:6]]
            yield dict(game="cooking", model=record["model"], mode=record["mode"], level=str(record["level"]),
                       seed=record["seed"], turn=index, chars=len(turn["reasoning"]),
                       after_good=sum((t["gain"] or 0) > 0 for t in after), after_n=len(after), text="\n".join(lines))


def connect_four_items(*folders, min_chars=200):
    """The same for Connect Four: the reasonings of the clock modes with the solver's verdict on what followed."""
    import gate_analysis as ga
    from duel_games import make_game
    for record, moves, base in ga.iter_connect_four(*folders):
        if moves is None or "+" in record["prompt"] or not os.path.exists(base + ".thoughts.jsonl"):
            continue
        modes = {"agent": record["mode"], "opponent": record["level"][len("self-"):] if record["level"].startswith("self-") else None}
        with open(base + ".thoughts.jsonl") as f:
            thoughts = [json.loads(line) for line in f if line.strip()]
        for thought in thoughts:
            mode = modes.get(thought["player"])
            if mode is None or not re.fullmatch(r"(e|r|er|lr|l)\d+", mode) or not thought["reasonings"]:
                continue
            reasoning = thought["reasonings"][-1]["reasoning"].replace("<think>", "").replace("</think>", "").strip()
            own = [move for move in moves if move["player"] == thought["player"]]
            now = [move for move in own if move["ply"] == thought["ply"]]
            if len(reasoning) < min_chars or not now or not now[0]["position"]:
                continue
            after = []
            for move in own:
                if move["ply"] > thought["ply"]:
                    if move["think"]:
                        break
                    after.append(move)
            if not after:
                continue
            game = make_game("connect_four", **{k: v for k, v in (record.get("game_options") or {}).items()})
            for move in record["history"][:thought["ply"] - 1]:
                game.play(move)
            history = record["history"]

            def verdict(move):
                quality, position = move["quality"] or {}, move["position"] or {}
                reply = history[move["ply"]] if move["ply"] < len(history) else None
                return (f"plays {move['move']}: " + ("BLUNDER" if quality.get("blunder") else "fine")
                        + f" (the solver: columns that keep the result {', '.join(position.get('safe', []))};"
                          f" position {position.get('best_class')})"
                        + (f"; the other side answers {reply}" if reply else "; the game ends"))
            body = reasoning if len(reasoning) <= 9000 else reasoning[:4500] + "\n[... cut ...]\n" + reasoning[-4500:]
            lines = ["GAME: Connect Four against another player. The player is " + thought["color"]
                     + ". Columns 1-7; the bottom line of the board is the bottom row.",
                     "MOVES SO FAR (X first): " + " ".join(history[:thought["ply"] - 1]),
                     "WHAT THE GAME SHOWS NOW:", game._text(), "REASONING:", body,
                     "ACTION PLAYED: " + verdict(now[0]), "THEN (without reasoning):"]
            lines += ["  " + verdict(move) for move in after[:6]]
            yield dict(game="connect_four", model=record["model"], mode=mode, level=record["level"],
                       seed=record["seed"], turn=thought["ply"], chars=len(reasoning), prompt=record["prompt"],
                       after_good=sum(not (m["quality"] or {}).get("blunder") for m in after), after_n=len(after),
                       text="\n".join(lines))


def sample_reasonings(folder="results", seed=0, quotas=None):
    """A fixed stratified sample: (game, model, mode) -> how many. The ids are shuffled and say nothing of the mode."""
    quotas = quotas or {("cooking", "Qwen/Qwen3-4B", "lr4"): 40, ("cooking", "Qwen/Qwen3-4B", "er4"): 20,
                        ("cooking", "Qwen/Qwen3-8B-FP8", "lr4"): 40, ("cooking", "Qwen/Qwen3-8B-FP8", "er4"): 20,
                        ("connect_four", "Qwen/Qwen3-4B", "lr4"): 40, ("connect_four", "Qwen/Qwen3-4B", "er4"): 20}
    pools = {}
    for item in list(cooking_items(folder)) + list(connect_four_items(folder)):
        pools.setdefault((item["game"], item["model"], item["mode"]), []).append(item)
    rng = random.Random(seed)
    chosen = []
    for key, count in quotas.items():
        pool = sorted(pools.get(key, []), key=lambda item: (item["level"], item["seed"], item["turn"], item.get("prompt", "")))
        chosen += rng.sample(pool, min(count, len(pool)))
    rng.shuffle(chosen)
    for number, item in enumerate(chosen):
        item["id"] = f"m{number:03d}"
    return chosen, {key: len(pool) for key, pool in pools.items()}
