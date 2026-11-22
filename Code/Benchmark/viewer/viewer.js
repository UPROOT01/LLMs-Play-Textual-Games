// Viewer of the benchmark's results, opened from the disk (file://): no server, no build step.
// The page cannot read files by itself, so the user picks the results folder once; the browser then gives the list of
// the files, and a file is read only when it is needed (the records of the chosen model/game/prompt, the log of the
// opened game). The layout of the folder is the one of records.py:
//     <results>/<model>/<game>/<prompt>/<mode>/level<L>-seed<S>.json  (+ .log, .opponent.log, .transcript.md, ...)
"use strict";

const EXTENSIONS = /^(.*?)\.(opponent\.log|transcript\.md|moves\.jsonl|thoughts\.jsonl|json|log)$/;
const TABS = [["log", "Log"], ["opponent.log", "Opponent's log"], ["transcript.md", "Transcript"],
              ["moves.jsonl", "Moves"], ["thoughts.jsonl", "Thoughts"], ["json", "Record"]];
const LEVELS = ["model", "game", "prompt", "mode"];

const tree = {};             // model -> game -> prompt -> mode -> base name -> {extension: File}
const oldLogs = new Map();   // "Experiments/…/x.log" -> File: the logs of the imported old games (the record's "log")
const records = new Map();   // "model/game/prompt/mode/base" -> record (parsed json), or null if unreadable
const selection = { model: null, game: null, prompt: null, mode: null, base: null, tab: "log" };
const $ = id => document.getElementById(id);

