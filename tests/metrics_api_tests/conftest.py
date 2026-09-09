"""Fixtures for the metrics API tests.

No VictoriaMetrics, no Docker, no network. The client is replaced with a fake
that records the queries it was asked to run and replays canned responses, so
tests can assert both the generated PromQL and the shape of the JSON built
from a given upstream answer.
"""
import pytest
from fastapi.testclient import TestClient

from metrics_api import app as app_module
from metrics_api.victoriametrics import Sample, Series


def vector(value, **labels):
    """One instant-query series."""
    return Series(labels=labels, samples=(Sample(timestamp=1_757_000_000.0, value=value),))


def matrix(values, start=1_757_000_000.0, step=60.0, **labels):
    """One range-query series from a list of values."""
    return Series(
        labels=labels,
        samples=tuple(
            Sample(timestamp=start + i * step, value=v) for i, v in enumerate(values)
        ),
    )


class FakeVictoriaMetrics:
    """Stands in for VictoriaMetricsClient.

    `instant` / `range_` hold what the next call returns; `raises` makes the
    next call fail, for exercising the 502 and upstream-400 paths.
    """

    def __init__(self):
        self.instant: list[Series] = []
        self.range_: list[Series] = []
        self.raises: Exception | None = None
        self.queries: list[str] = []
        self.range_calls: list[dict] = []

    def query(self, expr):
        self.queries.append(expr)
        if self.raises:
            raise self.raises
        return self.instant

    def query_range(self, expr, start, end, step):
        self.queries.append(expr)
        self.range_calls.append({"query": expr, "start": start, "end": end, "step": step})
        if self.raises:
            raise self.raises
        return self.range_

    @property
    def last_query(self) -> str:
        assert self.queries, "no query was issued"
        return self.queries[-1]


@pytest.fixture
def fake_vm(monkeypatch):
    fake = FakeVictoriaMetrics()
    monkeypatch.setattr(app_module, "client", lambda: fake)
    return fake


@pytest.fixture
def api():
    return TestClient(app_module.app, raise_server_exceptions=False)
