"""Shared pytest fixtures for the observability test suite.

kio_simulator.py is a script-style module: importing it has side effects
(creates OTel metric/log/trace exporters and providers, starts background
export threads, initializes a Langfuse client) and its behaviour is partly
driven by environment variables read once, at IMPORT time, into module-level
globals (KIO_ID, TASK_TYPE, KIO_REAL_KPI_ROLE, KIO2_REAL_LLM_ENABLED, ...).

To exercise different KIO_REAL_KPI_ROLE / KIO2_REAL_LLM_ENABLED combinations
in the same pytest process, each test that cares imports a FRESH copy of the
module via the `fresh_kio_module` fixture below, instead of relying on
Python's module cache (which would keep returning the first-imported
config). Re-importing repeatedly is safe: OpenTelemetry's global provider
setters (set_meter_provider / set_tracer_provider / set_logger_provider)
silently keep the first provider on subsequent calls (they just log a
warning), so this never errors — tests don't depend on real OTLP export
anyway, they patch specific instrument objects on the freshly imported
module and assert on those calls directly.
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIO_SIM_DIR = REPO_ROOT / "kio-simulator"

# kio_simulator.py does `from envelope import ...` — a same-directory import
# (not package-relative), exactly as it runs inside the Docker image
# (WORKDIR /app, envelope.py copied alongside it). Its directory must be on
# sys.path for that import to resolve here too.
if str(KIO_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(KIO_SIM_DIR))

# orchestrator/ is a plain top-level package (orchestrator/__init__.py) —
# needs the repo root on sys.path for `from orchestrator import planner` etc.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Env defaults applied to every fresh_kio_module() import unless overridden.
# None of these need to point at anything real — kio_simulator.py's exporters
# are all designed to degrade silently when their endpoint is unreachable
# (see its module docstring), which is exactly what we want in unit tests:
# no real network I/O, no test ever blocked on a live OTel collector.
_DEFAULT_ENV = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    "EXPORT_INTERVAL_MS": "600000",  # keep background exporter threads quiet during the test run
    "HEARTBEAT_INTERVAL_S": "600000",
    "LANGFUSE_PUBLIC_KEY": "",
    "LANGFUSE_SECRET_KEY": "",
    "NATS_ENABLED": "false",
    "KIO2_REAL_LLM_ENABLED": "false",
    "KIO_REAL_KPI_ROLE": "",
    "GPU_POWER_EXPORTER_URL": "",
}


@pytest.fixture
def fresh_kio_module(monkeypatch):
    """Factory fixture: fresh_kio_module(**env_overrides) -> a freshly
    imported kio_simulator module, with the given env vars layered on top of
    _DEFAULT_ENV. Call it once per test (or once per role under test)."""

    def _make(**env_overrides):
        env = {**_DEFAULT_ENV, **env_overrides}
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for mod_name in ("kio_simulator", "envelope"):
            sys.modules.pop(mod_name, None)
        return importlib.import_module("kio_simulator")

    return _make
