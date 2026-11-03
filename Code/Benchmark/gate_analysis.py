"""When does thinking pay? The Connect Four moves judged by the solver (c4solver.py), per mode and gate.

One row per move of an agent (move_table): the solver's view of the position before it (how many moves keep the game
value: n_safe; critical: only one does), whether the player thought, whether the move was a blunder (it changed the
game-theoretic value) and, where there is one, the fast move the player would have played without thinking (the
oracle gate and shadow_fast_move record it) and whether that was a blunder.
The games played with the solver have a .moves.jsonl next to the record (run_duel.py); for the older games the
moves are judged again here from the record's history and the .thoughts.jsonl (which moves were thought).

Tables:
    mode_summary()     per mode: moves, thinking rate, blunder rate, blunder rate on critical and quiet positions, and
                       on the moves that were thought / not thought, tokens per game
    thinking_value()   on the thinking moves with a fast move: how often thinking changed the move, repaired a fast
                       blunder, or spoiled a good fast move
    gate_quality()     per gate mode: how often it fired, and precision / recall against two ground truths: the
                       position is critical (solver), and the fast move was a blunder (where a fast move exists)
    surprise_quality() per mode: how often the answers predicted the reply, how often the prediction came true, and
                       for the surprise gate the blunder rate on the moves where it was / was not surprised
    strength_table()   per mode and opponent: score, blunders per game, tokens per game (for strength_vs_tokens())
Graphs (graphs/): strength_vs_tokens, blunders_by_mode.

    python gate_analysis.py results results_new      # prints the tables of the Connect Four games in the folders
"""
import json
import os
import sys

from reasoning_analysis import GAME_KEY, player_result

GRAPHS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graphs")


def iter_connect_four(*folders):
    """(record, moves, path without extension) of every Connect Four duel record; moves: the lines of its
    .moves.jsonl, or None for the games played before the solver."""
    for folder in folders:
        for root, _, files in os.walk(folder):
            for name in sorted(files):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(root, name)) as f:
                    record = json.load(f)
                if record.get("game") != "connect_four" or record.get("source") != "benchmark-duel":
                    continue
                base = os.path.join(root, name[:-len(".json")])
                moves = None
                if os.path.exists(base + ".moves.jsonl"):
                    with open(base + ".moves.jsonl") as f:
                        moves = [json.loads(line) for line in f if line.strip()]
                yield record, moves, base


def _players(record):
    """player -> mode of the players that are agents of the model (the bots are not judged)."""
    players = {"agent": record["mode"]}
    if str(record["level"]).startswith("self-"):
        players["opponent"] = record["level"][len("self-"):]
    return players


def rebuild_moves(record, base, solver):
    """The move records of an old game (no .moves.jsonl): from the history, the solver, and the .thoughts.jsonl for
    the moves that were thought. Only position, quality and think are known; no gate, no fast move, no tokens."""
    from c4solver import analyze_scores, move_quality
    thought = set()
    if os.path.exists(base + ".thoughts.jsonl"):
        with open(base + ".thoughts.jsonl") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry["reasonings"]:
                        thought.add(entry["ply"])
    agent_side = 0 if record["color"] == "X" else 1
    players = _players(record)
    moves = []
    for index, move in enumerate(record["history"]):
        side = index % 2
        player = "agent" if side == agent_side else "opponent"
        if player not in players:
            continue
        scores = solver.scores(record["history"][:index])
        moves.append(dict(ply=index + 1, side=side, color="XO"[side], player=player, move=move, forced=None,
                          illegal=None, think=index + 1 in thought, gate=None, fast=None, attempts=None,
                          position=analyze_scores(scores) if scores else None,
                          quality=move_quality(scores, move) if scores else None, fast_quality=None, tokens=None))
    return moves


