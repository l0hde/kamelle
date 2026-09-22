"""Generic backend — write the selection as plain JSON or YAML.

For every agent Kamelle has no dedicated adapter for. It does not pretend to
know the target's config schema; it just states the result in a machine-
readable document you can feed into your own tooling:

    kamelle auto --agent generic > models.json
    kamelle auto --agent generic -o out=~/.myagent/models.yaml

With no ``out`` option the document goes to stdout, and Kamelle's own output
moves to stderr so the redirect above stays clean.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from .. import yamlmini
from ..state import load_text, save_text
from .base import AdapterError, AgentAdapter, AgentState, ModelPlan

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
API_KEY_ENV = "OPENROUTER_API_KEY"
FORMATS = ("json", "yaml")


def _render(document: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    return yamlmini.dump(document)


def _load_document(text: str, fmt: str) -> dict:
    if fmt == "json":
        loaded = json.loads(text)
    else:
        loaded = yamlmini.parse_preferred(text)
    if not isinstance(loaded, dict):
        raise AdapterError("generic: existing document is not a mapping")
    return loaded


class GenericAdapter(AgentAdapter):
    name = "generic"
    display_name = "Generic"
    summary = "plain JSON/YAML document — stdout, or a file via -o out=PATH"
    default_config_path = None
    known_options = ("config", "out", "format")

    def __init__(self, config_path=None, options=None):
        super().__init__(config_path=config_path, options=options)
        out = self.options.get("out")
        if out and not self._config_path:
            self._config_path = Path(out).expanduser()
        fmt = self.options.get("format")
        if fmt and fmt not in FORMATS:
            raise AdapterError(f"generic: format must be one of {', '.join(FORMATS)}, got {fmt!r}")
        self.format = fmt or self._format_from_path()

    def _format_from_path(self) -> str:
        suffix = self._config_path.suffix.lower() if self._config_path else ""
        return "yaml" if suffix in (".yaml", ".yml") else "json"

    # -- identity ----------------------------------------------------------

    def is_installed(self) -> bool:
        return True

    def location(self) -> str:
        return str(self._config_path) if self._config_path else "stdout"

    @property
    def writes_to_stdout(self) -> bool:
        return self._config_path is None

    # -- the four verbs ----------------------------------------------------

    def load(self) -> dict:
        if not self._config_path:
            return {}
        text = load_text(self._config_path)
        if not text or not text.strip():
            return {}
        try:
            return _load_document(text, self.format)
        except (json.JSONDecodeError, yamlmini.YamlMiniError) as exc:
            raise AdapterError(f"generic: cannot read {self._config_path} — {exc}") from exc

    def describe(self, snapshot: dict) -> AgentState:
        primary = snapshot.get("primary")
        fallbacks = snapshot.get("fallbacks") or []
        return AgentState(
            primary=str(primary) if primary else None,
            fallbacks=[str(item) for item in fallbacks if item],
        )

    def apply(self, snapshot: dict, plan: ModelPlan) -> dict:
        from .. import __version__

        primary = plan.primary or snapshot.get("primary")
        return {
            "kamelle": {"version": __version__, "generated_at": datetime.now().isoformat(timespec="seconds")},
            "provider": {"name": "openrouter", "base_url": OPENROUTER_BASE_URL, "api_key_env": API_KEY_ENV},
            "primary": primary,
            "fallbacks": self.limited_fallbacks(plan.fallbacks),
        }

    def save(self, snapshot: dict) -> None:
        rendered = _render(snapshot, self.format)
        if self._config_path:
            save_text(self._config_path, rendered)
        else:
            sys.stdout.write(rendered)

    # -- safety net --------------------------------------------------------

    def backup_path(self) -> Path:
        from ..state import BACKUP_DIR

        return BACKUP_DIR / f"generic.{self.format}"

    def backup(self, snapshot: dict) -> Path | None:
        if not self._config_path or not snapshot:
            return None  # nothing was there to lose
        path = self.backup_path()
        save_text(path, _render(snapshot, self.format))
        return path

    def restore(self) -> bool:
        if not self._config_path:
            return False
        backup = load_text(self.backup_path())
        if backup is None:
            return False
        save_text(self._config_path, backup)
        return True
