"""Generate the metric registry from kio-simulator/kio_simulator.py.

The registry is what lets the metrics API answer queries without the caller
knowing OTel-vs-Prometheus name mangling, the histogram `_sum`/`_count` idiom,
or which instrument type a metric is. Rather than hand-maintaining it (it would
drift the first time someone adds an instrument), everything mechanical is
extracted from the simulator's source.

Why a STATIC PARSE and not an import:

  * Importing kio_simulator only creates the instruments the environment
    enables. With KIO_REAL_KPI_ROLE unset — the only way to import it without
    picking one role — none of the ~20 D1.1 KPI instruments exist at all, and
    with any single role set you get exactly one role's worth. There is no
    environment that yields the complete set.
  * The `if KIO_REAL_KPI_ROLE == "..."` block an instrument is declared inside
    is precisely the information needed for its `family` and `role` fields, and
    that structure is visible in the AST but erased at runtime.
  * Importing has side effects (starts OTel exporter threads, builds a Langfuse
    client). A build-time generator should not need to.

The output is committed as metrics_api/registry_data.py; the metrics API image
never needs kio_simulator.py in its build context. tests/metrics_api_tests/
re-runs this extraction and fails on any drift between source and committed
registry.

Usage:
    python scripts/generate_metric_registry.py            # write the registry
    python scripts/generate_metric_registry.py --stdout    # preview only
"""
from __future__ import annotations

import argparse
import ast
import pprint
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR = REPO_ROOT / "kio-simulator" / "kio_simulator.py"
OUTPUT = REPO_ROOT / "metrics_api" / "registry_data.py"

# meter.create_*() factory name -> the instrument classification the query
# builder switches on. Observable gauges are plain gauges as far as PromQL is
# concerned; the callback mechanism is a producer-side detail.
INSTRUMENT_FACTORIES = {
    "create_counter": "counter",
    "create_up_down_counter": "up_down_counter",
    "create_histogram": "histogram",
    "create_observable_gauge": "gauge",
    "create_observable_counter": "counter",
    "create_observable_up_down_counter": "up_down_counter",
}

# Attribute keys that are telemetry context rather than queryable metric
# labels. session.id is high-cardinality and deliberately never a metric label
# (contract's low-cardinality rule) — it appears only on logs and spans.
NON_LABEL_KEYS = {"session.id", "kio.status"}


def prom_name(otel_name: str) -> str:
    """OTel dotted name -> the name VictoriaMetrics actually stores."""
    return otel_name.replace(".", "_")


def label_name(attr_key: str) -> str:
    """Attribute key as it appears as a Prometheus label."""
    return attr_key.replace(".", "_")


class _LabelResolver:
    """Resolves the attribute-dict expressions passed to .record()/.add().

    The simulator builds its attribute dicts by name and then spreads them:

        labels     = {"kio.id": ..., "llm": ..., "task_type": ...}
        labels_src = {**labels, "source": data_source}
        labels_kpi = {**labels, "source": "simulated"}
        ...record(value, {**labels_src, "direction": "input"})

    so resolving a call site means following those assignments. Only string
    literal keys matter — values are irrelevant here, we want the label NAMES.
    """

    def __init__(self) -> None:
        self.bindings: dict[str, set[str]] = {}

    def learn_assignment(self, node: ast.Assign) -> None:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        keys = self.resolve(node.value)
        if keys is not None:
            self.bindings[node.targets[0].id] = keys

    def resolve(self, node: ast.expr) -> set[str] | None:
        """Label names contributed by an attribute-dict expression."""
        if isinstance(node, ast.Dict):
            keys: set[str] = set()
            for key, value in zip(node.keys, node.values):
                if key is None:  # {**other}
                    nested = self.resolve(value)
                    if nested is None:
                        return None
                    keys |= nested
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
                else:
                    return None  # a computed key we cannot read statically
            return keys
        if isinstance(node, ast.Name):
            return set(self.bindings.get(node.id, set())) or self.bindings.get(node.id)
        return None


