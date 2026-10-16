"""Direct LLM calls to Gemini, OpenRouter and Ollama Cloud through their OpenAI-compatible endpoints.

Providers are tried in order. A provider that errors (rate limit, 503 "high demand",
timeout) is skipped for COOLDOWN_S seconds, so a congested free tier does not slow
down every answer of a viva (a viva lasts a few minutes).
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from openai import OpenAI

COOLDOWN_S = 300

_down_until: dict[str, float] = {}

# Response cache: an identical request (same report for the planner, same answer for the judge) is answered from
# disk instead of spending free-tier quota again. Shown in the agent trace as "cache (provider)". LLM_CACHE=0 disables it.
CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"


def _cache_path(system: str, user: str, temperature: float) -> Path:
    key = hashlib.sha256(json.dumps([system, user, temperature]).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.json"


class LLMError(RuntimeError):
    pass


def providers() -> list[dict]:
    gemini = "https://generativelanguage.googleapis.com/v1beta/openai/"
    openrouter = "https://openrouter.ai/api/v1"
    ollama = "https://ollama.com/v1"
    return [
        {"name": "gemini", "base_url": gemini, "key": os.getenv("GEMINI_API_KEY"),
         "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"), "timeout": 25},
        {"name": "gemini-backup", "base_url": gemini, "key": os.getenv("GEMINI_API_KEY"),
         "model": os.getenv("GEMINI_BACKUP_MODEL", "gemini-flash-lite-latest"), "timeout": 30},
        {"name": "openrouter", "base_url": openrouter, "key": os.getenv("OPENROUTER_API_KEY"),
         "model": os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free"), "timeout": 60},
        {"name": "openrouter-backup", "base_url": openrouter, "key": os.getenv("OPENROUTER_API_KEY"),
         "model": os.getenv("OPENROUTER_BACKUP_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"), "timeout": 60},
        {"name": "openrouter-auto", "base_url": openrouter, "key": os.getenv("OPENROUTER_API_KEY"),
         "model": "openrouter/free", "timeout": 60},
        {"name": "ollama", "base_url": ollama, "key": os.getenv("OLLAMA_API_KEY"),
         "model": os.getenv("OLLAMA_MODEL", "gemma4:31b"), "timeout": 60},
    ]


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply that may contain think-tags or ``` fences."""
    text = re.sub(r"<(think|thought)>.*?</\1>", "", text or "", flags=re.S)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("no JSON object in model output")
    return json.loads(match.group(0))


def _ask(p: dict, system: str, user: str, temperature: float, errors: list) -> tuple[dict, str] | None:
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
    return None


def chat_json(system: str, user: str, temperature: float = 0.2) -> tuple[dict, str]:
    """Return (parsed JSON reply, provider name) from the cache or the first provider that answers."""
    use_cache = os.getenv("LLM_CACHE", "1") != "0"
    path = _cache_path(system, user, temperature)
    if use_cache and path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            return saved["data"], f"cache ({saved['provider']})"
        except (OSError, ValueError, KeyError):
            pass   # unreadable cache entry: ask a model instead
    result = _ask_providers(system, user, temperature)
    if use_cache:
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            path.write_text(json.dumps({"data": result[0], "provider": result[1]}), encoding="utf-8")
        except OSError:
            pass
    return result


def _ask_providers(system: str, user: str, temperature: float) -> tuple[dict, str]:
    errors, cooling = [], []
    for p in providers():
        if not p["key"]:
            continue
        if _down_until.get(p["name"], 0) > time.time():
            cooling.append(p)
            continue
        if result := _ask(p, system, user, temperature, errors):
            return result
    for p in cooling:  # everything else failed: give the cooling-down providers one more chance
        if result := _ask(p, system, user, temperature, errors):
            _down_until.pop(p["name"], None)
            return result
    raise LLMError("all LLM providers failed -> " + " | ".join(errors))
