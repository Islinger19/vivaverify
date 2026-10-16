// VivaVerify frontend: plain JS, no build step.
// - /ws/viva (our backend) streams agent steps, the plan, questions, scores and the final report.
// - Soniox real-time STT and TTS WebSockets are opened directly from the browser with
//   short-lived keys from /api/voice-token (sent in the WebSocket protocol list).

const API = window.VIVA_API || "";
const WS_BASE = (API || location.origin).replace(/^http/, "ws");
const SONIOX_STT = "wss://stt-rt.soniox.com/transcribe-websocket";
const SONIOX_TTS = "wss://tts-rt.soniox.com/tts-websocket";
const TTS_RATE = 24000;
const HISTORY_KEY = "vivaverify-improvement";
const SCREENS = ["setup", "analysed", "viva", "report", "progress", "how"];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let viva = null;        // our backend WebSocket
let keys = null;        // Soniox temporary keys {stt, tts}
let mic = null;         // active recording {stream, rec, sock, done}
let audioCtx = null;
let playing = [];       // TTS audio sources currently scheduled
let tts = null;         // the TTS WebSocket currently streaming audio
let session = null;     // everything about the running viva
let currentQuestion = "";
let finished = false;
let failed = null;      // the last error event, while the viva waits for "Try again"

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
    ["Report", doc.title], ["Words", doc.words], ["Sections", doc.chunks],
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
  if (failed) return retry();
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
  viva.onclose = () => { if (!finished && !failed) showError({ message: "The connection to the examiner was lost.", retry: false }); };
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
  else if (ev.type === "error") showError(ev);
}

// An LLM failure does not end the viva: the backend keeps its state and re-runs the failed step on "retry".
function showError(ev) {
  failed = ev;
  stopSpeaking();
  status(ev.message);
  const label = ev.retry ? "Try again" : "Start over";
  if (!session.started) {
    $("planList").innerHTML = `<li class="error">${esc(ev.message)}</li>`;
    $("beginBtn").disabled = false;
    $("beginBtn").textContent = label;
  } else {
    $("retryBtn").textContent = label;
    $("retryBtn").hidden = false;
  }
}

function retry() {
  const ev = failed;
  failed = null;
  if (!ev.retry || viva.readyState !== WebSocket.OPEN) return location.reload();
  $("retryBtn").hidden = true;
  if (!session.started) {
    $("beginBtn").disabled = true;
    $("beginBtn").textContent = "Preparing...";
    $("planList").innerHTML = '<li class="muted">Examiner is reading your report...</li>';
  }
  status("Trying again...");
  viva.send(JSON.stringify({ type: "retry" }));
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
  const best = {};   // the question's score so far, computed by the backend exactly as in the final report
  for (const e of session.evaluations) best[e.index] = e.question_score;
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

// Full feedback, for the transcript after the viva.
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

// During the viva the backend sends only the score and a neutral note, never the missing points,
// so nothing on screen (or in the browser's dev tools) gives away the answer to the follow-up.
function showEvaluation(e) {
  session.evaluations.push(e);
  $("evals").insertAdjacentHTML("afterbegin", `<div class="eval s${e.question_score}">
    <div class="eval-head"><span class="score">${e.question_score}/5</span>
      <span>Q${e.index}${e.followup ? " after follow-up" : ""} - ${esc(e.topic)}</span>
      ${e.flag ? `<span class="chip warn">${esc(e.flag)}</span>` : ""}</div>
    <p>${esc(e.note)}</p>
    ${e.flag ? "" : `<div class="muted small-text">Covered ${e.covered_count} of ${e.points} key points</div>`}
  </div>`);
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
    ["Answers flagged", r.transcript.filter((e) => e.flag).length], ["Time taken", `${minutes} min`],
  ]);
  $("rSummary").innerHTML = r.questions.map((q) => `
    <div class="rrow">
      <div class="rrow-head"><span class="score">${q.score}/5</span><b>${esc(q.topic)}</b>
        ${q.followup_used ? '<span class="chip warn">needed a follow-up</span>' : ""}</div>
      <p class="muted">${esc(q.question)}</p>
      ${q.missing.length ? `<div><b>Missing:</b> ${q.missing.map(esc).join("; ")}</div>` : ""}
      ${q.reread.map((c) => `<blockquote><b>Re-read [${c.id}]</b> ${esc(c.excerpt)}</blockquote>`).join("")}
    </div>`).join("");
  $("rTranscript").innerHTML = r.transcript.map((e) => `
    <div class="turn">
      <div class="who examiner">Examiner${e.followup ? " (follow-up)" : ""}</div><p>${esc(e.question)}</p>
      <div class="who student">You</div><p>${esc(e.answer) || '<span class="muted">(no answer)</span>'}</p>
      ${evalCard(e)}
    </div>`).join("");
  saveImprovement(r);
}

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
  for (const id of ["rSummary", "rTranscript"]) $(id).hidden = id !== b.dataset.tab;
}));

