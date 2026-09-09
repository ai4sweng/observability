"""PromQL/MetricsQL generation.

The caller supplies a metric name and filters; this module decides the query
SHAPE from the metric's instrument type. That decision is the reason the API
exists: `avg` means `rate(_sum)/rate(_count)` on a histogram, a plain value on
a gauge, and nothing coherent at all on a monotonic counter (where the useful
question is a rate or an increase).

Two notes on quantiles, because they are genuinely two different numbers:

  * `aggregate=p95` on a histogram uses `histogram_quantile` over `_bucket`.
    That is the p95 of the underlying observation distribution — the real
    "95th-percentile request took this long".
  * The `p95` inside a `format=summary` block is the 95th percentile of the
    period's SAMPLES of the snapshot expression (see stats.py). It answers
    "how bad did the average get during this window", which is a different
    question. Both are useful; they are not interchangeable, so they are
    reached by different parameters.
"""
from __future__ import annotations

import re

from metrics_api.registry import Metric

# Guard for every denominator, matching the idiom already used across the
# Grafana dashboards in this repo so API and dashboard agree numerically.
DIVIDE_GUARD = "0.001"

# Aggregations that describe a window rather than an instant, so they are only
# meaningful with a period.
_QUANTILES = {"p50": 0.5, "p90": 0.9, "p95": 0.95, "p99": 0.99}

VALID_AGGREGATES = ("avg", "min", "max", "sum", "rate", "increase", *_QUANTILES)

# VictoriaMetrics duration literals: 15s, 5m, 24h, 7d, 2w.
_DURATION = re.compile(r"^\d+(?:ms|s|m|h|d|w|y)$")

# A label value we will interpolate into a PromQL selector. Deliberately
# strict: these arrive from query strings, and a value containing a quote or a
# brace could otherwise change the shape of the generated query.
_LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.:@/+-]+$")


class InvalidQuery(ValueError):
    """A parameter combination that cannot produce a meaningful query.

    Always answered with HTTP 400 — the alternative is returning a number the
    caller would reasonably misread.
    """


def validate_duration(value: str, field: str) -> str:
    if not _DURATION.match(value or ""):
        raise InvalidQuery(
            f"{field} must be a duration like 15s, 5m, 24h or 7d (got '{value}')"
        )
    return value


def _validate_label_value(label: str, value: str) -> str:
    if not _LABEL_VALUE.match(value or ""):
        raise InvalidQuery(
            f"invalid value for label '{label}': '{value}' — allowed characters "
            "are letters, digits and _.:@/+-"
        )
    return value


def build_selector(metric: Metric, filters: dict[str, str | list[str]]) -> str:
    """The `{...}` label selector for a metric.

    A comma-separated filter becomes a regex alternation, so
    `kio_id=kio2-sim,kio3` is one query rather than two.
    """
    parts: list[str] = []
    for label in sorted(filters):
        raw = filters[label]
        values = [v.strip() for v in (raw.split(",") if isinstance(raw, str) else raw)]
        values = [v for v in values if v]
        if not values:
            continue
        for value in values:
            _validate_label_value(label, value)
        if len(values) == 1:
            parts.append(f'{label}="{values[0]}"')
        else:
            parts.append(f'{label}=~"{"|".join(values)}"')
    return "{" + ",".join(parts) + "}" if parts else ""


def _grouping(metric: Metric, group_by: list[str] | None) -> str:
    """The `by (...)` clause.

    Anything the caller did not explicitly ask to break out is aggregated away.
    That is what stops `kio.request.count` (labelled `status=ok|error`) from
    returning two series when the caller asked for one request rate.
    """
    if not group_by:
        return ""
    unknown = [g for g in group_by if g not in metric.labels]
    if unknown:
        raise InvalidQuery(
            f"cannot group '{metric.otel_name}' by {unknown}; "
            f"available labels: {', '.join(metric.labels)}"
        )
    return f" by ({','.join(group_by)})"


def default_aggregate(metric: Metric) -> str:
    """The aggregation that is meaningful for this instrument if none is asked
    for. Counters are monotonic, so their answer is a rate, never a mean."""
    if metric.is_counter:
        return "rate"
    if metric.is_histogram:
        return "avg"
    return "sum"


def build(
    metric: Metric,
    *,
    filters: dict[str, str | list[str]] | None = None,
    period: str = "5m",
    window: str | None = None,
    aggregate: str | None = None,
    group_by: list[str] | None = None,
) -> str:
    """The query expression for one metric.

    Used as-is for an instant query, and as the inner expression of a range
    query for `series` / `summary`.

    `window` is the lookback each rate/increase is computed over, and it is NOT
    the same thing as `period`:

      * For an instant query the two coincide — "the rate over the last 5m" is
        one number covering one 5m window, so `window` defaults to `period`.
      * For a RANGE query they must differ. Evaluating `rate(x[24h])` at every
        step of a 24h range gives a 24h-smoothed value at each point: the
        series flattens into a near-straight line and its min/max collapse
        toward the mean. The window has to be sized off the step instead (see
        auto_window), so each point summarises its own neighbourhood.
    """
    filters = dict(filters or {})
    metric.validate_labels(filters)
    validate_duration(period, "period")
    window = validate_duration(window or period, "window")

    aggregate = (aggregate or default_aggregate(metric)).lower()
    if aggregate not in VALID_AGGREGATES:
        raise InvalidQuery(
            f"unknown aggregate '{aggregate}'; valid: {', '.join(VALID_AGGREGATES)}"
        )

    selector = build_selector(metric, filters)
    grouping = _grouping(metric, group_by)

    if metric.is_histogram:
        return _histogram(metric, selector, window, aggregate, grouping, group_by)
    if metric.is_counter:
        return _counter(metric, selector, window, aggregate, grouping)
    return _gauge(metric, selector, aggregate, grouping)


