# Docker 内存优化：基准测试、观测与无功能回退验收方案

> **配套主报告**：[代码调研与优化 RFC](docker-memory-optimization-study.md)  
> **状态：仅测试设计；本 PR 不执行性能实验、不改动生产部署**  
> **固定对照基线：`main` @ `aed2c2a038cb6779de3145a2519c6373ec2a7278`**  
> **所有命令都应在用户主动选择的测试环境运行；不得将真实令牌、`.env` 或原始数据库上传到 CI / Issue / PR。**

## 1. 为什么首先测量

常见的四种“内存占用较高”互不等价：

1. **常驻 Python 匿名内存**：空闲较长时间后进程 RSS/PSS 或 cgroup `anon` 高，需关注常驻 WebUI 的缓存与线程/连接。
2. **任务期间的峰值**：下载 PDF、解析、并发 Futures 和生成报告时达到 `memory.peak`；此时只采集单次空闲 `docker stats` 会漏报。
3. **文件缓存、原生内存和数据库 I/O**：例如 PyMuPDF 解析、SQLite `quick_check`；不能仅靠 `tracemalloc` 诊断。
4. **短命或等待中的进程**：每分钟调度/健康探针或历史导入锁等待；可能造成周期性资源尖峰而不是永久泄漏。

Docker CLI 在 Linux 上会对部分 page cache 进行扣减；`PIDS` 包含线程。[Docker stats 定义](https://docs.docker.com/reference/cli/docker/container/stats/)。
因此报告至少应同时包含**容器内存、`anon`、峰值、PIDs、单进程 RSS/PSS 和耗时**；单一内存截图不足以评估改变。

## 2. 前置条件与数据隔离

本 PR **不要求在用户当前生产实例运行脚本**。正式实施前应取得下列信息，并尽量在使用**脱敏、可重建测试夹具**的副本环境复现：

| 元信息 | 要记录什么 | 不应记录或上传 |
| --- | --- | --- |
| 应用 | Git SHA、构建镜像 digest、镜像目标 `worker/webui` | 任意认证令牌 |
| Docker | Docker Engine / Compose 版本、宿主架构、cgroup v1/v2 | Docker Socket、宿主的私人目录 |
| 资源 | 可用 RAM、CPU、swap 策略、容器 limits、PUID/PGID、网络/磁盘类型（NAS/本地） | 内部 IP、主机标识（无需） |
| 业务配置 | 并发开启与否、`workers`、PDF 上限、报告种类、启用的来源类别、定时 WebDAV 开关 | API Key、SMTP/WebDAV 密码、全量 `.env` |
| 数据规模 | SQLite `.db` 文件字节数、历史报告文件数、文件大小分位数、PDF 大小分布 | 私有论文全文、用户收藏清单、个人偏好 |
| 外部 API | 使用 mock / fixture 还是用户同意的真实请求、调用总次数与耗时 | 原始 prompts、私有模型响应和 Bearer 头 |

**兼容性实验不得直接删除/修改生产数据库、日志或报告，也不得执行具有破坏性的 `docker compose down -v`。** 所有数据库损坏/锁竞争/网络失败注入只在隔离副本进行。

## 3. 最小只读观测命令（示例）

### 3.1 识别容器、配额、OOM 和基础 stats

~~~sh
# 不返回 .env 内容；观察实际内存、CPU、进程/线程数量。
docker stats --no-stream \
  arxiv-daily-researcher \
  arxiv-daily-researcher-config-panel

docker inspect --format \
  'name={{.Name}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} memory_limit={{.HostConfig.Memory}}' \
  arxiv-daily-researcher

docker inspect --format \
  'name={{.Name}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} memory_limit={{.HostConfig.Memory}}' \
  arxiv-daily-researcher-config-panel

# 宿主可查看各 PID 当前 RSS（KiB），但 RSS 共享库部分不能简单求和。
docker top arxiv-daily-researcher -eo pid,ppid,rss,comm,args
docker top arxiv-daily-researcher-config-panel -eo pid,ppid,rss,comm,args
~~~

注意：`.HostConfig.Memory` / Compose memory limit 是约束，**不是实测使用量**；`OOMKilled=false` 也不证明没有瞬时压力。镜像 `Size` 主要是磁盘指标。

### 3.2 cgroup v2 的匿名页、文件缓存和峰值（若该主机提供这些文件）

~~~sh
docker exec arxiv-daily-researcher sh -c '
  for name in memory.current memory.peak memory.events memory.stat; do
    file="/sys/fs/cgroup/$name"
    if [ -r "$file" ]; then
      echo "=== $name ==="
      cat "$file"
    fi
  done
'

docker exec arxiv-daily-researcher-config-panel sh -c '
  for name in memory.current memory.peak memory.events memory.stat; do
    file="/sys/fs/cgroup/$name"
    if [ -r "$file" ]; then
      echo "=== $name ==="
      cat "$file"
    fi
  done
'
~~~

`memory.current` 含可回收文件缓存，`memory.stat` 中 `anon`、`file`、`inactive_file` 用来区分；`memory.peak` 是**当前 cgroup 生命周期累计峰值**，不自动在单次任务后重置。cgroup v1/容器运行时不同时需改用对应控制器接口，不应把缺失文件当成“0 内存”。

### 3.3 同步记录短时尖峰（在合适的测试窗口手动执行）

~~~sh
# 在宿主执行，约 6 分钟，写入本地文件；可提前 Ctrl+C。
out="adr-docker-stats-$(date +%Y%m%d_%H%M%S).log"
i=0
while [ "$i" -lt 180 ]; do
  date -u '+%Y-%m-%dT%H:%M:%SZ'
  docker stats --no-stream \
    --format '{{.Name}} | {{.MemUsage}} | {{.CPUPerc}} | {{.PIDs}}' \
    arxiv-daily-researcher \
    arxiv-daily-researcher-config-panel
  i=$((i + 1))
  sleep 2
done > "$out"
echo "Saved local measurements to $out"
~~~

上述采样间隔为 `docker stats` 请求时间 **+ 2 秒**，并非严格 2 Hz / 0.5 Hz；要比较逐秒瞬时峰值，优先读取 `memory.peak` 或使用时间戳准确的 cgroup 采样器。运行者应只分享汇总统计，不直接公开可能包含宿主环境元信息的原始日志。

### 3.4 Python / 原生分配定位

- 同一 Python 进程内对比 `tracemalloc` snapshot，可辅助定位 Python 字典、列表、字节串的分配点；**不将其数值当作 PyMuPDF 原生内存或完整 RSS**。
- 进程 PSS（可访问时）由 Linux `/proc/<pid>/smaps_rollup` 提供；注意检查容器的 PID namespace / 权限。
- 统计 SQLite 连接与 WebUI 线程时，应将“最大并发时短时数量”与“请求结束 15 分钟后仍保留数量”分开。
- 内存增长不自动等于泄漏：Python allocator 和系统文件缓存可能保留可复用的内存页。首先重复操作，观察是否**单调且无界**增长。

## 4. 对照实验矩阵

所有实验在**相同镜像基线、相同数据夹具、相同设置**下执行；随后只替换对应优化 PR 重新执行。至少 3 次，建议 5 次；明确记录冷缓存/热缓存、Docker 是否刚重启和 cgroup peak 自何时开始计。

| 实验 | 步骤和规模 | 主要观察 | 正确性门槛 |
| --- | --- | --- | --- |
| B00 冷启动 | 启动双容器，记录首个健康状态，再空闲 15 分钟 | 冷/热 RSS、anon、PIDs、CPU | 无重启、健康检查通过、监听端口/触发心跳存在 |
| B01 空闲分钟调度 | WebDAV 计划同步关闭；至少观察 60 次分钟 tick | CPU、短命进程、anon/peak、唤醒次数 | 不触发任何远端 WebDAV 传输；其它 Cron 任务照常 |
| B02 计划同步 + 锁争用 | 隔离副本中运行 30–60 分钟独占历史导入锁；覆盖匹配分钟、失败重试、重启 | 等待进程数量、OOM、outbox 深度 | 正确分钟不丢、不重、不提前执行，重试与历史导入互斥一致 |
| B03 WebUI 空闲轮询 | 打开首页，不交互 15 分钟，正常前端状态刷新 | PIDs、CPU、anon、探针耗时 | 状态变化时响应时效不退化 |
| B04 历史报告 | 构建 100 / 1000 / 10000 个脱敏报告夹具（样本复杂度分层）并反复浏览/搜索/预览 | WebUI 浏览后 RSS/PSS、p50/p95 延迟 | 所有报告可见；筛选、收藏、预览与外部修改生效 |
| B05 PDF 下载解析 | 有效 5 / 20 / 40–50 MiB PDF，各 3 次，覆盖有/无 Content-Length | Worker `memory.peak`、anon、I/O、延迟 | 文本与旧版等价；超限/SSRF/重定向/临时清理不退化 |
| B06 MinerU 回退 | 模拟成功 ZIP、部分 ZIP、配额失效、本地 PyMuPDF fallback | 峰值与 Zip 解压缓冲 | ZIP 防护、失败原因、fallback 和报告均等价 |
| B07 日报完整流水线 | 相同脱敏 arXiv/OpenAlex/HF 结果夹具、同评分和 LLM 模拟响应；运行 200 候选与超过上限的队列 | Peak、处理速率、重试/SQLite 写入 | 输入集/候选队列/日报/断点重跑一致 |
| B08 并发评估 | `ENABLE_CONCURRENCY=false` 和 `true`/3 workers 分开测试，不暗中改变默认值 | pending Futures、线程、峰值、CPU | 取消/失败重试/报告结果完整且没有重复 |
| B09 SQLite 健康 | 小库、中库、大库（全部合成），每 30 秒健康检查；复制损坏库注入故障 | `quick_check` 耗时、CPU、RSS、磁盘 I/O | 故障能显式被发现；数据没有静默回滚/跳过 |
| B10 备份与恢复 | 本地只读备份副本与恢复沙箱 | RAM、磁盘写入、事务一致性 | 正确恢复、锁权限与完整性保持 |

在 B07/B08 使用 mock 的原则是**隔离网络供应商限速与回复随机性**；另设仅用户明确同意的真实外部 API 验收，不把相似但不同的 LLM 自然语言输出要求逐字一样。

## 5. 结果表格模板

每个测试环境、每种输入数据规模填写一次（`N/A` 表示不可用，不得冒充零）：

| 指标 | baseline main | 候选优化 | 改变 | 分析 |
| --- | --- | --- | --- | --- |
| Docker Worker steady memory（按相同 CLI 口径） | TBD | TBD | TBD | 不能与 `memory.current` 混用 |
| Worker `anon` 15min 稳态 | TBD | TBD | TBD | 空闲驻留 |
| Worker `memory.peak` | TBD | TBD | TBD | 同时记录 cgroup 重置/重建时刻 |
| WebUI `anon` 15min 稳态 | TBD | TBD | TBD | 历史浏览后还要再测 |
| WebUI `memory.peak` | TBD | TBD | TBD | HTTP / healthy workload |
| Worker / WebUI 最大 PIDs | TBD | TBD | TBD | 包括线程 |
| CPU 时间 / 利用率 | TBD | TBD | TBD | 同等真实工作量 |
| 关键请求 p50 / p95 | TBD | TBD | TBD | 3–5 次统计的置信程度有限 |
| Disk read / write | TBD | TBD | TBD | RAM 换 I/O 必须说明 |
| OOM events / restarts | TBD | TBD | TBD | 任意新增事件都需调查 |
| 完成论文数、报告数、持久状态 | TBD | TBD | 必须等价 | 优化不得减少工作 |

改善百分比建议用同一资源指标的 `(baseline - candidate) / baseline × 100%` 表示；同时提供绝对 MiB 改变量和输入规模。小样本波动或改善低于测试噪声时标为“未证明有效”。

## 6. 哪些行为必须通过自动化回归

建议沿用 `.github/workflows/test.yml` 已有 CI，并为各优化 PR 分别补定向测试：

- **所有 Python 回归**：`python -m pytest -q`。
- **前端/Compose**：`node --check src/modern_webui/static/app.js`、双 Compose `config --quiet`、`npm run test:browser`。
- **镜像烟测**：按现有 CI 构建 `--target worker` 和 `--target webui`，运行 `scripts/smoke_containers.sh`；确保真实 entrypoint / 健康检查仍工作。
- **PDF**：无 Content-Length、跨数据块的魔数、恶意重定向、超过配置限制、EOF/网络中断、解析异常后的临时文件清理、双线程处理相同 URL 不碰撞、参考关键词 PDF 哈希不变。
- **Cron / WebDAV**：计划同步关闭、计划开启、每分钟匹配、跨天边界、锁争用、崩溃重启、失败待补发的幂等性。
- **WebUI**：大历史集缓存内存预算、LRU 淘汰、签名失效、分页/非分页结果一致、外部修改即时反映、收藏偏好与身份隔离。
- **SQLite**：健康诊断细化但 `quick_check` 仍可显式调用；恢复时连接自动无效化、WAL/外部事务可见性、无文件或损坏文件错误的可解释性。
- **调度语义**：日报正常运行、补跑/补充、历史导入、Worker 触发回执和手动停止均无变化；保留全部源数据、SQLite checkpoint 和运行锁行为。

需要强调：现有回归与烟测的**通过不能替代内存基准**；CI 无需访问任何真实 LLM/WebDAV 账户，也不应该让 CI 测试主动发起昂贵的外部请求。

## 7. 合并门槛、风险处理与回滚

### 7.1 优化代码 PR 的评审门槛

- **功能等价性**：各既有测试 + 新定向测试通过；数据、权限、错误诊断和外部行为无未申明退化；
- **量化证据**：给出完整的测试场景、输入/运行时间、baseline SHA、修改后 SHA、指标统计、原始采集的时间窗口；
- **真实收益**：至少一个明确的目标指标稳健改善，且没有明显把问题转移到 CPU/I/O、延迟、缓存一致性或数据完整性；
- **回滚可行性**：不引入不可逆 DB schema 变更或强制用户手动迁移；具备恢复原逻辑的简单路径；
- **边界兼容**：检查 PR #12（LLM 推理参数）/ PR #13（ChatGPT SIWC）是否已合并，避免后续基准覆盖/污染其独立修改。

### 7.2 可选、仅作为讨论起点的工程红线

绝对目标需先测量，不对项目许诺内存一定降低某个数值。

- 核心功能断言、幂等与持久化状态：**零退化**；
- 同一输入工作量下不出现新增 OOM、额外网络调用或丢失任务；
- 当 CPU/耗时劣化明显高于观测噪声、或额外写盘不可接受时，停止该候选并重新评估；
- 监控周期更改导致故障发现延迟时，必须得到明确的运维可靠性预算，不得默默通过。

### 7.3 回滚

所有优化后续 PR 均针对单个机制（例如 PDF 下载路径、WebDAV 无效 tick、WebUI 缓存、健康检查），独立部署与回滚。**默认保留旧下载 bytes API**，并对升级前后的报告和 SQLite 实体使用相同存储格式。不要从高内存问题直接推导出“该删除历史数据”。

## 8. 参考与操作风险

- [Docker container stats](https://docs.docker.com/reference/cli/docker/container/stats/)
- [Docker runtime metrics / cgroup](https://docs.docker.com/engine/containers/runmetrics/)
- [Docker Compose deploy resources](https://docs.docker.com/reference/compose-file/deploy)
- [SQLite PRAGMA quick_check](https://www.sqlite.org/pragma.html#pragma_quick_check)
- [Python tracemalloc](https://docs.python.org/3/library/tracemalloc.html)
- [当前项目 CI](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/.github/workflows/test.yml)
- [当前项目容器入口与健康检查](https://github.com/yzr278892/arxiv-daily-researcher/blob/aed2c2a038cb6779de3145a2519c6373ec2a7278/src/utils/container_health.py)

**本文件及其配套 RFC 是待讨论的评审文档，不是已经验证的性能优化结果。**
