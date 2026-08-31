<div align="center">

# 🔬 ArXiv Daily Researcher

**An LLM-powered system for academic paper monitoring, selection, analysis, reporting, and research archiving**

[![Version](https://img.shields.io/badge/version-v4.2-brightgreen.svg)](CHANGELOG.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions)](https://github.com/features/actions)
[![Modern WebUI](https://img.shields.io/badge/Config_Panel-Modern_WebUI-465FD5)](#️-modern-management-webui)
[![中文文档](https://img.shields.io/badge/README-中文-blue.svg)](README.md)

*A complete workflow for daily literature tracking, focused research, and historical-data organization.*

</div>

---

ArXiv Daily Researcher collects papers from ArXiv and optional additional sources, evaluates them against a research profile, produces translated summaries and PDF analysis, tracks research trends, and delivers Markdown/HTML reports through multiple notification channels.

v4.2 uses SQLite for candidate papers, processing stages, report delivery, notification outbox rows, history-maintenance backlogs, and historical-daily-report queues. A paper can combine metadata and analysis variants from multiple sources, and each workflow can resume from completed stages; live configuration is isolated from the source tree for long-running deployments and upgrades.

The current release includes:

- **Daily research**: a fixed three-day lookback for new papers and revisions
- **Trend research**: topic, date-range, and category-based research reports
- **Legacy import and supplement reports**: an HTML delivery ledger by default, with complete repair, field repair, and omission scans available independently
- **Past daily-report queues**: replays full daily workflows over a selected date range
- **Modern management WebUI**: configuration, execution, reports, diagnostics, and account management through four sidebar groups and 18 top-level pages

---

## ✨ Core Features

<table>
<tr>
<td colspan="2" align="center"><sub>— Data collection & intelligent selection —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📡 Multi-Source Fetching

**ArXiv** is the primary source. The scanner paginates both first submissions and last updates, records scan receipts, and supports categories, announcement-delay rescans, and recovery watermarks. Optional sources include PRL, PRA/PRB, Nature, Science, Hugging Face Papers, and declarative journal definitions, with optional OpenAlex and Semantic Scholar enrichment. When the same paper appears in several sources, SQLite merges it by stable identity and retains source variants.

</td>
<td width="50%" valign="top">

### 🎯 Configurable Scoring Policies

Choose **Core Relevance V2**, **Weighted Keywords V1**, or **Learned Preference V1**. V2 uses primary-keyword relevance plus a strong match for qualification, while reference terms and author preferences contribute to ranking. V1 retains the compatible weighted threshold, and learned preferences refine ordering from saved feedback.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Deep analysis & recoverable archive —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔍 LLM and PDF Analysis

<code>CHEAP_LLM</code> handles scoring, translation, keywords, and TLDRs; <code>SMART_LLM</code> handles PDF deep analysis and trend synthesis. PDF parsing supports local **PyMuPDF** and **MinerU**, with shared timeouts, retries, rate limits, and safe error summaries.

</td>
<td width="50%" valign="top">

### 🗃️ SQLite Queues and Exact Delivery

Papers enter SQLite before downstream processing. Stable identity, source, and version control de-duplication; mergeable fields are unified while source-specific abstracts and analyses remain variants. Failed stages remain retryable, and report delivery, scan watermarks, and notification outbox rows are committed together after a report is written.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Trend research & history organization —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔬 Trend Research Mode

<code>trend_research</code> accepts keywords, date ranges, and ArXiv categories. It creates a TLDR for each paper and uses <code>SMART_LLM</code> to synthesize topic evolution, researchers, methods, research gaps, and future directions.

</td>
<td width="50%" valign="top">

### 📜 Legacy Import and Past Daily Reports

Legacy HTML reports can be indexed into the SQLite delivery ledger in one click, preventing future daily runs from repeating those papers. Complete repair additionally reads compatible JSON. HTML remains authoritative for report keywords, while the old keyword cache only fills a missing keyword section for the same reported paper. Field repair, report patching, and omission scans use SQLite as their source of truth; omissions produce calendar-week supplement reports. Past daily reports use a durable date-range queue and run the complete research pipeline day by day.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Reports & notifications —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 📄 Markdown + HTML Reports

Daily research, supplement reports, past daily reports, trend research, and keyword trends support Markdown and HTML output. The report viewer orders items by timestamp and provides previews, paper marks, and full-archive metadata search.

</td>
<td width="50%" valign="top">

### 🔔 Multi-Channel Notifications and Outbox

Supports **email, WeCom, DingTalk, Telegram, Slack, and generic webhooks**. Daily research, trend research, legacy import, supplement runs, past-report queues, and release updates send outcome summaries. Temporarily unavailable deliveries remain in the SQLite outbox for later retry.

</td>
</tr>
<tr>
<td colspan="2" align="center"><sub>— Configuration & operations —</sub></td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧙 Setup Wizard and Modern WebUI

The CLI wizard covers LLMs, sources, keywords, scoring, notifications, and advanced settings. The modern WebUI runs as a standalone ASGI service with Run, Content, Configuration, and System groups, bilingual light/dark themes, and account management, all backed by the same persistent worker data.

</td>
<td width="50%" valign="top">

### 🛡️ Backups, Sync, and Diagnostics

SQLite automatically creates consistent gzip snapshots: all copies from today are retained, while older dates retain the newest copy per day. WebDAV uses content-change uploads; Runtime Diagnostics provides run, LLM, and source health, while Usage Statistics provides token line trends for selectable time ranges.

</td>
</tr>
</table>

---

## 📑 Navigation

<table>
<tr>
<td width="50%" valign="top">

### 📘 Getting Started

| Section | Description |
| :----------------------: | :----------------------------------------- |
| [✨ Core Features](#-core-features) | Overview of sources, analysis, archive, and notifications |
| [🚀 Quick Start](#-quick-start) | Configure and run the first workflow in three steps |
| [🛠️ Configuration Tools](#️-configuration-tools) | CLI wizard, WebUI, and screenshots |
| [🐳 Deployment](#-deployment) | Docker, GHCR, GitHub Actions, and local scheduling |

</td>
<td width="50%" valign="top">

### 📗 In Depth

| Section | Description |
| :------------------------: | :---------------------------------------------- |
| [📖 Feature Details](#-feature-details) | Workflows, scoring, history tasks, reports, backup, and sync |
| [📁 Project Structure](#-project-structure) | Directories, modules, and runtime data |
| [❓ FAQ](#-faq) | LLMs, networking, queues, imports, and recovery |
| [📝 Changelog](CHANGELOG.md) | Complete version, release, and compatibility history |

</td>
</tr>
</table>

---

## 🚀 Quick Start

### Step 1: Clone the Project

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
~~~

Docker is the recommended option for long-running deployments. Local Python and GitHub Actions instructions appear later in this document.

### Step 2: Configure the System

For a first installation, start the interactive wizard:

~~~bash
python src/utils/setup_wizard.py
~~~

Docker users can also start the WebUI and configure the system in a browser:

~~~bash
docker compose up -d --build
~~~

Open <http://127.0.0.1:8501> and configure LLMs, sources, keywords, scoring, notifications, and the run time. The WebUI binds to the local host, which works well with a VPN or an authenticated reverse proxy.

Docker writes `data`, `logs`, `configs`, `runtime`, and `.env` as the `PUID` / `PGID` in `.env`, preventing root-owned files on NAS bind mounts. When upgrading from an old root-running image, set `ADR_REPAIR_OWNERSHIP=true` for one start, verify ownership, then remove it.

The live configuration is the Git-ignored <code>runtime/config.json</code>; <code>configs/config.example.json</code> remains the tracked example. On the first v4.2 start, an existing v4.1-or-earlier <code>configs/config.json</code> is copied safely and retained for rollback.

For a source checkout upgraded from v4.1 with Git, run this once before the first <code>git pull</code> so Git does not remove the formerly tracked configuration file:

~~~bash
if [ -f configs/config.json ] && [ ! -f runtime/config.json ]; then
  mkdir -p runtime
  mv configs/config.json runtime/config.json
fi
~~~

The WebUI enables administrator login by default. Initialize the account from the local address on first use; the password is written to `.env` only as a salted hash. Sessions are valid for seven days by default and repeated failed attempts are rate-limited. A trusted LAN installation can skip login during first setup; keep the panel behind a VPN or an HTTPS reverse proxy with access control as well.

<details>
<summary><b>Manual configuration</b></summary>

**1) Set the LLM environment variables:**

~~~env
CHEAP_LLM__API_KEY=sk-your-key
CHEAP_LLM__BASE_URL=https://api.openai.com/v1
CHEAP_LLM__MODEL_NAME=gpt-4o-mini

SMART_LLM__API_KEY=sk-your-key
SMART_LLM__BASE_URL=https://api.openai.com/v1
SMART_LLM__MODEL_NAME=gpt-4o
~~~

**2) Set a research topic and ArXiv categories:**

~~~bash
mkdir -p runtime
cp configs/config.example.json runtime/config.json
~~~

~~~json
{
  "keywords": {
    "primary_keywords": {
      "entries": [
        {"keyword": "quantum error correction", "weight": 1.0},
        {"keyword": "surface code", "weight": 0.8}
      ]
    },
    "research_context": "fault-tolerant quantum computing and quantum error-correcting codes"
  },
  "data_sources": {
    "enabled": ["arxiv"]
  },
  "target_domains": {
    "domains": ["quant-ph"]
  }
}
~~~

<code>runtime/config.json</code> supports JSONC comments. WebUI saves preserve existing comments and settings from tabs outside the current session.

</details>

### Step 3: Run

~~~bash
# Docker: start the worker and WebUI
docker compose up -d --build

# Local Python: install dependencies and run daily research
python -m venv venv
source venv/bin/activate
pip install -r requirements-core.txt -r requirements-webui.txt
python main.py

# Trend research example
python main.py --mode trend_research --keywords "quantum error correction"
~~~

For the first run, set **Maximum papers per run** to <code>5</code>, confirm reports, SQLite, notifications, and logs, then choose a regular limit or <code>0</code> for the complete available queue.

Runtime data is stored in:

- Reports: <code>data/reports/</code>
- SQLite: <code>data/daily_research/daily_research.db</code>
- Backups: <code>data/backups/</code>
- Logs: <code>logs/</code>

---

## 🛠️ Configuration Tools

The project offers two primary configuration paths: the **CLI setup wizard** and the **modern management WebUI**.

### 🧙 Interactive Setup Wizard

Useful for first deployments, SSH sessions, and headless servers:

~~~bash
python src/utils/setup_wizard.py
~~~

| Step | Area | Description |
| :---: | :--- | :---------- |
| 1 | LLMs | Configure CHEAP_LLM, SMART_LLM, and connection settings |
| 2 | Sources | ArXiv switch, categories, additional sources, and optional third-party APIs |
| 3 | Keywords | Primary keywords, research context, and reference-PDF keyword extraction |
| 4 | Scoring | Choose a policy, thresholds, weights, and author preferences |
| 5 | Notifications | Global switch, channel credentials, and outcome notifications |
| 6 | Advanced | PDF parsing, concurrency, retries, logs, proxy, backup, and WebDAV |

The wizard shows a configuration summary before writing and creates a backup for an existing configuration.

---

### 🖥️ Modern Management WebUI

#### Start the panel

~~~bash
# Local
uvicorn src.modern_webui.app:app --host 127.0.0.1 --port 8501

# Docker
docker compose up -d config-panel
~~~

Docker and local panel: <http://127.0.0.1:8501>

The default listener is local-only. For direct access from a trusted Tailscale network, set this host's Tailscale IPv4 in `.env`, then recreate only the WebUI:

~~~env
ADR_WEBUI_BIND_HOST=100.x.y.z
~~~

~~~bash
docker compose up -d --no-deps --force-recreate config-panel
~~~

Then open `http://100.x.y.z:8501`. This keeps Docker's host-network semantics, so no `ports:` mapping is needed. Do not use `0.0.0.0`, and initialize a WebUI administrator account before sharing access.

The WebUI and worker share <code>.env</code>, <code>configs/</code>, <code>data/</code>, and <code>logs/</code>. The primary sidebar groups pages as Run, Content, Configuration, and System; the secondary pages remain in the top bar, retaining context on long configuration pages. A saved configuration is loaded by the next task. After changing the run time, use the sidebar worker-restart control to reinstall cron.

#### 18 pages and navigation groups

| Group | Top pages | Functionality |
| :--- | :-------- | :------------ |
| **Run** | **Daily Research**, **Past Daily Reports**, **Trend Tasks** | Launch research and inspect status and queues; replay past dates one day at a time; configure trend keywords, ranges, categories, and analysis independently |
| **Content** | **Reports**, **Favorites**, **Search** | Browse and preview HTML reports, mark papers 👍/👎 inside a report, review preference profiles, and search the SQLite archive with source variants |
| **Configuration** | **Keywords**, **Data Sources**, **Scoring**, **API**, **Notifications**, **Advanced**, **Accounts** | Manage research topics, sources, scoring, LLM/PDF/third-party APIs, notifications, runtime controls, and administrator accounts |
| **System** | **Backup & Sync**, **History Maintenance**, **Runtime Diagnostics**, **Usage Statistics**, **Logs** | Manage exports, WebDAV, and local backups; run legacy maintenance; inspect run/LLM/source health, token line trends, and native log views |

### 🖼️ WebUI Screenshots

<table>
  <tr>
    <td align="center" width="33%">
      <img src="assets/webui_daily_push_v4.png" alt="Daily research status and queue" width="100%" />
      <br />
      <sub>Daily research, status panel, and queue</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_analytics_v4.png" alt="Usage statistics and token trends" width="100%" />
      <br />
      <sub>Token usage, range controls, and line trends</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_scoring_v4.png" alt="Scoring policies" width="100%" />
      <br />
      <sub>Scoring policies and qualification logic</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <img src="assets/webui_advanced_v4.png" alt="PDF parsing and keyword trend settings" width="100%" />
      <br />
      <sub>PDF parsing and keyword trend settings</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_data_management_v4.png" alt="Database backups" width="100%" />
      <br />
      <sub>Local backup and retention settings</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_history_import_v4.png" alt="Legacy history import" width="100%" />
      <br />
      <sub>v3.2 legacy history import entry point</sub>
    </td>
  </tr>
</table>

Screenshots use redacted test configuration. They contain no API keys, passwords, webhooks, email addresses, private network addresses, or local paths.

<details>
<summary><b>When should I use the wizard or the WebUI?</b></summary>

| Tool | Best for | Characteristics |
| :--- | :------- | :-------------- |
| **Setup Wizard** (<code>setup_wizard.py</code>) | First deployment, SSH, and headless environments | Step-by-step initialization and configuration review |
| **Modern WebUI** (<code>src/modern_webui/</code>) | Daily tuning, run observation, and report reading | Four sidebar groups and 18 top pages for status, queues, reports, backups, diagnostics, and connection tests |

The wizard is a convenient starting point, while the panel offers a practical daily operations view.

</details>

---

## 🐳 Deployment

### Docker Deployment <sup>Recommended</sup>

Docker Compose starts two services:

- <code>arxiv-daily-researcher</code>: worker, cron, queue watcher, and research tasks
- <code>config-panel</code>: modern management WebUI bound to <code>127.0.0.1:8501</code> by default

#### Build from source

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
docker compose up -d --build
docker compose ps
~~~

#### Use GHCR hosted images

v4.2 provides both x86_64 and ARM64 images. Pin a production deployment to its matching version tag:

~~~bash
export ADR_WORKER_IMAGE=ghcr.io/yzr278892/arxiv-daily-researcher:4.2
export ADR_WEBUI_IMAGE=ghcr.io/yzr278892/arxiv-daily-researcher-config-panel:4.2

docker compose pull
docker compose up -d --no-build --force-recreate
docker compose ps
~~~

<code>latest</code> tracks the newest formal release. Fixed version tags provide a clear upgrade, rollback, and validation path. Each <code>v&lt;version&gt;</code> Git tag passes the complete regression suite before AMD64/ARM64 images are published.

#### Common commands

~~~bash
# Service state and health
docker compose ps

# Worker or WebUI logs
docker compose logs -f arxiv-daily-researcher
docker compose logs -f config-panel

# Rebuild local source images and recreate containers
git pull
docker compose build
docker compose up -d --force-recreate

# Run trend research inside the worker
docker compose exec arxiv-daily-researcher python main.py --mode trend_research \
  --keywords "quantum error correction" \
  --date-from 2026-01-01 \
  --categories quant-ph

# Stop services
docker compose down
~~~

#### WebUI task triggering

The WebUI writes task requests through shared volumes, and the worker watches the trigger queue before launching the requested mode. Status, phase heartbeats, stop requests, and logs use the shared runtime directory, so the panel can present task progress, queues, and results.

<details>
<summary><b>Docker runtime settings</b></summary>

| Setting | Default | Description |
| :------ | :------ | :---------- |
| <code>TZ</code> | <code>Asia/Shanghai</code> | Container timezone |
| <code>daily_research.run_time</code> | <code>12:00</code> | Daily research time, configured in WebUI or <code>runtime/config.json</code> |
| <code>RUN_ON_STARTUP</code> | <code>false</code> | Run daily research once when the container starts |
| <code>MODE</code> | <code>cron</code> | <code>cron</code> for the resident scheduler; <code>run-once</code> for a single run |
| <code>SETUP_WIZARD</code> | <code>auto</code> | Check and start the setup wizard during first deployment |
| <code>ADR_WORKER_IMAGE</code> | local worker image | Select a GHCR worker image |
| <code>ADR_WEBUI_IMAGE</code> | local WebUI image | Select a GHCR WebUI image |
| <code>ADR_WEBUI_BIND_HOST</code> | <code>127.0.0.1</code> | WebUI listener; a trusted Tailnet may use this host's Tailscale IPv4. Do not set <code>0.0.0.0</code>. |

</details>

<details>
<summary><b>Using a local OpenAI-compatible LLM</b></summary>

The worker uses the host network, so a local Linux/NAS service can use its loopback address:

~~~env
CHEAP_LLM__API_KEY=ollama
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
CHEAP_LLM__MODEL_NAME=qwen2.5:7b
~~~

The worker and WebUI both use host-network semantics, so they reach host-local LLMs, proxies, and DNS through the same addresses. The panel listens on <code>127.0.0.1:8501</code> by default; configure reverse proxies and VPN access for the deployment topology.

</details>

---

### GitHub Actions Cloud Runs

Useful for temporary cloud execution. The repository includes:

- <code>daily-run.yml</code>: daily research, with the schedule disabled by default
- <code>trend-research.yml</code>: manually started trend research
- <code>test.yml</code>: complete regression suite for main and pull requests
- <code>publish-containers.yml</code>: tag validation, test, and GHCR publication

GitHub Actions needs an accessible OpenAI-compatible LLM service and uses Actions cache for SQLite state.

#### Configuration

1. Fork the repository
2. Open **Settings → Secrets and variables → Actions**
3. Configure the required secrets
4. Enable the daily schedule after reviewing LLM, notification, and cache settings

| Secret | Required | Description |
| :----- | :------: | :---------- |
| <code>CHEAP_LLM_API_KEY</code> | ✅ | API key for daily scoring, translation, and TLDR |
| <code>CHEAP_LLM_BASE_URL</code> | ✅ | CHEAP_LLM OpenAI-compatible endpoint |
| <code>CHEAP_LLM_MODEL_NAME</code> | ✅ | CHEAP_LLM model name |
| <code>SMART_LLM_API_KEY</code> | ✅ | API key for deep analysis and trend synthesis |
| <code>SMART_LLM_BASE_URL</code> | ✅ | SMART_LLM OpenAI-compatible endpoint |
| <code>SMART_LLM_MODEL_NAME</code> | ✅ | SMART_LLM model name |
| Notification secrets | Optional | SMTP, Telegram, webhooks, and related credentials |
| Third-party source keys | Optional | OpenAlex, Semantic Scholar, and MinerU |

The <code>schedule:</code> block in <code>daily-run.yml</code> is provided as comments. Enable it after configuration. Daily research always scans the most recent three days, while earlier dates belong in the past-daily-report queue.

---

### Local Scheduled Runs (System Cron)

System Cron can also start daily research:

~~~bash
crontab -e
0 12 * * * cd /path/to/arxiv-daily-researcher && /path/to/venv/bin/python main.py >> /path/to/arxiv-daily-researcher/logs/cron.log 2>&1
~~~

Use one project directory for the worker, WebUI, SQLite database, reports, and logs.

---

## 📖 Feature Details

### 🔄 Two Primary Research Modes

| Dimension | <code>daily_research</code> (default) | <code>trend_research</code> |
| :-------- | :------------------------------------- | :-------------------------- |
| Purpose | Track recent papers and revisions | Research a focused topic |
| Sources | ArXiv and enabled additional sources | ArXiv |
| Time range | Fixed most-recent 3 days with watermark recovery | Any date range |
| Selection | Scoring policy, queue priority, optional PDF analysis | Keyword search followed by TLDRs |
| Core analysis | Scores, translated summaries, keywords, and PDF deep analysis | Topic, time evolution, researchers, gaps, and method synthesis |
| Triggers | Cron / Docker / Actions / WebUI / CLI | CLI / WebUI / Actions |
| Output | <code>data/reports/daily_research/</code> | <code>data/reports/trend_research/</code> |

### 📜 Historical Maintenance Tasks

| Mode | Entry and coordination | Result |
| :--- | :--------------------- | :----- |
| <code>legacy_import</code> | System → History Maintenance → Read Legacy History; waits for related work to become idle | By default indexes existing HTML deliveries; complete repair reads compatible JSON, fills missing keywords for the same HTML paper, and schedules follow-up maintenance |
| <code>history_data_repair</code> | System → History Maintenance → Repair Historical Data | Uses SQLite to fill missing scores, TLDRs, translations, or deep analyses and patches original reports |
| <code>history_omission_scan</code> | System → History Maintenance → Scan Historical Omissions | Scans source-aware SQLite coverage and creates capped supplements by calendar week |
| <code>supplement_run</code> | Automatically after import or manually through CLI | Processes supplement backlog and produces supplement reports |
| <code>backfill_run</code> | Run → Past Daily Reports date range or CLI date range | Queues one complete daily workflow per past date |

### 📅 Daily Research Pipeline

~~~text
1. Load configuration, locks, activity gate, and the latest successful scan watermark
2. Scan ArXiv first submissions and last updates, then record source scan receipts
3. Register candidates in SQLite and prioritize retryable stages and supplement backlog
4. Score, translate, extract keywords, and validate content within the current run cap
5. Parse PDFs and perform SMART_LLM deep analysis for qualified papers
6. Write HTML and Markdown reports
7. Commit paper delivery, run result, watermark, and notification outbox rows in one SQLite transaction
8. Run backups, incremental WebDAV sync, keyword maintenance, and notification redelivery
~~~

<code>max_papers_per_run</code> limits scoring, translation, and analysis work. Full scanning and candidate registration remain separate. <code>0</code> processes the full available queue.

### 🔬 Trend Research Pipeline

~~~text
1. Search ArXiv by keywords, date range, and categories
2. Generate a TLDR for each paper and retain source/date information
3. Use SMART_LLM to synthesize themes, time evolution, researchers, gaps, and methods
4. Write Markdown, HTML, and metadata.json
5. Record the run and send a trend-research outcome notification
~~~

### 🎯 Scoring Policies and Qualification

| Policy | Qualification | Ranking and use case |
| :----- | :------------ | :------------------- |
| **Core Relevance V2** | Weighted primary-keyword relevance reaches the threshold and at least one primary keyword strongly matches | Recommended for new configurations; reference keywords, experts, and favorites add ranking signals |
| **Weighted Keywords V1 (compatible)** | Keyword relevance, reference terms, and author bonus are aggregated and compared with the threshold | Suitable for existing V1 configurations and reports |
| **Learned Preference V1** | Uses V1 qualification | Converts favorites, dismissals, and previous V1 passes into bounded, decayed ranking preferences |

The V1 threshold is configured as:

~~~text
pass threshold = base_score + weight_coefficient × Σ(keyword weights)
~~~

V2 thresholds, strong-match conditions, and ranking signals are configured in **Scoring**. Scoring results retain non-sensitive audit evidence for review in reports and Runtime Diagnostics.

### 📡 Sources and Scan Receipts

- **ArXiv**: primary source, complete category pagination for first submissions and last updates
- **Additional sources**: built-in and declarative definitions behind an independent switch
- **OpenAlex**: additional journal data; an API key can use the provider's official quota
- **Semantic Scholar**: optional TLDR, citation, and related enrichment
- **Source health**: every scan produces a terminal receipt; Runtime Diagnostics shows recent status, success rate, candidate count, and safe error summaries

Watermarks advance after a complete and safe delivery. Transient network conditions are handled through retries, backoff, and later recovery windows.

### 🔍 PDF Parsing and Content Analysis

| Mode | Best for | Configuration |
| :--- | :------- | :------------ |
| <code>pymupdf</code> | Local execution, standard PDFs, simple deployment | Advanced → PDF parser |
| <code>mineru</code> | Complex layouts and structured-text requirements | API → MinerU; selecting it expands its settings |

The API page offers MinerU connection tests and an official console link. Parsing-service issues retain a stage summary while the available local parser path continues processing.

### 📈 Keyword Trends and Favorites

The keyword module:

1. saves keywords extracted during scoring
2. performs batch normalization at midnight
3. generates keyword-trend reports on the configured schedule
4. aggregates favorite keywords and Top authors in Favorites & Search

Reference-PDF keyword extraction has its own switch. Disabled extraction stays outside scoring, and extracted keyword lists are presented in pages for long lists.

### 🔒 Mutual Exclusion and Recovery

| Scenario | Coordination |
| :------- | :----------- |
| Daily research | <code>daily_research.lock</code> and the daily-workflow gate |
| Trend research | A parameter-hashed trend-research lock |
| Legacy import, history repair, and omission scan | Exclusive legacy activity gate that waits for daily, trend, and maintenance tasks |
| Supplement and past daily reports | Shared daily-workflow gate for the SQLite queue and delivery ledger |

Kernel file locks are authoritative. PID and time data serve diagnostics. The WebUI presents active tasks, phase heartbeats, queues, and stop requests.

### 📄 Report System

#### Daily research, supplement reports, and past daily reports

Paths:

- <code>data/reports/daily_research/markdown/&lt;source&gt;/</code>
- <code>data/reports/daily_research/html/&lt;source&gt;/</code>

Reports include run summaries, paper lists, scores, translations, deep analysis, keywords, and token statistics. Supplement reports carry a supplement label. Past daily reports retain the target date and the actual runtime in their filenames for stable same-day ordering.

When a legacy HTML report is opened in **Reports**, the preview injects persistent 👍 / 👎 controls. Archive files remain unchanged, so opening the HTML file directly from disk does not add those controls.

#### Trend research reports

Paths:

- <code>data/reports/trend_research/markdown/&lt;keyword_slug&gt;/</code>
- <code>data/reports/trend_research/html/&lt;keyword_slug&gt;/</code>

Each run also creates <code>metadata.json</code> with research parameters and paper metadata.

#### Keyword trend reports

Paths:

- <code>data/reports/keyword_trend/markdown/</code>
- <code>data/reports/keyword_trend/html/</code>

### 🔔 Notification System

Supported channels: email, WeCom, DingTalk, Telegram, Slack, and generic webhooks.

Notification configuration has three layers:

1. global notification switch
2. channel-specific switch and credentials
3. outcome, failure-summary, attachment, and update-notification settings

Large workflows send one consolidated outcome notification. Partial completion, delays, and failures include the affected stage and a concise summary. The outbox retains pending deliveries for later retry.

### 🗄️ SQLite Backups and WebDAV

| Item | Behavior |
| :--- | :------- |
| Local SQLite backup | A consistent gzip snapshot after each daily run; all copies from today and the newest copy per older day |
| Retention | Any non-negative number in WebUI; default 7 days, with <code>0</code> for permanent retention |
| WebDAV archive | Incremental upload when database content changes; remote snapshots remain available |
| Sync scope | Configuration, SQLite history, keywords, and reports can be selected independently |
| Restore | System → Backup & Sync imports/exports zip, gz, and db archives after validation |

Stop SQLite-writing tasks and create a current export before restoring a historical archive.

---

## 📁 Project Structure

~~~text
arxiv-daily-researcher/
├── main.py                       # CLI entry point and mode dispatch
├── VERSION                       # Release version
├── .env.example                  # Environment-variable template
├── requirements-core.txt         # worker / CLI dependencies
├── requirements-webui.txt        # modern ASGI WebUI dependencies
├── docker-compose.yml            # worker + config-panel composition
├── docker/
│   ├── Dockerfile                # worker / webui multi-stage images
│   └── entrypoint.sh             # cron, trigger watcher, and worker startup
├── configs/
│   ├── config.example.json       # JSONC example configuration
│   └── templates/                # report, email, and notification templates
├── runtime/
│   └── config.json               # local runtime configuration (Git ignored)
├── src/
│   ├── modes/                    # daily / trend / legacy / supplement / backfill
│   ├── agents/                   # scoring, analysis, keyword, and trend agents
│   ├── sources/                  # ArXiv, OpenAlex, HF Papers, and other sources
│   ├── report/                   # daily, trend, and keyword-trend reports
│   ├── notifications/            # multi-channel delivery and SQLite outbox
│   ├── keyword_tracker/          # keyword normalization and trends
│   ├── utils/                    # SQLite, queues, locks, backups, sync, health checks
│   └── modern_webui/             # modern ASGI WebUI, static client, and i18n
├── .github/workflows/            # research, test, and image-publication workflows
├── data/                         # SQLite, reports, queues, backups (runtime generated)
├── logs/                         # system and per-task logs (runtime generated)
├── assets/                       # README screenshots
└── tests/                        # regression tests
~~~

---

## ❓ FAQ

<details>
<summary><b>1. How should I handle empty LLM responses, timeouts, or papers in the retry queue?</b></summary>

Open **System → Runtime Diagnostics → LLM Health** to review recent final outcomes, consecutive failures, success rate, last success time, and redacted error summaries.

- 401, 403, 404, and 400: verify API key, base URL, model name, and gateway compatibility
- 429, 5xx, timeouts, and empty responses: the shared retry/backoff policy retains incomplete stages
- After the provider or network recovers: run daily research or a supplement run to reuse completed stages

For a Chat-Completions-only compatible gateway, the first confirmed unsupported <code>/responses</code> route is stored as an endpoint capability and later requests skip that fallback. The run log keeps one redacted diagnostic. If neither route is usable, correct the service address, model, or gateway configuration.

SQLite queues and the delivery ledger are maintained by the application. Use the WebUI or CLI to continue recovery work.
</details>

<details>
<summary><b>2. How do I diagnose Docker DNS failures after a network change?</b></summary>

<code>NameResolutionError</code> and <code>Temporary failure in name resolution</code> usually point to host, Docker, or VPN DNS state. Check the host and worker in order:

~~~bash
getent hosts export.arxiv.org
docker exec arxiv-daily-researcher getent hosts export.arxiv.org
docker exec arxiv-daily-researcher cat /etc/resolv.conf
~~~

Restore the host upstream DNS, then recreate containers:

~~~bash
docker compose up -d --force-recreate
~~~

For a fixed DNS policy, create a local <code>docker-compose.override.yml</code> and select resolvers suitable for the deployment network.
</details>

<details>
<summary><b>3. Why can legacy-history import wait in the queue?</b></summary>

Legacy import takes exclusive SQLite access. During daily research, trend research, keyword maintenance, supplement runs, or past daily reports, the request stays in the trigger queue and the worker claims it after related work becomes idle.

Check **System → History Maintenance** or **System → Logs** and <code>legacy_import_*.log</code>. Independent maintenance tasks use <code>history_data_repair_*.log</code> and <code>history_omission_scan_*.log</code>. One request of each kind is sufficient.
</details>

<details>
<summary><b>4. How does legacy import handle repeated analyses, missing data, and missed papers?</b></summary>

The default **Read Legacy History** action parses paper cards already present in legacy HTML and creates delivery-ledger rows. It makes no LLM calls and does not scan for omissions, which is useful immediately after an upgrade.

**Fully repair legacy history** merges records by stable paper identity and selects the newest report analysis. Compatible JSON is written into SQLite. Extracted keywords from HTML cards are written directly to paper score records; only an already analyzed HTML card with no keyword section may read <code>data/keywords/keywords.db</code> as a read-only fallback for that same paper. The old cache's normalized terms, aliases, and derived counts are not migrated: the current SQLite normalization workflow owns them. **Check and Repair History** fills missing TLDRs, translations, or deep analyses from SQLite and patches original reports. Range-scan omissions enter the supplement queue by ISO calendar week. Each report follows the configured run cap, and remaining rows stay retryable.

Complete v4 records have a higher completeness priority, so their existing content remains available during import.
</details>

<details>
<summary><b>5. How does a large past-daily date range resume after interruption?</b></summary>

Each target date becomes a persistent <code>backfill_queue</code> row. The worker claims dates in order, and a run cap continues remaining papers for the same date in a later batch. After a container restart, incomplete dates return to pending status.

Outcome notifications summarize completed dates, deferred items, and failure summaries for the next run.
</details>

<details>
<summary><b>6. How should SQLite backups, local snapshots, and WebDAV archives be restored?</b></summary>

Today’s local snapshots provide recent-run recovery, while older dates retain their newest copy. WebDAV preserves archives uploaded after data changes and supports cross-device recovery.

Recommended steps:

1. stop daily, import, and supplement tasks
2. export a current zip from System → Backup & Sync as a protection copy
3. import the target zip, gz, or db archive and review validation results
4. restore reports, keywords, and configuration as required
5. restart the worker and inspect System → Runtime Diagnostics and queue state
</details>

<details>
<summary><b>7. How can Docker connect to local Ollama, vLLM, or LocalAI?</b></summary>

The worker uses the host network, so a local Linux/NAS OpenAI-compatible service can use:

~~~env
CHEAP_LLM__BASE_URL=http://127.0.0.1:11434/v1
~~~

Align model-service listening addresses, reverse-proxy rules, and firewall policy with the deployment topology. The WebUI and worker share host-network semantics; verify external API addresses with **Configuration → API → Test Connection**.
</details>

<details>
<summary><b>8. How can I tune Core Relevance V2 when the pass rate is low?</b></summary>

V2 requires the weighted average relevance of primary keywords and at least one strong primary-keyword match. Add clear, identifiable primary keywords in **Keywords**, then tune the core-relevance and strong-match thresholds in **Scoring**.

Reference keywords, expert authors, and favorites rank qualified papers. Primary keywords express the research topic.
</details>

<details>
<summary><b>9. How do GitHub Actions preserve SQLite state and avoid conflicting runs?</b></summary>

<code>daily-run.yml</code> and <code>trend-research.yml</code> share an Actions-cache prefix for <code>data/daily_research/</code> and keyword data, and use one concurrency group to serialize execution. Each cloud run saves a new state snapshot when it finishes.

Docker with persistent volumes is a strong fit for continuous operation. Actions works well for temporary cloud runs, validation, and manual trend research.
</details>

<details>
<summary><b>10. How should I upgrade after an update notification?</b></summary>

Update checks compare GitHub Releases and send a notification through enabled channels. Read the Release and CHANGELOG, export SQLite, then pin a new GHCR version or pull source and rebuild.

~~~bash
docker compose pull
docker compose up -d --no-build --force-recreate
docker compose ps
~~~

After the upgrade, inspect Runtime Diagnostics, queues, and recent logs to confirm compatibility with the current configuration.
</details>

---

## 📜 License

This project is licensed under [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html).

| Term | Description |
| :--- | :---------- |
| ✅ Use | Free to use, modify, and distribute |
| ✅ Commercial | Commercial use is allowed |
| 📋 Source disclosure | Modified versions must provide source under the same license |
| 🌐 Network use | Providing the service over a network also requires source disclosure |
| 📝 Attribution | Preserve original copyright and license notices |

---

## 💬 Community and Feedback

Contributions are welcome:

- **🐛 Report an issue**: [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues) — include reproduction steps, version, redacted logs, and deployment method
- **🔀 Contribute code**: Fork the repository and open a Pull Request
- **⭐ Star**: If the project helps your research, a Star is appreciated

---

## 🤝 API Use

Configuration and runtime behavior help deployments follow external-service requirements:

| API | Project-side behavior |
| :-- | :-------------------- |
| **ArXiv** | Category pagination with backoff, Retry-After handling, and scan receipts |
| **OpenAlex** | Called for enabled additional sources; supports an optional API key |
| **Semantic Scholar** | Optional enrichment source with API-key support, rate limits, and safe error summaries |
| **MinerU** | API page includes connection testing and an official console link; usage follows account quotas |

Review each provider’s current policy, quota, and account requirements before deployment. External calls use shared timeout, retry, and proxy-scope settings.

---

## 🙏 Acknowledgments

- [ArXiv](https://arxiv.org/), [OpenAlex](https://openalex.org/), and [Semantic Scholar](https://www.semanticscholar.org/) for academic-data services
- [MinerU](https://mineru.net/) for PDF parsing services
- The open-source communities behind Python, Docker, Starlette, Uvicorn, and related tools

---

## 📝 Changelog

See **[CHANGELOG.md](CHANGELOG.md)** for the complete version history.

### Latest Version Summary

<table>
<tr><th>Version</th><th>Date</th><th>Type</th><th>Highlights</th></tr>
<tr><td><b>v4.2</b></td><td>2026-08-30</td><td>🛡️ Operations & reliability</td><td>Database restores are mutually exclusive with active work through a shared activity lock. Live configuration moves to Git-ignored <code>runtime/</code>, with a tracked example, automatic legacy migration, and stable WebDAV archive names.</td></tr>
<tr><td><b>v4.1</b></td><td>2026-08-30</td><td>✨ Enhancements + fixes</td><td>Modern WebUI is the default panel with four navigation groups, 18 focused pages, account management, and local updates. Legacy maintenance, cross-source merging, LLM endpoint capability detection, runtime diagnostics, and Token line trends are expanded together.</td></tr>
<tr><td><b>v4.0</b></td><td>2026-08-25</td><td>🚀 Major release</td><td>SQLite daily-history system, durable candidate and retry queues, complete scan receipts, Core Relevance V2, favorites, legacy import with automatic supplement reports, past-daily date-range queues, SQLite backups with incremental WebDAV archive, LLM health, workflow notifications, GHCR AMD64/ARM64 images, and release regression.</td></tr>
<tr><td><b>v3.2</b></td><td>2026-04-26</td><td>✨ Enhancements + fixes</td><td>Network proxy, WebDAV data sync, configuration export, Docker update notifications, Daily Push tab, Markdown/HTML output switches, and trend-analysis output settings.</td></tr>
<tr><td><b>v3.1</b></td><td>2026-04-15</td><td>✨ Enhancements + fixes</td><td>Run management, log viewer, Trend Analysis tab, report-view improvements, ArXiv timeout guard, and run-lock improvements.</td></tr>
<tr><td><b>v3.0</b></td><td>2026-03-09</td><td>✨ Major release</td><td>Trend research, token tracking, setup wizard, concurrent locks, per-run logs, Streamlit configuration panel, and keyword-trend reports.</td></tr>
</table>

[View the complete history →](CHANGELOG.md)

---

<div align="center">

If this project helps your research, consider giving it a **Star** ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#/yzr278892/arxiv-daily-researcher&Date)

[![Issues](https://img.shields.io/github/issues/yzr278892/arxiv-daily-researcher?style=flat-square&label=Issues)](https://github.com/yzr278892/arxiv-daily-researcher/issues)

</div>
