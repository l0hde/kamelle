"""Tests for kamelle bench: openrouter.bench_model, state bench helpers, ranking bench_entry."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup so tests work from any cwd
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kamelle.models import ModelInfo  # noqa: E402
from kamelle.openrouter import (  # noqa: E402
    BENCH_EXPECTED_Q1,
    BENCH_EXPECTED_Q2,
    BENCH_PROMPT,
    bench_model,
    is_chat_model,
    is_free_model,
)
from kamelle.ranking import rank_models, score_model  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_model(**kw) -> ModelInfo:
    defaults = dict(
        id="test/model-1",
        context_length=128_000,
        created=time.time() - 86400,
        prompt_price=0.0,
        completion_price=0.0,
        provider="test",
        raw={},
    )
    defaults.update(kw)
    return ModelInfo(**defaults)


def _mock_response(status_code: int, body: dict | None = None, headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if body is not None:
        resp.json.return_value = body
    return resp


# ---------------------------------------------------------------------------
# bench_model — openrouter.py
# ---------------------------------------------------------------------------

class TestBenchModel:
    def test_passes_q1_and_q2(self):
        body = {"choices": [{"message": {"content": "68. Berlin."}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "ok"
        assert result["passes_q1"] is True
        assert result["passes_q2"] is True
        assert result["score"] == 2
        assert result["latency_ms"] is not None

    def test_passes_q1_only(self):
        body = {"choices": [{"message": {"content": "The answer is 68."}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["passes_q1"] is True
        assert result["passes_q2"] is False
        assert result["score"] == 1

    def test_passes_q2_only(self):
        body = {"choices": [{"message": {"content": "The capital is Berlin. I think 17*4=72."}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["passes_q1"] is False
        assert result["passes_q2"] is True
        assert result["score"] == 1

    def test_passes_neither(self):
        body = {"choices": [{"message": {"content": "I cannot answer math questions."}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["score"] == 0
        assert result["status"] == "ok"

    def test_rate_limit_429(self):
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(429)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "rate_limit"
        assert result["score"] == 0
        assert result["latency_ms"] is None

    def test_unavailable_503(self):
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(503)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "unavailable"

    def test_http_other_error(self):
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(500)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "http_500"
        assert result["latency_ms"] is not None

    def test_empty_response(self):
        body = {"choices": [{"message": {"content": ""}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "empty"
        assert result["score"] == 0

    def test_whitespace_only_response(self):
        body = {"choices": [{"message": {"content": "   \n  "}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "empty"

    def test_timeout(self):
        import requests as req
        with patch("kamelle.openrouter._retry_request", side_effect=req.Timeout()):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "timeout"
        assert result["latency_ms"] is None

    def test_request_exception(self):
        import requests as req
        with patch("kamelle.openrouter._retry_request", side_effect=req.RequestException("conn refused")):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "error"

    def test_malformed_json_response(self):
        body = {"choices": []}  # no message key
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["status"] == "empty"

    def test_response_truncated_at_400_chars(self):
        long_text = "x" * 1000 + "68" + "berlin"
        body = {"choices": [{"message": {"content": long_text}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        # response field is truncated; passes_q1/q2 depend on which part was kept
        assert len(result["response"]) <= 400

    def test_berlin_case_insensitive(self):
        body = {"choices": [{"message": {"content": "68 BERLIN"}}]}
        with patch("kamelle.openrouter._retry_request", return_value=_mock_response(200, body)):
            result = bench_model("fake-key", "test/model")
        assert result["passes_q2"] is True  # BERLIN.lower() contains "berlin"

    def test_bench_prompt_constant_not_empty(self):
        assert "17" in BENCH_PROMPT
        assert "Germany" in BENCH_PROMPT

    def test_bench_expected_values(self):
        assert BENCH_EXPECTED_Q1 == "68"
        assert BENCH_EXPECTED_Q2 == "berlin"


# ---------------------------------------------------------------------------
# State bench helpers — state.py
# ---------------------------------------------------------------------------

class TestBenchState:
    def test_save_and_get_bench_entry(self, tmp_path, monkeypatch):
        import kamelle.state as st
        monkeypatch.setattr(st, "BENCH_FILE", tmp_path / "bench.json")
        entry = {"status": "ok", "latency_ms": 500, "score": 2,
                 "passes_q1": True, "passes_q2": True, "response": "68. Berlin."}
        saved = st.save_bench_entry("test/model", entry)
        assert "measured_at" in saved
        loaded = st.get_bench_entry("test/model")
        assert loaded is not None
        assert loaded["status"] == "ok"
        assert loaded["score"] == 2

    def test_get_bench_entry_missing(self, tmp_path, monkeypatch):
        import kamelle.state as st
        monkeypatch.setattr(st, "BENCH_FILE", tmp_path / "bench.json")
        assert st.get_bench_entry("nonexistent/model") is None

    def test_load_all_bench_entries(self, tmp_path, monkeypatch):
        import kamelle.state as st
        monkeypatch.setattr(st, "BENCH_FILE", tmp_path / "bench.json")
        st.save_bench_entry("a/model", {"status": "ok", "score": 2})
        st.save_bench_entry("b/model", {"status": "error", "score": 0})
        all_entries = st.load_all_bench_entries()
        assert "a/model" in all_entries
        assert "b/model" in all_entries

    def test_bench_entry_is_fresh_within_24h(self, tmp_path, monkeypatch):
        import kamelle.state as st
        monkeypatch.setattr(st, "BENCH_FILE", tmp_path / "bench.json")
        st.save_bench_entry("x/model", {"status": "ok", "score": 1})
        entry = st.get_bench_entry("x/model")
        assert st.bench_entry_is_fresh(entry, hours=24) is True

    def test_bench_entry_is_stale(self):
        from datetime import datetime, timedelta
        import kamelle.state as st
        old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        entry = {"measured_at": old_ts, "status": "ok"}
        assert st.bench_entry_is_fresh(entry, hours=24) is False

    def test_bench_entry_is_fresh_none(self):
        import kamelle.state as st
        assert st.bench_entry_is_fresh(None) is False

    def test_bench_entry_is_fresh_missing_field(self):
        import kamelle.state as st
        assert st.bench_entry_is_fresh({}) is False

    def test_save_bench_entry_overwrites(self, tmp_path, monkeypatch):
        import kamelle.state as st
        monkeypatch.setattr(st, "BENCH_FILE", tmp_path / "bench.json")
        st.save_bench_entry("x/model", {"status": "ok", "score": 1})
        st.save_bench_entry("x/model", {"status": "ok", "score": 2})
        entry = st.get_bench_entry("x/model")
        assert entry["score"] == 2


# ---------------------------------------------------------------------------
# Ranking — score_model with bench_entry
# ---------------------------------------------------------------------------

class TestScoreModelWithBench:
    def test_full_bench_pass_adds_bonus(self):
        m = _make_model()
        score_no_bench = score_model(m, None, None)
        bench_pass = {"status": "ok", "score": 2}
        score_with_bench = score_model(m, None, bench_pass)
        assert score_with_bench > score_no_bench

    def test_partial_bench_pass_adds_half_bonus(self):
        m = _make_model()
        bench_half = {"status": "ok", "score": 1}
        bench_full = {"status": "ok", "score": 2}
        score_half = score_model(m, None, bench_half)
        score_full = score_model(m, None, bench_full)
        assert score_half < score_full

    def test_bench_fail_applies_penalty(self):
        m = _make_model()
        score_no_bench = score_model(m, None, None)
        bench_fail = {"status": "error", "score": 0}
        score_with_fail = score_model(m, None, bench_fail)
        assert score_with_fail < score_no_bench

    def test_bench_timeout_applies_penalty(self):
        m = _make_model()
        score_baseline = score_model(m, None, None)
        bench_timeout = {"status": "timeout", "score": 0}
        assert score_model(m, None, bench_timeout) < score_baseline

    def test_bench_rate_limit_applies_penalty(self):
        m = _make_model()
        score_baseline = score_model(m, None, None)
        bench_rl = {"status": "rate_limit", "score": 0}
        assert score_model(m, None, bench_rl) < score_baseline

    def test_rank_models_with_bench_map(self):
        m1 = _make_model(id="p/a", context_length=32_000, completion_price=0.0)
        m2 = _make_model(id="p/b", context_length=32_000, completion_price=0.0)
        bench_map = {
            "p/a": {"status": "ok", "score": 2},
            "p/b": {"status": "ok", "score": 0},
        }
        ranked = rank_models([m1, m2], bench_map=bench_map)
        assert ranked[0].id == "p/a"

    def test_rank_models_without_bench_map(self):
        m1 = _make_model(id="p/a", context_length=500_000)
        m2 = _make_model(id="p/b", context_length=100_000)
        ranked = rank_models([m1, m2])
        assert ranked[0].id == "p/a"

    def test_rank_models_bench_map_none_entry(self):
        m1 = _make_model(id="p/a")
        m2 = _make_model(id="p/b")
        # bench_map exists but p/a not in it
        bench_map = {"p/b": {"status": "ok", "score": 2}}
        ranked = rank_models([m1, m2], bench_map=bench_map)
        # p/b should rank higher due to bench bonus
        assert ranked[0].id == "p/b"


# ---------------------------------------------------------------------------
# is_chat_model filter — openrouter.py
# ---------------------------------------------------------------------------

class TestIsChatModel:
    def _model_with_modality(self, model_id: str, modality: str | None) -> ModelInfo:
        raw = {}
        if modality is not None:
            raw["architecture"] = {"modality": modality}
        return _make_model(id=model_id, raw=raw)

    def test_text_to_text_is_chat(self):
        m = self._model_with_modality("x/gpt", "text->text")
        assert is_chat_model(m) is True

    def test_text_plus_image_to_text_is_chat(self):
        m = self._model_with_modality("x/vision", "text+image->text")
        assert is_chat_model(m) is True

    def test_audio_to_audio_is_not_chat(self):
        m = self._model_with_modality("google/lyria", "audio->audio")
        assert is_chat_model(m) is False

    def test_text_to_audio_is_not_chat(self):
        m = self._model_with_modality("openai/tts", "text->audio")
        assert is_chat_model(m) is False

    def test_text_to_image_is_not_chat(self):
        m = self._model_with_modality("x/dalle", "text->image")
        assert is_chat_model(m) is False

    def test_lyria_id_filter(self):
        m = _make_model(id="google/lyria-3-pro-preview")
        assert is_chat_model(m) is False

    def test_dall_e_id_filter(self):
        m = _make_model(id="openai/dall-e-3")
        assert is_chat_model(m) is False

    def test_no_architecture_key_passes(self):
        m = _make_model(id="x/some-chat-model")
        assert is_chat_model(m) is True

    def test_empty_modality_passes(self):
        m = self._model_with_modality("x/chat", None)
        assert is_chat_model(m) is True
