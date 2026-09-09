"""FastAPI application: GET /api/metrics, GET /api/metrics/catalog.

Read-only and stateless — no database of its own, no ingest path. Runs with:

    uvicorn metrics_api.app:app --host 0.0.0.0 --port 8081

Unauthenticated by deliberate decision — the data is simulated test telemetry
and the rest of the read plane is open too; see the README.

Endpoint handlers are sync `def`, not `async def`: the VictoriaMetrics client
is the blocking `requests` library, so FastAPI runs these in its threadpool
rather than stalling the event loop.
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from metrics_api import query as querybuilder
from metrics_api import registry, stats, timefmt
from metrics_api.victoriametrics import (
    UpstreamQueryError,
    UpstreamUnavailable,
    VictoriaMetricsClient,
)

# Query-string keys that are parameters rather than label filters. Anything
# else is treated as a label filter and validated against the registry, so a
# typo becomes a 400 instead of a silently ignored filter.
RESERVED_PARAMS = {
    "metric",
    "period",
    "start",
    "end",
    "step",
    "window",
    "aggregate",
    "format",
    "group_by",
    "tz",
    "align",
}

VALID_FORMATS = ("auto", "snapshot", "summary", "series")

# Bound the fan-out: `metric` is required, but a caller could still ask for
# every metric at once and get 33 range queries in one request.
MAX_METRICS_PER_REQUEST = int(os.environ.get("MAX_METRICS_PER_REQUEST", "10"))

app = FastAPI(
    title="AI4SWENG Metrics Query API",
    description=(
        "Parameterized read access to KIO operational telemetry. "
        "D1.1 project KPIs are served separately by /api/kpis."
    ),
    version="1.0.0",
)

_client: VictoriaMetricsClient | None = None


def client() -> VictoriaMetricsClient:
    """Lazily built so tests can substitute one via app.dependency_overrides
    or by assigning to the module global."""
    global _client
    if _client is None:
        _client = VictoriaMetricsClient()
    return _client


@app.exception_handler(registry.UnknownMetric)
@app.exception_handler(registry.AmbiguousMetric)
@app.exception_handler(registry.InvalidLabel)
@app.exception_handler(querybuilder.InvalidQuery)
@app.exception_handler(timefmt.InvalidTime)
def _bad_request(request: Request, exc: Exception) -> JSONResponse:
    """Every caller-input error is a 400 carrying what was wrong.

    Deliberately never an empty 200: an empty success is indistinguishable
    from a healthy pipeline with no data, which is the confusion this API is
    built to remove.
    """
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(UpstreamUnavailable)
def _bad_gateway(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.exception_handler(UpstreamQueryError)
def _upstream_rejected(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "metrics_in_registry": len(registry.ALL)}


@app.get("/api/metrics/catalog")
def catalog(family: str = Query("operational", pattern="^(operational|kpi|all)$")) -> dict:
    """Every queryable metric, with what a caller needs to query it.

    Defaults to the operational family — the KPI family is served with its
    D1.1 metadata by /api/kpis/catalog.
    """
    if family == "all":
        entries = registry.operational() + registry.kpis()
    else:
        entries = registry.by_family(family)
    return {
        "family": family,
        "count": len(entries),
        "metrics": [
            {
                "otel_name": m.otel_name,
                "prom_name": m.prom_name,
                "instrument": m.instrument,
                "unit": m.unit,
                "family": m.family,
                "valid_labels": list(m.labels),
                "required_group_by": list(m.required_group_by),
                "default_aggregate": querybuilder.default_aggregate(m),
                "optional": m.optional,
                "requires_task_type": list(m.requires_task_type),
                **({"roles": list(m.roles)} if m.roles else {}),
            }
            for m in entries
        ],
    }


@app.get("/api/metrics")
def get_metrics(
    request: Request,
    metric: str = Query(..., description="Metric name(s), comma-separated"),
    period: str = Query("5m"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    step: str = Query("auto"),
    window: str | None = Query(
        None,
        description=(
            "Rate lookback per point. Defaults to `period` for a snapshot and to "
            "4x`step` for series/summary — see the note in query.build()."
        ),
    ),
    aggregate: str | None = Query(None),
    format: str = Query("auto"),
    group_by: str | None = Query(None),
    tz: str = Query(
        "UTC",
        description="IANA timezone deciding where bucket boundaries fall, e.g. Europe/Istanbul",
    ),
    align: str = Query(
        "auto",
        description=(
            "Snap range points to calendar boundaries. auto = yes for steps of "
            "an hour or more, no below that. calendar = always, none = never."
        ),
    ),
) -> dict:
    if format not in VALID_FORMATS:
        raise querybuilder.InvalidQuery(
            f"unknown format '{format}'; valid: {', '.join(VALID_FORMATS)}"
        )
    if (start or end) and period != "5m":
        raise querybuilder.InvalidQuery(
            "period is mutually exclusive with start/end — pass one or the other"
        )
    if bool(start) != bool(end):
        raise querybuilder.InvalidQuery("start and end must be given together")

    metrics = registry.resolve_many(metric)
    if len(metrics) > MAX_METRICS_PER_REQUEST:
        raise querybuilder.InvalidQuery(
            f"requested {len(metrics)} metrics; the limit is {MAX_METRICS_PER_REQUEST} "
            "per request"
        )

    filters = _label_filters(request)
    groupings = [g.strip() for g in (group_by or "").split(",") if g.strip()]

    absolute = bool(start and end)
    resolved_format = _resolve_format(format, absolute)
    effective_period = period

    if resolved_format == "snapshot":
        # An instant query's lookback IS the period: one number over one window.
        return _snapshot(
            metrics, filters, effective_period, window, aggregate, groupings
        )

    resolved_step = (
        querybuilder.auto_step(effective_period) if step == "auto" else
        querybuilder.validate_duration(step, "step")
    )
    range_args = _resolve_range(
        start, end, absolute, effective_period, resolved_step, tz, align
    )
    # Sized off the step, not the period: a period-wide rate window would
    # smooth every point of the range into the same value.
    resolved_window = window or querybuilder.auto_window(resolved_step)
    return _range(
        metrics,
        filters,
        effective_period,
        resolved_window,
        aggregate,
        groupings,
        range_args,
        resolved_step,
        tz,
        as_series=(resolved_format == "series"),
    )


def _resolve_range(
    start: str | None,
    end: str | None,
    absolute: bool,
    period: str,
    step: str,
    tz_name: str,
    align: str,
) -> tuple[str, str]:
    """The (start, end) pair handed to VictoriaMetrics.

    Without alignment these stay in relative form (`-24h`, `now`), which is
    what VictoriaMetrics prefers and needs no clock arithmetic here. With
    alignment they must become absolute, because snapping a boundary means
    knowing the actual instant: VictoriaMetrics places points at `start`,
    `start + step`, ..., so aligning `start` aligns the whole series.
    """
    step_seconds = querybuilder.duration_seconds(step)
    if not timefmt.should_align(step_seconds, align):
        if absolute:
            return start, end
        return f"-{period}", "now"

    tz = timefmt.zone(tz_name)
    if absolute:
        start_unix = timefmt.parse_instant(start, "start")
        end_unix = timefmt.parse_instant(end, "end")
    else:
        end_unix = time.time()
        start_unix = end_unix - querybuilder.duration_seconds(period)
    aligned_start = timefmt.floor_to_step(start_unix, step_seconds, tz)
    return f"{aligned_start:.0f}", f"{end_unix:.0f}"


def _resolve_format(requested: str, absolute_range: bool) -> str:
    """`auto` means snapshot for a bare request, summary once a time range is
    given — asking for a window implies wanting to know about the window."""
    if requested != "auto":
        return requested
    return "summary" if absolute_range else "snapshot"


def _label_filters(request: Request) -> dict[str, str]:
    """Every non-reserved query parameter, treated as a label filter.

    Unknown parameters are NOT silently dropped — they reach the registry's
    label validation and come back as a 400. A quietly ignored filter is the
    worst failure mode available here: the caller gets real-looking numbers
    for a query they did not ask.
    """
    return {
        key: ",".join(request.query_params.getlist(key))
        for key in request.query_params
        if key not in RESERVED_PARAMS
    }


def _describe(metric: registry.Metric, aggregate: str | None) -> dict[str, Any]:
    return {
        "metric": metric.otel_name,
        "instrument": metric.instrument,
        "unit": metric.unit,
        "aggregate": (aggregate or querybuilder.default_aggregate(metric)).lower(),
    }


def _no_data_reason(metric: registry.Metric, filters: dict[str, str]) -> str:
    """Why an empty result is empty — expected absence or a possible fault.

    Conditionally-emitted metrics (the repo.* gauges, every D1.1 KPI) are
    simply not reported by most KIOs, and saying so is more useful than a bare
    empty list.
    """
    if metric.requires_task_type:
        return (
            f"{metric.otel_name} is only reported by KIOs with task_type "
            f"{' or '.join(metric.requires_task_type)}"
        )
    if metric.roles:
        return (
            f"{metric.otel_name} is only reported by KIOs with "
            f"KIO_REAL_KPI_ROLE={' or '.join(metric.roles)}"
        )
    return (
        "no series matched — check the filters, or that a KIO is running and "
        "exporting to the collector"
    )


def _snapshot(
    metrics: list[registry.Metric],
    filters: dict[str, str],
    period: str,
    window: str | None,
    aggregate: str | None,
    groupings: list[str],
) -> dict:
    results = []
    for metric in metrics:
        expr = querybuilder.build(
            metric,
            filters=filters,
            period=period,
            window=window,
            aggregate=aggregate,
            group_by=groupings,
        )
        series = client().query(expr)
        entry: dict[str, Any] = {**_describe(metric, aggregate), "query": expr}
        if not series:
            entry["value"] = None
            entry["no_data_reason"] = _no_data_reason(metric, filters)
        elif groupings:
            entry["groups"] = [
                {"labels": s.labels, "value": s.latest} for s in series
            ]
        else:
            entry["value"] = series[0].latest
        results.append(entry)
    return {
        "format": "snapshot",
        "period": period,
        "window": window or period,
        "results": results,
    }


def _range(
    metrics: list[registry.Metric],
    filters: dict[str, str],
    period: str,
    window: str,
    aggregate: str | None,
    groupings: list[str],
    range_args: tuple[str, str],
    step: str,
    tz_name: str,
    *,
    as_series: bool,
) -> dict:
    start, end = range_args
    results = []
    for metric in metrics:
        expr = querybuilder.build(
            metric,
            filters=filters,
            period=period,
            window=window,
            aggregate=aggregate,
            group_by=groupings,
        )
        series = client().query_range(expr, start=start, end=end, step=step)
        entry: dict[str, Any] = {**_describe(metric, aggregate), "query": expr}
        if not series:
            entry["no_data_reason"] = _no_data_reason(metric, filters)
            entry["series" if as_series else "analysis"] = None
        elif as_series:
            entry["series"] = [
                {
                    "labels": s.labels,
                    "points": [
                        {
                            # ISO-8601 for a human reading the response,
                            # raw Unix alongside it for a machine.
                            "timestamp": timefmt.iso8601(sample.timestamp),
                            "unix": sample.timestamp,
                            "value": sample.value,
                        }
                        for sample in s.samples
                    ],
                }
                for s in series
            ]
        elif groupings:
            entry["groups"] = [
                {"labels": s.labels, "analysis": stats.summarize(s.values).as_dict()}
                for s in series
            ]
        else:
            entry["analysis"] = stats.summarize(series[0].values).as_dict()
        results.append(entry)

    envelope: dict[str, Any] = {
        "format": "series" if as_series else "summary",
        "start": start,
        "end": end,
        "step": step,
        "window": window,
        "timezone": tz_name,
        "results": results,
    }
    # `period` is only meaningful for a relative window; an explicit
    # start/end pair speaks for itself.
    if start == f"-{period}" and end == "now":
        envelope["period"] = period
    return envelope
