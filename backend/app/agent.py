"""The viva examiner as a LangGraph state machine.

    planner -> examiner -> judge -> follow_up     -> examiner   (weak answer, one probe allowed)
                                 -> next_question -> examiner
                                 -> reporter      -> END

`examiner` pauses the graph with interrupt() until the student's answer arrives. The
checkpointer keeps every viva's state between answers, keyed by thread_id = session id.
"""
import operator
import re
import time
from collections.abc import Iterator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import document, llm

MAX_QUESTIONS = 4   # default; the student can choose 3-5
MAX_FOLLOWUPS = 1   # per question
PASS_SCORE = 3      # default out of 5 ("standard"); below this the router asks a follow-up
FOLLOWUP_CAP = 4    # a follow-up can raise a question's score, but not to full marks

# Deterministic guardrails: run before the LLM judge, so they cannot be talked out of.
# Aimed at instructions to the examiner. A student describing their own project ("the system prompt tells the judge",
# "you are now marked present", "we block phrases like full marks") must not match.
MANIPULATION = re.compile(r"\b(ignore|disregard|forget) (all |the |your |any )?(previous|above|prior|earlier|system) "
                          r"(instructions?|prompts?|rules?)|"
                          r"\b(give|award|grant) me (a |the )?(5|five|full|max|maximum|top|perfect)\b|"
                          r"\bi (deserve|should get) (a |the )?(5|five|full|max|maximum|top|perfect) (marks?|score|points?)\b|"
                          r"\byou are now (a|an|my)\b|\bpretend (that )?you('re| are)\b",
                          re.I)
NO_ANSWER = re.compile(r"^\W*(i\s*(don'?t|do not)\s*know|no idea|not sure|pass|skip|nothing|u+m+|h+m+|pata nahi|nahi pata|"
                       r"पता नहीं|मालूम नहीं)?\W*$", re.I)
NUMBER = re.compile(r"(?<![\w.])\d+(\.\d+)?(?!\w)")   # a standalone number such as 0.21 or 3, not the 25 in BM25

PLANNER_SYSTEM = """You are a strict university viva examiner. You receive a student's project report split into numbered chunks [C1], [C2], ...
Plan a short oral viva that checks whether the student really understands THEIR OWN work.
Return JSON only:
{"questions": [{"topic": "2-4 words", "question": "...", "expected_points": ["...", "..."], "evidence": ["C3", "C7"]}]}
Rules:
- Exactly NUM questions. Each must be ONE question, never two joined with "and", under 30 words, natural to say out loud.
- Cover different areas, in this order where possible: the problem and why it matters; a design choice and the reason for it; how a key part is implemented; testing, results or limitations.
- Ask at the level of a final-year university project viva: questions that need reasoning - why a choice was made, how a part works, what trade-off or alternative was considered, what a result shows or why it happened.
- Never ask for a single number, a definition, or anything answerable with yes/no or one word. About results, ask what they show, not just the figures.
- expected_points: 2-4 short facts a good answer must contain, taken only from the report.
- evidence: ids of the chunks those facts come from."""

JUDGE_SYSTEM = """You grade one spoken viva answer. The student may answer in English, Hindi or Hinglish: judge the meaning, not the language, grammar or small speech-to-text errors.
Use ONLY the report excerpts as ground truth.
If a follow-up exchange is included, grade the student's understanding across BOTH answers together, as one answer to the original question.
Return JSON only:
{"score": 0-5, "covered": ["expected points the answer got right"], "missing": ["expected points not covered"], "contradictions": ["claims that conflict with the report"], "feedback": "one or two sentences to the student", "followup": "one short question probing the most important missing point, or null"}
Scoring: 5 = all expected points with clear reasoning; 4 = most points; 3 = about half and no errors; 2 = vague or mostly missing; 1 = mostly wrong; 0 = irrelevant.
contradictions: only real factual conflicts with the report (a wrong method, number, reason or result). Never list a missing point, a vague statement, or a word that speech-to-text probably misheard (such as "VM25" for "BM25").
The followup must ask about ONE missing point only, in under 20 words. It must ask the student to explain why or how, never for a single number, name or count, and must not contain the answer (no numbers or facts from the report)."""
FOLLOWUP_MAX_WORDS = 25


