from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModelInfo:
    id: str
    context_length: int
    created: int | float | None
    prompt_price: float | None
    completion_price: float | None
    provider: str
    raw: dict[str, Any]
    score: float = 0.0

    @property
    def is_router(self) -> bool:
        return self.id in {"openrouter/free", "openrouter/free:free"}
