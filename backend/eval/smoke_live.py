"""Smoke test of a deployed backend: uploads the sample report and runs a 3-question viva over the WebSocket,
answering every question with the same well-informed answer. Uses about 4 LLM calls.
Usage: .venv/Scripts/python eval/smoke_live.py [https://your-backend]
"""
import json
import sys
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://islinger19.ddnsfree.com").rstrip("/")
SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "smart_attendance_report.md"
ANSWER = ("Roll call wasted 8 to 10 minutes per lecture and proxy attendance was common. We used face_recognition "
          "because we only had 5 photos per student, and it gives pre-trained 128-d embeddings; HOG runs in real time "
          "on the Raspberry Pi. Blink detection with eye aspect ratio below 0.21 within 3 seconds stops photo spoofing. "
          "Accuracy was 94.6 percent in normal light, 89.3 with backlight and 81 percent in low light.")

print("health:", httpx.get(f"{BASE}/api/health", timeout=20).json())
doc = httpx.post(f"{BASE}/api/documents", files={"file": (SAMPLE.name, SAMPLE.read_bytes(), "text/markdown")},
                 timeout=30).json()
print(f"uploaded: {doc['words']} words, {doc['chunks']} chunks")

t0 = time.time()
with connect(BASE.replace("https", "wss").replace("http", "ws") + "/ws/viva", open_timeout=20) as ws:
    ws.send(json.dumps({"type": "start", "doc_id": doc["doc_id"], "num_questions": 3, "strictness": "standard"}))
    while True:
        ev = json.loads(ws.recv(timeout=180))
        if ev["type"] == "step":
            print(f"  step  {ev['node']:<9} {ev.get('ms', 0) / 1000:5.1f}s  {ev['detail']}")
        elif ev["type"] == "question":
            print(f"  ask   Q{ev['index']}: {ev['question']}")
            ws.send(json.dumps({"type": "answer", "text": ANSWER}))
        elif ev["type"] == "report":
            print(f"report: {ev['percent']}% - {ev['verdict']} ({time.time() - t0:.0f}s total)")
            break
        elif ev["type"] == "error":
            print("ERROR:", ev["message"])
            sys.exit(1)