class VivaState(TypedDict, total=False):
    doc_id: str
    num_questions: int      # viva settings chosen by the student
    pass_score: int
    plan: list[dict]
    qi: int                 # index of the current planned question
    question: str           # text currently being asked (planned question or follow-up)
    followups: int          # follow-ups already asked for the current question
    answer: str
    provider: str           # which LLM answered the last call (shown in the activity trace)
    results: Annotated[list[dict], operator.add]
    report: dict


# Questions already asked per report (keyed by a hash of its text), so "Practise again" gets new questions.
PRACTISED: dict[str, list[str]] = {}
PRACTISED_KEEP = 12


def plan_viva(state: VivaState) -> dict:
    doc = document.DOCS[state["doc_id"]]
    chunks = doc["chunks"]
    report_text = "\n".join(f"[{c['id']}] {c['text']}" for c in chunks)
    asked = PRACTISED.get(doc["key"], [])
    if asked:
        report_text += ("\n\nThe student has already practised these questions on this report. Ask different ones, "
                        "about other points or from a new angle:\n" + "\n".join(f"- {a}" for a in asked))
    n = state.get("num_questions") or MAX_QUESTIONS
    data, provider = llm.chat_json(PLANNER_SYSTEM.replace("NUM", str(n)), report_text)
    valid_ids = {c["id"] for c in chunks}
    plan = []
    for q in data.get("questions", [])[:n]:
        if not q.get("question") or not q.get("expected_points"):
            continue
        plan.append({"topic": q.get("topic") or "General", "question": q["question"].strip(),
                     "expected_points": list(q["expected_points"])[:4],
                     "evidence": [e for e in q.get("evidence", []) if e in valid_ids]})  # drop invented ids
    if not plan:
        raise llm.LLMError("the planner returned no usable questions")
    PRACTISED.pop(doc["key"], None)   # re-insert, so the least recently practised report is dropped first
    PRACTISED[doc["key"]] = (asked + [q["question"] for q in plan])[-PRACTISED_KEEP:]
    while len(PRACTISED) > document.MAX_DOCS:
        PRACTISED.pop(next(iter(PRACTISED)))
    return {"plan": plan, "qi": 0, "followups": 0, "question": plan[0]["question"], "provider": provider}


def ask(state: VivaState) -> dict:
    q = state["plan"][state["qi"]]
    answer = interrupt({"index": state["qi"] + 1, "total": len(state["plan"]), "topic": q["topic"],
                        "question": state["question"], "followup": state["followups"] > 0})
    return {"answer": str(answer).strip()[:3000]}


def parse_score(value) -> int:
    """The judge's score as an int 0-5; tolerates "4", 4.0 and "3/5"."""
    match = re.search(r"\d+(\.\d+)?", str(value if value is not None else 0))
    return max(0, min(5, int(round(float(match.group(0)))))) if match else 0


def question_score(attempts: list[dict]) -> int:
    """A follow-up can raise a question's score, but not to full marks."""
    first, last = attempts[0]["score"], attempts[-1]["score"]
    return max(first, min(last, FOLLOWUP_CAP)) if len(attempts) > 1 else first


def wants_followup(result: dict, followups: int, pass_score: int) -> bool:
    return result["score"] < pass_score and bool(result["next_followup"]) and followups < MAX_FOLLOWUPS


def live_note(result: dict, followup_next: bool, pass_score: int) -> str:
    """What the student sees right after answering. It never names the missing points or quotes the report,
    so it cannot give away the answer to the follow-up; the full feedback is shown after the viva."""
    if result["flag"]:
        return result["feedback"]
    if followup_next:
        reason = "Part of your answer conflicts with your report" if result["contradictions"] else "Some key points were missing"
        return f"{reason}, so the examiner will ask a follow-up."
    note = ("Good answer." if result["score"] >= 4 else
            "Fair answer, but some key points were missing." if result["score"] >= pass_score else
            "Several key points were missing.")
    if result["contradictions"]:
        note += " Part of it conflicts with your report."
    return note + (" The full feedback is in your result." if result["score"] < 5 else "")


