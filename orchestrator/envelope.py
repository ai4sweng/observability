"""KIOEnvelope — the standardized message wrapper published over NATS JetStream.

Matches the v2 Observability Integration Guide's description: every task
dispatched to a worker KIO is wrapped in an envelope carrying the active
session_id and the target kio_id, so the worker can bind its telemetry
(OTel resource attributes + Langfuse trace metadata) to the same session
the Workflow API/Session Manager are tracking.

Kept dependency-light (stdlib dataclasses + json) so both the orchestrator
services and kio-simulator can import this one file without pulling in a
heavier schema library.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


ENVELOPE_SCHEMA_VERSION = "1.0"


@dataclass
class KIOEnvelope:
    session_id: str
    kio_id: str
    task_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = ENVELOPE_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "KIOEnvelope":
        data = json.loads(raw)
        return KIOEnvelope(
            session_id=data["session_id"],
            kio_id=data["kio_id"],
            task_type=data["task_type"],
            payload=data.get("payload", {}),
            envelope_id=data.get("envelope_id", str(uuid.uuid4())),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            schema_version=data.get("schema_version", ENVELOPE_SCHEMA_VERSION),
        )


@dataclass
class KIOResult:
    """What a worker publishes back to kio.results.<kio_id> when it finishes
    a task — the Planner reads this to register lineage via the Session
    Manager (Task Completion / Lineage Registration steps in the v2 guide)."""

    session_id: str
    kio_id: str
    envelope_id: str
    status: str  # "ok" | "error"
    output: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "KIOResult":
        data = json.loads(raw)
        return KIOResult(
            session_id=data["session_id"],
            kio_id=data["kio_id"],
            envelope_id=data["envelope_id"],
            status=data["status"],
            output=data.get("output", {}),
            error=data.get("error"),
            completed_at=data.get("completed_at", datetime.now(timezone.utc).isoformat()),
        )


def task_subject(kio_id: str) -> str:
    """NATS subject a worker KIO subscribes to for incoming task envelopes."""
    return f"kio.tasks.{kio_id}"


def result_subject(kio_id: str) -> str:
    """NATS subject a worker KIO publishes its KIOResult to when done."""
    return f"kio.results.{kio_id}"


RESULTS_WILDCARD = "kio.results.*"
