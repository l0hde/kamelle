from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .state import BACKUP_FILE, load_json, save_json

OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


def load_config() -> dict:
    return load_json(OPENCLAW_CONFIG_PATH, {})


def save_config(config: dict) -> None:
    save_json(OPENCLAW_CONFIG_PATH, config)


def backup_config(config: dict) -> None:
    save_json(BACKUP_FILE, config)


def rollback_config() -> bool:
    backup = load_json(BACKUP_FILE, None)
    if not backup:
        return False
    save_config(backup)
    return True


def ensure_structure(config: dict) -> dict:
    config = deepcopy(config)
    config.setdefault("agents", {})
    config["agents"].setdefault("defaults", {})
    config["agents"]["defaults"].setdefault("model", {})
    config["agents"]["defaults"].setdefault("models", {})
    return config


def format_for_primary(model_id: str) -> str:
    if model_id == "openrouter/free":
        return "openrouter/openrouter/free"
    if model_id.startswith("openrouter/"):
        model_id = model_id[len("openrouter/"):]
    if not model_id.endswith(":free") and model_id != "openrouter/free":
        model_id = f"{model_id}:free"
    return f"openrouter/{model_id}"


def format_for_list(model_id: str) -> str:
    if model_id == "openrouter/free":
        return "openrouter/free"
    if model_id.startswith("openrouter/"):
        model_id = model_id[len("openrouter/"):]
    if not model_id.endswith(":free"):
        model_id = f"{model_id}:free"
    return model_id


def current_primary(config: dict) -> str | None:
    return config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary")


def current_fallbacks(config: dict) -> list[str]:
    return config.get("agents", {}).get("defaults", {}).get("model", {}).get("fallbacks", [])


def extract_openrouter_base(model_id: str | None) -> str | None:
    if not model_id:
        return None
    if model_id == "openrouter/openrouter/free":
        return "openrouter/free"
    if model_id.startswith("openrouter/"):
        return model_id[len("openrouter/"):]
    return None


def apply_models(config: dict, primary_model_id: str | None, fallback_model_ids: list[str]) -> dict:
    config = ensure_structure(config)
    if primary_model_id:
        config["agents"]["defaults"]["model"]["primary"] = format_for_primary(primary_model_id)
        config["agents"]["defaults"]["models"][format_for_list(primary_model_id)] = {}

    normalized_fallbacks = []
    for model_id in fallback_model_ids:
        normalized = format_for_list(model_id)
        normalized_fallbacks.append(normalized)
        config["agents"]["defaults"]["models"][normalized] = {}

    config["agents"]["defaults"]["model"]["fallbacks"] = normalized_fallbacks
    return config
