// VivaVerify frontend: plain JS, no build step.
// - /ws/viva (our backend) streams agent steps, the plan, questions, scores and the final report.
// - Soniox real-time STT and TTS WebSockets are opened directly from the browser with
//   short-lived keys from /api/voice-token (sent in the WebSocket protocol list).

const API = window.VIVA_API || "";
const WS_BASE = (API || location.origin).replace(/^http/, "ws");
const SONIOX_STT = "wss://stt-rt.soniox.com/transcribe-websocket";
const SONIOX_TTS = "wss://tts-rt.soniox.com/tts-websocket";
const TTS_RATE = 24000;
const HISTORY_KEY = "vivaverify-history";
const SCREENS = ["setup", "analysed", "viva", "report", "progress", "how"];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let viva = null;        // our backend WebSocket
let keys = null;        // Soniox temporary keys {stt, tts}
let mic = null;         // active recording {stream, rec, sock, done}
let audioCtx = null;
let playing = [];       // TTS audio sources currently scheduled
let session = null;     // everything about the running viva
let currentQuestion = "";
let finished = false;

// ---------- navigation ----------
function show(id) {
  for (const s of SCREENS) $(s).hidden = s !== id;
  const navTarget = ["progress", "how"].includes(id) ? id : "setup";
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.nav === navTarget));
  window.scrollTo(0, 0);
}

document.addEventListener("click", (e) => {
  const nav = e.target.closest("[data-nav]");
  if (!nav) return;
  const target = nav.dataset.nav;
  if (target === "progress") renderProgress();
  if (target === "setup" && session) {   // return to the running viva instead of a blank form
    show(finished ? "report" : session.started ? "viva" : "analysed");
    return;
  }
  show(target);
});

// ---------- setup ----------
$("file").addEventListener("change", () => {
  const f = $("file").files[0];
  $("dropText").textContent = f ? `${f.name} (${Math.round(f.size / 1024)} KB)` : "Drop a PDF / MD / TXT here, or click to choose";
});

$("startBtn").addEventListener("click", async () => {
  $("startBtn").disabled = true;
  setupMsg("Reading your report...");
  try {
    const doc = await uploadDocument();
    if ($("voiceOn").checked) {
      audioCtx = new AudioContext({ sampleRate: TTS_RATE });   // created inside the click so autoplay is allowed
      keys = await fetchJson(`${API}/api/voice-token`, { method: "POST" }).catch((e) => {
        addStep("Voice", `Voice unavailable (${e.message}) - continuing in text mode.`);
        return null;
      });
    }
    session = {
      doc, started: false, firstQuestion: null, plan: [], evaluations: [], startedAt: Date.now(),
      settings: { questions: +$("numQ").value, strictness: $("strict").value },
    };
    renderAnalysed(doc);
    show("analysed");
    addStep("Upload", `${doc.title}: ${doc.words} words split into ${doc.chunks} chunks and indexed for retrieval.`);
    openViva(doc.doc_id);
  } catch (e) {
    setupMsg(e.message, true);
    $("startBtn").disabled = false;
  }
});

async function uploadDocument() {
  const form = new FormData();
  const file = $("file").files[0];
  if (file) form.append("file", file);
  else form.append("text", $("paste").value);
  return fetchJson(`${API}/api/documents`, { method: "POST", body: form });
}

