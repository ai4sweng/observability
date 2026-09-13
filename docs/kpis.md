# Real Project KPIs (D1.1)

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

**[`docs/KPI_Metrik_Referansi_v1.3.docx`](KPI_Metrik_Referansi_v1.3.docx)** — the full catalog of every KPI
(1.1–9.2) and work-package/task-level metric from the project's official
Project Management Handbook (D1.1), plus the mapping of which KPI belongs to
which KIO.

Six KIOs carry D1.1 KPI roles via the `KIO_REAL_KPI_ROLE` env var
(`docker-compose.yml`), each publishing its own metric set in
`kio_simulator.py`. **All six run by default**, so every D1.1 KPI populates
out of `docker compose up`.

Each KPI is emitted in the unit D1.1's own "Unit of Measure" column
specifies — which for all but three KPIs is a **percentage of baseline**, with
the SotA IDE/SDK baseline normalised to 100 % (KPI 1.1 is literally "% of
baseline time", target "≤70 %"). Emitting them that way is what makes each
value directly comparable to its D1.1 target with no unit conversion and no
assumed baseline midpoint. The exceptions keep D1.1's own scale: KPI 8.1
(% of eligible users), 8.2 (% of users, and MOS 1–5) and 8.3 (a count).

`scripts/d11_kpis.py` is the single source of truth for every KPI's target,
direction and owning KIOs (D1.1 Table 3 + Table 8); both dashboard generators
read it. Values are still simulated:

- **kio2-sim** (`KIO_REAL_KPI_ROLE=bugfix`, D1.1's "Bug Locate & Fix / LLM
  Debugger" — exactly the job FocusTracer does):
  - `kio_kpi_bugfix_time_pct_of_baseline` — KPI 6.1 (Bug-fix time)
  - `kio_kpi_issue_resolution_pct_of_baseline` — KPI 1.2 (Issue resolution speed)
  - `kio_slicing_success_rate` — WP3 task metric (Dynamic slicing success rate, target ≥85%)
  - `kio_kpi_customer_reported_issues_pct_of_baseline` — KPI 6.2 (Customer-reported issues)
- **kio3** (`KIO_REAL_KPI_ROLE=nlp-requirements`, D1.1's "NLP → Formal Requirements"):
  - `kio_kpi_codegen_speed_pct_of_baseline` — KPI 1.1 (Code generation speed)
  - `kio_kpi_code_quality_pct_of_baseline` — KPI 3.1 (Code quality improvement)
- **kio4** (`KIO_REAL_KPI_ROLE=architecture-to-code`, D1.1's "Architecture-to-Code Planner"):
  - `kio_kpi_codegen_speed_pct_of_baseline`, `kio_kpi_code_quality_pct_of_baseline` (same as kio3, KPI 1.1 + 3.1)
  - `kio_kpi_review_score_pct_of_baseline` — KPI 3.2 (Review score increase, KIO4 only)
- **kio7** (`KIO_REAL_KPI_ROLE=ai-sysdev`, D1.1's "AI-SysDev" — shared across most
  KPIs (1.1, 1.2, 2.x, 3.x, 4.1, 5.1, 6.x, 7.1, 9.x); only the five where KIO7 is
  a clear primary owner and not already covered by the other three KIOs are simulated):
  - `kio_kpi_dev_productivity_pct_of_baseline` — KPI 4.1 (Developer productivity)
  - `kio_kpi_time_to_market_pct_of_baseline` — KPI 5.1 (Time-to-Market)
  - `kio_kpi_annual_cost_pct_of_baseline` — KPI 7.1 (Annual cost saving)
  - `kio_kpi_refactoring_effort_pct_of_baseline` — KPI 9.1 (Refactoring effort reduction)
  - `kio_kpi_technical_debt_pct_of_baseline` — KPI 9.2 (Technical debt reduction)
- **kio8** (`KIO_REAL_KPI_ROLE=green-deploy`, D1.1's "Cross-Architecture /
  Energy-Efficient Deploy" — shares KPI 2.1/2.2 with KIO7/KIO10, sole owner of KPI 8.3):
  - `kio_kpi_lifecycle_energy_pct_of_baseline` — KPI 2.1 (Lifecycle energy reduction)
  - `kio_kpi_deploy_energy_efficiency_pct_of_baseline` — KPI 2.2 (Deployment energy efficiency)
  - `kio_kpi_cross_arch_build_success_count` — KPI 8.3 (Cross-Architecture Build Success Rate)
- **kio13** (`KIO_REAL_KPI_ROLE=adoption`, D1.1's "Adoption & Usage Tracking" —
  owner of the only two D1.1 KPIs not shared with any other KIO):
  - `kio_kpi_adoption_rate_pct` — KPI 8.1 (Adoption rate)
  - `kio_kpi_active_usage_pct`, `kio_kpi_satisfaction_mos` — KPI 8.2 (Active usage & satisfaction)

Note: kio3/kio4's `task_type` was renamed in 2026-08 (`test-generation`/`debug`
→ `nlp-requirements`/`architecture-to-code`) to match D1.1's actual KIO3/KIO4
identities — D1.1's KPI assignments are keyed to those real roles, not to
whatever arbitrary name the simulator first picked. kio7/kio8/kio13 were added
the same way, directly against D1.1's corresponding identities. kio7's
`ai-sysdev` role deliberately left KPI 2.1/2.2 out (see the comment in
`kio_simulator.py`) — those two are implemented here under kio8 instead,
D1.1's clearer/more specific owner.

The "D1.1 Real Project KPIs" sections on the KIO Detail dashboard show these
metrics (populated only when the selected KIO has the matching
`KIO_REAL_KPI_ROLE`, "N/A" otherwise — see [Real vs. Simulated Data Map](kio-simulators.md#real-vs-simulated-data-map)). Every
KIO D1.1 assigns a KPI to (KIO2, KIO3, KIO4, KIO7, KIO8, KIO13) has its role
defined, covering all 16 of D1.1's KPIs (1.1–9.2) via simulated data — but only
the KIOs actually running (kio2-sim and kio3 by default) emit; uncomment the
others to populate their panels.

For the plan to surface these KPIs on the opening dashboard with baseline/
target attainment, versioning, and a query API, see
[`ROADMAP_KPI_API.md`](ROADMAP_KPI_API.md).
