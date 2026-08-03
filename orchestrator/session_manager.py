"""Session Manager — registers session starts and lineage history in Postgres
(v2 guide, "Session Initialization" + "Lineage Registration" steps).

Backend-portable on purpose: production (docker-compose) points this at the
dedicated `orchestrator-postgres` service via SESSION_DB_DSN; local dev/tests
can point it at a throwaway SQLite file or `sqlite:///:memory:` with zero code
changes — column types below are kept generic (String/Text, JSON serialized
as text) so both backends work off the same SQLAlchemy Core table definitions.
The real Postgres deployment additionally gets `postgres/init/001-schema.sql`
(UUID/JSONB-typed) applied by the Postgres image at first boot; this module
doesn't depend on those richer types, it just uses whatever table already
exists (or creates a portable one itself for SQLite/tests via `create_all`).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)

SESSION_DB_DSN = os.environ.get("SESSION_DB_DSN", "sqlite:///./sessions.db")

metadata = MetaData()

sessions_table = Table(
    "sessions",
    metadata,
    Column("session_id", String(36), primary_key=True),
    Column("kio_id", String(64), nullable=False),
    Column("task_type", String(128), nullable=False),
    Column("status", String(16), nullable=False, default="pending"),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

lineage_table = Table(
    "lineage",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), nullable=False),
    Column("kio_id", String(64), nullable=False),
    Column("envelope_id", String(36), nullable=False),
    Column("status", String(16), nullable=False),
    Column("output", Text),
    Column("error", Text),
    Column("recorded_at", DateTime, nullable=False),
)


class SessionManager:
    def __init__(self, dsn: Optional[str] = None):
        self.engine = create_engine(dsn or SESSION_DB_DSN, future=True)
        # Portable create-if-missing — a no-op against the real Postgres
        # instance once 001-schema.sql has already created these tables.
        metadata.create_all(self.engine, checkfirst=True)

    def create_session(self, session_id: str, kio_id: str, task_type: str) -> None:
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(
                insert(sessions_table).values(
                    session_id=session_id,
                    kio_id=kio_id,
                    task_type=task_type,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )

    def mark_running(self, session_id: str) -> None:
        self._set_status(session_id, "running")

    def record_lineage(
        self,
        session_id: str,
        kio_id: str,
        envelope_id: str,
        status: str,
        output: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Task Completion / Lineage Registration (v2 guide): the Planner
        calls this once a worker's KIOResult comes back over
        kio.results.<kio_id>."""
        import uuid

        with self.engine.begin() as conn:
            conn.execute(
                insert(lineage_table).values(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    kio_id=kio_id,
                    envelope_id=envelope_id,
                    status=status,
                    output=json.dumps(output or {}),
                    error=error,
                    recorded_at=datetime.now(timezone.utc),
                )
            )
        self._set_status(session_id, "completed" if status == "ok" else "failed")

    def _set_status(self, session_id: str, status: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(sessions_table)
                .where(sessions_table.c.session_id == session_id)
                .values(status=status, updated_at=datetime.now(timezone.utc))
            )

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(sessions_table).where(sessions_table.c.session_id == session_id)
            ).mappings().first()
            return dict(row) if row else None

    def get_lineage(self, session_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(lineage_table)
                .where(lineage_table.c.session_id == session_id)
                .order_by(lineage_table.c.recorded_at)
            ).mappings().all()
            return [dict(r) for r in rows]
