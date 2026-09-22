"""Tests for the agent adapter layer: registry, OpenClaw, Hermes, generic.

Every adapter is exercised against a config file in a tmp_path, never against
the real one on the machine running the tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kamelle.adapters import (  # noqa: E402
    ADAPTERS,
    DEFAULT_ADAPTER,
    AdapterError,
    AgentState,
    ModelPlan,
    adapter_names,
    detect_installed,
    get_adapter,
    parse_options,
    resolve_name,
    same_model,
)
from kamelle.adapters.generic import GenericAdapter  # noqa: E402
from kamelle.adapters.hermes import HermesAdapter  # noqa: E402
from kamelle.adapters.openclaw import OpenClawAdapter  # noqa: E402
from kamelle.yamlmini import parse as parse_yaml  # noqa: E402

HERMES_CONFIG = """\
# my hand-written hermes config
database:
  journal_mode: wal
model:
  default: anthropic/claude-opus-4.6
  provider: auto
  base_url: https://openrouter.ai/api/v1
agent:
  max_turns: 500

# ── Fallback Model ───────────────────────────────────────────────
# fallback_model:
#   provider: openrouter
_config_version: 45
"""

PLAN = ModelPlan(primary="qwen/qwen3-coder:free", fallbacks=["openrouter/free", "deepseek/deepseek-r1:free"])


@pytest.fixture
def hermes(tmp_path) -> HermesAdapter:
    path = tmp_path / "config.yaml"
    path.write_text(HERMES_CONFIG)
    return HermesAdapter(config_path=path)


@pytest.fixture
def openclaw(tmp_path) -> OpenClawAdapter:
    path = tmp_path / "openclaw.json"
    path.write_text(json.dumps({"meta": {"keep": "me"}, "env": {"OPENROUTER_API_KEY": "sk-or-test"}}))
    return OpenClawAdapter(config_path=path)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_ships_the_three_documented_backends(self):
        assert set(adapter_names()) == {"openclaw", "hermes", "generic"}

    def test_default_is_still_openclaw(self):
        assert DEFAULT_ADAPTER == "openclaw"
        assert resolve_name(None) == "openclaw"

    def test_every_registered_name_matches_its_class(self):
        for name, cls in ADAPTERS.items():
            assert cls.name == name
            assert cls.display_name and cls.summary

    def test_environment_selects_the_agent(self, monkeypatch):
        monkeypatch.setenv("KAMELLE_AGENT", "hermes")
        assert resolve_name(None) == "hermes"

    def test_explicit_argument_beats_the_environment(self, monkeypatch):
        monkeypatch.setenv("KAMELLE_AGENT", "hermes")
        assert resolve_name("generic") == "generic"

    def test_unknown_agent_lists_the_known_ones(self):
        with pytest.raises(AdapterError) as excinfo:
            resolve_name("emacs")
        assert "openclaw" in str(excinfo.value)

    def test_get_adapter_builds_the_class(self):
        assert isinstance(get_adapter("hermes"), HermesAdapter)

    def test_parse_options(self):
        assert parse_options(["out=/tmp/a.json", "format=yaml"]) == {"out": "/tmp/a.json", "format": "yaml"}
        assert parse_options(None) == {}

    def test_malformed_option_is_rejected(self):
        with pytest.raises(AdapterError):
            parse_options(["outyaml"])

    def test_unknown_option_is_rejected(self):
        with pytest.raises(AdapterError) as excinfo:
            get_adapter("hermes", options=["nonsense=1"])
        assert "nonsense" in str(excinfo.value)

    def test_detect_installed_returns_adapter_instances(self):
        for adapter in detect_installed():
            assert adapter.is_installed()


class TestSameModel:
    @pytest.mark.parametrize("left,right", [
        ("a/b:free", "a/b:free"),
        ("a/b", "a/b:free"),
        ("a/b:free", "a/b"),
    ])
    def test_matches(self, left, right):
        assert same_model(left, right)

    @pytest.mark.parametrize("left,right", [
        ("a/b:free", "a/c:free"),
        (None, "a/b"),
        ("a/b", None),
        ("", "a/b"),
    ])
    def test_does_not_match(self, left, right):
        assert not same_model(left, right)


class TestStatusTag:
    def test_tags_primary_and_fallback(self):
        adapter = get_adapter("generic")
        state = AgentState(primary="a/b:free", fallbacks=["c/d:free"])
        assert adapter.status_tag(state, "a/b:free") == "PRIMARY"
        assert adapter.status_tag(state, "c/d:free") == "FALLBACK"
        assert adapter.status_tag(state, "e/f:free") == ""

    def test_tolerates_a_missing_free_suffix(self):
        adapter = get_adapter("generic")
        state = AgentState(primary="a/b:free", fallbacks=[])
        assert adapter.status_tag(state, "a/b") == "PRIMARY"


# ---------------------------------------------------------------------------
# OpenClaw
# ---------------------------------------------------------------------------

class TestOpenClawAdapter:
    def test_writes_the_documented_shape(self, openclaw):
        after = openclaw.apply(openclaw.load(), PLAN)
        model = after["agents"]["defaults"]["model"]
        assert model["primary"] == "openrouter/qwen/qwen3-coder:free"
        assert model["fallbacks"] == ["openrouter/free", "deepseek/deepseek-r1:free"]

    def test_registers_every_model_it_names(self, openclaw):
        after = openclaw.apply(openclaw.load(), PLAN)
        registry = after["agents"]["defaults"]["models"]
        assert "qwen/qwen3-coder:free" in registry
        assert "deepseek/deepseek-r1:free" in registry

    def test_keeps_unrelated_config(self, openclaw):
        after = openclaw.apply(openclaw.load(), PLAN)
        assert after["meta"] == {"keep": "me"}

    def test_apply_does_not_mutate_the_snapshot(self, openclaw):
        before = openclaw.load()
        openclaw.apply(before, PLAN)
        assert "agents" not in before

    def test_describe_returns_openrouter_ids(self, openclaw):
        state = openclaw.describe(openclaw.apply(openclaw.load(), PLAN))
        assert state.primary == "qwen/qwen3-coder:free"
        assert state.fallbacks == ["openrouter/free", "deepseek/deepseek-r1:free"]

    def test_native_keeps_openclaws_own_spelling(self, openclaw):
        native = openclaw.native(openclaw.apply(openclaw.load(), PLAN))
        assert native["primary"] == "openrouter/qwen/qwen3-coder:free"

    def test_router_pseudo_model_keeps_its_double_prefix(self, openclaw):
        after = openclaw.apply(openclaw.load(), ModelPlan(primary="openrouter/free", fallbacks=[]))
        assert after["agents"]["defaults"]["model"]["primary"] == "openrouter/openrouter/free"
        assert openclaw.describe(after).primary == "openrouter/free"

    def test_save_and_reload(self, openclaw):
        openclaw.save(openclaw.apply(openclaw.load(), PLAN))
        assert openclaw.describe(openclaw.load()).primary == "qwen/qwen3-coder:free"

    def test_backup_and_restore(self, openclaw):
        before = openclaw.load()
        openclaw.backup(before)
        openclaw.save(openclaw.apply(before, PLAN))
        assert openclaw.restore() is True
        assert openclaw.load() == before

    def test_reads_the_api_key_from_its_config(self, openclaw):
        assert openclaw.api_key() == "sk-or-test"

    def test_missing_config_is_an_empty_snapshot(self, tmp_path):
        adapter = OpenClawAdapter(config_path=tmp_path / "nope.json")
        assert adapter.load() == {}
        assert adapter.describe(adapter.load()) == AgentState(None, [])


# ---------------------------------------------------------------------------
# Hermes
# ---------------------------------------------------------------------------

class TestHermesAdapter:
    def test_reads_the_current_selection(self, hermes):
        state = hermes.describe(hermes.load())
        assert state.primary == "anthropic/claude-opus-4.6"
        assert state.fallbacks == []

    def test_writes_model_default_and_the_chain(self, hermes):
        after = hermes.apply(hermes.load(), PLAN)
        data = parse_yaml(after.text)
        assert data["model"]["default"] == "qwen/qwen3-coder:free"
        assert data["fallback_providers"] == [
            {"provider": "openrouter", "model": "openrouter/free"},
            {"provider": "openrouter", "model": "deepseek/deepseek-r1:free"},
        ]

    def test_points_the_provider_at_openrouter(self, hermes):
        data = parse_yaml(hermes.apply(hermes.load(), PLAN).text)
        assert data["model"]["provider"] == "openrouter"

    def test_keeps_an_existing_base_url(self, hermes):
        data = parse_yaml(hermes.apply(hermes.load(), PLAN).text)
        assert data["model"]["base_url"] == "https://openrouter.ai/api/v1"

    def test_fills_in_a_missing_base_url(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("model:\n  default: something\n")
        adapter = HermesAdapter(config_path=path)
        data = parse_yaml(adapter.apply(adapter.load(), PLAN).text)
        assert data["model"]["base_url"] == "https://openrouter.ai/api/v1"

    def test_keeps_every_comment_and_unrelated_key(self, hermes):
        after = hermes.apply(hermes.load(), PLAN)
        assert "# my hand-written hermes config" in after.text
        assert "# ── Fallback Model" in after.text
        data = parse_yaml(after.text)
        assert data["database"] == {"journal_mode": "wal"}
        assert data["agent"] == {"max_turns": 500}
        assert data["_config_version"] == 45

    def test_touches_only_the_lines_it_owns(self, hermes):
        after = hermes.apply(hermes.load(), PLAN)
        before_lines = set(HERMES_CONFIG.splitlines())
        removed = before_lines - set(after.text.splitlines())
        assert removed == {
            "  default: anthropic/claude-opus-4.6",
            "  provider: auto",
        }

    def test_drops_the_legacy_fallback_key(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(HERMES_CONFIG + "fallback_model:\n  provider: openrouter\n  model: old/model\n")
        adapter = HermesAdapter(config_path=path)
        after = adapter.apply(adapter.load(), PLAN)
        assert "fallback_model" not in parse_yaml(after.text)
        assert adapter.describe(after).fallbacks == ["openrouter/free", "deepseek/deepseek-r1:free"]

    def test_reads_a_legacy_chain_before_it_is_rewritten(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(HERMES_CONFIG + "fallback_model:\n  provider: openrouter\n  model: old/model\n")
        adapter = HermesAdapter(config_path=path)
        assert adapter.describe(adapter.load()).fallbacks == ["old/model"]

    def test_rewriting_twice_leaves_one_chain(self, hermes):
        once = hermes.apply(hermes.load(), PLAN)
        twice = hermes.apply(once, ModelPlan(primary="a/b:free", fallbacks=["c/d:free"]))
        assert twice.text.count("fallback_providers:") == 1
        assert hermes.describe(twice).fallbacks == ["c/d:free"]

    def test_empty_chain_is_written_as_an_empty_list(self, hermes):
        after = hermes.apply(hermes.load(), ModelPlan(primary="a/b:free", fallbacks=[]))
        assert parse_yaml(after.text)["fallback_providers"] == []

    def test_keeping_the_primary_leaves_model_default_alone(self, hermes):
        after = hermes.apply(hermes.load(), ModelPlan(primary=None, fallbacks=["c/d:free"]))
        assert parse_yaml(after.text)["model"]["default"] == "anthropic/claude-opus-4.6"
        assert hermes.describe(after).fallbacks == ["c/d:free"]

    def test_save_then_reload_round_trips(self, hermes):
        hermes.save(hermes.apply(hermes.load(), PLAN))
        state = hermes.describe(hermes.load())
        assert state.primary == "qwen/qwen3-coder:free"
        assert state.fallbacks == ["openrouter/free", "deepseek/deepseek-r1:free"]

    def test_backup_restores_the_file_byte_for_byte(self, hermes):
        before = hermes.load()
        hermes.backup(before)
        hermes.save(hermes.apply(before, PLAN))
        assert hermes.restore() is True
        assert hermes.config_path.read_text() == HERMES_CONFIG

    def test_restore_without_a_backup_is_false(self, tmp_path):
        adapter = HermesAdapter(config_path=tmp_path / "config.yaml")
        adapter.backup_path().unlink(missing_ok=True)
        assert adapter.restore() is False

    def test_missing_config_still_produces_a_valid_plan(self, tmp_path):
        adapter = HermesAdapter(config_path=tmp_path / "nothing-here.yaml")
        after = adapter.apply(adapter.load(), PLAN)
        assert adapter.describe(after).primary == "qwen/qwen3-coder:free"

    def test_config_the_line_editor_cannot_rewrite_is_refused(self, tmp_path):
        # PyYAML reads anchors happily; the surgical writer must not pretend to.
        path = tmp_path / "config.yaml"
        path.write_text("model: &anchor\n  default: x\n")
        adapter = HermesAdapter(config_path=path)
        with pytest.raises(AdapterError):
            adapter.apply(adapter.load(), PLAN)
        assert path.read_text() == "model: &anchor\n  default: x\n"

    def test_provider_can_be_overridden(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(HERMES_CONFIG)
        adapter = HermesAdapter(config_path=path, options={"provider": "nous"})
        data = parse_yaml(adapter.apply(adapter.load(), PLAN).text)
        assert data["model"]["provider"] == "nous"
        assert data["fallback_providers"][0]["provider"] == "nous"

    def test_reads_the_api_key_from_the_env_file(self, tmp_path):
        (tmp_path / ".env").write_text("SOMETHING=1\nOPENROUTER_API_KEY=\"sk-or-hermes\"\n")
        adapter = HermesAdapter(config_path=tmp_path / "config.yaml")
        assert adapter.api_key() == "sk-or-hermes"

    def test_no_env_file_means_no_key(self, tmp_path):
        adapter = HermesAdapter(config_path=tmp_path / "config.yaml")
        assert adapter.api_key() is None


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------

class TestGenericAdapter:
    def test_defaults_to_json_on_stdout(self, capsys):
        adapter = GenericAdapter()
        assert adapter.writes_to_stdout is True
        assert adapter.location() == "stdout"
        adapter.save(adapter.apply(adapter.load(), PLAN))
        document = json.loads(capsys.readouterr().out)
        assert document["primary"] == "qwen/qwen3-coder:free"
        assert document["fallbacks"] == ["openrouter/free", "deepseek/deepseek-r1:free"]

    def test_names_the_provider_and_the_key_variable(self, capsys):
        adapter = GenericAdapter()
        adapter.save(adapter.apply(adapter.load(), PLAN))
        provider = json.loads(capsys.readouterr().out)["provider"]
        assert provider["name"] == "openrouter"
        assert provider["api_key_env"] == "OPENROUTER_API_KEY"

    def test_writes_a_json_file(self, tmp_path):
        target = tmp_path / "models.json"
        adapter = GenericAdapter(options={"out": str(target)})
        assert adapter.writes_to_stdout is False
        adapter.save(adapter.apply(adapter.load(), PLAN))
        assert json.loads(target.read_text())["primary"] == "qwen/qwen3-coder:free"

    def test_infers_yaml_from_the_file_suffix(self, tmp_path):
        target = tmp_path / "models.yaml"
        adapter = GenericAdapter(options={"out": str(target)})
        assert adapter.format == "yaml"
        adapter.save(adapter.apply(adapter.load(), PLAN))
        assert parse_yaml(target.read_text())["primary"] == "qwen/qwen3-coder:free"

    def test_explicit_format_wins_over_the_suffix(self, tmp_path):
        target = tmp_path / "models.json"
        adapter = GenericAdapter(options={"out": str(target), "format": "yaml"})
        adapter.save(adapter.apply(adapter.load(), PLAN))
        assert parse_yaml(target.read_text())["primary"] == "qwen/qwen3-coder:free"

    def test_unknown_format_is_rejected(self):
        with pytest.raises(AdapterError):
            GenericAdapter(options={"format": "toml"})

    def test_reads_back_what_it_wrote(self, tmp_path):
        target = tmp_path / "models.json"
        adapter = GenericAdapter(options={"out": str(target)})
        adapter.save(adapter.apply(adapter.load(), PLAN))
        state = adapter.describe(adapter.load())
        assert state.primary == "qwen/qwen3-coder:free"
        assert state.fallbacks == ["openrouter/free", "deepseek/deepseek-r1:free"]

    def test_keeps_the_previous_primary_when_the_plan_has_none(self, tmp_path):
        target = tmp_path / "models.json"
        adapter = GenericAdapter(options={"out": str(target)})
        adapter.save(adapter.apply(adapter.load(), PLAN))
        after = adapter.apply(adapter.load(), ModelPlan(primary=None, fallbacks=["x/y:free"]))
        assert after["primary"] == "qwen/qwen3-coder:free"
        assert after["fallbacks"] == ["x/y:free"]

    def test_backup_and_restore_a_file(self, tmp_path):
        target = tmp_path / "models.json"
        adapter = GenericAdapter(options={"out": str(target)})
        adapter.save(adapter.apply(adapter.load(), PLAN))
        first = adapter.load()
        adapter.backup(first)
        adapter.save(adapter.apply(first, ModelPlan(primary="other/model:free", fallbacks=[])))
        assert adapter.restore() is True
        assert adapter.describe(adapter.load()).primary == "qwen/qwen3-coder:free"

    def test_nothing_to_back_up_on_a_first_run(self, tmp_path):
        adapter = GenericAdapter(options={"out": str(tmp_path / "models.json")})
        assert adapter.backup(adapter.load()) is None

    def test_stdout_mode_cannot_restore(self):
        assert GenericAdapter().restore() is False

    def test_broken_document_raises(self, tmp_path):
        target = tmp_path / "models.json"
        target.write_text("{ not json")
        with pytest.raises(AdapterError):
            GenericAdapter(options={"out": str(target)}).load()
