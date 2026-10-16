"""Direct LLM calls to Gemini and OpenRouter through their OpenAI-compatible endpoints.

Providers are tried in order. A provider that errors (rate limit, 503 "high demand",
timeout) is skipped for COOLDOWN_S seconds, so a congested free tier does not slow
down every answer of a viva (a viva lasts a few minutes).
"""
import json
import os
import re
import time

from openai import OpenAI

COOLDOWN_S = 300

_down_until: dict[str, float] = {}


class LLMError(RuntimeError):
    pass


def providers() -> list[dict]:
    openrouter = "https://openrouter.ai/api/v1"
    return [
        {"name": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
         "key": os.getenv("GEMINI_API_KEY"), "model": os.getenv("GEMINI_MODEL", "gemini-flash-latest"), "timeout": 25},
        {"name": "openrouter", "base_url": openrouter, "key": os.getenv("OPENROUTER_API_KEY"),
         "model": os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free"), "timeout": 60},
        {"name": "openrouter-backup", "base_url": openrouter, "key": os.getenv("OPENROUTER_API_KEY"),
         "model": os.getenv("OPENROUTER_BACKUP_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"), "timeout": 60},
        {"name": "openrouter-auto", "base_url": openrouter, "key": os.getenv("OPENROUTER_API_KEY"),
         "model": "openrouter/free", "timeout": 60},
    ]


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply that may contain think-tags or ``` fences."""
    text = re.sub(r"<(think|thought)>.*?</\1>", "", text or "", flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("no JSON object in model output")
    return json.loads(match.group(0))


def chat_json(system: str, user: str, temperature: float = 0.2) -> tuple[dict, str]:
    """Return (parsed JSON reply, provider name) from the first provider that answers."""
    errors = []
    for p in providers():
        if not p["key"] or _down_until.get(p["name"], 0) > time.time():
            continue
        try:
            client = OpenAI(api_key=p["key"], base_url=p["base_url"], timeout=p["timeout"], max_retries=0)
            resp = client.chat.completions.create(
                model=p["model"], temperature=temperature,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return extract_json(resp.choices[0].message.content), p["name"]
        except ValueError as e:  # bad JSON: model quality issue, try the next one but don't cool down
            errors.append(f"{p['name']}: {e}")
        except Exception as e:  # provider unavailable: cool it down
            _down_until[p["name"]] = time.time() + COOLDOWN_S
            errors.append(f"{p['name']}: {str(e)[:150]}")
    raise LLMError("all LLM providers failed -> " + " | ".join(errors))