async function fetchJson(url, options) {
  const r = await fetch(url, options);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

// ---------- report analysed ----------
function renderAnalysed(doc) {
  const s = session.settings;
  $("docTiles").innerHTML = tiles([
    ["Report", doc.title], ["Words", doc.words], ["Chunks indexed", doc.chunks],
    ["Questions", s.questions], ["Strictness", cap(s.strictness)],
  ]);
  $("chunkList").innerHTML = doc.outline.map((c) =>
    `<li data-chunk="${esc(c.id)}"><b>${esc(c.id)}</b> ${esc(c.preview)}</li>`).join("");
}

function showPlan(ev, ms) {
  session.plan = ev.topics;
  $("planList").innerHTML = ev.topics.map((t) => `<li>${esc(t)}</li>`).join("");
  const used = new Set(ev.evidence.flat());
  document.querySelectorAll("#chunkList li").forEach((li) => {
    if (used.has(li.dataset.chunk)) li.insertAdjacentHTML("beforeend", ' <span class="chip">evidence</span>');
  });
  $("planMeta").textContent = ms ? `Planned in ${(ms / 1000).toFixed(1)} s.` : "";
  renderDots();
}

$("beginBtn").addEventListener("click", () => {
  session.started = true;
  show("viva");
  if (session.firstQuestion) showQuestion(session.firstQuestion);
});

// ---------- viva session ----------
function openViva(docId) {
  viva = new WebSocket(`${WS_BASE}/ws/viva`);
  viva.onopen = () => viva.send(JSON.stringify({
    type: "start", doc_id: docId, num_questions: session.settings.questions, strictness: session.settings.strictness,
  }));
  viva.onmessage = (m) => handle(JSON.parse(m.data));
  viva.onclose = () => { if (!finished) status("Connection closed. Reload to start again."); };
}

let lastPlannerMs = 0;
function handle(ev) {
  if (ev.type === "step") {
    if (ev.node === "Planner") lastPlannerMs = ev.ms;
    addStep(ev.node, ev.detail, ev.ms);
  } else if (ev.type === "plan") showPlan(ev, lastPlannerMs);
  else if (ev.type === "question") {
    if (!session.started) {          // hold the first question until the student presses Begin
      session.firstQuestion = ev;
      $("beginBtn").disabled = false;
      $("beginBtn").textContent = "Begin viva";
    } else showQuestion(ev);
  } else if (ev.type === "evaluation") showEvaluation(ev);
  else if (ev.type === "report") showReport(ev);
  else if (ev.type === "error") {
    finished = true;
    status(`Error: ${ev.message}`);
    $("planList").innerHTML = `<li class="error">${esc(ev.message)}</li>`;
  }
}

function showQuestion(q) {
  currentQuestion = q.question;
  session.current = q.index;
  $("qCounter").textContent = `Question ${q.index} of ${q.total}`;
  $("qTopic").textContent = q.topic;
  $("qFollow").hidden = !q.followup;
  $("qText").textContent = q.question;
  $("answer").value = "";
  $("live").textContent = "";
  setAnswering(true);
  $("replayBtn").hidden = !keys;
  status(keys ? "Listen, then press Start speaking." : "Type your answer.");
  renderDots();
  speak(q.question);
}

function renderDots() {
  const best = {};
  for (const e of session.evaluations) best[e.index] = Math.max(best[e.index] ?? 0, e.score);
  $("qDots").innerHTML = session.plan.map((t, i) => {
    const n = i + 1;
    const state = n in best ? (best[n] >= 4 ? "good" : best[n] >= 3 ? "mid" : "low") : n === session.current ? "now" : "";
    return `<span class="dot ${state}" title="${esc(t)}">${n in best ? `${best[n]}/5` : `Q${n}`}</span>`;
  }).join("");
}

async function submitAnswer() {
  setAnswering(false);
  if (mic) await stopMic();
  stopSpeaking();
  viva.send(JSON.stringify({ type: "answer", text: $("answer").value.trim() }));
  status("Judging your answer against your report...");
}

function setAnswering(on) {
  $("answer").disabled = !on;
  $("sendBtn").disabled = !on;
  $("micBtn").disabled = !on || !keys;
}

function evalCard(e) {
  const list = (title, items) => items && items.length ? `<div><b>${title}:</b> ${items.map(esc).join("; ")}</div>` : "";
  return `<div class="eval s${e.score}">
    <div class="eval-head"><span class="score">${e.score}/5</span>
      <span>Q${e.index}${e.followup ? " follow-up" : ""} - ${esc(e.topic)}</span>
      ${e.flag ? `<span class="chip warn">${esc(e.flag)}</span>` : ""}</div>
    <p>${esc(e.feedback)}</p>
    ${list("Covered", e.covered)}${list("Missing", e.missing)}${list("Conflicts with your report", e.contradictions)}
  </div>`;
}

function showEvaluation(e) {
  session.evaluations.push(e);
  $("evals").insertAdjacentHTML("afterbegin", evalCard(e));
  renderDots();
}

// ---------- result ----------
function showReport(r) {
  finished = true;
  stopSpeaking();
  show("report");
  $("rPercent").textContent = `${r.percent}%`;
  $("rPercent").className = `big-score ${r.percent >= 75 ? "good" : r.percent >= 50 ? "mid" : "low"}`;
  $("rVerdict").textContent = r.verdict;
  $("rFlags").textContent = r.flags.length ? `Flags: ${r.flags.join(", ")}` : "";
  const minutes = Math.max(1, Math.round((Date.now() - session.startedAt) / 60000));
  $("rTiles").innerHTML = tiles([
    ["Questions", r.questions.length], ["Follow-ups needed", r.questions.filter((q) => q.followup_used).length],
    ["Answers flagged", session.evaluations.filter((e) => e.flag).length], ["Time taken", `${minutes} min`],
  ]);
  $("rSummary").innerHTML = r.questions.map((q) => `
    <div class="rrow">
      <div class="rrow-head"><span class="score">${q.score}/5</span><b>${esc(q.topic)}</b>
        ${q.followup_used ? '<span class="chip warn">needed a follow-up</span>' : ""}</div>
      <p class="muted">${esc(q.question)}</p>
      ${q.missing.length ? `<div><b>Missing:</b> ${q.missing.map(esc).join("; ")}</div>` : ""}
      ${q.reread.map((c) => `<blockquote><b>Re-read [${c.id}]</b> ${esc(c.excerpt)}</blockquote>`).join("")}
    </div>`).join("");
  $("rTranscript").innerHTML = session.evaluations.map((e) => `
    <div class="turn">
      <div class="who examiner">Examiner${e.followup ? " (follow-up)" : ""}</div><p>${esc(e.question)}</p>
      <div class="who student">You</div><p>${esc(e.answer) || '<span class="muted">(no answer)</span>'}</p>
      ${evalCard(e)}
    </div>`).join("");
  saveHistory(r);
}

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
  for (const id of ["rSummary", "rTranscript"]) $(id).hidden = id !== b.dataset.tab;
}));

