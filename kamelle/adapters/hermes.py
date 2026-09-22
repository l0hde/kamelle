"""Hermes backend — ``~/.hermes/config.yaml``.

Hermes keeps the primary model in ``model.default`` (bare OpenRouter id, no
provider prefix) and the fallback chain in ``fallback_providers``, a list of
``{provider, model}`` entries tried in order. ``fallback_model`` is the legacy
spelling of the same thing; Hermes merges both when reading, so writing a new
chain means dropping the legacy key — exactly what ``hermes fallback`` does.

That config file is full of hand-written comments, so Kamelle edits it line by
line through :mod:`kamelle.yamlmini` instead of re-serialising it, and verifies
the result parses back to what it intended before anything is written.
"""

from __future__ import annotations

from pathlib import Path

from .. import yamlmini
from ..state import load_text, save_text
from .base import AdapterError, AgentAdapter, AgentState, ModelPlan

HERMES_HOME = Path.home() / ".hermes"
HERMES_CONFIG_PATH = HERMES_HOME / "config.yaml"
HERMES_ENV_PATH = HERMES_HOME / ".env"

OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

PRIMARY_PATH = ("model", "default")
PROVIDER_PATH = ("model", "provider")
BASE_URL_PATH = ("model", "base_url")
CHAIN_PATH = ("fallback_providers",)
LEGACY_CHAIN_PATH = ("fallback_model",)


class HermesSnapshot:
    """The config file as text plus its parsed form, kept side by side.

    The text is what gets written back (comments and all); the parsed mapping
    is only ever read.
    """

    __slots__ = ("text", "data")

    def __init__(self, text: str, data: dict):
        self.text = text
        self.data = data

    @classmethod
    def from_text(cls, text: str) -> "HermesSnapshot":
        try:
            data = yamlmini.parse_preferred(text)
        except yamlmini.YamlMiniError as exc:
            raise AdapterError(f"hermes: cannot read config.yaml — {exc}") from exc
        return cls(text, data)


def _chain_entries(data: dict) -> list[dict]:
    """The effective chain, new key first, mirroring Hermes' own merge order."""
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path in (CHAIN_PATH, LEGACY_CHAIN_PATH):
        raw = yamlmini.get_path(data, path)
        candidates = [raw] if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            provider = str(entry.get("provider") or "").strip()
            model = str(entry.get("model") or "").strip()
            if not provider or not model or (provider, model) in seen:
                continue
            seen.add((provider, model))
            entries.append({"provider": provider, "model": model})
    return entries


class HermesAdapter(AgentAdapter):
    name = "hermes"
    display_name = "Hermes"
    summary = "model.default + fallback_providers in ~/.hermes/config.yaml"
    default_config_path = HERMES_CONFIG_PATH
    backup_suffix = ".config.yaml"
    known_options = ("config", "provider", "base_url")

    @property
    def provider(self) -> str:
        return self.options.get("provider", OPENROUTER_PROVIDER)

    @property
    def base_url(self) -> str:
        return self.options.get("base_url", OPENROUTER_BASE_URL)

    def is_installed(self) -> bool:
        return bool(self.config_path and self.config_path.exists()) or HERMES_HOME.exists()

    def load(self) -> HermesSnapshot:
        path = self.config_path
        text = load_text(path, "") if path else ""
        return HermesSnapshot.from_text(text or "")

    def describe(self, snapshot: HermesSnapshot) -> AgentState:
        primary = yamlmini.get_path(snapshot.data, PRIMARY_PATH)
        return AgentState(
            primary=str(primary) if primary else None,
            fallbacks=[entry["model"] for entry in _chain_entries(snapshot.data)],
        )

    def apply(self, snapshot: HermesSnapshot, plan: ModelPlan) -> HermesSnapshot:
        try:
            text = self._rewrite(snapshot, plan)
        except yamlmini.YamlMiniError as exc:
            # PyYAML may well have read a construct the line editor cannot
            # safely rewrite (an anchor, say). Stop rather than mangle it.
            raise AdapterError(f"hermes: cannot edit config.yaml — {exc}") from exc
        return self._verified(text, plan)

    def _rewrite(self, snapshot: HermesSnapshot, plan: ModelPlan) -> str:
        text = snapshot.text
        if plan.primary:
            text = yamlmini.set_path(text, PRIMARY_PATH, plan.primary)
            text = yamlmini.set_path(text, PROVIDER_PATH, self.provider)
            if not yamlmini.get_path(snapshot.data, BASE_URL_PATH):
                text = yamlmini.set_path(text, BASE_URL_PATH, self.base_url)

        chain = [
            {"provider": self.provider, "model": model_id}
            for model_id in self.limited_fallbacks(plan.fallbacks)
        ]
        text = yamlmini.set_path(text, CHAIN_PATH, chain)
        # One source of truth: Hermes would otherwise append the legacy entries
        # to the chain Kamelle just wrote.
        return yamlmini.delete_path(text, LEGACY_CHAIN_PATH)

    def _verified(self, text: str, plan: ModelPlan) -> HermesSnapshot:
        """Re-read the rewritten text and refuse to hand back a config that drifted."""
        snapshot = HermesSnapshot.from_text(text)
        state = self.describe(snapshot)
        expected_fallbacks = self.limited_fallbacks(plan.fallbacks)
        if plan.primary and state.primary != plan.primary:
            raise AdapterError(
                f"hermes: refusing to write — model.default came back as {state.primary!r}, "
                f"expected {plan.primary!r}"
            )
        if state.fallbacks != expected_fallbacks:
            raise AdapterError(
                "hermes: refusing to write — fallback chain came back as "
                f"{state.fallbacks} instead of {expected_fallbacks}"
            )
        return snapshot

    def save(self, snapshot: HermesSnapshot) -> None:
        if not self.config_path:
            raise AdapterError("hermes: no config path to write to")
        save_text(self.config_path, snapshot.text)

    def backup(self, snapshot: HermesSnapshot) -> Path:
        path = self.backup_path()
        save_text(path, snapshot.text)
        return path

    def restore(self) -> bool:
        backup = load_text(self.backup_path())
        if backup is None or not self.config_path:
            return False
        save_text(self.config_path, backup)
        return True

    def api_key(self) -> str | None:
        """Hermes keeps credentials in ``~/.hermes/.env`` (``hermes config env-path``)."""
        env_path = HERMES_ENV_PATH
        if self.config_path and self.config_path != HERMES_CONFIG_PATH:
            env_path = self.config_path.parent / ".env"
        text = load_text(env_path)
        if not text:
            return None
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("OPENROUTER_API_KEY="):
                continue
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
        return None
