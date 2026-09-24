<div align="center">

# 🔬 ArXiv Daily Researcher

**论文监控、筛选、分析与研究归档**

[![Release](https://img.shields.io/github/v/release/yzr278892/arxiv-daily-researcher?label=release)](https://github.com/yzr278892/arxiv-daily-researcher/releases)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](#-部署方式)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README_EN.md)

*从论文发现到报告归档，在一个界面中管理。*

</div>

---

ArXiv Daily Researcher 定期检索 arXiv 和已启用的其他来源，按研究主题筛选论文，生成摘要译文、可选的 PDF 分析，以及 Markdown / HTML 报告。WebUI 用于配置、手动运行、查看报告和维护历史数据；Worker 负责定时任务。

---

## ✨ 核心功能

<table>
<tr><td colspan="2" align="center"><sub>— 发现与筛选 —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 📡 多来源检索

扫描 arXiv 首次提交与修订论文；可启用 PRL、PRA/PRB、Nature、Science、Hugging Face Papers 和自定义期刊来源。OpenAlex 与 Semantic Scholar 可补充论文信息。同一论文跨来源合并，来源记录仍可追溯。

</td>
<td width="50%" valign="top">

### 🎯 可配置评分

可按主关键词相关性、加权关键词或收藏偏好筛选论文。可选的加权关键词策略支持为不关注的主题设置独立扣分权重；每次处理篇数可限制，剩余论文保留在队列中。

</td>
</tr>
<tr><td colspan="2" align="center"><sub>— 分析与交付 —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 🔍 摘要与 PDF 分析

使用两组可独立配置的 LLM 处理评分、摘要译文、关键词、评分生成的中文 TL;DR 与深度分析。Semantic Scholar 的英文 TL;DR 默认翻译为中文，可在 API 配置中关闭；译文不可用时，原文折叠显示。PDF 可由本地 PyMuPDF 或 MinerU 解析；处理失败的阶段可重试。

</td>
<td width="50%" valign="top">

### 📄 报告与通知

生成每日研究、过去日报、补充报告、专题趋势和关键词趋势报告。支持邮件、企业微信、钉钉、Telegram、Slack 与通用 Webhook，均可在 WebUI 发送测试通知。

</td>
</tr>
<tr><td colspan="2" align="center"><sub>— 归档与运维 —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 🗃️ 历史与收藏

SQLite 保存论文处理状态、交付记录和收藏。可导入旧 HTML 历史、补全已有论文、扫描报告时段内的遗漏，并将补充报告独立归档。报告按实际批次浏览，论文可检索和标记偏好。

</td>
<td width="50%" valign="top">

### 🖥️ 管理面板

WebUI 提供任务状态、配置、报告、备份、诊断和 Token 用量。支持中英文、浅色/深色主题及管理员账户；侧边栏显示当前版本和可用更新。Token 用量区分普通输入、缓存输入与输出。

</td>
</tr>
</table>

---

## 📑 导航目录

| 章节 | 内容 |
| :--- | :--- |
| [🚀 快速开始](#-快速开始) | 配置 LLM、启动服务、完成首次运行 |
| [🛠️ 配置工具](#️-配置工具) | WebUI、命令行向导与界面截图 |
| [🐳 部署方式](#-部署方式) | 用户部署、源码测试与升级 |
| [📖 功能详解](#-功能详解) | 任务、报告、历史维护与数据 |
| [📁 项目结构](#-项目结构) | 代码与持久化目录 |
| [❓ 常见问题](#-常见问题) | 访问、任务、权限与恢复 |
| [📝 更新日志](CHANGELOG.md) | 版本变更和兼容性说明 |

---

## 🚀 快速开始

需要 Docker Compose、两组可用的 OpenAI 兼容 LLM 配置，以及可访问论文来源的网络。

### 1. 获取项目并填写 LLM

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
~~~

编辑 `.env` 中的 `CHEAP_LLM` 和 `SMART_LLM`：分别填写 API Key、Base URL 和模型名。两组可以使用同一服务；其他设置可在 WebUI 中完成。运行配置保存于 `runtime/config.json`，项目提供 `configs/config.example.json` 作为示例。

### 2. 启动 Worker 和 WebUI

~~~bash
docker compose pull
docker compose up -d
docker compose ps
~~~

打开 `http://HOST:8501`（将 HOST 换成宿主机地址），创建管理员账户。依次填写研究背景与主关键词、选择论文来源和评分策略，再用侧边栏“保存所有更改”保存配置。使用加权关键词＋扣分策略时，在关键词页设置不关注关键词及其权重。面板默认通过 8501 端口访问，请仅在受控局域网、Tailnet 或受保护的反向代理后开放。

### 3. 验证一次研究任务

在“每日研究”中将单次最多处理篇数设为 5，保存后手动启动。任务状态、队列和日志可在同页查看；报告出现在“内容 → 报告”。确认流程和通知正常后，再调整处理篇数。

运行数据保存在 `data/` 和 `logs/`；升级容器不会删除这些目录。

---

## 🛠️ 配置工具

### 🖥️ 现代管理 WebUI

| 分组 | 常用页面 |
| :--- | :--- |
| 运行 | 每日研究、过去日报、趋势任务 |
| 内容 | 报告、收藏、论文检索 |
| 配置 | 关键词、数据源、评分、API、通知、高级设置、账户 |
| 系统 | 备份与同步、历史维护、运行诊断、用量统计、日志 |

在“配置 → API”可测试 LLM 和第三方服务连接；“配置 → 通知”可逐个测试已配置渠道。设置变更统一通过左侧“保存所有更改”写入，任务执行时读取保存后的配置。

### 🧙 命令行配置向导

SSH 或无浏览器环境可在 Worker 容器中运行：

~~~bash
docker compose exec arxiv-daily-researcher python src/utils/setup_wizard.py
~~~

向导涵盖 LLM、论文来源、研究背景、评分、通知及运行设置。已有配置请先备份 `.env` 和 `runtime/config.json`。

### 🖼️ WebUI 界面预览

<table>
  <tr>
    <td align="center" width="33%"><img src="assets/webui_daily_push_v4.png" alt="每日研究任务和队列" width="100%" /><br /><sub>每日研究</sub></td>
    <td align="center" width="33%"><img src="assets/webui_analytics_v4.png" alt="Token 用量统计" width="100%" /><br /><sub>用量统计</sub></td>
    <td align="center" width="33%"><img src="assets/webui_scoring_v4.png" alt="论文评分策略" width="100%" /><br /><sub>评分设置</sub></td>
  </tr>
  <tr>
    <td align="center" width="33%"><img src="assets/webui_api_semantic_v4.png" alt="Semantic Scholar TL;DR 翻译设置" width="100%" /><br /><sub>API 配置</sub></td>
    <td align="center" width="33%"><img src="assets/webui_data_management_v4.png" alt="备份与同步" width="100%" /><br /><sub>备份与同步</sub></td>
    <td align="center" width="33%"><img src="assets/webui_history_import_v4.png" alt="历史维护" width="100%" /><br /><sub>历史维护</sub></td>
  </tr>
</table>

截图来自隔离的演示数据。

---

## 🐳 部署方式

### 用户部署

根目录 `docker-compose.yml` 固定使用已发布的双架构镜像：

| 服务 | 镜像 | 用途 |
| :--- | :--- | :--- |
| Worker | `ghcr.io/yzr278892/arxiv-daily-researcher:4.5` | 定时运行与任务处理，使用宿主机网络 |
| WebUI | `ghcr.io/yzr278892/arxiv-daily-researcher-config-panel:4.5` | 管理面板，映射 8501:8501 |

Worker 可通过 `localhost` 访问宿主机上的本地 LLM 或代理；WebUI 容器访问宿主机服务时使用 `host.docker.internal`。容器按 `.env` 中的 `PUID` / `PGID` 写入挂载目录，默认值为 1000:1000；NAS 部署请先改为实际用户 ID。

升级前备份 data/、runtime/ 与 .env，阅读 [更新日志](CHANGELOG.md)，然后执行：

~~~bash
git pull
docker compose pull
docker compose up -d --force-recreate
docker compose ps
~~~

旧配置如仍位于 `configs/config.json`，首次启动时会迁移到 `runtime/config.json`；请保留原文件直至确认配置加载正常。

### 本机源码测试

`tests/docker-compose.yml` 构建当前源码，仅供开发验证；测试 Worker 不会按定时任务自动运行。它默认复用工作区的 `.env`、`runtime/` 和 `data/`，不要与用户部署同时运行。

~~~bash
docker compose -f tests/docker-compose.yml up -d --build
docker compose -f tests/docker-compose.yml ps
docker compose -f tests/docker-compose.yml down
~~~

仓库另提供命令行入口及 GitHub Actions 工作流；长期运行时应持久化 data/、runtime/、logs/ 和 .env。

---

## 📖 功能详解

### 🔄 研究任务

每日研究扫描已启用来源，将候选论文写入 SQLite，再进行评分、译文和可选深度分析。单次篇数上限只限制本次处理量；未处理和失败项保留在队列中。过去日报可按日期补跑；趋势任务按关键词和时间范围生成专题报告。

### 📜 历史维护

| 操作 | 用途 |
| :--- | :--- |
| 读取旧历史 | 将旧 HTML 报告中的论文登记到交付账本 |
| 补全历史数据 | 为已有论文补齐缺失字段并更新报告 |
| 扫描历史遗漏 | 依据已导入报告的批次日期查找遗漏论文 |
| 迁移已有补充报告 | 移动旧目录文件并更新 SQLite 报告路径 |

前三项维护任务可在 Worker 空闲时或指定时段运行，处理篇数与每日研究分别设置。补充报告存放在 data/reports/other_reports/supplement/，与关键词趋势一同出现在“其他报告”。收藏现有合格论文时也会覆盖补充报告中的论文。

### 📊 用量、通知与备份

Token 用量按模型和时间展示普通输入、缓存输入、输出；可导入旧报告中记录的用量。旧报告没有缓存字段时，其输入按普通输入统计。

任务结果通过已启用渠道发送；发送失败会留在通知队列中等待重试。本地备份生成 SQLite 一致性快照，WebDAV 可按所选范围增量同步。恢复数据库前需停止运行中的任务。

---

## 📁 项目结构

~~~text
arxiv-daily-researcher/
├── docker-compose.yml         用户部署：GHCR 镜像
├── tests/docker-compose.yml   本机源码测试
├── main.py                    命令行入口
├── src/                       来源、分析、报告、通知与 WebUI
├── configs/                   配置示例和模板
├── runtime/config.json        当前运行配置（Git 忽略）
├── data/                      SQLite、报告与备份（Git 忽略）
├── logs/                      运行日志（Git 忽略）
└── assets/                    脱敏界面截图
~~~

---

## ❓ 常见问题

<details>
<summary><b>WebUI 无法访问或无法登录？</b></summary>

先运行 docker compose ps，确认 config-panel 为 healthy，并检查宿主机 8501 端口和防火墙。首次访问需创建管理员账户；已有账户使用本机 .env 中保存的账户配置，重建容器不会重置密码。日志可用 docker compose logs --tail=100 config-panel 查看。

</details>

<details>
<summary><b>任务已提交，却一直没有开始？</b></summary>

检查 docker compose ps 和 docker compose logs --tail=100 arxiv-daily-researcher。历史维护可能正等待 Worker 空闲或指定时间窗口；“系统 → 历史维护”会显示任务状态。每日研究的未处理论文会留在 SQLite 队列，下一次运行继续处理。

</details>

<details>
<summary><b>容器提示挂载目录没有写入权限？</b></summary>

使用 id 查询宿主机运行用户的 UID/GID，在 .env 中设置 PUID 和 PGID，再重建容器。不要直接删除 data/ 或将目录长期交给 root 写入。

</details>

<details>
<summary><b>LLM 返回 429、超时或部分论文分析失败？</b></summary>

在“系统 → 运行诊断”查看失败阶段，并在“配置 → API”测试连接。检查模型名、Base URL、额度、限流及代理；修复后重新运行，已完成的论文阶段会保留。

</details>

<details>
<summary><b>升级前应备份哪些文件？</b></summary>

保存 .env、runtime/、data/ 和需要保留的 logs/；也可在“系统 → 备份与同步”创建 SQLite 快照。不要只备份仓库代码：论文账本、收藏和报告存放在 data/ 中。

</details>

---

## 📜 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。

## 💬 反馈

问题与建议请提交至 [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues)，附上部署方式、复现步骤和已脱敏的日志。

## 🙏 致谢

感谢 [arXiv](https://arxiv.org/)、[OpenAlex](https://openalex.org/)、[Semantic Scholar](https://www.semanticscholar.org/) 和 [MinerU](https://mineru.net/) 提供的数据与工具。使用第三方服务时请遵守其配额和条款。

## 📝 更新日志

完整变更及升级说明见 [CHANGELOG.md](CHANGELOG.md)。