def _gate_from_test(test: ast.expr) -> tuple[str, list[str]] | None:
    """The conditional-emission gate an `if` imposes, if it is one we track.

    Two env-driven gates decide whether an instrument exists at all:

        if KIO_REAL_KPI_ROLE == "bugfix":                        -> D1.1 KPIs
        if KIO_REAL_KPI_ROLE in ("nlp-requirements", "..."):
        if TASK_TYPE == "code-analysis":                         -> repo.* gauges

    Returns ("role" | "task_type", [values]) or None for any other condition.
    """
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    if not isinstance(test.left, ast.Name):
        return None
    kind = {"KIO_REAL_KPI_ROLE": "role", "TASK_TYPE": "task_type"}.get(test.left.id)
    if kind is None:
        return None
    op, comparator = test.ops[0], test.comparators[0]
    if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
        return kind, [comparator.value]
    if isinstance(op, ast.In) and isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
        return kind, [e.value for e in comparator.elts if isinstance(e, ast.Constant)]
    return None


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def extract(source: str) -> dict[str, dict]:
    tree = ast.parse(source)

    # instrument variable name -> otel metric name, so .record()/.add() call
    # sites can be attributed back to the metric they belong to.
    var_to_metric: dict[str, str] = {}
    metrics: dict[str, dict] = {}
    resolver = _LabelResolver()

    def _entry(call: ast.Call, otel_name: str, ctx: dict) -> dict:
        roles = ctx["roles"]
        task_types = ctx["task_types"]
        return {
            "otel_name": otel_name,
            "prom_name": prom_name(otel_name),
            "instrument": INSTRUMENT_FACTORIES[call.func.attr],
            "unit": _kwarg(call, "unit"),
            "family": "kpi" if roles else "operational",
            "roles": list(roles),
            # Conditionally emitted: a missing series is expected, not a fault.
            "optional": bool(roles or task_types),
            "requires_task_type": list(task_types),
            "labels": set(),
            "required_group_by": set(),
        }

    def _is_instrument_call(call: ast.expr) -> bool:
        return (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in INSTRUMENT_FACTORIES
            and bool(call.args)
            and isinstance(call.args[0], ast.Constant)
        )

    def visit(node: ast.AST, ctx: dict) -> None:
        """Walk the tree carrying the conditional-emission gates in force."""
        if isinstance(node, ast.If):
            gate = _gate_from_test(node.test)
            inner = ctx
            if gate is not None:
                kind, values = gate
                key = "roles" if kind == "role" else "task_types"
                inner = {**ctx, key: values}
            for child in node.body:
                visit(child, inner)
            # An `elif` chain lands in orelse; each branch carries its own gate.
            for child in node.orelse:
                visit(child, ctx)
            return

        if isinstance(node, ast.Assign):
            resolver.learn_assignment(node)
            if _is_instrument_call(node.value):
                otel_name = node.value.args[0].value
                metrics[otel_name] = _entry(node.value, otel_name, ctx)
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    var_to_metric[node.targets[0].id] = otel_name
            for child in ast.iter_child_nodes(node):
                visit(child, ctx)
            return

        # A bare create_observable_gauge(...) call — not bound to a variable.
        if isinstance(node, ast.Expr) and _is_instrument_call(node.value):
            otel_name = node.value.args[0].value
            metrics.setdefault(otel_name, _entry(node.value, otel_name, ctx))

        for child in ast.iter_child_nodes(node):
            visit(child, ctx)

    for stmt in tree.body:
        visit(stmt, {"roles": [], "task_types": []})

    # Second pass: attribute label sets from every .record()/.add() call site.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            resolver.learn_assignment(node)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("record", "add"):
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        metric_name = var_to_metric.get(node.func.value.id)
        if metric_name is None or len(node.args) < 2:
            continue
        keys = resolver.resolve(node.args[1])
        if not keys:
            continue
        entry = metrics[metric_name]
        base = {"kio.id", "llm", "task_type"}
        for key in keys:
            if key in NON_LABEL_KEYS:
                continue
            entry["labels"].add(label_name(key))
            if key not in base and key != "source":
                entry["required_group_by"].add(label_name(key))

    # Observable gauges record via Observation(...) in a callback rather than
    # .record(), so pick their labels up from those constructor calls.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Observation"
            and len(node.args) == 2
        ):
            keys = resolver.resolve(node.args[1]) or set()
            for name, entry in metrics.items():
                if entry["instrument"] == "gauge" and not entry["labels"]:
                    entry["labels"] |= {label_name(k) for k in keys if k not in NON_LABEL_KEYS}

    return metrics


def finalize(metrics: dict[str, dict]) -> dict[str, dict]:
    """Sort sets into stable lists so the generated file is diff-friendly."""
    out = {}
    for name in sorted(metrics):
        entry = dict(metrics[name])
        entry["labels"] = sorted(entry["labels"])
        entry["required_group_by"] = sorted(entry["required_group_by"])
        entry["supports_source"] = "source" in entry["labels"]
        out[name] = entry
    return out


HEADER = '''"""Generated metric registry — DO NOT EDIT BY HAND.

Regenerate with:
    python scripts/generate_metric_registry.py

Extracted from kio-simulator/kio_simulator.py by scripts/generate_metric_registry.py.
tests/metrics_api_tests/test_registry_drift.py fails if this file and the
simulator disagree.

Fields that are NOT derivable from the simulator (D1.1 baseline/target/direction
for KPI metrics, and the human-readable KPI names) are layered on separately in
registry.py — they come from docs/KPI_Metrik_Referansi_v1.3.docx, not from code.
"""
'''


def render(metrics: dict[str, dict]) -> str:
    body = pprint.pformat(metrics, width=100, sort_dicts=False)
    return f"{HEADER}\nMETRICS = {body}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args()

    metrics = finalize(extract(SIMULATOR.read_text(encoding="utf-8")))
    rendered = render(metrics)

    if args.stdout:
        print(rendered)
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    operational = sum(1 for e in metrics.values() if e["family"] == "operational")
    kpi = len(metrics) - operational
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}: {len(metrics)} metrics ({operational} operational, {kpi} kpi)")


if __name__ == "__main__":
    main()
