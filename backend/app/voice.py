"""Mints short-lived Soniox keys so the browser can open the real-time STT and TTS
WebSockets directly. The project key never leaves the server."""
import os

import httpx

TEMP_KEY_URL = "https://api.soniox.com/v1/auth/temporary-api-key"


def temporary_keys(ttl_s: int = 1800) -> dict:
    headers = {"Authorization": f"Bearer {os.environ['SONIOX_API_KEY']}"}
    keys = {}
    for name, usage in (("stt", "transcribe_websocket"), ("tts", "tts_rt")):
        r = httpx.post(TEMP_KEY_URL, headers=headers, timeout=10, json={
            "usage_type": usage, "expires_in_seconds": ttl_s, "max_session_duration_seconds": 300})
        r.raise_for_status()
        keys[name] = r.json()["api_key"]
    return keys
