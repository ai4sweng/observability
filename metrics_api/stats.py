"""Summary statistics over a range query's samples.

`format=summary` runs ONE range query and derives current/avg/min/max/p95 from
the points it returns. The alternative — a MetricsQL `rollup()` plus a
`quantile_over_time()` plus an instant query — costs three round trips and can
return statistics computed over subtly different windows.

What the numbers mean, stated plainly because it is easy to misread:
these are statistics over the PERIOD'S SAMPLES of the snapshot expression. For
`kio.request.duration_ms` with the default `avg` aggregate, `max` is "the
highest that the average latency got during the window", not "the slowest
single request". The latter is `aggregate=p95`/`p99`, which goes through
`histogram_quantile` on the real bucket distribution — see query.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Summary:
    current: float | None
    avg: float | None
    min: float | None
    max: float | None
    p95: float | None
    samples: int

    def as_dict(self) -> dict:
        return asdict(self)


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear-interpolated percentile.

    Matches numpy's default ('linear') so a reader comparing this against an
    ad-hoc numpy calculation gets the same answer. numpy itself is not a
    dependency of this service.
    """
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(values: list[float], *, current: float | None = None) -> Summary:
    """Statistics for one series' samples.

    `current` defaults to the last sample; pass it explicitly when the caller
    already has a separately-fetched instant value.
    """
    if not values:
        return Summary(current=current, avg=None, min=None, max=None, p95=None, samples=0)
    return Summary(
        current=current if current is not None else values[-1],
        avg=sum(values) / len(values),
        min=min(values),
        max=max(values),
        p95=percentile(values, 0.95),
        samples=len(values),
    )
