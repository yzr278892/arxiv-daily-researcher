<div align="center">

<img src="assets/icon.svg" alt="ArXiv Daily Researcher" width="88" />

# ArXiv Daily Researcher

**追踪论文、按研究兴趣筛选、生成中文研究报告**

[![Release](https://img.shields.io/github/v/release/yzr278892/arxiv-daily-researcher?label=release)](https://github.com/yzr278892/arxiv-daily-researcher/releases)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](#-部署方式)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README_EN.md)

*日常文献追踪、专题趋势研究与论文归档。*

[快速开始](#-快速开始) · [界面预览](#webui-preview) · [使用说明](#-功能详解) · [发行说明](https://github.com/yzr278892/arxiv-daily-researcher/releases)

</div>

---

ArXiv Daily Researcher 定期检索 arXiv 与可选期刊来源，根据关键词和个人偏好筛选论文，生成中文摘要、PDF 分析及 Markdown / HTML 报告。通过浏览器完成配置、运行和阅读，也可将结果发送到邮件或即时通信平台。

## ✨ 核心功能

<table>
<tr><td colspan="2" align="center"><sub>— 发现与筛选 —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 📡 多来源检索

追踪 arXiv 新论文和修订版，支持 PRL、PRA/PRB、Nature、Science、Hugging Face Papers 和自定义 OpenAlex 期刊。跨来源关联同一论文，保留各来源的记录与分析。

</td>
<td width="50%" valign="top">

### 🎯 研究兴趣评分

提供核心相关性、加权关键词、加权关键词＋扣分、偏好学习四种策略。可分别设置关注主题、不关注主题和作者偏好，查看逐项评分及通过原因。

</td>
</tr>
<tr><td colspan="2" align="center"><sub>— 阅读与研究 —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 🔍 中文摘要与全文分析

生成摘要译文、简短 TL;DR 和关键词；通过筛选后可继续分析 PDF 的方法、结论和局限。支持 PyMuPDF 本地解析、MinerU 服务，以及可关闭的 Semantic Scholar TL;DR 翻译。

</td>
<td width="50%" valign="top">

### 📈 专题与关键词趋势

按关键词、日期和 arXiv 分类开展专题研究，汇总研究方向与方法演变。关键词趋势报告展示已收集论文中的主题变化。

</td>
</tr>
<tr><td colspan="2" align="center"><sub>— 归档与交付 —</sub></td></tr>
<tr>
<td width="50%" valign="top">

### 🗃️ 报告、收藏与历史

浏览 Markdown / HTML 报告，检索论文并记录喜欢或不喜欢的内容。支持自动收藏合格论文、按日期补跑、导入旧报告与补全历史；未完成的论文保留在队列中。

</td>
<td width="50%" valign="top">

### 🔔 通知与运行管理

支持邮件、企业微信、钉钉、Telegram、Slack 和通用 Webhook。WebUI 提供中英文、浅色/深色主题、运行日志、Token 统计、数据库备份与 WebDAV 同步。

</td>
</tr>
</table>

---

## 📑 导航目录

| 章节 |
| :--- |
| [🚀 快速开始](#-快速开始) |
| [🛠️ 配置工具](#️-配置工具) |
| [🐳 部署方式](#-部署方式) |
| [📖 功能详解](#-功能详解) |
| [📁 项目结构](#-项目结构) |
| [❓ 常见问题](#-常见问题) |
| [📝 更新日志](CHANGELOG.md) |

---

## 🚀 快速开始

准备一台安装了 Docker Compose 的 Linux 主机，以及可用的 OpenAI 兼容模型服务。主机需能访问所选论文来源和模型 API。

### 1. 获取项目

~~~bash
git clone https://github.com/yzr278892/arxiv-daily-researcher.git
cd arxiv-daily-researcher
cp .env.example .env
mkdir -p data logs runtime
~~~

运行 `id -u` 和 `id -g`，将 `.env` 中的 `PUID`、`PGID` 设为当前用户的 ID。这些目录和 `.env` 应由该用户拥有并可写。

### 2. 启动服务

~~~bash
docker compose pull
docker compose up -d
docker compose ps
~~~

打开 `http://HOST:8501`，将 `HOST` 换成主机地址，创建管理员账户。面板包含配置和账户管理，仅在可信局域网或 Tailscale 中直接使用 HTTP；公网访问请配置 HTTPS 反向代理。

### 3. 配置并运行

在“配置 → API”填写低成本 LLM 和高性能 LLM 的 API Key、Base URL、模型名，并测试连接。前者负责评分和翻译，后者负责深度分析和趋势总结；两组可以使用同一个模型。

在“关键词”中填写研究背景与主关键词，选择数据源和评分策略，点击侧边栏“保存所有更改”。首次运行可将每日研究的处理上限设为 5 篇；确认结果后再调整篇数和运行时间。

在“运行 → 每日研究”启动任务，在“内容 → 报告”阅读结果。

---

## 🛠️ 配置工具

### 🖥️ WebUI

| 分组 | 页面 |
| :--- | :--- |
| 运行 | 每日研究、过去日报、趋势任务 |
| 内容 | 报告、收藏、论文检索 |
| 配置 | 关键词、数据源、评分、API、通知、高级设置、账户 |
| 系统 | 备份与同步、历史维护、运行诊断、用量统计、日志 |

设置统一通过“保存所有更改”保存。来源开关控制相应配置的显示；通知渠道可单独发送测试消息。侧边栏显示当前版本，检查到更新后提供发布页链接。

### 🧙 命令行向导

可在终端中配置模型、研究主题、来源和通知：

~~~bash
docker compose exec arxiv-daily-researcher python src/utils/setup_wizard.py
~~~

<a id="webui-preview"></a>

### 🖼️ 界面预览

<table>
  <tr>
    <td align="center" width="50%"><a href="assets/webui_daily_push_v4.png"><img src="assets/webui_daily_push_v4.png" alt="每日研究任务和队列" width="100%" /></a><br /><b>每日研究</b><br /><sub>任务进度与待处理队列</sub></td>
    <td align="center" width="50%"><a href="assets/webui_scoring_v4.png"><img src="assets/webui_scoring_v4.png" alt="四种论文评分策略" width="100%" /></a><br /><b>评分设置</b><br /><sub>相关性、权重与扣分</sub></td>
  </tr>
  <tr>
    <td align="center" width="50%"><a href="assets/webui_api_semantic_v4.png"><img src="assets/webui_api_semantic_v4.png" alt="Semantic Scholar TL;DR 翻译设置" width="100%" /></a><br /><b>API 配置</b><br /><sub>来源服务与 TL;DR 翻译</sub></td>
    <td align="center" width="50%"><a href="assets/webui_analytics_v4.png"><img src="assets/webui_analytics_v4.png" alt="普通输入、缓存输入和输出 Token 统计" width="100%" /></a><br /><b>用量统计</b><br /><sub>输入、缓存与输出</sub></td>
  </tr>
  <tr>
    <td align="center" width="50%"><a href="assets/webui_history_import_v4.png"><img src="assets/webui_history_import_v4.png" alt="历史导入与维护调度" width="100%" /></a><br /><b>历史维护</b><br /><sub>导入、补全与运行时段</sub></td>
    <td align="center" width="50%"><a href="assets/webui_data_management_v4.png"><img src="assets/webui_data_management_v4.png" alt="数据库备份与恢复" width="100%" /></a><br /><b>备份与同步</b><br /><sub>数据快照与恢复</sub></td>
  </tr>
</table>

<sub>截图使用演示配置与模拟用量数据。</sub>

---

## 🐳 部署方式

### Docker Compose

根目录 `docker-compose.yml` 启动两个服务，镜像支持 `linux/amd64` 和 `linux/arm64`：

| 服务 | 镜像 | 职责 |
| :--- | :--- | :--- |
| Worker | `ghcr.io/yzr278892/arxiv-daily-researcher:4.7` | 定时运行、论文处理与通知 |
| WebUI | `ghcr.io/yzr278892/arxiv-daily-researcher-config-panel:4.7` | 管理界面，映射 `8501:8501` |

Worker 使用宿主机网络。WebUI 使用桥接网络；配置宿主机上的模型或代理时，请使用两个容器都可访问的主机地址，避免将 `localhost` 当作同一个位置。

WebUI 默认监听宿主机所有网卡。通过同机反向代理提供 HTTPS 时，可在 `.env` 设置 `ADR_WEBUI_BIND_ADDRESS=127.0.0.1` 和 `WEBUI_COOKIE_SECURE=true`，再重建 WebUI。后者要求浏览器始终通过 HTTPS 访问；不要在纯 HTTP 下启用。未经 TLS 保护的公网 HTTP 会暴露登录信息和面板数据。

升级前备份 `.env`、`runtime/`、`data/` 和自定义的 `configs/templates/`，按需保留 `logs/`，然后阅读 [发行说明](https://github.com/yzr278892/arxiv-daily-researcher/releases)。一般升级步骤：

~~~bash
git pull --ff-only
docker compose pull
docker compose up -d
docker compose ps
~~~

数据通过目录挂载保留。旧配置 `configs/config.json` 会在首次启动时迁移到 `runtime/config.json`。

### 源码开发

`tests/docker-compose.yml` 从源码构建 Worker 和 WebUI，Worker 仅执行手动提交的任务。它默认挂载当前工作区的配置和数据；使用中的容器不可当作临时测试环境。开发验证请使用隔离工作副本和数据。

~~~bash
docker compose -f tests/docker-compose.yml up -d --build
docker compose -f tests/docker-compose.yml ps
~~~

仓库还提供 `main.py` 命令行入口，以及每日研究和趋势研究的 GitHub Actions 工作流。

---

## 📖 功能详解

### 🎯 评分策略

| 策略 | 判定方式 |
| :--- | :--- |
| 核心相关性 V2 | 主关键词的相关度决定是否通过；参考关键词和作者偏好辅助排序。 |
| 加权关键词 V1 | 关键词相关度乘权重，再加作者分，与动态及格线比较。 |
| 加权关键词＋扣分 | 沿用加权关键词的加分与及格线，不关注关键词按相关度和独立权重扣分。 |
| 偏好学习 V1 | 在加权评分上结合喜欢、不喜欢和历史通过论文的学习信号调整得分。 |

选择扣分策略后，“关键词”页显示不关注关键词设置。每个扣分词的权重范围为 0–1；同一词不能同时作为主关键词和扣分词。

### 📄 研究任务与报告

每日研究处理新论文与未完成队列；过去日报按指定日期补跑；趋势任务围绕主题和时间范围汇总论文。处理篇数上限只限制单次工作量，失败或尚未处理的论文可在后续运行继续。

报告按实际批次浏览。“其他报告”收录关键词趋势和历史维护生成的补充报告。可自动收藏评分通过的论文，也可在收藏页一次性收藏已有合格论文。

### 📜 历史维护

| 操作 | 用途 |
| :--- | :--- |
| 读取旧历史 | 从旧 HTML 报告及兼容的历史记录导入论文与处理结果。 |
| 补全历史数据 | 为已有论文补齐缺失内容并更新报告。 |
| 扫描历史遗漏 | 按已导入报告的日期范围查找遗漏论文。 |
| 迁移已有补充报告 | 整理旧补充报告的名称、目录与关联记录。 |

前三项可选择闲时运行或指定时段运行，默认时段为 00:00–06:00。历史维护与每日研究分别配置处理篇数。

### 📊 用量与备份

用量统计按模型和时间展示普通输入、缓存输入、输出 Token。可导入历史报告中的统计；未记录缓存拆分的输入计为普通输入。

本地备份保存 SQLite 一致性快照，可设置保留天数和当天副本数。WebDAV 支持配置、报告及数据库同步。迁移整套服务时，还需保存 `.env`、`runtime/`、`data/` 和自定义模板。

---

## 📁 项目结构

~~~text
arxiv-daily-researcher/
├── docker-compose.yml         已发布镜像部署
├── tests/docker-compose.yml   源码开发部署
├── main.py                    命令行入口
├── src/                       检索、评分、分析、报告与 WebUI
├── configs/                   配置示例和报告、通知模板
├── runtime/config.json        当前运行配置
├── data/                      论文数据库、报告、PDF 与备份
├── logs/                      运行日志
└── .env                       模型凭据、通知和账户配置
~~~

---

## ❓ 常见问题

<details>
<summary><b>WebUI 打不开，或无法登录？</b></summary>

用 `docker compose ps` 检查 `config-panel` 状态，确认宿主机 8501 端口已开放且没有冲突。日志命令为 `docker compose logs --tail=100 config-panel`。首次访问需创建管理员账户；已有账户信息保存在 `.env`，重建容器不会重置。能登录的所有者可在“配置 → 账户”管理其他账户。

</details>

<details>
<summary><b>点击运行后，任务为什么在等待？</b></summary>

确认 Worker 已启动。每日研究、过去日报和历史维护会按互斥规则排队；历史维护还受运行时段约束。状态面板显示等待原因，可用 `docker compose logs --tail=100 arxiv-daily-researcher` 查看 Worker 日志。

</details>

<details>
<summary><b>如何调整筛选结果过多或过少的问题？</b></summary>

先查看报告中的关键词得分和通过原因，再调整主关键词、权重或及格线。扣分策略适合减少相近但不关注的主题；核心相关性策略要求论文与主关键词有实质关联。新的设置作用于后续处理，已有报告保留原评分。

</details>

<details>
<summary><b>模型连接超时、返回 429，或 PDF 分析失败怎么办？</b></summary>

在“系统 → 运行诊断”查看具体阶段，在“配置 → API”测试连接。核对 API Key、模型名、Base URL、额度和代理。遇到限流可减少并发；PDF 无法获取时，检查来源链接或 MinerU 配置。修复后再次运行，系统复用已完成的处理阶段。

</details>

<details>
<summary><b>为什么有些 Semantic Scholar TL;DR 仍是英文？</b></summary>

在“配置 → API → Semantic Scholar”启用 TL;DR 翻译，默认开启。翻译失败时保留原文并折叠展示；关闭翻译时直接展示原文。开关作用于后续处理，已生成的报告不会因保存设置而重写。

</details>

<details>
<summary><b>容器提示目录不可写怎么办？</b></summary>

核对 `.env` 中的 `PUID` / `PGID` 与挂载目录所有者。首次部署应先以该用户创建 `data/`、`logs/`、`runtime/`，并确保 `.env` 可写；修正权限后重新启动容器。

</details>

<details>
<summary><b>换主机时需要迁移什么？</b></summary>

等待任务结束，停止旧服务，再复制 `.env`、`runtime/`、`data/` 以及自定义的 `configs/templates/`；需要历史日志时一并复制 `logs/`。新主机核对用户 ID 和目录权限后启动两个服务。WebUI 的数据库备份只覆盖数据库，不能替代整套文件迁移。

</details>

---

## 📜 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。

## 💬 反馈

问题与建议请提交至 [GitHub Issues](https://github.com/yzr278892/arxiv-daily-researcher/issues)，附上部署方式、复现步骤和脱敏日志。

## 🙏 致谢

感谢 [arXiv](https://arxiv.org/)、[OpenAlex](https://openalex.org/)、[Semantic Scholar](https://www.semanticscholar.org/) 和 [MinerU](https://mineru.net/) 提供的数据与工具。

## 📝 更新日志

版本变更与升级说明见 [CHANGELOG.md](CHANGELOG.md)。
