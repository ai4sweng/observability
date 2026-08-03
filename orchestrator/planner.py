"""Planner & Prompt Router (v2 guide, "Task Routing" + "Lineage Registration").

Two responsibilities, kept in one module because they share the routing table
and envelope helpers, but they run in two different places:

1. `dispatch()` — build a KIOEnvelope for a task and publish it to
   `kio.tasks.<kio_id>`. Called in-process by workflow_api.py right after it
   registers the session — v2 describes this as the Planner "taking over"
   from the Workflow API; here that hand-off is a plain function call rather
   than a second network hop, which is a deliberate simplification for a
   single-node demo (documented in kio2-integration and the main README).

2. `run_result_listener()` — a standalone, long-running loop (its own
   container: the `planner` service in docker-compose) that subscribes to
   `kio.results.*`, and for every KIOResult a worker publishes back, calls
   the Session Manager to register lineage. This part genuinely is
   decoupled from any single HTTP request's lifecycle, so it stays a
   separate process.

Routing is deliberately simple — a static task_type -> kio_id table, not a
real multi-step workflow graph / LangGraph. Building an actual graph-based
planner (conditional branching, multi-KIO pipelines, retries) is future work;
this gives every envelope a real destination and is honest about not being
more than that.
"""
from __future__ import annotations

import asyncio
import logging
import os

from orchestrator.envelope import KIOEnvelope, KIOResult, RESULTS_WILDCARD, task_subject
from orchestrator.session_manager import SessionManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("planner")

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")

# task_type -> kio_id. Matches the task_type each kio-simulator already
# declares via KIO_TASK_TYPE in docker-compose.yml. Override per-request by
# passing an explicit "target_kio" in the workflow payload.
DEFAULT_ROUTING_TABLE = {
    "code-analysis": "kio2-sim",
    "test-generation": "kio3",
    "debug": "kio4",
}


def select_target_kio(task_type: str, target_kio: str | None = None) -> str:
    if target_kio:
        return target_kio
    kio_id = DEFAULT_ROUTING_TABLE.get(task_type)
    if not kio_id:
        raise ValueError(
            f"No routing rule for task_type={task_type!r} and no explicit "
            f"target_kio given. Known task_types: {sorted(DEFAULT_ROUTING_TABLE)}"
        )
    return kio_id


async def dispatch(nc, session_id: str, task_type: str, payload: dict, target_kio: str | None = None) -> str:
    """Builds the envelope and publishes it. Returns the resolved kio_id."""
    kio_id = select_target_kio(task_type, target_kio)
    envelope = KIOEnvelope(session_id=session_id, kio_id=kio_id, task_type=task_type, payload=payload)
    subject = task_subject(kio_id)
    await nc.publish(subject, envelope.to_json().encode())
    logger.info(f"Dispatched envelope {envelope.envelope_id} (session={session_id}) -> {subject}")
    return kio_id


async def run_result_listener(nc, session_manager: SessionManager) -> None:
    """Long-running: subscribe to every KIO's result subject and register
    lineage as replies come in. Runs until the process is stopped."""

    async def _on_result(msg):
        try:
            result = KIOResult.from_json(msg.data)
        except Exception:
            logger.warning("Malformed KIOResult on %s, dropping", msg.subject, exc_info=True)
            return
        session_manager.record_lineage(
            session_id=result.session_id,
            kio_id=result.kio_id,
            envelope_id=result.envelope_id,
            status=result.status,
            output=result.output,
            error=result.error,
        )
        logger.info(
            f"Lineage recorded: session={result.session_id} kio={result.kio_id} "
            f"status={result.status}"
        )

    await nc.subscribe(RESULTS_WILDCARD, cb=_on_result)
    logger.info(f"Planner listening on {RESULTS_WILDCARD}")
    while True:
        await asyncio.sleep(3600)


async def _main() -> None:
    import nats  # local import: keep this file importable (for dispatch())
    # without nats-py installed, e.g. inside workflow_api's test suite.

    session_manager = SessionManager()
    nc = await nats.connect(NATS_URL)
    try:
        await run_result_listener(nc, session_manager)
    finally:
        await nc.close()


if __name__ == "__main__":
    asyncio.run(_main())