def move_table(*folders, rebuild=True):
    """A pandas DataFrame with one row per judged move of an agent of the model. rebuild: judge the old games with
    the solver (it must be compiled)."""
    import pandas as pd
    solver = None
    rows = []
    for record, moves, base in iter_connect_four(*folders):
        if moves is None:
            if not rebuild:
                continue
            if solver is None:
                from c4solver import shared_solver
                solver = shared_solver()
            moves = rebuild_moves(record, base, solver)
        players = _players(record)
        for move in moves:
            position, quality, fast_quality = move["position"], move["quality"], move.get("fast_quality")
            gate = move.get("gate")
            tokens = move.get("tokens") or {}
            rows.append(dict(**{name: record[name] for name in GAME_KEY}, player=move["player"],
                             player_mode=players.get(move["player"]), ply=move["ply"], color=move["color"],
                             move=move["move"], think=bool(move["think"]), rebuilt=move.get("tokens") is None,
                             judged=quality is not None,
                             n_safe=position["n_safe"] if position else None,
                             critical=position["n_safe"] == 1 if position else None,
                             best_class=position["best_class"] if position else None,
                             immediate_win=bool(position["immediate_win"]) if position else None,
                             cls=quality["cls"] if quality else None,
                             blunder=bool(quality["blunder"]) if quality else None,
                             lost=quality["lost"] if quality else None,
                             gate_fired=bool(move["think"]) if gate is not None else None,
                             gate_reason=gate.get("reason") if gate else None,
                             fast_move=move.get("fast_move"),
                             fast_blunder=bool(fast_quality["blunder"]) if fast_quality else None,
                             changed=(move.get("fast_move") != move["move"]) if move.get("fast_move") else None,
                             tokens_think=tokens.get("think"), tokens_gate=tokens.get("gate"),
                             tokens_normal=tokens.get("normal"), tokens_summary=tokens.get("summary"),
                             prediction=move.get("prediction"), prediction_regex=move.get("prediction_regex"),
                             reply=record["history"][move["ply"]] if move["ply"] < len(record["history"]) else None,
                             surprised=gate.get("surprised") if gate else None,
                             player_result=player_result(record, move["player"])))
    table = pd.DataFrame(rows)
    if not table.empty: # hit: the predicted reply was played (None where there is no prediction or no reply)
        known = table.prediction.notna() & table.reply.notna()
        table["hit"] = [(str(p) == str(r)) if ok else None
                        for p, r, ok in zip(table.prediction, table.reply, known)]
    return table


def _rate(series):
    series = series.dropna()
    return round(series.mean() * 100, 1) if len(series) else None


def _keys(by):
    """The grouping columns: a name or a sequence of names."""
    return [by] if isinstance(by, str) else list(by)


def mode_summary(table, by=("player_mode",)):
    """Per group: judged moves, thinking rate, blunder rate (all / critical positions / quiet positions / moves that
    were thought / moves that were not), illegal-first-answer rate is not known here (see the records)."""
    import pandas as pd
    judged = table[table.judged]
    rows = []
    for key, group in judged.groupby(_keys(by)):
        key = key if isinstance(key, tuple) else (key,)
        rows.append(dict(zip(_keys(by), key), games=len(group.drop_duplicates(GAME_KEY)), moves=len(group),
                         think_pct=_rate(group.think), critical_pct=_rate(group.critical),
                         blunder_pct=_rate(group.blunder),
                         blunder_critical_pct=_rate(group[group.critical == True].blunder),
                         blunder_quiet_pct=_rate(group[group.critical == False].blunder),
                         blunder_thought_pct=_rate(group[group.think].blunder),
                         blunder_fast_pct=_rate(group[~group.think].blunder),
                         think_on_critical_pct=_rate(group[group.critical == True].think),
                         think_on_quiet_pct=_rate(group[group.critical == False].think)))
    return pd.DataFrame(rows).set_index(_keys(by))


def thinking_value(table, by=("player_mode",)):
    """On the thinking moves that have a fast move (oracle gate, shadow): how often the move changed, the fast and
    the final blunder rates, repaired (fast blunder, final fine) and spoiled (fast fine, final blunder)."""
    import pandas as pd
    both = table[table.think & table.fast_blunder.notna() & table.blunder.notna()]
    rows = []
    for key, group in both.groupby(_keys(by)):
        key = key if isinstance(key, tuple) else (key,)
        rows.append(dict(zip(_keys(by), key), thinking_moves=len(group), changed_pct=_rate(group.changed),
                         fast_blunder_pct=_rate(group.fast_blunder), final_blunder_pct=_rate(group.blunder),
                         repaired=int((group.fast_blunder & ~group.blunder).sum()),
                         spoiled=int((~group.fast_blunder & group.blunder).sum()),
                         unrepaired=int((group.fast_blunder & group.blunder).sum())))
    return pd.DataFrame(rows).set_index(_keys(by)) if rows else pd.DataFrame()


