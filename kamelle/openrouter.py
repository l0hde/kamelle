from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

from .models import ModelInfo
from .state import CACHE_FILE, DEFAULT_CACHE_HOURS, cache_is_fresh, load_json, save_json

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


class KamelleError(RuntimeError):
    pass


def get_api_key() -> str | None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        return api_key

    if OPENCLAW_CONFIG_PATH.exists():
        try:
            config = json.loads(OPENCLAW_CONFIG_PATH.read_text())
            return config.get("env", {}).get("OPENROUTER_API_KEY")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _retry_request(method, url, *, max_retries=3, **kwargs):
    """Execute an HTTP request with exponential backoff and 429 handling."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = method(url, **kwargs)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(retry_after, 30))
                last_exc = requests.HTTPError(f"429 Too Many Requests", response=response)
                continue
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    if last_exc:
        raise last_exc
    raise requests.RequestException("Max retries exceeded")


def fetch_models(api_key: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = _retry_request(requests.get, OPENROUTER_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise KamelleError(f"OpenRouter returned HTTP {status} while fetching models.") from exc
    except requests.RequestException as exc:
        raise KamelleError(f"Could not reach OpenRouter: {exc}") from exc

    data = response.json()
    return data.get("data", [])


BENCH_PROMPT = (
    "Answer these two tasks in order:\n"
    "1. What is 17 times 4? Give only the number.\n"
    "2. Name the capital of Germany in one word."
)
BENCH_EXPECTED_Q1 = "68"
BENCH_EXPECTED_Q2 = "berlin"


def bench_model(
    api_key: str,
    model_id: str,
    timeout_seconds: int = 20,
) -> dict:
    """Send a standardised quality prompt and return a bench result dict.

    Returns a dict with keys:
        status: "ok" | "error" | "timeout" | "rate_limit" | "unavailable" | "empty"
        latency_ms: int | None
        response: str | None  (raw model text, truncated to 400 chars)
        passes_q1: bool  (response contains '68')
        passes_q2: bool  (response lowercased contains 'berlin')
        score: int  (0-2)
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/l0hde/kamelle",
        "X-Title": "Kamelle Bench",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": BENCH_PROMPT}],
        "max_tokens": 60,
        "stream": False,
    }
    started = time.perf_counter()
    try:
        resp = _retry_request(
            requests.post, OPENROUTER_CHAT_URL,
            max_retries=1,
            headers=headers, json=payload, timeout=timeout_seconds,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code == 429:
            return {"status": "rate_limit", "latency_ms": None, "response": None,
                    "passes_q1": False, "passes_q2": False, "score": 0}
        if resp.status_code == 503:
            return {"status": "unavailable", "latency_ms": None, "response": None,
                    "passes_q1": False, "passes_q2": False, "score": 0}
        if resp.status_code != 200:
            return {"status": f"http_{resp.status_code}", "latency_ms": elapsed_ms, "response": None,
                    "passes_q1": False, "passes_q2": False, "score": 0}

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError):
            return {"status": "empty", "latency_ms": elapsed_ms, "response": None,
                    "passes_q1": False, "passes_q2": False, "score": 0}

        if not text.strip():
            return {"status": "empty", "latency_ms": elapsed_ms, "response": "",
                    "passes_q1": False, "passes_q2": False, "score": 0}

        passes_q1 = BENCH_EXPECTED_Q1 in text
        passes_q2 = BENCH_EXPECTED_Q2 in text.lower()
        return {
            "status": "ok",
            "latency_ms": elapsed_ms,
            "response": text[:400],
            "passes_q1": passes_q1,
            "passes_q2": passes_q2,
            "score": int(passes_q1) + int(passes_q2),
        }
    except requests.Timeout:
        return {"status": "timeout", "latency_ms": None, "response": None,
                "passes_q1": False, "passes_q2": False, "score": 0}
    except requests.RequestException:
        return {"status": "error", "latency_ms": None, "response": None,
                "passes_q1": False, "passes_q2": False, "score": 0}


def probe_model_latency(api_key: str, model_id: str, timeout_seconds: int = 20) -> tuple[str, int | None]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/l0hde/kamelle",
        "X-Title": "Kamelle Latency Probe",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    started = time.perf_counter()
    try:
        response = _retry_request(
            requests.post, OPENROUTER_CHAT_URL,
            max_retries=2,
            headers=headers, json=payload, timeout=timeout_seconds,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 200:
            return "ok", elapsed_ms
        if response.status_code == 429:
            return "rate_limit", None
        if response.status_code == 503:
            return "unavailable", None
        return f"http_{response.status_code}", None
    except requests.Timeout:
        return "timeout", None
    except requests.RequestException:
        return "error", None


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def normalize_model(model: dict) -> ModelInfo:
    pricing = model.get("pricing", {}) or {}
    model_id = model.get("id", "")
    provider = model_id.split("/")[0] if "/" in model_id else ""
    return ModelInfo(
        id=model_id,
        context_length=int(model.get("context_length") or 0),
        created=model.get("created"),
        prompt_price=_to_float(pricing.get("prompt")),
        completion_price=_to_float(pricing.get("completion")),
        provider=provider,
        raw=model,
    )


def is_free_model(model: ModelInfo) -> bool:
    if ":free" in model.id:
        return True
    if model.prompt_price == 0.0 and (model.completion_price in {0.0, None}):
        return True
    return False


_NON_CHAT_MODALITIES = {"audio->audio", "text->audio", "audio->text",
                        "text->image", "image->text", "image->image"}


def is_chat_model(model: ModelInfo) -> bool:
    """Return True if the model supports text-in / text-out chat completions."""
    arch = model.raw.get("architecture") or {}
    modality: str | None = arch.get("modality") or arch.get("input_modalities") or None
    if modality:
        # OpenRouter uses e.g. "text->text", "text+image->text", "text->audio"
        if modality in _NON_CHAT_MODALITIES:
            return False
        if "text" not in modality:
            return False
    # Also filter by known non-chat ID patterns
    _id = model.id.lower()
    if any(p in _id for p in ("/lyria", "/dall-e", "/stable-diffusion",
                               "/midjourney", "/suno", "/audio")):
        return False
    return True


def get_free_models(api_key: str, force_refresh: bool = False) -> list[ModelInfo]:
    if not force_refresh:
        cache = load_json(CACHE_FILE, {})
        if cache and cache_is_fresh(cache, hours=DEFAULT_CACHE_HOURS):
            return [normalize_model(m) for m in cache.get("models", [])]

    models = fetch_models(api_key)
    free_models = [normalize_model(m) for m in models]
    free_models = [m for m in free_models if is_free_model(m) and is_chat_model(m)]

    save_json(
        CACHE_FILE,
        {
            "cached_at": datetime.now().isoformat(),
            "cache_hours": DEFAULT_CACHE_HOURS,
            "models": [m.raw for m in free_models],
            "count": len(free_models),
        },
    )
    return free_models
