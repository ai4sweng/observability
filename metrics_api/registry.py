"""Metric registry lookup layer.

`registry_data.py` holds the generated facts (see
scripts/generate_metric_registry.py). This module is what the rest of the API
uses: name resolution, label validation, and family filtering.

Name resolution is deliberately forgiving, because the whole point of the API
is that a caller should not have to know which spelling is "real". All of these
resolve to the same metric:

    kio.request.duration_ms     (OTel declaration name)
    kio_request_duration_ms     (what VictoriaMetrics stores)
    request.duration_ms         (prefix dropped)
    request_duration_ms

Ambiguity is an error rather than a guess — if a short form ever matches two
metrics, the caller is told to disambiguate instead of silently getting one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from metrics_api.registry_data import METRICS

# Labels every metric carries, from the simulator's shared attribute dict.
BASE_LABELS = ("kio_id", "llm", "task_type")


class UnknownMetric(LookupError):
    """Requested metric name is not in the registry."""

    def __init__(self, name: str, suggestions: list[str]) -> None:
        self.name = name
        self.suggestions = suggestions
        detail = f"unknown metric '{name}'"
        if suggestions:
            detail += f"; did you mean: {', '.join(suggestions)}"
        super().__init__(detail)


class AmbiguousMetric(LookupError):
    """A shortened metric name matches more than one registry entry."""

    def __init__(self, name: str, matches: list[str]) -> None:
        self.name = name
        self.matches = matches
        super().__init__(
            f"metric name '{name}' is ambiguous; matches {', '.join(matches)} "
            "— use the full name"
        )


class InvalidLabel(ValueError):
    """A label filter the requested metric does not carry.

    Kept distinct from UnknownMetric because the API must answer this with a
    400: returning an empty 200 would be indistinguishable from "the pipeline
    is broken", which is the failure mode this registry exists to prevent.
    """

    def __init__(self, metric: str, label: str, valid: list[str]) -> None:
        self.metric = metric
        self.label = label
        self.valid = valid
        super().__init__(
            f"metric '{metric}' does not carry label '{label}'; "
            f"valid labels: {', '.join(valid)}"
        )


@dataclass(frozen=True)
class Metric:
    """One registry entry, as the query builder sees it."""

    otel_name: str
    prom_name: str
    instrument: str
    unit: str | None
    family: str
    labels: tuple[str, ...]
    required_group_by: tuple[str, ...]
    roles: tuple[str, ...] = ()
    optional: bool = False
    requires_task_type: tuple[str, ...] = ()
    supports_source: bool = False

    @property
    def is_histogram(self) -> bool:
        return self.instrument == "histogram"

    @property
    def is_counter(self) -> bool:
        return self.instrument == "counter"

    @property
    def is_gauge(self) -> bool:
        return self.instrument in ("gauge", "up_down_counter")

    def validate_labels(self, labels: dict[str, str]) -> None:
        """Raise InvalidLabel for any filter this metric cannot support."""
        for name in labels:
            if name not in self.labels:
                raise InvalidLabel(self.otel_name, name, list(self.labels))


def _build() -> dict[str, Metric]:
    out: dict[str, Metric] = {}
    for name, entry in METRICS.items():
        out[name] = Metric(
            otel_name=entry["otel_name"],
            prom_name=entry["prom_name"],
            instrument=entry["instrument"],
            unit=entry["unit"],
            family=entry["family"],
            labels=tuple(entry["labels"]),
            required_group_by=tuple(entry["required_group_by"]),
            roles=tuple(entry["roles"]),
            optional=entry["optional"],
            requires_task_type=tuple(entry["requires_task_type"]),
            supports_source=entry["supports_source"],
        )
    return out


ALL: dict[str, Metric] = _build()


def _aliases() -> dict[str, list[str]]:
    """Every accepted spelling -> the otel names it could mean."""
    index: dict[str, list[str]] = {}

    def add(alias: str, otel_name: str) -> None:
        index.setdefault(alias, [])
        if otel_name not in index[alias]:
            index[alias].append(otel_name)

    for metric in ALL.values():
        add(metric.otel_name, metric.otel_name)
        add(metric.prom_name, metric.otel_name)
        for prefix in ("kio.", "kio_"):
            if metric.otel_name.startswith(prefix):
                stripped = metric.otel_name[len(prefix):]
                add(stripped, metric.otel_name)
                add(stripped.replace(".", "_"), metric.otel_name)
    return index


ALIASES: dict[str, list[str]] = _aliases()


def _suggest(name: str, limit: int = 3) -> list[str]:
    """Cheap substring suggestions for an unknown name — no fuzzy matching,
    just enough to make a 400 actionable."""
    needle = name.replace(".", "_").strip("_").lower()
    if not needle:
        return []
    hits = [m.otel_name for m in ALL.values() if needle in m.prom_name.lower()]
    if not hits:
        tail = needle.split("_")[-1]
        hits = [m.otel_name for m in ALL.values() if tail and tail in m.prom_name.lower()]
    return sorted(hits)[:limit]


def resolve(name: str) -> Metric:
    """A metric by any accepted spelling.

    Raises UnknownMetric or AmbiguousMetric — both map to HTTP 400.
    """
    key = name.strip()
    matches = ALIASES.get(key)
    if matches is None:
        raise UnknownMetric(name, _suggest(name))
    if len(matches) > 1:
        raise AmbiguousMetric(name, sorted(matches))
    return ALL[matches[0]]


def resolve_many(names: str | list[str]) -> list[Metric]:
    """Resolve a comma-separated list (or an already-split list), preserving
    request order and dropping duplicates."""
    if isinstance(names, str):
        names = [part for part in (p.strip() for p in names.split(",")) if part]
    seen: set[str] = set()
    out: list[Metric] = []
    for name in names:
        metric = resolve(name)
        if metric.otel_name not in seen:
            seen.add(metric.otel_name)
            out.append(metric)
    return out


def by_family(family: str) -> list[Metric]:
    """All metrics in a family ('operational' or 'kpi'), name-sorted."""
    return sorted(
        (m for m in ALL.values() if m.family == family),
        key=lambda m: m.otel_name,
    )


def operational() -> list[Metric]:
    return by_family("operational")


def kpis() -> list[Metric]:
    return by_family("kpi")
