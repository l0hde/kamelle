from __future__ import annotations

import time

from .models import ModelInfo

TRUSTED_PROVIDERS = [
    "google",
    "meta-llama",
    "mistralai",
    "deepseek",
    "nvidia",
    "qwen",
    "microsoft",
    "allenai",
    "arcee-ai",
    "openrouter",
]


def score_model(model: ModelInfo, latency_entry: dict | None = None, bench_entry: dict | None = None) -> float:
    score = 0.0

    # Context window (35%)
    context_score = min(model.context_length / 1_000_000, 1.0)
    score += context_score * 0.35

    # Recency (25%)
    if model.created:
        try:
            days_old = (time.time() - float(model.created)) / 86400
            recency_score = max(0.0, 1 - (days_old / 365))
            score += recency_score * 0.25
        except (ValueError, TypeError):
            pass

    # Provider trust (15%)
    if model.provider in TRUSTED_PROVIDERS:
        idx = TRUSTED_PROVIDERS.index(model.provider)
        trust_score = 1 - (idx / len(TRUSTED_PROVIDERS))
        score += trust_score * 0.15

    # Latency (10% max — user requirement)
    if latency_entry and latency_entry.get("status") == "ok":
        latency_ms = latency_entry.get("latency_ms")
        if isinstance(latency_ms, (int, float)) and latency_ms > 0:
            latency_score = max(0.0, 1 - (latency_ms / 10000))
            score += latency_score * 0.10
    elif latency_entry and latency_entry.get("status") in ("timeout", "unavailable"):
        score -= 0.05  # small penalty for unreachable models

    # Free completion bonus (5%)
    if model.completion_price == 0.0:
        score += 0.05

    # Bench quality bonus (10% max)
    if bench_entry and bench_entry.get("status") == "ok":
        bench_score_raw = bench_entry.get("score", 0)  # 0-2
        score += (bench_score_raw / 2.0) * 0.10
    elif bench_entry and bench_entry.get("status") in ("error", "timeout", "unavailable", "rate_limit"):
        score -= 0.05  # penalty for broken models

    # Router penalty
    if model.is_router:
        score -= 0.15

    return round(score, 6)


def rank_models(
    models: list[ModelInfo],
    latency_map: dict | None = None,
    bench_map: dict | None = None,
) -> list[ModelInfo]:
    ranked = []
    for m in models:
        latency_entry = latency_map.get(m.id) if latency_map else None
        bench_entry = bench_map.get(m.id) if bench_map else None
        m.score = score_model(m, latency_entry, bench_entry)
        ranked.append(m)
    ranked.sort(key=lambda x: x.score, reverse=True)
    return ranked
