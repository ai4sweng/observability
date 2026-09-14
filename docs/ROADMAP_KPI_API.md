# Roadmap — GA KPI Scoreboard, Versioning & KPI Query API

Scope: the four items raised in the consortium meeting.

> 1. Opening page dashboard GA KPIs and target levels needs to be shown
> 2. Versioning needs to be added
> 3. KIO owners or anybody shall be able to collect previous data with API by
>    providing KIO number and time interval
> 4. Then merge and integrate with T3.4 (Philippe)

Normative sources: **D1.1 Project Management Handbook** (§2.6 KPIs & targets,
Table 8 global KPIs, Table 9 WP/task KPIs, Table 10 milestone tracking, §3.3
Min/Baseline/Max bands, §3.9 Documentation & Version Control Standards) and the
**Observability Integration Guide v2.2**.

---

## Decisions taken (2026-09-09)

Four scope questions were settled before implementation; the phases below
reflect them.

| # | Decision |
|---|---|
| D1 | **Opening page carries the 16 global KPIs only** (D1.1 Table 8, KPI 1.1–9.2). WP/task-level KPIs (Table 9) are out of scope for the opening page; if they are wanted later they get their own dashboard. |
| D2 | **Versioning scope is narrowed to what a viewer can see**: the observability stack's own version must be visible, and each KIO's own version must be visible **on the KIO Detail page**. `SERVICE_VERSION` is already emitted per KIO — it just needs surfacing. Catalog and API-contract versioning are not part of the ask; `/v1` in the API path is kept anyway because it costs nothing. |
| D3 | **KPI panels are per-KIO, not one-size-fits-all**: which KPIs a KIO reports differs by KIO (KIO2's set is not KIO3's set). **Resolved** — the authoritative mapping is "Table 3. KPIs: baseline values and targets", diffed against the catalog in P0 below. |
| D4 | **On the KIO Detail page, the KPI section goes at the very top** — above the ops panels. Empty data for a KIO that has no producer running is acceptable and does not need to be hidden or padded, so the default `docker-compose.yml` KIO set stays as it is (only `kio2-sim` and `kio3` up by default). |
| D5 | **The API is not ours to build.** Item 3 is owned by Hemant: [issue #3](https://github.com/ai4sweng/observability/issues/3) covers the metric registry + `GET /api/metrics` for operational telemetry, with a companion issue for the D1.1 KPI Query API. **Our scope is the Grafana side only** — items 1 and 2, i.e. P1a, P1b and the dashboard half of P4. P2 and P3 below are retained as context and as the interface we consume, not as our work. |
| D6 | **No new service on our side**, following from D5. This rules out the catalog-exporter design originally proposed in P1a; targets are carried in dashboard JSON instead — see the revised P1a and the drift trade-off recorded there. |

**No blocking input outstanding.** D3's mapping arrived with Table 3 and is
reconciled against the catalog in P0; work can start.

---

## 0. Where we stand today

| Layer | Today | Gap vs. the four items |
|---|---|---|
| Telemetry ingest | `otel-collector` to VictoriaMetrics / VictoriaLogs / Tempo; KIO sims emit 16 D1.1 KPI metrics under 6 `KIO_REAL_KPI_ROLE` roles | — |
| Overview dashboard | Ops metrics only (rate, errors, latency, tokens, cost, energy, heartbeat) | **No GA KPI / target section at all** (item 1) |
| KIO Detail dashboard | Per-role D1.1 KPI stat panels, values only | No baseline/target comparison, per-KIO only |
| KPI catalog | `KPIinterface` to `ai4sweng` pip package, `ai4sweng/metrics.json`: id, name, definition, unit, baseline, target, `kios[]`, `otel[]` | Baseline/target are **prose strings**, not machine-comparable; `kios[]` records D1.1 co-ownership, not who actually reports (D3) |
| Read/query path | None. Only Grafana UI + raw VictoriaMetrics on `:8428` | **No API** (item 3) |
| Versioning | `observability` git tag `v1.0.2`; `ai4sweng` 0.1.0 (untagged); per-KIO `SERVICE_VERSION` emitted but displayed nowhere | Neither the stack version nor the KIO version is visible to a dashboard viewer (item 2 / D2) |
| Integration surface | `remote-kio/INTEGRATION.md` (write path: how a KIO pushes) | No read-path contract for a consumer like T3.4 (item 4) |

Two structural facts that shape everything below:

1. **The catalog already exists and is already the single source of truth.**
   `metrics.json` is where every KPI's identity, unit, owning KIOs and OTel
   instrument live. Items 1, 2 and 3 all need the *same* data out of it. So the
   catalog gets extended once, and the dashboard, the API and the client all
   read that one extension — no target values duplicated per Grafana panel.
2. **D1.1 states targets as "% of baseline", the telemetry emits absolute
   units.** D1.1 KPI 6.1 target is "about 80 % of baseline (7–10 h)"; the
   simulator emits `kio.bugfix.duration_hours` in hours. Nothing can compute
   "are we on target?" until the numeric baseline is declared machine-readably.
   **This is the prerequisite for both item 1 and item 3**, and it is P0 below.

---

## P0 — Machine-readable targets in the catalog *(prerequisite)*

Repo: `KPIinterface`. Without this, item 1 becomes 16 hand-tuned Grafana
thresholds that silently drift from D1.1, and item 3 can only return raw
numbers with no verdict.

Extend each KPI object in `ai4sweng/metrics.json` with a `targets` block,
keeping the existing prose `baseline` / `unit` / `target` fields untouched (they
are the human-facing D1.1 quotes and stay authoritative for reporting):

```json
{
  "id": "6.1",
  "name": "Bug-fix time",
  "baseline": "100% (~8-12 hours per issue)",
  "target": "~80% of baseline (~7-10 hours)",
  "targets": {
    "direction": "lower_is_better",
    "baseline_absolute": { "value": 10.0, "min": 8.0, "max": 12.0, "unit": "h" },
    "target_pct_of_baseline": 80.0,
    "bands": { "min": 90.0, "baseline": 100.0, "max": 80.0 },
    "d11_ref": "Table 8, KPI 6.1"
  }
}
```

- `direction` — `lower_is_better` (1.1, 1.2, 2.1, 3.1, 5.1, 6.1, 6.2, 7.1, 9.1,
  9.2) or `higher_is_better` (2.2, 3.2, 4.1, 8.1, 8.2, 8.3).
- `baseline_absolute` — the SotA IDE/SDK figure from D1.1's Baseline column, in
  the same unit the OTel instrument emits. This is what turns an emitted
  `9.4 h` into `94 % of baseline`.
- `bands` — D1.1 §3.3 Figure 2 Min / Baseline / Max target range, so the status
  is a three-band verdict, not a pass/fail coin flip.
- `d11_ref` — traceability back to the handbook table (D1.1 §3.6 requires
  documented links).

Status is then derived uniformly, everywhere:

```
pct_of_baseline = value / baseline_absolute.value * 100
status = on_target | in_band | off_target | no_data
```

### The KIO-to-KPI mapping (D3 — resolved 2026-09-09)

The authoritative mapping is **"Table 3. KPIs: baseline values and targets"**,
supplied by the project side. It is a condensed revision of D1.1 Table 8: the
baselines and targets agree with Table 8, and the `Related KIO` column
supersedes the ownership assignments in D1.1 Table 7.

Diffed against the `kios[]` arrays already in `ai4sweng/metrics.json`, the
result is unusually clean — **the only discrepancy is a systematic KIO1
over-attribution**:

| KPI | `metrics.json` today | Table 3 | Fix |
|---|---|---|---|
| 1.1, 1.2 | KIO1, KIO2, KIO3, KIO4, KIO7 | KIO2, KIO3, KIO4, KIO7 | drop KIO1 |
| 2.1, 2.2, 7.1 | KIO1, KIO7, KIO8, KIO10 | KIO7, KIO8, KIO10 | drop KIO1 |
| 3.1, 3.2 | KIO1, KIO4, KIO7, KIO9 | KIO4, KIO7, KIO9 | drop KIO1 |
| **4.1** | KIO1, KIO7, KIO13 | KIO1, KIO7, KIO13 | **unchanged — correct** |
| 5.1 | KIO1, KIO7, KIO11 | KIO7, KIO11 | drop KIO1 |
| 6.1, 6.2 | KIO1, KIO2, KIO7, KIO11 | KIO2, KIO7, KIO11 | drop KIO1 |
| 8.1, 8.2 | KIO1, KIO13 | KIO13 | drop KIO1 |
| 8.3 | KIO1, KIO8 | KIO8 | drop KIO1 |
| 9.1, 9.2 | KIO1, KIO7, KIO9 | KIO7, KIO9 | drop KIO1 |

Every other KIO assignment matches exactly, and no KPI in Table 3 is missing
from `metrics.json`. Notably, the one KIO1 entry the `KPIinterface` README
records as a deliberate keep-as-is decision — KPI 4.1 — is **confirmed correct
by Table 3**; the other fifteen were over-attribution from reading D1.1 Table 7's
"KIO1: All KPIs, starting with KPI 1.1".

**This simplifies P0**: the two-field `kios` / `reporting_kios` design proposed
earlier is dropped. Table 3 answers both questions at once, so there is one
field, `kios[]`, corrected per the table above. One field, one source, no
divergence to keep in sync.

### Resulting per-KIO KPI sets

This is the panel set for each KIO Detail page (P1b):

| KIO | KPIs | Count |
|---|---|---|
| KIO1 | 4.1 | 1 |
| KIO2 | 1.1, 1.2, 6.1, 6.2 | 4 |
| KIO3 | 1.1, 1.2 | 2 |
| KIO4 | 1.1, 1.2, 3.1, 3.2 | 4 |
| KIO7 | 1.1, 1.2, 2.1, 2.2, 3.1, 3.2, 4.1, 5.1, 6.1, 6.2, 7.1, 9.1, 9.2 | **13** |
| KIO8 | 2.1, 2.2, 7.1, 8.3 | 4 |
| KIO9 | 3.1, 3.2, 9.1, 9.2 | 4 |
| KIO10 | 2.1, 2.2, 7.1 | 3 |
| KIO11 | 5.1, 6.1, 6.2 | 3 |
| KIO13 | 4.1, 8.1, 8.2 | 3 |
| **KIO5, KIO6, KIO12** | **none** | **0** |

Two consequences for the dashboards:

- **KIO7 carries 13 of the 16 KPIs.** Its KPI row is by far the largest and
  needs a layout that does not push everything else off-screen — a compact
  multi-column stat grid rather than 13 full-width panels.
- **KIO5, KIO6 and KIO12 have no global KPI at all.** Their KIO Detail pages
  must show an explicit *"no global KPI assigned (Table 3)"* note, not an empty
  row that reads as a data outage. Note that D1.1 Table 7 *did* assign KPIs to
  KIO6 (9.2) and KIO12 (6.2) — see the open question on Table 3 vs Table 7.

Also in P0 (same PR, same file):

- `d11_source` (e.g. `"D1.1 v1.01-v2-22.12-v6"`) at the top of `metrics.json`,
  purely as a provenance string for the targets. Full catalog SemVer versioning
  is **not** in scope per D2; the field is one line and keeps the targets
  traceable to the handbook revision they were copied from.
- KPI 8.2 keeps its two instruments (`usage_pct`, `mos_score`) — each needs its
  own `targets` block (at least 60 % and at least 4.0/5), so `targets` is keyed
  per `otel_key` for multi-instrument KPIs.
- **KPI 3.1 direction — now settled by Table 3, and it exposes a real bug.**
  Table 3 states the target as "≤ 70 % of the **adverse-quality** measure", so
  KPI 3.1 is unambiguously `lower_is_better` over a defect-style composite
  (complexity, code smells, standards violations). The simulator, however,
  emits `kio.code_quality.score_pct` — a *score*, which reads as
  higher-is-better, and the KIO Detail panel presents it that way today. As it
  stands the dashboard would show a rising line as "improving" while the KPI
  definition means the opposite. Fix one of the two, explicitly:
  (a) redefine the emitted metric as an adverse-quality measure and rename it
  (`kio.code_quality.adverse_score_pct`), or (b) keep the score and record in
  the catalog that KPI 3.1 is measured as `100 - score` before comparison.
  (a) is cleaner and matches Table 3's wording; either way it must be written
  down, because a silently inverted KPI is worse than a missing one.
- Regenerate `ai4sweng/__init__.pyi` (`python scripts/gen_stubs.py`) and extend
  `tests/test_kpi.py` with a schema test: every KPI has `targets`, every
  `direction` is one of the two literals, every `baseline_absolute.unit` matches
  its `otel[].unit`. `tests/test_stubs.py` already fails on stub drift.

Deliverable: `ai4sweng` **0.2.0**, git-tagged.

---

## P1a — Item 1: GA KPI scoreboard on the opening page

Repo: `observability`. Target: `grafana/dashboards/ai4sweng-overview.json`.
Per D1.1 Table 8 and decision D1: the 16 global KPIs (1.1–9.2), nothing else.

**Approach (revised per D6): targets live in the dashboard JSON.**
The original design here was a `kpi-catalog-exporter` publishing the catalog's
baselines and targets into VictoriaMetrics as constant gauges, so panels, the
API and the client would all read one source. D5/D6 rule that out — we are not
adding a service. So each KPI panel carries its own baseline and target:

```promql
# KPI 6.1 — bug-fix time as % of baseline (baseline 10 h, from D1.1 Table 8)
100 * (
  sum(rate(kio_bugfix_duration_hours_sum{kio_id=~"$kio_id"}[$__rate_interval]))
  / clamp_min(sum(rate(kio_bugfix_duration_hours_count{kio_id=~"$kio_id"}[$__rate_interval])), 0.001)
) / 10
```

...with the target (80) and the D1.1 Min/Baseline/Max bands expressed as the
panel's `thresholds.steps`, and the KPI id, prose baseline and prose target in
the panel description so a viewer can see where the number came from.

**The trade-off, and the way out — which is now much closer than it was.**
Hardcoding means baselines and targets exist in two places:
`ai4sweng/metrics.json` (P0) and 16 Grafana panels. A D1.1 target revision has
to be applied twice, and nothing fails loudly if it is only applied once.

The clean fix is for the targets to arrive **as data**, and PR #5 put that seam
in place: `/api/kpis/catalog` (referenced in `metrics_api/app.py` but not yet
built) is planned to serve the KPI family *with* its D1.1 metadata. Grafana can
consume a REST endpoint like that through the **Infinity datasource**, and
adding it is genuinely within our scope — no new service, just:

```yaml
# docker-compose.yml (grafana service) — the env var already exists
GF_INSTALL_PLUGINS: victoriametrics-logs-datasource,yesoreyeram-infinity-datasource
```

plus one entry in `grafana/provisioning/datasources/datasources.yml`. The
precedent is already there: the VictoriaLogs datasource is installed exactly
this way.

So P1a has two stages:

1. **Now** — targets in panel config, as above. Unblocks item 1 immediately
   without waiting on the API.
2. **When `/api/kpis/catalog` ships** — add the Infinity datasource, read
   baselines/targets/direction from it, and compute status from data. This also
   unlocks the dynamic per-KIO panel set in P1b, collapsing its interim
   row-per-KIO layout into one repeated row.

Mitigation for stage 1, so the duplication cannot rot silently: a small guard
script under `scripts/` that parses the dashboard JSON and asserts every
hardcoded baseline and target matches `ai4sweng/metrics.json`, run in CI. Cheap,
and it converts silent drift into a failing check — the same pattern
`KPIinterface`'s `tests/test_stubs.py` already uses for stub drift, and the same
pattern `metrics_api`'s own `test_registry_drift.py` uses against the simulator.

New dashboard section, placed **above** the existing ops rows so it is the first
thing on the opening page:

1. **Row: `GA KPIs (D1.1) — Attainment vs. Target`**
2. **Header stats**: `On target X / 16`, `In band Y`, `Off target Z`,
   `No producer N`, plus milestone progress against D1.1 Table 10
   (M24 at least 20–50 %, M36 at least 75 %, M42 at least 90 % of KPI targets
   achieved).
3. **The scoreboard table** — one row per KPI (all 16, always all 16):
   `KPI ID | Name | Owning KIO(s) | Current | Unit | % of baseline | Target |
   Status | Producer`. Status cell colour-mapped to D1.1's three bands
   (green `on_target` / amber `in_band` / red `off_target` / grey `no_data`).
4. **Bar gauge**: `% of baseline` per KPI with the target as a threshold marker
   — the "target levels needs to be shown" ask, made visual at a glance.
5. **Footer text/stat panel**: catalog version, `d11_source`, stack version,
   dashboard version (item 2's visible surface).

Per D4, the default `docker-compose.yml` KIO set is left alone (`kio2-sim` and
`kio3` only), so on a fresh `docker compose up` most rows legitimately read
`no_data`. That is the honest state, not a bug — but it has to *look*
deliberate, so the `no_data` cell renders as `No producer running` with the
owning KIO named, and the `Producer` column distinguishes `SIMULATED` from
`REAL` so a demo is never mistaken for real attainment. The existing "all
SIMULATED" panel-title convention on the KIO Detail dashboard carries over.

---

## P1b — Items 1 + D3/D4: per-KIO KPI section, at the top of KIO Detail

Repo: `observability`. Target: `grafana/dashboards/ai4sweng-kio.json`.

Today the D1.1 KPI panels on this dashboard are **near the bottom**, split into
six hardcoded role rows (`kio2-sim (bugfix role)`, `kio3/kio4`, `kio7`, `kio8`,
`kio13`), and every row renders for every selected KIO — so viewing KIO3 shows
five rows of `N/A` belonging to other KIOs. D3 and D4 change both of those.

**Restructure:**

1. **Move the KPI content to the very top of the dashboard**, above the `LLM` /
   `Task type` / `Requests/sec` stat strip. New first row:
   `D1.1 KPIs — $kio_id`.
2. **Make the panel set follow the selected KIO** rather than showing all six
   role groups. The six role rows collapse into one data-driven row.
3. Keep the KIO's own version visible in the same header area (see P4/D2).

**A constraint worth stating plainly, because it decides the layout.** In
Grafana you can have *either* a dynamically repeated panel set *or* per-KPI
baselines and targets baked into panel config — **not both**, once D6 removes
the exporter:

- A dynamic set means one panel definition repeated over a query variable
  (`repeat: kpi_id`). One definition can hold only one `thresholds.steps` block,
  but each KPI has its own baseline divisor and its own target — so a repeated
  panel cannot carry them.
- Per-KPI targets in panel config therefore require one static panel per KPI.
- The only way to have both is for the target values to arrive **in the data**
  (the exporter, or Hemant's registry behind the KPI API), so status can be
  computed in PromQL instead of configured per panel. That is P1a's option 1.

So the layout that works today:

1. **Top row per KIO**, titled `D1.1 KPIs — KIO2`, `D1.1 KPIs — KIO3`, …, one
   row per KIO listed in Table 3, containing exactly that KIO's KPI panels
   (see the per-KIO sets in P0) with that KPI's own baseline and thresholds.
   KIO5/KIO6/KIO12 get a single note panel instead of a row of blanks, and
   KIO7's 13 KPIs go in a compact stat grid rather than 13 wide panels.
2. Rows sit **above** the `LLM` / `Task type` / `Requests/sec` stat strip, per
   D4. Only the row matching the selected `$kio_id` has data; the others read
   `no_data`, which D4 accepts. Rows other than the selected KIO's are
   **collapsed by default**, so the page opens on one populated row rather than
   six mostly-empty ones.
3. When P1a's option 1 lands, all of these collapse into **one** dynamic row and
   the per-KIO duplication disappears. The row-per-KIO shape is the interim, not
   the destination — worth writing down so it is not mistaken for the design.

This is a reorganization of panels that already exist (the six role rows near
the bottom of the dashboard today), plus correcting them to be
mapping-driven rather than role-guessed, plus adding the baseline/target/band
comparison they currently lack.

---

## P2 — Item 3: the KPI Query API — *owned by Hemant; operational half already merged*

> **Per D5 this is not our work**, and the first half has already landed:
> `metrics_api/` merged to `master` in PR #5 (`ba46346`) — registry +
> `GET /api/metrics` + `GET /api/metrics/catalog` + `/health`, on port `8081`
> via `METRICS_API_PORT`, unauthenticated (trusted LAN), with ~1500 lines of
> tests including a drift test against `kio_simulator.py`. **Its decisions
> govern**, not the sketch below.
>
> **What landed matters for our side in three ways:**
>
> 1. `metrics_api/registry_data.py` is **generated** from the simulator
>    (`scripts/generate_metric_registry.py`) and already covers all 33 metrics
>    including `family: "kpi"` — every D1.1 KPI metric, with instrument, unit,
>    valid labels, `required_group_by`, `supports_source` and producer `roles`.
>    So the registry duplication concern is resolved in the better direction:
>    it is machine-generated with a drift test, not a hand-kept parallel file.
> 2. **Baselines, targets and directions are deliberately not in it yet.**
>    `registry_data.py`'s docstring states they will be "layered on separately
>    in `registry.py`" from `docs/KPI_Metrik_Referansi_v1.3.docx`, and the
>    `/api/metrics/catalog` docstring already points at a
>    **`/api/kpis/catalog`** that does not exist yet. That endpoint is the
>    companion issue, and it is exactly the seam P1a needs.
> 3. `registry.py`'s `roles` field is the **producer** mapping (derived from
>    `KIO_REAL_KPI_ROLE` — which KIO *emits* a metric), which is a different
>    question from Table 3's **ownership** mapping (which KIOs a KPI *belongs
>    to*). Both are needed: Table 3 decides which panels exist per KIO,
>    `roles` decides which of them can have data. Neither substitutes for the
>    other.
>
> **Coordination ask, now concrete:** when `/api/kpis/catalog` is built, take
> the KIO mapping and the baseline/target values from **Table 3**, not from
> `KPI_Metrik_Referansi_v1.3.docx` — the v1.3 reference predates Table 3 and
> very likely carries the same KIO1 over-attribution found in
> `ai4sweng/metrics.json` (see P0). If the API and our catalog are seeded from
> different revisions, the dashboard and the API will disagree about who owns
> what.

Original sketch, for reference. This is the headline deliverable:
*"KIO owners or anybody shall be able to collect previous data by providing KIO
number and time interval."*

**Stack**: FastAPI + uvicorn (same pattern already proven in
`orchestrator/workflow_api.py`), read-only, depending on `ai4sweng>=0.2.0` for
the catalog. Port `8090`, overridable as `KPI_API_PORT` in `.env.example` —
consistent with how every other port in this repo is centralized.

**It is a facade, not a datastore.** It composes VictoriaMetrics
(`:8428/api/v1/query_range`, Prometheus-compatible), VictoriaLogs
(`:9428`, LogsQL) and Tempo (`:3200`, TraceQL) and annotates every result with
catalog metadata. No new database, no duplicated retention policy.

### Endpoints (all under `/v1`)

| Endpoint | Purpose |
|---|---|
| `GET /v1/health` | Liveness + reachability of VM / VL / Tempo |
| `GET /v1/catalog` | All 16 KPIs: metadata, numeric targets, owning KIOs, `catalog_version` |
| `GET /v1/kios` | Declared KIOs, live/stale (from `kio_heartbeat`), `service.version`, owned KPI ids |
| **`GET /v1/kios/{kio_id}/kpis`** | **The headline call.** Every KPI that KIO owns over `[from, to]`: series + aggregates + baseline/target + status |
| `GET /v1/kios/{kio_id}/kpis/{kpi_id}` | One KPI, one KIO, full resolution |
| `GET /v1/kpis/{kpi_id}` | One KPI across every owning KIO (cross-KIO comparison) |
| `GET /v1/kios/{kio_id}/metrics` | Raw Integration-Guide §2.1 contract metrics (requests, duration, errors, tokens, cost, sessions) |
| `GET /v1/kios/{kio_id}/logs` | VictoriaLogs passthrough, LogsQL pre-scoped to `kio.id` |
| `GET /v1/kios/{kio_id}/traces` | Tempo passthrough, TraceQL pre-scoped to the KIO |

**Parameters**, uniform across every time-ranged endpoint:

- `kio_id` — accepts `KIO2`, `kio2`, `kio2-sim` and bare `2`. The meeting said
  "providing KIO number"; a KIO owner should not have to know our container
  naming.
- `from` / `to` — ISO-8601 (`2026-06-01T00:00:00Z`) **or** relative
  (`from=now-7d`, `to=now`). Default: last 24 h.
- `step` — optional; auto-derived from the range (target about 500 points) when
  omitted, so a naive year-long query cannot blow up.
- `agg` — `mean` (default), `p50`, `p95`, `p99`, `min`, `max`, `last`, `sum`.
- `format` — `json` (default) or `csv`. CSV matters: D1.1 §2.9 has WP leaders
  submitting KPI data on standard templates two weeks before each milestone
  review — a CSV they can paste is the difference between the API being used
  and being ignored.
- `include` — `series,aggregate,target` (default all); `aggregate`-only keeps a
  milestone report response small.

### Response shape

```json
{
  "kio_id": "KIO2",
  "range": { "from": "2026-06-01T00:00:00Z", "to": "2026-09-01T00:00:00Z", "step": "1h" },
  "catalog_version": "0.2.0",
  "api_version": "1.0.0",
  "d11_source": "D1.1 v1.01-v2-22.12-v6",
  "kpis": [
    {
      "id": "6.1",
      "name": "Bug-fix time",
      "definition": "Average elapsed time between bug report creation and successful fix merged into the main branch.",
      "otel_metric": "kio.bugfix.duration_hours",
      "unit": "h",
      "data_source": "SIMULATED",
      "aggregate": { "mean": 9.4, "p95": 11.2, "last": 9.1, "samples": 2160 },
      "baseline": { "value": 10.0, "unit": "h", "prose": "100% (~8-12 hours per issue)" },
      "target": { "pct_of_baseline": 80.0, "direction": "lower_is_better", "prose": "~80% of baseline (~7-10 hours)" },
      "attainment": { "pct_of_baseline": 94.0, "status": "in_band" },
      "series": [ { "t": "2026-06-01T00:00:00Z", "v": 9.8 } ]
    }
  ],
  "warnings": ["KPI 6.2 has no data in the requested range (no producer running)"]
}
```

Design commitments worth stating up front, because they are what make it
usable by someone outside this repo:

- **Never silently omit a KPI.** A KIO's KPI with no data comes back with
  `status: "no_data"` and a `warnings` entry, not absent from the array. A
  consumer diffing two responses must not see a KPI appear and disappear.
- **`data_source: SIMULATED | REAL`** on every KPI, derived the same way the
  dashboards already distinguish it. A milestone report must never quote
  simulated attainment as real.
- **Auto-generated OpenAPI at `/docs` and `/openapi.json`** — FastAPI gives
  this free, and it *is* the "helper API" discoverability the meeting asked
  for. It also becomes the machine-readable contract handed to T3.4 (item 4).
- **Optional bearer auth** (`KPI_API_TOKEN`), off by default for local use,
  mirroring the collector's existing `bearertokenauth` pattern in
  `.env.example`. Read-only, but "anybody" in the meeting note means anybody in
  the consortium, not on the internet.
- **Caps**: max range, max points per response, max concurrent upstream
  queries — a facade in front of a shared store needs them.

Also in P2: `docker-compose.yml` service block (127.0.0.1-bound by default like
the rest), `Dockerfile`, `requirements.txt`, `.env.example` entries, a README
section, and pytest coverage under `tests/kpi_api_tests/` (catalog resolution,
`kio_id` normalization, relative-time parsing, status/band computation, a mocked
VictoriaMetrics round-trip).

---

## P3 — Item 3 completed: reporting, export, client

- **`GET /v1/kios/{kio_id}/report?milestone=M24`** — a milestone-shaped rollup
  keyed to D1.1 Table 10 (M6/M12/M24/M36/M42): every owned KPI's aggregate over
  that milestone window, attainment %, band status, and the milestone's own
  expected threshold (M24 at least 20–50 %, M36 at least 75 %, M42 at least
  90 %). JSON or CSV. This is the endpoint the Quality Manager actually needs;
  the raw series endpoint is the one KIO owners need. Ship both.
- **`GET /v1/report?milestone=M24`** — the same, consortium-wide, all 16 KPIs.
- **Python client in the `ai4sweng` package** — `KPI.query(...)`, so the package
  the KIO teams already install for the *write* path (`.record()`) becomes the
  *read* path too. One dependency, both directions:
  ```python
  from ai4sweng import KPI
  df = KPI.KIO2.bug_fix_time.history(since="now-30d")     # one KPI
  rep = KPI.report(kio="KIO2", milestone="M24")            # milestone rollup
  ```
  This is the concrete answer to "we want more of an API than the interface" —
  the interface is not replaced, it grows a read side and an HTTP surface.
- `ai4sweng` **0.3.0**, tagged.

---

## P4 — Item 2: versioning (scope per D2)

D1.1 §3.9 requires version metadata on every artefact, branch-based Git flow
(`main`/`dev`/`feature/*`/`release/*`), commits referencing Task/Deliverable/KPI,
and automated tagging. Per D2 the meeting's ask is the **visible** part of that:
the stack's own version, and each KIO's version on the KIO Detail page.

### In scope

| Layer | What gets versioned | Where it becomes visible |
|---|---|---|
| **Stack release** | `observability` git tags (continuing from `v1.0.2`), a `VERSION` file, `CHANGELOG.md`, dashboard JSON `version` field bumped per change | A stat/text panel on **both** dashboards showing the stack version + dashboard version, plus `GET /v1/health` |
| **Producers (KIOs)** | Per-KIO `SERVICE_VERSION` — **already emitted** as the `service.version` resource attribute by `kio_simulator.py`, currently displayed nowhere | A `KIO version` stat panel in the **KIO Detail header row** (next to `LLM` / `Task type`, alongside the new KPI section from P1b), and the `service_version` column in the Overview `KIO inventory` table + `GET /v1/kios` |

The producer half is the one with real diagnostic value: when a KPI number moves,
`service.version` is what tells you which KIO build produced it. The plumbing is
already there — this is a dashboard-side change plus confirming the attribute
survives the collector's resource handling into VictoriaMetrics as a queryable
label (verify before building the panel; if it does not, it needs promoting to a
metric label in `kio_simulator.py`).

### Deliberately out of scope

- **KPI catalog SemVer.** Not asked for. `d11_source` (P0) stays as a one-line
  provenance string, but no `catalog_version` label on the target gauges. The
  consequence, recorded here so it is a known trade-off rather than an
  oversight: if a D1.1 target is later revised, historical attainment is
  recomputed against the *new* target, and "we hit 75 % at M24" is no longer
  independently reproducible. D1.1 §2.7 does anticipate the KPI framework being
  refined, so this may come back — the exporter design in P1a leaves room to add
  the label later without reworking anything.
- **API contract SemVer / deprecation policy.** Not asked for. The `/v1` path
  prefix and `api_version` field are kept regardless, because they cost nothing
  now and adding a prefix to a URL that partners have already integrated against
  costs a lot later.

Deliverables: `VERSION` + `CHANGELOG.md` in the `observability` repo, the two
dashboard version panels, the KIO-version panel, and **v1.1.0** as the release
carrying items 1–3.

---

## P5 — Item 4: merge & integrate with T3.4 *(gated — see open questions)*

Preparation that is useful regardless of how T3.4 turns out to be shaped:

1. **A versioned read-path contract doc** — `docs/KPI_API_CONTRACT.md`, the read
   counterpart to the existing write-path `remote-kio/INTEGRATION.md`, with the
   OpenAPI spec as its normative annex.
2. **A consumable client** — the `ai4sweng` package from P3, installable via
   `pip install "ai4sweng[otel] @ git+..."`, already the consortium's install
   path.
3. **A deployable image** — `kpi-api` published so T3.4 can run it against our
   store, or point its own instance at their own.
4. **A named integration direction, decided before coding.** Either T3.4 *pulls*
   KPI history from `/v1` (we are the server, they poll or query on demand), or
   T3.4 *pushes* its own telemetry via OTLP under a `kio_id` and appears in the
   same scoreboard (we are the sink, the existing v2.2 contract already covers
   it), or both. These are different pieces of work; picking one after
   implementation starts is where integration schedules go wrong.

---

## Sequencing & dependencies

**Ours (Grafana side, per D5):**

```
P0  catalog: numeric targets + Table 3 KIO fix      [KPIinterface]  <- prerequisite
     |
     +-- P1a Overview GA KPI scoreboard (16 global) [observability] <- item 1
     |
     +-- P1b KIO Detail: per-KIO KPI rows, at top   [observability] <- item 1 + D3/D4
              |
              +-- P4  stack + KIO version visibility, v1.1.0        <- item 2
```

**Hemant's (API side):** metric registry + `GET /api/metrics` (issue #3) and the
companion KPI Query API — the P2/P3 material above, kept here as the interface
we consume.

**Gated:** P5, T3.4 integration — depends on the open questions, not on our code.

P1a and P1b are independent once P0 lands. P4 is small and dashboard-side, and
should ship **with** P1b: the KIO version panel belongs in the same KIO Detail
header row as the new KPI section, so doing them together is one layout change
instead of two.

**Critical path is now P0**, and it is small: numeric `targets` blocks plus a
one-line-per-KPI KIO1 removal. The one thing that must not be skipped is the
KPI 3.1 direction fix — build the panels on an inverted metric and the
scoreboard will confidently show the wrong sign.

---

## Open questions

1. **Table 3 vs D1.1 Table 7 — which governs?** Table 3 is being treated as
   authoritative (D3), but it disagrees with D1.1 Table 7 in four places, and
   these are not rounding differences:
   - **KIO6** — Table 7 assigns it KPI 9.2 (technical debt reduction); Table 3
     assigns 9.2 to KIO7/KIO9 and gives KIO6 nothing.
   - **KIO12** — Table 7 assigns it KPI 6.2 (as "security issue detection");
     Table 3 assigns 6.2 to KIO2/KIO7/KIO11 and gives KIO12 nothing.
   - **KIO3** — Table 7 links it to KPI 3.1; Table 3 gives it 1.1 and 1.2.
   - **KIO11** — Table 7 links it to KPI 3.1 and 7.1; Table 3 gives it 5.1,
     6.1, 6.2.

   Consequence if Table 3 stands: KIO5, KIO6 and KIO12 have no global KPI and
   their pages show a "none assigned" note. Worth a one-line confirmation from
   the coordinator, since three KIO teams finding no KPI on their dashboard will
   ask about it.
2. **Which document is Table 3 from?** It is not D1.1's own Table 8 numbering,
   so `d11_source` in the catalog needs the right document and revision to point
   at.
3. **T3.4 direction and owner.** What T3.4 covers exactly, and whether the
   integration is T3.4-pulls-from-the-API or T3.4-pushes-telemetry-to-us (or
   both). Now largely a question for the API side (D5), but it affects whether
   the dashboards need a T3.4-shaped view.
4. **API exposure.** Issue #3 decides this for `/api/metrics`: unauthenticated,
   trusted LAN only, matching the existing posture of `:8428` / `:9428` /
   `:3200`. Consistent, and documented there. Flagged only because the meeting's
   "anybody shall be able to collect previous data" may later mean
   partner-reachable, which would make auth and TLS a prerequisite rather than a
   follow-up.

Answered on 2026-09-09 — see **Decisions taken** above: opening-page KPI scope
(D1), versioning scope (D2), per-KIO KPI mapping (D3, via Table 3), default
scoreboard population and KIO Detail placement (D4), ownership split with the
API side (D5), and no-new-service (D6). KPI 3.1's direction is settled by
Table 3 and tracked as a fix in P0.
