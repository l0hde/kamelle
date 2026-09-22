"""Tests for the CLI's agent wiring: --agent routing, plans, and JSON output.

Every invocation is pinned to a config file under tmp_path, so the suite never
reads or writes a real agent's configuration.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kamelle import cli  # noqa: E402
from kamelle.models import ModelInfo  # noqa: E402
from kamelle.yamlmini import parse as parse_yaml  # noqa: E402

HERMES_CONFIG = "model:\n  default: anthropic/claude-opus-4.6\n  provider: auto\n# keep me\n"


def _model(model_id: str, context_length: int = 128_000) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        context_length=context_length,
        created=time.time() - 86_400,
        prompt_price=0.0,
        completion_price=0.0,
        provider=model_id.split("/")[0],
        raw={"architecture": {"modality": "text->text"}},
    )


CATALOG = [
    _model("qwen/qwen3-coder:free", 1_000_000),
    _model("deepseek/deepseek-r1:free", 500_000),
    _model("openrouter/free", 100_000),
]


def run(*argv: str) -> None:
    """Invoke the CLI the way a shell would, with the network stubbed out."""
    with patch.object(cli, "get_free_models", return_value=list(CATALOG)), \
         patch.object(cli, "get_api_key", return_value="sk-or-test"), \
         patch.object(sys, "argv", ["kamelle", *argv]):
        cli.main()


@pytest.fixture
def openclaw_config(tmp_path) -> Path:
    path = tmp_path / "openclaw.json"
    path.write_text(json.dumps({"meta": {"keep": "me"}}))
    return path


@pytest.fixture
def hermes_config(tmp_path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(HERMES_CONFIG)
    return path


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------

class TestAgentsCommand:
    def test_lists_every_backend(self, capsys):
        run("agents")
        out = capsys.readouterr().out
        for name in ("openclaw", "hermes", "generic"):
            assert name in out

    def test_marks_the_active_one(self, capsys):
        run("agents", "--agent", "hermes", "--json")
        rows = json.loads(capsys.readouterr().out)
        active = [row["name"] for row in rows if row["active"]]
        assert active == ["hermes"]

    def test_unknown_agent_is_rejected_by_the_parser(self):
        with pytest.raises(SystemExit):
            run("agents", "--agent", "emacs")


# ---------------------------------------------------------------------------
# auto
# ---------------------------------------------------------------------------

class TestAutoOnOpenClaw:
    def test_dry_run_changes_nothing(self, capsys, openclaw_config):
        before = openclaw_config.read_text()
        run("auto", "--agent-config", str(openclaw_config), "--dry-run")
        assert openclaw_config.read_text() == before
        assert "Dry run only" in capsys.readouterr().out

    def test_writes_primary_and_fallbacks(self, capsys, openclaw_config):
        run("auto", "--agent-config", str(openclaw_config))
        config = json.loads(openclaw_config.read_text())
        model = config["agents"]["defaults"]["model"]
        assert model["primary"] == "openrouter/qwen/qwen3-coder:free"
        assert "openrouter/free" in model["fallbacks"]
        assert config["meta"] == {"keep": "me"}

    def test_json_output_keeps_openclaw_spelling(self, capsys, openclaw_config):
        run("auto", "--agent-config", str(openclaw_config), "--json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["agent"] == "openclaw"
        assert payload["primary"] == "openrouter/qwen/qwen3-coder:free"
        assert payload["best_model"] == "qwen/qwen3-coder:free"

    def test_json_dry_run_does_not_write(self, capsys, openclaw_config):
        before = openclaw_config.read_text()
        run("auto", "--agent-config", str(openclaw_config), "--json", "--dry-run")
        assert openclaw_config.read_text() == before

    def test_keep_primary_only_touches_fallbacks(self, capsys, openclaw_config):
        run("auto", "--agent-config", str(openclaw_config))
        run("switch", "deepseek/deepseek-r1:free", "--agent-config", str(openclaw_config))
        run("auto", "--agent-config", str(openclaw_config), "--keep-primary")
        model = json.loads(openclaw_config.read_text())["agents"]["defaults"]["model"]
        assert model["primary"] == "openrouter/deepseek/deepseek-r1:free"


class TestAutoOnHermes:
    def test_writes_hermes_keys_and_keeps_comments(self, capsys, hermes_config):
        run("auto", "--agent", "hermes", "--agent-config", str(hermes_config))
        text = hermes_config.read_text()
        data = parse_yaml(text)
        assert data["model"]["default"] == "qwen/qwen3-coder:free"
        assert data["model"]["provider"] == "openrouter"
        assert data["fallback_providers"][0] == {"provider": "openrouter", "model": "openrouter/free"}
        assert "# keep me" in text

    def test_dry_run_changes_nothing(self, capsys, hermes_config):
        run("auto", "--agent", "hermes", "--agent-config", str(hermes_config), "--dry-run")
        assert hermes_config.read_text() == HERMES_CONFIG

    def test_plan_names_the_agent(self, capsys, hermes_config):
        run("auto", "--agent", "hermes", "--agent-config", str(hermes_config), "--dry-run")
        assert "plan for Hermes" in capsys.readouterr().out

    def test_environment_selects_hermes(self, capsys, hermes_config, monkeypatch):
        monkeypatch.setenv("KAMELLE_AGENT", "hermes")
        run("auto", "--agent-config", str(hermes_config))
        assert parse_yaml(hermes_config.read_text())["model"]["default"] == "qwen/qwen3-coder:free"


class TestAutoOnGeneric:
    def test_prints_a_json_document_on_stdout(self, capsys):
        run("auto", "--agent", "generic")
        document = json.loads(capsys.readouterr().out)
        assert document["primary"] == "qwen/qwen3-coder:free"

    def test_kamelle_chatter_goes_to_stderr(self, capsys):
        run("auto", "--agent", "generic")
        captured = capsys.readouterr()
        assert "Kamelle's plan" in captured.err
        assert "Kamelle's plan" not in captured.out

    def test_writes_yaml_to_a_file(self, capsys, tmp_path):
        target = tmp_path / "models.yaml"
        run("auto", "--agent", "generic", "-o", f"out={target}")
        assert parse_yaml(target.read_text())["primary"] == "qwen/qwen3-coder:free"

    def test_bad_option_fails_cleanly(self, capsys):
        with pytest.raises(SystemExit):
            run("auto", "--agent", "generic", "-o", "nonsense")


# ---------------------------------------------------------------------------
# the rest of the surface
# ---------------------------------------------------------------------------

class TestOtherCommands:
    def test_switch_targets_the_named_agent(self, capsys, hermes_config):
        run("switch", "deepseek", "--agent", "hermes", "--agent-config", str(hermes_config))
        assert parse_yaml(hermes_config.read_text())["model"]["default"] == "deepseek/deepseek-r1:free"

    def test_fallbacks_leaves_the_primary_alone(self, capsys, hermes_config):
        run("fallbacks", "--agent", "hermes", "--agent-config", str(hermes_config))
        data = parse_yaml(hermes_config.read_text())
        assert data["model"]["default"] == "anthropic/claude-opus-4.6"
        assert len(data["fallback_providers"]) == 3  # the stub catalog holds three

    def test_rollback_restores_the_previous_config(self, capsys, hermes_config):
        run("auto", "--agent", "hermes", "--agent-config", str(hermes_config))
        run("rollback", "--agent", "hermes", "--agent-config", str(hermes_config))
        assert hermes_config.read_text() == HERMES_CONFIG

    def test_rollback_without_a_backup_exits_nonzero(self, capsys, tmp_path):
        adapter_backup = cli.get_adapter("hermes").backup_path()
        adapter_backup.unlink(missing_ok=True)
        with pytest.raises(SystemExit) as excinfo:
            run("rollback", "--agent", "hermes", "--agent-config", str(tmp_path / "c.yaml"))
        assert excinfo.value.code == 1

    def test_status_reports_the_agent(self, capsys, hermes_config):
        run("auto", "--agent", "hermes", "--agent-config", str(hermes_config))
        capsys.readouterr()
        run("status", "--agent", "hermes", "--agent-config", str(hermes_config), "--json")
        payload = json.loads(capsys.readouterr().out)
        assert payload["agent"] == "hermes"
        assert payload["primary"] == "qwen/qwen3-coder:free"

    def test_list_tags_the_active_models(self, capsys, openclaw_config):
        run("auto", "--agent-config", str(openclaw_config))
        capsys.readouterr()
        run("list", "--agent-config", str(openclaw_config), "--json", "-n", "3")
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["status"] == "PRIMARY"
        assert any(row["status"] == "FALLBACK" for row in rows[1:])

    def test_profile_applies_to_the_named_agent(self, capsys, hermes_config):
        run("profile", "echo", "--agent", "hermes", "--agent-config", str(hermes_config))
        data = parse_yaml(hermes_config.read_text())
        assert data["model"]["default"] == "openrouter/free"
        assert data["fallback_providers"] == []

    def test_history_records_which_agent_changed(self, capsys, hermes_config):
        run("auto", "--agent", "hermes", "--agent-config", str(hermes_config))
        capsys.readouterr()
        run("history", "-n", "1")
        assert "hermes" in capsys.readouterr().out
