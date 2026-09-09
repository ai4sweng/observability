"""The D1.1 global KPI table — single source of truth for both dashboard generators.

Every field is transcribed from the AI4SWEng Project Management Handbook
(D1.1): Table 8 "KPIs: baseline values, reference conditions, and targets" for
the definitions, units and targets, and Table 3 "KPIs: baseline values and
targets" for the `owners` column (its "Related KIO" column supersedes the
ownership assignments in D1.1 Table 7).

Two things about D1.1 that shape this table:

1. **D1.1 states almost every KPI as a percentage of baseline**, with the
   state-of-the-art IDE/SDK baseline normalised to 100 % — KPI 1.1's unit is
   literally "% of baseline time". `kio_simulator.py` emits each KPI in that
   unit, so `target` here is directly comparable to the measured value with no
   conversion. `baseline_abs` records what 100 % means in the real world, for
   display only.

   The exceptions, which D1.1 states on their own scales, are KPI 8.1 (% of
   eligible users), 8.2 (% of users, and MOS 1-5) and 8.3 (a plain number).

2. **Two D1.1 target cells contain a typo**: KPI 3.2 and KPI 4.1 both read
   "≤ 120 % of baseline" while the very same rows say "+20 % improvement" /
   "≈20 % productivity gain", and Table 3 gives ">=4.2 / 5" and ">=~1.20".
   They are increases, so both are recorded here as `HIGHER` with a target of
   120. If the consortium later confirms the "≤" literally, flip the two
   `direction` fields and regenerate.

Regenerate the dashboards after editing this file:

    python scripts/generate_kio_dashboards.py
    python scripts/generate_overview_kpis.py
"""
from __future__ import annotations

from dataclasses import dataclass

LOWER = "lower"    # target is a ceiling: measured <= target is on target
HIGHER = "higher"  # target is a floor:   measured >= target is on target
BINARY = "binary"  # met once the count reaches the target at all

HIST = "histogram"
COUNTER = "counter"


@dataclass(frozen=True)
class Kpi:
    kpi_id: str
    name: str
    owners: tuple[int, ...]      # KIO numbers, from Table 3's "Related KIO"
    metric: str                  # Prometheus/VictoriaMetrics name
    instrument: str              # HIST or COUNTER
    unit: str                    # Grafana unit id
    target: float                # in the metric's own unit
    direction: str
    baseline_abs: str            # what 100 % (or the baseline) is in reality
    target_text: str             # D1.1 target, as written
    definition: str

    @property
    def slug(self) -> str:
        return self.kpi_id.replace(".", "_")

    def value_expr(self, selector: str = "") -> str:
        """Average (histogram) or total (counter) over the panel's time range."""
        if self.instrument == HIST:
            return (
                f"sum(rate({self.metric}_sum{{{selector}}}[$__range]))"
                f" / clamp_min(sum(rate({self.metric}_count{{{selector}}}[$__range])), 0.001)"
            )
        return f"sum(increase({self.metric}{{{selector}}}[$__range]))"

    def attainment_expr(self, selector: str = "") -> str:
        """Percent of the D1.1 target reached; 100 always means "target met".

        The two directions have to be normalised differently or a KPI that
        improves by going down would read as failing.
        """
        v = self.value_expr(selector)
        if self.direction == LOWER:
            core = f"100 * {self.target} / clamp_min({v}, 0.001)"
        elif self.direction == HIGHER:
            core = f"100 * ({v}) / {self.target}"
        elif self.direction == BINARY:
            core = f"100 * clamp_max({v}, {self.target}) / {self.target}"
        else:
            raise ValueError(self.direction)
        # Without this a KPI whose owning KIOs have no producer running returns
        # zero series, Grafana drops the row, and the KPI vanishes from a list
        # that is meant to show all of them. -1 is mapped to "no producer".
        return f"({core}) or vector(-1)"

    def thresholds(self) -> list[dict]:
        """Green when on target, amber within 20 % of it, red beyond."""
        if self.direction == LOWER:
            return [
                {"color": "green", "value": None},
                {"color": "orange", "value": self.target},
                {"color": "red", "value": self.target * 1.2},
            ]
        return [
            {"color": "red", "value": None},
            {"color": "orange", "value": self.target * 0.8},
            {"color": "green", "value": self.target},
        ]

    def description(self) -> str:
        owners = ", ".join(f"KIO{o}" for o in self.owners)
        arrow = "lower is better" if self.direction != HIGHER else "higher is better"
        return (
            f"**Target: {self.target_text}** ({arrow})\n\n"
            f"{self.definition}\n\n"
            f"Baseline 100% = {self.baseline_abs}.\n\n"
            f"Owning KIO(s) per D1.1 Table 3: {owners}.\n\n"
            f"Metric `{self.metric}`. Values are SIMULATED — no real KIO module "
            f"reports this yet.\n\n"
            f"Empty means the selected KIO does not report this KPI."
        )


