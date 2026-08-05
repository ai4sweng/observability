"""Branch coverage for kio_simulator.simulate_request() beyond the
accuracy-labeling regression test in test_kpi_metrics.py: the fully-simulated
path (no real Ollama) and both REAL outcomes (KIO2_REAL_LLM_ENABLED=true) —
success and failure. The real-failure case is the key regression this guards
(see simulate_request()'s docstring): a genuinely failed real Ollama call
must be recorded as a real kio.request error, never silently swapped for a
fresh simulated 'success'."""
import time

import pytest


def _quiet(m, monkeypatch):
    """Stub out the side-channel emitters that would otherwise attempt real
    network I/O (harmless when unreachable, but slow/noisy for a unit test —
    see their own docstrings for why they're safe to no-op)."""
    monkeypatch.setattr(m, "emit_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "emit_langfuse_trace", lambda *a, **k: None)
    monkeypatch.setattr(m, "emit_unstructured", lambda *a, **k: "stub summary")
    monkeypatch.setattr(m, "emit_real_kpi_metrics", lambda *a, **k: None)


def test_simulated_path_success(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO2_REAL_LLM_ENABLED="false")
    _quiet(m, monkeypatch)
    monkeypatch.setattr(m.random, "random", lambda: 0.99)  # > 0.07 -> not an error
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip the real sleep

    result = m.simulate_request(session_id="sess-sim-ok")

    assert result["status"] == "ok"
    assert result["error"] is None


def test_simulated_path_error(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO2_REAL_LLM_ENABLED="false")
    _quiet(m, monkeypatch)
    monkeypatch.setattr(m.random, "random", lambda: 0.0)  # < 0.07 -> forced error
    monkeypatch.setattr(time, "sleep", lambda s: None)

    result = m.simulate_request(session_id="sess-sim-err")

    assert result["status"] == "error"
    assert result["error"] in ("timeout", "internal", "rate_limit")


def test_real_success_path_uses_measured_values(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO2_REAL_LLM_ENABLED="true")
    _quiet(m, monkeypatch)
    monkeypatch.setattr(m, "_call_ollama_real", lambda prompt, model=None: {
        "ok": True, "text": "the bug is X", "input_tokens": 12, "output_tokens": 34,
        "duration_s": 0.05, "tokens_per_second": 680.0,
    })
    monkeypatch.setattr(m, "_read_gpu_power_watts", lambda: None)
    monkeypatch.setattr(m, "_read_gpu_temperature_celsius", lambda: None)

    result = m.simulate_request(session_id="sess-real-ok")

    assert result["status"] == "ok"
    assert result["output"]["tokens_out"] == 34


def test_real_failure_path_is_recorded_as_a_real_error(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO2_REAL_LLM_ENABLED="true")
    _quiet(m, monkeypatch)
    monkeypatch.setattr(m, "_call_ollama_real", lambda prompt, model=None: {
        "ok": False, "error_type": "ollama_unreachable", "duration_s": 0.05,
    })
    monkeypatch.setattr(m, "_read_gpu_power_watts", lambda: None)
    monkeypatch.setattr(m, "_read_gpu_temperature_celsius", lambda: None)

    result = m.simulate_request(session_id="sess-real-fail")

    assert result["status"] == "error"
    assert result["error"] == "ollama_unreachable"
    assert result["output"]["tokens_out"] == 0


def test_active_sessions_gauge_is_decremented_even_on_exception(fresh_kio_module, monkeypatch):
    """The `finally: active_sessions.add(-1, labels)` block must run even if
    something above it raises — verified by making emit_trace blow up and
    checking active_sessions still balances out."""
    m = fresh_kio_module(KIO2_REAL_LLM_ENABLED="false")
    monkeypatch.setattr(m.random, "random", lambda: 0.99)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    calls = []
    orig_add = m.active_sessions.add

    def _tracking_add(value, labels=None):
        calls.append(value)
        return orig_add(value, labels)

    monkeypatch.setattr(m.active_sessions, "add", _tracking_add)
    monkeypatch.setattr(m, "emit_trace", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        m.simulate_request(session_id="sess-exc")

    assert calls == [1, -1]
