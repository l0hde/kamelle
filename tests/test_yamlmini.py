"""Tests for kamelle.yamlmini: the block-YAML subset reader and the surgical writer.

The writer's whole reason to exist is that it must not disturb a config file it
does not own, so most of these tests assert on what stayed the same.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kamelle.yamlmini import (  # noqa: E402
    YamlMiniError,
    delete_path,
    dump,
    get_path,
    parse,
    set_path,
)

SAMPLE = """\
# Hermes-style config
database:
  journal_mode: wal
model:
  default: anthropic/claude-opus-4.6   # the picker writes this
  provider: auto
  base_url: https://openrouter.ai/api/v1
agent:
  max_turns: 500
  verbose: false
  personalities: {}
platform_toolsets:
  cli:
    - hermes-cli
  slack:
    - hermes-slack
stt:
  openai:
    language: ''
_config_version: 45

# ── Fallback Model ────────────────────────────────────────────────
# fallback_model:
#   provider: openrouter
"""


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

class TestParse:
    def test_nested_mappings(self):
        data = parse(SAMPLE)
        assert data["model"]["default"] == "anthropic/claude-opus-4.6"
        assert data["database"]["journal_mode"] == "wal"
        assert data["stt"]["openai"]["language"] == ""

    def test_scalar_types(self):
        data = parse(SAMPLE)
        assert data["agent"]["max_turns"] == 500
        assert data["agent"]["verbose"] is False
        assert data["_config_version"] == 45

    def test_inline_comment_is_not_part_of_the_value(self):
        assert parse(SAMPLE)["model"]["default"] == "anthropic/claude-opus-4.6"

    def test_url_value_keeps_its_colon(self):
        assert parse(SAMPLE)["model"]["base_url"] == "https://openrouter.ai/api/v1"

    def test_empty_flow_mapping(self):
        assert parse(SAMPLE)["agent"]["personalities"] == {}

    def test_sequence_of_scalars(self):
        assert parse(SAMPLE)["platform_toolsets"]["cli"] == ["hermes-cli"]

    def test_sequence_at_key_indent(self):
        data = parse("chain:\n- provider: openrouter\n  model: a/b:free\n- provider: x\n  model: c/d\n")
        assert data["chain"] == [
            {"provider": "openrouter", "model": "a/b:free"},
            {"provider": "x", "model": "c/d"},
        ]

    def test_sequence_indented_below_key(self):
        data = parse("chain:\n  - provider: openrouter\n    model: a/b:free\n")
        assert data["chain"] == [{"provider": "openrouter", "model": "a/b:free"}]

    def test_key_after_same_indent_sequence(self):
        data = parse("chain:\n- one\n- two\nafter: yes\n")
        assert data == {"chain": ["one", "two"], "after": True}

    def test_empty_key_without_children_is_null(self):
        assert parse("model:\nother: 1\n")["model"] is None

    def test_quoted_values(self):
        data = parse("a: 'it''s here'\nb: \"plain\"\n")
        assert data == {"a": "it's here", "b": "plain"}

    def test_empty_document(self):
        assert parse("") == {}
        assert parse("# only a comment\n") == {}

    def test_anchors_are_refused(self):
        with pytest.raises(YamlMiniError):
            parse("base: &anchor\n  a: 1\n")

    def test_flow_collection_with_content_is_refused(self):
        with pytest.raises(YamlMiniError):
            parse("models: [a, b]\n")


class TestGetPath:
    def test_hit_and_miss(self):
        data = parse(SAMPLE)
        assert get_path(data, ("model", "provider")) == "auto"
        assert get_path(data, ("model", "nope")) is None
        assert get_path(data, ("nope", "deeper"), "fallback") == "fallback"


# ---------------------------------------------------------------------------
# set_path
# ---------------------------------------------------------------------------

class TestSetPath:
    def test_replaces_a_nested_scalar_in_place(self):
        out = set_path(SAMPLE, ("model", "default"), "qwen/qwen3-coder:free")
        assert parse(out)["model"]["default"] == "qwen/qwen3-coder:free"
        assert "anthropic/claude-opus-4.6" not in out

    def test_leaves_every_other_line_untouched(self):
        out = set_path(SAMPLE, ("model", "default"), "x/y:free")
        changed = [
            (a, b) for a, b in zip(SAMPLE.splitlines(), out.splitlines()) if a != b
        ]
        assert len(changed) == 1
        assert changed[0][0].strip().startswith("default:")

    def test_keeps_comments(self):
        out = set_path(SAMPLE, ("model", "default"), "x/y:free")
        assert "# Hermes-style config" in out
        assert "# ── Fallback Model" in out
        assert out.count("#") == SAMPLE.count("#") - 1  # only the inline one is replaced

    def test_appends_a_new_top_level_key(self):
        out = set_path(SAMPLE, ("fallback_providers",), [{"provider": "openrouter", "model": "a/b:free"}])
        assert parse(out)["fallback_providers"] == [{"provider": "openrouter", "model": "a/b:free"}]
        assert out.startswith(SAMPLE.rstrip("\n").split("\n")[0])

    def test_rewrites_an_existing_list_without_duplicating_the_key(self):
        once = set_path(SAMPLE, ("fallback_providers",), [{"provider": "p", "model": "a"}])
        twice = set_path(once, ("fallback_providers",), [{"provider": "p", "model": "b"}])
        assert twice.count("fallback_providers:") == 1
        assert parse(twice)["fallback_providers"] == [{"provider": "p", "model": "b"}]

    def test_empty_list_renders_inline(self):
        out = set_path(SAMPLE, ("fallback_providers",), [])
        assert "fallback_providers: []" in out
        assert parse(out)["fallback_providers"] == []

    def test_inserts_a_missing_key_under_an_existing_parent(self):
        out = set_path(SAMPLE, ("model", "api_mode"), "chat")
        data = parse(out)
        assert data["model"]["api_mode"] == "chat"
        assert data["model"]["default"] == "anthropic/claude-opus-4.6"

    def test_creates_intermediate_mappings_for_a_new_branch(self):
        out = set_path(SAMPLE, ("routing", "deep", "value"), 7)
        assert parse(out)["routing"] == {"deep": {"value": 7}}

    def test_turns_an_empty_flow_mapping_into_a_block(self):
        out = set_path(SAMPLE, ("agent", "personalities", "default"), "kamelle")
        assert parse(out)["agent"]["personalities"] == {"default": "kamelle"}
        assert parse(out)["agent"]["max_turns"] == 500

    def test_refuses_to_nest_under_a_scalar(self):
        with pytest.raises(YamlMiniError):
            set_path(SAMPLE, ("model", "default", "deeper"), "x")

    def test_empty_path_is_refused(self):
        with pytest.raises(YamlMiniError):
            set_path(SAMPLE, (), "x")

    def test_sequence_items_do_not_shadow_real_keys(self):
        text = "chain:\n  - provider: openrouter\n    model: a/b\nmodel:\n  default: keep-me\n"
        out = set_path(text, ("model", "default"), "changed")
        data = parse(out)
        assert data["model"]["default"] == "changed"
        assert data["chain"] == [{"provider": "openrouter", "model": "a/b"}]

    def test_values_needing_quotes_are_quoted(self):
        out = set_path(SAMPLE, ("model", "default"), "yes")
        assert parse(out)["model"]["default"] == "yes"


class TestDeletePath:
    def test_removes_a_top_level_block(self):
        text = SAMPLE + "fallback_model:\n  provider: openrouter\n  model: a/b\n"
        out = delete_path(text, ("fallback_model",))
        assert "fallback_model" not in parse(out)
        assert parse(out)["model"]["default"] == "anthropic/claude-opus-4.6"
        # The commented-out example of the same key is not a key, so it stays.
        assert "# fallback_model:" in out

    def test_missing_key_is_a_no_op(self):
        assert delete_path(SAMPLE, ("nope",)) == SAMPLE

    def test_removes_a_nested_key_only(self):
        out = delete_path(SAMPLE, ("model", "provider"))
        data = parse(out)
        assert "provider" not in data["model"]
        assert data["model"]["base_url"] == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# dump
# ---------------------------------------------------------------------------

class TestDump:
    def test_round_trips_through_parse(self):
        document = {
            "primary": "a/b:free",
            "fallbacks": ["c/d:free", "e/f:free"],
            "provider": {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1"},
            "count": 3,
            "enabled": True,
            "missing": None,
        }
        assert parse(dump(document)) == document

    def test_list_of_mappings_round_trips(self):
        document = {"chain": [{"provider": "openrouter", "model": "a/b:free"}]}
        assert parse(dump(document)) == document

    def test_empty_containers(self):
        assert parse(dump({"a": [], "b": {}})) == {"a": [], "b": {}}

    def test_top_level_must_be_a_mapping(self):
        with pytest.raises(YamlMiniError):
            dump(["not", "a", "mapping"])  # type: ignore[arg-type]


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None, reason="PyYAML not installed"
)
class TestAgreesWithPyYAML:
    """PyYAML is the reference implementation whenever it happens to be around."""

    def test_sample_parses_identically(self):
        import yaml

        assert parse(SAMPLE) == yaml.safe_load(SAMPLE)

    def test_written_file_parses_identically(self):
        import yaml

        out = set_path(SAMPLE, ("model", "default"), "qwen/qwen3-coder:free")
        out = set_path(out, ("fallback_providers",), [{"provider": "openrouter", "model": "a/b:free"}])
        assert parse(out) == yaml.safe_load(out)

    def test_dump_is_readable_by_pyyaml(self):
        import yaml

        document = {"primary": "a/b:free", "fallbacks": ["c/d:free"]}
        assert yaml.safe_load(dump(document)) == document
