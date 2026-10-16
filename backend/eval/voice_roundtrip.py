"""One end-to-end voice test (run once - Soniox usage is kept minimal):

  text answer -> Soniox TTS WebSocket -> audio -> Soniox STT WebSocket -> transcript -> LLM judge

Authenticates exactly like the browser does: short-lived keys sent in the WebSocket
subprotocol list, so it also proves the browser path works.
Usage: .venv/Scripts/python eval/voice_roundtrip.py
"""
import base64
import difflib
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agent, document, main, voice  # noqa: E402,F401  (main loads .env)
from websockets.sync.client import connect  # noqa: E402

HERE = Path(__file__).resolve().parent
RATE = 16000
ANSWER = ("We added a liveness check based on blink detection. The eye aspect ratio is computed from facial "
          "landmarks, and a student is marked present only if they blink within three seconds, so printed "
          "photos and phone screens are rejected.")
QUESTION = {"topic": "Liveness", "question": "How does your system stop someone using a photo for proxy attendance?",
            "expected_points": ["blink detection liveness check", "eye aspect ratio from facial landmarks",
                                "must blink within 3 seconds / EAR below 0.21"], "evidence": ["C3"]}


def tts(key: str) -> tuple[bytes, dict]:
    t0 = time.time()
    audio, first = bytearray(), None
    with connect("wss://tts-rt.soniox.com/tts-websocket", subprotocols=["soniox-api-key", key],
                 ping_interval=None) as ws:
        proto = ws.subprotocol
        ws.send(json.dumps({"stream_id": "s1", "model": "tts-rt-v2", "language": "en", "voice": "Adrian",
                            "audio_format": "pcm_s16le", "sample_rate": RATE}))
        ws.send(json.dumps({"stream_id": "s1", "text": ANSWER, "text_end": False}))
        ws.send(json.dumps({"stream_id": "s1", "text": "", "text_end": True}))   # same as the SDK's finish()
        while True:
            msg = json.loads(ws.recv(timeout=30))
            if msg.get("error_message"):
                raise RuntimeError(msg)
            if msg.get("audio"):
                first = first or time.time() - t0
                audio += base64.b64decode(msg["audio"])
            if msg.get("terminated"):
                break
    return bytes(audio), {"subprotocol_echoed": proto, "first_audio_s": round(first, 2),
                          "total_s": round(time.time() - t0, 2), "audio_s": round(len(audio) / 2 / RATE, 2)}


def stt(key: str, audio: bytes) -> tuple[str, dict]:
    t0 = time.time()
    text = ""
    with connect("wss://stt-rt.soniox.com/transcribe-websocket", subprotocols=["soniox-api-key", key],
                 ping_interval=None) as ws:
        proto = ws.subprotocol
        ws.send(json.dumps({"model": "stt-rt-v5", "audio_format": "pcm_s16le", "sample_rate": RATE,
                            "num_channels": 1, "language_hints": ["en", "hi"]}))
        for i in range(0, len(audio), 3200):        # 100 ms frames
            ws.send(audio[i:i + 3200])
        ws.send("")                                   # empty text frame = end of audio
        while True:
            msg = json.loads(ws.recv(timeout=30))
            if msg.get("error_message"):
                raise RuntimeError(msg)
            text += "".join(t["text"] for t in msg.get("tokens", []) if t.get("is_final") and not t["text"].startswith("<"))
            if msg.get("finished"):
                break
    return text.strip(), {"subprotocol_echoed": proto, "total_s": round(time.time() - t0, 2)}


def words(s: str) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in s).split()


if __name__ == "__main__":
    keys = voice.temporary_keys(ttl_s=300)
    audio, tts_info = tts(keys["tts"])
    with wave.open(str(HERE / "voice_answer.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE), w.writeframes(audio)
    transcript, stt_info = stt(keys["stt"], audio)
    similarity = difflib.SequenceMatcher(None, words(ANSWER), words(transcript)).ratio()

    # Grade the transcribed answer with the real judge node.
    doc_id = document.add_document("sample", (HERE.parent / "samples" / "smart_attendance_report.md").read_text("utf-8"))["doc_id"]
    state = {"doc_id": doc_id, "plan": [QUESTION], "qi": 0, "question": QUESTION["question"], "followups": 0,
             "answer": transcript}
    judged = agent.evaluate(state)["results"][0]

    out = {"spoken_text": ANSWER, "transcript": transcript, "word_similarity": round(similarity, 3),
           "tts": tts_info, "stt": stt_info,
           "judge": {k: judged[k] for k in ("score", "provider", "covered", "missing", "feedback")}}
    (HERE / "voice_roundtrip.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
