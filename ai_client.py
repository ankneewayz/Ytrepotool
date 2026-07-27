"""
AI client for Emina.

Provider order:
  1. Google Gemini (free tier) — primary, since it's free to run.
  2. Anthropic Claude — fallback if GEMINI call fails/quota hit and a key is set.
  3. OpenAI-compatible endpoint — final fallback if configured.

Each provider is a thin async function returning plain text. If a provider
raises, the caller falls through to the next one automatically.
"""
import httpx

from config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    OPENAI_API_KEY, OPENAI_BASE_URL,
    logger,
)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def _call_gemini(system_prompt: str, history: list[dict], user_message: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")

    contents = []
    for turn in history:
        role = "model" if turn["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": turn["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 500},
    }

    url = GEMINI_URL.format(model=GEMINI_MODEL)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned empty text")
    return text


async def _call_anthropic(system_prompt: str, history: list[dict], user_message: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    messages = [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": user_message})

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 500,
                "system": system_prompt,
                "messages": messages,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    blocks = data.get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Anthropic returned empty text")
    return text


async def _call_openai_compatible(system_prompt: str, history: list[dict], user_message: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": user_message})

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": "gpt-4o-mini", "messages": messages, "max_tokens": 500},
        )
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("OpenAI-compatible provider returned empty text")
    return text


PROVIDERS = [
    ("gemini", _call_gemini),
    ("anthropic", _call_anthropic),
    ("openai", _call_openai_compatible),
]


EXTRACT_PROMPT = """Read the message below. Pull out any durable personal facts the speaker stated \
about themselves — things worth remembering long-term (preferences, relationships, important dates, \
goals, habits, dislikes, ongoing projects). Ignore small talk, questions, and anything not about the \
speaker. Never extract passwords or account credentials.

Respond with ONLY a JSON array of short fact strings, nothing else. If there's nothing worth \
remembering, respond with []. Keep each fact to one clause, written in third person about "the user".

Message: {message}"""


async def extract_facts(message: str) -> list[str]:
    """Best-effort long-term memory extraction. Returns [] on any failure — this
    should never block or break the actual chat reply."""
    import json

    prompt = EXTRACT_PROMPT.format(message=message)
    try:
        raw = await _call_gemini("You extract structured facts. Output JSON only.", [], prompt)
    except Exception as e:  # noqa: BLE001
        logger.warning("Fact extraction failed: %s", e)
        return []

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
    try:
        facts = json.loads(raw)
        if isinstance(facts, list):
            return [str(f).strip() for f in facts if str(f).strip()]
    except Exception:  # noqa: BLE001
        pass
    return []


async def generate_reply(system_prompt: str, history: list[dict], user_message: str) -> str:
    """Try each configured provider in order, falling back on failure."""
    last_error = None
    for name, fn in PROVIDERS:
        try:
            return await fn(system_prompt, history, user_message)
        except Exception as e:  # noqa: BLE001 - deliberately broad, we fall through
            last_error = e
            logger.warning("AI provider '%s' failed: %s", name, e)
            continue
    logger.error("All AI providers failed. Last error: %s", last_error)
    return "Ugh, i am sleeping right now"
