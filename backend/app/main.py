"""HTTP + WebSocket API for VivaVerify.

POST /api/documents    upload a report (PDF / MD / TXT file, or pasted text)
POST /api/voice-token  short-lived Soniox keys for the browser's STT/TTS WebSockets
WS   /ws/viva          runs one viva: streams agent steps, questions, scores and the report
"""
import asyncio
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from langgraph.types import Command  # noqa: E402

from . import agent, document, llm, voice  # noqa: E402

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
VOICE_TOKENS_PER_10_MIN = 10
UPLOADS_PER_10_MIN = 30   # per IP; a whole class behind one college network must still fit
VIVAS_PER_10_MIN = 20     # each viva costs about 5-9 LLM calls of the free-tier quota
STRICTNESS = {"friendly": 2, "standard": 3, "strict": 4}   # pass mark out of 5
BUSY_MESSAGE = ("The AI examiner is busy right now: every free AI model is overloaded or out of quota. "
                "Please try again in a minute.")

ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="VivaVerify")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])
_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def allow(kind: str, ip: str, limit: int, window_s: int = 600) -> bool:
    """Sliding-window rate limit per client IP (uvicorn --proxy-headers gives the real IP behind nginx)."""
    log = _hits[(kind, ip)]
    now = time.time()
    while log and now - log[0] > window_s:
        log.popleft()
    if len(log) >= limit:
        return False
    log.append(now)
    return True


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """CORS does not cover WebSockets, so the browser's Origin is checked here when ALLOWED_ORIGINS is set."""
    if "*" in ORIGINS or not origin:   # no Origin header: not a browser (smoke test, eval scripts)
        return True
    return origin in ORIGINS or urlparse(origin).netloc == host   # the backend also serves the frontend itself


@app.get("/api/health")
def health():
    return {"ok": True, "llm_providers": [p["name"] for p in llm.providers() if p["key"]],
            "voice": bool(os.getenv("SONIOX_API_KEY"))}


@app.post("/api/documents")
async def upload_document(request: Request, file: UploadFile | None = File(None), text: str = Form("")):
    if not allow("upload", request.client.host if request.client else "unknown", UPLOADS_PER_10_MIN):
        raise HTTPException(429, "Too many uploads - try again in a few minutes.")
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large (max 5 MB).")
        try:
            body = document.extract_text(file.filename, data)
        except Exception:
            raise HTTPException(400, "Could not read this file - upload a text PDF, MD or TXT, or paste the text.")
        title = file.filename
    else:
        title, body = "Pasted text", text
    try:
        return document.add_document(title, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/voice-token")
def voice_token(request: Request):
    if not allow("voice", request.client.host if request.client else "unknown", VOICE_TOKENS_PER_10_MIN):
        raise HTTPException(429, "Too many voice sessions - try again in a few minutes.")
    try:
        return voice.temporary_keys()
    except Exception as e:
        raise HTTPException(502, f"Voice service unavailable: {str(e)[:200]}")


async def relay(ws: WebSocket, session: str, graph_input) -> str:
    """Run the graph in a worker thread and forward its events live.
    Returns "report" when the viva has ended, "error" when a step failed (it can be retried), else "question"."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker():
        try:
            for event in agent.run(session, graph_input):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except llm.LLMError as e:
            print(f"LLM failure: {e}", flush=True)   # full detail stays in the server log
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": BUSY_MESSAGE, "retry": True})
        except Exception as e:
            print(f"Viva error: {e!r}", flush=True)
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "retry": True,
                                                         "message": "Something went wrong - please try again."})
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, worker)
    outcome = "question"
    while (event := await queue.get()) is not None:
        await ws.send_json(event)
        if event["type"] in ("report", "error"):
            outcome = event["type"]
    return outcome


@app.websocket("/ws/viva")
async def viva(ws: WebSocket):
    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=1008)
        return
    await ws.accept()
    session = uuid.uuid4().hex
    try:
        start = await ws.receive_json()
        if start.get("doc_id") not in document.DOCS:
            await ws.send_json({"type": "error", "message": "Unknown document - please upload it again."})
            return
        if not allow("viva", ws.client.host if ws.client else "unknown", VIVAS_PER_10_MIN):
            await ws.send_json({"type": "error", "message": "Too many vivas from this network - try again in a few minutes."})
            return
        try:
            num_questions = min(5, max(3, int(start.get("num_questions") or agent.MAX_QUESTIONS)))
        except (TypeError, ValueError):
            num_questions = agent.MAX_QUESTIONS
        graph_input = {"doc_id": start["doc_id"], "results": [], "num_questions": num_questions,
                       "pass_score": STRICTNESS.get(start.get("strictness"), agent.PASS_SCORE)}
        while (outcome := await relay(ws, session, graph_input)) != "report":
            message = await ws.receive_json()
            # After a failed step, resuming with None re-runs that step from the last checkpoint, so the
            # student's answer and the viva so far are kept.
            graph_input = None if outcome == "error" else Command(resume=str(message.get("text", "")))
    except WebSocketDisconnect:
        pass
    finally:
        agent.GRAPH.checkpointer.delete_thread(session)
        try:
            await ws.close()
        except RuntimeError:
            pass


# Local development: serve the frontend from the same origin (on Vercel it is hosted separately).
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
