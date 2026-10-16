"""Turns an uploaded report into small numbered chunks ([C1], [C2], ...) plus a BM25 index.

The chunk ids are what the planner cites as evidence and what the final gap report
points the student back to.
"""
import hashlib
import io
import re
import uuid

from pypdf import PdfReader
from rank_bm25 import BM25Okapi

MAX_CHARS = 60_000      # roughly a 20-page report; keeps prompts inside free-tier limits
CHUNK_WORDS = 120
MIN_CHUNKS = 3
MAX_DOCS = 200          # in-memory store, oldest documents are dropped first

STOP = set("a an the and or of to in on for with is are was were be been it this that as by at from we our "
           "i you he she they them their its into than then so such can will would should could has have had "
           "do does did not no yes what why how which who when where".split())

DOCS: dict[str, dict] = {}


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    return data.decode("utf-8", errors="ignore")


def plain(text: str) -> str:
    """Chunk text without markdown heading marks, for showing to the student."""
    return re.sub(r"#+\s*", "", text)


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def chunk_text(text: str, size: int = CHUNK_WORDS) -> list[dict]:
    """Greedy paragraph packing: whole paragraphs up to `size` words, long ones are split."""
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    chunks, buf = [], []
    for para in filter(None, paragraphs):
        words = para.split()
        if buf and len(buf) + len(words) > size:
            chunks.append(" ".join(buf))
            buf = []
        buf.extend(words)
        while len(buf) > size * 1.5:
            chunks.append(" ".join(buf[:size]))
            buf = buf[size:]
    if buf:
        chunks.append(" ".join(buf))
    return [{"id": f"C{i + 1}", "text": c} for i, c in enumerate(chunks)]


def add_document(title: str, text: str) -> dict:
    text = (text or "")[:MAX_CHARS]
    chunks = chunk_text(text)
    if len(chunks) < MIN_CHUNKS:
        raise ValueError("Document is too short for a viva - upload at least ~300 words of your report.")
    while len(DOCS) >= MAX_DOCS:
        DOCS.pop(next(iter(DOCS)))
    doc_id = uuid.uuid4().hex[:12]
    DOCS[doc_id] = {"title": title, "chunks": chunks, "key": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "bm25": BM25Okapi([tokenize(c["text"]) for c in chunks])}
    outline = [{"id": c["id"], "preview": " ".join(plain(c["text"]).split()[:16]) + " ..."} for c in chunks]
    return {"doc_id": doc_id, "title": title, "chunks": len(chunks), "words": len(text.split()), "outline": outline}


def search(doc_id: str, query: str, k: int = 2) -> list[dict]:
    doc = DOCS[doc_id]
    scores = doc["bm25"].get_scores(tokenize(query))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [doc["chunks"][i] for i in ranked if scores[i] > 0]


def get_chunks(doc_id: str, ids: list[str]) -> list[dict]:
    by_id = {c["id"]: c for c in DOCS[doc_id]["chunks"]}
    return [by_id[i] for i in ids if i in by_id]