def evaluate(state: VivaState) -> dict:
    q = state["plan"][state["qi"]]
    answer = state["answer"]
    earlier = [r for r in state.get("results") or [] if r["index"] == state["qi"] + 1]
    pass_score = state.get("pass_score") or PASS_SCORE
    result = {"index": state["qi"] + 1, "topic": q["topic"], "question": state["question"], "answer": answer,
              "followup": bool(earlier), "evidence": q["evidence"], "flag": None, "points": len(q["expected_points"]),
              "covered": [], "missing": q["expected_points"], "contradictions": [], "next_followup": None}

    def done(provider: str) -> dict:
        result["question_score"] = question_score(earlier + [result])
        result["note"] = live_note(result, wants_followup(result, state.get("followups") or 0, pass_score), pass_score)
        return {"results": [result], "provider": provider}

    if MANIPULATION.search(answer):
        result.update(score=0, flag="manipulation attempt", provider="guardrail",
                      feedback="The answer tried to instruct the examiner instead of answering, so it scores 0.")
        return done("guardrail")
    if NO_ANSWER.match(answer):
        result.update(score=0, flag="no answer", provider="guardrail",
                      feedback="No real answer was given, so it scores 0. Re-read the sections listed in the report.")
        return done("guardrail")

    # Ground the judge: the planner's evidence chunks plus whatever BM25 finds for this exchange.
    chunks = document.get_chunks(state["doc_id"], q["evidence"])
    for c in document.search(state["doc_id"], f"{state['question']} {answer}"):
        if c not in chunks:
            chunks.append(c)
    excerpts = "\n".join(f"[{c['id']}] {c['text']}" for c in chunks)
    if earlier:   # a follow-up is graded together with the first answer, so filling the gap can raise the score
        exchange = (f"Question: {earlier[0]['question']}\nStudent answer: {earlier[0]['answer']}\n\n"
                    f"Follow-up question: {state['question']}\nStudent follow-up answer: {answer}")
    else:
        exchange = f"Question: {state['question']}\nStudent answer: {answer}"
    prompt = f"Report excerpts:\n{excerpts}\n\nExpected points: {q['expected_points']}\n\n{exchange}"
    data, provider = llm.chat_json(JUDGE_SYSTEM, prompt, temperature=0)

    score = parse_score(data.get("score"))
    contradictions = list(data.get("contradictions") or [])
    if contradictions:
        score = min(score, 2)  # a confident wrong claim never passes
    missing = list(data.get("missing") or [])
    followup = str(data.get("followup") or "").strip() or None
    if followup and (len(followup.split()) > FOLLOWUP_MAX_WORDS or followup.count("?") > 1 or NUMBER.search(followup)):
        # One point, one question, and nothing taken from the report (the fallback never quotes the missing point).
        followup = f"Let's go deeper on {q['topic']}. Can you explain that part of your work in more detail?"
    result.update(score=score, provider=provider, covered=list(data.get("covered") or []),
                  missing=missing, contradictions=contradictions,
                  feedback=str(data.get("feedback") or ""), next_followup=followup,
                  evidence=sorted({*q["evidence"], *(c["id"] for c in chunks)}, key=lambda i: int(i[1:])))
    return done(provider)


def route_after_evaluate(state: VivaState) -> str:
    if wants_followup(state["results"][-1], state["followups"], state.get("pass_score") or PASS_SCORE):
        return "follow_up"
    if state["qi"] + 1 < len(state["plan"]):
        return "next_question"
    return "reporter"


def follow_up(state: VivaState) -> dict:
    return {"question": state["results"][-1]["next_followup"], "followups": state["followups"] + 1}


def next_question(state: VivaState) -> dict:
    qi = state["qi"] + 1
    return {"qi": qi, "question": state["plan"][qi]["question"], "followups": 0}


TRANSCRIPT_FIELDS = ("index", "topic", "question", "answer", "score", "flag", "followup", "covered", "missing",
                     "contradictions", "feedback")
# Sent while the viva is running. No missing points, feedback or contradictions: they would reveal the answer
# to the follow-up, and anything sent is readable in the browser's developer tools even if the page hides it.
LIVE_FIELDS = ("index", "topic", "question", "answer", "score", "question_score", "flag", "followup", "note",
               "points", "provider")


