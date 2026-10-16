from fastapi.testclient import TestClient

from app import main, voice
from tests.conftest import SAMPLE

client = TestClient(main.app)


def test_health():
    assert client.get("/api/health").json()["ok"] is True


def test_upload_rejects_short_text_and_accepts_a_report():
    assert client.post("/api/documents", data={"text": "too short"}).status_code == 400
    r = client.post("/api/documents", files={"file": ("report.md", SAMPLE.read_bytes(), "text/markdown")})
    assert r.status_code == 200 and r.json()["chunks"] >= 3


def test_full_viva_over_websocket(fake_llm):
    fake_llm([5, 1, 4, 5, 5])
    doc_id = client.post("/api/documents", data={"text": SAMPLE.read_text(encoding="utf-8")}).json()["doc_id"]
    seen = []
    with client.websocket_connect("/ws/viva") as ws:
        ws.send_json({"doc_id": doc_id})
        while True:
            event = ws.receive_json()
            seen.append(event["type"])
            if event["type"] == "question":
                ws.send_json({"type": "answer", "text": "Because of proxy attendance and wasted lecture time."})
            if event["type"] in ("report", "error"):
                break
    assert seen.count("question") == 5 and seen[-1] == "report"
    assert "step" in seen and "evaluation" in seen


def test_unknown_document_is_refused_over_websocket():
    with client.websocket_connect("/ws/viva") as ws:
        ws.send_json({"doc_id": "nope"})
        assert ws.receive_json()["type"] == "error"


def test_voice_tokens_are_rate_limited(monkeypatch):
    monkeypatch.setattr(voice, "temporary_keys", lambda: {"stt": "a", "tts": "b"})
    main._token_log.clear()
    codes = [client.post("/api/voice-token").status_code for _ in range(main.VOICE_TOKENS_PER_10_MIN + 1)]
    assert codes[:-1] == [200] * main.VOICE_TOKENS_PER_10_MIN and codes[-1] == 429
