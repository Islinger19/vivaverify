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


def test_fallback_skips_failing_providers_and_cools_them_down(monkeypatch):
    calls = []

    class FakeOpenAI:
        def __init__(self, base_url, **kw):
            self.base_url = base_url
            self.chat = self
            self.completions = self

        def create(self, model, **kw):
            calls.append(self.base_url)
            if "openrouter" not in self.base_url:   # Gemini (first in line) is "down"
                raise RuntimeError("429 rate limit / 503 high demand")
            msg = type("M", (), {"content": '{"ok": true}'})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_down_until", {})
    for var in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.setenv(var, "x")

    assert llm.chat_json("s", "u") == ({"ok": True}, "openrouter")
    tried = len(calls)                       # gemini, gemini-backup, then openrouter
    assert llm.chat_json("s", "a different question") == ({"ok": True}, "openrouter")
    assert len(calls) == tried + 1           # the failing providers were skipped while cooling down


def test_cooling_down_providers_get_a_last_chance_when_all_else_fails(monkeypatch):
    class FakeOpenAI:
        def __init__(self, base_url, **kw):
            self.base_url = base_url
            self.chat = self
            self.completions = self

        def create(self, model, **kw):
            if "openrouter" not in self.base_url:
                raise RuntimeError("503")
            msg = type("M", (), {"content": '{"ok": true}'})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    for var in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.setenv(var, "x")
    # OpenRouter is still cooling down from an earlier failure, but it has recovered and is the only one that works
    monkeypatch.setattr(llm, "_down_until", {"openrouter": 9e12, "openrouter-backup": 9e12, "openrouter-auto": 9e12})
    assert llm.chat_json("s", "u") == ({"ok": True}, "openrouter")


def test_identical_requests_are_served_from_the_cache(monkeypatch):
    calls = []

    class FakeOpenAI:
        def __init__(self, base_url, **kw):
            self.chat = self
            self.completions = self

        def create(self, model, **kw):
            calls.append(model)
            msg = type("M", (), {"content": '{"score": 4}'})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "_down_until", {})
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    first = llm.chat_json("judge", "answer A")
    again = llm.chat_json("judge", "answer A")
    other = llm.chat_json("judge", "answer B")
    assert first == ({"score": 4}, "gemini") and again == ({"score": 4}, "gemini")
    assert other[1] == "gemini" and len(calls) == 2   # a different answer is graded fresh
