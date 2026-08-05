"""Unit tests for orchestrator.planner's routing table and dispatch()."""
import json

import pytest

from orchestrator import planner


@pytest.mark.parametrize("task_type,expected_kio", [
    ("code-analysis", "kio2-sim"),
    ("nlp-requirements", "kio3"),          # D1.1: KIO3 = NLP -> Formal Requirements
    ("architecture-to-code", "kio4"),      # D1.1: KIO4 = Architecture-to-Code Planner
    ("ai-sysdev", "kio7"),                 # D1.1: KIO7 = AI-SysDev
])
def test_select_target_kio_known_task_types(task_type, expected_kio):
    assert planner.select_target_kio(task_type) == expected_kio


def test_select_target_kio_explicit_override_wins():
    assert planner.select_target_kio("code-analysis", target_kio="kio99") == "kio99"


def test_select_target_kio_unknown_task_type_raises():
    with pytest.raises(ValueError, match="No routing rule"):
        planner.select_target_kio("does-not-exist")


class _FakeNats:
    def __init__(self):
        self.published = []

    async def publish(self, subject, data):
        self.published.append((subject, data))


@pytest.mark.asyncio
async def test_dispatch_publishes_envelope_to_resolved_kio():
    nc = _FakeNats()

    kio_id = await planner.dispatch(nc, session_id="sess-1", task_type="ai-sysdev", payload={"x": 1})

    assert kio_id == "kio7"
    assert len(nc.published) == 1
    subject, data = nc.published[0]
    assert subject == "kio.tasks.kio7"
    envelope = json.loads(data)
    assert envelope["session_id"] == "sess-1"
    assert envelope["kio_id"] == "kio7"
    assert envelope["task_type"] == "ai-sysdev"
    assert envelope["payload"] == {"x": 1}


@pytest.mark.asyncio
async def test_dispatch_respects_explicit_target_kio():
    nc = _FakeNats()

    kio_id = await planner.dispatch(
        nc, session_id="sess-2", task_type="code-analysis", payload={}, target_kio="kio-custom"
    )

    assert kio_id == "kio-custom"
    subject, _ = nc.published[0]
    assert subject == "kio.tasks.kio-custom"
