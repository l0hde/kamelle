from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

KAMELLE_DIR = Path.home() / ".openclaw"
CACHE_FILE = KAMELLE_DIR / ".kamelle-cache.json"
STATE_FILE = KAMELLE_DIR / ".kamelle-state.json"
BACKUP_FILE = KAMELLE_DIR / ".kamelle-backup.json"
DEFAULT_CACHE_HOURS = 1
LATENCY_CACHE_HOURS = 6


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def cache_is_fresh(cache: dict, hours: int = DEFAULT_CACHE_HOURS) -> bool:
    cached_at = cache.get("cached_at")
    if not cached_at:
        return False
    try:
        ts = datetime.fromisoformat(cached_at)
    except Exception:
        return False
    return datetime.now() - ts < timedelta(hours=hours)


def load_state() -> dict:
    return load_json(STATE_FILE, {})


def save_state(data: dict) -> None:
    save_json(STATE_FILE, data)


def latency_entry_is_fresh(entry: dict | None, hours: int = LATENCY_CACHE_HOURS) -> bool:
    if not entry:
        return False
    measured_at = entry.get("measured_at")
    if not measured_at:
        return False
    try:
        ts = datetime.fromisoformat(measured_at)
    except Exception:
        return False
    return datetime.now() - ts < timedelta(hours=hours)


def get_latency_entry(model_id: str) -> dict | None:
    state = load_state()
    return state.get("latency", {}).get(model_id)


def save_latency_entry(model_id: str, status: str, latency_ms: int | None) -> dict:
    state = load_state()
    state.setdefault("latency", {})
    entry = {
        "status": status,
        "latency_ms": latency_ms,
        "measured_at": datetime.now().isoformat(),
    }
    state["latency"][model_id] = entry
    save_state(state)
    return entry
