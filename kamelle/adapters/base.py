"""The contract every agent backend implements.

Kamelle's job is unchanged: find the best free OpenRouter models and rank them.
What changes per agent is only *where the answer is written down*. An adapter
owns that last step — reading the agent's config, putting a model plan into it,
and writing it back safely.

Model ids stay in OpenRouter's own spelling (``qwen/qwen3-coder:free``) all the
way through Kamelle. Translating to whatever an agent expects on disk happens
inside the adapter, and :meth:`AgentAdapter.describe` translates back, so the
CLI can compare what it reads to what it ranked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..state import BACKUP_DIR


class AdapterError(RuntimeError):
    """An adapter could not read or write its agent's config."""


def same_model(left: str | None, right: str | None) -> bool:
    """Compare two model ids, tolerating a missing ``:free`` suffix on either side.

    OpenRouter lists some free models with the suffix and some without, and
    agents normalise it differently on the way to disk.
    """
    if not left or not right:
        return False
    if left == right:
        return True
    return left == f"{right}:free" or f"{left}:free" == right


@dataclass(frozen=True)
class ModelPlan:
    """What Kamelle wants an agent to use: one primary, then a fallback chain."""

    primary: str | None = None
    fallbacks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentState:
    """What an agent's config currently says, in OpenRouter spelling."""

    primary: str | None = None
    fallbacks: list[str] = field(default_factory=list)


class AgentAdapter:
    """Base class for agent backends.

    Subclasses provide the four verbs Kamelle needs — :meth:`load`,
    :meth:`describe`, :meth:`apply`, :meth:`save` — plus a backup strategy.
    A *snapshot* (whatever :meth:`load` returns) is opaque to callers: it is
    passed straight back into :meth:`apply` and :meth:`save`.
    """

    name: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    summary: ClassVar[str] = ""
    #: Where this agent keeps its config, unless overridden per invocation.
    default_config_path: ClassVar[Path | None] = None
    #: Longest fallback chain the agent understands; None means "no limit".
    max_fallbacks: ClassVar[int | None] = None
    #: Suffix used for this adapter's backup file.
    backup_suffix: ClassVar[str] = ".json"
    #: Adapter-specific ``--agent-option`` keys, for the error message on a typo.
    known_options: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config_path: str | Path | None = None, options: dict[str, str] | None = None):
        self.options = dict(options or {})
        unknown = set(self.options) - set(self.known_options)
        if unknown:
            known = ", ".join(self.known_options) or "none"
            raise AdapterError(
                f"{self.name}: unknown option(s) {', '.join(sorted(unknown))}. Known options: {known}"
            )
        override = config_path or self.options.get("config")
        self._config_path = Path(override).expanduser() if override else self.default_config_path

    # -- identity ----------------------------------------------------------

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    @property
    def writes_to_stdout(self) -> bool:
        """True when :meth:`save` prints instead of writing a file.

        Kamelle then moves its own chatter to stderr so a redirect stays clean.
        """
        return False

    def is_installed(self) -> bool:
        """True when this agent looks present on the machine."""
        return bool(self._config_path and self._config_path.exists())

    def location(self) -> str:
        return str(self._config_path) if self._config_path else "—"

    def api_key(self) -> str | None:
        """An OPENROUTER_API_KEY this agent already has on file, if any.

        Lets ``kamelle --agent hermes`` work for someone whose key only ever
        lived in that agent's own credential file. The environment still wins.
        """
        return None

    # -- the four verbs ----------------------------------------------------

    def load(self) -> Any:
        """Return an opaque snapshot of the agent's current config."""
        raise NotImplementedError

    def describe(self, snapshot: Any) -> AgentState:
        """Report what ``snapshot`` selects, in OpenRouter spelling."""
        raise NotImplementedError

    def apply(self, snapshot: Any, plan: ModelPlan) -> Any:
        """Return a new snapshot with ``plan`` written into it. Never mutates."""
        raise NotImplementedError

    def save(self, snapshot: Any) -> None:
        """Persist a snapshot produced by :meth:`apply`."""
        raise NotImplementedError

    # -- safety net --------------------------------------------------------

    def backup_path(self) -> Path:
        return BACKUP_DIR / f"{self.name}{self.backup_suffix}"

    def backup(self, snapshot: Any) -> Path | None:
        """Store ``snapshot`` so :meth:`restore` can put it back."""
        raise NotImplementedError

    def restore(self) -> bool:
        """Write the last backup back into the agent's config."""
        raise NotImplementedError

    # -- helpers for subclasses -------------------------------------------

    def native(self, snapshot: Any) -> dict:
        """The selection exactly as this agent spells it on disk, for JSON output."""
        state = self.describe(snapshot)
        return {"primary": state.primary, "fallbacks": state.fallbacks}

    def status_tag(self, state: AgentState, model_id: str) -> str:
        """``PRIMARY`` / ``FALLBACK`` / ``""`` for one model in a listing."""
        if same_model(state.primary, model_id):
            return "PRIMARY"
        if any(same_model(fallback, model_id) for fallback in state.fallbacks):
            return "FALLBACK"
        return ""

    def limited_fallbacks(self, fallbacks: list[str]) -> list[str]:
        """Trim a chain to what this agent can actually hold."""
        if self.max_fallbacks is None:
            return list(fallbacks)
        return list(fallbacks)[: self.max_fallbacks]

    def note_for(self, plan: ModelPlan) -> str | None:
        """A one-line caveat to show the user, or None when the plan fits."""
        if self.max_fallbacks is None or len(plan.fallbacks) <= self.max_fallbacks:
            return None
        return (
            f"{self.display_name} keeps {self.max_fallbacks} of {len(plan.fallbacks)} fallbacks "
            f"— its config format holds no more."
        )
