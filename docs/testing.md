# Tests (pytest)

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

There's a permanent pytest suite under `tests/` covering
`kio-simulator/kio_simulator.py` (D1.1 KPI emission for every role, the 3
fallback tiers of the real GPU temperature reading, the NATS task handler,
`simulate_request()`'s simulated/real-success/real-failure branches) and `metrics_api/` (the generated registry and its
drift detection, PromQL generation per instrument type, the summary
statistics, VictoriaMetrics error classification, and every endpoint against
a fake metrics store):

```bash
uv venv
uv pip install -r kio-simulator/requirements.txt \
               -r metrics_api/requirements.txt \
               -r tests/requirements-test.txt
uv run pytest
```

No real OTel collector/NATS/Postgres/VictoriaMetrics is required — every
external dependency is faked (the OTLP exporters silently fall back when the
endpoint is unreachable, NATS via a fake `nc`, Postgres via
`sqlite:///:memory:`, and VictoriaMetrics via a fake client that records the
queries it was asked to run).