// ---------- How to improve (actionable feedback on what was missed) ----------
function saveImprovement(r) {
  const items = r.questions.map((q) => {
    const ev = r.transcript.filter((e) => e.index === q.index).at(-1) || {};
    return {
      index: q.index,
      topic: q.topic,
      question: q.question,
      score: q.score,
      followup_used: q.followup_used,
      answer: ev.answer || "",
      covered: ev.covered || [],
      missing: (q.missing && q.missing.length) ? q.missing : (ev.missing || []),
      contradictions: ev.contradictions || [],
      feedback: ev.feedback || "",
      reread: q.reread || [],
    };
  });
  const data = {
    title: session?.doc?.title || "Project Report",
    date: new Date().toISOString(),
    percent: r.percent,
    verdict: r.verdict,
    items,
  };
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(data)); } catch {}
}

function renderProgress() {
  let data = null;
  try { data = JSON.parse(localStorage.getItem(HISTORY_KEY)); } catch {}
  if (!data || !data.items || !data.items.length) {
    $("progressEmpty").hidden = false;
    $("improveSummary").hidden = true;
    $("improveList").innerHTML = "";
    return;
  }
  $("progressEmpty").hidden = true;
  $("improveSummary").hidden = false;

  const gaps = data.items.filter((it) => it.score < 4 || (it.missing && it.missing.length > 0));
  $("improveSummary").innerHTML = tiles([
    ["Report", data.title],
    ["Latest score", `${data.percent}%`, data.verdict],
    ["To revise", gaps.length === 0 ? "Nothing - well done" : `${gaps.length} of ${data.items.length} questions`],
  ]);

  $("improveList").innerHTML = data.items.map((it) => {
    const badgeClass = it.score >= 4 ? "good" : it.score >= 3 ? "mid" : "low";
    const statusText = it.score >= 4 ? "Well defended" : it.score >= 3 ? "Some gaps" : "Revise this";

    const missingHtml = it.missing && it.missing.length ? `
      <div class="improve-block">
        <div class="improve-title warn">Points you missed</div>
        <ul class="improve-list">
          ${it.missing.map((m) => `<li>${esc(m)}</li>`).join("")}
        </ul>
      </div>` : "";

    const contradictionsHtml = it.contradictions && it.contradictions.length ? `
      <div class="improve-block">
        <div class="improve-title error">Conflicts with your report</div>
        <ul class="improve-list error">
          ${it.contradictions.map((c) => `<li>${esc(c)}</li>`).join("")}
        </ul>
      </div>` : "";

    const feedbackHtml = it.feedback ? `
      <div class="improve-block">
        <div class="improve-title">Examiner's comment</div>
        <p class="improve-text">${esc(it.feedback)}</p>
      </div>` : "";

    const rereadHtml = it.reread && it.reread.length ? `
      <div class="improve-block">
        <div class="improve-title info">Sections to read again</div>
        ${it.reread.map((r) => `<blockquote><b>Section [${esc(r.id)}]</b> ${esc(r.excerpt)}</blockquote>`).join("")}
      </div>` : "";

    return `
      <div class="card improve-card ${badgeClass}">
        <div class="rrow-head">
          <span class="score ${badgeClass}">${it.score}/5</span>
          <div>
            <b>Q${it.index}: ${esc(it.topic)}</b>
            <span class="muted small-text"> &middot; ${statusText}</span>
          </div>
        </div>
        <p class="improve-q">${esc(it.question)}</p>
        ${it.answer ? `<div class="improve-answer"><span class="muted">Your answer: </span>${esc(it.answer)}</div>` : ""}
        ${missingHtml}
        ${contradictionsHtml}
        ${feedbackHtml}
        ${rereadHtml}
      </div>`;
  }).join("");
}

// ---------- Soniox real-time speech-to-text ----------
async function startMic() {
  stopSpeaking();   // otherwise the mic can pick up the examiner's voice and add the question to the answer
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
$("retryBtn").addEventListener("click", retry);
$("replayBtn").addEventListener("click", () => speak(currentQuestion));

// ---------- Soniox real-time text-to-speech ----------
function speak(text) {
  if (!keys || !audioCtx) return;
  stopSpeaking();
  const streamId = `q${Date.now()}`;
  const sock = new WebSocket(SONIOX_TTS, ["soniox-api-key", keys.tts]);
  tts = sock;
  let at = audioCtx.currentTime + 0.05;
  let carry = new Uint8Array(0);   // an odd byte left over between chunks

  sock.onopen = () => {
    sock.send(JSON.stringify({ stream_id: streamId, model: "tts-rt-v2", language: "en", voice: "Adrian",
                               audio_format: "pcm_s16le", sample_rate: TTS_RATE }));
    sock.send(JSON.stringify({ stream_id: streamId, text, text_end: false }));
    sock.send(JSON.stringify({ stream_id: streamId, text: "", text_end: true }));
  };
  sock.onmessage = (m) => {
    if (sock !== tts) return;   // stopped or replaced: drop audio that is still arriving
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
  if (tts) {
    try { tts.close(); } catch { /* already closed */ }
    tts = null;
  }
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

function status(text) { $("status").textContent = text; }

function setupMsg(text, isError = false) {
  $("setupMsg").textContent = text;
  $("setupMsg").classList.toggle("error", isError);
}