// ---------- progress (stored only in this browser) ----------
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch { return []; }
}

function saveHistory(r) {
  const history = loadHistory();
  history.push({
    date: new Date().toISOString(), title: session.doc.title, percent: r.percent, verdict: r.verdict,
    strictness: session.settings.strictness, topics: r.questions.map((q) => ({ topic: q.topic, score: q.score })),
  });
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-50))); } catch { /* storage unavailable */ }
}

$("clearBtn").addEventListener("click", () => {
  try { localStorage.removeItem(HISTORY_KEY); } catch { /* storage unavailable */ }
  renderProgress();
});

function renderProgress() {
  const h = loadHistory();
  $("progressEmpty").hidden = h.length > 0;
  $("progressBody").hidden = $("historyCard").hidden = h.length === 0;
  $("pTiles").innerHTML = "";
  if (!h.length) return;
  const scores = h.map((a) => a.percent);
  const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
  const delta = h.length > 1 ? scores.at(-1) - scores.at(-2) : null;
  $("pTiles").innerHTML = tiles([
    ["Vivas taken", h.length], ["Best score", `${Math.max(...scores)}%`],
    ["Latest score", `${scores.at(-1)}%`, delta === null ? "" : `${delta >= 0 ? "+" : ""}${delta} vs previous`],
    ["Average", `${avg}%`],
  ]);
  renderChart(h);
  renderTopics(h);
  $("historyRows").innerHTML = h.slice().reverse().map((a) => `<tr><td>${fmtDate(a.date)}</td><td>${esc(a.title)}</td>
    <td>${a.topics.length}</td><td>${esc(cap(a.strictness))}</td><td class="num">${a.percent}%</td><td>${esc(a.verdict)}</td></tr>`).join("");
}

