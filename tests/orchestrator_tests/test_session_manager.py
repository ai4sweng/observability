"""Unit tests for orchestrator.session_manager.SessionManager, backed by an
in-memory SQLite database — the module is deliberately backend-portable (see
its docstring) so tests never need a real Postgres instance."""
import pytest

from orchestrator.session_manager import SessionManager


@pytest.fixture
def sm():
    return SessionManager(dsn="sqlite:///:memory:")


def test_create_session_sets_pending_status(sm):
    sm.create_session("sess-1", kio_id="kio7", task_type="ai-sysdev")

    session = sm.get_session("sess-1")
    assert session is not None
    assert session["kio_id"] == "kio7"
    assert session["task_type"] == "ai-sysdev"
    assert session["status"] == "pending"


def test_get_session_unknown_id_returns_none(sm):
    assert sm.get_session("does-not-exist") is None


def test_mark_running_updates_status(sm):
    sm.create_session("sess-2", kio_id="kio3", task_type="nlp-requirements")
    sm.mark_running("sess-2")

    assert sm.get_session("sess-2")["status"] == "running"


def test_record_lineage_marks_session_completed_on_ok(sm):
    sm.create_session("sess-3", kio_id="kio2-sim", task_type="code-analysis")
    sm.mark_running("sess-3")

    sm.record_lineage("sess-3", kio_id="kio2-sim", envelope_id="env-1",
                       status="ok", output={"tokens_out": 10}, error=None)

    assert sm.get_session("sess-3")["status"] == "completed"
    lineage = sm.get_lineage("sess-3")
    assert len(lineage) == 1
    assert lineage[0]["envelope_id"] == "env-1"
    assert lineage[0]["status"] == "ok"


def test_record_lineage_marks_session_failed_on_error(sm):
    sm.create_session("sess-4", kio_id="kio4", task_type="architecture-to-code")

    sm.record_lineage("sess-4", kio_id="kio4", envelope_id="env-2",
                       status="error", output={}, error="ollama_timeout")

    assert sm.get_session("sess-4")["status"] == "failed"
    lineage = sm.get_lineage("sess-4")
    assert lineage[0]["error"] == "ollama_timeout"


def test_get_lineage_orders_by_recorded_at(sm):
    sm.create_session("sess-5", kio_id="kio7", task_type="ai-sysdev")
    sm.record_lineage("sess-5", kio_id="kio7", envelope_id="env-a", status="ok")
    sm.record_lineage("sess-5", kio_id="kio7", envelope_id="env-b", status="ok")

    lineage = sm.get_lineage("sess-5")
    assert [row["envelope_id"] for row in lineage] == ["env-a", "env-b"]
