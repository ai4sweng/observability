"""Generate one Grafana detail dashboard per KIO — DO NOT EDIT THE OUTPUT BY HAND.

Regenerate with:
    python scripts/generate_kio_dashboards.py

Each KIO gets its own dashboard carrying *only* the KPIs that D1.1 "Table 3.
KPIs: baseline values and targets" assigns to it, followed by the shared
operational panels. Grafana cannot hide a panel conditionally, so a single
dashboard would have to show all 16 KPIs on every KIO's page — hence one file
per KIO.

Source of truth for the mapping is TABLE_3 below, transcribed from the
"Related KIO" column of Table 3. Edit that and re-run.

Inputs:
    scripts/kio_dashboard_template.json    (template: every panel + variables;
                                            not under grafana/dashboards/, so
                                            Grafana never loads it and re-running
                                            this script is idempotent)
Outputs:
    grafana/dashboards/ai4sweng-kio<N>.json   one per KIO in Table 3

Each output page carries its KIO's KPI panels followed by the shared
operational panels, so there is no separate all-KIOs page.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASH_DIR = REPO / "grafana" / "dashboards"
TEMPLATE = REPO / "scripts" / "kio_dashboard_template.json"

# --------------------------------------------------------------------- Table 3
# KPI id -> (name, owning KIOs, baseline, target).  Verbatim from Table 3.
KPIS = {
    "1.1": ("Code generation speed",              [2, 3, 4, 7],  "~100-120 min",             "<=70% (~70-90 min)"),
    "1.2": ("Issue resolution speed",             [2, 3, 4, 7],  "1.0-1.5 working days",     "<=70%"),
    "2.1": ("Lifecycle energy reduction",         [7, 8, 10],    "100%",                     "<=85%"),
    "2.2": ("Deployment energy efficiency",       [7, 8, 10],    "100%",                     ">=15% improvement"),
    "3.1": ("Code quality improvement",           [4, 7, 9],     "100%",                     "<=70% of the adverse-quality measure"),
    "3.2": ("Review score increase",              [4, 7, 9],     "~3.5 / 5",                 ">=4.2 / 5"),
    "4.1": ("Developer productivity",             [1, 7, 13],    "1 feature / developer / day", "~1.20"),
    "5.1": ("Time-to-market",                     [7, 11],       "5-7 days",                 "3-5 days"),
    "6.1": ("Bug-fix time",                       [2, 7, 11],    "8-12 hours",               "7-10 hours"),
    "6.2": ("Customer-reported issues",           [2, 7, 11],    "~5 / feature",             "~4 / feature"),
    "7.1": ("Annual cost saving",                 [7, 8, 10],    "<=EUR 100 000 / year",     "<=EUR 85 000 / year"),
    "8.1": ("Adoption rate",                      [13],          "0%",                       ">=50% within two pilot sprints"),
    "8.2": ("Active usage and satisfaction",      [13],          "0% / MOS ~3.0",            ">=60% / MOS >=4.0"),
    "8.3": ("Cross-architecture build success",   [8],           "0",                        ">=1 validated target"),
    "9.1": ("Refactoring reduction",              [7, 9],        "2-3 h / feature",          "1.5-2 h / feature"),
    "9.2": ("Technical debt reduction",           [7, 9],        "1.0-1.2 h / 100 LOC",      "<=0.8 h"),
}

# KPI id -> panel id(s) in the template. KPI 8.2 is two instruments.
KPI_PANELS: dict[str, list[int]] = {
    "1.1": [50], "1.2": [26], "2.1": [60], "2.2": [61],
    "3.1": [51], "3.2": [52], "4.1": [54], "5.1": [55],
    "6.1": [25], "6.2": [28], "7.1": [56], "8.1": [64],
    "8.2": [65, 66], "8.3": [62], "9.1": [57], "9.2": [58],
}

# Grafana auto-scales its built-in duration units, so KPI 1.1's 80 minutes
# renders as "1.34 hours" — useless next to a target stated in minutes.
UNIT_OVERRIDES: dict[str, str] = {"1.1": "suffix: min"}

# Bug-fix / issue-resolution trend: only meaningful for a KIO owning both.
TREND_PANEL = 29
TREND_REQUIRES = {"6.1", "1.2"}

# Dynamic slicing is a WP3 task metric, not a Table 3 KPI. Dropped everywhere.
DROP_PANELS = {27, 30}

# The template's five original KPI row headers, each hardcoding a producer role
# ("— kio2-sim (bugfix role)", "— kio7 (ai-sysdev)", ...). Replaced by the single
# per-KIO row this script builds, so they must not survive as "ops" panels.
OLD_KPI_ROWS = {24, 49, 53, 59, 63}

# Row titles that name a specific KIO. On KIO3's page a row headed "kio2-sim"
# is exactly the cross-KIO confusion these dashboards exist to remove.
RETITLE = {
    "Code-Analysis-Specific Statistics (data only in kio2-sim)":
        "Code-Analysis Statistics (reported by code-analysis KIOs only)",
    "GPU Temperature (real — kio2-sim in real mode only)":
        "GPU Temperature (real-LLM mode only)",
    "Model Comparison & Data Source (adapted from the idt4gdc-grafana-demo style)":
        "Model Comparison & Data Source",
}

# kio_id label values per KIO number. KIO2 has both the reserved real module
# identity and the simulator; every other KIO uses the plain id.
KIO_LABELS: dict[int, list[str]] = {2: ["kio2-sim", "kio2"]}


def kio_label_values(n: int) -> list[str]:
    return KIO_LABELS.get(n, [f"kio{n}"])


def table3_sets() -> dict[int, list[str]]:
    """KIO number -> its KPI ids, in ascending KPI order."""
    out: dict[int, list[str]] = {}
    for kpi_id, (_, owners, _, _) in KPIS.items():
        for n in owners:
            out.setdefault(n, []).append(kpi_id)
    return {n: sorted(ids, key=lambda s: tuple(int(p) for p in s.split("."))) for n, ids in sorted(out.items())}


def describe(kpi_id: str) -> str:
    name, owners, baseline, target = KPIS[kpi_id]
    owner_txt = ", ".join(f"KIO{o}" for o in owners)
    return (
        f"**Target: {target}**  |  Baseline: {baseline}\n\n"
        f"D1.1 Table 3 — KPI {kpi_id} ({name}). Owning KIO(s): {owner_txt}.\n\n"
        "Empty means this KIO is not reporting the KPI yet. Values are simulated."
    )


def main() -> None:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in template["panels"]}

    all_kpi_panel_ids = {pid for ids in KPI_PANELS.values() for pid in ids}
    structural = all_kpi_panel_ids | DROP_PANELS | OLD_KPI_ROWS | {TREND_PANEL, 100}
    ops_panels = [p for p in template["panels"] if p["id"] not in structural]
    for p in ops_panels:
        if p.get("title") in RETITLE:
            p["title"] = RETITLE[p["title"]]

    sets = table3_sets()
    written = []

    for n, kpi_ids in sets.items():
        d = copy.deepcopy(template)
        labels = kio_label_values(n)

        d["uid"] = f"ai4sweng-kio{n}"
        d["title"] = f"AI4SWENG — KIO{n} Detail"
        d["version"] = 1

        # kio_id becomes a fixed custom variable: the page is this KIO's page,
        # and it must not depend on whether a producer is currently running.
        for var in d.get("templating", {}).get("list", []):
            if var.get("name") != "kio_id":
                continue
            var.update({
                "type": "custom",
                "label": "KIO",
                "query": ",".join(labels),
                "options": [
                    {"selected": i == 0, "text": v, "value": v} for i, v in enumerate(labels)
                ],
                "current": {"selected": True, "text": labels[0], "value": labels[0]},
                "includeAll": False,
                "multi": False,
                "hide": 0 if len(labels) > 1 else 2,
            })
            var.pop("refresh", None)
            var.pop("datasource", None)
            var.pop("definition", None)

        panels: list[dict] = [{
            "id": 100,
            "type": "row",
            "title": f"D1.1 Project KPIs — KIO{n} (Table 3)",
            "collapsed": False,
            "panels": [],
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
        }]

        slot = 0
        y = 1
        for kpi_id in kpi_ids:
            for pid in KPI_PANELS[kpi_id]:
                p = copy.deepcopy(by_id[pid])
                name = KPIS[kpi_id][0]
                # KPI 8.2 is two instruments; name each by what it measures
                # rather than repeating the combined KPI title twice.
                if len(KPI_PANELS[kpi_id]) > 1:
                    name = "Active usage" if pid == 65 else "Satisfaction (MOS)"
                p["title"] = f"KPI {kpi_id} — {name}"
                p["description"] = describe(kpi_id)
                defaults = p.setdefault("fieldConfig", {}).setdefault("defaults", {})
                defaults["noValue"] = "—"
                if kpi_id in UNIT_OVERRIDES:
                    defaults["unit"] = UNIT_OVERRIDES[kpi_id]
                p["gridPos"] = {"h": 4, "w": 6, "x": (slot % 4) * 6, "y": y + (slot // 4) * 4}
                panels.append(p)
                slot += 1
        # Grafana floats panels up into free grid space, so a KPI row that
        # doesn't fill all 24 columns pulls the operational stats up beside the
        # KPIs and blurs the section boundary. Pad the last row.
        used = (slot % 4) * 6
        if used:
            panels.append({
                "id": 101,
                "type": "text",
                "title": "",
                "transparent": True,
                "options": {"mode": "markdown", "content": ""},
                "gridPos": {"h": 4, "w": 24 - used, "x": used, "y": y + (slot // 4) * 4},
            })
        y += ((slot + 3) // 4) * 4

        if TREND_REQUIRES <= set(kpi_ids):
            t = copy.deepcopy(by_id[TREND_PANEL])
            t["title"] = "Bug-fix time / issue resolution trend (hours)"
            t["gridPos"] = {"h": 7, "w": 24, "x": 0, "y": y}
            panels.append(t)
            y += 7

        # shared operational panels, original order, shifted below the KPI block
        panels.extend(shift(ops_panels, y))
        d["panels"] = panels

        out = DASH_DIR / f"ai4sweng-kio{n}.json"
        out.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append((out.name, n, kpi_ids, labels))

    print(f"KIOs with KPIs in Table 3: {len(written)}")
    for fname, n, ids, labels in written:
        print(f"  {fname:26} KIO{n:<3} kio_id={','.join(labels):16} {len(ids):2} KPI: {', '.join(ids)}")
    no_kpi = [f"KIO{i}" for i in range(1, 14) if i not in sets]
    print(f"\nTable 3'te KPI atanmamis KIO'lar (dashboard uretilmedi): {', '.join(no_kpi)}")
    print(f"Dynamic slicing panelleri kaldirildi: {sorted(DROP_PANELS)}")
    print(f"Ops panel sayisi (her sayfada paylasilan): {len(ops_panels)}")


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


if __name__ == "__main__":
    main()
