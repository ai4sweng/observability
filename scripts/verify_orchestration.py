#!/usr/bin/env python3
"""End-to-end smoke test for the NATS/Postgres orchestration layer
(Workflow API -> Planner -> NATS JetStream -> worker KIO -> Planner's result
listener -> Session Manager -> Postgres lineage).

This is the "live docker compose up pass against the real NATS/Postgres"
verification step flagged as still-open in README.md's "V2 Guideline
Evaluation" section — it was only ever tested at the logic level (fake
pub/sub + SQLite) in the sandbox this repo was built in, which has no Docker.
Run this against a real `docker compose up -d` stack to close that gap.

Usage:
    python scripts/verify_orchestration.py
    python scripts/verify_orchestration.py --base-url http://localhost:8080 --timeout 30

Exit code 0 = the full round trip worked (session created, task dispatched,
worker replied, lineage recorded). Non-zero = something in the chain is
broken, with a message pointing at which hop failed.

No third-party dependencies (stdlib only), so it can run without installing
anything into the orchestrator's venv first.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _http(method: str, url: str, body: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, {"raw": payload.decode(errors="replace")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8080", help="Workflow API base URL")
    ap.add_argument("--task-type", default="nlp-requirements",
                     help="Routed by the Planner's static table to a worker KIO (default -> kio3)")
    ap.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for lineage to appear")
    ap.add_argument("--poll-interval", type=float, default=1.5, help="Seconds between GET /workflow/{id} polls")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")

    print(f"[1/4] GET {base}/healthz ...")
    try:
        status, body = _http("GET", f"{base}/healthz", timeout=5)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"FAIL: Workflow API unreachable at {base} ({exc}). "
              f"Is `docker compose up -d workflow-api nats orchestrator-postgres` running? "
              f"Check WORKFLOW_API_PORT in .env if you remapped the host port.")
        return 1
    if status != 200:
        print(f"FAIL: /healthz returned HTTP {status}: {body}")
        return 1
    print("      ok.")

    print(f"[2/4] POST {base}/workflow/run  (task_type={args.task_type!r}) ...")
    status, body = _http("POST", f"{base}/workflow/run", {"task_type": args.task_type})
    if status != 202:
        print(f"FAIL: expected HTTP 202, got {status}: {body}")
        if status == 400:
            print("      Hint: unknown task_type. Known routes (orchestrator/planner.py "
                  "DEFAULT_ROUTING_TABLE): code-analysis, nlp-requirements, "
                  "architecture-to-code, ai-sysdev.")
        return 1
    session_id = body["session_id"]
    kio_id = body["kio_id"]
    print(f"      ok. session_id={session_id} routed to kio_id={kio_id}")

    print(f"[3/4] NATS dispatch: envelope should already be on kio.tasks.{kio_id} — "
          f"confirming {kio_id} is the container that will consume it "
          f"(NATS_ENABLED=true expected on kio3/kio4 in docker-compose.yml) ...")

    print(f"[4/4] Polling GET {base}/workflow/{session_id} for up to {args.timeout:.0f}s "
          f"until {kio_id} has published a KIOResult and the Planner's result "
          f"listener has recorded lineage in Postgres ...")
    deadline = time.monotonic() + args.timeout
    lineage = []
    session_status = None
    while time.monotonic() < deadline:
        status, body = _http("GET", f"{base}/workflow/{session_id}")
        if status != 200:
            print(f"FAIL: GET /workflow/{session_id} returned HTTP {status}: {body}")
            return 1
        session_status = body["session"]["status"]
        lineage = body["lineage"]
        if lineage:
            break
        time.sleep(args.poll_interval)

    if not lineage:
        print(f"FAIL: no lineage recorded within {args.timeout:.0f}s (session status stuck at "
              f"{session_status!r}). Likely causes:\n"
              f"  - {kio_id} isn't running, or NATS_ENABLED isn't set on it (check docker-compose.yml)\n"
              f"  - the `planner` container (run_result_listener) isn't up or can't reach NATS/Postgres\n"
              f"  - `docker compose logs {kio_id} planner nats orchestrator-postgres` for the real error")
        return 1

    print(f"      ok. session status={session_status!r}, lineage rows={len(lineage)}")
    print(json.dumps(lineage[-1], indent=2, default=str))
    print("\nPASS: full round trip verified — Workflow API -> Planner -> NATS JetStream -> "
          f"{kio_id} -> kio.results.{kio_id} -> Planner's result listener -> Postgres lineage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