// Column chart: one series (score per attempt), bars <= 24px, 4px rounded top, hairline grid,
// labels only on the best and latest bars, every bar has a hover/focus tooltip.
function renderChart(h) {
  const W = 560, H = 230, L = 40, R = 12, T = 24, B = 30;
  const plotH = H - T - B, band = (W - L - R) / h.length, bw = Math.min(24, band * 0.6), base = T + plotH;
  const best = Math.max(...h.map((a) => a.percent));
  const grid = [0, 50, 100].map((v) => {
    const y = base - (v / 100) * plotH;
    return `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" class="gridline"/><text x="${L - 8}" y="${y + 4}" class="axis" text-anchor="end">${v}%</text>`;
  }).join("");
  const bars = h.map((a, i) => {
    const x = L + i * band + (band - bw) / 2, hgt = Math.max(1, (a.percent / 100) * plotH), y = base - hgt, r = Math.min(4, hgt);
    const path = `M${x},${base} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${base} Z`;
    const label = i === h.length - 1 || a.percent === best
      ? `<text x="${x + bw / 2}" y="${y - 6}" class="value" text-anchor="middle">${a.percent}%</text>` : "";
    return `<g class="bar" tabindex="0" data-i="${i}"><rect x="${L + i * band}" y="${T}" width="${band}" height="${plotH}" class="hit"/>
      <path d="${path}" class="mark"/>${label}
      <text x="${x + bw / 2}" y="${base + 18}" class="axis" text-anchor="middle">#${i + 1}</text></g>`;
  }).join("");
  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Score per attempt">${grid}${bars}</svg>`;
  $("chart").querySelectorAll(".bar").forEach((g) => {
    const a = h[+g.dataset.i];
    const on = () => tooltip(g, `${a.percent}%`, `Attempt ${+g.dataset.i + 1} · ${fmtDate(a.date)} · ${a.title}`);
    g.addEventListener("pointerenter", on);
    g.addEventListener("focus", on);
    g.addEventListener("pointerleave", () => { $("tooltip").hidden = true; });
    g.addEventListener("blur", () => { $("tooltip").hidden = true; });
  });
}

function tooltip(target, value, label) {
  const tip = $("tooltip");
  tip.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = value;
  const span = document.createElement("span");
  span.textContent = label;
  tip.append(strong, span);
  tip.hidden = false;
  const box = target.getBoundingClientRect();
  tip.style.left = `${box.left + box.width / 2 + window.scrollX}px`;
  tip.style.top = `${box.top + window.scrollY - 8}px`;
}

// Meters: average score per topic; the fill carries severity, and a text label always says it too.
function renderTopics(h) {
  const byTopic = {};
  const names = {};
  for (const a of h) for (const t of a.topics) {
    const key = t.topic.trim().toLowerCase();   // "Liveness Check" and "Liveness check" are one topic
    names[key] ??= cap(key);
    (byTopic[key] ??= []).push(t.score);
  }
  const rows = Object.entries(byTopic).map(([key, s]) => [names[key], s.reduce((x, y) => x + y, 0) / s.length])
    .sort((a, b) => a[1] - b[1]).slice(0, 6);
  $("topicMeters").innerHTML = rows.map(([topic, avg]) => {
    const level = avg >= 4 ? ["good", "strong"] : avg >= 3 ? ["mid", "revise"] : ["low", "needs work"];
    return `<div class="meter-row"><div class="meter-label"><span>${esc(topic)}</span>
      <span class="muted">${avg.toFixed(1)}/5 · ${level[1]}</span></div>
      <div class="meter ${level[0]}"><div style="width:${(avg / 5) * 100}%"></div></div></div>`;
  }).join("");
}

// ---------- Soniox real-time speech-to-text ----------
async function startMic() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const sock = new WebSocket(SONIOX_STT, ["soniox-api-key", keys.stt]);
  const rec = new MediaRecorder(stream);
  let finalText = $("answer").value ? `${$("answer").value} ` : "";
  let resolveDone;
  mic = { stream, rec, sock, done: new Promise((r) => (resolveDone = r)) };

  sock.onopen = () => {
    sock.send(JSON.stringify({
      model: "stt-rt-v5", audio_format: "auto", enable_endpoint_detection: true,
      language_hints: $("lang").value === "hi" ? ["hi", "en"] : ["en"],
    }));
    rec.start(250);   // send a chunk every 250 ms
  };
  rec.ondataavailable = (e) => { if (e.data.size && sock.readyState === WebSocket.OPEN) sock.send(e.data); };
  rec.onstop = () => { if (sock.readyState === WebSocket.OPEN) sock.send(""); };   // empty frame = end of audio
  sock.onmessage = (m) => {
    const res = JSON.parse(m.data);
    if (res.error_message) { status(`Speech error: ${res.error_message}`); return; }
    let partial = "";
    for (const t of res.tokens || []) {
      if (t.text.startsWith("<")) continue;   // <end> / <fin> markers
      if (t.is_final) finalText += t.text; else partial += t.text;
    }
    $("answer").value = finalText.trimStart();
    $("live").textContent = partial;
    if (res.finished) sock.close();
  };
  sock.onclose = () => { $("live").textContent = ""; resolveDone(); };
  sock.onerror = () => status("Could not reach the speech service - you can type instead.");

  $("answer").readOnly = true;
  $("micBtn").textContent = "Stop speaking";
  $("micBtn").classList.add("rec");
  status("Listening...");
}

