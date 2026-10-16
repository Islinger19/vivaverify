import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import llm, main, voice
from tests.conftest import PLAN, SAMPLE

client = TestClient(main.app)
ANSWER = "Because of proxy attendance and wasted lecture time."


@pytest.fixture(autouse=True)
def no_rate_limits_between_tests():
    main._hits.clear()


def upload_sample() -> str:
    return client.post("/api/documents", data={"text": SAMPLE.read_text(encoding="utf-8")}).json()["doc_id"]


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
    codes = [client.post("/api/voice-token").status_code for _ in range(main.VOICE_TOKENS_PER_10_MIN + 1)]
    assert codes[:-1] == [200] * main.VOICE_TOKENS_PER_10_MIN and codes[-1] == 429


def test_llm_outage_shows_a_friendly_message(monkeypatch):
    from app import llm

    def down(*a, **k):
        raise llm.LLMError("all LLM providers failed -> gemini: 503 | openrouter: 429")

    monkeypatch.setattr(llm, "chat_json", down)
    doc_id = client.post("/api/documents", data={"text": SAMPLE.read_text(encoding="utf-8")}).json()["doc_id"]
    with client.websocket_connect("/ws/viva") as ws:
        ws.send_json({"doc_id": doc_id})
        event = ws.receive_json()
        while event["type"] != "error":
            event = ws.receive_json()
    assert event["message"] == main.BUSY_MESSAGE


def test_a_failed_judge_call_can_be_retried_without_losing_the_viva(monkeypatch):
    judged = []

    def flaky(system, user, temperature=0.2):
        if system.startswith("You are a strict university viva examiner"):
            return PLAN, "fake"
        judged.append(user)
        if len(judged) == 2:                    # every provider is down while Q2 is being graded
            raise llm.LLMError("all LLM providers failed")
        return {"score": 5, "covered": [], "missing": [], "contradictions": [], "feedback": "", "followup": None}, "fake"

    monkeypatch.setattr(llm, "chat_json", flaky)
    seen = []
    with client.websocket_connect("/ws/viva") as ws:
        ws.send_json({"type": "start", "doc_id": upload_sample()})
        while True:
            event = ws.receive_json()
            seen.append(event["type"])
            if event["type"] == "question":
                ws.send_json({"type": "answer", "text": ANSWER})
            elif event["type"] == "error":
                assert event["retry"] and event["message"] == main.BUSY_MESSAGE
                ws.send_json({"type": "retry"})
            elif event["type"] == "report":
                break
    assert seen.count("error") == 1 and seen.count("question") == 4
    assert len(event["questions"]) == 4 and event["percent"] == 100
    assert judged[1] == judged[2]               # the same answer was graded again, not lost


def test_bad_settings_and_unreadable_files_do_not_crash(fake_llm):
    fake_llm([5, 5, 5, 5])
    r = client.post("/api/documents", files={"file": ("report.pdf", b"%PDF-1.4 this is not really a pdf", "application/pdf")})
    assert r.status_code == 400
    with client.websocket_connect("/ws/viva") as ws:
        ws.send_json({"type": "start", "doc_id": upload_sample(), "num_questions": "lots"})
        while (event := ws.receive_json())["type"] not in ("question", "error"):
            pass
    assert event["type"] == "question" and event["total"] == 4


def test_uploads_are_rate_limited():
    codes = [client.post("/api/documents", data={"text": "too short"}).status_code
             for _ in range(main.UPLOADS_PER_10_MIN + 1)]
    assert codes[:-1] == [400] * main.UPLOADS_PER_10_MIN and codes[-1] == 429


def test_websocket_from_another_website_is_refused(monkeypatch):
    monkeypatch.setattr(main, "ORIGINS", ["https://vivaverify.vercel.app"])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/viva", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with client.websocket_connect("/ws/viva", headers={"origin": "https://vivaverify.vercel.app"}) as ws:
        ws.send_json({"doc_id": "nope"})
        assert ws.receive_json()["type"] == "error"
