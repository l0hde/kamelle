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


def score_model(model: ModelInfo) -> float:
    score = 0.0

    context_score = min(model.context_length / 1_000_000, 1.0)
    score += context_score * 0.45

    if model.created:
        try:
            days_old = (time.time() - float(model.created)) / 86400
            recency_score = max(0.0, 1 - (days_old / 365))
            score += recency_score * 0.25
        except Exception:
            pass

    if model.provider in TRUSTED_PROVIDERS:
        idx = TRUSTED_PROVIDERS.index(model.provider)
        trust_score = 1 - (idx / len(TRUSTED_PROVIDERS))
        score += trust_score * 0.20

    if model.is_router:
        score -= 0.15

    if model.completion_price == 0.0:
        score += 0.10

    return round(score, 6)


def rank_models(models: list[ModelInfo]) -> list[ModelInfo]:
    ranked = []
    for m in models:
        m.score = score_model(m)
        ranked.append(m)
    ranked.sort(key=lambda x: x.score, reverse=True)
    return ranked
