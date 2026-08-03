"""Workflow API (v2 guide, "KIO1 Workflow API").

Entry point a client calls to trigger a task: POST /workflow/run. Registers
the session via the Session Manager, hands off to the Planner to dispatch the
first task envelope over NATS JetStream, and returns 202 + session_id
immediately (matches the guide: "The API returns this ID to the client with
an HTTP 202 Accepted status so the client can track progress").

Run with: uvicorn orchestrator.workflow_api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from orchestrator import planner
from orchestrator.session_manager import SessionManager

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    import nats

    _state["session_manager"] = SessionManager()
    _state["nc"] = await nats.connect(NATS_URL)
    yield
    await _state["nc"].close()


app = FastAPI(title="AI4SWENG Workflow API", lifespan=lifespan)


class RunRequest(BaseModel):
    task_type: str
    payload: dict[str, Any] = {}
    target_kio: Optional[str] = None


class RunResponse(BaseModel):
    session_id: str
    kio_id: str
    status: str = "accepted"


@app.post("/workflow/run", response_model=RunResponse, status_code=202)
async def run_workflow(body: RunRequest) -> RunResponse:
    session_id = str(uuid.uuid4())
    try:
        kio_id = planner.select_target_kio(body.task_type, body.target_kio)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _state["session_manager"].create_session(session_id, kio_id, body.task_type)
    await planner.dispatch(_state["nc"], session_id, body.task_type, body.payload, body.target_kio)
    _state["session_manager"].mark_running(session_id)

    return RunResponse(session_id=session_id, kio_id=kio_id)


@app.get("/workflow/{session_id}")
async def get_workflow(session_id: str) -> dict[str, Any]:
    session = _state["session_manager"].get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    lineage = _state["session_manager"].get_lineage(session_id)
    return {"session": session, "lineage": lineage}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
