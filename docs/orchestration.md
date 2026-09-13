# Orchestration Layer (NATS JetStream) — How KIOs Get Triggered

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

> **⚠️ Disabled by default — this belongs to KIO1, not the observability platform.**
> Architecturally the Workflow API + Planner (the "KIO1 orchestrator" in the v2
> guideline) are **KIO1's** responsibility; here they were only a *reference / demo*
> implementation to drive the simulators end-to-end. To keep this repository
> observability-only, the `nats`, `orchestrator-postgres`, `workflow-api` and
> `planner` services are **commented out in `docker-compose.yml`**, and the KIOs'
> `NATS_ENABLED` / `NATS_URL` / `depends_on` lines are neutralized so the stack
> stays valid without them. The `orchestrator/` code stays as a reference. Re-enable
> only if you deliberately want the dispatch demo (uncomment those services and
> restore the KIO NATS lines). The section below describes it as originally built.

Per the v2 guideline's architecture (Workflow API → Session Manager → Planner →
NATS JetStream → worker KIO → NATS → Planner → Session Manager lineage), this is now
implemented — see `orchestrator/`. This is a **separate concern from observability**:
it's about how a task gets *dispatched* to a KIO, not how that KIO reports telemetry.
A worker KIO's OTel/Langfuse instrumentation is identical either way.

```
POST /workflow/run  ──►  workflow-api  ──► Session Manager (Postgres: sessions)
                              │                      ▲
                              ▼                      │ lineage
                          Planner ──► NATS JetStream ──► worker KIO (kio3/kio4)
                              ▲                              │
                              └──────── kio.results.* ◄──────┘
```

- **`orchestrator/envelope.py`** — `KIOEnvelope` (task_type, session_id, kio_id, payload)
  and `KIOResult` (status, output, error). Vendored, byte-for-byte, into
  `kio-simulator/envelope.py` too, so kio-simulator's Docker build context doesn't need
  to change (see comment in that file for why).
- **`orchestrator/session_manager.py`** — registers sessions and records lineage in
  Postgres (`orchestrator-postgres`, schema in `orchestrator/postgres/init/001-schema.sql`).
  Portable to SQLite for local testing (`SESSION_DB_DSN=sqlite:///...`) — no code change.
- **`orchestrator/workflow_api.py`** — `POST /workflow/run` (`{"task_type", "payload",
  "target_kio"?}`) registers the session and hands off to the Planner, returns 202 +
  `session_id` immediately. `GET /workflow/{session_id}` shows status + lineage.
- **`orchestrator/planner.py`** — routes `task_type` → `kio_id` (a static table:
  `code-analysis→kio2-sim`, `nlp-requirements→kio3`, `architecture-to-code→kio4`,
  `ai-sysdev→kio7`, or an explicit `target_kio` override), builds the envelope, publishes
  to `kio.tasks.<kio_id>`. Also runs as its own long-running container, subscribed to
  `kio.results.*`, registering lineage as workers reply.
- **kio-simulator's NATS consumer** (`NATS_ENABLED`, **disabled by default now**) —
  subscribes to its own `kio.tasks.<KIO_ID>`, runs the exact same `simulate_request()`
  used in internal-timer mode (just fed the envelope's `session_id` instead of
  generating its own), publishes a `KIOResult` back to `kio.results.<KIO_ID>`.

**Scope, stated plainly:** the "Planner & Prompt Router" here is a static routing table,
not a real multi-step LangGraph workflow graph (conditional branching, multi-KIO
pipelines, retries) — that's real future work, this just gives every envelope a
genuine destination and closes the loop honestly. Tested at the logic level (a fake
pub/sub double standing in for NATS, SQLite standing in for Postgres — no Docker in the
dev sandbox this was built in); the real NATS wire protocol and the real Postgres schema
still want one live `docker compose up` verification pass on an actual machine.

Try it (uncomment the `nats` / `orchestrator-postgres` / `workflow-api` / `planner`
services in `docker-compose.yml` first — they're disabled by default):
```bash
curl -X POST http://localhost:8080/workflow/run \
  -H "Content-Type: application/json" \
  -d '{"task_type": "architecture-to-code"}'
# -> 202 {"session_id": "...", "kio_id": "kio4", "status": "accepted"}
curl http://localhost:8080/workflow/{session_id}
# -> {"session": {...}, "lineage": [...]}
```

## Orchestration verification

Instead of re-running the `curl` example above by hand every time, there's a
script that verifies the whole chain in one pass (`POST /workflow/run` → NATS
JetStream → kio3's NATS consumer → `kio.results.kio3` → the Planner's
`run_result_listener` → Postgres lineage), with no extra packages to install
(stdlib-only). Uncomment the `nats` / `orchestrator-postgres` / `workflow-api` /
`planner` services in `docker-compose.yml` first — they're disabled by default:

```bash
docker compose up -d nats orchestrator-postgres workflow-api planner kio3
python scripts/verify_orchestration.py
```

Exits with `PASS`/`FAIL`; on `FAIL` it prints exactly which hop broke (Workflow
API unreachable at all / lineage never arrives / etc.) and which container's
logs to check. This step has never been run against a real NATS/Postgres,
since the sandbox this repo was developed in has no Docker — running this
script closes the last open verification item flagged in
[Design Decisions & v2 Guideline Evaluation](design-decisions.md).

**This is a completely separate concern from KIO telemetry.** Complying with
the Integration Contract (sending telemetry, see
[Connecting a Remote KIO](remote-connectivity.md)) is mandatory; registering
with the Workflow API/Planner/NATS described here is **optional** — only
needed if a KIO wants to be triggerable by `POST /workflow/run`.
