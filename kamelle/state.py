from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

KAMELLE_DIR = Path.home() / ".openclaw"
CACHE_FILE = KAMELLE_DIR / ".kamelle-cache.json"
STATE_FILE = KAMELLE_DIR / ".kamelle-state.json"
BACKUP_FILE = KAMELLE_DIR / ".kamelle-backup.json"
HISTORY_FILE = KAMELLE_DIR / ".kamelle-history.json"
DEFAULT_CACHE_HOURS = 1
LATENCY_CACHE_HOURS = 6

BENCH_CACHE_HOURS = 24
BENCH_FILE = KAMELLE_DIR / ".kamelle-bench.json"

_state_lock = threading.Lock()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        import sys
        print(f"⚠️ Warning: {path} contains invalid JSON, treating as default.", file=sys.stderr)
        return default
    except OSError as exc:
        import sys
        print(f"⚠️ Warning: Could not read {path}: {exc}", file=sys.stderr)
        return default


def save_json(path: Path, data: Any) -> None:
    ensure_parent(path)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def cache_is_fresh(cache: dict, hours: int = DEFAULT_CACHE_HOURS) -> bool:
    cached_at = cache.get("cached_at")
    if not cached_at:
        return False
    try:
        ts = datetime.fromisoformat(cached_at)
    except (ValueError, TypeError):
        return False
    return datetime.now() - ts < timedelta(hours=hours)


def load_state() -> dict:
    return load_json(STATE_FILE, {})


def save_state(data: dict) -> None:
    with _state_lock:
        save_json(STATE_FILE, data)


def latency_entry_is_fresh(entry: dict | None, hours: int = LATENCY_CACHE_HOURS) -> bool:
    if not entry:
        return False
    measured_at = entry.get("measured_at")
    if not measured_at:
        return False
    try:
        ts = datetime.fromisoformat(measured_at)
    except (ValueError, TypeError):
        return False
    return datetime.now() - ts < timedelta(hours=hours)


def get_latency_entry(model_id: str) -> dict | None:
    state = load_state()
    return state.get("latency", {}).get(model_id)


def save_latency_entry(model_id: str, status: str, latency_ms: int | None) -> dict:
    with _state_lock:
        state = load_json(STATE_FILE, {})
        state.setdefault("latency", {})
        entry = {
            "status": status,
            "latency_ms": latency_ms,
            "measured_at": datetime.now().isoformat(),
        }
        state["latency"][model_id] = entry
        save_json(STATE_FILE, state)
        return entry


def append_history_entry(from_model: str | None, to_model: str | None, score: float | None) -> None:
    entries = load_json(HISTORY_FILE, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "ts": datetime.now().isoformat(),
        "from": from_model,
        "to": to_model,
        "score": round(score, 3) if score is not None else None,
    })
    save_json(HISTORY_FILE, entries)


def bench_entry_is_fresh(entry: dict | None, hours: int = BENCH_CACHE_HOURS) -> bool:
    if not entry:
        return False
    measured_at = entry.get("measured_at")
    if not measured_at:
        return False
    try:
        ts = datetime.fromisoformat(measured_at)
    except (ValueError, TypeError):
        return False
    return datetime.now() - ts < timedelta(hours=hours)


def get_bench_entry(model_id: str) -> dict | None:
    data = load_json(BENCH_FILE, {})
    return data.get(model_id)


def save_bench_entry(model_id: str, result: dict) -> dict:
    """Persist one bench result under model_id, add measured_at timestamp."""
    entry = dict(result)
    entry["measured_at"] = datetime.now().isoformat()
    with _state_lock:
        data = load_json(BENCH_FILE, {})
        data[model_id] = entry
        save_json(BENCH_FILE, data)
    return entry


def load_all_bench_entries() -> dict:
    """Return all saved bench entries keyed by model_id."""
    return load_json(BENCH_FILE, {})
