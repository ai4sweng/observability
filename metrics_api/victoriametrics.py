"""VictoriaMetrics client.

Thin on purpose: VictoriaMetrics speaks the Prometheus query API, so there is
nothing to abstract beyond error classification and turning the response
envelope into plain Python.

Error classification is the part that matters. Three outcomes that look alike
in a naive client have to stay distinguishable, because two are faults and one
is a legitimate answer:

  * VictoriaMetrics unreachable        -> UpstreamUnavailable -> HTTP 502
  * VictoriaMetrics rejected the query -> UpstreamQueryError   -> HTTP 400
  * query ran, matched no series       -> empty result         -> HTTP 200

Collapsing the third into the first two (or vice versa) is how "the telemetry
pipeline is broken" gets reported as "the value is zero".
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

DEFAULT_BASE_URL = os.environ.get(
    "VICTORIAMETRICS_URL", "http://victoriametrics:8428"
)
DEFAULT_TIMEOUT = float(os.environ.get("VICTORIAMETRICS_TIMEOUT_S", "10"))


class UpstreamUnavailable(RuntimeError):
    """VictoriaMetrics could not be reached at all."""


class UpstreamQueryError(ValueError):
    """VictoriaMetrics was reached but rejected the query.

    Almost always a query the API generated from bad caller input, so this
    surfaces as a 400 rather than a 502.
    """


@dataclass(frozen=True)
class Sample:
    """One (timestamp, value) point. `value` is None for a NaN sample, which
    VictoriaMetrics returns as the string "NaN"."""

    timestamp: float
    value: float | None


@dataclass(frozen=True)
class Series:
    """One returned time series: its labels, plus its samples."""

    labels: dict[str, str]
    samples: tuple[Sample, ...]

    @property
    def latest(self) -> float | None:
        for sample in reversed(self.samples):
            if sample.value is not None:
                return sample.value
        return None

    @property
    def values(self) -> list[float]:
        return [s.value for s in self.samples if s.value is not None]


def _to_float(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # NaN/Inf are not JSON-representable and mean "no value here" anyway.
    return value if value == value and value not in (float("inf"), float("-inf")) else None


class VictoriaMetricsClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self._session = requests.Session()

    def query(self, expr: str) -> list[Series]:
        """Instant query — the value now."""
        payload = self._get("/api/v1/query", {"query": expr})
        return _parse(payload, vector=True)

    def query_range(self, expr: str, start: str, end: str, step: str) -> list[Series]:
        """Range query. `start`/`end` accept VictoriaMetrics' relative forms
        (`-24h`, `now`) as well as absolute timestamps, so no date arithmetic
        is needed here."""
        payload = self._get(
            "/api/v1/query_range",
            {"query": expr, "start": start, "end": end, "step": step},
        )
        return _parse(payload, vector=False)

    def _get(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = self._session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise UpstreamUnavailable(
                f"could not reach VictoriaMetrics at {self.base_url}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise UpstreamUnavailable(
                f"VictoriaMetrics returned {response.status_code} for {path}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamUnavailable(
                f"VictoriaMetrics returned a non-JSON body for {path}"
            ) from exc

        if payload.get("status") == "error" or response.status_code >= 400:
            raise UpstreamQueryError(
                payload.get("error") or f"VictoriaMetrics rejected the query ({response.status_code})"
            )
        return payload


def _parse(payload: dict, *, vector: bool) -> list[Series]:
    result = (payload.get("data") or {}).get("result") or []
    series: list[Series] = []
    for item in result:
        labels = {k: v for k, v in (item.get("metric") or {}).items() if k != "__name__"}
        if vector:
            raw = [item.get("value")] if item.get("value") else []
        else:
            raw = item.get("values") or []
        samples = tuple(
            Sample(timestamp=float(point[0]), value=_to_float(point[1]))
            for point in raw
            if point
        )
        series.append(Series(labels=labels, samples=samples))
    return series
