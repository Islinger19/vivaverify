# VivaVerify

**An AI viva examiner for your own project report.** Upload your report, and an agent plans a short
viva from it, asks the questions out loud, listens to your spoken answers (English, Hindi or Hinglish),
grades each answer against your own document, follows up when an answer is weak, and ends with a
scorecard that points to the exact sections you should re-read.

## How it works

```
planner -> examiner -> judge -> follow_up     -> examiner   (score < 3, max one follow-up per question)
                             -> next_question -> examiner
                             -> reporter      -> END
```

| Part | What it does |
|---|---|
| Planner (LLM) | Reads the chunked report, plans 4 questions with expected points and evidence chunk ids |
| Examiner | Pauses the graph with LangGraph `interrupt()` until the student answers (human in the loop) |
| Guardrails | Regex checks for "give me full marks"-style manipulation and empty answers, run before the LLM |
| Retriever | BM25 over the report chunks, to ground the judge in the right sections |
| Judge (LLM) | Scores 0-5 against the expected points; any contradiction with the report caps the score at 2 |
| Router | Conditional edge: follow-up, next question, or final report |
| Reporter | Deterministic scoring (follow-up recovery capped at 4/5), verdict and "re-read" excerpts |
| LLM access | Direct calls to Gemini, then OpenRouter (OpenAI-compatible APIs); failing providers cool down for 5 min |
| Voice | Browser talks directly to Soniox real-time STT/TTS WebSockets with short-lived keys from the backend |

## Screens

Setup (questions 3-5, strictness Friendly/Standard/Strict, language, voice) -> Report analysed (chunks + viva plan)
-> Viva (question, mic/typing, feedback cards, live agent trace with timings) -> Result (summary with sections to
re-read, full transcript) -> My progress (score per attempt, weakest topics, history; stored in the browser) ->
How it works.

## Run locally

```bash
cd backend
uv venv .venv && uv pip install -r requirements.txt --python .venv/Scripts/python.exe   # Windows
cp .env.example .env   # fill in GEMINI_API_KEY, OPENROUTER_API_KEY, SONIOX_API_KEY
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000 (FastAPI also serves `frontend/` locally).

## Tests and evaluation

```bash
.venv/Scripts/python -m pytest -q            # 19 offline tests, fake LLM, no API calls
.venv/Scripts/python eval/run_eval.py        # live judge accuracy + simulated students (~25 LLM calls)
.venv/Scripts/python eval/voice_roundtrip.py # one Soniox TTS -> STT -> judge round trip
python eval/ui_demo.py                       # two real vivas in Edge, screenshots of every screen (server must run)
```

Results are saved in `backend/eval/` (`results.md`, `results.json`, `voice_roundtrip.json`, `screenshots/`).

## Deploy

**Backend (VM, HTTPS via Dynu DNS + certbot):** copy the `vivaverify/` folder to the VM, put the real
`backend/.env` in place (set `ALLOWED_ORIGINS` to the Vercel URL), then:

```bash
sudo DOMAIN=yourname.dynu.net EMAIL=you@example.com bash backend/deploy/setup_vm.sh
```

This installs nginx + certbot, runs uvicorn under systemd (`vivaverify.service`, one worker), proxies the
`/ws/viva` WebSocket, and gets a Let's Encrypt certificate.

**Frontend (Vercel):** set the backend domain in `frontend/config.js`, then deploy the `frontend/` folder
(Root Directory = `vivaverify/frontend`, no build command). `vercel.json` adds the microphone permission
and security headers.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Status and configured providers |
| `POST /api/documents` | Upload `file` (PDF/MD/TXT, max 5 MB) or `text`; returns `doc_id` |
| `POST /api/voice-token` | Two short-lived Soniox keys (`stt`, `tts`), rate-limited to 10 per IP per 10 min |
| `WS /ws/viva` | Send `{"type":"start","doc_id":...}`, then `{"type":"answer","text":...}` per question. Receives `step`, `question`, `evaluation`, `report`, `error` events |

## Known limitations

- Documents and sessions are kept in memory (single worker); a restart clears them.
- Free-tier LLMs take about 5-14 s per judged answer; Gemini's free tier often returns 503 and is skipped.
- Voice input uses MediaRecorder WebM/Ogg, so it works in Chrome, Edge and Firefox, not Safari (typing still works).
- Scores are guidance from an AI examiner, not an official grade.
