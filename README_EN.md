<div align="center">

<img src="assets/icon.svg" alt="ArXiv Daily Researcher" width="88" />

# ArXiv Daily Researcher

**Track papers, filter by research interests, and generate research reports in Chinese**

[![Release](https://img.shields.io/github/v/release/yzr278892/arxiv-daily-researcher?label=release)](https://github.com/yzr278892/arxiv-daily-researcher/releases)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](#-deployment)
[![中文](https://img.shields.io/badge/README-中文-red.svg)](README.md)

*Daily literature tracking, topic research, and paper archives.*

[Quick start](#-quick-start) · [Interface preview](#webui-preview) · [Usage](#-usage) · [Release notes](https://github.com/yzr278892/arxiv-daily-researcher/releases)

</div>

---

ArXiv Daily Researcher regularly searches arXiv and optional journal sources, filters papers by keywords and personal preferences, and produces Chinese summaries, PDF analyses, and Markdown / HTML reports. Configure, run, and read in a browser, with results also available through email and messaging services.

## ✨ Features

<table>
<tr><td colspan="2" align="center"><sub>— Discovery and filtering —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 📡 Multiple paper sources

Track new arXiv papers and revisions, plus PRL, PRA/PRB, Nature, Science, Hugging Face Papers, and custom OpenAlex journals. Link the same paper across sources while retaining each source's records and analyses.

</td>
<td width="50%" valign="top">

### 🎯 Research interest scoring

Choose core relevance, weighted keywords, weighted keywords with penalties, or learned preferences. Configure topics of interest, unwanted topics, and preferred authors; inspect individual scores and qualification reasons.

</td>
</tr>
<tr><td colspan="2" align="center"><sub>— Reading and research —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 🔍 Chinese summaries and full-text analysis

Generate translated abstracts, short TL;DRs, and keywords. Qualifying papers can receive further PDF analysis of their methods, findings, and limitations. Supports local PyMuPDF, MinerU, and optional translation of Semantic Scholar TL;DRs.

</td>
<td width="50%" valign="top">

### 📈 Topic and keyword trends

Research a topic by keyword, date range, and arXiv category to summarize changes in research directions and methods. Keyword trend reports show how topics develop within the collected papers.

</td>
</tr>
<tr><td colspan="2" align="center"><sub>— Archives and delivery —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 🗃️ Reports, favorites, and history

Browse Markdown / HTML reports, search papers, and record likes or dislikes. Automatically save qualifying papers, rerun past dates, import old reports, and repair historical records. Unfinished papers remain queued.

</td>
<td width="50%" valign="top">

### 🔔 Notifications and management

Supports email, WeCom, DingTalk, Telegram, Slack, and generic webhooks. The WebUI provides Chinese and English interfaces, light/dark themes, logs, token statistics, database backups, and WebDAV synchronization.

</td>
</tr>
</table>

---

## 📑 Contents

| Section |
| :--- |
| [🚀 Quick start](#-quick-start) |
| [🛠️ Configuration tools](#️-configuration-tools) |
| [🐳 Deployment](#-deployment) |
| [📖 Usage](#-usage) |
| [📁 Project structure](#-project-structure) |
| [❓ FAQ](#-faq) |
| [📝 Changelog](CHANGELOG.md) |

---

## 🚀 Quick start

Use a Linux host with Docker Compose and an OpenAI-compatible model service. The host needs network access to your chosen paper sources and model APIs.

### 1. Get the project

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
mkdir -p data logs runtime
~~~

Run `id -u` and `id -g`, then set `PUID` and `PGID` in `.env` to your user's IDs. That user must own and be able to write to these directories and `.env`.

### 2. Start the services

~~~bash
docker compose pull
docker compose up -d
docker compose ps
~~~

Open `http://HOST:8501`, replacing `HOST` with your server address, and create an administrator account. The panel manages configuration and accounts; access it through a trusted LAN, Tailscale, or a protected reverse proxy.

### 3. Configure and run

Under **Configuration → API**, enter the API key, base URL, and model name for both the low-cost and high-capability LLM, then test each connection. The former handles scoring and translation; the latter handles detailed analysis and trend summaries. Both roles may use the same model.

Enter your research background and primary keywords under **Keywords**, select sources and a scoring strategy, and click **Save All Changes** in the sidebar. For an initial run, set the daily processing limit to 5 papers; adjust the limit and schedule after reviewing the results.

Start a task under **Run → Daily Research** and read its results under **Content → Reports**.

---

## 🛠️ Configuration tools

### 🖥️ WebUI

| Group | Pages |
| :--- | :--- |
| Run | Daily research, past daily reports, trend tasks |
| Content | Reports, favorites, paper search |
| Configuration | Keywords, data sources, scoring, API, notifications, advanced settings, accounts |
| System | Backup and sync, history maintenance, diagnostics, usage statistics, logs |

Use **Save All Changes** for configuration edits. Source switches control which settings are shown, and each notification channel has its own test action. The sidebar shows the installed version and links to a release when an update is available.

### 🧙 Command-line wizard

Configure models, research topics, sources, and notifications from a terminal:

~~~bash
docker compose exec arxiv-daily-researcher python src/utils/setup_wizard.py
~~~

<a id="webui-preview"></a>

### 🖼️ Interface preview

<table>
  <tr>
    <td align="center" width="50%"><a href="assets/webui_daily_push_v4.png"><img src="assets/webui_daily_push_v4.png" alt="Daily research tasks and queue" width="100%" /></a><br /><b>Daily research</b><br /><sub>Task progress and pending papers</sub></td>
    <td align="center" width="50%"><a href="assets/webui_scoring_v4.png"><img src="assets/webui_scoring_v4.png" alt="Four paper scoring strategies" width="100%" /></a><br /><b>Scoring</b><br /><sub>Relevance, weights, and penalties</sub></td>
  </tr>
  <tr>
    <td align="center" width="50%"><a href="assets/webui_api_semantic_v4.png"><img src="assets/webui_api_semantic_v4.png" alt="Semantic Scholar TL;DR translation settings" width="100%" /></a><br /><b>API configuration</b><br /><sub>Source services and TL;DR translation</sub></td>
    <td align="center" width="50%"><a href="assets/webui_analytics_v4.png"><img src="assets/webui_analytics_v4.png" alt="Regular input, cached input, and output token usage" width="100%" /></a><br /><b>Usage statistics</b><br /><sub>Input, cache, and output</sub></td>
  </tr>
  <tr>
    <td align="center" width="50%"><a href="assets/webui_history_import_v4.png"><img src="assets/webui_history_import_v4.png" alt="History import and maintenance scheduling" width="100%" /></a><br /><b>History maintenance</b><br /><sub>Import, repair, and scheduling</sub></td>
    <td align="center" width="50%"><a href="assets/webui_data_management_v4.png"><img src="assets/webui_data_management_v4.png" alt="Database backups and restore" width="100%" /></a><br /><b>Backup and sync</b><br /><sub>Database snapshots and recovery</sub></td>
  </tr>
</table>

<sub>Screenshots use demonstration settings and simulated usage data.</sub>

---

## 🐳 Deployment

### Docker Compose

The root `docker-compose.yml` starts two services. Both images support `linux/amd64` and `linux/arm64`:

| Service | Image | Purpose |
| :--- | :--- | :--- |
| Worker | `ghcr.io/yzr278892/arxiv-daily-researcher:4.6` | Scheduled runs, paper processing, and notifications |
| WebUI | `ghcr.io/yzr278892/arxiv-daily-researcher-config-panel:4.6` | Management interface, mapped to `8501:8501` |

The worker uses host networking; the WebUI uses a bridge network. For a model or proxy running on the host, use a host address reachable from both containers. `localhost` refers to a different location in each network.

Before upgrading, back up `.env`, `runtime/`, `data/`, and customized `configs/templates/`; keep `logs/` if needed. Read the [release notes](https://github.com/yzr278892/arxiv-daily-researcher/releases). A routine upgrade uses:

~~~bash
git pull --ff-only
docker compose pull
docker compose up -d
docker compose ps
~~~

Mounted directories preserve your data. A legacy `configs/config.json` is migrated to `runtime/config.json` on first startup.

### Source development

`tests/docker-compose.yml` builds the worker and WebUI from source, with the worker handling only manually submitted tasks. It mounts the current workspace's settings and data by default; use a separate working copy for development tests.

~~~bash
docker compose -f tests/docker-compose.yml up -d --build
docker compose -f tests/docker-compose.yml ps
~~~

The repository also includes the `main.py` command-line entry point and GitHub Actions workflows for daily and trend research.

---

## 📖 Usage

### 🎯 Scoring strategies

| Strategy | Qualification |
| :--- | :--- |
| Core relevance V2 | Primary keyword relevance determines qualification; reference keywords and author preferences help rank papers. |
| Weighted keywords V1 | Keyword relevance × weight, plus author bonuses, is compared with a dynamic threshold. |
| Weighted keywords with penalties | Uses the weighted keyword score and threshold, subtracting unwanted keyword relevance × its individual weight. |
| Learned preferences V1 | Adjusts weighted scores using signals from likes, dislikes, and previously qualifying papers. |

Selecting the penalty strategy shows unwanted keyword settings on the **Keywords** page. Each penalty weight ranges from 0 to 1; a primary keyword cannot also be a penalty term.

### 📄 Research tasks and reports

Daily research handles new papers and the unfinished queue. Past daily reports rerun specified dates; trend tasks summarize papers for a topic and date range. The paper limit applies to each run, with failed or unprocessed papers available for later runs.

Browse reports by actual batch. **Other Reports** contains keyword trends and supplements generated by history maintenance. Qualifying papers can be saved automatically or added together from the favorites page.

### 📜 History maintenance

| Action | Purpose |
| :--- | :--- |
| Import old history | Import papers and results from old HTML reports and compatible history records. |
| Repair historical data | Fill missing content in existing records and update reports. |
| Scan historical omissions | Search for missing papers within the dates covered by imported reports. |
| Migrate existing supplements | Organize old supplement filenames, directories, and associated records. |

The first three tasks can run while the worker is idle or during a chosen window, defaulting to 00:00–06:00. History maintenance and daily research have separate processing limits.

### 📊 Usage and backups

Usage statistics show regular input, cached input, and output tokens by model and time. Historical report statistics can be imported; input without a recorded cache breakdown counts as regular input.

Local backups take consistent SQLite snapshots with configurable retention and a daily copy limit. WebDAV can synchronize settings, reports, and databases. Migrating the entire service also requires `.env`, `runtime/`, `data/`, and customized templates.

---

## 📁 Project structure

~~~text
arxiv-daily-researcher/
├── docker-compose.yml         Released image deployment
├── tests/docker-compose.yml   Source development deployment
├── main.py                    Command-line entry point
├── src/                       Sources, scoring, analysis, reports, and WebUI
├── configs/                   Examples and report/notification templates
├── runtime/config.json        Active runtime configuration
├── data/                      Paper database, reports, PDFs, and backups
├── logs/                      Runtime logs
└── .env                       Model credentials, notifications, and accounts
~~~

---

## ❓ FAQ

<details>
<summary><b>What if the WebUI is unreachable or I cannot sign in?</b></summary>

Check `config-panel` with `docker compose ps`, and confirm host port 8501 is accessible and not already in use. Read its logs with `docker compose logs --tail=100 config-panel`. First access requires an administrator account; existing accounts are stored in `.env` and survive container recreation. An authenticated owner can manage other accounts under **Configuration → Accounts**.

</details>

<details>
<summary><b>Why is a submitted task still waiting?</b></summary>

Confirm the worker is running. Daily research, past reports, and history maintenance queue according to their mutual exclusion rules; maintenance also follows its configured time window. The status panel shows why a task is waiting. Inspect worker logs with `docker compose logs --tail=100 arxiv-daily-researcher`.

</details>

<details>
<summary><b>How do I change which papers pass the filter?</b></summary>

Read the keyword scores and qualification reasons in a report, then adjust primary keywords, weights, or thresholds. Penalties help reduce nearby but unwanted topics; core relevance requires a substantive match with primary keywords. New settings apply to subsequent processing; existing reports retain their original scores.

</details>

<details>
<summary><b>What should I check after a timeout, HTTP 429, or PDF analysis failure?</b></summary>

Find the failing stage under **System → Diagnostics** and test connections under **Configuration → API**. Check API keys, model names, base URLs, quotas, and proxy settings. Reduce concurrency for rate limits. For PDF failures, check the source link or MinerU settings. After fixing the cause, run again; completed stages are reused.

</details>

<details>
<summary><b>Why are some Semantic Scholar TL;DRs still in English?</b></summary>

Enable TL;DR translation under **Configuration → API → Semantic Scholar**; it is on by default. Failed translations retain the original text in a collapsed section. Disabling translation displays the original directly. The setting applies to subsequent processing; saving it does not rewrite existing reports.

</details>

<details>
<summary><b>How do I fix an unwritable mounted directory?</b></summary>

Match `PUID` / `PGID` in `.env` to the mounted directories' owner. Before the first deployment, create `data/`, `logs/`, and `runtime/` as that user and make sure `.env` is writable. Restart the containers after correcting permissions.

</details>

<details>
<summary><b>What should I copy when moving to another host?</b></summary>

Wait for tasks to finish and stop the old services. Copy `.env`, `runtime/`, `data/`, and customized `configs/templates/`, plus `logs/` if needed. Check user IDs and directory permissions on the new host before starting both services. A database export from the WebUI covers the database only, so retain the other files separately.

</details>

---

## 📜 License

Licensed under [AGPL-3.0](LICENSE).

## 💬 Feedback

Use [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues) for problems and suggestions. Include your deployment method, reproduction steps, and sanitized logs.

## 🙏 Acknowledgments

Thanks to [arXiv](https://arxiv.org/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/), and [MinerU](https://mineru.net/) for their data and tools.

## 📝 Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history and upgrade notes.
