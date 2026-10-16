"""Offline fixtures: the LLM is replaced by a scripted fake so tests cost no API calls."""
from pathlib import Path

import pytest

from app import agent, document, llm

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "smart_attendance_report.md"

PLAN = {"questions": [
    {"topic": "Problem", "question": "Why did your department need automatic attendance?",
     "expected_points": ["roll call wastes 8-10 minutes", "proxy attendance"], "evidence": ["C1"]},
    {"topic": "Model choice", "question": "Why did you use face_recognition instead of training a CNN?",
     "expected_points": ["only 5 photos per student", "pre-trained 128-d embeddings"], "evidence": ["C2", "C99"]},
    {"topic": "Liveness", "question": "How does your system stop photo spoofing?",
     "expected_points": ["blink detection", "eye aspect ratio below 0.21"], "evidence": ["C3"]},
    {"topic": "Results", "question": "How accurate was the system in low light?",
     "expected_points": ["81 percent in low light"], "evidence": ["C5"]},
]}


class FakeLLM:
    """Returns the plan for planner prompts and a queued score for every judge prompt."""

    def __init__(self, scores, followup="What exactly did the blink check measure?"):
        self.scores = list(scores)
        self.followup = followup
        self.judge_calls = 0

    def __call__(self, system, user, temperature=0.2):
        if system.startswith("You are a strict university viva examiner"):
            return PLAN, "fake"
        self.judge_calls += 1
        score = self.scores.pop(0)
        return ({"score": score, "covered": [], "missing": ["something"] if score < 5 else [],
                 "contradictions": [], "feedback": "ok", "followup": self.followup}, "fake")


@pytest.fixture
def doc_id():
    return document.add_document("sample", SAMPLE.read_text(encoding="utf-8"))["doc_id"]


@pytest.fixture
def fake_llm(monkeypatch):
    def install(scores, **kw):
        fake = FakeLLM(scores, **kw)
        monkeypatch.setattr(llm, "chat_json", fake)
        return fake
    return install


@pytest.fixture
def graph():
    return agent.build_graph()


@pytest.fixture(autouse=True)
def isolated_llm_cache(tmp_path, monkeypatch):
    """Every test gets an empty response cache, so results never leak between tests."""
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
