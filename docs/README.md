# Documentation Index

This is the full documentation set for the **[AI4SWENG Observability Stack](../README.md)**.
If you're new here, start with the root [`README.md`](../README.md) for the
project overview and quick start, then come back here for the topic you need.

## Start here depending on who you are

| I am… | Start with |
|---|---|
| Just trying the stack locally | Root [`README.md`](../README.md) — Quick start |
| Connecting my own KIO (local or a different machine), and what data to send | [Connecting a KIO — Data Contract & Setup Guide](remote-connectivity.md) → [`remote-kio/`](../remote-kio/) |
| Looking for the normative integration contract | [`Observability_v2.2.docx`](https://ai4seceu.sharepoint.com/:f:/s/AI4SwEng134/IgCtKA3X92K5T7XYr7gvzTIKAXhV17nbktjaGPy9_BZ26rM?e=WKBQQB) |
| Building dashboards / querying data programmatically | [Metrics Reference & Query API](metrics-reference.md) |

## All guides

| Guide | Covers |
|---|---|
| [Architecture & Components](architecture.md) | What each service does, the repo layout, and the standing design decisions (Prometheus, Tempo, Langfuse, Auth/TLS, Grafana access) |
| [The KIO Simulators](kio-simulators.md) | Which simulators run by default, per-KIO LLM/task-type config, and the real-vs-simulated data map |
| [Metrics Reference & Query API](metrics-reference.md) | The 7 mandatory metrics + optional extras, the `/api/metrics` HTTP query API, unstructured logs, and traces |
| [Real Project KPIs (D1.1)](kpis.md) | The 16 D1.1 KPIs, which KIO emits which, and how they map to dashboard panels |
| [Connecting a KIO — Data Contract & Setup Guide](remote-connectivity.md) | **Start here to connect a KIO.** The full data contract (mandatory metrics + types/units, optional D1.1 extras), and setup for both a remote-server deployment and a fully-local one |
| [Real LLM Integration (KIO2)](real-llm-integration.md) | Running a real Ollama LLM + real GPU energy/temperature behind `kio2-sim` |
| [Langfuse (LLM Tracing)](langfuse.md) | The self-hosted Langfuse stack for prompt/completion/cost tracing, and remote-deployment host settings |
| [Tests (pytest)](testing.md) | Running the test suite; what's faked vs. what needs a real backend |
| [Design Decisions & v2 Guideline Evaluation](design-decisions.md) | Why this stack made the choices it did, evaluated against a proposed v2 integration guide |
| [Roadmap — KPI Scoreboard & Query API](ROADMAP_KPI_API.md) | Planned work: GA KPI dashboard, versioning, and a KPI query API |

## Reference documents (normative)

| Document | For |
|---|---|
| [`Observability_v2.2.docx`](https://ai4seceu.sharepoint.com/:f:/s/AI4SwEng134/IgCtKA3X92K5T7XYr7gvzTIKAXhV17nbktjaGPy9_BZ26rM?e=WKBQQB) | The normative Integration Guide — start here for any brand-new KIO |
| [`KPI_Metrik_Referansi_v1.3.docx`](report/KPI_Metrik_Referansi_v1.3.docx) | Full D1.1 KPI catalog and KIO ownership mapping |
| [`Observability_Technical_Report_EN_v1.5.docx`](report/Observability_Technical_Report_EN_v1.5.docx) | Technical report (English) |
| [`Observability_Teknik_Rapor_v1.5.docx`](report/Observability_Teknik_Rapor_v1.5.docx) | Technical report (Turkish) |
| [`old/`](old/) | Superseded document versions, kept for history |

## The `remote-kio/` example folder

Separate from this `docs/` set, [`remote-kio/`](../remote-kio/) is a
**runnable** starter kit — plain-Python and Docker examples, a networking
guide, and a preflight connectivity checker — for connecting a KIO that lives
on another machine. [Connecting a KIO](remote-connectivity.md) explains how it
fits together; the folder itself is where you actually run something.
