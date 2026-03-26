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
        except Exception:
            return None
    return None


def fetch_models(api_key: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(OPENROUTER_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise KamelleError(f"OpenRouter returned HTTP {status} while fetching models.") from exc
    except requests.RequestException as exc:
        raise KamelleError(f"Could not reach OpenRouter: {exc}") from exc

    data = response.json()
    return data.get("data", [])


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
        response = requests.post(OPENROUTER_CHAT_URL, headers=headers, json=payload, timeout=timeout_seconds)
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
    except Exception:
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


def get_free_models(api_key: str, force_refresh: bool = False) -> list[ModelInfo]:
    if not force_refresh:
        cache = load_json(CACHE_FILE, {})
        if cache and cache_is_fresh(cache, hours=DEFAULT_CACHE_HOURS):
            return [normalize_model(m) for m in cache.get("models", [])]

    models = fetch_models(api_key)
    free_models = [normalize_model(m) for m in models]
    free_models = [m for m in free_models if is_free_model(m)]

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
