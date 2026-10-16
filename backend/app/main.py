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

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from langgraph.types import Command  # noqa: E402

from . import agent, document, llm, voice  # noqa: E402

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
VOICE_TOKENS_PER_10_MIN = 10
STRICTNESS = {"friendly": 2, "standard": 3, "strict": 4}   # pass mark out of 5

app = FastAPI(title="VivaVerify")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])
_token_log: dict[str, deque] = defaultdict(deque)


@app.get("/api/health")
def health():
    return {"ok": True, "llm_providers": [p["name"] for p in llm.providers() if p["key"]],
            "voice": bool(os.getenv("SONIOX_API_KEY"))}


@app.post("/api/documents")
async def upload_document(file: UploadFile | None = File(None), text: str = Form("")):
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large (max 5 MB).")
        title, body = file.filename, document.extract_text(file.filename, data)
    else:
        title, body = "Pasted text", text
    try:
        return document.add_document(title, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/voice-token")
def voice_token(request: Request):
    log = _token_log[request.client.host if request.client else "unknown"]
    now = time.time()
    while log and now - log[0] > 600:
        log.popleft()
    if len(log) >= VOICE_TOKENS_PER_10_MIN:
        raise HTTPException(429, "Too many voice sessions - try again in a few minutes.")
    log.append(now)
    try:
        return voice.temporary_keys()
    except Exception as e:
        raise HTTPException(502, f"Voice service unavailable: {str(e)[:200]}")


async def relay(ws: WebSocket, session: str, graph_input) -> bool:
    """Run the graph in a worker thread and forward its events live. True when the viva has ended."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker():
        try:
            for event in agent.run(session, graph_input):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)[:300]})
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, worker)
    ended = False
    while (event := await queue.get()) is not None:
        await ws.send_json(event)
        ended = ended or event["type"] in ("report", "error")
    return ended


@app.websocket("/ws/viva")
async def viva(ws: WebSocket):
    await ws.accept()
    session = uuid.uuid4().hex
    try:
        start = await ws.receive_json()
        if start.get("doc_id") not in document.DOCS:
            await ws.send_json({"type": "error", "message": "Unknown document - please upload it again."})
            return
        graph_input = {"doc_id": start["doc_id"], "results": [],
                       "num_questions": min(5, max(3, int(start.get("num_questions") or agent.MAX_QUESTIONS))),
                       "pass_score": STRICTNESS.get(start.get("strictness"), agent.PASS_SCORE)}
        while not await relay(ws, session, graph_input):
            message = await ws.receive_json()
            graph_input = Command(resume=message.get("text", ""))
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