def _precision_recall(fired, truth):
    import pandas as pd
    mask = fired.notna() & truth.notna()
    fired, truth = fired[mask].astype(bool), truth[mask].astype(bool)
    if not len(fired):
        return dict(n=0, precision=None, recall=None)
    tp = (fired & truth).sum()
    return dict(n=int(len(fired)), precision=round(tp / fired.sum() * 100, 1) if fired.sum() else None,
                recall=round(tp / truth.sum() * 100, 1) if truth.sum() else None)


def gate_quality(table, by=("player_mode",)):
    """Per gate mode: firing rate, and precision / recall of "fired" against "the position is critical" (every move)
    and against "the fast move was a blunder" (the moves with a fast move: all of them for the oracle gate, the
    thinking moves with shadow_fast_move for the others)."""
    import pandas as pd
    gated = table[table.gate_fired.notna()]
    rows = []
    for key, group in gated.groupby(_keys(by)):
        key = key if isinstance(key, tuple) else (key,)
        critical = _precision_recall(group.gate_fired, group.critical)
        fast = _precision_recall(group.gate_fired, group.fast_blunder)
        rows.append(dict(zip(_keys(by), key), moves=len(group), fired_pct=_rate(group.gate_fired),
                         critical_precision=critical["precision"], critical_recall=critical["recall"],
                         fast_blunder_n=fast["n"], fast_blunder_precision=fast["precision"],
                         fast_blunder_recall=fast["recall"]))
    return pd.DataFrame(rows).set_index(_keys(by)) if rows else pd.DataFrame()


def surprise_quality(table, by=("player_mode",)):
    """Per group: moves, moves with a prediction of the reply (predicted_pct), predictions that came true (hit_pct),
    agreement between the extractor and the regex reading of the answer where both exist (agree_pct), and for the
    surprise gate the thinking rate and the blunder rate when it was surprised, when the reply was as expected, and
    when it had no expectation."""
    import pandas as pd
    rows = []
    for key, group in table.groupby(_keys(by)):
        key = key if isinstance(key, tuple) else (key,)
        both = group[group.prediction.notna() & group.prediction_regex.notna()]
        surprised = group[group.surprised == True]
        expected = group[group.surprised == False]
        blind = group[group.surprised.isna() & group.gate_reason.eq("no expectation")]
        rows.append(dict(zip(_keys(by), key), moves=len(group), predicted_pct=_rate(group.prediction.notna()),
                         hit_pct=_rate(group.hit), agree_pct=_rate(both.prediction.astype(str) == both.prediction_regex.astype(str)),
                         surprised_pct=_rate(group.surprised.dropna() == True) if group.surprised.notna().any() else None,
                         blunder_surprised_pct=_rate(surprised.blunder), blunder_expected_pct=_rate(expected.blunder),
                         blunder_no_expectation_pct=_rate(blind.blunder),
                         critical_when_surprised_pct=_rate(surprised.critical), critical_when_expected_pct=_rate(expected.critical)))
    return pd.DataFrame(rows).set_index(_keys(by)) if rows else pd.DataFrame()


def strength_table(*folders, by=("mode", "level", "prompt"), moves=None):
    """Per group of records: games, score of the agent, blunders per game (agent / opponent), thinking rate, tokens
    generated per game by the agent (its cost) and its gate tokens. moves: a move_table() of the same folders, for the
    blunders and thinking rate of the games played without the solver (the records of the others have them)."""
    import pandas as pd
    counts = {}
    if moves is not None and not moves.empty:
        judged = moves[moves.judged]
        for key, group in judged.groupby(GAME_KEY + ["player"]):
            counts[key] = dict(blunders=int(group.blunder.sum()), think_rate=round(group.think.mean(), 3))
    rows = []
    for record, _, _ in iter_connect_four(*folders):
        tokens = record.get("tokens") or {}
        key = tuple(record[name] for name in GAME_KEY)
        agent, opponent = counts.get(key + ("agent",), {}), counts.get(key + ("opponent",), {})
        rows.append(dict(**{name: record[name] for name in GAME_KEY}, score=record["score"],
                         win=record["result"] == "win",
                         blunders=record.get("blunders", agent.get("blunders")),
                         opponent_blunders=record.get("opponent_blunders", opponent.get("blunders")),
                         think_rate=record.get("think_rate", agent.get("think_rate")), tokens=tokens.get("generated"),
                         tokens_gate=tokens.get("generated_gate", 0), tokens_shadow=tokens.get("generated_shadow", 0),
                         tokens_summary=tokens.get("generated_summary", 0),
                         predictions=record.get("predictions"), prediction_hits=record.get("prediction_hits"),
                         repaired=record.get("repaired"), spoiled=record.get("spoiled")))
    games = pd.DataFrame(rows)
    if games.empty:
        return games
    result = games.groupby(_keys(by)).agg(games=("score", "size"), score=("score", "mean"), wins=("win", "sum"),
                                         blunders=("blunders", "mean"), opponent_blunders=("opponent_blunders", "mean"),
                                         think_rate=("think_rate", "mean"), ktokens=("tokens", lambda t: t.mean() / 1000),
                                         ktokens_gate=("tokens_gate", lambda t: t.mean() / 1000),
                                         ktokens_summary=("tokens_summary", lambda t: t.mean() / 1000),
                                         repaired=("repaired", "mean"), spoiled=("spoiled", "mean"),
                                         predictions=("predictions", "mean"), prediction_hits=("prediction_hits", "mean"))
    return result.round(2)


