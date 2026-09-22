"""OpenClaw backend — Kamelle's original target, now one adapter among several.

OpenClaw keeps its model choice in ``~/.openclaw/openclaw.json`` under
``agents.defaults.model``, and it spells model ids differently in the two
places it uses them:

* ``model.primary`` wants a provider prefix — ``openrouter/qwen/qwen3:free``
* ``model.fallbacks`` and the ``models`` registry want the bare OpenRouter id

:func:`format_for_primary` and :func:`format_for_list` encode exactly that, and
they are kept here (rather than in a generic module) because they are OpenClaw
trivia, not something other agents share.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from ..state import BACKUP_FILE, LEGACY_DIR, load_json, save_json
from .base import AgentAdapter, AgentState, ModelPlan

OPENCLAW_CONFIG_PATH = LEGACY_DIR / "openclaw.json"


# ---------------------------------------------------------------------------
# Config shape
# ---------------------------------------------------------------------------

def load_config(path: Path | None = None) -> dict:
    return load_json(path or OPENCLAW_CONFIG_PATH, {})


def save_config(config: dict, path: Path | None = None) -> None:
    save_json(path or OPENCLAW_CONFIG_PATH, config)


def backup_config(config: dict) -> None:
    save_json(BACKUP_FILE, config)


def rollback_config(path: Path | None = None) -> bool:
    backup = load_json(BACKUP_FILE, None)
    if not backup:
        return False
    save_config(backup, path)
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


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OpenClawAdapter(AgentAdapter):
    name = "openclaw"
    display_name = "OpenClaw"
    summary = "agents.defaults.model in ~/.openclaw/openclaw.json"
    default_config_path = OPENCLAW_CONFIG_PATH
    known_options = ("config",)

    def load(self) -> dict:
        return load_config(self.config_path)

    def describe(self, snapshot: dict) -> AgentState:
        return AgentState(
            primary=extract_openrouter_base(current_primary(snapshot)),
            fallbacks=list(current_fallbacks(snapshot)),
        )

    def apply(self, snapshot: dict, plan: ModelPlan) -> dict:
        return apply_models(snapshot, plan.primary, self.limited_fallbacks(plan.fallbacks))

    def native(self, snapshot: dict) -> dict:
        return {
            "primary": current_primary(snapshot),
            "fallbacks": list(current_fallbacks(snapshot)),
        }

    def save(self, snapshot: dict) -> None:
        save_config(snapshot, self.config_path)

    def backup(self, snapshot: dict) -> Path:
        save_json(BACKUP_FILE, snapshot)
        return BACKUP_FILE

    def backup_path(self) -> Path:
        # Pinned to the shared JSON backup so `kamelle rollback` still finds
        # snapshots taken before the adapter layer existed.
        return BACKUP_FILE

    def restore(self) -> bool:
        return rollback_config(self.config_path)

    def api_key(self) -> str | None:
        """OpenClaw stores credentials under ``env`` in its config."""
        path = self.config_path
        if not path or not path.exists():
            return None
        try:
            config = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return config.get("env", {}).get("OPENROUTER_API_KEY")