PCT = "percent"

KPIS: tuple[Kpi, ...] = (
    Kpi("1.1", "Code generation speed", (2, 3, 4, 7),
        "kio_kpi_codegen_speed_pct_of_baseline", HIST, PCT, 70, LOWER,
        "≈100–120 min per 300–500 LOC feature", "≤70% of baseline (≈70–90 min)",
        "Average end-to-end time from template/prompt initiation to first successful compile of a 300–500 LOC feature."),
    Kpi("1.2", "Issue resolution speed", (2, 3, 4, 7),
        "kio_kpi_issue_resolution_pct_of_baseline", HIST, PCT, 70, LOWER,
        "≈1.0–1.5 working days per issue", "≤70% of baseline (≈0.7 days)",
        "Average elapsed time from defect detection in CI/CD or QA to confirmed resolution and successful re-build merge."),
    Kpi("2.1", "Lifecycle energy reduction", (7, 8, 10),
        "kio_kpi_lifecycle_energy_pct_of_baseline", HIST, PCT, 85, LOWER,
        "conventional pipeline lifecycle energy", "≤85% of baseline (≥15% reduction)",
        "Total energy consumed across a feature's lifecycle — training, inference and build/test/deploy — normalised per delivered function point."),
    Kpi("2.2", "Deployment energy efficiency", (7, 8, 10),
        "kio_kpi_deploy_energy_efficiency_pct_of_baseline", HIST, PCT, 115, HIGHER,
        "un-optimised deployment stack, tokens·s⁻¹·W⁻¹", "≥115% of baseline (≥15% improvement)",
        "Throughput per unit power during inference or code generation (tokens·s⁻¹·W⁻¹)."),
    Kpi("3.1", "Code quality improvement", (4, 7, 9),
        "kio_kpi_code_quality_pct_of_baseline", HIST, PCT, 70, LOWER,
        "adverse-quality composite (complexity, smells, standards)", "≤70% of the adverse-quality measure (≥30% improvement)",
        "Composite of readability, maintainability and standards conformance, from third-party tooling such as a Sonar ruleset. D1.1 measures ADVERSE quality, so lower is better."),
    Kpi("3.2", "Review score increase", (4, 7, 9),
        "kio_kpi_review_score_pct_of_baseline", HIST, PCT, 120, HIGHER,
        "≈3.5 / 5 average peer-review score", "≥120% of baseline (≈4.2 / 5)",
        "Average improvement in peer-review evaluation score — clarity, maintainability, readability, test coverage."),
    Kpi("4.1", "Developer productivity", (1, 7, 13),
        "kio_kpi_dev_productivity_pct_of_baseline", HIST, PCT, 120, HIGHER,
        "≈1 feature / developer / day", "≥120% of baseline (≈1.20 features / developer / day)",
        "Average number of completed coding tasks or features per developer per sprint, normalised by actual working hours."),
    Kpi("5.1", "Time-to-market", (7, 11),
        "kio_kpi_time_to_market_pct_of_baseline", HIST, PCT, 70, LOWER,
        "≈5–7 days per pilot feature", "≤70% of baseline (≈3–5 days)",
        "Average elapsed time from feature design (prompt/template definition) to successful deployment in the AI4SWEng test environment."),
    Kpi("6.1", "Bug-fix time", (2, 7, 11),
        "kio_kpi_bugfix_time_pct_of_baseline", HIST, PCT, 80, LOWER,
        "≈8–12 hours per issue", "≤80% of baseline (≈7–10 hours)",
        "Average elapsed time between bug report creation and a successful fix merged into the main branch."),
    Kpi("6.2", "Customer-reported issues", (2, 7, 11),
        "kio_kpi_customer_reported_issues_pct_of_baseline", HIST, PCT, 80, LOWER,
        "≈5 issues per released feature", "≤80% of baseline (≈4 issues / feature)",
        "Average number of functional or usability issues reported by test users acting as customers, per released pilot feature."),
    Kpi("7.1", "Annual cost", (7, 8, 10),
        "kio_kpi_annual_cost_pct_of_baseline", HIST, PCT, 85, LOWER,
        "≈EUR 100 000 / developer / year", "≤85% of baseline (≈EUR 85 000 / year)",
        "Total software development and maintenance expenditure — developer time, testing effort, rework hours — normalised per FTE. D1.1 measures the remaining cost, so lower is better."),
    Kpi("8.1", "Adoption rate", (13,),
        "kio_kpi_adoption_rate_pct", HIST, PCT, 50, HIGHER,
        "0% of eligible users (no GenAI integration)", "≥50% of active developers within two pilot sprints",
        "Percentage of internal active developers or teams consistently using AI4SWEng GenAI features."),
    Kpi("8.2", "Active usage", (13,),
        "kio_kpi_active_usage_pct", HIST, PCT, 60, HIGHER,
        "0% of developers", "≥60% of developers using a feature at least once per sprint",
        "Percentage of developers actively using AI4SWEng features at least once per sprint. First half of KPI 8.2."),
    Kpi("8.2", "Satisfaction (MOS)", (13,),
        "kio_kpi_satisfaction_mos", HIST, "none", 4.0, HIGHER,
        "MOS ≈3.0 / 5 for standard IDE tools", "≥4.0 / 5",
        "Mean Opinion Score (1–5) from user surveys on usefulness, trust and ease of integration. Second half of KPI 8.2."),
    Kpi("8.3", "Cross-architecture build success", (8,),
        "kio_kpi_cross_arch_build_success_count", COUNTER, "short", 1, BINARY,
        "0 — manual adaptation required for each target", "≥1 validated working target",
        "Whether cross code is generated, compiled and executed on at least one heterogeneous target (FPGA via HLS, ARM via GCC, RISC-V via LLVM) using AI-assisted configuration."),
    Kpi("9.1", "Refactoring reduction", (7, 9),
        "kio_kpi_refactoring_effort_pct_of_baseline", HIST, PCT, 80, LOWER,
        "≈2–3 hours per 300–500 LOC feature", "≤80% of baseline (≈1.5–2 h / feature)",
        "Post-review or post-deployment code refactoring effort per feature, in developer hours or commits."),
    Kpi("9.2", "Technical debt reduction", (7, 9),
        "kio_kpi_technical_debt_pct_of_baseline", HIST, PCT, 80, LOWER,
        "≈1.0–1.2 hours of debt per 100 LOC", "≤80% of baseline (≈0.8 h / 100 LOC)",
        "Estimated technical-debt effort required to fix code smells, complexity issues and maintainability violations, as reported by automated code-quality tools."),
)


def for_kio(n: int) -> tuple[Kpi, ...]:
    """The KPIs D1.1 Table 3 assigns to one KIO, in KPI-id order."""
    return tuple(k for k in KPIS if n in k.owners)


def kio_numbers() -> tuple[int, ...]:
    """Every KIO that Table 3 assigns at least one KPI to."""
    return tuple(sorted({n for k in KPIS for n in k.owners}))


def panel_title(k: Kpi) -> str:
    return f"KPI {k.kpi_id} — {k.name}"
