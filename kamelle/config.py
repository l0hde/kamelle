"""Backwards-compatible view of the OpenClaw config helpers.

These lived here when OpenClaw was the only agent Kamelle served. They now
belong to :mod:`kamelle.adapters.openclaw`, and this module re-exports them so
older imports (and anything scripted against them) keep working unchanged.

New code should go through :func:`kamelle.adapters.get_adapter` instead.
"""

from __future__ import annotations

from .adapters.openclaw import (
    OPENCLAW_CONFIG_PATH,
    apply_models,
    backup_config,
    current_fallbacks,
    current_primary,
    ensure_structure,
    extract_openrouter_base,
    format_for_list,
    format_for_primary,
    load_config,
    rollback_config,
    save_config,
)

__all__ = [
    "OPENCLAW_CONFIG_PATH",
    "apply_models",
    "backup_config",
    "current_fallbacks",
    "current_primary",
    "ensure_structure",
    "extract_openrouter_base",
    "format_for_list",
    "format_for_primary",
    "load_config",
    "rollback_config",
    "save_config",
]