def strength_vs_tokens(strength, path=None, title="Connect Four: score against tokens per game"):
    """One point per mode (and opponent): the agent's score against the thousands of tokens it generated per game."""
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots(figsize=(6, 4))
    table = strength.reset_index()
    levels = sorted(table.level.unique()) if "level" in table else [None]
    markers = "osD^v<>"
    for marker, level in zip(markers, levels):
        part = table if level is None else table[table.level == level]
        axis.scatter(part.ktokens, part.score, marker=marker, label=f"vs {level}" if level else None)
        for _, row in part.iterrows():
            axis.annotate(row["mode"], (row.ktokens, row.score), textcoords="offset points", xytext=(4, 4), fontsize=8)
    axis.set_xlabel("tokens generated per game by the agent (thousands)")
    axis.set_ylabel("score (win 1, draw 0.5, loss 0)")
    axis.set_ylim(-0.05, 1.05)
    axis.set_title(title)
    if len(levels) > 1:
        axis.legend()
    axis.grid(alpha=0.3)
    figure.tight_layout()
    if path:
        figure.savefig(path)
    return figure


def blunders_by_mode(summary, path=None):
    """Blunder rate per mode: all moves, critical positions, quiet positions."""
    import matplotlib.pyplot as plt
    import numpy as np
    table = summary.reset_index()
    figure, axis = plt.subplots(figsize=(7, 4))
    x = np.arange(len(table))
    width = 0.27
    for offset, column, label in ((-width, "blunder_pct", "all moves"), (0, "blunder_critical_pct", "critical (1 safe move)"),
                                  (width, "blunder_quiet_pct", "quiet")):
        axis.bar(x + offset, table[column].fillna(0), width, label=label)
    axis.set_xticks(x)
    axis.set_xticklabels(table.player_mode)
    axis.set_ylabel("blunders (% of moves)")
    axis.set_title("Connect Four: moves that change the game value, by mode")
    axis.legend()
    axis.grid(axis="y", alpha=0.3)
    figure.tight_layout()
    if path:
        figure.savefig(path)
    return figure


def main(folders):
    import pandas as pd
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    table = move_table(*folders)
    if table.empty:
        print("no Connect Four duel games in", folders)
        return
    print(f"{len(table)} moves of {len(table.drop_duplicates(GAME_KEY))} games "
          f"({table.rebuilt.sum()} moves judged now from the histories)\n")
    print("Per mode (all prompts):")
    print(mode_summary(table), "\n")
    print("Per mode and prompt:")
    print(mode_summary(table, by=("player_mode", "prompt")), "\n")
    value = thinking_value(table)
    if not value.empty:
        print("What thinking changed (moves with a fast move):")
        print(value, "\n")
    quality = gate_quality(table)
    if not quality.empty:
        print("The gates against the solver:")
        print(quality, "\n")
    if table.prediction.notna().any():
        print("Predictions of the reply and the surprise gate:")
        print(surprise_quality(table[table.prediction.notna() | table.surprised.notna()]), "\n")
    strength = strength_table(*folders, by=("mode", "level"), moves=table)
    print("Strength and cost (agent's games, all prompts together):")
    print(strength)
    os.makedirs(GRAPHS, exist_ok=True)
    strength_vs_tokens(strength, os.path.join(GRAPHS, "strength_vs_tokens.pdf"))
    blunders_by_mode(mode_summary(table), os.path.join(GRAPHS, "blunders_by_mode.pdf"))
    print(f"\ngraphs saved in {GRAPHS}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["results"])
