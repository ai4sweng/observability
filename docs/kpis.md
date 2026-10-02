# Real Project KPIs (D1.1)

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

**[`docs/report/KPI_Metrik_Referansi_v1.3.docx`](report/KPI_Metrik_Referansi_v1.3.docx)** — the full catalog of every KPI
(1.1–9.2) and work-package/task-level metric from the project's official
Project Management Handbook (D1.1), plus the mapping of which KPI belongs to
which KIO.

Six simulator roles are defined via the `KIO_REAL_KPI_ROLE` env var, each
publishing its own metric set in `kio_simulator.py`. Two run by default:
`kio2-sim` (`bugfix`) and `kio7-sim` (`ai-sysdev`).

Each KPI is emitted in the unit D1.1's own "Unit of Measure" column
specifies — which for all but three KPIs is a **percentage of baseline**, with
the SotA IDE/SDK baseline normalised to 100 % (KPI 1.1 is literally "% of
baseline time", target "≤70 %"). Emitting them that way is what makes each
value directly comparable to its D1.1 target with no unit conversion and no
assumed baseline midpoint. The exceptions keep D1.1's own scale: KPI 8.1
(% of eligible users), 8.2 (% of users, and MOS 1–5) and 8.3 (a count).

`scripts/d11_kpis.py` is the single source of truth for every KPI's target,
direction and owning KIOs (D1.1 Table 3 + Table 8); both dashboard generators
read it. Values are still simulated. The roles below marked *not running*
are defined in `kio_simulator.py` but no simulator currently runs them (see
[KIO Simulators](kio-simulators.md#why-only-two)):

- **kio2-sim** (`KIO_REAL_KPI_ROLE=bugfix`, D1.1's "Bug Locate & Fix / LLM
  Debugger"):
  - `kio_kpi_bugfix_time_pct_of_baseline` — KPI 6.1 (Bug-fix time)
  - `kio_kpi_issue_resolution_pct_of_baseline` — KPI 1.2 (Issue resolution speed)
  - `kio_slicing_success_rate` — WP3 task metric (Dynamic slicing success rate, target ≥85%)
  - `kio_kpi_customer_reported_issues_pct_of_baseline` — KPI 6.2 (Customer-reported issues)
- **KIO3 role** — *not running* (`KIO_REAL_KPI_ROLE=nlp-requirements`, D1.1's "NLP → Formal Requirements"):
  - `kio_kpi_codegen_speed_pct_of_baseline` — KPI 1.1 (Code generation speed)
  - `kio_kpi_code_quality_pct_of_baseline` — KPI 3.1 (Code quality improvement)
- **KIO4 role** — *not running* (`KIO_REAL_KPI_ROLE=architecture-to-code`, D1.1's "Architecture-to-Code Planner"):
  - `kio_kpi_codegen_speed_pct_of_baseline`, `kio_kpi_code_quality_pct_of_baseline` (same as the KIO3 role, KPI 1.1 + 3.1)
  - `kio_kpi_review_score_pct_of_baseline` — KPI 3.2 (Review score increase, KIO4 only)
- **kio7-sim** (`KIO_REAL_KPI_ROLE=ai-sysdev`, D1.1's "AI-SysDev" — shared across most
  KPIs (1.1, 1.2, 2.x, 3.x, 4.1, 5.1, 6.x, 7.1, 9.x); only the five where KIO7 is
  a clear primary owner and not covered by another role are simulated):
  - `kio_kpi_dev_productivity_pct_of_baseline` — KPI 4.1 (Developer productivity)
  - `kio_kpi_time_to_market_pct_of_baseline` — KPI 5.1 (Time-to-Market)
  - `kio_kpi_annual_cost_pct_of_baseline` — KPI 7.1 (Annual cost saving)
  - `kio_kpi_refactoring_effort_pct_of_baseline` — KPI 9.1 (Refactoring effort reduction)
  - `kio_kpi_technical_debt_pct_of_baseline` — KPI 9.2 (Technical debt reduction)
- **KIO8 role** — *not running* (`KIO_REAL_KPI_ROLE=green-deploy`, D1.1's "Cross-Architecture /
  Energy-Efficient Deploy" — shares KPI 2.1/2.2 with KIO7/KIO10, sole owner of KPI 8.3):
  - `kio_kpi_lifecycle_energy_pct_of_baseline` — KPI 2.1 (Lifecycle energy reduction)
  - `kio_kpi_deploy_energy_efficiency_pct_of_baseline` — KPI 2.2 (Deployment energy efficiency)
  - `kio_kpi_cross_arch_build_success_count` — KPI 8.3 (Cross-Architecture Build Success Rate)
- **KIO13 role** — *not running* (`KIO_REAL_KPI_ROLE=adoption`, D1.1's "Adoption & Usage Tracking" —
  owner of the only two D1.1 KPIs not shared with any other KIO):
  - `kio_kpi_adoption_rate_pct` — KPI 8.1 (Adoption rate)
  - `kio_kpi_active_usage_pct`, `kio_kpi_satisfaction_mos` — KPI 8.2 (Active usage & satisfaction)

Each `AI4SWENG — KIO*N* Detail` dashboard opens with a "D1.1 Project KPIs —
KIO*N*" row holding that KIO's KPIs per Table 3 ("—" while nothing reports
them). KIO1, KIO9, KIO10 and KIO11 own KPIs too but have no simulator role.
The Overview dashboard shows every KPI against its baseline (green) and
target (red). For KPI versioning and the planned KPI query API, see
[`ROADMAP_KPI_API.md`](ROADMAP_KPI_API.md).
