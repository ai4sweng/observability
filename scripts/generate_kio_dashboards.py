"""Generate one Grafana detail dashboard per KIO — DO NOT EDIT THE OUTPUT BY HAND.

Regenerate with:
    python scripts/generate_kio_dashboards.py

Each KIO gets its own dashboard carrying *only* the KPIs D1.1 Table 3 assigns
to it, followed by the shared operational panels. Grafana cannot hide a panel
conditionally, so a single dashboard would have to show all 17 KPI series on
every KIO's page — hence one file per KIO.

KPI definitions, targets and ownership all come from scripts/d11_kpis.py; the
KPI panels are built from that table rather than copied out of the template, so
a change there propagates to every page.

Inputs:
    scripts/d11_kpis.py                    (KPI table: D1.1 Table 3 + Table 8)
    scripts/kio_dashboard_template.json    (operational panels + variables;
                                            not under grafana/dashboards/, so
                                            Grafana never loads it and re-running
                                            this script is idempotent)
Outputs:
    grafana/dashboards/ai4sweng-kio<N>.json   one per KIO in Table 3
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d11_kpis as d11  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DASH_DIR = REPO / "grafana" / "dashboards"
TEMPLATE = REPO / "scripts" / "kio_dashboard_template.json"

DS = {"type": "prometheus", "uid": "victoriametrics"}

# Template panel ids that the KPI section replaces: the five original KPI row
# headers (each hardcoding a producer role, e.g. "— kio2-sim (bugfix role)"),
# every KPI stat/gauge, the KIO2-specific trend pair, and the dynamic-slicing
# panels (a WP3 task metric, not a D1.1 global KPI).
DROP_PANELS = {24, 25, 26, 27, 28, 29, 30, 49, 50, 51, 52, 53, 54,
               55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 100, 101}

# Row titles that name a specific KIO. On KIO3's page a row headed "kio2-sim"
# is exactly the cross-KIO confusion these per-KIO dashboards exist to remove.
RETITLE = {
    "Code-Analysis-Specific Statistics (data only in kio2-sim)":
        "Code-Analysis Statistics (reported by code-analysis KIOs only)",
    "GPU Temperature (real — kio2-sim in real mode only)":
        "GPU Temperature (real-LLM mode only)",
    "Model Comparison & Data Source (adapted from the idt4gdc-grafana-demo style)":
        "Model Comparison & Data Source",
}

# kio_id label values per KIO number. KIO2 has both the reserved real-module
# identity and the simulator; every other KIO uses the plain id.
KIO_LABELS: dict[int, list[str]] = {2: ["kio2-sim", "kio2"]}


def kio_label_values(n: int) -> list[str]:
    return KIO_LABELS.get(n, [f"kio{n}"])


def kpi_panel(k: d11.Kpi, pid: int, grid: dict) -> dict:
    """A stat panel showing the KPI in D1.1's own unit, coloured by its target."""
    return {
        "id": pid,
        "type": "stat",
        "title": d11.panel_title(k),
        "description": k.description(),
        "datasource": DS,
        "gridPos": grid,
        "fieldConfig": {
            "defaults": {
                "unit": k.unit,
                "decimals": 1 if k.instrument == d11.HIST else 0,
                "noValue": "—",
                "thresholds": {"mode": "absolute", "steps": k.thresholds()},
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "textMode": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
        "targets": [{
            "refId": "A",
            "datasource": DS,
            "editorMode": "code",
            "expr": k.value_expr('kio_id="$kio_id"'),
            "legendFormat": f"KPI {k.kpi_id}",
            "range": True,
            "instant": False,
        }],
    }


def shift(panels: list[dict], start_y: int) -> list[dict]:
    """Re-stack panels below start_y, preserving their relative rows."""
    out = [copy.deepcopy(p) for p in panels]
    rows = sorted({p["gridPos"]["y"] for p in out})
    remap: dict[int, int] = {}
    cur = start_y
    for oy in rows:
        remap[oy] = cur
        cur += max(p["gridPos"]["h"] for p in out if p["gridPos"]["y"] == oy)
    for p in out:
        p["gridPos"]["y"] = remap[p["gridPos"]["y"]]
    return out


def main() -> None:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    ops_panels = [p for p in template["panels"] if p["id"] not in DROP_PANELS]
    for p in ops_panels:
        if p.get("title") in RETITLE:
            p["title"] = RETITLE[p["title"]]

    written = []
    for n in d11.kio_numbers():
        kpis = d11.for_kio(n)
        labels = kio_label_values(n)
        d = copy.deepcopy(template)

        d["uid"] = f"ai4sweng-kio{n}"
        d["title"] = f"AI4SWENG — KIO{n} Detail"
        d["version"] = 1

        # kio_id becomes a fixed custom variable: this page is this KIO's page,
        # and it must not depend on whether a producer is currently running.
        for var in d.get("templating", {}).get("list", []):
            if var.get("name") != "kio_id":
                continue
            var.update({
                "type": "custom",
                "label": "KIO",
                "query": ",".join(labels),
                "options": [{"selected": i == 0, "text": v, "value": v}
                            for i, v in enumerate(labels)],
                "current": {"selected": True, "text": labels[0], "value": labels[0]},
                "includeAll": False,
                "multi": False,
                "hide": 0 if len(labels) > 1 else 2,
            })
            for gone in ("refresh", "datasource", "definition"):
                var.pop(gone, None)

        panels: list[dict] = [{
            "id": 100,
            "type": "row",
            "title": f"D1.1 Project KPIs — KIO{n}  (SIMULATED DATA)",
            "collapsed": False,
            "panels": [],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
        }]

        for i, k in enumerate(kpis):
            panels.append(kpi_panel(k, 110 + i, {
                "h": 4, "w": 6, "x": (i % 4) * 6, "y": 1 + (i // 4) * 4,
            }))

        # Grafana floats panels up into free grid space, so a KPI block that
        # doesn't fill all 24 columns pulls the operational stats up beside the
        # KPIs and blurs the section boundary. Pad the last row.
        used = (len(kpis) % 4) * 6
        if used:
            panels.append({
                "id": 109,
                "type": "text",
                "title": "",
                "transparent": True,
                "options": {"mode": "markdown", "content": ""},
                "gridPos": {"h": 4, "w": 24 - used,
                            "x": used, "y": 1 + (len(kpis) // 4) * 4},
            })

        y = 1 + ((len(kpis) + 3) // 4) * 4
        panels.extend(shift(ops_panels, y))
        d["panels"] = panels

        out = DASH_DIR / f"ai4sweng-kio{n}.json"
        out.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append((out.name, n, kpis, labels))

    print(f"KIOs with KPIs in D1.1 Table 3: {len(written)}")
    for fname, n, kpis, labels in written:
        ids = ", ".join(k.kpi_id for k in kpis)
        print(f"  {fname:24} KIO{n:<3} kio_id={','.join(labels):16} {len(kpis):2} KPI: {ids}")
    missing = [f"KIO{i}" for i in range(1, 14) if i not in d11.kio_numbers()]
    print(f"\nNo KPI in Table 3, so no dashboard: {', '.join(missing)}")
    print(f"Shared operational panels per page: {len(ops_panels)}")


if __name__ == "__main__":
    main()