async function stopMic() {
  const m = mic;
  mic = null;
  if (m.rec.state !== "inactive") m.rec.stop();
  m.stream.getTracks().forEach((t) => t.stop());
  await Promise.race([m.done, new Promise((r) => setTimeout(r, 4000))]);   // wait for final words
  $("answer").readOnly = false;
  $("micBtn").textContent = "Start speaking";
  $("micBtn").classList.remove("rec");
  status("Edit your answer if needed, then submit.");
}

$("micBtn").addEventListener("click", () => (mic ? stopMic() : startMic().catch((e) => status(`Microphone: ${e.message}`))));
$("sendBtn").addEventListener("click", submitAnswer);
$("replayBtn").addEventListener("click", () => speak(currentQuestion));

// ---------- Soniox real-time text-to-speech ----------
function speak(text) {
  if (!keys || !audioCtx) return;
  stopSpeaking();
  const streamId = `q${Date.now()}`;
  const sock = new WebSocket(SONIOX_TTS, ["soniox-api-key", keys.tts]);
  let at = audioCtx.currentTime + 0.05;
  let carry = new Uint8Array(0);   // an odd byte left over between chunks

  sock.onopen = () => {
    sock.send(JSON.stringify({ stream_id: streamId, model: "tts-rt-v2", language: "en", voice: "Adrian",
                               audio_format: "pcm_s16le", sample_rate: TTS_RATE }));
    sock.send(JSON.stringify({ stream_id: streamId, text, text_end: false }));
    sock.send(JSON.stringify({ stream_id: streamId, text: "", text_end: true }));
  };
  sock.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.audio) {
      const raw = Uint8Array.from(atob(msg.audio), (c) => c.charCodeAt(0));
      const bytes = new Uint8Array(carry.length + raw.length);
      bytes.set(carry);
      bytes.set(raw, carry.length);
      const even = bytes.length - (bytes.length % 2);
      carry = bytes.slice(even);
      const pcm = new Int16Array(bytes.buffer, 0, even / 2);
      const buffer = audioCtx.createBuffer(1, pcm.length, TTS_RATE);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
      const src = audioCtx.createBufferSource();
      src.buffer = buffer;
      src.connect(audioCtx.destination);
      at = Math.max(at, audioCtx.currentTime);
      src.start(at);
      at += buffer.duration;
      playing.push(src);
    }
    if (msg.terminated || msg.error_message) sock.close();
  };
}

function stopSpeaking() {
  playing.forEach((s) => { try { s.stop(); } catch { /* already ended */ } });
  playing = [];
}

// ---------- UI helpers ----------
function tiles(items) {
  return items.map(([label, value, note]) => `<div class="tile"><div class="tile-label">${esc(label)}</div>
    <div class="tile-value${String(value).length > 14 ? " long" : ""}">${esc(value)}</div>${note ? `<div class="tile-note">${esc(note)}</div>` : ""}</div>`).join("");
}

function addStep(node, detail, ms) {
  const li = document.createElement("li");
  li.innerHTML = `<b>${esc(node)}</b> ${esc(detail)}${ms >= 100 ? ` <span class="ms">${(ms / 1000).toFixed(1)} s</span>` : ""}`;
  $("trace").append(li);
  li.scrollIntoView({ block: "nearest" });
}

const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1);
const fmtDate = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function status(text) { $("status").textContent = text; }

function setupMsg(text, isError = false) {
  $("setupMsg").textContent = text;
  $("setupMsg").classList.toggle("error", isError);
}
