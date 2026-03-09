<div align="center">

<h1>🔬 ArXiv Daily Researcher</h1>

<p><strong>面向科研场景的多数据源论文监控、筛选、深度分析与趋势研究系统（v3.0）</strong></p>

<p>
  <a href="https://www.python.org/downloads/"><img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white"></a>
  <a href="https://www.docker.com/"><img alt="Docker" src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white"></a>
  <a href="https://github.com/features/actions"><img alt="GitHub Actions" src="https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions&logoColor=white"></a>
  <a href="https://streamlit.io/"><img alt="Streamlit" src="https://img.shields.io/badge/Web_UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white"></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img alt="License" src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg"></a>
</p>

<p>
  <em>每天自动收集论文、智能筛选、生成报告与通知；需要时一条命令做跨时间段趋势研究。</em>
</p>

</div>

---

## 📌 项目定位

ArXiv Daily Researcher 是一个 **双模式（daily_research / trend_research）** 的科研自动化系统：

- **daily_research（默认）**：每天从 ArXiv + 期刊抓取论文，关键词加权评分，及格论文深度分析，输出报告并通知。
- **trend_research**：按关键词与时间范围检索 ArXiv，批量生成 TLDR，并通过 Skills 系统完成研究趋势分析。

同时内置：关键词趋势追踪（SQLite + AI 标准化）、Token 统计、配置向导、Web 配置面板、Docker 与 GitHub Actions 部署。

---

## 🧭 导航目录

<table>
  <tr>
    <td width="33%" valign="top">
      <b>快速开始</b><br>
      • <a href="#-核心能力总览">核心能力总览</a><br>
      • <a href="#-系统工作流">系统工作流</a><br>
      • <a href="#-5-分钟快速启动">5 分钟快速启动</a>
    </td>
    <td width="33%" valign="top">
      <b>运行与部署</b><br>
      • <a href="#-运行模式与命令">运行模式与命令</a><br>
      • <a href="#-docker-部署">Docker 部署</a><br>
      • <a href="#️-github-actions-云端运行">GitHub Actions 云端运行</a>
    </td>
    <td width="33%" valign="top">
      <b>配置与进阶</b><br>
      • <a href="#️-配置入口向导--web-ui">配置入口（向导 / Web UI）</a><br>
      • <a href="#️-关键配置速览">关键配置速览</a><br>
      • <a href="#-复杂问题-qa">复杂问题 QA</a>
    </td>
  </tr>
</table>

---

## ✨ 核心能力总览

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>📡 多数据源抓取</h3>
      ArXiv + OpenAlex 期刊源（PRL/PRA/Nature/Science 等），支持按数据源独立抓取上限，带历史去重。
    </td>
    <td width="50%" valign="top">
      <h3>🎯 双 LLM 评分体系</h3>
      CHEAP_LLM 逐关键词打分（0-10）+ 动态及格线 + 可选作者加分，支持主关键词与参考文献关键词融合。
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🔍 深度 PDF 分析</h3>
      SMART_LLM 对及格论文做深度分析；PDF 解析支持 MinerU（云）与 PyMuPDF（本地）并自动降级。
    </td>
    <td width="50%" valign="top">
      <h3>📈 关键词趋势追踪</h3>
      SQLite 记录关键词，AI 标准化归并，输出 Mermaid 图表与 HTML 关键词趋势报告。
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🔬 趋势研究模式（v3.0）</h3>
      指定关键词/时间区间，自动生成 TLDR，并通过 5 个 Skills 维度做全局趋势分析。
    </td>
    <td width="50%" valign="top">
      <h3>🔔 多渠道通知 + Token 追踪</h3>
      邮件（HTML）、企业微信、钉钉、Telegram、Slack、Webhook；报告与通知同步展示 token 消耗。
    </td>
  </tr>
</table>

---

## 🔄 系统工作流

### 1) 每日研究模式（`daily_research`）

```text
自动更新检查
  -> 关键词准备（主关键词 + reference关键词）
  -> 多源抓取（ArXiv / OpenAlex）
  -> 评分筛选（CHEAP_LLM）
  -> 及格论文深度分析（SMART_LLM）
  -> 分数据源生成 Markdown/HTML 报告
  -> 关键词标准化与趋势报告（按频率）
  -> 发送通知（可选）
```

### 2) 趋势研究模式（`trend_research`）

```text
按关键词+时间范围检索 ArXiv
  -> 为每篇论文生成 TLDR（可并发）
  -> Skills 趋势分析（5维）
  -> 生成 Markdown/HTML 趋势报告
  -> 发送通知（可选）
```

---

## 🚀 5 分钟快速启动

### 1. 安装依赖

```bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置（推荐向导）

```bash
python src/utils/setup_wizard.py
```

或手动：

```bash
cp .env.example .env
# 编辑 .env 与 configs/config.json
```

### 3. 运行

```bash
python main.py
```

---

## 🧪 运行模式与命令

### Daily Research（默认）

```bash
python main.py
```

### Trend Research（v3.0）

```bash
python main.py --mode trend_research --keywords "quantum error correction"

python main.py --mode trend_research \
  --keywords "large language model" \
  --date-from 2025-01-01 \
  --date-to 2025-12-31 \
  --sort-order descending \
  --max-results 300
```

### 两种模式对比

| 维度 | `daily_research` | `trend_research` |
| :-- | :-- | :-- |
| 目标 | 每日监控与筛选 | 主题趋势研究 |
| 数据源 | ArXiv + 期刊 | ArXiv（关键词检索） |
| 核心处理 | 评分 + 及格论文深度分析 | 全量 TLDR + Skills 趋势分析 |
| 报告目录 | `data/reports/daily_research/` | `data/reports/trend_research/` |

---

## 🧙 配置入口（向导 / Web UI）

### CLI 配置向导（首次部署推荐）

```bash
python src/utils/setup_wizard.py
```

向导覆盖 7 个阶段：LLM、搜索、数据源、关键词、评分、通知、高级配置。

### Streamlit Web UI（日常调参推荐）

```bash
streamlit run src/webui/config_panel.py
```

浏览器访问 `http://localhost:8501`，6 个 Tab 管理配置：LLM、Search、Keywords、Scoring、Notifications、Advanced。

> 说明：项目当前无 `requirements-webui.txt`，Web UI 依赖已包含在 `requirements.txt`。

---

## 🐳 Docker 部署

```bash
cp .env.example .env
# 编辑 .env

docker compose -f docker/docker-compose.yml up -d
```

常用命令：

```bash
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f
docker compose -f docker/docker-compose.yml run --rm -e MODE=run-once arxiv-researcher
docker compose -f docker/docker-compose.yml down
```

Web UI 容器：

```bash
docker compose -f docker/docker-compose.yml --profile webui up -d config-panel
```

默认环境变量（容器）：

| 变量 | 默认值 | 说明 |
| :-- | :-- | :-- |
| `TZ` | `Asia/Shanghai` | 时区 |
| `CRON_SCHEDULE` | `0 8 * * *` | 每日定时表达式 |
| `RUN_ON_STARTUP` | `true` | 启动时先执行一次 |
| `MODE` | `cron` | `cron` / `run-once` |

---

## ☁️ GitHub Actions 云端运行

工作流文件：`.github/workflows/daily-run.yml`

- 默认计划任务：每天 **UTC 00:00**（北京时间 08:00）
- 支持手动触发 `workflow_dispatch`
- 报告与日志作为 Artifact 保留 30 天
- 历史数据通过 Actions Cache 跨运行保留

必填 Secrets（核心）：

- `CHEAP_LLM_API_KEY` / `CHEAP_LLM_BASE_URL` / `CHEAP_LLM_MODEL_NAME`
- `SMART_LLM_API_KEY` / `SMART_LLM_BASE_URL` / `SMART_LLM_MODEL_NAME`

---

## ⚙️ 关键配置速览

<details>
<summary><b>1) 数据源与抓取范围</b></summary>

```jsonc
{
  "search_settings": {
    "search_days": 7,
    "max_results": 100,
    "max_results_per_source": {"arxiv": 150, "prl": 50}
  },
  "data_sources": {
    "enabled": ["arxiv", "prl"],
    "journals": [],
    "reports_by_source": true
  },
  "target_domains": {"domains": ["quant-ph"]}
}
```

</details>

<details>
<summary><b>2) 关键词与评分</b></summary>

```jsonc
{
  "keywords": {
    "primary_keywords": {"weight": 1.0, "keywords": ["quantum computing"]},
    "enable_reference_extraction": true,
    "research_context": "..."
  },
  "scoring_settings": {
    "author_bonus": {"enabled": false, "expert_authors": [], "bonus_points": 5.0},
    "passing_score_formula": {"base_score": 5.0, "weight_coefficient": 3.0}
  }
}
```

评分逻辑：

- 论文总分 = Σ(关键词相关度 × 关键词权重) + 作者加分
- 及格线 = `base_score + weight_coefficient × 关键词总权重`

</details>

<details>
<summary><b>3) PDF 解析、并发、重试</b></summary>

```jsonc
{
  "pdf_parser": {
    "mode": "mineru",
    "mineru_model_version": "pipeline",
    "poll_interval": 3,
    "poll_timeout": 300
  },
  "concurrency": {"enabled": false, "workers": 3},
  "retry": {"max_attempts": 3, "min_wait": 2, "max_wait": 30}
}
```

