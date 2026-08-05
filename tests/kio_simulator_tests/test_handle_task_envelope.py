"""Unit tests for kio_simulator.handle_task_envelope() — the NATS task
handler's core logic, deliberately factored out (per its own docstring) so
it's testable against a fake `nc` (anything with an async publish) without a
real NATS server."""
import json

import pytest


class _FakeNats:
    def __init__(self):
        self.published = []

    async def publish(self, subject, data):
        self.published.append((subject, data))


def _envelope_json(session_id="sess-1", kio_id="kio7", task_type="ai-sysdev"):
    return json.dumps({
        "session_id": session_id,
        "kio_id": kio_id,
        "task_type": task_type,
        "payload": {},
        "envelope_id": "env-1",
        "created_at": "2026-08-05T00:00:00+00:00",
        "schema_version": "1.0",
    }).encode()


@pytest.mark.asyncio
async def test_happy_path_publishes_result_to_this_kios_subject(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_ID="kio7")
    monkeypatch.setattr(m, "simulate_request", lambda session_id=None: {
        "status": "ok", "output": {"tokens_out": 42, "summary": "stub"}, "error": None,
    })
    nc = _FakeNats()

    result = await m.handle_task_envelope(nc, _envelope_json(session_id="sess-1"))

    assert result["status"] == "ok"
    assert len(nc.published) == 1
    subject, data = nc.published[0]
    assert subject == "kio.results.kio7"
    published = json.loads(data)
    assert published["session_id"] == "sess-1"
    assert published["kio_id"] == "kio7"
    assert published["envelope_id"] == "env-1"
    assert published["status"] == "ok"


@pytest.mark.asyncio
async def test_envelope_session_id_is_passed_through_to_simulate_request(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_ID="kio3")
    seen = {}

    def _fake_simulate(session_id=None):
        seen["session_id"] = session_id
        return {"status": "ok", "output": {}, "error": None}

    monkeypatch.setattr(m, "simulate_request", _fake_simulate)
    nc = _FakeNats()

    await m.handle_task_envelope(nc, _envelope_json(session_id="from-envelope"))

    assert seen["session_id"] == "from-envelope"


@pytest.mark.asyncio
async def test_malformed_json_returns_error_without_publishing(fresh_kio_module):
    m = fresh_kio_module()
    nc = _FakeNats()

    result = await m.handle_task_envelope(nc, b"not valid json{{{")

    assert result == {"status": "error", "output": {}, "error": "malformed_envelope"}
    assert nc.published == []


@pytest.mark.asyncio
async def test_error_result_from_simulate_request_still_publishes(fresh_kio_module, monkeypatch):
    m = fresh_kio_module(KIO_ID="kio2-sim")
    monkeypatch.setattr(m, "simulate_request", lambda session_id=None: {
        "status": "error", "output": {}, "error": "timeout",
    })
    nc = _FakeNats()

    result = await m.handle_task_envelope(nc, _envelope_json(kio_id="kio2-sim"))

    assert result["status"] == "error"
    assert result["error"] == "timeout"
    subject, data = nc.published[0]
    published = json.loads(data)
    assert published["status"] == "error"
    assert published["error"] == "timeout"


@pytest.mark.asyncio
async def test_simulate_request_exception_is_caught_and_reported(fresh_kio_module, monkeypatch):
    """handle_task_envelope must not let a raised exception from
    simulate_request() propagate and kill the NATS consumer loop."""
    m = fresh_kio_module(KIO_ID="kio4")

    def _boom(session_id=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(m, "simulate_request", _boom)
    nc = _FakeNats()

    result = await m.handle_task_envelope(nc, _envelope_json(kio_id="kio4"))

    assert result["status"] == "error"
    assert "handler_exception" in result["error"]
    # Still publishes a KIOResult back so the Planner doesn't wait forever.
    assert len(nc.published) == 1