// ---------------------------------------------------------------- helpers

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function natural(a, b) {
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function modeOrder(a, b) { // "0" first, then by name with the numbers in order (er2 before er10)
  return (a === "0" ? -1 : 0) - (b === "0" ? -1 : 0) || natural(a, b);
}

function parseBase(base) { // "level3-seed1" -> {level: "3", seed: "1"}; "levelself-e1-seed1" -> {level: "self-e1", ...}
  const match = base.match(/^level(.+)-seed(\d+)$/);
  return match ? { level: match[1], seed: match[2] } : { level: base, seed: "" };
}

function mean(values) {
  const numbers = values.filter(v => typeof v === "number" && !Number.isNaN(v));
  return numbers.length ? numbers.reduce((a, b) => a + b, 0) / numbers.length : null;
}

function sem(values) {
  const numbers = values.filter(v => typeof v === "number");
  if (numbers.length < 2) return null;
  const m = mean(numbers);
  return Math.sqrt(numbers.reduce((a, b) => a + (b - m) ** 2, 0) / (numbers.length - 1) / numbers.length);
}

function fmt(value, digits = 3) {
  return value === null || value === undefined ? "–" : Number(value).toFixed(digits);
}

function fmtTokens(value) {
  if (value === null || value === undefined) return "–";
  return value >= 1000 ? (value / 1000).toFixed(1) + "k" : String(Math.round(value));
}

function remember(key, value) { try { localStorage.setItem("viewer." + key, value); } catch (error) { /* private window */ } }
function recall(key) { try { return localStorage.getItem("viewer." + key); } catch (error) { return null; } }

function key(model, game, prompt, mode, base) { return [model, game, prompt, mode, base].join("/"); }

// ---------------------------------------------------------------- the folder

function indexFiles(files) {
  for (const name of Object.keys(tree)) delete tree[name];
  records.clear();
  oldLogs.clear();
  let count = 0;
  const anyResults = [...files].some(file => /(^|\/)results[^/]*\//i.test(file.webkitRelativePath || ""));
  for (const file of files) {
    const relative = file.webkitRelativePath || file.name;
    const old = relative.indexOf("Experiments/");
    if (old >= 0 && relative.endsWith(".log")) { oldLogs.set(relative.slice(old), file); continue; }
    const parts = relative.split("/");
    // the five last parts are model/game/prompt/mode/file; inside a folder named results*, exactly those after it
    let start = -1;
    for (let i = parts.length - 6; i >= 0; i--) if (/^results/i.test(parts[i])) { start = i + 1; break; }
    if (start < 0 && !anyResults && parts.length >= 5) start = parts.length - 5; // a model's folder was chosen
    if (start < 0 || parts.length - start !== 5) continue;
    const [model, game, prompt, mode, name] = parts.slice(start);
    const match = name.match(EXTENSIONS);
    if (!match) continue;
    (((((tree[model] ??= {})[game] ??= {})[prompt] ??= {})[mode] ??= {})[match[1]] ??= {})[match[2]] = file;
    count++;
  }
  // a game is a base name with a record: files without one (a log alone) are still shown, with no score
  return count;
}

function node(depth) { // the part of the tree chosen down to depth levels
  let current = tree;
  for (let i = 0; i < depth; i++) current = current?.[selection[LEVELS[i]]];
  return current || {};
}

function fillSelectors() {
  LEVELS.forEach((level, depth) => {
    const options = Object.keys(node(depth)).sort(level === "mode" ? modeOrder : natural);
    if (!options.includes(selection[level])) {
      const saved = recall(level);
      selection[level] = options.includes(saved) ? saved : options[0] ?? null;
    }
    $(level).innerHTML = options.map(option =>
      `<option value="${escapeHtml(option)}"${option === selection[level] ? " selected" : ""}>${escapeHtml(option)}</option>`).join("");
  });
}

async function loadRecords() { // the records of every mode of the chosen model, game and prompt
  const { model, game, prompt } = selection;
  const jobs = [];
  for (const [mode, games] of Object.entries(node(3))) {
    for (const [base, files] of Object.entries(games)) {
      const id = key(model, game, prompt, mode, base);
      if (files.json && !records.has(id)) jobs.push([id, files.json]);
    }
  }
  for (let i = 0; i < jobs.length; i += 200) { // in batches: thousands of files open at once stall the browser
    $("status").textContent = `reading the records… ${i} / ${jobs.length}`;
    await Promise.all(jobs.slice(i, i + 200).map(async ([id, file]) => {
      try { records.set(id, JSON.parse(await file.text())); } catch (error) { records.set(id, null); }
    }));
  }
  $("status").textContent = "";
}

function record(mode, base) {
  return records.get(key(selection.model, selection.game, selection.prompt, mode, base)) || null;
}

// ---------------------------------------------------------------- the table of the modes

function isDuel(recordsOfMode) { return recordsOfMode.some(r => r && "result" in r); }

function modeRow(mode) {
  const games = node(3)[mode] || {};
  const list = Object.keys(games).map(base => record(mode, base)).filter(Boolean);
  const tokens = name => list.map(r => r.tokens?.[name]);
  const scores = list.map(r => r.norm_score);
  const thinking = tokens("generated_thinking").reduce((a, b) => a + (b || 0), 0);
  const turns = tokens("think_turns").reduce((a, b) => a + (b || 0), 0);
  return {
    mode, games: list.length, score: mean(scores), sem: sem(scores),
    won: list.filter(r => r.norm_score === 1).length,
    loops: list.filter(r => r.loop_stopped === true).length,
    steps: mean(list.map(r => r.steps)),
    tokens: mean(tokens("generated")), thinkTurns: mean(tokens("think_turns")),
    perReasoning: turns ? thinking / turns : null,
    duel: isDuel(list),
    wins: list.filter(r => r.result === "win").length, draws: list.filter(r => r.result === "draw").length,
    losses: list.filter(r => r.result === "loss").length,
    illegal: mean(list.map(r => r.illegal)), thinkRate: mean(list.map(r => r.think_rate)),
    blunderRate: mean(list.map(r => r.blunder_rate)),
  };
}

function renderModes() {
  const rows = Object.keys(node(3)).sort(modeOrder).map(modeRow);
  const duel = rows.some(row => row.duel);
  const columns = duel
    ? [["games", "games"], ["score", "score"], ["± s.e.", "sem"], ["W / D / L", "wdl"], ["illegal / game", "illegal"],
       ["thinking rate", "thinkRate"], ["blunder rate", "blunderRate"], ["tokens / game", "tokens"],
       ["tokens / reasoning", "perReasoning"]]
    : [["games", "games"], ["score", "score"], ["± s.e.", "sem"], ["won", "won"], ["loop-stopped", "loops"],
       ["steps", "steps"], ["tokens / game", "tokens"], ["thinking turns", "thinkTurns"],
       ["tokens / reasoning", "perReasoning"]];
  const cell = (row, name) => {
    switch (name) {
      case "score": return `<span class="bar"><i style="width:${Math.round(100 * (row.score || 0))}%"></i></span>${fmt(row.score)}`;
      case "sem": return fmt(row.sem);
      case "won": case "loops": // a share of the games: the modes do not have the same number of games
        return row.games ? `${Math.round(100 * row[name] / row.games)}% <small>(${row[name]})</small>` : "–";
      case "wdl": return `${row.wins} / ${row.draws} / ${row.losses}`;
      case "tokens": case "perReasoning": return fmtTokens(row[name]);
      case "steps": case "thinkTurns": return fmt(row[name], 1);
      case "illegal": case "thinkRate": case "blunderRate": return fmt(row[name], 2);
      default: return row[name];
    }
  };
  $("overviewTitle").textContent = `${selection.model} · ${selection.game} · ${selection.prompt}`;
  $("modes").innerHTML =
    `<thead><tr><th>mode</th>${columns.map(([title]) => `<th>${title}</th>`).join("")}</tr></thead><tbody>` +
    rows.map(row => `<tr data-mode="${escapeHtml(row.mode)}" class="${row.mode === selection.mode ? "selected" : ""}">` +
      `<td class="mono">${escapeHtml(row.mode)}</td>${columns.map(([, name]) => `<td>${cell(row, name)}</td>`).join("")}</tr>`).join("") +
    "</tbody>";
}

// ---------------------------------------------------------------- the grid of the games of a mode

function basesOfMode() {
  return Object.keys(node(4)).sort((a, b) => {
    const x = parseBase(a), y = parseBase(b);
    return natural(x.level, y.level) || natural(x.seed, y.seed);
  });
}

function renderGrid() {
  const bases = basesOfMode();
  const levels = [...new Set(bases.map(base => parseBase(base).level))].sort(natural);
  const seeds = [...new Set(bases.map(base => parseBase(base).seed))].sort(natural);
  const byCell = Object.fromEntries(bases.map(base => { const p = parseBase(base); return [p.level + "/" + p.seed, base]; }));
  $("gamesTitle").textContent = `mode ${selection.mode} · ${bases.length} games`;
  let html = `<thead><tr><th>level \\ seed</th>${seeds.map(seed => `<th>${escapeHtml(seed)}</th>`).join("")}<th>mean</th></tr></thead><tbody>`;
  for (const level of levels) {
    const scores = [];
    html += `<tr><td class="mono">${escapeHtml(level)}</td>`;
    for (const seed of seeds) {
      const base = byCell[level + "/" + seed];
      if (!base) { html += `<td class="none">·</td>`; continue; }
      const r = record(selection.mode, base);
      const score = r ? r.norm_score : null;
      if (typeof score === "number") scores.push(score);
      const marks = (r && r.norm_score === 1 ? "★" : "") + (r && r.loop_stopped === true ? "↻" : "");
      const alpha = typeof score === "number" ? (0.06 + 0.5 * score).toFixed(2) : 0;
      html += `<td class="cell${base === selection.base ? " open" : ""}" data-base="${escapeHtml(base)}" ` +
              `style="background:rgba(var(--heat),${alpha})" title="${escapeHtml(base)}">${score === null ? "log" : fmt(score, 2)}${marks ? " " + marks : ""}</td>`;
    }
    html += `<td class="mean">${fmt(mean(scores), 3)}</td></tr>`;
  }
  $("grid").innerHTML = html + "</tbody>";
}

// ---------------------------------------------------------------- one game

function howEnded(r) {
  if ("result" in r) return `${r.result}${r.termination ? " (" + r.termination + ")" : ""}`;
  if (r.norm_score === 1) return "won";
  if (r.loop_stopped === true) return "stopped for a loop";
  if (r.max_steps && r.steps >= r.max_steps) return "step limit";
  return "ended by the game (lost)";
}

function renderFacts(r) {
  if (!r) { $("facts").innerHTML = `<span class="fact">no record for this game</span>`; $("trajectory").innerHTML = ""; return; }
  const t = r.tokens || {};
  const facts = [
    ["score", `${r.score ?? "?"} / ${r.max_score ?? "?"} (${fmt(r.norm_score, 2)})`], ["end", howEnded(r)],
    ["steps", `${r.steps}${r.max_steps ? " / " + r.max_steps : ""}`],
    ["opponent", r.opponent], ["plays", r.color], ["illegal", r.illegal], ["blunders", r.blunders],
    ["tokens", t.generated], ["thinking tokens", t.generated_thinking], ["thinking turns", t.think_turns],
    ["normal turns", t.normal_turns], ["capped thinking", t.capped_think_turns], ["capped answers", t.capped_normal_turns],
    ["empty reasonings", t.empty_reasonings], ["retries", t.nonempty_retries], ["longest prompt", t.max_prompt],
    ["seconds", r.seconds], ["profile", r.profile], ["source", r.source], ["played", (r.created || "").slice(0, 16).replace("T", " ")],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  $("facts").innerHTML = facts.map(([name, value]) => `<span class="fact"><span>${name}</span><b>${escapeHtml(value)}</b></span>`).join("");
  renderTrajectory(r);
}

function renderTrajectory(r) { // the score along the steps of a textual game: trajectory = [[step, score, seconds], ...]
  const points = (r.trajectory || []).filter(p => Array.isArray(p) && typeof p[1] === "number");
  if ("result" in r || points.length < 2 || !r.max_score) { $("trajectory").innerHTML = ""; return; }
  const width = 1000, height = 90, pad = 6;
  const lastStep = Math.max(r.max_steps || 0, points[points.length - 1][0]) || 1;
  const x = step => pad + (width - 2 * pad) * step / lastStep;
  const y = score => height - pad - (height - 2 * pad) * score / r.max_score;
  let path = `M${x(points[0][0])},${y(points[0][1])}`;
  for (let i = 1; i < points.length; i++) path += `H${x(points[i][0])}V${y(points[i][1])}`;
  $("trajectory").innerHTML =
    `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="score along the steps">` +
    `<line x1="${pad}" x2="${width - pad}" y1="${y(r.max_score)}" y2="${y(r.max_score)}" stroke="var(--line)" stroke-dasharray="4 4"/>` +
    `<line x1="${pad}" x2="${width - pad}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>` +
    `<path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>` +
    `<div class="caption">score along the steps (0 to ${r.max_score}, dashed: the maximum; ${points[points.length - 1][0]} steps played)</div>`;
}

// How a cooking game was lost, read from the last command of the log (the game's last message is not logged).
// The records do not say it: it is the reading of THESIS_LOG.md, 2026-11-19.
const LOSSES = [
  [/^eat\b/, "ate an ingredient"], [/^cook\b/, "cooking (wrong appliance, or cooked twice)"],
  [/^(chop|dice|slice|cut)\b/, "cutting (wrong cut, or cut twice)"],
];

function lastCommand(text) { // the last answer of the first game of the log
  let last = null, contexts = 0;
  for (const block of parseLog(text)) {
    if (block.kind === "divider" && ++contexts > 1) break;
    if (block.kind !== "agent" && block.kind !== "selfeval") continue;
    const body = block.lines.join("\n");
    const end = body.lastIndexOf("</think>");
    last = (end < 0 ? body : body.slice(end + "</think>".length)).trim().split("\n")[0].trim();
  }
  return last;
}

async function renderLastCommand(base) { // a fact added after the others, when the log has been read
  const r = record(selection.mode, base), log = filesOfGame(base).log;
  if (!r || !log || "result" in r || r.norm_score === 1) return;
  let command;
  try { command = lastCommand(await log.text()); } catch (error) { return; }
  if (!command || selection.base !== base) return;
  const lost = r.loop_stopped !== true && !(r.max_steps && r.steps >= r.max_steps);
  const how = lost && r.game === "cooking" ? LOSSES.find(([pattern]) => pattern.test(command.toLowerCase()))?.[1] : null;
  $("facts").insertAdjacentHTML("beforeend",
    (how ? `<span class="fact"><span>lost by</span><b>${escapeHtml(how)}</b></span>` : "") +
    `<span class="fact"><span>last command</span><b>${escapeHtml(command)}</b></span>`);
}

// The log of a game (agent.py): blocks opened by a marker line.
const MARKERS = [
  [/^CONTEXT INITIALIZED/, "divider"], [/^GAME \++/, "game"], [/^AGENT -+/, "agent"],
  [/^SELF-EVALUATION:/, "selfeval"], [/^SUMMARY \(/, "summary"], [/^DUEL:/, "info"],
];

function parseLog(text) {
  const blocks = [];
  let current = null;
  for (const line of text.split("\n")) {
    const marker = MARKERS.find(([pattern]) => pattern.test(line));
    if (marker) {
      current = { kind: marker[1], lines: [] };
      blocks.push(current);
    } else if (/^LOOP DETECTED/.test(line)) {
      blocks.push({ kind: "notice", lines: [line] });
    } else {
      if (!current) { current = { kind: "info", lines: [] }; blocks.push(current); }
      current.lines.push(line);
    }
  }
  return blocks;
}

function renderGameText(text) { // the status line of TextWorld is a prompt followed by 120 spaces: shown as a badge
  const badges = [];
  const body = text.replace(/^>\s{20,}-= (.+?) =-(\d+)\/(\d+)\s*$/gm, (all, room, score, moves) => {
    badges.push(`${room} · score ${score} · moves ${moves}`);
    return "";
  }).replace(/\n{3,}/g, "\n\n").trim();
  return `<pre>${escapeHtml(body)}</pre>` + badges.map(badge => `<span class="badge">${escapeHtml(badge)}</span>`).join(" ");
}

function renderLog(text) {
  let turn = 0;
  return parseLog(text).map(block => {
    const body = block.lines.join("\n").replace(/^\n+|\n+$/g, "");
    if (block.kind === "divider") return `<div class="block divider">— a new context —</div>`;
    if (block.kind === "notice") return `<div class="block notice"><pre>${escapeHtml(body)}</pre></div>`;
    if (block.kind === "game") return body ? `<div class="block game"><div class="label">game</div>${renderGameText(body)}</div>` : "";
    if (block.kind === "info") return body ? `<div class="block info"><pre>${escapeHtml(body)}</pre></div>` : "";
    if (block.kind === "summary") return `<div class="block summary"><div class="label">summary kept in the context</div><pre>${escapeHtml(body)}</pre></div>`;
    turn++;
    if (block.kind === "agent") {
      return `<div class="block agent" data-turn="${turn}"><div class="label">turn ${turn} · answer without thinking</div>` +
             `<pre class="command">${escapeHtml(body)}</pre></div>`;
    }
    // a self-evaluation: one or more reasonings between <think> and </think>, then the command
    const end = body.lastIndexOf("</think>");
    const reasoning = end < 0 ? "" : body.slice(0, end).replace(/<\/?think>/g, "").trim();
    const command = end < 0 ? body : body.slice(end + "</think>".length).trim();
    const words = reasoning ? reasoning.split(/\s+/).length : 0;
    return `<div class="block selfeval" data-turn="${turn}"><div class="label">turn ${turn} · thinking</div>` +
           `<details open><summary>reasoning (${words} words)</summary><pre>${escapeHtml(reasoning || "(empty)")}</pre></details>` +
           `<pre class="command">${escapeHtml(command)}</pre></div>`;
  }).join("");
}

function renderJsonl(text) {
  const lines = text.split("\n").filter(line => line.trim());
  return `<div class="jsonl">` + lines.map((line, i) => {
    let item, title = `line ${i + 1}`;
    try {
      item = JSON.parse(line);
      title = ["ply", "player", "color", "move"].filter(name => item[name] !== undefined)
        .map(name => `${name} ${item[name]}`).join(" · ") || title;
      if (item.think !== undefined) title += item.think ? " · thinking" : "";
    } catch (error) { item = line; }
    const body = typeof item === "string" ? item : JSON.stringify(item, null, 2);
    return `<details><summary class="mono">${escapeHtml(title)}</summary><div class="raw"><pre>${escapeHtml(body)}</pre></div></details>`;
  }).join("") + `</div>`;
}

function filesOfGame(base) { // the files next to the record, plus the log of an imported old game where it is
  const files = { ...(node(4)[base] || {}) };
  const path = record(selection.mode, base)?.log;
  if (!files.log && typeof path === "string" && path.includes("Experiments/")) {
    const file = oldLogs.get(path.slice(path.indexOf("Experiments/")));
    if (file) files.log = file;
    else files.missingLog = path;
  }
  return files;
}

async function renderContent() {
  const files = filesOfGame(selection.base);
  const available = TABS.filter(([extension]) => files[extension]);
  if (!available.some(([extension]) => extension === selection.tab)) selection.tab = available[0]?.[0];
  $("tabs").innerHTML = available.map(([extension, title]) =>
    `<button data-tab="${extension}" class="${extension === selection.tab ? "active" : ""}">${title}</button>`).join("");
  const isLog = selection.tab === "log" || selection.tab === "opponent.log";
  $("logtools").classList.toggle("hidden", !isLog);
  $("hint").textContent = files.missingLog
    ? `The log of this game is ${files.missingLog}: to read it, choose the folder Code (it holds both Benchmark/results and Experiments).`
    : (!files.log ? "This game has no log: only its record was saved." : "");
  if (!selection.tab) { $("content").innerHTML = "<p>No file for this game.</p>"; return; }
  const opened = selection.base + "/" + selection.tab;
  $("content").innerHTML = "<p>reading…</p>";
  let text;
  try { text = await files[selection.tab].text(); } catch (error) { text = null; }
  if (opened !== selection.base + "/" + selection.tab) return; // another game was opened meanwhile
  if (text === null) { $("content").innerHTML = "<p>The file could not be read (was the folder moved? choose it again).</p>"; return; }
  if (isLog) $("content").innerHTML = renderLog(text);
  else if (selection.tab.endsWith("jsonl")) $("content").innerHTML = renderJsonl(text);
  else if (selection.tab === "json") {
    let pretty = text;
    try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (error) { /* shown as it is */ }
    $("content").innerHTML = `<div class="raw"><pre>${escapeHtml(pretty)}</pre></div>`;
  } else $("content").innerHTML = `<div class="raw"><pre>${escapeHtml(text)}</pre></div>`;
  applyFilter();
}

function applyFilter() {
  const query = $("filter").value.trim().toLowerCase();
  const blocks = [...$("content").querySelectorAll(".block")];
  let shown = 0;
  for (const block of blocks) {
    const hit = !query || block.textContent.toLowerCase().includes(query);
    block.classList.toggle("hidden", !hit);
    if (hit && block.dataset.turn) shown++;
  }
  $("filterCount").textContent = query ? `${shown} turns` : "";
}

async function openGame(base, scroll = true) {
  selection.base = base;
  const { level, seed } = parseBase(base);
  $("detail").hidden = false;
  $("detailTitle").textContent = `${selection.mode} · level ${level}${seed ? " · seed " + seed : ""}`;
  renderFacts(record(selection.mode, base));
  renderLastCommand(base);
  for (const cell of $("grid").querySelectorAll("td.cell")) cell.classList.toggle("open", cell.dataset.base === base);
  const bases = basesOfMode(), at = bases.indexOf(base);
  $("prev").disabled = at <= 0;
  $("next").disabled = at < 0 || at >= bases.length - 1;
  await renderContent();
  if (scroll) $("detail").scrollIntoView({ block: "start" });
}

function step(offset) {
  const bases = basesOfMode(), at = bases.indexOf(selection.base) + offset;
  if (selection.base && at >= 0 && at < bases.length) openGame(bases[at], false);
}

// ---------------------------------------------------------------- the page

async function refresh(changed) { // changed: the level of the selector that changed, or null for a new folder
  fillSelectors();
  LEVELS.forEach(level => selection[level] && remember(level, selection[level]));
  if (changed !== "mode") await loadRecords();
  renderModes();
  renderGrid();
  selection.base = null;
  $("detail").hidden = true;
}

$("folder").addEventListener("change", async event => {
  $("status").textContent = "listing the files…";
  // The list is copied and the input emptied. Left in the input, the 30,000 file names are part of the state of the
  // page that Chrome saves in its history at every scroll step: its main process then checks every name again, a
  // second of work each time, and scrolling stalls (trace of 2026-11-19). The File objects stay readable.
  const files = [...event.target.files];
  event.target.value = "";
  const count = indexFiles(files);
  if (!count) {
    $("status").textContent = "no game found: choose the folder Code/Benchmark/results";
    return;
  }
  for (const id of ["selectors", "overview", "games"]) $(id).hidden = false;
  $("welcome").hidden = true;
  $("pickButton").textContent = "Choose another folder…";
  await refresh(null);
  $("status").textContent = `${count} files`;
});

LEVELS.forEach(level => $(level).addEventListener("change", event => {
  selection[level] = event.target.value;
  refresh(level);
}));

$("modes").addEventListener("click", event => {
  const row = event.target.closest("tr[data-mode]");
  if (!row) return;
  selection.mode = row.dataset.mode;
  refresh("mode");
});

$("grid").addEventListener("click", event => {
  const cell = event.target.closest("td.cell");
  if (cell) openGame(cell.dataset.base);
});

$("tabs").addEventListener("click", event => {
  const button = event.target.closest("button[data-tab]");
  if (!button) return;
  selection.tab = button.dataset.tab;
  renderContent();
});

$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
document.addEventListener("keydown", event => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName) || $("detail").hidden) return;
  if (event.key === "ArrowLeft") step(-1);
  if (event.key === "ArrowRight") step(1);
});

$("hideThink").addEventListener("change", event => document.body.classList.toggle("hideThink", event.target.checked));
$("hideGame").addEventListener("change", event => document.body.classList.toggle("hideGame", event.target.checked));
$("filter").addEventListener("input", applyFilter);

if (typeof module !== "undefined") module.exports = { lastCommand, parseLog, renderLog, parseBase, modeOrder, indexFiles, tree, oldLogs };
