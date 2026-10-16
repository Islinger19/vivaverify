import pytest

from app import document, llm
from tests.conftest import SAMPLE


def test_chunks_are_numbered_bounded_and_lossless():
    text = SAMPLE.read_text(encoding="utf-8")
    chunks = document.chunk_text(text)
    assert [c["id"] for c in chunks] == [f"C{i}" for i in range(1, len(chunks) + 1)]
    assert all(len(c["text"].split()) <= document.CHUNK_WORDS * 1.5 for c in chunks)
    assert sum(len(c["text"].split()) for c in chunks) == len(text.split())


def test_too_short_document_is_rejected():
    with pytest.raises(ValueError):
        document.add_document("tiny", "Just one short paragraph about my project.")


def test_bm25_finds_the_section_that_answers_the_question(doc_id):
    assert document.search(doc_id, "blink eye aspect ratio liveness")[0]["id"] == "C3"
    assert document.search(doc_id, "accuracy low light percent")[0]["id"] == "C5"


@pytest.mark.parametrize("raw", [
    '{"score": 4}',
    '```json\n{"score": 4}\n```',
    '<think>let me grade this</think>\nHere you go: {"score": 4}',
])
def test_extract_json_tolerates_fences_and_reasoning(raw):
    assert llm.extract_json(raw) == {"score": 4}


def test_fallback_skips_a_failing_provider_and_cools_it_down(monkeypatch):
    calls = []

    class FakeOpenAI:
        def __init__(self, base_url, **kw):
            self.base_url = base_url
            self.chat = self
            self.completions = self

        def create(self, model, **kw):
            calls.append(model)
            if "googleapis" in self.base_url:
                raise RuntimeError("503 high demand")
            msg = type("M", (), {"content": '{"ok": true}'})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_down_until", {})
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "y")

    assert llm.chat_json("s", "u") == ({"ok": True}, "openrouter")
    assert llm.chat_json("s", "u") == ({"ok": True}, "openrouter")
    assert sum("gemini" in m for m in calls) == 1   # second call skipped Gemini (cooldown)