def _histogram(
    metric: Metric,
    selector: str,
    period: str,  # the rate window; see build()'s note on window vs period
    aggregate: str,
    grouping: str,
    group_by: list[str] | None,
) -> str:
    name = metric.prom_name
    if aggregate in _QUANTILES:
        # `le` must survive the inner aggregation for histogram_quantile to
        # have buckets to interpolate between.
        inner_by = ",".join([*(group_by or []), "le"])
        return (
            f"histogram_quantile({_QUANTILES[aggregate]}, "
            f"sum by ({inner_by}) (rate({name}_bucket{selector}[{period}])))"
        )
    if aggregate == "avg":
        return (
            f"sum{grouping} (rate({name}_sum{selector}[{period}])) "
            f"/ clamp_min(sum{grouping} (rate({name}_count{selector}[{period}])), {DIVIDE_GUARD})"
        )
    if aggregate == "sum":
        # Total of the observed values, e.g. total milliseconds spent.
        return f"sum{grouping} (increase({name}_sum{selector}[{period}]))"
    if aggregate in ("rate", "increase"):
        # Observation frequency rather than observed magnitude.
        fn = "rate" if aggregate == "rate" else "increase"
        return f"sum{grouping} ({fn}({name}_count{selector}[{period}]))"
    raise InvalidQuery(
        f"aggregate '{aggregate}' is not available on histogram "
        f"'{metric.otel_name}' as an instant value; request format=summary "
        "for min/max across a period"
    )


def _counter(metric: Metric, selector: str, period: str, aggregate: str, grouping: str) -> str:
    name = metric.prom_name
    if aggregate == "rate":
        return f"sum{grouping} (rate({name}{selector}[{period}]))"
    if aggregate == "increase":
        return f"sum{grouping} (increase({name}{selector}[{period}]))"
    if aggregate == "sum":
        # The raw cumulative total since the series began. Valid, but rarely
        # what someone wants — kept because "total tokens ever" is a fair ask.
        return f"sum{grouping} ({name}{selector})"
    raise InvalidQuery(
        f"aggregate '{aggregate}' is not meaningful for the monotonic counter "
        f"'{metric.otel_name}' — use rate, increase or sum, or request "
        "format=summary for statistics on its rate over a period"
    )


def _gauge(metric: Metric, selector: str, aggregate: str, grouping: str) -> str:
    name = metric.prom_name
    if aggregate in ("sum", "avg", "min", "max"):
        fn = {"sum": "sum", "avg": "avg", "min": "min", "max": "max"}[aggregate]
        return f"{fn}{grouping} ({name}{selector})"
    raise InvalidQuery(
        f"aggregate '{aggregate}' is not meaningful for gauge "
        f"'{metric.otel_name}' — use sum, avg, min or max"
    )


def auto_step(period: str, minimum_seconds: int = 15) -> str:
    """Sampling resolution for a range query.

    `period / 100`, floored at the simulators' 15s metric export interval —
    a finer step cannot reveal more detail, it only produces gaps between
    scrapes that read as missing data.
    """
    validate_duration(period, "period")
    seconds = duration_seconds(period)
    step = max(seconds // 100, minimum_seconds)
    return f"{step}s"


# The KIO simulators' default metric export interval (EXPORT_INTERVAL_MS), which
# is this stack's equivalent of a scrape interval.
EXPORT_INTERVAL_SECONDS = 15


def auto_window(step: str, export_interval_seconds: int = EXPORT_INTERVAL_SECONDS) -> str:
    """Rate lookback for one point of a range query.

    `max(step + export_interval, 4 * export_interval)` — Grafana's
    `$__rate_interval` formula, and the reasoning behind each half matters:

      * A window narrower than the step leaves gaps between points; one
        narrower than a couple of export intervals straddles too few samples
        to form a rate at all. Hence the `4 * export_interval` floor.
      * A window much wider than the step smooths away the detail the step was
        chosen to reveal. So the window tracks the STEP, plus one export
        interval of slack so each bucket reliably contains a sample.

    An earlier version of this used `4 * step`, which is only sensible for
    small steps: it turned a 1d step into a 4-day lookback and a 30d step into
    a 120-day one, smoothing each point across several neighbouring buckets.
    """
    validate_duration(step, "step")
    step_seconds = duration_seconds(step)
    return f"{max(step_seconds + export_interval_seconds, 4 * export_interval_seconds)}s"


_UNIT_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def duration_seconds(value: str) -> int:
    validate_duration(value, "duration")
    match = re.match(r"^(\d+)(ms|s|m|h|d|w|y)$", value)
    amount, unit = int(match.group(1)), match.group(2)
    return int(amount * _UNIT_SECONDS[unit])
