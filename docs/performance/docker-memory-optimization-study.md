# Docker 内存占用优化：代码审查、性能假设与实施 RFC

> **状态：Research / Design only（仅调研，不实施）**  
> **审查日期：2026-10-20**  
> **基线：`main` @ [`aed2c2a038cb6779de3145a2519c6373ec2a7278`](https://github.com/yzr278892/arxiv-daily-researcher/commit/aed2c2a038cb6779de3145a2519c6373ec2a7278)**  
> **范围：自托管 Docker Worker + Config Panel 的内存和资源优化**  
> **配套：[可复现基准与验收手册](docker-memory-benchmark-plan.md)**

## 0. 执行摘要 / 结论

**可以在不削减功能的前提下进一步降低内存成本；现阶段不能承诺具体节省多少 MB。** 代码层面存在若干可以验证的额外数据复制、频繁启动的短命 Python 进程、WebUI 以条目数而不是字节数约束的缓存，以及数据库健康检查带来的周期性 CPU/I/O 成本。然而，本次检查**只能确认代码路径与潜在成本，不能测出用户实际容器的常驻 RSS、PSS、容器内存或任务峰值**。

建议先测量，再以独立、可回滚的小 PR 优化下列候选：
1. **P0：PDF 流式落盘与分块哈希**，避免下载路径的整文件缓冲与二次拼接；优先验证 Worker 的**任务峰值**。
2. **P0：WebDAV 定时检查的禁用快速退出与锁前判定**，避免无计划同步时仍进入复杂 Python/协调锁路径，并分析独占锁期间的分钟级任务堆积；优先验证**空闲 CPU、瞬时内存和进程数**。
3. **P1：WebUI 历史报告缓存引入按字节预算的 LRU / 有界结构**，优先验证**浏览历史后的稳态内存**。
4. **P1：WebUI 高频健康检查与数据库完整性检查分层**；优先验证**周期性的资源尖峰**，但不能丢失完整性探测。
5. **P1/P2：PyMuPDF 延迟导入、WebUI 线程/SQLite 连接生命周期、并发 Futures 有界化**，视基准数据实施。

**明确不建议**直接缩小 Docker 内存限制、削减日报扫描窗口/论文处理数量、关闭 PDF/MinerU、删历史数据、删通知/备份/WebDAV、随意减少已启用的并发，或仅为镜像小而切到 Alpine。这些都不构成“保持所有功能不变”的内存优化方案。

## 1. 审查范围与可复核事实

审查了 `main` 上的部署文件、入口脚本、依赖、健康检查、WebUI 缓存、PDF 处理、SQLite 及主工作流。这里的事实是**源码事实**，不是线上压测结果。

| 主题 | 已由源码确认 | 证据 |
| --- | --- | --- |
| 镜像架构 | `python:3.12-slim` 多阶段构建；Worker 与 WebUI 依赖及最终镜像分离 | [Dockerfile](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker/Dockerfile) |
| 容器额度 | Worker `memory: 768M` / `reservation: 384M`；Panel `256M` / `96M`，CPU 限制分别为 1 / 0.5 | [Compose](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker-compose.yml) |
| 空闲 Worker | 常驻 Cron、Shell watcher、`tail`；WebUI 请求队列每 5 秒检查。日报/PDF 分析由定时或手动任务启动，而非始终常驻模型进程 | [Worker entrypoint](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker/entrypoint.sh) |
| 定时检查 | WebDAV 入口每分钟以新的 Python 进程触发；`legacy_import_activity_gate` 在是否启用计划同步的配置判断**之前**取得 | [Cron / entrypoint](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker/entrypoint.sh) · [WebDAV scheduler](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/webdav_scheduler.py) |
| 健康检查 | Worker 每分钟运行轻量心跳/目录探测；WebUI 每 30 秒运行独立 Python，包含 SQLite `PRAGMA quick_check`（DB 存在时） | [Compose](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker-compose.yml) · [container_health.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/container_health.py) |
| PDF 下载 | `safe_download._bounded_response_bytes` 将全部分块放入 `chunks`，随后 `b"".join(chunks)`；PyMuPDF 路径还把完整字节写入临时文件，之后打开解析 | [safe_download.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/safe_download.py) · [analysis_agent.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/agents/analysis_agent.py) |
| PDF 上限 | 默认 `PDF_DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024`；本地 PyMuPDF 深度分析只提取前 20 页 | [config.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/config.py) · [analysis_agent.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/agents/analysis_agent.py) |
| PyMuPDF | `analysis_agent` 与 `keyword_agent` 顶层导入 `fitz`；关键词文件哈希一次性 `f.read()` | [analysis_agent.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/agents/analysis_agent.py) · [keyword_agent.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/agents/keyword_agent.py) |
| 历史报告缓存 | WebUI `_REPORTS_LIST_CACHE` 至多 16 个 key；`_REPORT_PAPERS_CACHE` 至多 64 个文件、每份最多 500 张卡片；`_LEGACY_SUPPLEMENT_CACHE` 至多 8192 项。限制是**项数**，不是保留内存预算 | [backend.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/modern_webui/backend.py) |
| SQLite | `DailyResearchStore` 在每个使用线程上复用 SQLite 连接，开启 WAL，随 DB 文件身份变更失效；WebUI 缓存 `DailyResearchStore` | [daily_research_store.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/daily_research_store.py) · [backend.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/modern_webui/backend.py) |
| 评分并发 | `ENABLE_CONCURRENCY` 默认 **false**，`CONCURRENCY_WORKERS` 默认 3；显式开启并发后，会为某数据源的所有待处理论文先创建 `Future` 映射 | [config.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/config.py) · [daily_research.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/modes/daily_research.py) |
| 既有优化 | WebUI 已去除大科学计算依赖；备份的 WebUI 文件导出已采用临时文件/流式路径；Worker health 默认是轻量检查。这些不是待修复的“未优化缺陷” | [requirements-webui.txt](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/requirements-webui.txt) · [backup.py](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/backup.py) |

### 1.1 不能由本次代码审查直接得出的结论

本次**没有**访问用户运行中的 NAS / VPS、Docker daemon、历史 SQLite 文件、报告数量、真实 PDF 样本与账号配置。因此无法确认：

- Worker / WebUI 目前真实的 RSS、PSS、匿名页、文件缓存和容器峰值；
- 是“空闲内存高”、日报执行时内存高、浏览报告后持续攀升，还是 Docker 统计了大量可回收文件缓存；
- 某项方案能够减少的具体 MB、百分比或耗时；
- 用户是否启用了并发、多少历史报告、PDF 分布以及 WebDAV 计划同步。

**没有指标就不应下结论说存在内存泄漏。** 后文的排序是“代码证据 × 潜在成本 × 功能回归风险”的工程假设，必须按配套基准手册验证。

## 2. 先澄清 Docker 内存概念

`deploy.resources.limits.memory` / `reservations.memory` / `memswap_limit` 是**资源策略**；相加的 Worker 768M + WebUI 256M = **1024M 的内存上限标称总量**，并不表示当前已分配或消耗 1024M。两服务 memory reservations 标称共 480M，也不是常驻 RAM 测量。镜像层大小、容器磁盘可写层、匿名 RSS 与 cgroup 文件缓存也不是同一个指标。

Linux 上 Docker CLI 的 `docker stats` 内存使用值通常从总计量中扣除了部分不活跃文件缓存，直接与 cgroup `memory.current` 对比可能不同；`PIDS` 包括线程。通过任务前后的**多维指标**而非一张 `docker stats` 截图评估优化。[Docker stats 官方文档](https://docs.docker.com/reference/cli/docker/container/stats/) · [资源限制官方文档](https://docs.docker.com/reference/compose-file/deploy)

建议收集以下指标：容器 `memory.current` / `memory.peak`（cgroup v2 支持时）、`memory.stat` 中的 `anon`、`file`、`inactive_file`、`memory.events` 中的 OOM 事件，Docker stats 的 MemUsage / PIDs / CPU，进程 RSS 或 PSS（`/proc/<pid>/smaps_rollup`），以及请求耗时/磁盘 I/O。Python `tracemalloc` 仅用来**辅助定位** Python 分配，不能替代对 PyMuPDF 等 C 扩展的进程/容器实际内存测量。[Python tracemalloc](https://docs.python.org/3/library/tracemalloc.html)

## 3. 优化候选及决策

以下所有内容均为**后续 PR 的提案**，本 PR 不修改实现。

| ID / 优先级 | 变更方案 | 主要作用对象 | 预期收益方向（未量化） | 风险 / 工作量 | 推荐结论 |
| --- | --- | --- | --- | --- | --- |
| M01 / P0 | 对 PyMuPDF PDF 路径使用安全的流式临时文件下载；关键词 PDF 哈希改为分块 | Worker 解析峰值 | 少持有完整原始 bytes 与分块列表 | 中 / 中 | **优先做** |
| M02 / P0 | WebDAV 禁用模式先判断后取得活动锁；验证锁竞争期间分钟 tick 聚集情况 | Worker 空闲/锁等待峰值 | 减少空 tick 成本、潜在被阻塞的子进程 | 低至中 / 小到中 | **优先做** |
| M03 / P1 | WebUI 报告解析缓存设置总字节预算 + LRU，限制对象驻留 | WebUI 浏览历史后的常驻 | 降低旧报告列表与卡片缓存驻留 | 中 / 中 | **基准确认后做** |
| M04 / P1 | WebUI 轻量 liveness 高频探针与 SQLite 深度健康探针分层 | WebUI 周期性进程与 I/O 峰值 | 减少 `quick_check` 的频繁扫描 | 中 / 中 | **保留腐败检测才做** |
| M05 / P1 | PyMuPDF 从模块顶层延迟到首次真正解析时导入 | Worker 无 PDF 任务的进程内存 | 缩小无 PDF 的任务启动峰值 | 低 / 小 | 可与 M01 同批 |
| M06 / P1 | WebUI 线程池与 SQLite 连接保留策略审查 | WebUI 常驻/多请求 | 避免多线程各自长期留连接 | 中至高 / 中 | **实测确认再做** |
| M07 / P2 | 并发评分/深度分析使用有界提交窗口，而不是一次性提交所有 Futures | Worker 并发峰值 | 减少已排队 Future/输入对象 | 高 / 中到大 | 需独立回归 PR |
| M08 / P2 | 报告目录索引/服务端分页，避免构造全部报告对象 | WebUI 超大历史目录 | 降低单次请求对象量与扫描耗时 | 高 / 大 | 数据规模确实大才做 |
| M09 / P2 | MinerU ZIP 结果采用安全临时文件，而非 `BytesIO` 包装完整压缩包 | Worker MinerU 峰值 | 降低 ZIP 的压缩数据内存驻留 | 中到高 / 中 | 单独压缩包安全测试 |
| M10 / 非首要 | 镜像依赖/层与 Python 启动开销审计 | 磁盘、拉取与启动速度 | 可能减小镜像和冷启动成本 | 中 / 中 | **不能等同 RAM 优化** |

### 3.1 M01：PDF 安全流式落盘（P0）

**现状路径**：`download_external_bytes` 中维护 `chunks: list[bytes]`，在下载完成后通过 `b"".join(chunks)` 新建完整 bytes；`AnalysisAgent._parse_pdf_with_pymupdf` 随后将该 bytes 写入磁盘并由 `fitz.open(path)` 解析。PDF 原始字节在 PyMuPDF 解析期间仍在 Python 局部变量中。`KeywordAgent._calculate_pdf_hash` 对整份文件调用 `f.read()`。

**潜在内存机制**：
- 下载时分块列表与最终 joined bytes **可能短暂同时存在**，大致与 PDF 文件大小同阶；对 50 MiB 文件仅能说有潜在数十 MiB 量级的额外缓冲，不能直接把它当作真实容器峰值。
- 临时文件页缓存和 PyMuPDF 的原生解析工作区仍存在；流式落盘减少的是**Python 完整文件缓冲**，不是消除一切 PDF 解析开销。

**建议设计**：新增内部流式“下载到安全临时文件”能力（或等价受控接口），让 PyMuPDF 直接从文件读取。保留原 `download_external_bytes` API 给现有调用者；不要一次改动所有网络下载。额外将参考 PDF 哈希改为 64 KiB ~ 1 MiB 分块增量计算。

**不可改变的约束**：
- 每一个跳转都必须经过当前外部 URL/SSRF 校验，禁用不受控自动重定向；
- 保留响应头大小检查、实际累计下载字节上限、初始 KiB 文件头魔数校验（包含跨块头标识）和最大重定向次数；
- 请求失败/中断/解析异常后清理临时文件；不得留下半下载文件被误认为有效 PDF；
- 维持 MinerU → PyMuPDF 的原有回退逻辑、PyMuPDF 解析前 20 页的默认语义、下载错误分类、重试策略与结果文本；
- 保留历史论文数据、报告格式、缓存键与复跑行为；不把原先合法但带前导注释的 PDF 判为错误。

**验收**：同一批可公开复用的 PDF 样本，文本提取逐字等价或满足事前约定的归一化等价；网络模拟必须覆盖缺少 Content-Length、超限、重定向、块边界魔数、网络中断、清理失败和并发下载；比较 Worker PDF 阶段 `memory.peak`、`anon`、任务耗时与磁盘写入。

### 3.2 M02：WebDAV 轻量 tick 与锁协调（P0）

**现状**：Cron 每分钟启动一次 `python -m utils.webdav_scheduler`；在模块 `main()` 中，先获取 `legacy_import_activity_gate` 再进入 `run_scheduled_webdav_sync`；配置是否启用 `WEBDAV_SYNC_MODE == scheduled` 的判断在后面。大规模旧历史导入持有独占门锁时，分钟 tick 可能积累为等待中的 Python 进程。

**优化目标**：关闭定时 WebDAV 时，尽量以低导入成本读取最小必需配置后提前退出；启用时保留全部调度保障，避免排队检查进程无界累积。

**必须强调调度正确性**：不能简单把每分钟 Cron 改成每 5 分钟、遇到锁就跳过分钟，或把失败 outbox 清空。现有 `scheduled_task_key`、重试队列、历史导入互斥、重启补偿与按分钟匹配是产品语义。最稳妥的第一步是**禁用时快速退出**；“启用时如何去重/避免被阻塞 tick 聚集”需先做独占锁压力测试，证明不会漏掉任一分钟应触发的任务或待补发任务，再采用独立可恢复的调度策略。

**验收**：计划同步关闭 24h 不执行远端传输；打开后匹配分钟恰好一次，晚于执行时不丢应执行任务，失败可重试，重启持久性不变；测试连续 30–60 分钟独占历史导入造成的 Cron 实际进程数与锁队列。以空闲 CPU、短命 Python 的峰值及进程数为主要收益指标，**不承诺让 Worker 的持久进程 RSS 下降**。

### 3.3 M03：WebUI 缓存预算与失效正确性（P1）

**现状**：`_REPORTS_LIST_CACHE` 缓存整个目录查询的 groups/rows，键最多 16；`_REPORT_PAPERS_CACHE` 最多 64 份报告，每份可缓存多达 500 个卡片条目；`_LEGACY_SUPPLEMENT_CACHE` 最多 8192 个文件判断。即便缓存项数有限，单项保存的列表/字典大小仍随历史记录数量变化。

**候选措施**：
- 缓存保留总字节**预算**与 LRU，优先淘汰最近最少访问的大对象。Python 对象深度大小应做采样估算并留保守余量，不能只测 `sys.getsizeof(list)`；
- 优先限制**已解析报告卡片**和**完整目录列表**，保留现有按文件/目录签名检测外部改动的机制；
- 对高频首页状态小对象继续使用既有 TTL 缓存；不要把所有缓存一起禁用，避免 NAS 的重复文件遍历和 SQLite 读取更慢；
- 若确实存在上万历史报告，另立独立索引/分页阶段（M08）；旧的 API 和筛选/预览语义要保持不变。

**验收**：合成不同历史规模（例如 100 / 1,000 / 10,000 份）的报告目录，重复首页→历史→预览→返回；记录 WebUI 启动 RSS、浏览后稳态 RSS/PSS、`anon`、延迟 p50/p95、目录扫描次数；外部修改/删除文件和还原备份后缓存必须立刻反映新结果，收藏/偏好修改不能回显旧数据。

### 3.4 M04：健康检查分层而非减少安全保障（P1）

WebUI health 当前以 30 秒周期启动 Python、读取配置并对存在的 SQLite 执行 `PRAGMA quick_check`。SQLite 官方定义该操作的复杂度为 **O(N)**，随数据库规模增长；检查会涉及磁盘、文件缓存以及短命进程。Worker 自身已把 `--full` 与高频轻量健康检查分离，不应误写为 Worker 每分钟执行整库扫描。

**候选设计**：
- WebUI 的 30 秒健康检查保留进程可用性、HTTP /api/health、必要目录与权限等轻量 liveness；
- SQLite `quick_check` 改到较低频率的**独立完整性检查**、首次启动、恢复后或显式按需诊断；必须保留**独立、可见的最近检查时间和失败状态**，不能把数据损坏静默隐藏；
- 不复用一个可能一直成功、掩盖真实 SQLite 问题的永久健康缓存。健康状态及 Docker restart policy 的语义变化需要产品/运维评审；
- 不能用“数据库文件存在”替代数据完整性检查，也不建议所有失败直接触发无休止重启。

**验收**：大库下高频探测的 P95 CPU/Wall time/短时 RSS 应下降；数据库损坏、权限失效、SQLite 被替换、WebUI HTTP 服务故障仍可检测并明确报告。对数据损坏发现时延要在方案中事先声明，而不是删除监控。

### 3.5 M05–M10：二线选项与避免过度优化

- **M05 延迟导入 PyMuPDF**：只有第一次需要处理 PDF 时导入，错误与 fallback 一致；由于日报执行本来是子进程，收益可能只体现在“当次没有解析 PDF”的任务，并**不一定降低长时间空闲 Worker 的基础内存**。
- **M06 线程/连接管理**：`run_in_threadpool` 加 `DailyResearchStore` 每线程复用 SQLite 连接是有意的已有优化。要测线程峰值、已开连接数量、PSS，再讨论 AnyIO thread limiter 或连接清理；不能在进行中的事务上粗暴关闭连接，且不得破坏 DB 更换/恢复时的 inode 失效处理。
- **M07 并发 Future 提交窗口**：目前显式启用并发时，按数据源一次构造所有 Futures；可以用“最多 N 个执行中 + 有界待派发队列”的滑窗。**当前默认不启用并发**，所以这一优化不应被当作默认运行时首要瓶颈。须保证数据库阶段顺序、异常归属、取消、中断、失败重试、报告顺序约束不退化。
- **M08 大目录索引/分页**：是可能较大的结构改造，先在 M03 后测是否仍存在显著成本；不可默认只取最近 N 篇/份报告或减少既有历史搜索范围。
- **M09 MinerU ZIP 缓冲**：当前 `download_external_bytes → io.BytesIO(zip_bytes) → ZipFile`，压缩包完全在内存。改造时需保留 Zip bomb 防护、条目数/解压后大小上限、有效文件选择、错误处理和本地解析回退；不应与 M01 混为一次大重构。
- **M10 镜像大小**：Worker 正当依赖 `PyMuPDF`、`OpenAI SDK` 等；WebUI 已不需要完整论文分析依赖。拆层/缩容器镜像首先优化的是磁盘、推送、启动，不一定影响稳态 RSS，必须分别测量，不能用镜像体积宣称省 RAM。

## 4. 功能完整性契约（任何后续实施 PR 均必须满足）

1. **部署和持久化**：保持两个服务的 Compose 使用方式、挂载目录、PUID/PGID、`.env` / `runtime/config.json` 读取优先级、已有 SQLite/WAL 数据与历史报告不丢失；无需用户迁移。
2. **研究能力**：arXiv、OpenAlex、自定义期刊、HuggingFace、Semantic Scholar；关键词/参考 PDF、LLM 筛选、翻译、深度分析、MinerU 自动回退、Markdown/HTML 报告和通知完整保留。
3. **任务语义**：每日扫描窗口、完整分页、版本去重、所有候选的持久登记、补跑/补充、断点恢复、失败重试、任务取消、线程安全与持久化历史入队顺序不变。不得为了省内存静默减少扫描或分析数量。
4. **运维能力**：Cron 每日研究、WebDAV 按分钟计划、更新检查、关键词维护、WebUI 手动触发与状态回执、备份/恢复/导出、数据库与目录健康诊断均继续可用。
5. **安全性**：下载重定向/SSRF 限制、PDF/ZIP 超限防护、报告 HTML/Markdown 过滤、WebUI 鉴权、secret 不泄露、任务锁与 SQLite 恢复排他行为不得退化。
6. **无隐性功能开关**：不因 RAM 优化而关闭日志、异常诊断、PDF 深度分析、历史索引或健康检查；若必须调整诊断频率，须保留独立检测与显式状态。
7. **向前兼容**：PR #12 推理强度参数与 PR #13 ChatGPT SIWC 仍未合并到 `main`；本报告以现有 `main` 为基线，后续代码 PR 要针对最新主线重新复核，不能覆盖这两个独立功能 PR。

## 5. 需要测量的场景与成功条件

详细命令、环境记录、样本矩阵与失败注入见 [docker-memory-benchmark-plan.md](docker-memory-benchmark-plan.md)。

| 场景 | 重点指标 | 需要排除的错误解释 |
| --- | --- | --- |
| 双容器刚启动、空闲 15 分钟 | 稳态容器 anon、RSS/PSS、PIDs | 不能拿 Compose `768M/256M` 上限当占用 |
| Cron 关闭 WebDAV 定时、空闲至少 30–60 分钟 | 分钟级 CPU、进程/PIDs、峰值 | 不应把短命 Python 的尖峰解释为持续泄漏 |
| 旧历史导入独占锁 30–60 分钟 | 存活 WebDAV 检查进程数、待执行任务数 | 不能遗漏应执行的计划同步 |
| 打开/切换 WebUI 页面，历史 100/1k/10k 份 | 浏览后 RSS/PSS、cache 对象与 p95 延迟 | “缓存越少越好”未考虑反复遍历 NAS |
| 5 / 20 / 40–50 MiB 公开 PDF | Worker `memory.peak` / anon 与解析等价 | 文件缓存增长不等于 Python 没释放 |
| 连续生成日报、逐篇深度分析 | 峰值、CPU、耗时、PDF 清理、最终 SQLite ledger | 不得以跳过论文/分析换取 RAM 数字 |
| 并发开/关分别测 | Pending Futures、线程数、错误处理 | 默认并发是**关闭**的 |
| SQLite 中大型库下 30 秒健康探针 | 单次探针耗时、CPU/I/O/进程 | 不能通过不检查数据库来“假成功” |

**最低要求（可讨论的评审阈值，而非保证收益）**：

- 对每个实施优化，明确其对应的内存指标（稳态还是峰值）；在**相同环境、相同输入、重复至少 3 次**的情况下，得到可重复的改善，或如无改善则不合并；
- 任何被优化场景不得出现新的 OOM / `memory.events` 异常；报告、状态、去重、恢复与失败注入测试无退化；
- 监控 CPU、I/O、耗时 p50/p95，防止通过无界写盘或加重网络请求来换低 RAM；具体性能回退容忍度由实测建立；
- 正常功能与接口不可见变化。确需改变轮询或数据库完整性发现时延的实现，必须记录其明确的可靠性预算并取得维护者认可；
- 优化收益需同时记录成本（代码复杂度、维护负担、额外磁盘写入）。

不设置“必须下降 30% / 必须低于 300MB”等未经证实的结果指标。所有绝对 MB 目标应由第一轮基准确定。

## 6. 推荐的后续 PR 拆分（本 PR 不实施）

1. **Benchmark PR**：增加只读、可选的性能观测脚本/报告采样流程；不自动采集密钥、论文全文或私有元数据；建立 main baseline。
2. **Worker download PR（M01 + M05）**：先新增下载到文件的安全接口及单元测试，再在 PyMuPDF 路径切换；保留 `download_external_bytes` 与 MinerU 的当前 API；以 PDF 峰值数据决策。
3. **Worker scheduling PR（M02）**：禁用模式早退出为第一层；锁竞争与 minute-schedule/outbox 证明后再决定是否需要进一步调度进程治理。
4. **WebUI memory PR（M03）**：报告缓存字节预算、LRU、外部修改失效和高历史量模拟；不直接改变浏览结果。
5. **WebUI health PR（M04）**：轻量 liveness + 有审计结果的低频完整性检查，并补故障注入。
6. **Conditional PRs（M06–M09）**：仅当前面阶段仍测到瓶颈时推进线程/SQLite、有界 Future、索引/分页和 MinerU ZIP 改造。

**每一个优化 PR 均可独立审查、测量、合并和回滚。** 若方案未获得可复现的资源改善，就保留原实现，避免纯“理论优化”增加复杂度。

## 7. 本文档的取舍与待确认问题

- **不把报告当作测量**：实际数据仅能从用户机器或者用代表性合成数据搭建的受控 Docker 环境取得。
- **不把高镜像尺寸等同于高内存**：磁盘压缩/层共享优化不自动降低 Worker/WebUI 的 anon RSS。
- **不把线程多等同于泄漏**：线程数量、SQLite 缓存和 Python 内存分配器驻留需要结合 PSS、GC 与同负载重复运行调查。
- **不削减健康检查以制造好看的 stats**：优化检查频率必须保持“故障被发现”的产品能力和完整性保障。
- **不重构部署拓扑作为第一步**：合并 Worker + WebUI 可能省去一个解释器，却破坏调度隔离、错误恢复和独立重启的既有边界。
- **不触及生产数据**：本 PR 不修改 `docker-compose.yml`、Dockerfile、requirements、`src/`、`tests/` 或任何资源限制值。

## 8. 参考资料

**源码基线**：[main 固定提交](https://github.com/yzr278892/arxiv-daily-researcher/tree/aed2c2a038cb6779de3145a2519c6373ec2a7278)；[Dockerfile](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker/Dockerfile)；[Compose](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/docker-compose.yml)；[Worker scheduler](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/webdav_scheduler.py)；[PDF downloader](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/safe_download.py)；[WebUI backend](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/modern_webui/backend.py)；[container health](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/container_health.py)。

**上游定义与测量方法**：

- [Docker stats — 内存与 page cache 的口径](https://docs.docker.com/reference/cli/docker/container/stats/)
- [Docker Compose 资源限制与 reservations](https://docs.docker.com/reference/compose-file/deploy)
- [Docker runtime metrics — cgroup 内存指标](https://docs.docker.com/engine/containers/runmetrics/)
- [SQLite PRAGMA quick_check — 复杂度和检查范围](https://www.sqlite.org/pragma.html#pragma_quick_check)
- [Python tracemalloc — Python 内存分配诊断及其边界](https://docs.python.org/3/library/tracemalloc.html)
