"""Registry of agent backends Kamelle can write a model selection into.

Adding an agent means writing one :class:`~kamelle.adapters.base.AgentAdapter`
subclass and listing it in :data:`ADAPTERS`. Nothing else in Kamelle — the
OpenRouter client, the ranking, the bench — needs to know it exists.
"""

from __future__ import annotations

import os

from .base import AdapterError, AgentAdapter, AgentState, ModelPlan, same_model
from .hermes import HermesAdapter
from .openclaw import OpenClawAdapter

__all__ = [
    "ADAPTERS",
    "DEFAULT_ADAPTER",
    "AdapterError",
    "AgentAdapter",
    "AgentState",
    "ModelPlan",
    "adapter_names",
    "detect_installed",
    "get_adapter",
    "parse_options",
    "resolve_name",
    "same_model",
]

ADAPTERS: dict[str, type[AgentAdapter]] = {
    cls.name: cls for cls in (OpenClawAdapter, HermesAdapter)
}

#: OpenClaw stays the default so existing scripts and cron jobs keep working.
DEFAULT_ADAPTER = "openclaw"

#: Environment override, for people who mainly drive a different agent.
AGENT_ENV_VAR = "KAMELLE_AGENT"


def adapter_names() -> list[str]:
    return list(ADAPTERS)


def resolve_name(name: str | None = None) -> str:
    """Pick an adapter name: explicit argument, then $KAMELLE_AGENT, then the default."""
    chosen = (name or os.environ.get(AGENT_ENV_VAR) or DEFAULT_ADAPTER).strip().lower()
    if chosen not in ADAPTERS:
        raise AdapterError(
            f"Unknown agent {chosen!r}. Available: {', '.join(adapter_names())}"
        )
    return chosen


def parse_options(pairs: list[str] | None) -> dict[str, str]:
    """Turn ``['out=/tmp/x.json', 'format=yaml']`` into a dict."""
    options: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise AdapterError(f"Agent option {pair!r} must look like key=value")
        key, value = pair.split("=", 1)
        options[key.strip()] = value.strip()
    return options


def get_adapter(
    name: str | None = None,
    config_path: str | None = None,
    options: list[str] | dict[str, str] | None = None,
) -> AgentAdapter:
    """Build the adapter for ``name`` (see :func:`resolve_name`)."""
    parsed = options if isinstance(options, dict) else parse_options(options)
    return ADAPTERS[resolve_name(name)](config_path=config_path, options=parsed)


def detect_installed() -> list[AgentAdapter]:
    """Instantiate every adapter and return the ones whose agent is present."""
    found = []
    for cls in ADAPTERS.values():
        try:
            adapter = cls()
        except AdapterError:
            continue
        if adapter.is_installed():
            found.append(adapter)
    return found