def make_report(state: VivaState) -> dict:
    """Deterministic scoring, so the final mark cannot be hallucinated by the LLM."""
    chunks = {c["id"]: c["text"] for c in document.DOCS[state["doc_id"]]["chunks"]}
    rows = []
    for i, q in enumerate(state["plan"], start=1):
        attempts = [r for r in state["results"] if r["index"] == i]
        if not attempts:
            continue
        last = attempts[-1]
        score = question_score(attempts)
        rows.append({"index": i, "topic": q["topic"], "question": q["question"], "score": score,
                     "followup_used": len(attempts) > 1, "flags": [a["flag"] for a in attempts if a["flag"]],
                     "missing": last["missing"] if score < 4 else [],
                     "reread": [{"id": e, "excerpt": " ".join(document.plain(chunks[e]).split()[:28]) + " ..."}
                                for e in q["evidence"][:2]] if score < 4 else []})
    percent = round(100 * sum(r["score"] for r in rows) / (5 * len(rows))) if rows else 0
    verdict = ("Strong understanding" if percent >= 75 else
               "Partial understanding - revise the gaps below" if percent >= 50 else
               "Weak understanding - key parts of the report could not be defended")
    flags = sorted({f for r in rows for f in r["flags"]})
    # The full feedback (including the missing points) reaches the browser only now that the viva is over.
    transcript = [{k: r[k] for k in TRANSCRIPT_FIELDS} for r in state["results"]]
    return {"report": {"percent": percent, "verdict": verdict, "flags": flags, "questions": rows,
                       "transcript": transcript}}


def build_graph(checkpointer=None):
    g = StateGraph(VivaState)
    g.add_node("planner", plan_viva)
    g.add_node("examiner", ask)
    g.add_node("judge", evaluate)
    g.add_node("follow_up", follow_up)
    g.add_node("next_question", next_question)
    g.add_node("reporter", make_report)
    g.add_edge(START, "planner")
    g.add_edge("planner", "examiner")
    g.add_edge("examiner", "judge")
    g.add_conditional_edges("judge", route_after_evaluate, ["follow_up", "next_question", "reporter"])
    g.add_edge("follow_up", "examiner")
    g.add_edge("next_question", "examiner")
    g.add_edge("reporter", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


GRAPH = build_graph()


def to_events(update: dict) -> list[dict]:
    """Translate LangGraph stream updates into the small event protocol the UI understands."""
    events = []
    for node, data in update.items():
        if node == "__interrupt__":
            events.append({"type": "question", **data[0].value})
        elif node == "planner":
            topics = ", ".join(q["topic"] for q in data["plan"])
            events.append({"type": "step", "node": "Planner",
                           "detail": f"Read the report and planned {len(data['plan'])} questions ({topics}) via {data['provider']}."})
            events.append({"type": "plan", "topics": [q["topic"] for q in data["plan"]],
                           "evidence": [q["evidence"] for q in data["plan"]]})
        elif node == "examiner":
            events.append({"type": "step", "node": "Examiner", "detail": "Answer received."})
        elif node == "judge":
            r = data["results"][-1]
            events.append({"type": "step", "node": "Judge",
                           "detail": f"Scored {r['score']}/5 via {r['provider']}" + (f" - flagged: {r['flag']}." if r["flag"] else ".")})
            events.append({"type": "evaluation", "covered_count": min(len(r["covered"]), r["points"]),
                           **{k: r[k] for k in LIVE_FIELDS}})
        elif node == "follow_up":
            events.append({"type": "step", "node": "Router",
                           "detail": "Below the pass mark, so asking one follow-up on the missing point."})
        elif node == "next_question":
            events.append({"type": "step", "node": "Router", "detail": f"Moving to question {data['qi'] + 1}."})
        elif node == "reporter":
            events.append({"type": "report", **data["report"]})
    return events


def run(session_id: str, graph_input) -> Iterator[dict]:
    """Advance one viva until it pauses for an answer or finishes; yields UI events as they happen."""
    config = {"configurable": {"thread_id": session_id}}
    last = time.perf_counter()
    for update in GRAPH.stream(graph_input, config, stream_mode="updates"):
        now = time.perf_counter()
        for event in to_events(update):
            if event["type"] == "step":
                event["ms"] = round((now - last) * 1000)   # time spent in this node (shown in the trace)
            yield event
        last = now
