<div align="center">

# 🔬 ArXiv Daily Researcher

**An LLM-powered system for paper monitoring, selection, analysis, reporting, and research archiving**

[![Version](https://img.shields.io/badge/version-v4.3-brightgreen.svg)](CHANGELOG.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions)](https://github.com/features/actions)
[![Modern WebUI](https://img.shields.io/badge/Config_Panel-Modern_WebUI-465FD5)](#️-modern-management-webui)
[![中文文档](https://img.shields.io/badge/README-中文-blue.svg)](README.md)

*For continuous literature tracking, focused research, and historical-data organisation.*

</div>

---

ArXiv Daily Researcher collects papers from ArXiv and optional extensions, evaluates them against a research profile, produces translated summaries and PDF analysis, and delivers Markdown, HTML, and notification results.

v4.3 stores candidates, processing stages, report delivery, notification outbox rows, favourite preferences, history-maintenance backlog, and past-date report queues in SQLite. Workflows resume from completed stages, while live configuration is kept separate from source code for durable deployments, upgrades, and recovery. The Docker Worker reliably consumes history-maintenance requests submitted by the WebUI.

---

## ✨ Core Features

<table>
<tr>
<td width="50%" valign="top">

### 📡 Multiple Sources and Exact Delivery

ArXiv scans first submissions and last updates with full pagination and scan receipts. Optional sources include PRL, PRA/PRB, Nature, Science, Hugging Face Papers, declarative journals, and OpenAlex or Semantic Scholar enrichment. Stable identities merge the same work while preserving source-specific variants.

</td>
<td width="50%" valign="top">

### 🎯 Scoring and Content Analysis

Choose Core Relevance V2, Weighted Keywords V1, or Learned Preference V1. CHEAP_LLM handles scoring, translation, keywords, and TLDRs; SMART_LLM handles PDF deep analysis and trend synthesis. Local PyMuPDF and MinerU are supported.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🗃️ Queues, Retries, and History Maintenance

Candidates enter SQLite before downstream processing. Failed stages remain retryable, and report delivery plus notification outbox rows are committed atomically. Legacy import, field repair, omission scans, supplement reports, and past-date reports all have durable state.

</td>
<td width="50%" valign="top">

### 📄 Reports, Favourites, and Search

Daily, supplement, past-date, trend, and keyword-trend reports support HTML and Markdown. The viewer moves through actual report batches, so multiple reports from one day are not skipped. Favourites, preferences, and full-text search are backed by SQLite.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔔 Notifications and Observability

Email, WeCom, DingTalk, Telegram, Slack, and generic webhooks are supported. Every channel has a test delivery action. Run, maintenance, source, LLM, and token-use views show status and concise issue summaries.

</td>
<td width="50%" valign="top">

### 🖥️ Modern Management WebUI

The standalone ASGI panel provides 18 pages across Run, Content, Configuration, and System. It supports Chinese and English, light and dark themes, account management, and local refreshes. A typical remote address is `http://<host>:8501`.

</td>
</tr>
</table>

---

## 📑 Navigation

<table>
<tr>
<td width="50%" valign="top">

### 📘 Getting Started

| Section | Content |
| :--: | :--- |
| [✨ Core Features](#-core-features) | Capability overview |
| [🚀 Quick Start](#-quick-start) | First deployment, configuration, and run |
| [🛠️ Configuration Tools](#️-configuration-tools) | Wizard, WebUI, and screenshots |
| [🐳 Deployment](#-deployment) | User deployment, development tests, and upgrades |

</td>
<td width="50%" valign="top">

### 📗 In Depth

| Section | Content |
| :--: | :--- |
| [📖 Feature Details](#-feature-details) | Workflows, maintenance, reports, and notifications |
| [📁 Project Structure](#-project-structure) | Code, runtime data, and test Compose |
| [❓ FAQ](#-faq) | Deployment, tasks, and recovery |
| [📝 Changelog](CHANGELOG.md) | Version and compatibility record |

</td>
</tr>
</table>

---

## 🚀 Quick Start

### Step 1: Get the Project

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
~~~

### Step 2: Set Configuration

At minimum, set both LLM configurations in `.env`. The remaining configuration can be completed in the WebUI:

~~~env
CHEAP_LLM__API_KEY=sk-your-key
CHEAP_LLM__BASE_URL=https://api.openai.com/v1
CHEAP_LLM__MODEL_NAME=gpt-4o-mini

SMART_LLM__API_KEY=sk-your-key
SMART_LLM__BASE_URL=https://api.openai.com/v1
SMART_LLM__MODEL_NAME=gpt-4o
~~~

The live configuration is Git-ignored at `runtime/config.json`; `configs/config.example.json` is the tracked example. A first deployment can create it from the WebUI or the setup wizard.

When upgrading a source deployment from v4.1 or earlier, run this once if `configs/config.json` still exists:

~~~bash
if [ -f configs/config.json ] && [ ! -f runtime/config.json ]; then
  mkdir -p runtime
  mv configs/config.json runtime/config.json
fi
~~~

### Step 3: Start the User Deployment

The root `docker-compose.yml` uses official GHCR images:

~~~bash
docker compose pull
docker compose up -d
docker compose ps
~~~

Open `http://<host>:8501` to initialise the administrator account and configure the research profile, sources, scoring, and notifications. The panel is explicitly published as `8501:8501`; expose it only through a controlled LAN, Tailnet, reverse proxy, or firewall policy.

For the first research run, set the maximum papers per run to `5`. Verify reports, SQLite, notifications, and logs before choosing a daily limit or `0` (all available queue items).

Default runtime locations:

- Reports: `data/reports/`
- SQLite: `data/daily_research/daily_research.db`
- Backups: `data/backups/`
- Logs: `logs/`

---

## 🛠️ Configuration Tools

### 🧙 Interactive Setup Wizard

Useful for a first deployment, SSH, or a headless server:

~~~bash
python src/utils/setup_wizard.py
~~~

| Step | Content |
| :--: | :--- |
| 1 | CHEAP_LLM, SMART_LLM, and connection settings |
| 2 | ArXiv, categories, additional sources, and external APIs |
| 3 | Research context, keywords, and reference keywords |
| 4 | Scoring policy, thresholds, weights, and author preferences |
| 5 | Notification channels and task notification settings |
| 6 | PDF, concurrency, retries, proxy, backup, and WebDAV |

### 🖥️ Modern Management WebUI

Run directly on the local host:

~~~bash
uvicorn src.modern_webui.app:app --host 127.0.0.1 --port 8501
~~~

Docker deployments provide the panel through `config-panel`. The WebUI and worker share `.env`, `runtime/`, `configs/`, `data/`, and `logs/`; the sidebar **Save All Changes** action writes the configuration for later tasks.

| Group | Pages | Purpose |
| :--- | :--- | :--- |
| Run | Daily Research, Past Daily Reports, Trend Tasks | Start work and inspect queues, state, and logs |
| Content | Reports, Favourites, Search | Browse reports, manage saved papers, and search the archive |
| Configuration | Keywords, Sources, Scoring, API, Notifications, Advanced, Accounts | Maintain research and runtime settings |
| System | Backup & Sync, History Maintenance, Diagnostics, Usage, Logs | Operate data and investigate runs |

### 🖼️ WebUI Screenshots

<table>
  <tr>
    <td align="center" width="33%">
      <img src="assets/webui_daily_push_v4.png" alt="Daily research state and queue" width="100%" />
      <br />
      <sub>Daily research, state, and queue</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_analytics_v4.png" alt="Usage statistics and token trends" width="100%" />
      <br />
      <sub>Usage statistics and time ranges</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_scoring_v4.png" alt="Scoring policy and author preferences" width="100%" />
      <br />
      <sub>Scoring policy and qualification</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <img src="assets/webui_advanced_v4.png" alt="Advanced and proxy settings" width="100%" />
      <br />
      <sub>PDF, concurrency, and proxy settings</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_data_management_v4.png" alt="Backup and sync" width="100%" />
      <br />
      <sub>Local backups and WebDAV</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_history_import_v4.png" alt="History maintenance schedule" width="100%" />
      <br />
      <sub>History maintenance and run mode</sub>
    </td>
  </tr>
</table>

Screenshots use a current, sanitised test configuration. They contain no API keys, passwords, webhooks, email addresses, private-network addresses, real reports, or local paths.

---

## 🐳 Deployment

### User Deployment: Root Compose <sup>Recommended</sup>

The root `docker-compose.yml` is only for actual deployments. It pins the official images that match the v4.3 Release:

| Service | Image | Network and entrypoint |
| :--- | :--- | :--- |
| `arxiv-daily-researcher` | `ghcr.io/yzr278892/arxiv-daily-researcher:4.3` | Host network; cron, task queue, and worker |
| `config-panel` | `ghcr.io/yzr278892/arxiv-daily-researcher-config-panel:4.3` | Bridge network; `8501:8501` WebUI |

The worker uses host networking and can call a host-local LLM or proxy directly. The WebUI uses an explicit port map; use `host.docker.internal` when testing a host-local service from the panel.

Common commands:

~~~bash
# Pull the pinned version and start it
docker compose pull
docker compose up -d

# State, logs, and health
docker compose ps
docker compose logs -f arxiv-daily-researcher
docker compose logs -f config-panel

# Upgrade to the version recorded in the checked-out Compose file
git pull
docker compose pull
docker compose up -d --force-recreate

# Stop services (keeps data, logs, and runtime)
docker compose down
~~~

`PUID` and `PGID` default to `1000`. Set them to the host user's actual UID/GID in `.env` before a NAS or non-default-user deployment to avoid root-owned bind-mount files.

### Development Tests: `tests/docker-compose.yml`

`tests/docker-compose.yml` is only for local source builds, feature verification, and screenshots. It is not a daily-run deployment image:

~~~bash
# Run from the repository root; builds the current working tree
docker compose -f tests/docker-compose.yml up -d --build
docker compose -f tests/docker-compose.yml ps

# Test-container logs
docker compose -f tests/docker-compose.yml logs -f worker
docker compose -f tests/docker-compose.yml logs -f config-panel

# Finish the test deployment
docker compose -f tests/docker-compose.yml down
~~~

The test worker uses `MODE=manual`: it never starts daily research on cron, but it accepts explicit WebUI task requests. It reuses the current workspace's `.env`, `runtime/`, `data/`, and `logs/`, so do not run it alongside the root user deployment; both would compete for the same port and runtime data. Use a separate worktree when an isolated test state is required.

### GitHub Actions and Local CLI

The repository includes daily research, trend research, full regression, and image-publishing workflows. Actions are useful for temporary or cloud runs; a Docker deployment with persistent directories is better for long-lived state.

Local Python example:

~~~bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-core.txt -r requirements-webui.txt
python main.py

# Focused trend research
python main.py --mode trend_research --keywords "quantum error correction"
~~~

---

## 📖 Feature Details

### 🔄 Daily and Trend Research

| Dimension | `daily_research` | `trend_research` |
| :--- | :--- | :--- |
| Goal | Track recent papers and revisions | Investigate a focused topic |
| Scope | Fixed three-day lookback plus watermark recovery | Chosen keywords, date range, and categories |
| Processing | Scoring, translation, keywords, optional PDF analysis | Per-paper TLDR and combined trend analysis |
| Output | Daily, supplement, or past-date reports | Markdown, HTML, and metadata |
| Trigger | Cron, WebUI, CLI, Actions | WebUI, CLI, Actions |

Daily research scans sources completely before it writes candidates to SQLite. The per-run cap limits downstream scoring and analysis only; unprocessed and failed papers remain queued for the next run.

### 📜 History Maintenance and Supplement Reports

| Task | Purpose | Run rule |
| :--- | :--- | :--- |
| Legacy import | Index papers already present in legacy HTML; full repair can read compatible JSON | Idle worker or time window |
| Historical data repair | Fill missing score, TLDR, translation, or deep analysis from SQLite and patch the original report | Idle worker or time window |
| Historical omission scan | Scan SQLite-covered ranges and create calendar-week backlog | Idle worker or time window |
| Supplement report | Process backlog and write independent supplement reports | Maintenance hand-off or manual request |
| Past daily reports | Re-run the full daily pipeline for a date range | Durable queue, one date at a time |

History maintenance defaults to idle execution. It can instead be limited to a daily window, defaulting to `00:00–06:00`. Its per-run paper cap is separate from the daily-research cap.

### 📄 Reports, Favourites, and Search

- Daily, supplement, and past-date reports live under `data/reports/daily_research/`; trend and keyword-trend reports use their corresponding directories.
- Daily reports are ordered by filename timestamp. **Previous Report / Next Report** follows actual report batches, so every same-day report remains reachable.
- 👍 / 👎 markers in previews are stored in SQLite without changing the archived HTML. The Favourites page can automatically save future qualifying papers and scan existing qualifying papers.
- Search filters the archive by title, author, abstract, TLDR, keyword, source, date, score, and favourite state.

### 🔔 Notifications, Backups, and Recovery

Large tasks produce one summary notification. Failures and partial results include only the affected stage and a short reason. Failed deliveries remain in the SQLite outbox for later retry, and every configured channel has a test-delivery action in the WebUI.

| Item | Behaviour |
| :--- | :--- |
| Local backup | Consistent SQLite gzip snapshots; keep every copy from today and the newest copy from each older date |
| WebDAV | Incremental uploads when content changes; configuration, history, keywords, and reports are selectable |
| Restore | Stop writers, export a protective backup in **Backup & Sync**, then import the target archive |
| Diagnostics | Inspect task, LLM, source, notification-outbox, and token-use state |

### 🔒 Runtime and Access Boundaries

Daily research, trend research, history maintenance, supplements, and past-date reports coordinate through locks and activity state to prevent concurrent SQLite writes. Database restore is rejected while a writer is active.

The WebUI requires an administrator account by default. Sessions, password hashes, and login throttling stay in local configuration. Keep authentication enabled and restrict a LAN or Tailnet deployment with a firewall, VPN, or reverse proxy.

---

## 📁 Project Structure

~~~text
arxiv-daily-researcher/
├── main.py                       # CLI entry point
├── VERSION                       # Release version
├── docker-compose.yml            # User deployment: pinned GHCR images
├── docker/
│   ├── Dockerfile                # worker / webui multi-stage images
│   └── entrypoint.sh             # user cron and test manual modes
├── configs/
│   └── config.example.json       # Runtime configuration example
├── runtime/
│   └── config.json               # Local live configuration (Git ignored)
├── src/
│   ├── modes/                    # daily, trend, history, supplement, backfill jobs
│   ├── agents/                   # scoring, analysis, and keyword components
│   ├── sources/                  # data sources
│   ├── notifications/            # notifications and outbox
│   ├── modern_webui/             # ASGI WebUI, frontend, and i18n
│   └── utils/                    # SQLite, locks, backups, sync, health checks
├── data/                         # SQLite, reports, queues, backups (runtime generated)
├── logs/                         # Runtime logs
├── assets/                       # Sanitised README screenshots
└── tests/
    ├── docker-compose.yml        # Local source development/test Compose
    └── test_*.py                 # Regression tests
~~~

---

## ❓ FAQ

<details>
<summary><b>How do I reach the WebUI after Docker deployment?</b></summary>

The root Compose maps `8501:8501`. Use `http://127.0.0.1:8501` on the host, or `http://&lt;host&gt;:8501` on a controlled LAN or Tailnet. Do not expose an unauthenticated panel directly to the public internet.

</details>

<details>
<summary><b>Why is there a separate Compose file for development tests?</b></summary>

The root Compose pulls released GHCR images for reproducible user deployment. `tests/docker-compose.yml` builds the current source tree and disables cron so it can verify changes. Do not run both at once.

</details>

<details>
<summary><b>What should I do after LLM timeouts, 429s, or partially failed papers?</b></summary>

Open **System → Diagnostics** and inspect LLM health and the stage summary. Temporary network failures, throttling, 5xx responses, timeouts, and empty responses are retried according to policy. Completed stages remain saved and unfinished papers stay in SQLite; run again after the service is fixed.

</details>

<details>
<summary><b>Why has a history-maintenance request not started?</b></summary>

History work waits for daily research, trend tasks, supplement runs, and past-date reports to become idle. A time-window policy also waits for its configured window. The status panel and relevant log show the waiting reason.

</details>

<details>
<summary><b>How do I upgrade safely?</b></summary>

Export a SQLite backup, read the Release and CHANGELOG, update the checked-out source, then run `docker compose pull` and `docker compose up -d --force-recreate`. Check `docker compose ps`, Diagnostics, and recent logs afterwards.

</details>

---

## 📜 License

This project is licensed under [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html).

## 💬 Community and Feedback

- [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues): include the version, deployment type, reproduction steps, and a sanitised log summary.
- Contributions through Forks and Pull Requests are welcome.

## 🤝 API Use

Follow the current policies, quotas, and account requirements of ArXiv, OpenAlex, Semantic Scholar, MinerU, and the LLM services you use. The project provides timeout, retry, rate-limit, and proxy controls; it does not replace provider restrictions.

## 🙏 Acknowledgments

Thanks to [ArXiv](https://arxiv.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/), [MinerU](https://mineru.net/), and the open-source community.

---

## 📝 Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full history.

| Version | Date | Summary |
| :--- | :--- | :--- |
| **v4.3** | 2026-09-01 | Fixes the Docker Worker module path used to consume WebUI history-maintenance queues. |
| **v4.2** | 2026-09-01 | Runtime-config migration, history scheduling, automatic favourites, notification tests, report-batch navigation, local WebUI refreshes, and separate user/test Compose files. |
| **v4.1** | 2026-08-30 | Modern WebUI, history maintenance, multi-source merging, diagnostics, and token usage. |
| **v4.0** | 2026-08-25 | SQLite history and queues, complete scanning, scoring, supplement reports, past-date reports, backups, and dual-architecture GHCR images. |

---

<div align="center">

If this project helps your research, a Star is appreciated ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#/yzr278892/arxiv-daily-researcher&Date)

</div>
