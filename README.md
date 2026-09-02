<div align="center">

# 🔬 ArXiv Daily Researcher

**基于 LLM 的论文监控、筛选、分析、报告与研究归档系统**

[![Version](https://img.shields.io/badge/version-v4.4-brightgreen.svg)](CHANGELOG.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Supported-2088FF?logo=github-actions)](https://github.com/features/actions)
[![Modern WebUI](https://img.shields.io/badge/Config_Panel-Modern_WebUI-465FD5)](#️-现代管理-webui)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README_EN.md)

*用于持续文献追踪、专题研究和历史资料整理。*

</div>

---

ArXiv Daily Researcher 从 ArXiv 和可选扩展来源收集论文，按研究主题评分、生成摘要翻译和 PDF 分析，并交付 Markdown、HTML 报告与通知。

v4.4 使用 SQLite 保存候选、处理阶段、报告交付、通知发件箱、收藏偏好、历史维护积压与过去日报队列。补充报告独立归档，可将旧目录中的补充报告连同 SQLite 路径一次迁移。LLM 请求将稳定指令置于前缀以利用提供商缓存；Token 用量分别记录普通输入、缓存输入和输出。任务可从已完成阶段恢复；运行配置与源码分离，便于长期部署、升级和备份恢复。Docker Worker 在没有 WebUI 请求时不再重复启动 Python 任务选择器，例行健康检查也只校验本地存活状态，降低待机 CPU 占用。

---

## ✨ 核心功能

<table>
<tr>
<td width="50%" valign="top">

### 📡 多来源与精确交付

ArXiv 完整分页扫描首次提交和最后更新，记录来源扫描收据。可启用 PRL、PRA/PRB、Nature、Science、Hugging Face Papers、声明式期刊来源，以及 OpenAlex、Semantic Scholar 增强。同一论文按稳定身份归并，来源变体保留可查。

</td>
<td width="50%" valign="top">

### 🎯 评分与内容分析

提供核心相关性 V2、加权关键词 V1 和偏好学习 V1。CHEAP_LLM 用于评分、译文、关键词和 TLDR；SMART_LLM 用于 PDF 深度分析和趋势综合。支持本地 PyMuPDF 与 MinerU。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🗃️ 队列、重试与历史维护

候选先写入 SQLite，再按优先级处理；失败阶段可重试，报告交付与通知发件箱原子提交。旧历史导入、字段补全、遗漏扫描、补充报告和过去日报均有持久化状态。

</td>
<td width="50%" valign="top">

### 📄 报告、收藏与检索

每日研究、补充报告、过去日报、趋势研究和关键词趋势均支持 HTML/Markdown。补充报告与关键词趋势归入“其他报告”；日报和补充报告都按实际批次前后浏览。收藏、偏好和全文检索基于 SQLite 保存。

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔔 通知与可观测性

支持邮件、企业微信、钉钉、Telegram、Slack 和通用 Webhook。每个渠道可发送测试通知；运行、历史维护、来源、LLM 与 Token 用量均提供状态和问题摘要。用量按普通输入、缓存输入、输出和模型展示，并写入报告与通知。

</td>
<td width="50%" valign="top">

### 🖥️ 现代管理 WebUI

独立 ASGI 面板覆盖运行、内容、配置和系统四组共 18 个页面，支持中英文、浅色/深色主题、账户管理和局部刷新。常用远程访问地址为 `http://<宿主机>:8501`。

</td>
</tr>
</table>

---

## 📑 导航目录

<table>
<tr>
<td width="50%" valign="top">

### 📘 快速上手

| 章节 | 内容 |
| :--: | :--- |
| [✨ 核心功能](#-核心功能) | 功能概览 |
| [🚀 快速开始](#-快速开始) | 首次部署、配置与运行 |
| [🛠️ 配置工具](#️-配置工具) | 配置向导、WebUI 与截图 |
| [🐳 部署方式](#-部署方式) | 用户部署、开发测试与升级 |

</td>
<td width="50%" valign="top">

### 📗 深入了解

| 章节 | 内容 |
| :--: | :--- |
| [📖 功能详解](#-功能详解) | 工作流、历史维护、报告和通知 |
| [📁 项目结构](#-项目结构) | 代码、运行数据与测试 Compose |
| [❓ 常见问题](#-常见问题) | 部署、任务和恢复排查 |
| [📝 更新日志](CHANGELOG.md) | 版本与兼容性记录 |

</td>
</tr>
</table>

---

## 🚀 快速开始

### 第一步：获取项目

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
~~~

### 第二步：填写配置

至少在 `.env` 中填写两组 LLM 参数；其他内容可在 WebUI 完成：

~~~env
CHEAP_LLM__API_KEY=sk-your-key
CHEAP_LLM__BASE_URL=https://api.openai.com/v1
CHEAP_LLM__MODEL_NAME=gpt-4o-mini

SMART_LLM__API_KEY=sk-your-key
SMART_LLM__BASE_URL=https://api.openai.com/v1
SMART_LLM__MODEL_NAME=gpt-4o
~~~

运行配置位于 Git 忽略的 `runtime/config.json`，示例文件为 `configs/config.example.json`。首次部署可在 WebUI 或配置向导中创建运行配置。

从 v4.1 或更早源码部署升级时，如 `configs/config.json` 仍存在，请先执行一次：

~~~bash
if [ -f configs/config.json ] && [ ! -f runtime/config.json ]; then
  mkdir -p runtime
  mv configs/config.json runtime/config.json
fi
~~~

### 第三步：启动用户部署

根目录 `docker-compose.yml` 使用官方 GHCR 镜像：

~~~bash
docker compose pull
docker compose up -d
docker compose ps
~~~

打开 `http://<宿主机>:8501`，完成管理员账户初始化、研究主题、数据源、评分和通知配置。面板端口固定映射为 `8501:8501`；只应通过受控 LAN、Tailnet、反向代理或防火墙规则暴露。

首次研究建议将“本次最多处理论文数”设为 `5`，验证报告、SQLite、通知和日志后再调整为日常值或 `0`（处理全部可用队列）。

运行数据默认保存在：

- 报告：`data/reports/`
- SQLite：`data/daily_research/daily_research.db`
- 备份：`data/backups/`
- 日志：`logs/`

---

## 🛠️ 配置工具

### 🧙 交互式配置向导

适合首次部署、SSH 或无浏览器环境：

~~~bash
python src/utils/setup_wizard.py
~~~

| 步骤 | 内容 |
| :--: | :--- |
| 1 | CHEAP_LLM、SMART_LLM 与连接参数 |
| 2 | ArXiv、分类、额外来源与第三方 API |
| 3 | 研究背景、关键词与参考文献关键词 |
| 4 | 评分策略、阈值、权重与作者偏好 |
| 5 | 通知渠道和任务通知选项 |
| 6 | PDF、并发、重试、代理、备份与 WebDAV |

### 🖥️ 现代管理 WebUI

本地直接运行：

~~~bash
uvicorn src.modern_webui.app:app --host 127.0.0.1 --port 8501
~~~

Docker 部署由 `config-panel` 提供服务。WebUI 与 worker 共享 `.env`、`runtime/`、`configs/`、`data/` 和 `logs/`；保存设置后，后续任务会读取新配置。左侧“保存所有更改”统一保存各配置页内容。

“用量统计”可按报告批次从归档 Markdown/HTML 导入历史 Token 用量。导入会与 SQLite 中已有运行记录去重，重复执行不会累计；Markdown 保留的模型拆分和缓存输入会一并写入。旧报告未区分缓存输入时，原有输入按普通输入保存。

| 分组 | 页面 | 用途 |
| :--- | :--- | :--- |
| 运行 | 每日研究、过去日报、趋势任务 | 启动任务、查看队列、状态与日志 |
| 内容 | 报告、收藏、检索 | 浏览各类报告、管理收藏、检索论文库 |
| 配置 | 关键词、数据源、评分、API、通知、高级设置、账户 | 维护研究和运行参数 |
| 系统 | 备份与同步、历史维护、运行诊断、用量统计、日志 | 维护数据与排查运行问题 |

### 🖼️ WebUI 界面预览

<table>
  <tr>
    <td align="center" width="33%">
      <img src="assets/webui_daily_push_v4.png" alt="每日研究状态与队列" width="100%" />
      <br />
      <sub>每日研究、状态和队列</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_analytics_v4.png" alt="普通输入、缓存输入、输出 Token 与历史导入" width="100%" />
      <br />
      <sub>普通输入、缓存输入、趋势和历史导入</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_scoring_v4.png" alt="评分策略与作者偏好" width="100%" />
      <br />
      <sub>评分策略与资格判断</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <img src="assets/webui_advanced_v4.png" alt="高级设置与代理" width="100%" />
      <br />
      <sub>PDF、并发和代理设置</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_data_management_v4.png" alt="备份与同步" width="100%" />
      <br />
      <sub>本地备份和 WebDAV</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/webui_history_import_v4.png" alt="历史维护与补充报告迁移" width="100%" />
      <br />
      <sub>历史维护、运行方式与补充报告迁移</sub>
    </td>
  </tr>
</table>

截图使用当前版本的脱敏测试配置生成，不包含 API Key、密码、Webhook、邮箱、内网地址、真实报告或本机路径。

---

## 🐳 部署方式

### 用户部署：根目录 Compose <sup>推荐</sup>

根目录 `docker-compose.yml` 只用于实际部署，固定引用与 v4.4 Release 对应的官方镜像：

| 服务 | 镜像 | 网络与入口 |
| :--- | :--- | :--- |
| `arxiv-daily-researcher` | `ghcr.io/yzr278892/arxiv-daily-researcher:4.4` | 宿主机网络；cron、任务队列和 worker |
| `config-panel` | `ghcr.io/yzr278892/arxiv-daily-researcher-config-panel:4.4` | bridge 网络；`8501:8501` WebUI |

worker 使用宿主机网络，可直接访问宿主机的本地 LLM 或代理。WebUI 使用显式端口映射；需要从 WebUI 测试宿主机服务时使用 `host.docker.internal`。

常用命令：

~~~bash
# 拉取指定版本并启动
docker compose pull
docker compose up -d

# 状态、日志和健康检查
docker compose ps
docker compose logs -f arxiv-daily-researcher
docker compose logs -f config-panel

# 升级到仓库中指定的新版本
git pull
docker compose pull
docker compose up -d --force-recreate

# 停止服务（不删除 data、logs、runtime）
docker compose down
~~~

`PUID` 和 `PGID` 默认是 `1000`。NAS 或非默认用户部署前，请在 `.env` 中设置为宿主机实际 UID/GID，避免挂载目录由 root 写入。

### 开发测试：`tests/docker-compose.yml`

`tests/docker-compose.yml` 只用于本机源码构建、功能验证和截图，不发布或替代日常运行镜像：

~~~bash
# 在仓库根目录执行；构建当前工作树中的 worker 与 WebUI
docker compose -f tests/docker-compose.yml up -d --build
docker compose -f tests/docker-compose.yml ps

# 查看测试容器日志
docker compose -f tests/docker-compose.yml logs -f worker
docker compose -f tests/docker-compose.yml logs -f config-panel

# 结束测试容器
docker compose -f tests/docker-compose.yml down
~~~

测试 worker 使用 `MODE=manual`：不会按 cron 自动运行每日研究，但可从 WebUI 手动触发任务。它复用当前工作区的 `.env`、`runtime/`、`data/` 和 `logs/`，因此不要与根目录用户部署同时运行；两者会竞争相同的端口和运行数据。需要隔离测试时，请在单独的工作副本中执行。

### GitHub Actions 与本地 CLI

仓库提供每日研究、趋势研究、完整回归和镜像发布工作流。Actions 适合临时或云端运行；长期状态建议使用 Docker 持久化目录。

本地 Python 运行示例：

~~~bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-core.txt -r requirements-webui.txt
python main.py

# 专题趋势研究
python main.py --mode trend_research --keywords "quantum error correction"
~~~

---

## 📖 功能详解

### 🔄 每日研究与趋势研究

| 维度 | `daily_research` | `trend_research` |
| :--- | :--- | :--- |
| 目标 | 持续追踪近期论文与修订版 | 围绕主题进行专题研究 |
| 范围 | 固定回看最近 3 天，结合水位线恢复 | 指定关键词、日期范围和分类 |
| 处理 | 评分、译文、关键词、可选 PDF 分析 | 逐篇 TLDR 与综合趋势分析 |
| 输出 | 每日、补充或过去日报 | Markdown、HTML 与 metadata |
| 触发 | cron、WebUI、CLI、Actions | WebUI、CLI、Actions |

每日研究完整扫描来源后先写入 SQLite。单次处理上限只限制后续评分与分析；未处理和失败论文保留在队列，下一次优先恢复。

### 📜 历史维护与补充报告

| 任务 | 作用 | 运行方式 |
| :--- | :--- | :--- |
| 读取旧历史 | 将旧 HTML 中已有论文登记到交付账本；完整修复时读取兼容 JSON | 闲时或指定时间段 |
| 补全历史数据 | 基于 SQLite 补齐评分、TLDR、译文或深度分析，并回写原报告 | 闲时或指定时间段 |
| 扫描历史遗漏 | 按已导入报告的批次时间范围扫描，按自然周形成补充积压 | 闲时或指定时间段 |
| 补充报告 | 处理积压并生成独立补充报告 | 由维护任务衔接或手动触发 |
| 过去日报 | 按日期范围重跑完整每日流程 | 持久队列，逐日执行 |

历史维护默认在 worker 空闲时运行；也可设置为每日指定时间段，默认 `00:00–06:00`。历史维护与每日研究分别配置每次最多处理的论文数。

旧版本历史导入卡片可将已有补充报告迁移到新目录。迁移会统一命名为 `Supplement_Report_<timestamp>`，并同步更新 SQLite 的运行和论文交付报告路径；运行中的任务会阻止迁移。

### 📄 报告、收藏与检索

- 日报和过去日报位于 `data/reports/daily_research/`；补充报告位于 `data/reports/other_reports/supplement/`；趋势报告和关键词趋势分别位于对应目录。
- 报告查看的“其他报告”包含关键词趋势和补充报告。日报与补充报告按来源、类型和文件时间戳独立批次导航。
- 日报和补充报告预览中的 👍 / 👎 标记写入 SQLite，不修改归档 HTML。收藏页支持自动收藏后续合格论文，以及扫描并收藏现有合格论文。
- 论文检索可按标题、作者、摘要、TLDR、关键词、来源、日期、最低分数和收藏状态过滤。

### 🔔 通知、备份与恢复

大型任务发送一条汇总结果通知，失败或部分完成时只包含阶段和简短原因。通知失败保留在 SQLite outbox，后续任务重试。每个已配置渠道均可在 WebUI 发送测试通知。

| 项目 | 行为 |
| :--- | :--- |
| 本地备份 | SQLite 一致性 gzip 快照；当天保留全部，旧日期每天保留最新一份 |
| WebDAV | 内容变化时增量上传；配置、历史、关键词和报告可分别选择 |
| 恢复 | 先停止写入任务，再在“备份与同步”导出保护副本并导入目标归档 |
| 诊断 | 查看任务、LLM、来源健康、通知积压和 Token 用量 |

### 🔒 运行与访问边界

每日研究、趋势研究、历史维护、补充报告和过去日报使用锁与活动状态协调，避免并发写入 SQLite。数据库恢复会在活动任务期间拒绝执行。

WebUI 默认要求管理员账户；会话、密码哈希和登录限流保存在本地配置中。若通过 LAN 或 Tailnet 访问，请保留认证并配合防火墙、VPN 或反向代理限制来源。

---

## 📁 项目结构

~~~text
arxiv-daily-researcher/
├── main.py                       # CLI 入口
├── VERSION                       # 发布版本
├── docker-compose.yml            # 用户部署：固定 GHCR 镜像
├── docker/
│   ├── Dockerfile                # worker / webui 多阶段镜像
│   └── entrypoint.sh             # 用户 cron 与测试手动模式
├── configs/
│   └── config.example.json       # 运行配置示例
├── runtime/
│   └── config.json               # 本机运行配置（Git 忽略）
├── src/
│   ├── modes/                    # 每日、趋势、历史、补充与补跑任务
│   ├── agents/                   # 评分、分析和关键词组件
│   ├── sources/                  # 数据源
│   ├── notifications/            # 通知与 outbox
│   ├── modern_webui/             # ASGI WebUI、静态前端与 i18n
│   └── utils/                    # SQLite、锁、备份、同步和健康检查
├── data/                         # SQLite、报告、队列和备份（运行时生成）
├── logs/                         # 运行日志（运行时生成）
├── assets/                       # README 脱敏截图
└── tests/
    ├── docker-compose.yml        # 本机源码开发/测试 Compose
    └── test_*.py                 # 回归测试
~~~

---

## ❓ 常见问题

<details>
<summary><b>Docker 部署后如何访问 WebUI？</b></summary>

根目录 Compose 映射 `8501:8501`。在宿主机打开 `http://127.0.0.1:8501`，在受控局域网或 Tailnet 使用 `http://&lt;宿主机&gt;:8501`。不要把未认证面板直接公开到互联网。

</details>

<details>
<summary><b>开发测试为什么使用另一个 Compose 文件？</b></summary>

根目录 Compose 只拉取已发布的 GHCR 镜像，保证用户部署可复现。`tests/docker-compose.yml` 从当前源码构建，并禁止 cron 自动运行，用于验证改动；两者不应同时运行。

</details>

<details>
<summary><b>LLM 超时、429 或部分论文失败后怎么办？</b></summary>

查看“系统 → 运行诊断”的 LLM 健康和阶段摘要。临时网络、限流、5xx、超时或空响应会按策略重试；已完成阶段保留，未完成论文留在 SQLite 队列，修复服务后再次运行即可继续。

</details>

<details>
<summary><b>历史维护为何没有立刻执行？</b></summary>

历史任务会等待每日研究、趋势任务、补充报告和过去日报空闲。若选择了指定时间段，还需处于设置的时间窗口内。状态面板和对应日志会显示等待原因与下一步。

</details>

<details>
<summary><b>如何安全升级？</b></summary>

先导出 SQLite 备份，阅读 Release 和 CHANGELOG，再拉取源码中的新 Compose、执行 `docker compose pull` 和 `docker compose up -d --force-recreate`。升级后检查 `docker compose ps`、运行诊断和最近日志。

</details>

---

## 📜 许可证

本项目采用 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) 许可证。

## 💬 社区与反馈

- [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues)：请提供版本、部署方式、复现步骤和已脱敏日志摘要。
- 欢迎通过 Fork 和 Pull Request 贡献改进。

## 🤝 API 使用说明

请遵守 ArXiv、OpenAlex、Semantic Scholar、MinerU 和所用 LLM 服务的最新政策、配额与账户要求。项目提供超时、重试、限速和代理范围配置，但不替代服务方的使用限制。

## 🙏 致谢

感谢 [ArXiv](https://arxiv.org/)、[OpenAlex](https://openalex.org/)、[Semantic Scholar](https://www.semanticscholar.org/)、[MinerU](https://mineru.net/) 和开源社区提供的服务与工具。

---

## 📝 更新日志

完整变更请查看 [CHANGELOG.md](CHANGELOG.md)。

| 版本 | 日期 | 摘要 |
| :--- | :--- | :--- |
| **v4.4** | 2026-09-02 | Worker 空队列时跳过 Python 任务选择器，例行健康检查改为轻量存活校验，降低待机 CPU 占用。 |
| **v4.3** | 2026-09-02 | 修复历史维护队列消费；补充报告独立归档、浏览与 SQLite 路径迁移；Token 用量区分普通输入、缓存输入和输出。 |
| **v4.2** | 2026-09-01 | 运行配置迁移、历史维护调度、自动收藏、通知测试、报告批次导航、WebUI 局部刷新与用户/测试 Compose 分层。 |
| **v4.1** | 2026-08-30 | 现代 WebUI、历史维护、多来源归并、诊断与 Token 用量。 |
| **v4.0** | 2026-08-25 | SQLite 历史与队列、完整扫描、评分、补充报告、过去日报、备份和 GHCR 双架构镜像。 |

---

<div align="center">

如果项目对你的研究有帮助，欢迎点一个 Star ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=yzr278892/arxiv-daily-researcher&type=Date)](https://star-history.com/#/yzr278892/arxiv-daily-researcher&Date)

</div>