</details>

<details>
<summary><b>4) 趋势研究模式（v3.0）</b></summary>

```jsonc
{
  "trend_research": {
    "default_date_range_days": 365,
    "max_results": 500,
    "sort_order": "ascending",
    "generate_tldr": true,
    "tldr_batch_size": 10,
    "output_formats": ["markdown", "html"],
    "enabled_skills": [
      "temporal_evolution",
      "hot_topics",
      "key_authors",
      "research_gaps",
      "methodology_trends"
    ]
  }
}
```

</details>

<details>
<summary><b>5) 通知与 Token 追踪</b></summary>

```jsonc
{
  "notifications": {
    "enabled": true,
    "on_success": true,
    "on_failure": true,
    "top_n": 5,
    "channels": {
      "email": {"enabled": false},
      "wechat_work": {"enabled": false},
      "dingtalk": {"enabled": false},
      "telegram": {"enabled": false},
      "slack": {"enabled": false},
      "generic_webhook": {"enabled": false}
    }
  },
  "token_tracking": {"enabled": true}
}
```

</details>

---

## 🧱 项目结构（按职责）

```text
main.py                          # 入口：选择 daily_research / trend_research
src/
  config.py                      # 配置加载与合并（.env + config.json）
  modes/
    daily_research.py            # 每日研究主流程
    trend_research.py            # 趋势研究主流程
  agents/
    analysis_agent.py            # 评分 + 深度分析
    keyword_agent.py             # 参考PDF关键词提取
    trend_agent.py               # TLDR + Skills趋势分析
  sources/
    arxiv_source.py              # ArXiv 源
    openalex_source.py           # 期刊源（OpenAlex）
    semantic_scholar_enricher.py # TLDR/arXiv信息增强
    search_agent.py              # 多源调度与编排
  report/
    daily/                       # 每日报告（Markdown/HTML）
    trend/                       # 趋势研究报告
    keyword_trend/               # 关键词趋势报告
  keyword_tracker/
    database.py                  # SQLite
    normalizer.py                # AI 标准化
    tracker.py                   # 趋势聚合与图表输出
  notifications/
    notifier.py                  # 通知渠道编排
  webui/
    config_panel.py              # Streamlit 配置面板
  utils/
    setup_wizard.py              # 交互式配置向导
```

---

## ❓ 复杂问题 QA

<details>
<summary><b>1) Docker 内访问本地 LLM 失败（连接拒绝）</b></summary>

本项目 compose 使用 `network_mode: host`，容器可直接访问宿主机网络；`.env` 中用 `127.0.0.1` 即可。

</details>

<details>
<summary><b>2) 429 限速频繁触发</b></summary>

建议顺序：降低并发（`workers`）、减小 `max_results`、配置 `OPENALEX_EMAIL`、必要时增大重试等待上限。

</details>

<details>
<summary><b>3) 期刊论文无法做深度分析</b></summary>

期刊源默认不保证 PDF；系统会优先尝试用 DOI 从 Semantic Scholar 找 arXiv 版本，若无可用 PDF，则仅进行评分/摘要相关流程。

</details>

<details>
<summary><b>4) MinerU 异常时是否中断任务</b></summary>

不会。`mode=mineru` 下解析失败会自动降级到 PyMuPDF，本次任务继续执行。

</details>

<details>
<summary><b>5) 关键词标准化误合并</b></summary>

可关闭 `keyword_tracker.normalization.enabled`，或将 `batch_size` 调小后观察归并质量。

</details>

<details>
<summary><b>6) 如何清空历史并重新处理全部论文</b></summary>

删除 `data/history/` 下历史文件后，下次运行会视为全新数据。

</details>

<details>
<summary><b>7) trend_research 能否自动定时</b></summary>

当前默认调度链路（Docker/GitHub Actions）面向 daily 模式；trend 建议通过外部 cron/调度器按需触发命令行。

</details>

---

## 📝 版本与变更说明

- 当前主版本：**v3.0（2026-03-09）**
- v3.0 核心新增：trend_research、Token 统计、关键词趋势 HTML 报告、配置向导、Streamlit 配置面板。

<details>
<summary><b>查看历史版本变更（折叠）</b></summary>

详见仓库提交历史与 `README` 旧版记录：v2.3 / v2.2 / v2.1 / v2.0 / v1.x。

</details>

---

## 📜 许可证

本项目采用 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) 许可证。

---

<div align="center">

如果这个项目对你有帮助，欢迎点一个 **Star** ⭐

<a href="https://github.com/yzr278892/arxiv-daily-researcher/issues">提交问题</a>
·
<a href="mailto:yzr278892@gmail.com">联系作者</a>

</div>
