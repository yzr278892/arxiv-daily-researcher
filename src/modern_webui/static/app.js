const NAVIGATION = [
  { id: "run", label: "运行", pages: [
    ["daily_research", "🚀 每日研究"],
    ["past_daily", "🗓 过去日报"],
    ["trend_tasks", "📈 趋势任务"],
  ] },
  { id: "content", label: "内容", pages: [
    ["reports", "📄 报告"],
    ["favorites", "⭐ 收藏"],
    ["paper_search", "🔎 检索"],
  ] },
  { id: "configuration", label: "配置", pages: [
    ["keywords", "🔑 关键词"],
    ["data_sources", "🧭 数据源"],
    ["scoring", "⚖️ 评分"],
    ["api", "🔌 API"],
    ["notifications", "🔔 通知"],
    ["advanced", "⚙️ 高级设置"],
    ["accounts", "👤 账户"],
  ] },
  { id: "system", label: "系统", pages: [
    ["backup_sync", "☁️ 备份与同步"],
    ["history_tasks", "🗂 历史维护"],
    ["diagnostics", "🩺 运行诊断"],
    ["analytics", "📊 用量统计"],
    ["logs", "📜 日志"],
  ] },
];

const PAGE_META = {
  daily_research: ["运行 / 每日研究", "每日研究", "扫描、筛选、分析并生成报告。"],
  past_daily: ["运行 / 过去日报", "过去日报", "按日期补跑日报。"],
  trend_tasks: ["运行 / 趋势任务", "趋势任务", "按关键词和时间段生成趋势报告。"],
  reports: ["内容 / 报告查看", "报告查看", "查看日报与趋势报告。"],
  favorites: ["内容 / 收藏", "收藏", "管理收藏论文与偏好。"],
  paper_search: ["内容 / 论文检索", "论文检索", "检索已处理论文。"],
  keywords: ["配置 / 关键词", "关键词", "设置研究背景、关键词与参考文献提取。"],
  data_sources: ["配置 / 数据源", "数据源", "管理 arXiv 和其他数据源。"],
  scoring: ["配置 / 评分", "评分", "设置资格判定、排序和作者偏好。"],
  api: ["配置 / API", "API 配置", "配置 LLM、PDF 解析和第三方服务。"],
  notifications: ["配置 / 通知", "通知", "配置任务通知。"],
  advanced: ["配置 / 高级设置", "高级设置", "配置解析、并发、重试、代理和持久化。"],
  accounts: ["配置 / 账户", "账户管理", "管理面板账户。"],
  backup_sync: ["系统 / 备份与同步", "备份与同步", "配置备份和 WebDAV 同步。"],
  history_tasks: ["系统 / 历史维护", "历史维护", "导入、补全和扫描历史数据。"],
  diagnostics: ["系统 / 运行诊断", "运行诊断", "查看任务、LLM 与数据源健康状态。"],
  analytics: ["系统 / 用量统计", "用量统计", "查看 LLM Token 用量。"],
  logs: ["系统 / 日志", "运行日志", "查看本地运行日志。"],
};

// Most wording comes from the compatibility panel's shared i18n catalogue.
// These entries are unique to the modern presentation layer, so they live
// beside that layer rather than duplicating an otherwise shared translation.
const MODERN_EN_TRANSLATIONS = Object.freeze({
  "现代管理面板": "Modern management panel",
  "正在读取配置文件…": "Loading configuration files…",
  "重新加载配置": "Reload configuration",
  "重启研究容器": "Restart research container",
  "已发送研究容器重启请求，正在刷新面板状态。": "Restart requested; refreshing panel status.",
  "状态自动刷新": "Auto refresh status",
  "显示非 arXiv 来源报告": "Show non-arXiv source reports",
  "每行一个关键词": "One keyword per line",
  "深色模式": "Dark mode",
  "浅色模式": "Light mode",
  "有未保存修改": "Unsaved changes",
  "关键词": "Keyword",
  "权重（0–1）": "Weight (0–1)",
  "添加关键词": "Add keyword",
  "关键词尚未添加": "No keywords added",
  "作者": "Author",
  "加分": "Bonus points",
  "添加作者": "Add author",
  "作者尚未添加": "No authors added",
  "主关键词参与资格判定与排序；每个关键词可设置 0–1 的独立权重。": "Primary keywords determine qualification and ranking; set an individual 0–1 weight for each keyword.",
  "为指定作者设置独立加分；核心相关性 V2 仅在论文已经合格后用于排序。": "Set an individual bonus for each author. In Core Relevance V2, it ranks papers only after they qualify.",
  "最多关键词数量": "Maximum keyword count",
  "启动 arXiv 来源": "Enable arXiv source",
  "启动额外数据源": "Enable extra data sources",
  "请求超时（秒）": "Request timeout (seconds)",
  "公告回看宽限（天）": "Announcement lookback grace (days)",
  "基础通过分": "Base passing score",
  "单关键词最高分": "Maximum score per keyword",
  "输出 Markdown": "Markdown report",
  "输出 HTML": "HTML report",
  "综合分析": "Comprehensive analysis",
  "研究关键词": "Research keywords",
  "已保存提示词模板": "Saved prompt template",
  "不使用模板": "No template",
  "查看范围": "View range",
  "每页": "Rows per page",
  "上一页": "Previous",
  "下一页": "Next",
  "运行": "Run",
  "内容": "Content",
  "配置": "Configuration",
  "系统": "System",
  "运行 / 每日研究": "Run / Daily Research",
  "每日研究": "Daily Research",
  "扫描、筛选、分析与报告生成。任务仍由现有 Worker 队列安全执行。": "Scan, filter, analyse, and generate reports. Existing worker queues continue to execute tasks safely.",
  "运行 / 过去日报": "Run / Past Daily Reports",
  "过去日报": "Past Daily Reports",
  "按自然日将指定时间段加入队列，逐日生成与当日一致的报告。": "Queue a date range by calendar day and generate the same report format used on each day.",
  "运行 / 趋势任务": "Run / Trend Research",
  "趋势任务": "Trend Research",
  "对指定关键词和时间段执行独立趋势研究。": "Run independent trend research for selected keywords and a date range.",
  "内容 / 报告查看": "Content / Reports",
  "报告查看": "Reports",
  "浏览日报、趋势研究和关键词趋势报告。": "Browse daily, trend-research, and keyword-trend reports.",
  "内容 / 收藏": "Content / Favorites",
  "收藏": "Favorites",
  "查看已标记论文、作者偏好和关键词偏好。": "Review marked papers, author preferences, and keyword preferences.",
  "内容 / 论文检索": "Content / Paper Search",
  "论文检索": "Paper Search",
  "从 SQLite 多维论文历史库检索已处理论文及其来源变体。": "Search processed papers and source variants in the SQLite research history.",
  "配置 / 关键词": "Configuration / Keywords",
  "关键词": "Keywords",
  "设置研究背景、主关键词和参考文献关键词提取。": "Set research context, primary keywords, and reference-keyword extraction.",
  "配置 / 数据源": "Configuration / Data Sources",
  "数据源": "Data Sources",
  "管理内置 arXiv 与额外数据来源。": "Manage the built-in arXiv source and additional data sources.",
  "配置 / 评分": "Configuration / Scoring",
  "评分": "Scoring",
  "控制论文资格判定、排序与作者偏好。": "Control paper qualification, ranking, and author preferences.",
  "配置 / API": "Configuration / API",
  "API 配置": "API Configuration",
  "配置 LLM、PDF 解析和第三方数据服务。": "Configure LLMs, PDF parsing, and third-party data services.",
  "配置 / 通知": "Configuration / Notifications",
  "通知": "Notifications",
  "配置任务完成和阶段级异常通知。": "Configure completion and stage-level issue notifications.",
  "配置 / 高级设置": "Configuration / Advanced",
  "高级设置": "Advanced",
  "调整解析、并发、重试、代理和持久化行为。": "Adjust parsing, concurrency, retries, proxy, and persistence behaviour.",
  "配置 / 账户": "Configuration / Accounts",
  "账户管理": "Account Management",
  "管理本地面板账户与密码。": "Manage local panel accounts and passwords.",
  "系统 / 备份与同步": "System / Backup & Sync",
  "备份与同步": "Backup & Sync",
  "导出配置、配置 WebDAV 和管理 SQLite 本地备份。": "Export configuration, configure WebDAV, and manage local SQLite backups.",
  "系统 / 历史维护": "System / History Maintenance",
  "历史维护": "History Maintenance",
  "导入旧版本历史、补全缺失字段并扫描遗漏论文。": "Import legacy history, repair missing fields, and scan for omitted papers.",
  "系统 / 运行诊断": "System / Run Diagnostics",
  "运行诊断": "Run Diagnostics",
  "查看正常每日研究、过去日报，以及所有任务的 LLM 与数据源健康记录。": "Review daily and past-report runs, plus LLM and data-source health from every task.",
  "系统 / 用量统计": "System / Usage Statistics",
  "用量统计": "Usage Statistics",
  "查看已记录的 LLM Token 使用情况。": "Review recorded LLM token usage.",
  "系统 / 日志": "System / Logs",
  "运行日志": "Run Logs",
  "按任务分组读取最近的本地运行日志。": "Read recent local run logs grouped by task.",
  "开始每日研究": "Start Daily Research",
  "状态面板": "Status Panel",
  "当前任务": "Current Task",
  "空闲": "Idle",
  "可以开始任务": "Ready to start",
  "待处理论文": "Pending Papers",
  "会在后续每日研究中处理": "Will be processed in a future daily research run",
  "待重试论文": "Papers Awaiting Retry",
  "保留阶段状态与问题摘要": "Stage state and issue summary are retained",
  "每日研究队列": "Daily Research Queue",
  "每日研究设置": "Daily Research Settings",
  "生成 HTML 报告": "Generate HTML reports",
  "生成 Markdown 报告": "Generate Markdown reports",
  "报告包含全部论文": "Include all papers in reports",
  "本次最多处理论文数（0 不限）": "Maximum papers this run (0 = unlimited)",
  "每日运行时间": "Daily run time",
  "历史维护每次最多处理论文数（0 不限）": "Maximum papers per history-maintenance task (0 = unlimited)",
  "历史维护运行方式": "History maintenance schedule",
  "闲时运行": "Run when idle",
  "指定时间段运行": "Run in a time window",
  "指定时段开始": "Window start",
  "指定时段结束": "Window end",
  "选择过去日期范围后开始运行。系统会按天把任务写入持久化队列，并与其他研究任务安全互斥。": "Choose a past date range and start the run. Jobs are stored in a durable per-day queue and safely interlocked with other research tasks.",
  "开始日期": "Start date",
  "结束日期": "End date",
  "开始运行": "Start Run",
  "刷新状态": "Refresh Status",
  "过去日报队列": "Past Daily Report Queue",
  "等待中": "Pending",
  "暂无待处理日期": "No pending dates",
  "已生成历史日期报告": "Past-date reports generated",
  "失败任务可在日志中查看": "See logs for failed tasks",
  "趋势研究": "Trend Research",
  "分析参数": "Analysis Parameters",
  "趋势研究配置": "Trend Research Settings",
  "输出格式会在保存时转换为兼容配置。": "Output formats are converted to the compatible saved configuration.",
  "研究关键词": "Research keywords",
  "arXiv 分类（可选）": "arXiv categories (optional)",
  "排序": "Sort order",
  "由早到晚": "Oldest first",
  "由晚到早": "Newest first",
  "最多结果数": "Maximum results",
  "报告位置": "Report placement",
  "报告开头": "Beginning of report",
  "报告末尾": "End of report",
  "为每篇趋势论文生成 TL;DR": "Generate a TL;DR for each trend paper",
  "用简短文字概括每篇入选论文的研究问题、方法和结论；关闭后趋势报告只保留标题、摘要和综合分析。": "Create a short summary of each selected paper's question, method, and findings. When disabled, trend reports keep titles, abstracts, and the overall analysis only.",
  "TL;DR 批大小": "TL;DR batch size",
  "输出格式": "Output Formats",
  "分析技能": "Analysis Skills",
  "综合分析": "Comprehensive analysis",
  "综合分析模板": "Comprehensive Analysis Template",
  "每次趋势研究都会运行综合分析；默认使用内置模板，可在此查看、编辑、保存或恢复。": "Every trend run performs comprehensive analysis. The built-in template is used by default and can be viewed, edited, saved, or restored here.",
  "模板操作": "Template Actions",
  "删除当前模板": "Delete Current Template",
  "选择模板后，模板内容会作为本次趋势研究的深度分析提示词；不选择时使用默认分析流程。": "A selected template is used as the deep-analysis prompt for this run; otherwise the default analysis flow is used.",
  "保存新的提示词模板": "Save a New Prompt Template",
  "提示词模板": "Prompt template",
  "新建或自定义模板": "New or custom template",
  "新建模板": "New template",
  "恢复内置模板": "Restore built-in template",
  "填写趋势研究的深度分析要求": "Describe the deep-analysis requirements for trend research",
  "模板会作为本次趋势研究的深度分析要求。编辑内置模板后保存即可覆盖本机副本；恢复会重新使用内置内容。": "The template supplies the deep-analysis requirements for this trend run. Saving an edited built-in template overrides its local copy; restoring it uses the built-in text again.",
  "已恢复内置提示词模板。": "Built-in prompt template restored.",
  "模板名称": "Template name",
  "模板内容": "Template content",
  "保存模板": "Save Template",
  "报告浏览": "Report Browser",
  "刷新列表": "Refresh List",
  "预览": "Preview",
  "报告预览": "Report Preview",
  "修改时间：": "Modified:",
  "显示非 arXiv 来源报告": "Show reports from non-arXiv sources",
  "收藏的论文": "Favorite Papers",
  "收藏画像": "Preference Profile",
  "收藏设置": "Favorite Settings",
  "自动收藏及格论文": "Automatically favorite qualifying papers",
  "正向偏好": "Positive preference",
  "负向偏好": "Negative preference",
  "按标记时间倒序展示；点击标题可打开论文页面。": "Newest marks first; select a title to open the paper page.",
  "收藏时间": "Marked at",
  "论文": "Paper",
  "论文标识": "Paper ID",
  "暂无 👍 收藏论文。": "No liked papers yet.",
  "权重": "Weight",
  "收藏次数": "Favorites",
  "收藏关键词": "Favorite Keywords",
  "次数": "Count",
  "暂无收藏关键词统计": "No favorite keyword statistics",
  "检索条件": "Search Filters",
  "来源": "Source",
  "全部来源": "All sources",
  "完成日期开始": "Completed from",
  "完成日期结束": "Completed to",
  "最低分数": "Minimum score",
  "仅收藏论文": "Favorites only",
  "搜索": "Search",
  "研究背景会用于评分和参考文献关键词提取。": "Research context is used for scoring and reference-keyword extraction.",
  "主关键词": "Primary Keywords",
  "主关键词参与资格判定与排序。": "Primary keywords take part in qualification and ranking.",
  "参考文献 PDF 关键词提取": "Reference PDF Keyword Extraction",
  "高重要度": "High importance",
  "中重要度": "Medium importance",
  "低重要度": "Low importance",
  "尚未提取关键词。": "No keywords have been extracted yet.",
  "选择需要扫描的 arXiv 分类。": "Choose arXiv categories to scan.",
  "开启后可选择内置来源或添加 ISSN 期刊来源。": "Enable this to choose built-in sources or add ISSN journal sources.",
  "评价策略": "Scoring Strategy",
  "作者加分": "Author Bonus",
  "低成本 LLM": "Low-cost LLM",
  "高性能 LLM": "High-capability LLM",
  "测试连接": "Test Connection",
  "自定义": "Custom",
  "归一化使用的 LLM": "LLM for normalization",
  "该选择会用于每日关键词标准化，并同步记录到 LLM 健康统计。": "This model is used for daily keyword normalization and recorded in LLM health statistics.",
  "自定义趋势视图天数": "Custom trend view days",
  "关闭后不会请求 OpenAlex。开启后，还需在“数据源 → 额外数据源”中选择期刊来源。": "When disabled, OpenAlex is never requested. When enabled, choose journal sources under Data Sources → Additional Data Sources.",
  "通知设置": "Notification Settings",
  "任务成功通知": "Task success notifications",
  "任务失败通知": "Task failure notifications",
  "通知中展示论文数量": "Papers shown in notifications",
  "附加报告文件": "Attach report files",
  "通知渠道": "Notification Channels",
  "邮件": "Email",
  "SMTP 主机": "SMTP host",
  "端口": "Port",
  "发件人": "Sender",
  "收件人（逗号分隔）": "Recipients (comma-separated)",
  "当前已跳过登录，仅建议在可信内网使用。可在 .env 设置 WEBUI_AUTH_ENABLED=true 后重新启用账户验证。": "Login is currently skipped. Use this mode only on a trusted LAN; set WEBUI_AUTH_ENABLED=true in .env to enable account protection again.",
  "配置导出": "Configuration Export",
  "导出当前 config.json 与 .env。导出文件含凭据，请妥善保存。": "Export the current config.json and .env. The exported file contains credentials; store it securely.",
  "导出配置": "Export Configuration",
  "按需同步配置、SQLite 历史、关键词和报告文件。": "Synchronize configuration, SQLite history, keywords, and report files as needed.",
  "上传": "Upload",
  "下载": "Download",
  "远程目录": "Remote directory",
  "同步时机": "Sync timing",
  "手动": "Manual",
  "定时": "Scheduled",
  "报告完成后": "After report completion",
  "配置文件": "Configuration files",
  "关键词数据": "Keyword data",
  "报告文件": "Report files",
  "本地备份": "Local Backup",
  "本地备份按保留策略自动整理；启用 WebDAV 后会在本地快照成功后增量镜像到远端。": "Local backups are cleaned up by the retention policy; with WebDAV enabled, successful snapshots are incrementally mirrored remotely.",
  "本地保存天数（0 永久保存）": "Local retention days (0 = keep forever)",
  "当天最多数量（0 不限）": "Maximum same-day copies (0 = unlimited)",
  "生成本地备份": "Create Local Backup",
  "导出备份": "Export Backup",
  "导入备份": "Import Backup",
  "上传并恢复": "Upload and Restore",
  "暂无本地备份": "No local backups",
  "旧版本历史导入": "Legacy History Import",
  "启用完整补全流程": "Enable Full Repair Flow",
  "关闭后仅导入 HTML 已包含的论文，避免新的每日研究重复处理。": "When disabled, import only papers already present in HTML to prevent future daily research from reprocessing them.",
  "读取旧历史": "Import Legacy History",
  "补全历史数据": "Repair Historical Data",
  "扫描历史遗漏": "Scan Historical Omissions",
  "操作": "Actions",
  "没有未完成的历史维护任务。": "There are no unfinished history-maintenance tasks.",
  "LLM 健康": "LLM Health",
  "数据源健康": "Data Source Health",
  "汇总所有真实任务（含历史维护）的 LLM 调用；查看不会发送探针请求，也不会额外消耗 Token。": "Aggregates LLM calls from all real tasks, including history maintenance. Viewing does not send probes or consume additional tokens.",
  "所选范围内没有 LLM 调用记录。": "No LLM calls in the selected range.",
  "汇总所有真实任务（含历史维护）的数据源请求；查看不会发送探针请求。": "Aggregates data-source requests from all real tasks, including history maintenance. Viewing does not send probes.",
  "所选范围内没有数据源请求记录。": "No data-source requests in the selected range.",
  "周一": "Mon",
  "周三": "Wed",
  "周五": "Fri",
  "周日": "Sun",
  "近 90 天": "Last 90 days",
  "近 365 天": "Last 365 days",
  "输入 Token": "Input Tokens",
  "输出 Token": "Output Tokens",
  "刷新最新日志": "Refresh Latest Log",
  "开启后，仅在任务运行或刚提交等待接手时每 5 秒刷新状态、队列和日志尾部。": "When enabled, refresh status, queue, and the live-log tail every 5 seconds only while a task is running or waiting for worker hand-off.",
  "开启后，在历史任务运行或等待工作进程接手时每 5 秒刷新状态、进度和日志尾部。": "When enabled, refresh status, progress, and the live-log tail every 5 seconds only while a history task is running or waiting for worker hand-off.",
  "Ollama（本地）": "Ollama (Local)",
  "智谱 AI": "Zhipu AI",
  "启用Telegram": "Enable Telegram",
  "启用Slack": "Enable Slack",
  "mineru：云端 API（质量更高）｜pymupdf：本地（无需网络）。": "mineru: cloud API (higher fidelity) | pymupdf: local (no network required).",
  "推荐：3–5，过高可能触发速率限制。": "Recommended: 3–5. Higher values may trigger rate limits.",
  "扫描报告": "Scan Reports",
  "导入论文卡": "Import Paper Cards",
  "写入投递记录": "Write Delivery Records",
  "补充任务": "Supplement Tasks",
  "暂无用量记录": "No usage recorded",
  "报告元数据": "Report Metadata",
  "时间范围": "Date Range",
  "论文数量": "Paper Count",
  "前一天": "Previous Day",
  "后一天": "Next Day",
  "输入": "Input",
  "输出": "Output",
  "合计": "Total",
  "扫描、筛选、分析并生成报告。": "Scan, filter, analyse, and generate reports.",
  "按日期补跑日报。": "Run daily reports for selected past dates.",
  "按关键词和时间段生成趋势报告。": "Generate trend reports for selected keywords and dates.",
  "查看日报与趋势报告。": "View daily and trend reports.",
  "管理收藏论文与偏好。": "Manage favorite papers and preferences.",
  "检索已处理论文。": "Search processed papers.",
  "设置研究背景、关键词与参考文献提取。": "Set research context, keywords, and reference extraction.",
  "管理 arXiv 和其他数据源。": "Manage arXiv and other sources.",
  "设置资格判定、排序和作者偏好。": "Set qualification, ranking, and author preferences.",
  "配置 LLM、PDF 解析和第三方服务。": "Configure LLMs, PDF parsing, and third-party services.",
  "配置任务通知。": "Configure task notifications.",
  "配置解析、并发、重试、代理和持久化。": "Configure parsing, concurrency, retries, proxy, and persistence.",
  "管理面板账户。": "Manage dashboard accounts.",
  "配置备份和 WebDAV 同步。": "Configure backups and WebDAV sync.",
  "导入、补全和扫描历史数据。": "Import, repair, and scan historical data.",
  "查看任务、LLM 与数据源健康状态。": "View task, LLM, and data-source health.",
  "查看 LLM Token 用量。": "View LLM token usage.",
  "查看本地运行日志。": "View local run logs.",
  "收藏所有通过论文": "Favorite All Qualifying Papers",
  "正在扫描…": "Scanning…",
  "发送测试通知": "Send Test Notification",
  "正在发送…": "Sending…",
  "测试通知已发送。": "Test notification sent.",
  "历史维护任务已加入队列。": "History maintenance task queued.",
  "历史维护任务已重新加入队列。": "History maintenance task queued again.",
  "请先保存历史维护设置。": "Save history maintenance settings first.",
  "导入后补全缺失数据并扫描遗漏。": "Repair missing data and scan omissions after import.",
  "仅导入 HTML 中已有的论文。": "Import only papers already in HTML.",
  "运行设置": "Run Settings",
  "任务完成后显示最近导入结果。": "The latest import result appears when the task finishes.",
  "运行中每 5 秒更新。": "Updates every 5 seconds while running.",
  "代理范围": "Proxy Scope",
  "支持 HTTP 和 SOCKS5 代理。": "Supports HTTP and SOCKS5 proxies.",
  "期刊论文数据源": "Journal paper source",
  "可选补充论文流": "Optional supplementary paper feed",
  "TL;DR 增强": "TL;DR enrichment",
  "评分和分析": "Scoring and analysis",
  "通知推送": "Notification delivery",
  "备份和恢复": "Backup and restore",
});

const FALLBACK_ARXIV_CATEGORIES = [
  "quant-ph", "hep-th", "hep-ex", "hep-lat", "gr-qc", "astro-ph", "cond-mat", "physics", "math-ph", "cs.AI", "cs.LG", "stat.ML",
];
const BUILTIN_SOURCES = [
  ["prl", "PRL"], ["pra", "PRA"], ["prb", "PRB"], ["prc", "PRC"], ["prd", "PRD"], ["pre", "PRE"], ["prx", "PRX"], ["prxq", "PRX Quantum"], ["rmp", "RMP"],
  ["nature", "Nature"], ["nature_physics", "Nature Physics"], ["nature_communications", "Nature Communications"], ["science", "Science"], ["science_advances", "Science Advances"], ["npj_quantum_information", "npj Quantum Information"], ["quantum", "Quantum"], ["new_journal_of_physics", "New Journal of Physics"], ["huggingface_papers", "Hugging Face Papers"],
];

const state = {
  auth: null,
  settings: null,
  draft: { config: {}, env: {}, clearEnv: new Set() },
  group: "run",
  page: "daily_research",
  tables: {},
  pagedRenderers: new Map(),
  localRequestVersions: new Map(),
  timers: new Map(),
  pageData: {},
  renderToken: 0,
  language: window.localStorage.getItem("adr-modern-language") === "en" ? "en" : "zh",
  theme: window.localStorage.getItem("adr-modern-theme") === "dark" ? "dark" : "light",
  configurationDirty: false,
  translationIndex: new Map(),
  translationsLoaded: false,
  translationsPromise: null,
  // Chinese is the authored UI language. Until English has been selected,
  // walking every newly-rendered DOM node merely returns the source text and
  // makes large local refreshes feel needlessly sluggish.
  hasUsedEnglish: window.localStorage.getItem("adr-modern-language") === "en",
  originalText: new WeakMap(),
  originalAttributes: new WeakMap(),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function localizedString(value) {
  const source = String(value ?? "");
  if (state.language !== "en") return source;
  const leading = source.match(/^\s*/)?.[0] || "";
  const trailing = source.match(/\s*$/)?.[0] || "";
  const core = source.slice(leading.length, source.length - trailing.length);
  const translate = (text) => state.translationIndex.get(text) || MODERN_EN_TRANSLATIONS[text] || "";
  let translated = translate(core);
  // Section headings are rendered as an icon followed by wording in one text
  // node (for example "🧮 评价策略").  The shared catalogue stores the
  // wording without its presentational icon, so retain that prefix while
  // translating the semantic portion.
  if (!translated) {
    const decorated = core.match(/^(\S+\s+)(.+)$/u);
    if (decorated) {
      const suffix = translate(decorated[2]);
      if (suffix) translated = `${decorated[1]}${suffix}`;
    }
  }
  return `${leading}${translated || core}${trailing}`;
}

function localizeRoot(root = document) {
  const rootNode = root === document ? document.documentElement : root;
  if (!rootNode) return;
  const walker = document.createTreeWalker(rootNode, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || !node.nodeValue?.trim() || parent.closest("script, style, pre, code, textarea")) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    if (!state.originalText.has(node)) state.originalText.set(node, node.nodeValue || "");
    node.nodeValue = localizedString(state.originalText.get(node));
  });
  const elements = [];
  if (rootNode instanceof Element && rootNode.matches("[placeholder], [title], [aria-label]")) elements.push(rootNode);
  elements.push(...$$('[placeholder], [title], [aria-label]', rootNode));
  elements.forEach((element) => {
    let originals = state.originalAttributes.get(element);
    if (!originals) {
      originals = new Map();
      state.originalAttributes.set(element, originals);
    }
    ["placeholder", "title", "aria-label"].forEach((attribute) => {
      if (!element.hasAttribute(attribute)) return;
      if (!originals.has(attribute)) originals.set(attribute, element.getAttribute(attribute) || "");
      element.setAttribute(attribute, localizedString(originals.get(attribute)));
    });
  });
  document.documentElement.lang = state.language === "en" ? "en" : "zh-CN";
}

function renderLanguageButton() {
  const button = $("#language-button");
  if (button) button.textContent = state.language === "en" ? "中文" : "English";
}

function applyTheme() {
  const theme = state.theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  const color = theme === "dark" ? "#111827" : "#f7f9fc";
  $("meta[name='theme-color']")?.setAttribute("content", color);
  const button = $("#theme-button");
  if (button) button.textContent = theme === "dark" ? localeText("浅色模式", "Light mode") : localeText("深色模式", "Dark mode");
}

function toggleTheme() {
  state.theme = state.theme === "dark" ? "light" : "dark";
  window.localStorage.setItem("adr-modern-theme", state.theme);
  applyTheme();
}

function applyLocale(root = document) {
  // The authored Chinese tree needs no translation. Avoid a full TreeWalker
  // pass for every page/status refresh until a user has actually switched to
  // English at least once in this browser session.
  if (state.language === "en" || state.hasUsedEnglish) localizeRoot(root);
  else document.documentElement.lang = "zh-CN";
  renderLanguageButton();
}

function localeText(chinese, english) {
  return state.language === "en" ? english : chinese;
}

function pagerSummary(page, pages, total) {
  return state.language === "en"
    ? `Page ${page} / ${pages} · ${total} item${total === 1 ? "" : "s"}`
    : `第 ${page} / ${pages} 页 · 共 ${total} 条`;
}

function pageSizeLabel(size) {
  return state.language === "en" ? `${size} items` : `${size} 条`;
}

function reportCountLabel(count) {
  return state.language === "en" ? `${count} report${count === 1 ? "" : "s"}` : `${count} 份报告`;
}

function loadTranslations() {
  if (state.translationsLoaded) return Promise.resolve();
  if (state.translationsPromise) return state.translationsPromise;
  state.translationsPromise = api("/api/i18n")
    .then((payload) => {
      const entries = payload?.items && typeof payload.items === "object" ? Object.values(payload.items) : [];
      state.translationIndex = new Map(entries
        .filter((entry) => entry && typeof entry.zh === "string" && typeof entry.en === "string")
        .map((entry) => [entry.zh, entry.en]));
      state.translationsLoaded = true;
    })
    .finally(() => { state.translationsPromise = null; });
  return state.translationsPromise;
}

function warmTranslations() {
  if (state.translationsLoaded || state.translationsPromise) return;
  const request = () => { void loadTranslations().catch(() => null); };
  // Do not compete with first paint or the first status request. The idle
  // preload keeps a later English switch immediate in normal use.
  if ("requestIdleCallback" in window) window.requestIdleCallback(request, { timeout: 2000 });
  else window.setTimeout(request, 800);
}

async function toggleLanguage() {
  const nextLanguage = state.language === "en" ? "zh" : "en";
  const button = $("#language-button");
  if (nextLanguage === "en" && !state.translationsLoaded) {
    // English is an explicit choice, so load the complete shared catalogue
    // before changing language. This keeps the initial Chinese screen off
    // the critical path without ever showing a partially translated page.
    if (button) button.disabled = true;
    try {
      await loadTranslations();
    } catch (_) {
      // The small modern-only catalogue still gives a useful fallback when a
      // transient request fails. A later idle retry can fill in the shared
      // wording without making the language control unresponsive.
    } finally {
      if (button) button.disabled = false;
    }
  }
  state.language = nextLanguage;
  if (nextLanguage === "en") state.hasUsedEnglish = true;
  window.localStorage.setItem("adr-modern-language", state.language);
  renderNavigation();
  if (!state.auth?.authenticated) {
    showAuth(state.auth || { enabled: true, configured: false });
  } else {
    // Some controls are assembled from localeText() while rendering (for
    // example pagers). Re-render the active page so switching back to
    // Chinese cannot leave those dynamic English strings behind.
    await renderPage({ preserveScroll: true });
  }
  applyLocale(document);
  updateConfigurationDirtyIndicator();
  applyTheme();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

function escapeAttribute(value) { return escapeHtml(value); }

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch (_) {
    return null;
  }
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 19);
  return new Intl.DateTimeFormat(state.language === "en" ? "en-US" : "zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat(state.language === "en" ? "en-US" : "zh-CN").format(number) : "—";
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—";
}

function relativeLocalDateKey(offsetDays = 0) {
  // Date inputs represent an operator's local calendar day.  ISO timestamps
  // are UTC, so slicing ``toISOString()`` made "today" and "yesterday"
  // one day early for users east of UTC before their local morning.
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() + Number(offsetDays || 0));
  return localDateKey(date);
}

function pageGroup(page) {
  return NAVIGATION.find((group) => group.pages.some(([id]) => id === page));
}

function configValue(key, fallback = "") {
  return state.draft.config[key] ?? state.settings?.config?.[key] ?? fallback;
}

function hasUnsavedConfiguration() {
  return state.configurationDirty
    || Object.keys(state.draft.config).length > 0
    || Object.keys(state.draft.env).length > 0
    || state.draft.clearEnv.size > 0;
}

function updateConfigurationDirtyIndicator() {
  const dirty = hasUnsavedConfiguration();
  const saveButton = $("#save-button");
  if (saveButton) {
    saveButton.classList.toggle("has-unsaved", dirty);
    saveButton.textContent = dirty
      ? localeText("保存所有更改 · 有未保存修改", "Save All Changes · Unsaved")
      : localeText("保存所有更改", "Save All Changes");
  }
  const status = $("#file-status");
  if (status) status.textContent = dirty
    ? localeText("配置有未保存修改", "Configuration has unsaved changes")
    : localeText(".env 与 config.json 已加载", ".env and config.json loaded");
  $$('[data-save-hint]').forEach((item) => { item.hidden = !dirty; });
}

function markConfigurationDirty() {
  state.configurationDirty = true;
  updateConfigurationDirtyIndicator();
}

function envValue(key, fallback = "") {
  return state.draft.env[key] ?? state.settings?.env?.[key] ?? fallback;
}

function booleanValue(value, fallback = false) {
  // Environment values come from .env as strings.  In particular, the
  // string "false" must not render as a checked switch just because it is a
  // non-empty JavaScript value.  Blank optional env values retain each
  // control's documented default, matching the Streamlit collectors.
  if (value === undefined || value === null || value === "") return Boolean(fallback);
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function secretConfigured(key) {
  return Boolean(state.settings?.secrets?.[key]);
}

function arxivCategories() {
  const supplied = state.settings?.arxiv_categories;
  if (Array.isArray(supplied) && supplied.length) {
    return supplied
      .filter((item) => item && typeof item.code === "string")
      .map((item) => ({ code: item.code, label: String(item.label || item.code) }));
  }
  // The fallback keeps an already-loaded offline page usable while the
  // settings request is still pending.  Normal deployments receive the full
  // shared catalog from the backend above.
  return FALLBACK_ARXIV_CATEGORIES.map((code) => ({ code, label: code }));
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body !== "string" && !(options.body instanceof Blob)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json().catch(() => ({})) : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(detail || "请求未完成。 ");
  }
  return payload;
}

function toast(text, type = "success") {
  const node = $("#toast");
  node.textContent = localizedString(text);
  node.className = `toast ${type}`;
  node.hidden = false;
  window.clearTimeout(node._timer);
  node._timer = window.setTimeout(() => { node.hidden = true; }, 4200);
}

function runLocalRefresh(promise) {
  Promise.resolve(promise).catch((error) => toast(error?.message || "请求未完成。", "error"));
}

function beginLocalRequest(name) {
  const next = Number(state.localRequestVersions.get(name) || 0) + 1;
  state.localRequestVersions.set(name, next);
  return next;
}

function isCurrentLocalRequest(name, version) {
  return state.localRequestVersions.get(name) === version;
}

function clearTimers() {
  for (const timer of state.timers.values()) window.clearTimeout(timer);
  state.timers.clear();
}

function scheduleRefresh(key, callback, milliseconds = 5000) {
  window.clearTimeout(state.timers.get(key));
  state.timers.set(key, window.setTimeout(callback, milliseconds));
}

function setLocation() {
  const target = `${state.group}/${state.page}`;
  if (window.location.hash.slice(1) !== target) window.history.replaceState(null, "", `#${target}`);
}

function readLocation() {
  const [group, page] = window.location.hash.slice(1).split("/");
  const candidate = NAVIGATION.find((item) => item.id === group && item.pages.some(([id]) => id === page));
  if (candidate) {
    state.group = group;
    state.page = page;
  }
}

function renderNavigation() {
  const navigation = $("#group-navigation");
  navigation.innerHTML = NAVIGATION.map((group) => `
    <button class="group-button ${group.id === state.group ? "is-active" : ""}" data-group="${group.id}">
      ${escapeHtml(group.label)}
    </button>
  `).join("");
  const current = pageGroup(state.page) || NAVIGATION[0];
  $("#top-tabs").innerHTML = current.pages.map(([id, label]) => `
    <button class="top-tab ${id === state.page ? "is-active" : ""}" data-page="${id}" role="tab" aria-selected="${id === state.page}">${escapeHtml(label)}</button>
  `).join("");
  $$("[data-group]", navigation).forEach((button) => button.addEventListener("click", () => {
    const group = NAVIGATION.find((item) => item.id === button.dataset.group);
    state.group = group.id;
    state.page = group.pages[0][0];
    renderNavigation();
    renderPage();
  }));
  $$("[data-page]", $("#top-tabs")).forEach((button) => button.addEventListener("click", () => {
    state.page = button.dataset.page;
    state.group = pageGroup(state.page).id;
    renderNavigation();
    renderPage();
  }));
  applyLocale(navigation);
  applyLocale($("#top-tabs"));
}

function pageHeader() {
  const [breadcrumb, title, subtitle] = PAGE_META[state.page] || ["", "", ""];
  return `<header class="page-header"><p class="eyebrow">${escapeHtml(breadcrumb)}</p><h1>${escapeHtml(title)}</h1><p class="subtitle">${escapeHtml(subtitle)}</p></header>`;
}

function section(title, body, options = {}) {
  const icon = options.icon ? `${escapeHtml(options.icon)} ` : "";
  const configurationHint = options.saveHint === false || pageGroup(state.page)?.id !== "configuration"
    ? ""
    : `<p class="save-hint" data-save-hint ${hasUnsavedConfiguration() ? "" : "hidden"}>有未保存修改</p>`;
  return `<section class="section-card ${options.className || ""}"><div class="section-heading"><h2>${icon}${escapeHtml(title)}</h2>${options.hint ? `<p>${escapeHtml(options.hint)}</p>` : ""}</div>${body}${configurationHint}</section>`;
}

function divider() { return '<div class="section-divider"></div>'; }

function field(options) {
  const {
    label, key, type = "text", scope = "config", fallback = "", help = "", min, max, step,
    // Page renderers historically use ``choices``.  Accept ``options`` as
    // well so the shared control remains clear for future callers rather
    // than silently rendering an empty select element.
    choices: suppliedChoices, options: optionChoices,
    placeholder = "", rows = 4, required = false,
  } = options;
  const choices = Array.isArray(suppliedChoices)
    ? suppliedChoices
    : (Array.isArray(optionChoices) ? optionChoices : []);
  const value = scope === "env" ? envValue(key, fallback) : configValue(key, fallback);
  const data = `data-field="${escapeAttribute(key)}" data-scope="${scope}"`;
  const hint = help ? `<span class="field-help">${escapeHtml(help)}</span>` : "";
  if (type === "checkbox") {
    return `<label class="toggle-field"><span>${escapeHtml(label)}${hint}</span><input type="checkbox" ${data} ${booleanValue(value, fallback) ? "checked" : ""}/><i></i></label>`;
  }
  if (type === "textarea" || type === "lines") {
    const transform = type === "lines" ? ' data-transform="lines"' : "";
    const text = Array.isArray(value) ? value.join("\n") : String(value ?? "");
    return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><textarea ${data}${transform} rows="${rows}" placeholder="${escapeAttribute(placeholder)}">${escapeHtml(text)}</textarea></label>`;
  }
  if (type === "select" || type === "multiselect") {
    const multiple = type === "multiselect" ? " multiple" : "";
    const current = Array.isArray(value) ? value.map(String) : [String(value ?? "")];
    return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><select ${data}${multiple}>${choices.map((choice) => {
      const item = typeof choice === "object" ? choice : { value: choice, label: choice };
      return `<option value="${escapeAttribute(item.value)}" ${current.includes(String(item.value)) ? "selected" : ""}>${escapeHtml(item.label)}</option>`;
    }).join("")}</select></label>`;
  }
  if (type === "secret") {
    const configured = secretConfigured(key);
    return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><input type="password" ${data} data-secret="1" autocomplete="new-password" placeholder="${configured ? "已配置；留空则保持不变" : placeholder}" /></label>`;
  }
  if (type === "range") {
    const rangeKey = `${scope}:${key}`;
    return `<label class="form-field range-field"><span class="range-heading"><span>${escapeHtml(label)}${hint}</span><output data-range-output="${escapeAttribute(rangeKey)}">${escapeHtml(value)}</output></span><input type="range" ${data} data-range-key="${escapeAttribute(rangeKey)}" min="${min ?? ""}" max="${max ?? ""}" step="${step ?? "1"}" value="${escapeAttribute(value)}" /></label>`;
  }
  const numeric = type === "number" || type === "range";
  const attrs = `${numeric ? ` min="${min ?? ""}" max="${max ?? ""}" step="${step ?? "1"}"` : ""}${required ? " required" : ""}`;
  return `<label class="form-field"><span>${escapeHtml(label)}${hint}</span><input type="${type === "range" ? "range" : type}" ${data}${attrs} value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}" /></label>`;
}

function bindFields(root = document) {
  $$("[data-field]", root).forEach((element) => {
    const eventName = element.type === "checkbox" || element.tagName === "SELECT" ? "change" : "input";
    element.addEventListener(eventName, () => {
      const scope = element.dataset.scope || "config";
      const key = element.dataset.field;
      let value;
      if (element.type === "checkbox") value = element.checked;
      else if (element.multiple) value = Array.from(element.selectedOptions).map((item) => item.value);
      else if (element.type === "number" || element.type === "range") value = element.value === "" ? 0 : Number(element.value);
      else value = element.value;
      if (element.dataset.transform === "lines") value = String(value).split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      if (scope === "env") {
        if (element.dataset.secret && !value) delete state.draft.env[key];
        else state.draft.env[key] = value;
      } else {
        state.draft.config[key] = value;
      }
      markConfigurationDirty();
      if (element.type === "range") {
        const output = $$('[data-range-output]', root)
          .find((item) => item.dataset.rangeOutput === element.dataset.rangeKey);
        // ``HTMLOutputElement.value`` is not rendered consistently by all
        // engines.  Updating the text node keeps the value visible in the
        // browser as the slider moves.
        if (output) output.textContent = element.value;
      }
    });
  });
}

function tableId(key) { return `${state.page}:${key}`; }

function pagedTableMarkup(id, columns, rows, options = {}) {
  const entry = state.tables[id] || { size: 5, page: 0 };
  state.tables[id] = entry;
  const size = entry.size;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  entry.page = Math.min(entry.page, pages - 1);
  const visible = rows.slice(entry.page * size, (entry.page + 1) * size);
  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = visible.length ? visible.map((row) => `<tr>${columns.map((column) => `<td>${column.html ? column.html(row) : escapeHtml(column.value ? column.value(row) : row[column.key] ?? "—")}</td>`).join("")}</tr>`).join("") : `<tr><td class="empty-cell" colspan="${columns.length}">${escapeHtml(options.empty || "暂无数据")}</td></tr>`;
  // An empty collection has nothing to page through.  Leaving the disabled
  // page controls below an empty-state message made maintenance and
  // diagnostic cards look like unfinished forms rather than a clear empty
  // result.  Keep headers when they provide context, but show paging only
  // once there are real rows to inspect.
  const pager = rows.length ? `<div class="pager"><label>${localeText("每页", "Rows per page")}<select data-table-size="${escapeAttribute(id)}"><option value="5" ${size === 5 ? "selected" : ""}>${pageSizeLabel(5)}</option><option value="10" ${size === 10 ? "selected" : ""}>${pageSizeLabel(10)}</option></select></label><span>${pagerSummary(entry.page + 1, pages, rows.length)}</span><button class="secondary-button compact-button" data-table-prev="${escapeAttribute(id)}" ${entry.page === 0 ? "disabled" : ""}>${localeText("上一页", "Previous")}</button><button class="secondary-button compact-button" data-table-next="${escapeAttribute(id)}" ${entry.page >= pages - 1 ? "disabled" : ""}>${localeText("下一页", "Next")}</button></div>` : "";
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${pager}</div>`;
}

function pagedTable(key, columns, rows, options = {}) {
  const id = tableId(key);
  state.pagedRenderers.set(id, () => pagedTableMarkup(id, columns, rows, options));
  return `<div data-paged-host="${escapeAttribute(id)}">${pagedTableMarkup(id, columns, rows, options)}</div>`;
}

function nativeScrollTable(columns, rows, options = {}) {
  // Use this for compact, inspection-oriented lists.  Pagination makes it
  // unnecessarily hard to compare a small preference/cache list, while an
  // internal native scrollbar keeps the page height stable once it grows.
  const visibleRows = Math.max(1, Number(options.visibleRows || 10));
  const template = `repeat(${Math.max(1, columns.length)}, minmax(0, 1fr))`;
  const header = columns.map((column) => `<span role="columnheader">${escapeHtml(column.label)}</span>`).join("");
  const content = rows.length
    ? rows.map((row) => `<div class="native-scroll-table-row" role="row">${columns.map((column) => {
      const value = column.value ? column.value(row) : row[column.key] ?? "—";
      return `<span role="cell">${escapeHtml(value)}</span>`;
    }).join("")}</div>`).join("")
    : `<p class="empty-state native-scroll-empty">${escapeHtml(options.empty || "暂无数据")}</p>`;
  return `<div class="native-scroll-table" role="table" style="--native-scroll-columns:${escapeAttribute(template)};--native-scroll-visible-rows:${visibleRows}"><div class="native-scroll-table-header" role="row">${header}</div><div class="native-scroll-table-body">${content}</div></div>`;
}

function bindPagers(root = document) {
  $$('[data-table-size]', root).forEach((element) => element.addEventListener("change", () => {
    const item = state.tables[element.dataset.tableSize];
    if (!item) return;
    item.size = Number(element.value);
    item.page = 0;
    refreshPagedHost(element.dataset.tableSize);
  }));
  $$('[data-table-prev]', root).forEach((element) => element.addEventListener("click", () => {
    const item = state.tables[element.dataset.tablePrev];
    if (!item) return;
    item.page -= 1;
    refreshPagedHost(element.dataset.tablePrev);
  }));
  $$('[data-table-next]', root).forEach((element) => element.addEventListener("click", () => {
    const item = state.tables[element.dataset.tableNext];
    if (!item) return;
    item.page += 1;
    refreshPagedHost(element.dataset.tableNext);
  }));
}

function refreshPagedHost(id, root = document) {
  const render = state.pagedRenderers.get(id);
  const host = $$('[data-paged-host]', root).find((item) => item.dataset.pagedHost === id)
    || $$('[data-paged-host]').find((item) => item.dataset.pagedHost === id);
  if (!render || !host) return false;
  host.innerHTML = render();
  bindPagers(host);
  applyLocale(host);
  return true;
}

function triggerNotice(status) {
  const trigger = status?.trigger;
  if (!trigger?.stale) return "";
  const english = state.language === "en";
  const age = Number(trigger.age_seconds);
  const ageText = Number.isFinite(age)
    ? (english ? `Waiting for ${Math.max(0, Math.floor(age))} seconds` : `已等待 ${Math.max(0, Math.floor(age))} 秒`)
    : (english ? "Waiting beyond the normal hand-off time" : "已等待超过正常接手时间");
  const title = english ? "⚠️ Worker has not claimed the request" : "⚠️ 工作进程未接手请求";
  if (trigger.can_clear) {
    const description = english
      ? `${ageText}, with no active task. Clear the stale local request before starting again.`
      : `${ageText}，且没有运行中的任务。可以清除本地过期请求后重新开始。`;
    return `<div class="issue-box trigger-notice"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p><button class="secondary-button compact-button" data-clear-stale-triggers="1">${english ? "Clear stale requests" : "清除过期请求"}</button></div>`;
  }
  const description = english
    ? `${ageText}, with no active task. In Docker, keep the request and inspect or restart the research container.`
    : `${ageText}，且没有运行中的任务。Docker 部署请保留请求，并检查或重启研究容器。`;
  return `<div class="issue-box trigger-notice"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p></div>`;
}

function statusCard(status, options = {}) {
  const task = status.task || {};
  const total = Number(task.total);
  const current = Number(task.current);
  const progress = Number.isFinite(total) && total > 0 && Number.isFinite(current) ? `<div class="progress"><div><i style="width:${Math.max(3, Math.min(100, current / total * 100))}%"></i></div><span>当前进度 ${current} / ${total}</span></div>` : "";
  const counters = task.counters && typeof task.counters === "object" ? task.counters : null;
  const counterText = counters ? [
    `登记 ${formatNumber(counters.registered)}`,
    `评分 ${formatNumber(counters.scored)}`,
    `分析 ${formatNumber(counters.analyzed)}`,
    `完成 ${formatNumber(counters.completed)}`,
    `失败 ${formatNumber(counters.failed)}`,
  ].join(" · ") : "";
  const relevantLocks = Array.isArray(status.relevant_locks) ? status.relevant_locks : [];
  const relevantNames = new Set(relevantLocks.map((lock) => String(lock.name || "")));
  const lockLine = relevantLocks.length ? `<p class="status-locks">运行锁：${relevantLocks.map((lock) => `${escapeHtml(lock.name || "—")}${lock.pid ? ` (PID ${escapeHtml(lock.pid)})` : ""}`).join(" · ")}</p>` : "";
  const relatedLocks = (Array.isArray(status.active_locks) ? status.active_locks : []).filter((lock) => !relevantNames.has(String(lock.name || "")));
  const relatedLine = relatedLocks.length ? `<p class="status-locks">同时运行：${relatedLocks.map((lock) => escapeHtml(lock.name || "—")).join(" · ")}</p>` : "";
  const liveLog = status.live_log && typeof status.live_log === "object" && status.live_log.content ? `<details class="live-log" open><summary>📜 ${escapeHtml(status.live_log.name || "运行日志")} · 日志尾部 15 行${status.live_log.truncated ? "（已截断）" : ""}</summary><pre>${escapeHtml(status.live_log.content)}</pre></details>` : "";
  const stop = status.can_stop && options.allowStop !== false
    ? `<button class="danger-button" data-stop-task="${escapeAttribute(status.stop_kind || options.kind || "")}">停止当前任务</button>`
    : "";
  const refresh = options.refresh === false ? "" : `<button class="secondary-button" data-refresh-status="${escapeAttribute(options.kind || "daily")}">刷新状态</button>`;
  return `<div class="status-card"><div class="status-line"><i class="status-dot ${escapeAttribute(task.state || "idle")}"></i><div><p class="eyebrow">当前任务</p><h3>${escapeHtml(task.label || "正在读取状态")}</h3><p class="muted">${escapeHtml(task.phase || "")}</p></div><span class="timestamp">${task.started_at ? `开始于 ${escapeHtml(formatTime(task.started_at))}` : ""}</span></div>${counterText ? `<p class="status-counters">${escapeHtml(counterText)}</p>` : ""}${progress}${lockLine}${relatedLine}${triggerNotice(status)}${task.detail ? `<p class="issue-box">${escapeHtml(task.detail)}</p>` : ""}${liveLog}<div class="action-row">${options.startLabel ? `<button class="primary-button" data-start-task="${escapeAttribute(options.mode)}" ${status.can_start ? "" : "disabled"}>${escapeHtml(options.startLabel)}</button>` : ""}${stop}${refresh}</div></div>`;
}

function metrics(items) {
  return `<div class="metric-grid">${items.map((item) => `<article class="metric-card"><p>${escapeHtml(item.label)}</p><strong>${escapeHtml(item.value)}</strong><span>${escapeHtml(item.help || "")}</span></article>`).join("")}</div>`;
}

async function fetchStatus(kind) { return api(`/api/status/${encodeURIComponent(kind)}`); }

function dailyLaunch(status) {
  const launchHint = status.can_start ? "" : '<p class="hint-text">已有任务运行或正在等待工作进程接手；完成后可再次启动。</p>';
  return `<div class="action-row"><button class="primary-button" data-start-task="daily_research" ${status.can_start ? "" : "disabled"}>开始每日研究</button></div>${launchHint}`;
}

function dailyQueue(status) {
  return metrics([
    { label: "待处理论文", value: formatNumber(status.queue?.pending), help: "会在后续每日研究中处理" },
    { label: "待重试论文", value: formatNumber(status.queue?.retry), help: "保留阶段状态与问题摘要" },
  ]);
}

function updateDailyStatus(root, status) {
  const launch = $("#daily-launch", root);
  const statusHost = $("#daily-status-content", root);
  const queue = $("#daily-queue-content", root);
  if (!launch || !statusHost || !queue) return false;
  launch.innerHTML = dailyLaunch(status);
  statusHost.innerHTML = statusCard(status, { kind: "daily", refresh: false });
  queue.innerHTML = dailyQueue(status);
  // Only the newly replaced status/action fragments need event bindings. The
  // settings form stays in place, keeping unfinished edits and focus intact.
  bindCommon(launch);
  bindCommon(statusHost);
  applyLocale(launch);
  applyLocale(statusHost);
  return true;
}

async function refreshDailyStatus() {
  if (state.page !== "daily_research") return;
  const root = $("#page-root");
  try {
    const status = await fetchStatus("daily");
    if (state.page !== "daily_research" || !updateDailyStatus(root, status)) return;
    if (status.is_active && state.pageData.dailyAutoRefresh !== false) {
      scheduleRefresh("daily", refreshDailyStatus, 5000);
    }
  } catch (error) {
    // Preserve the usable page and settings form if a transient status read
    // fails; the next manual refresh or task update can retry it.
    toast(`状态刷新失败：${error.message}`, "error");
  }
}

async function renderDaily(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取每日研究状态…</div>`;
  const status = await fetchStatus("daily");
  if (token !== state.renderToken) return;
  const autoRefresh = state.pageData.dailyAutoRefresh !== false;
  root.innerHTML = `${pageHeader()}${section("每日研究", `<div id="daily-launch">${dailyLaunch(status)}</div>`, { icon: "🚀" })}${divider()}${section("状态面板", `<label class="toggle-field refresh-row"><span><strong>状态自动刷新</strong><small>开启后，仅在任务运行或刚提交等待接手时每 5 秒刷新状态、队列和日志尾部。</small></span><input id="daily-auto-refresh" type="checkbox" ${autoRefresh ? "checked" : ""}/><i></i></label><div id="daily-status-content" class="task-status-region">${statusCard(status, { kind: "daily", refresh: false })}</div>${divider()}<h3>每日研究队列</h3><div id="daily-queue-content">${dailyQueue(status)}</div>`, { icon: "📊" })}${divider()}${renderDailySettings()}`;
  bindCommon(root);
  $("#daily-auto-refresh", root)?.addEventListener("change", (event) => {
    state.pageData.dailyAutoRefresh = event.target.checked;
    if (!event.target.checked) window.clearTimeout(state.timers.get("daily"));
    else if (status.is_active) scheduleRefresh("daily", refreshDailyStatus, 5000);
  });
  if (status.is_active && autoRefresh) scheduleRefresh("daily", refreshDailyStatus, 5000);
}

function renderDailySettings() {
  return section("每日研究设置", `<div class="form-grid three">${field({ label: "生成 HTML 报告", key: "enable_html_report", type: "checkbox", fallback: true })}${field({ label: "生成 Markdown 报告", key: "enable_markdown_report", type: "checkbox", fallback: true })}${field({ label: "报告包含全部论文", key: "include_all_in_report", type: "checkbox", fallback: true })}</div><div class="form-grid two">${field({ label: "本次最多处理论文数（0 不限）", key: "daily_max_papers_per_run", type: "number", min: 0, max: 100000, step: 1, fallback: 200 })}${field({ label: "每日运行时间", key: "daily_run_time", type: "time", fallback: "12:00" })}</div>`, { icon: "⚙️", saveHint: false });
}

function compactTaskNotice(status) {
  const task = status?.task || {};
  if (status?.is_active) return `<p class="info-box">⏳ ${escapeHtml(task.label || "任务")}${task.phase ? ` · ${escapeHtml(task.phase)}` : ""}</p>`;
  if (["failed", "rejected", "interrupted", "skipped_busy"].includes(task.state)) return `<p class="issue-box">${escapeHtml(task.label || "上次任务未完成")}${task.detail ? `：${escapeHtml(task.detail)}` : ""}</p>`;
  return "";
}

function pastDailyMarkup(status, values) {
  const yesterday = relativeLocalDateKey(-1);
  const queue = status.backfill || {};
  const hasQueue = ["pending", "running", "completed", "failed"].some((key) => Number(queue[key] || 0) > 0);
  return `${section("过去日报", `<p class="hint-text">选择过去日期范围后开始运行。系统会按天把任务写入持久化队列，并与其他研究任务安全互斥。</p><div class="form-grid two"><label class="form-field"><span>开始日期</span><input id="backfill-from" type="date" min="1991-01-01" max="${yesterday}" value="${escapeAttribute(values.from)}" /></label><label class="form-field"><span>结束日期</span><input id="backfill-to" type="date" min="1991-01-01" max="${yesterday}" value="${escapeAttribute(values.to)}" /></label></div><div class="action-row"><button id="backfill-start" class="primary-button" ${status.can_start ? "" : "disabled"}>开始运行</button><button id="past-status-refresh" class="secondary-button">刷新状态</button></div>${triggerNotice(status)}${compactTaskNotice(status)}`, { icon: "🗓" })}${divider()}${section("过去日报队列", hasQueue ? metrics([
    { label: "等待中", value: formatNumber(queue.pending), help: queue.next_date ? `下一日期：${queue.next_date}` : "暂无待处理日期" },
    { label: "运行中", value: formatNumber(queue.running), help: queue.active_date ? `当前日期：${queue.active_date}` : "" },
    { label: "已完成", value: formatNumber(queue.completed), help: "已生成历史日期报告" },
    { label: "失败", value: formatNumber(queue.failed), help: queue.first_error || "失败任务可在日志中查看" },
  ]) : '<p class="empty-state">当前没有过去日报任务。</p>', { icon: "📋" })}`;
}

function bindPastDaily(root, values, token) {
  $("#backfill-from", root)?.addEventListener("change", (event) => { state.pageData.past = { ...values, from: event.target.value }; });
  $("#backfill-to", root)?.addEventListener("change", (event) => { state.pageData.past = { ...values, to: event.target.value }; });
  $("#past-status-refresh", root)?.addEventListener("click", () => runLocalRefresh(refreshPastDailyContent(root, token)));
  $("#backfill-start", root)?.addEventListener("click", async () => {
    const from = $("#backfill-from").value; const to = $("#backfill-to").value;
    if (!from || !to || from > to) return toast("请填写有效的开始和结束日期。", "error");
    try { await api("/api/tasks/backfill_run", { method: "POST", body: { args: { date_from: from, date_to: to } } }); toast("过去日报已加入队列。 "); await refreshPastDailyContent(root, token); } catch (error) { toast(error.message, "error"); }
  });
}

async function refreshPastDailyContent(root = $("#page-root"), token = state.renderToken) {
  const host = $("#past-daily-content", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("past-daily-content");
  const yesterday = relativeLocalDateKey(-1);
  const values = state.pageData.past || { from: yesterday, to: yesterday };
  host.innerHTML = '<div class="loading">正在读取过去日报队列…</div>';
  const status = await fetchStatus("past");
  if (token !== state.renderToken || state.page !== "past_daily" || !isCurrentLocalRequest("past-daily-content", requestVersion)) return;
  host.innerHTML = pastDailyMarkup(status, values);
  bindCommon(host);
  bindPastDaily(root, values, token);
  applyLocale(host);
}

async function renderPastDaily(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="past-daily-content"><div class="loading">正在读取过去日报队列…</div></div>`;
  await refreshPastDailyContent(root, token);
}

function renderTrendForm(templates = []) {
  const config = configValue;
  const today = relativeLocalDateKey(0);
  const defaultFrom = relativeLocalDateKey(-Number(config("trend_default_date_range_days", 365)));
  const configuredPrompt = String(config("trend_analysis_prompt", "") || "");
  const defaultTemplate = templates.find((item) => item.default) || templates.find((item) => item.builtin) || null;
  const matchingTemplate = templates.find((item) => item.text === configuredPrompt) || (!configuredPrompt ? defaultTemplate : null);
  // Keep a previously saved custom prompt even when it has not been stored
  // in the template library.  The Streamlit collector preserves that value
  // until the operator explicitly selects a template (or “no template”);
  // merely opening this page and saving another setting must not erase it.
  const values = state.pageData.trend || {
    keywords: "", date_from: defaultFrom, date_to: today, categories: [],
    max_results: config("trend_max_results", 500), sort_order: config("trend_sort_order", "ascending"),
    analysis_prompt: configuredPrompt || defaultTemplate?.text || "", template: matchingTemplate?.name || "",
  };
  const categoryChoices = arxivCategories().map((item) => ({ value: item.code, label: item.label }));
  const selectedTemplate = templates.find((item) => item.name === values.template) || null;
  const templateLabel = (item) => `${item.name}${item.default ? localeText("（默认内置）", " (built-in default)") : item.builtin ? localeText("（内置）", " (built-in)") : ""}`;
  const templateOptions = templates.map((item) => `<option value="${escapeAttribute(item.name)}" ${values.template === item.name ? "selected" : ""}>${escapeHtml(templateLabel(item))}</option>`).join("");
  const outputFormats = Array.isArray(config("trend_output_formats", ["markdown", "html"]))
    ? config("trend_output_formats", ["markdown", "html"])
    : ["markdown", "html"];
  const generateTldr = booleanValue(config("trend_generate_tldr", true), true);
  const outputField = (key, format, label) => {
    const enabled = state.draft.config[key] ?? outputFormats.includes(format);
    return `<label class="toggle-field"><span>${escapeHtml(label)}</span><input type="checkbox" data-field="${escapeAttribute(key)}" data-scope="config" ${enabled ? "checked" : ""}/><i></i></label>`;
  };
  const templateName = selectedTemplate?.name || String(values.template_name || "");
  const templateText = String(values.analysis_prompt || selectedTemplate?.text || defaultTemplate?.text || "");
  const templateEditor = `<div class="trend-template-editor"><div class="form-grid two"><label class="form-field"><span>提示词模板</span><select id="trend-template"><option value="">新建或自定义模板</option>${templateOptions}</select></label><div class="form-field"><span>模板操作</span><div class="template-action-buttons"><button id="trend-template-new" type="button" class="secondary-button">新建模板</button><button id="trend-template-delete" type="button" class="secondary-button" ${selectedTemplate && (!selectedTemplate.builtin || selectedTemplate.overridden) ? "" : "disabled"}>${selectedTemplate?.builtin ? "恢复内置模板" : "删除当前模板"}</button></div></div></div><label class="form-field"><span>模板名称</span><input id="trend-template-name" maxlength="120" ${selectedTemplate ? "readonly" : ""} value="${escapeAttribute(templateName)}" placeholder="例如：实验进展综述" /></label><label class="form-field"><span>模板内容</span><textarea id="trend-template-text" rows="8" maxlength="8000" placeholder="填写趋势研究的深度分析要求">${escapeHtml(templateText)}</textarea></label><p class="hint-text">每次趋势研究都会运行综合分析；默认使用内置模板，可在此查看、编辑、保存或恢复。</p><div class="action-row"><button id="trend-template-save" type="button" class="secondary-button">保存模板</button><span id="trend-template-result" class="inline-result"></span></div></div>`;
  const tldrSettings = `${field({ label: "为每篇趋势论文生成 TL;DR", key: "trend_generate_tldr", type: "checkbox", fallback: true, help: "用简短文字概括每篇入选论文的研究问题、方法和结论；关闭后趋势报告只保留标题、摘要和综合分析。" })}<div id="trend-tldr-dependent" class="trend-dependent-fields" ${generateTldr ? "" : "hidden"}>${field({ label: "TL;DR 批大小", key: "trend_tldr_batch_size", type: "number", min: 1, max: 50, fallback: 10 })}</div>`;
  const analysisSettings = `<h3>综合分析模板</h3>${templateEditor}`;
  return {
    run: `<label class="form-field"><span>研究关键词</span><input id="trend-keywords" value="${escapeAttribute(values.keywords)}" placeholder="例如 quantum error correction" /></label>`,
    parameters: `<div class="form-grid two"><label class="form-field"><span>开始日期</span><input id="trend-from" type="date" value="${escapeAttribute(values.date_from)}" /></label><label class="form-field"><span>结束日期</span><input id="trend-to" type="date" value="${escapeAttribute(values.date_to)}" /></label></div><div id="trend-categories-host">${tagMultiSelect({ id: "trend-categories", label: "arXiv 分类（可选）", selected: values.categories, choices: categoryChoices, addLabel: localeText("添加分类", "Add category"), emptyLabel: localeText("不限制 arXiv 分类", "No arXiv category limit") })}</div>`,
    configuration: `<div class="form-grid two"><label class="form-field"><span>排序</span><select id="trend-sort"><option value="ascending" ${values.sort_order === "ascending" ? "selected" : ""}>由早到晚</option><option value="descending" ${values.sort_order === "descending" ? "selected" : ""}>由晚到早</option></select></label><label class="form-field"><span>最多结果数</span><input id="trend-max-results" type="number" min="10" max="5000" value="${escapeAttribute(values.max_results)}" /></label>${field({ label: "默认日期范围（天）", key: "trend_default_date_range_days", type: "number", min: 30, max: 3650, fallback: 365 })}${field({ label: "报告位置", key: "trend_report_position", type: "select", choices: [{ value: "beginning", label: "报告开头" }, { value: "end", label: "报告末尾" }], fallback: "end" })}</div><div class="trend-settings-block">${tldrSettings}</div><h3>输出格式</h3><div class="form-grid two">${outputField("trend_output_md", "markdown", "输出 Markdown")}${outputField("trend_output_html", "html", "输出 HTML")}</div>${analysisSettings}`,
  };
}

function parseTrendKeywords(value) {
  const source = String(value || "").trim();
  if (!source) return [];
  const words = [];
  let current = "";
  let quote = "";
  let escaping = false;
  for (const character of source) {
    if (escaping) {
      current += character;
      escaping = false;
      continue;
    }
    if (character === "\\") {
      escaping = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      else current += character;
      continue;
    }
    if (character === "\"" || character === "'") {
      quote = character;
      continue;
    }
    if (/\s/.test(character)) {
      if (current) {
        words.push(current);
        current = "";
      }
      continue;
    }
    current += character;
  }
  if (escaping || quote) throw new Error("关键词中的引号或转义符不完整。");
  if (current) words.push(current);
  return words;
}

async function renderTrend(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取趋势任务状态…</div>`;
  const [status, templateData] = await Promise.all([fetchStatus("trend"), api("/api/trend/templates")]);
  if (token !== state.renderToken) return;
  let templates = Array.isArray(templateData.items) ? templateData.items : [];
  const form = renderTrendForm(templates);
  root.innerHTML = `${pageHeader()}${section("趋势研究", `${form.run}<div id="trend-launch"><div class="action-row"><button id="trend-start" class="primary-button" ${status.can_start ? "" : "disabled"}>开始运行</button></div></div><div id="trend-status-content" class="task-status-region">${statusCard(status, { kind: "trend", refresh: false })}</div>`, { icon: "📈" })}${divider()}${section("分析参数", form.parameters, { icon: "🔍" })}${divider()}${section("趋势研究配置", form.configuration, { icon: "⚙️", hint: "输出格式会在保存时转换为兼容配置。" })}`;
  bindCommon(root);
  const preserveTrend = () => {
    const prior = state.pageData.trend || {};
    const templateControl = $("#trend-template");
    state.pageData.trend = {
      keywords: $("#trend-keywords").value, date_from: $("#trend-from").value, date_to: $("#trend-to").value,
      categories: Array.isArray(prior.categories) ? [...prior.categories] : [], sort_order: $("#trend-sort").value,
      max_results: Number($("#trend-max-results").value), template: templateControl?.value ?? String(prior.template || ""),
      template_name: $("#trend-template-name")?.value ?? String(prior.template_name || ""),
      analysis_prompt: $("#trend-template-text")?.value ?? String(prior.analysis_prompt || ""),
    };
  };
  const templateOptions = (selected) => `<option value="">新建或自定义模板</option>${templates.map((item) => `<option value="${escapeAttribute(item.name)}" ${item.name === selected ? "selected" : ""}>${escapeHtml(`${item.name}${item.default ? localeText("（默认内置）", " (built-in default)") : item.builtin ? localeText("（内置）", " (built-in)") : ""}`)}</option>`).join("")}`;
  const selectedTemplate = () => templates.find((item) => item.name === $("#trend-template")?.value) || null;
  const updateTemplateAction = () => {
    const item = selectedTemplate();
    const button = $("#trend-template-delete");
    if (!button) return;
    button.disabled = !item || Boolean(item.builtin && !item.overridden);
    button.textContent = item?.builtin ? "恢复内置模板" : "删除当前模板";
  };
  const selectTemplate = (name) => {
    const item = templates.find((candidate) => candidate.name === name) || null;
    const selector = $("#trend-template");
    const nameInput = $("#trend-template-name");
    const textInput = $("#trend-template-text");
    if (selector) selector.value = item?.name || "";
    if (nameInput) {
      nameInput.value = item?.name || "";
      nameInput.readOnly = Boolean(item);
    }
    if (textInput) textInput.value = item?.text || "";
    updateTemplateAction();
    preserveTrend();
  };
  const refreshTemplateOptions = (selected) => {
    const selector = $("#trend-template");
    if (!selector) return;
    selector.innerHTML = templateOptions(selected);
    selector.value = selected || "";
  };
  const trendCategoryChoices = () => arxivCategories().map((item) => ({ value: item.code, label: item.label }));
  const renderTrendCategories = () => {
    const host = $("#trend-categories-host", root);
    if (!host) return;
    const selected = state.pageData.trend?.categories || [];
    host.innerHTML = tagMultiSelect({
      id: "trend-categories",
      label: "arXiv 分类（可选）",
      selected,
      choices: trendCategoryChoices(),
      addLabel: localeText("添加分类", "Add category"),
      emptyLabel: localeText("不限制 arXiv 分类", "No arXiv category limit"),
    });
    bindTrendCategories();
    applyLocale(host);
  };
  const bindTrendCategories = () => bindTagSelector(root, "trend-categories", (value) => {
    preserveTrend();
    if (!value || state.pageData.trend.categories.includes(value)) return;
    state.pageData.trend.categories.push(value);
    renderTrendCategories();
  }, (value) => {
    preserveTrend();
    state.pageData.trend.categories = state.pageData.trend.categories.filter((item) => item !== value);
    renderTrendCategories();
  });
  ["#trend-keywords", "#trend-from", "#trend-to"].forEach((selector) => $(selector)?.addEventListener("change", preserveTrend));
  ["#trend-sort", "#trend-max-results", "#trend-template-name", "#trend-template-text"].forEach((selector) => $(selector)?.addEventListener("change", () => {
    preserveTrend(); markConfigurationDirty();
  }));
  $("#trend-keywords")?.addEventListener("input", preserveTrend);
  ["#trend-template-name", "#trend-template-text"].forEach((selector) => $(selector)?.addEventListener("input", () => {
    preserveTrend(); markConfigurationDirty();
  }));
  $('[data-field="trend_generate_tldr"]', root)?.addEventListener("change", (event) => {
    $("#trend-tldr-dependent", root).hidden = !event.target.checked;
  });
  $("#trend-template", root)?.addEventListener("change", (event) => {
    selectTemplate(event.target.value);
    markConfigurationDirty();
  });
  $("#trend-template-new", root)?.addEventListener("click", () => {
    selectTemplate("");
    markConfigurationDirty();
  });
  $("#trend-template-save")?.addEventListener("click", async () => {
    const name = $("#trend-template-name").value.trim();
    const text = $("#trend-template-text").value.trim();
    try {
      const result = await api("/api/trend/templates", { method: "PUT", body: { name, text } });
      templates = Array.isArray(result.items) ? result.items : templates;
      refreshTemplateOptions(name);
      selectTemplate(name);
      markConfigurationDirty();
      $("#trend-template-result", root).textContent = "模板已保存。";
      $("#trend-template-result", root).className = "inline-result success";
      toast("提示词模板已保存。", "success");
    } catch (error) { toast(error.message, "error"); }
  });
  $("#trend-template-delete")?.addEventListener("click", async () => {
    const name = $("#trend-template").value;
    const item = selectedTemplate();
    if (!name || !item) return;
    const action = item.builtin ? "恢复" : "删除";
    if (!window.confirm(`确认${action}提示词模板“${name}”？`)) return;
    try {
      const result = await api("/api/trend/templates/delete", { method: "POST", body: { name } });
      templates = Array.isArray(result.items) ? result.items : templates;
      const nextName = item.builtin ? name : "";
      refreshTemplateOptions(nextName);
      selectTemplate(nextName);
      markConfigurationDirty();
      toast(item.builtin ? "已恢复内置提示词模板。" : "提示词模板已删除。", "success");
    } catch (error) { toast(error.message, "error"); }
  });
  const startTrend = async () => {
    preserveTrend(); const values = state.pageData.trend;
    if (!values.keywords.trim()) return toast("请填写至少一个研究关键词。", "error");
    if (!values.date_from || !values.date_to || values.date_from > values.date_to) return toast("请填写有效的日期范围。", "error");
    let keywords;
    try {
      keywords = parseTrendKeywords(values.keywords);
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    try {
      await api("/api/tasks/trend_research", { method: "POST", body: { args: { keywords, date_from: values.date_from, date_to: values.date_to, categories: values.categories, sort_order: values.sort_order, max_results: values.max_results, analysis_prompt: values.analysis_prompt.trim() } } });
      toast("趋势任务已加入队列。 "); await refreshTrendStatus(root, token);
    } catch (error) { toast(error.message, "error"); }
  };
  root._trendStartHandler = startTrend;
  $("#trend-start").addEventListener("click", startTrend);
}

function updateTrendStatus(root, status) {
  const launch = $("#trend-launch", root);
  const statusHost = $("#trend-status-content", root);
  if (!launch || !statusHost) return false;
  launch.innerHTML = `<div class="action-row"><button id="trend-start" class="primary-button" ${status.can_start ? "" : "disabled"}>开始运行</button></div>`;
  statusHost.innerHTML = statusCard(status, { kind: "trend", refresh: false });
  bindCommon(statusHost);
  applyLocale(launch);
  applyLocale(statusHost);
  return true;
}

async function refreshTrendStatus(root = $("#page-root"), token = state.renderToken) {
  if (state.page !== "trend_tasks") return;
  try {
    const status = await fetchStatus("trend");
    if (token !== state.renderToken || !updateTrendStatus(root, status)) return;
    // The run button is replaced with the status fragment above.  Rebind its
    // existing submit handler through a click relay rather than reconstructing
    // the analysis form or discarding its in-progress values.
    const start = root._trendStartHandler;
    if (typeof start === "function") $("#trend-start", root)?.addEventListener("click", start);
  } catch (error) { toast(`状态刷新失败：${error.message}`, "error"); }
}

function reportTypeLabel(type) {
  return ({ daily: "每日研究", trend: "趋势研究", keyword_trend: "关键词趋势" })[type] || type;
}

function reportGroupKey(type, source) {
  return `${type}:${source}`;
}

function scrollSelect({ id, rows, selected, label, placeholder = "—", optionAttribute, valueAttribute }) {
  if (!rows.length) {
    return `<div class="scroll-select is-empty" aria-disabled="true"><span>${escapeHtml(placeholder)}</span></div>`;
  }
  const active = rows.find((item) => item.id === selected);
  const selectedLabel = active ? label(active) : placeholder;
  return `<details class="scroll-select" data-scroll-select="${escapeAttribute(id)}"><summary><span>${escapeHtml(selectedLabel)}</span><i class="dropdown-chevron" aria-hidden="true"></i></summary><div class="scroll-select-options" role="listbox">${rows.map((item) => `<button type="button" role="option" aria-selected="${item.id === selected ? "true" : "false"}" class="${item.id === selected ? "is-selected" : ""}" ${escapeAttribute(optionAttribute)}="${escapeAttribute(id)}" ${escapeAttribute(valueAttribute)}="${escapeAttribute(item.id)}">${escapeHtml(label(item))}</button>`).join("")}</div></details>`;
}

function reportPicker(title, icon, type, rows, selected) {
  if (!rows.length) {
    return `<div class="report-picker"><h3>${escapeHtml(icon)} ${escapeHtml(title)}</h3><p class="report-count">${reportCountLabel(0)}</p><p class="muted">${localeText("暂无报告", "No reports")}</p></div>`;
  }
  const groups = new Map();
  rows.forEach((row) => {
    const source = String(row.source || "unknown");
    if (!groups.has(source)) groups.set(source, []);
    groups.get(source).push(row);
  });
  const selections = state.pageData.reportSelections || {};
  const body = [...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([source, groupRows]) => {
    const groupKey = reportGroupKey(type, source);
    const saved = selections[groupKey];
    const selectedHere = groupRows.some((item) => item.id === selected) ? selected : (groupRows.some((item) => item.id === saved) ? saved : groupRows[0].id);
    const sourceLabel = type === "keyword_trend"
      ? "关键词趋势"
      : String(groupRows[0].source_label || source);
    return `<div class="report-picker-group"><div class="report-select-field"><span>${escapeHtml(sourceLabel)} <small>(${groupRows.length})</small></span>${scrollSelect({ id: groupKey, rows: groupRows, selected: selectedHere, label: (item) => item.label, optionAttribute: "data-report-select-option", valueAttribute: "data-report-id" })}</div><button class="secondary-button compact-button report-preview-button" data-preview-group="${escapeAttribute(groupKey)}">${localeText("预览", "Preview")}</button></div>`;
  }).join("");
  return `<div class="report-picker"><h3>${escapeHtml(icon)} ${escapeHtml(title)}</h3><p class="report-count">${reportCountLabel(rows.length)}</p>${body}</div>`;
}

function formatReportSize(bytes) {
  const size = Number(bytes);
  return Number.isFinite(size) ? `${(size / 1024).toFixed(1)} KB` : "—";
}

function findAdjacentDailyReport(report, rows, direction) {
  if (!report?.date) return null;
  const sameSource = rows.filter((item) => item.source === report.source && item.date);
  const dates = [...new Set(sameSource.map((item) => item.date))].sort();
  const current = dates.indexOf(report.date);
  const targetDate = dates[current + direction];
  if (!targetDate) return null;
  // ``list_reports`` returns the newest report first, including when a
  // supplement and a normal run share the same logical calendar date.
  return sameSource.find((item) => item.date === targetDate) || null;
}

function reportInfoHtml(report) {
  const metadata = report.type === "trend" && report.metadata && Object.keys(report.metadata).length
    ? `<details class="report-metadata"><summary>报告元数据</summary><div class="metric-grid compact-metrics">${report.metadata.keyword !== undefined ? `<div class="metric-card"><p>关键词</p><strong>${escapeHtml(report.metadata.keyword)}</strong></div>` : ""}${report.metadata.date_from && report.metadata.date_to ? `<div class="metric-card"><p>时间范围</p><strong>${escapeHtml(report.metadata.date_from)} → ${escapeHtml(report.metadata.date_to)}</strong></div>` : ""}${report.metadata.total_papers !== undefined ? `<div class="metric-card"><p>论文数量</p><strong>${escapeHtml(report.metadata.total_papers)}</strong></div>` : ""}</div></details>`
    : "";
  return `<p class="report-file-info"><strong>${escapeHtml(reportTypeLabel(report.type))}</strong> · <code>${escapeHtml(report.source)}</code> · <code>${escapeHtml(report.name)}</code> · ${escapeHtml(formatReportSize(report.size_bytes))} · ${localeText("修改时间：", "Modified:")}${escapeHtml(formatTime(report.modified_at))}</p>${metadata}`;
}

function normalizeReportText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function appendReportMarkup(rawHtml, markup, closingTag) {
  const source = String(rawHtml || "");
  const pattern = new RegExp(`</${closingTag}\\s*>`, "i");
  return pattern.test(source)
    ? source.replace(pattern, `${markup}</${closingTag}>`)
    : `${source}${markup}`;
}

function buildMarkedReportHtml(rawHtml, papers) {
  // Reports can be several hundred KB.  Parsing one with DOMParser, mutating
  // it and serialising it again keeps the main application thread busy for a
  // noticeable time on lower-power NAS clients.  Keep the report document
  // intact and let its sandboxed iframe perform the small decoration after
  // the browser has parsed it in the iframe context instead.
  const candidates = Array.isArray(papers)
    ? papers.filter((paper) => paper?.title && paper?.paper_id && paper?.source)
    : [];
  if (!candidates.length) return { html: String(rawHtml || ""), injected: false };
  const serializedCandidates = JSON.stringify(candidates).replaceAll("<", "\\u003c");
  const style = '<style id="adr-report-mark-style">.adr-report-mark-bar{float:right;display:flex;gap:4px;margin-left:12px}.adr-report-mark-btn{border:1px solid rgba(127,127,127,.45);border-radius:8px;background:rgba(255,255,255,.78);cursor:pointer;font-size:13px;line-height:1;padding:4px 7px;color:inherit}.adr-report-mark-btn:hover{background:rgba(255,255,255,.95)}.adr-report-mark-btn.active[data-preference=like]{background:#16a34a;border-color:#16a34a;color:#fff}.adr-report-mark-btn.active[data-preference=dislike]{background:#dc2626;border-color:#dc2626;color:#fff}</style>';
  const script = `<script>(function(){if(window.__adrReportMarks)return;window.__adrReportMarks=true;var candidates=${serializedCandidates};function normalize(value){return String(value||'').replace(/\\s+/g,' ').trim().toLocaleLowerCase();}function button(preference,current){var item=document.createElement('button');item.type='button';item.className='adr-report-mark-btn'+(preference===current?' active':'');item.dataset.preference=preference;item.title=preference==='like'?'喜欢':'不感兴趣';item.textContent=preference==='like'?'👍':'👎';return item;}function set(bar,pref){pref=pref==='like'||pref==='dislike'?pref:'none';bar.dataset.current=pref;bar.querySelectorAll('.adr-report-mark-btn').forEach(function(item){item.classList.toggle('active',item.dataset.preference===pref);});}document.querySelectorAll('.revision-label').forEach(function(node){var text=normalize(node.textContent);if(/^v\\d+$/.test(text)||text==='↻ 重试')node.remove();});var used={};document.querySelectorAll('.card.pass,.card.fail').forEach(function(card){var cardText=normalize(card.textContent);var index=-1;for(var i=0;i<candidates.length;i+=1){if(!used[i]&&cardText.indexOf(normalize(candidates[i].title))!==-1){index=i;break;}}if(index<0)return;var field=card.querySelector('.field');if(!field)return;var paper=candidates[index];used[index]=true;var current=paper.preference==='like'||paper.preference==='dislike'?paper.preference:'none';var bar=document.createElement('div');bar.className='adr-report-mark-bar';bar.dataset.source=String(paper.source);bar.dataset.paperId=String(paper.paper_id);bar.dataset.current=current;bar.append(button('like',current),button('dislike',current));field.insertBefore(bar,field.firstChild);});window.addEventListener('message',function(event){var data=event.data||{};if(data.type!=='adr-report-mark-state')return;document.querySelectorAll('.adr-report-mark-bar').forEach(function(bar){if(bar.dataset.source===String(data.source||'')&&bar.dataset.paperId===String(data.paper_id||''))set(bar,data.preference);});});document.addEventListener('click',function(event){var item=event.target&&event.target.closest?event.target.closest('.adr-report-mark-btn'):null;if(!item)return;event.preventDefault();var bar=item.closest('.adr-report-mark-bar');if(!bar)return;var wanted=item.dataset.preference===(bar.dataset.current||'none')?'none':item.dataset.preference;set(bar,wanted);parent.postMessage({type:'adr-report-mark',source:bar.dataset.source,paper_id:bar.dataset.paperId,preference:wanted},'*');});})();</script>`;
  return {
    html: appendReportMarkup(appendReportMarkup(rawHtml, style, "head"), script, "body"),
    injected: true,
  };
}

async function fetchReportHtml(reportId) {
  const response = await fetch(`/api/reports/${encodeURIComponent(reportId)}/file`, { credentials: "same-origin" });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || "读取报告失败。 ");
  }
  return response.text();
}

function reportDirectoryMarkup(reports, selected, showNonArxiv) {
  const browser = section("报告浏览", `<div class="toolbar"><label class="toggle-field"><span>显示非 arXiv 来源报告</span><input id="report-non-arxiv" type="checkbox" ${showNonArxiv ? "checked" : ""}/><i></i></label><button id="reports-refresh" class="secondary-button">刷新列表</button></div><div class="report-grid">${reportPicker("每日研究", "📅", "daily", reports.daily, selected)}${reportPicker("趋势研究", "🔬", "trend", reports.trend, selected)}${reportPicker("关键词趋势", "📈", "keyword_trend", reports.keyword_trend, selected)}</div>`, { icon: "📚" });
  return `${browser}${selected ? '<div id="report-preview" class="loading">正在加载报告预览…</div>' : section("报告预览", '<p class="report-empty-state">尚未生成可查看的报告。</p>')}`;
}

function updateReportPickerSelection(root, report) {
  const groupKey = reportGroupKey(report.type, report.source);
  $$('[data-report-select-option]', root).forEach((option) => {
    if (option.dataset.reportSelectOption !== groupKey) return;
    const active = option.dataset.reportId === report.id;
    option.setAttribute("aria-selected", String(active));
    option.classList.toggle("is-selected", active);
  });
  const selector = $$('[data-scroll-select]', root).find((item) => item.dataset.scrollSelect === groupKey);
  if (selector) {
    const label = $("summary > span", selector);
    if (label) label.textContent = report.label;
    selector.open = false;
  }
}

function bindReportDirectory(root, reports, token) {
  const all = [...reports.daily, ...reports.trend, ...reports.keyword_trend];
  const chooseReport = (reportId) => {
    const report = all.find((item) => item.id === reportId);
    if (!report) return;
    state.pageData.selectedReport = report.id;
    state.pageData.reportSelections[reportGroupKey(report.type, report.source)] = report.id;
    state.reportMarkAbortController?.abort();
    state.reportMarkAbortController = null;
    updateReportPickerSelection(root, report);
    const preview = $("#report-preview", root);
    if (preview) preview.outerHTML = '<div id="report-preview" class="loading">正在加载报告预览…</div>';
    else {
      const directory = $("#reports-directory", root);
      if (directory) directory.insertAdjacentHTML("beforeend", '<div id="report-preview" class="loading">正在加载报告预览…</div>');
    }
    loadReportPreview(report, reports, token, chooseReport);
  };
  $("#report-non-arxiv", root)?.addEventListener("change", (event) => {
    state.pageData.showNonArxiv = event.target.checked;
    state.pageData.selectedReport = "";
    state.pageData.reportSelections = {};
    runLocalRefresh(refreshReportsDirectory(root, token));
  });
  $("#reports-refresh", root)?.addEventListener("click", () => {
    state.pageData.selectedReport = "";
    state.pageData.reportSelections = {};
    runLocalRefresh(refreshReportsDirectory(root, token));
  });
  $$('[data-report-select-option]', root).forEach((button) => button.addEventListener("click", () => chooseReport(button.dataset.reportId)));
  $$('[data-preview-group]', root).forEach((button) => button.addEventListener("click", () => {
    const selectedOption = $$('[data-report-select-option]', root).find((item) => (
      item.dataset.reportSelectOption === button.dataset.previewGroup
      && item.getAttribute("aria-selected") === "true"
    ));
    if (selectedOption) chooseReport(selectedOption.dataset.reportId);
  }));
  return chooseReport;
}

async function refreshReportsDirectory(root = $("#page-root"), token = state.renderToken) {
  const host = $("#reports-directory", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("reports-directory");
  state.reportMarkAbortController?.abort();
  state.reportMarkAbortController = null;
  const showNonArxiv = Boolean(state.pageData.showNonArxiv);
  host.innerHTML = '<div class="loading">正在读取报告目录…</div>';
  const reports = await api(`/api/reports?non_arxiv=${showNonArxiv ? "1" : "0"}`);
  if (token !== state.renderToken || state.page !== "reports" || !isCurrentLocalRequest("reports-directory", requestVersion)) return;
  const all = [...reports.daily, ...reports.trend, ...reports.keyword_trend];
  let selected = state.pageData.selectedReport;
  if (!selected || !all.some((item) => item.id === selected)) selected = all[0]?.id || "";
  state.pageData.selectedReport = selected;
  if (!state.pageData.reportSelections) state.pageData.reportSelections = {};
  host.innerHTML = reportDirectoryMarkup(reports, selected, showNonArxiv);
  bindCommon(host);
  const chooseReport = bindReportDirectory(root, reports, token);
  applyLocale(host);
  const report = all.find((item) => item.id === selected);
  if (report) await loadReportPreview(report, reports, token, chooseReport);
}

async function renderReports(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="reports-directory"><div class="loading">正在读取报告目录…</div></div>`;
  await refreshReportsDirectory(root, token);
}

async function loadReportPreview(report, reports, token, chooseReport) {
  const preview = $("#report-preview");
  if (!preview) return;
  const requestVersion = beginLocalRequest("report-preview");
  try {
    const [html, paperResponse] = await Promise.all([
      fetchReportHtml(report.id),
      report.type === "daily"
        ? api(`/api/reports/${encodeURIComponent(report.id)}/papers`).catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] }),
    ]);
    if (token !== state.renderToken || state.pageData.selectedReport !== report.id || !isCurrentLocalRequest("report-preview", requestVersion)) return;
    const marked = buildMarkedReportHtml(html, paperResponse.items || []);
    const previous = report.type === "daily" ? findAdjacentDailyReport(report, reports.daily, -1) : null;
    const next = report.type === "daily" ? findAdjacentDailyReport(report, reports.daily, 1) : null;
    const navigation = report.type === "daily" ? `<div class="report-navigation"><button class="secondary-button compact-button" data-report-nav="${previous ? escapeAttribute(previous.id) : ""}" ${previous ? "" : "disabled"}>← 前一天</button><button class="secondary-button compact-button" data-report-nav="${next ? escapeAttribute(next.id) : ""}" ${next ? "" : "disabled"}>后一天</button></div>` : "";
    // ``#report-preview`` starts as a loading placeholder.  Drop that class
    // once real content arrives; retaining it wraps the full preview card in
    // a second, oversized dashed border.
    preview.className = "report-preview-host";
    preview.innerHTML = section("报告预览", `${reportInfoHtml(report)}${navigation}<iframe class="report-frame" sandbox="allow-scripts allow-popups" referrerpolicy="no-referrer" title="报告预览"></iframe>`);
    const frame = $(".report-frame", preview);
    frame.srcdoc = marked.html;
    $$('[data-report-nav]', preview).forEach((button) => button.addEventListener("click", () => {
      if (button.dataset.reportNav) chooseReport(button.dataset.reportNav);
    }));
    if (report.type === "daily" && marked.injected) {
      const markAbortController = new AbortController();
      state.reportMarkAbortController = markAbortController;
      window.addEventListener("message", async function onReportMark(event) {
        if (event.source !== frame.contentWindow) return;
        const action = event.data || {};
        if (action.type !== "adr-report-mark") return;
        const paper = (paperResponse.items || []).find((item) => String(item.source) === String(action.source || "") && String(item.paper_id) === String(action.paper_id || ""));
        if (!paper || !["like", "dislike", "none"].includes(action.preference)) return;
        try {
          const saved = await api("/api/preferences", { method: "PUT", body: { ...paper, preference: action.preference } });
          paper.preference = saved.preference;
          frame.contentWindow?.postMessage({ type: "adr-report-mark-state", source: paper.source, paper_id: paper.paper_id, preference: saved.preference }, "*");
          toast("论文偏好已保存。 ");
        } catch (error) {
          frame.contentWindow?.postMessage({ type: "adr-report-mark-state", source: paper.source, paper_id: paper.paper_id, preference: paper.preference || "none" }, "*");
          toast(error.message, "error");
        }
      }, { signal: markAbortController.signal });
    }
  } catch (error) {
    preview.className = "report-preview-host";
    preview.innerHTML = section("报告预览", `<p class="error-message">${escapeHtml(error.message)}</p>`);
  }
}

async function renderFavorites(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取收藏数据…</div>`;
  const data = await api("/api/favorites");
  if (token !== state.renderToken) return;
  const settingsCard = favoritesSettingsCard();
  if (!data.available) {
    root.innerHTML = `${pageHeader()}${section("收藏", '<p class="empty-state">SQLite 数据库尚未创建；运行一次每日研究或导入历史后即可查看。</p>', { icon: "⭐" })}${divider()}${settingsCard}`;
  } else {
    const likeCount = Number(data.counts?.like || 0);
    const dislikeCount = Number(data.counts?.dislike || 0);
    if (!likeCount && !dislikeCount) {
      root.innerHTML = `${pageHeader()}${section("收藏", '<p class="empty-state">尚未标记论文。可直接在每日研究报告的论文卡片中使用 👍 或 👎。</p>', { icon: "⭐" })}${divider()}${settingsCard}`;
    } else {
      const likedRows = Array.isArray(data.liked) ? data.liked : [];
      const paperTitle = (row) => {
        const fallback = String(row.source || "").toLowerCase() === "arxiv" && row.paper_id
          ? `https://arxiv.org/abs/${row.paper_id}`
          : null;
        const url = safeExternalUrl(row.url) || fallback;
        return url
          ? `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.title || row.paper_id)}</a>`
          : escapeHtml(row.title || row.paper_id);
      };
      const likedList = likedRows.length
        ? `<p class="hint-text">按标记时间倒序展示；点击标题可打开论文页面。</p><div class="favorite-papers-table">${pagedTable("favorite-papers", [
          { label: "收藏时间", value: (row) => formatTime(row.updated_at) },
          { label: "论文", html: paperTitle },
          { label: "来源", value: (row) => row.source || "—" },
          { label: "论文标识", value: (row) => row.paper_id || "—" },
        ], likedRows, { empty: "暂无 👍 收藏论文。" })}</div>`
        : '<p class="empty-state">暂无 👍 收藏论文。</p>';
      root.innerHTML = `${pageHeader()}${section("收藏的论文", `${metrics([{ label: "👍 收藏", value: formatNumber(likeCount), help: "正向偏好" }, { label: "👎 不喜欢", value: formatNumber(dislikeCount), help: "负向偏好" }])}${likedList}`, { icon: "⭐" })}${divider()}${section("收藏画像", `<div class="form-grid two"><div><p class="scroll-list-label">收藏作者 Top</p>${nativeScrollTable([{ label: "作者", key: "name" }, { label: "收藏次数", key: "count" }], data.authors || [], { empty: "暂无收藏作者统计", visibleRows: 10 })}</div><div><p class="scroll-list-label">收藏关键词</p>${nativeScrollTable([{ label: "关键词", key: "keyword" }, { label: "次数", key: "count" }], data.keywords || [], { empty: "暂无收藏关键词统计", visibleRows: 10 })}</div></div>`, { icon: "🧩" })}${divider()}${settingsCard}`;
    }
  }
  bindCommon(root);
  bindFavoriteActions(root);
}

function favoritesSettingsCard() {
  return section("收藏设置", `${field({ label: "自动收藏及格论文", key: "auto_favorite_qualified_papers", type: "checkbox", fallback: true })}<div class="action-row"><button id="favorite-collect-qualified" type="button" class="primary-button">收藏所有通过论文</button></div>`, { icon: "⚙️", saveHint: false });
}

function qualifiedFavoritesMessage(result) {
  const scanned = formatNumber(result.scanned);
  const added = formatNumber(result.added);
  return state.language === "en"
    ? `Scanned ${scanned} scored papers; added ${added} favorites.`
    : `已扫描 ${scanned} 篇评分论文，新增收藏 ${added} 篇。`;
}

async function collectQualifiedFavorites(button) {
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = localeText("正在扫描…", "Scanning…");
  try {
    const result = await api("/api/favorites/collect", { method: "POST", body: {} });
    toast(qualifiedFavoritesMessage(result), "success");
    if (state.page === "favorites") await renderFavorites(state.renderToken);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (document.body.contains(button)) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
}

function bindFavoriteActions(root) {
  $("#favorite-collect-qualified", root)?.addEventListener("click", (event) => {
    void collectQualifiedFavorites(event.currentTarget);
  });
}

function pagedItemsMarkup(id, items, empty = "暂无数据") {
  const entry = state.tables[id] || { size: 5, page: 0 };
  state.tables[id] = entry;
  const pages = Math.max(1, Math.ceil(items.length / entry.size));
  entry.page = Math.min(entry.page, pages - 1);
  const visible = items.slice(entry.page * entry.size, (entry.page + 1) * entry.size);
  if (!items.length) return `<p class="empty-state">${escapeHtml(empty)}</p>`;
  return `${visible.join("")}<div class="pager"><label>${localeText("每页", "Rows per page")}<select data-table-size="${escapeAttribute(id)}"><option value="5" ${entry.size === 5 ? "selected" : ""}>${pageSizeLabel(5)}</option><option value="10" ${entry.size === 10 ? "selected" : ""}>${pageSizeLabel(10)}</option></select></label><span>${pagerSummary(entry.page + 1, pages, items.length)}</span><button class="secondary-button compact-button" data-table-prev="${escapeAttribute(id)}" ${entry.page === 0 ? "disabled" : ""}>${localeText("上一页", "Previous")}</button><button class="secondary-button compact-button" data-table-next="${escapeAttribute(id)}" ${entry.page >= pages - 1 ? "disabled" : ""}>${localeText("下一页", "Next")}</button></div>`;
}

function pagedItems(key, items, empty = "暂无数据") {
  const id = tableId(key);
  state.pagedRenderers.set(id, () => pagedItemsMarkup(id, items, empty));
  return `<div class="paged-items-host" data-paged-host="${escapeAttribute(id)}">${pagedItemsMarkup(id, items, empty)}</div>`;
}

function searchParamsFromState() {
  const values = state.pageData.search || { query: "", source: "", completed_from: "", completed_to: "", min_score: "", liked_only: false, page: 0, size: 20 };
  state.pageData.search = values;
  const params = new URLSearchParams({ query: values.query, limit: String(values.size), offset: String(values.page * values.size) });
  if (values.source) params.set("source", values.source);
  if (values.completed_from) params.set("completed_from", values.completed_from);
  if (values.completed_to) params.set("completed_to", values.completed_to);
  if (values.min_score) params.set("min_score", values.min_score);
  if (values.liked_only) params.set("liked_only", "true");
  return params;
}

function sourceVariantCard(variant) {
  const statuses = [
    variant.strategy_id ? `策略：${variant.strategy_id}` : "",
    variant.score_status ? `评分：${variant.score_status}` : "",
    variant.translation_status ? `翻译：${variant.translation_status}` : "",
    variant.analysis_status ? `分析：${variant.analysis_status}` : "",
  ].filter(Boolean).join(" · ");
  const url = safeExternalUrl(variant.url);
  const pdfUrl = safeExternalUrl(variant.pdf_url);
  const analysis = variant.analysis && typeof variant.analysis === "object" && Object.keys(variant.analysis).length
    ? `<details class="paper-analysis"><summary>深度分析</summary><pre>${escapeHtml(JSON.stringify(variant.analysis, null, 2))}</pre></details>`
    : "";
  return `<article class="paper-variant"><h4>${escapeHtml(variant.source || "—")} · ${escapeHtml(formatTime(variant.completed_at) || "未完成")}</h4>${statuses ? `<p class="muted">${escapeHtml(statuses)}</p>` : ""}${variant.tldr ? `<p><strong>TL;DR：</strong>${escapeHtml(variant.tldr)}</p>` : ""}${variant.abstract_cn ? `<p><strong>中文摘要：</strong>${escapeHtml(variant.abstract_cn)}</p>` : ""}${variant.extracted_keywords?.length ? `<p><strong>关键词：</strong>${escapeHtml(variant.extracted_keywords.join(" · "))}</p>` : ""}${analysis}${variant.last_error ? `<p class="error-message">问题：${escapeHtml(variant.last_error)}</p>` : ""}${url || pdfUrl ? `<p>${url ? `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">论文页面</a>` : ""}${url && pdfUrl ? " · " : ""}${pdfUrl ? `<a href="${escapeAttribute(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ""}</p>` : ""}${variant.report_path ? `<p class="muted">报告路径：${escapeHtml(variant.report_path)}</p>` : ""}</article>`;
}

function paperCard(item) {
  const score = Number.isFinite(Number(item.total_score)) ? Number(item.total_score).toFixed(1) : "—";
  const badge = item.is_qualified === true ? "🟢" : item.is_qualified === false ? "⚪" : "·";
  const sources = (item.sources || [item.source]).filter(Boolean).join(", ");
  const preference = item.preference === "like" ? " 👍" : item.preference === "dislike" ? " 👎" : "";
  const url = safeExternalUrl(item.url);
  const pdfUrl = safeExternalUrl(item.pdf_url);
  const metadata = [
    `来源：${sources || "—"}`,
    `完成：${formatTime(item.completed_at) || "—"}`,
    item.published_date ? `发布：${item.published_date}` : "",
    item.strategy_id ? `策略：${item.strategy_id}` : "",
  ].filter(Boolean).join(" ｜ ");
  const variants = (item.variants || []).map(sourceVariantCard).join("");
  return `<details class="paper-card"><summary><span class="score-pill">${score}</span><strong>${badge} ${escapeHtml(item.title)}${preference}</strong><small>${escapeHtml(metadata)}</small></summary><div class="paper-content">${item.authors?.length ? `<p><strong>作者：</strong>${escapeHtml(item.authors.slice(0, 12).join(", "))}</p>` : ""}${item.tldr ? `<p><strong>TL;DR：</strong>${escapeHtml(item.tldr)}</p>` : ""}${(item.merged_keywords || item.extracted_keywords)?.length ? `<p><strong>合并关键词：</strong>${escapeHtml((item.merged_keywords || item.extracted_keywords).join(" · "))}</p>` : ""}${item.categories?.length ? `<p class="muted">分类：${escapeHtml(item.categories.join(" "))}</p>` : ""}${url || pdfUrl ? `<p>${url ? `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">arXiv / 论文页面</a>` : ""}${url && pdfUrl ? " ｜ " : ""}${pdfUrl ? `<a href="${escapeAttribute(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ""}</p>` : ""}${variants ? `<div class="paper-variants"><h3>来源变体</h3>${variants}</div>` : ""}</div></details>`;
}

async function renderPaperSearch(token) {
  const root = $("#page-root");
  const values = state.pageData.search || { query: "", source: "", completed_from: "", completed_to: "", min_score: "", liked_only: false, page: 0, size: 20 };
  root.innerHTML = `${pageHeader()}${section("检索条件", `<div class="form-grid two"><label class="form-field"><span>关键词</span><input id="search-query" value="${escapeAttribute(values.query)}" placeholder="标题、摘要、TL;DR 或关键词" /></label><label class="form-field"><span>来源</span><select id="search-source"><option value="">全部来源</option></select></label><label class="form-field"><span>完成日期开始</span><input id="search-from" type="date" value="${escapeAttribute(values.completed_from)}" /></label><label class="form-field"><span>完成日期结束</span><input id="search-to" type="date" value="${escapeAttribute(values.completed_to)}" /></label><label class="form-field"><span>最低分数</span><input id="search-score" type="number" step="0.5" min="0" value="${escapeAttribute(values.min_score)}" /></label><label class="toggle-field"><span>仅收藏论文</span><input id="search-liked" type="checkbox" ${values.liked_only ? "checked" : ""}/><i></i></label></div><div class="action-row"><button id="search-run" class="primary-button">搜索</button></div>`, { icon: "🔍" })}<div id="search-results"></div>`;
  const sourceSelect = $("#search-source");
  try {
    const sourceProbe = await api("/api/papers?limit=5&offset=0");
    if (token !== state.renderToken) return;
    if (!sourceProbe.available) {
      root.innerHTML = `${pageHeader()}${section("论文检索", '<p class="info-box">SQLite 数据库尚未创建；运行一次每日研究或导入历史后即可检索。</p>', { icon: "🔍" })}`;
      return;
    }
    sourceSelect.insertAdjacentHTML("beforeend", (sourceProbe.sources || []).map((source) => `<option value="${escapeAttribute(source)}" ${values.source === source ? "selected" : ""}>${escapeHtml(source)}</option>`).join(""));
    if (values.executed) await loadSearchResults(token);
  } catch (error) { $("#search-results").innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
  $("#search-run").addEventListener("click", () => {
    state.pageData.search = { ...values, query: $("#search-query").value, source: sourceSelect.value, completed_from: $("#search-from").value, completed_to: $("#search-to").value, min_score: $("#search-score").value, liked_only: $("#search-liked").checked, page: 0, size: values.size, executed: true };
    loadSearchResults(token);
  });
}

async function loadSearchResults(token) {
  const target = $("#search-results"); if (!target) return;
  target.innerHTML = '<div class="loading">正在检索 SQLite 历史库…</div>';
  try {
    const values = state.pageData.search;
    const result = await api(`/api/papers?${searchParamsFromState()}`);
    if (token !== state.renderToken) return;
    if (!result.available) {
      target.innerHTML = '<p class="info-box">SQLite 数据库尚未创建；运行一次每日研究或导入历史后即可检索。</p>';
      return;
    }
    const pages = Math.max(1, Math.ceil(result.total / values.size));
    const pager = pages > 1 ? `<div class="pager"><span>${state.language === "en" ? `Page ${values.page + 1} / ${pages} · 20 papers per page` : `第 ${values.page + 1} / ${pages} 页 · 每页 20 篇`}</span><button id="search-prev" class="secondary-button compact-button" ${values.page === 0 ? "disabled" : ""}>${localeText("上一页", "Previous")}</button><button id="search-next" class="secondary-button compact-button" ${values.page >= pages - 1 ? "disabled" : ""}>${localeText("下一页", "Next")}</button></div>` : "";
    target.innerHTML = section(`检索结果（共 ${result.total} 篇匹配）`, `${result.items?.length ? result.items.map(paperCard).join("") : '<p class="empty-state">没有匹配的论文。</p>'}${pager}`);
    $("#search-prev")?.addEventListener("click", () => { values.page -= 1; loadSearchResults(token); });
    $("#search-next")?.addEventListener("click", () => { values.page += 1; loadSearchResults(token); });
  } catch (error) { target.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`; }
}

function weightedEntries(configKey, legacyNamesKey, legacyValueKey, nameKey, valueKey, fallbackValue) {
  const supplied = configValue(configKey, null);
  if (Array.isArray(supplied)) {
    return supplied
      .filter((item) => item && typeof item === "object" && String(item[nameKey] || "").trim())
      .map((item) => ({ [nameKey]: String(item[nameKey]).trim(), [valueKey]: Number(item[valueKey]) }));
  }
  const names = configValue(legacyNamesKey, []);
  const sharedValue = Number(configValue(legacyValueKey, fallbackValue));
  return Array.isArray(names)
    ? names.filter((name) => String(name || "").trim()).map((name) => ({ [nameKey]: String(name).trim(), [valueKey]: sharedValue }))
    : [];
}

function weightedEntryEditor({ id, entries, nameKey, valueKey, nameLabel, valueLabel, placeholder, min = 0, max = 1, step = 0.05, addLabel = "添加" }) {
  const cards = entries.length
    ? entries.map((entry, index) => `<span class="weighted-entry-card"><span>${escapeHtml(entry[nameKey])}</span><small>${escapeHtml(Number(entry[valueKey]).toFixed(2))}</small><button type="button" data-weighted-entry-remove="${escapeAttribute(id)}" data-weighted-entry-index="${index}" aria-label="移除 ${escapeAttribute(entry[nameKey])}">×</button></span>`).join("")
    : `<span class="tag-select-placeholder">${escapeHtml(nameLabel)}尚未添加</span>`;
  return `<div id="${escapeAttribute(id)}" class="weighted-entry-editor"><div class="weighted-entry-box" aria-label="${escapeAttribute(nameLabel)}列表"><div class="weighted-entry-cards">${cards}</div></div><div class="weighted-entry-add"><label class="form-field"><span>${escapeHtml(nameLabel)}</span><input id="${escapeAttribute(id)}-name" maxlength="500" placeholder="${escapeAttribute(placeholder)}" /></label><label class="form-field narrow-number-field"><span>${escapeHtml(valueLabel)}</span><input id="${escapeAttribute(id)}-value" type="number" min="${min}" max="${max}" step="${step}" value="${escapeAttribute(step === 0.05 ? 1 : 5)}" /></label><button type="button" class="secondary-button" data-weighted-entry-add="${escapeAttribute(id)}">${escapeHtml(addLabel)}</button></div></div>`;
}

function replaceWeightedEntryEditor(root, options) {
  const host = $(`#${options.id}`, root);
  if (!host) return;
  host.outerHTML = weightedEntryEditor(options);
  bindWeightedEntryEditor(root, options);
}

function bindWeightedEntryEditor(root, options) {
  const { id, configKey, legacyNamesKey, legacyValueKey, nameKey, valueKey, fallbackValue, min = 0, max = 1 } = options;
  const currentEntries = () => weightedEntries(configKey, legacyNamesKey, legacyValueKey, nameKey, valueKey, fallbackValue);
  const persist = (entries) => {
    state.draft.config[configKey] = entries;
    state.draft.config[legacyNamesKey] = entries.map((entry) => entry[nameKey]);
    markConfigurationDirty();
  };
  $(`[data-weighted-entry-add="${id}"]`, root)?.addEventListener("click", () => {
    const name = String($(`#${id}-name`, root)?.value || "").trim();
    const numericValue = Number($(`#${id}-value`, root)?.value);
    if (!name) return toast(`请填写${options.nameLabel}。`, "error");
    if (!Number.isFinite(numericValue) || numericValue < min || numericValue > max) {
      return toast(`${options.valueLabel}必须在 ${min}–${max} 之间。`, "error");
    }
    const entries = currentEntries();
    if (entries.some((entry) => entry[nameKey].localeCompare(name, undefined, { sensitivity: "accent" }) === 0)) {
      return toast(`${options.nameLabel}已存在。`, "error");
    }
    entries.push({ [nameKey]: name, [valueKey]: numericValue });
    persist(entries);
    replaceWeightedEntryEditor(root, { ...options, entries });
  });
  $$(`[data-weighted-entry-remove="${id}"]`, root).forEach((button) => button.addEventListener("click", () => {
    const index = Number(button.dataset.weightedEntryIndex);
    const entries = currentEntries();
    if (!Number.isInteger(index) || index < 0 || index >= entries.length) return;
    entries.splice(index, 1);
    persist(entries);
    replaceWeightedEntryEditor(root, { ...options, entries });
  }));
}

function primaryKeywordEditor() {
  const entries = weightedEntries("primary_keyword_entries", "primary_keywords", "primary_keyword_weight", "keyword", "weight", 1);
  return {
    id: "primary-keyword-editor",
    entries,
    configKey: "primary_keyword_entries",
    legacyNamesKey: "primary_keywords",
    legacyValueKey: "primary_keyword_weight",
    nameKey: "keyword",
    valueKey: "weight",
    nameLabel: "关键词",
    valueLabel: "权重（0–1）",
    placeholder: "例如 quantum sensing",
    min: 0,
    max: 1,
    step: 0.05,
    addLabel: "添加关键词",
  };
}

async function renderKeywords(token) {
  const root = $("#page-root");
  const context = escapeHtml(String(configValue("research_context", "") || ""));
  const editor = primaryKeywordEditor();
  root.innerHTML = `${pageHeader()}<section class="section-card"><p class="hint-text">研究背景会用于评分和参考文献关键词提取。</p><label class="form-field"><textarea data-field="research_context" data-scope="config" aria-label="研究背景" rows="6" placeholder="描述你的研究问题、方法与关注方向">${context}</textarea></label></section>${divider()}${section("主关键词", `${weightedEntryEditor(editor)}`, { icon: "🏷️", hint: "主关键词参与资格判定与排序；每个关键词可设置 0–1 的独立权重。" })}${divider()}${renderReferenceExtraction()}`;
  bindCommon(root);
  bindWeightedEntryEditor(root, editor);
  const referenceToggle = $('[data-field="enable_reference_extraction"]', root);
  referenceToggle?.addEventListener("change", (event) => {
    $("#reference-extraction-dependent", root).hidden = !event.target.checked;
    $("#reference-extraction-disabled-hint", root).hidden = event.target.checked;
    if (event.target.checked) loadExtractedKeywords(root, token);
  });
  if (booleanValue(configValue("enable_reference_extraction", false), false)) loadExtractedKeywords(root, token);
}

async function loadExtractedKeywords(root, token) {
  const host = $("#extracted-keywords", root);
  if (host) host.innerHTML = '<div class="loading">正在读取已提取关键词…</div>';
  try {
    const result = await api("/api/extracted-keywords");
    if (token !== state.renderToken || !booleanValue(configValue("enable_reference_extraction", false), false)) return;
    if (host) {
      host.innerHTML = extractedKeywordList(result.items || []);
      bindPagers(host);
      applyLocale(host);
    }
  } catch (error) { /* cache visibility should not prevent configuration */ }
}

function extractedKeywordList(items) {
  if (!items.length) return '<p class="empty-state">尚未提取关键词。</p>';
  const count = formatNumber(items.length);
  const countLabel = localeText(`已提取 ${count} 个关键词`, `${count} extracted keywords`);
  return `<div class="extracted-keywords-table"><p class="hint-text">${escapeHtml(countLabel)}</p>${pagedTable("reference-extracted-keywords", [
    { label: "关键词", value: (row) => row.keyword || "—" },
    { label: "权重", value: (row) => {
      const weight = Number(row.weight);
      return Number.isFinite(weight) ? weight.toFixed(2) : "—";
    } },
  ], items, { empty: "尚未提取关键词。" })}</div>`;
}

function renderReferenceExtraction() {
  const enabled = booleanValue(configValue("enable_reference_extraction", false), false);
  const fields = `<div class="reference-extraction-fields"><div class="form-grid two">${field({ label: "最多关键词数量", key: "max_reference_keywords", type: "number", min: 1, max: 50, fallback: 10 })}${field({ label: "相似度阈值", key: "similarity_threshold", type: "range", min: 0, max: 1, step: 0.05, fallback: 0.75 })}</div><div class="form-grid three">${weightField("高重要度", "high", 1, 3)}${weightField("中重要度", "medium", 0.2, 5)}${weightField("低重要度", "low", 0.1, 2)}</div><div id="extracted-keywords"><div class="loading">正在读取已提取关键词…</div></div></div>`;
  const content = `${field({ label: "启用参考文献关键词提取", key: "enable_reference_extraction", type: "checkbox", fallback: false })}<div id="reference-extraction-dependent" ${enabled ? "" : "hidden"}>${fields}</div><p id="reference-extraction-disabled-hint" class="hint-text" ${enabled ? "hidden" : ""}>关闭后不会展示或使用此前提取的关键词；缓存会保留，重新开启后可继续复用。</p>`;
  return section("参考文献 PDF 关键词提取", content, { icon: "📚" });
}

function weightField(label, level, defaultWeight, defaultCount) {
  return `<div class="mini-card"><h3>${escapeHtml(label)}</h3>${field({ label: "权重", key: `ref_weight_${level}`, type: "number", min: 0, max: 5, step: 0.1, fallback: defaultWeight })}${field({ label: "数量", key: `ref_count_${level}`, type: "number", min: 0, max: 20, fallback: defaultCount })}</div>`;
}

function ensureSourceState() {
  if (state.pageData.sources) return state.pageData.sources;
  const config = state.settings.config || {};
  const definitions = Array.isArray(config.extra_source_definitions) ? config.extra_source_definitions : [];
  const builtins = state.settings.builtin_sources || [];
  const builtinCodes = new Set(builtins.map((item) => item.code));
  state.pageData.sources = {
    arxiv: (config.enabled_sources || []).includes("arxiv"),
    domains: Array.isArray(config.domains) ? config.domains : ["quant-ph"],
    extraEnabled: Boolean(config.extra_sources_enabled),
    builtins: builtins.filter((item) => item.code === "prl" ? (config.enabled_sources || []).includes("prl") : definitions.some((definition) => definition.code === item.code)).map((item) => item.code),
    custom: definitions.filter((definition) => !builtinCodes.has(definition.code)),
  };
  return state.pageData.sources;
}

function sourceDefinition(code) {
  return (state.settings.builtin_sources || []).find((item) => item.code === code);
}

function tagMultiSelect({ id, label, selected, choices, addLabel, emptyLabel, help = "" }) {
  const byValue = new Map(choices.map((item) => [String(item.value), item]));
  const selectedSet = new Set(selected.map(String));
  const selectedItems = selected.map((value) => byValue.get(String(value)) || {
    value: String(value), label: String(value),
  });
  const chips = selectedItems.length
    ? selectedItems.map((item) => `<span class="source-tag"><span>${escapeHtml(item.label)}</span><button type="button" data-tag-remove="${escapeAttribute(id)}" data-tag-value="${escapeAttribute(item.value)}" aria-label="移除 ${escapeAttribute(item.label)}">×</button></span>`).join("")
    : `<span class="tag-select-placeholder">${escapeHtml(emptyLabel)}</span>`;
  const available = choices.filter((item) => !selectedSet.has(String(item.value)));
  const picker = available.length
    ? `<details class="tag-select-dropdown" data-tag-selector="${escapeAttribute(id)}"><summary><span>${escapeHtml(addLabel)}</span><i class="dropdown-chevron" aria-hidden="true"></i></summary><div class="tag-select-options" role="listbox" aria-label="${escapeAttribute(label)}">${available.map((item) => `<button type="button" role="option" data-tag-add="${escapeAttribute(id)}" data-tag-value="${escapeAttribute(item.value)}">${escapeHtml(item.label)}</button>`).join("")}</div></details>`
    : `<div class="tag-select-complete">${escapeHtml(localeText("已全部选择", "All selected"))}</div>`;
  return `<div class="form-field tag-select-field"><span>${escapeHtml(label)}${help ? `<span class="field-help">${escapeHtml(help)}</span>` : ""}</span><div class="tag-select-box"><div class="tag-select-chips">${chips}</div>${picker}</div></div>`;
}

function bindTagSelector(root, id, onAdd, onRemove) {
  $$(`[data-tag-add="${id}"]`, root).forEach((button) => button.addEventListener("click", () => onAdd(String(button.dataset.tagValue || ""))));
  $$(`[data-tag-remove="${id}"]`, root).forEach((button) => button.addEventListener("click", () => onRemove(String(button.dataset.tagValue || ""))));
}

function renderSources() {
  const data = ensureSourceState();
  // A report directory split only has meaning when there is a real secondary
  // source.  Keeping the toggle hidden for an empty master group mirrors the
  // Streamlit panel and avoids persisting a misleading no-op setting.
  const openAlexEnabled = booleanValue(envValue("ENABLE_OPENALEX", false), false);
  const hasExtraSource = data.builtins.length > 0 || (openAlexEnabled && data.custom.length > 0);
  const builtinChoices = (state.settings.builtin_sources || []).map((item) => ({
    value: item.code,
    label: `${item.display_name}（${item.code}）`,
  }));
  const customRows = pagedItems(
    "custom-sources",
    data.custom.map((item, index) => `<div class="list-row"><span><strong>${escapeHtml(item.display_name)}</strong> · ${escapeHtml(item.code)} · ${escapeHtml(item.full_name)}${item.issn?.length ? ` · ISSN: ${escapeHtml(item.issn.join(", "))}` : ""}</span><button class="icon-danger" data-remove-custom="${index}" aria-label="移除来源">×</button></div>`),
    "暂无自定义额外来源。",
  );
  const categoryChoices = arxivCategories().map((item) => ({ value: item.code, label: item.label }));
  const customOpenAlex = openAlexEnabled
    ? `<div class="source-custom source-custom-panel"><h3>自定义 OpenAlex 期刊来源</h3><p class="hint-text">通过 ISSN 添加需要跟踪的期刊；保存后会与内置来源一起参与额外数据源任务。</p>${customRows}<div class="form-grid two"><label class="form-field"><span>来源代码</span><input id="custom-code" placeholder="optica_express" /></label><label class="form-field"><span>展示名称</span><input id="custom-display" placeholder="Opt. Express" /></label><label class="form-field"><span>完整名称</span><input id="custom-full" placeholder="Optics Express" /></label><label class="form-field"><span>ISSN（逗号分隔）</span><input id="custom-issn" placeholder="1094-4087" /></label></div><div class="action-row"><button id="custom-add" class="secondary-button">添加来源</button></div></div>`
    : "";
  const sourceReportGrouping = hasExtraSource
    ? `<div class="source-report-group">${field({ label: "按数据源分类整理报告", key: "reports_by_source", type: "checkbox", fallback: true })}</div>`
    : "";
  return `${section("arXiv", `<label class="toggle-field"><span>启动 arXiv 来源</span><input id="source-arxiv" type="checkbox" ${data.arxiv ? "checked" : ""}/><i></i></label>${data.arxiv ? `<p class="hint-text">选择需要扫描的 arXiv 分类。</p>${tagMultiSelect({ id: "source-domains", label: "arXiv 分类", selected: data.domains, choices: categoryChoices, addLabel: localeText("添加分类", "Add category"), emptyLabel: localeText("尚未选择分类", "No categories selected") })}<div class="form-grid two">${field({ label: "请求超时（秒）", key: "arxiv_fetch_timeout_seconds", type: "number", min: 30, max: 1800, fallback: 180 })}${field({ label: "公告回看宽限（天）", key: "arxiv_announcement_lookback_grace_days", type: "number", min: 0, max: 30, fallback: 2 })}</div>` : ""}`)}${divider()}${section("额外数据源", `<label class="toggle-field"><span>启动额外数据源</span><input id="extra-enabled" type="checkbox" ${data.extraEnabled ? "checked" : ""}/><i></i></label>${data.extraEnabled ? `${tagMultiSelect({ id: "extra-builtins", label: "内置来源", selected: data.builtins, choices: builtinChoices, addLabel: localeText("添加内置来源", "Add built-in source"), emptyLabel: localeText("尚未选择内置来源", "No built-in sources selected") })}${sourceReportGrouping}${customOpenAlex}${data.builtins.includes("huggingface_papers") ? `<div class="form-grid two">${field({ label: "Hugging Face 可用性滞后（天）", key: "huggingface_papers_availability_lag_days", type: "number", min: 0, max: 30, fallback: 2 })}${field({ label: "回看宽限（天）", key: "huggingface_papers_lookback_grace_days", type: "number", min: 0, max: 30, fallback: 2 })}${field({ label: "请求超时（秒）", key: "huggingface_papers_request_timeout_seconds", type: "number", min: 5, max: 600, fallback: 30 })}${field({ label: "请求间隔（秒）", key: "huggingface_papers_request_interval_seconds", type: "number", min: 0, max: 60, step: 0.05, fallback: 0.25 })}</div>` : ""}` : '<p class="hint-text">开启后可选择内置来源；启用 OpenAlex 后可在此添加 ISSN 期刊来源。</p>'}`)}`;
}

function validatedCustomJournalSource(raw, data) {
  // Keep the add-button feedback as strict as the shared
  // ``validate_source_definitions`` contract used by Streamlit and by the
  // save endpoint.  This avoids the frustrating path where an operator adds
  // a visibly valid-looking journal and only discovers an ISSN typo after
  // editing unrelated settings and pressing the global Save button.
  const code = String(raw.code || "").trim().toLowerCase();
  if (!/^[a-z][a-z0-9_]{1,63}$/.test(code)) {
    throw new Error("来源代码无效；只能使用小写字母、数字和下划线。");
  }
  if (["arxiv", "prl"].includes(code)) {
    throw new Error(`来源代码与内置核心来源冲突：${code}。`);
  }
  if (data.custom.some((item) => item.code === code) || data.builtins.includes(code)) {
    throw new Error("来源代码已经存在。");
  }
  const text = (value, label) => {
    const normalized = String(value || "").trim();
    if (!normalized) throw new Error(`${label}不能为空。`);
    if (normalized.length > 200 || /[\x00-\x1F\x7F]/.test(normalized)) {
      throw new Error(`${label}包含无效字符或过长。`);
    }
    return normalized;
  };
  const issn = [];
  for (const item of raw.issn || []) {
    const normalized = String(item || "").trim().toUpperCase();
    if (!/^\d{4}-\d{3}[\dX]$/.test(normalized)) {
      throw new Error(`ISSN 无效：${normalized || "（空）"}。`);
    }
    if (!issn.includes(normalized)) issn.push(normalized);
  }
  if (!issn.length) throw new Error("ISSN 至少需要填写一项。");
  return {
    type: "openalex_journal",
    code,
    display_name: text(raw.display_name, "展示名称"),
    full_name: text(raw.full_name, "完整名称"),
    issn,
  };
}

function refreshSources(pageRoot) {
  const host = $("#sources-settings", pageRoot);
  if (!host) return;
  host.innerHTML = renderSources();
  bindCommon(host);
  bindSources(pageRoot);
  applyLocale(host);
  updateConfigurationDirtyIndicator();
}

function bindSources(pageRoot) {
  const root = $("#sources-settings", pageRoot) || pageRoot;
  const data = ensureSourceState();
  const refresh = () => refreshSources(pageRoot);
  $("#source-arxiv", root)?.addEventListener("change", (event) => { data.arxiv = event.target.checked; markConfigurationDirty(); refresh(); });
  $("#extra-enabled", root)?.addEventListener("change", (event) => { data.extraEnabled = event.target.checked; markConfigurationDirty(); refresh(); });
  bindTagSelector(root, "source-domains", (value) => {
    if (!value || data.domains.includes(value)) return;
    data.domains.push(value); markConfigurationDirty(); refresh();
  }, (value) => { data.domains = data.domains.filter((item) => item !== value); markConfigurationDirty(); refresh(); });
  bindTagSelector(root, "extra-builtins", (value) => {
    if (!value || data.builtins.includes(value)) return;
    data.builtins.push(value); markConfigurationDirty(); refresh();
  }, (value) => { data.builtins = data.builtins.filter((item) => item !== value); markConfigurationDirty(); refresh(); });
  $$('[data-remove-custom]', root).forEach((button) => button.addEventListener("click", () => { data.custom.splice(Number(button.dataset.removeCustom), 1); markConfigurationDirty(); refresh(); }));
  $("#custom-add", root)?.addEventListener("click", () => {
    try {
      const candidate = validatedCustomJournalSource({
        code: $("#custom-code").value,
        display_name: $("#custom-display").value,
        full_name: $("#custom-full").value,
        issn: $("#custom-issn").value.split(","),
      }, data);
      data.custom.push(candidate);
      markConfigurationDirty();
      refresh();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function renderDataSources(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="sources-settings">${renderSources()}</div>`;
  bindCommon($("#sources-settings", root));
  bindSources(root);
}

async function renderScoring(token) {
  const strategy = configValue("score_strategy", "core_relevance_v2");
  const authorEditor = authorBonusEditor();
  const root = $("#page-root");
  const strategyFields = (value) => `${strategyDescription(value)}${strategyQualificationNotice(value)}${renderStrategyFields(value)}${legacyFormulaPreview(value)}`;
  root.innerHTML = `${pageHeader()}${section("评价策略", `<p class="hint-text">选择论文如何获得资格，以及通过后如何排序。新配置建议使用核心相关性 V2。</p>${field({ label: "评分策略", key: "score_strategy", type: "select", choices: [{ value: "core_relevance_v2", label: "核心相关性 V2（推荐）" }, { value: "legacy_weighted_keyword_v1", label: "加权关键词 V1（兼容）" }, { value: "learned_preference_v1", label: "偏好学习 V1（个性化）" }], fallback: "core_relevance_v2" })}<div id="scoring-strategy-fields">${strategyFields(strategy)}</div>`, { icon: "🧮" })}${divider()}${renderAuthorBonus(authorEditor)}`;
  bindCommon(root);
  $('[data-field="score_strategy"]', root)?.addEventListener("change", (event) => {
    const host = $("#scoring-strategy-fields", root);
    if (!host) return;
    host.innerHTML = strategyFields(event.target.value);
    bindCommon(host);
    applyLocale(host);
  });
  bindWeightedEntryEditor(root, authorEditor);
  $('[data-field="enable_author_bonus"]', root)?.addEventListener("change", (event) => {
    const region = $("#author-bonus-entry-region", root);
    if (region) region.hidden = !event.target.checked;
  });
  bindLegacyFormulaPreview(root, strategy);
  if (strategy === "learned_preference_v1") {
    try {
      const learned = await api("/api/learned-preferences");
      if (token !== state.renderToken) return;
      const host = $("#learned-library");
      const signedWeight = (row) => {
        const value = Number(row.weight);
        return Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}` : "—";
      };
      if (host) host.innerHTML = `<div class="form-grid two"><div><p class="scroll-list-label">学习关键词</p>${nativeScrollTable([{ label: "关键词", key: "term" }, { label: "权重", value: signedWeight }], learned.keywords || [], { empty: "暂无学习关键词", visibleRows: 10 })}</div><div><p class="scroll-list-label">学习作者</p>${nativeScrollTable([{ label: "作者", key: "term" }, { label: "权重", value: signedWeight }], learned.authors || [], { empty: "暂无学习作者", visibleRows: 10 })}</div></div><p class="hint-text">偏好项来自已保存的喜欢/不喜欢和历史记录；仅展示当前 SQLite 历史库中的结果。</p>`;
    } catch (error) { /* preference library is optional */ }
  }
}

function strategyDescription(strategy) {
  const descriptions = {
    core_relevance_v2: "先由模型为每个关键词给出内容相关度。资格只看主关键词：按主关键词权重计算平均相关度，并且至少一个主关键词达到“强匹配”门槛；两项都满足才通过。通过后，参考关键词和收藏作者只用于排序，不能让无关论文获得资格。",
    legacy_weighted_keyword_v1: "将主关键词和参考关键词的得分按权重累加，再叠加专家作者加分；总分达到“基础分 + 权重系数 × 全部关键词总权重”即通过。参考词和作者加分同时影响资格与排序，适合复现旧报告或临时回退，但可能放大非核心信号。",
    learned_preference_v1: "以加权关键词 V1 为基础，再加入从喜欢/不喜欢和历史通过记录学习到的关键词、作者偏好。每个学习项先限幅再衰减，且已直接配置的关键词不会重复计算；但学习项仍会改变总分，因此也可能改变资格。适合个人化筛选，不适合严格复现基准结果。",
  };
  return `<p class="info-box">${escapeHtml(descriptions[strategy] || descriptions.core_relevance_v2)}</p>`;
}

function strategyQualificationNotice(strategy) {
  if (strategy !== "core_relevance_v2") return "";
  const primary = configValue("primary_keywords", []);
  const hasPrimary = Array.isArray(primary) ? primary.some((item) => String(item || "").trim()) : Boolean(String(primary || "").trim());
  if (hasPrimary) return "";
  return configValue("enable_reference_extraction", false)
    ? '<p class="info-box">尚未配置主要关键词。此次运行会先从参考文献 PDF 提取可用关键词；提取到的关键词将临时作为核心集合。若没有可用 PDF 或提取结果为空，运行会给出明确提示。</p>'
    : '<p class="issue-box">尚未配置主要关键词，且参考文献关键词提取未启用。运行无法开始；请添加主要关键词，或启用参考文献关键词提取并提供参考 PDF。</p>';
}

function renderStrategyFields(strategy) {
  const maxKeywordScore = Math.max(1, Number(configValue("max_score_per_keyword", 10)) || 10);
  if (strategy === "core_relevance_v2") return `<div class="form-grid three">${field({ label: "核心相关性门槛", key: "core_relevance_threshold", type: "number", min: 0, max: maxKeywordScore, step: 0.5, fallback: 6 })}${field({ label: "核心词强匹配门槛", key: "core_keyword_min_score", type: "number", min: 0, max: maxKeywordScore, step: 0.5, fallback: 7 })}${field({ label: "参考词排序系数", key: "reference_ranking_weight", type: "number", min: 0, max: 5, step: 0.05, fallback: 0.25, help: "仅影响已合格论文的排序，不参与是否推荐。" })}</div>${field({ label: "每个关键词最高得分", key: "max_score_per_keyword", type: "number", min: 1, max: 100, fallback: 10 })}`;
  if (strategy === "learned_preference_v1") return `<div class="form-grid two">${field({ label: "学习权重衰减系数", key: "learned_weight_dampening", type: "number", min: 0, max: 1, step: 0.05, fallback: 0.5 })}${field({ label: "学习单项限幅", key: "learned_term_weight_cap", type: "number", min: 0.1, max: 10, step: 0.1, fallback: 2 })}</div>${legacyStrategyFields()}<div id="learned-library"><div class="loading">正在读取偏好词库…</div></div>`;
  return legacyStrategyFields();
}

function legacyStrategyFields() { return `<div class="form-grid three">${field({ label: "基础分", key: "passing_score_base", type: "number", min: 0, max: 100, step: 0.5, fallback: 5 })}${field({ label: "权重系数", key: "passing_score_weight_coefficient", type: "number", min: 0, max: 20, step: 0.5, fallback: 3 })}${field({ label: "每个关键词最高得分", key: "max_score_per_keyword", type: "number", min: 1, max: 100, fallback: 10 })}</div>`; }

function legacyFormulaMarkup(count, totalWeight, base, coefficient) {
  const passing = base + coefficient * totalWeight;
  if (state.language === "en") {
    const noun = count === 1 ? "primary keyword" : "primary keywords";
    return `With ${count} ${noun} at a total weight of ${totalWeight.toFixed(2)}: passing score = ${base} + ${coefficient} × ${totalWeight.toFixed(2)} = <strong>${passing.toFixed(1)}</strong>`;
  }
  return `共 ${count} 个主关键词，总权重 ${totalWeight.toFixed(2)}：通过分数 = ${base} + ${coefficient} × ${totalWeight.toFixed(2)} = <strong>${passing.toFixed(1)}</strong>`;
}

function legacyFormulaPreview(strategy) {
  if (strategy === "core_relevance_v2") return "";
  const primary = weightedEntries("primary_keyword_entries", "primary_keywords", "primary_keyword_weight", "keyword", "weight", 1);
  const count = primary.length;
  const totalWeight = primary.reduce((sum, entry) => sum + (Number.isFinite(Number(entry.weight)) ? Number(entry.weight) : 0), 0);
  const base = Number(configValue("passing_score_base", 5));
  const coefficient = Number(configValue("passing_score_weight_coefficient", 3));
  // Keep the number formatting and interactive preview identical in both
  // languages.  The legacy policy is one of the few UI messages assembled
  // from live form values, so the generic static catalogue cannot translate
  // it after rendering.
  return `<p id="legacy-formula-preview" class="info-box">${legacyFormulaMarkup(count, totalWeight, Number.isFinite(base) ? base : 5, Number.isFinite(coefficient) ? coefficient : 3)}</p>`;
}

function bindLegacyFormulaPreview(root, strategy) {
  if (strategy === "core_relevance_v2") return;
  const preview = $("#legacy-formula-preview", root);
  if (!preview) return;
  const update = () => {
    const primary = weightedEntries("primary_keyword_entries", "primary_keywords", "primary_keyword_weight", "keyword", "weight", 1);
    const count = primary.length;
    const totalWeight = primary.reduce((sum, entry) => sum + (Number.isFinite(Number(entry.weight)) ? Number(entry.weight) : 0), 0);
    const baseField = $('[data-field="passing_score_base"]', root);
    const coefficientField = $('[data-field="passing_score_weight_coefficient"]', root);
    const base = Number(baseField?.value ?? configValue("passing_score_base", 5));
    const coefficient = Number(coefficientField?.value ?? configValue("passing_score_weight_coefficient", 3));
    const normalizedBase = Number.isFinite(base) ? base : 5;
    const normalizedCoefficient = Number.isFinite(coefficient) ? coefficient : 3;
    preview.innerHTML = legacyFormulaMarkup(
      count, totalWeight, normalizedBase, normalizedCoefficient,
    );
  };
  $$('[data-field="passing_score_base"], [data-field="passing_score_weight_coefficient"]', root).forEach((element) => element.addEventListener("input", update));
}

function authorBonusEditor() {
  const entries = weightedEntries("author_bonus_entries", "expert_authors", "author_bonus_points", "author", "points", 5);
  return {
    id: "author-bonus-editor",
    entries,
    configKey: "author_bonus_entries",
    legacyNamesKey: "expert_authors",
    legacyValueKey: "author_bonus_points",
    nameKey: "author",
    valueKey: "points",
    nameLabel: "作者",
    valueLabel: "加分",
    placeholder: "例如 Alice Smith",
    min: 0,
    max: 50,
    step: 0.5,
    addLabel: "添加作者",
  };
}

function renderAuthorBonus(editor = authorBonusEditor()) {
  const enabled = booleanValue(configValue("enable_author_bonus", false), false);
  return section("作者加分", `<p class="hint-text">为指定作者设置独立加分；核心相关性 V2 仅在论文已经合格后用于排序。</p>${field({ label: "启用作者加分", key: "enable_author_bonus", type: "checkbox", fallback: false })}<div id="author-bonus-entry-region" ${enabled ? "" : "hidden"}>${weightedEntryEditor(editor)}</div>`, { icon: "👤" });
}

function llmSection(role, title, icon) {
  const prefix = role === "cheap" ? "CHEAP_LLM" : "SMART_LLM";
  const keyPrefix = role === "cheap" ? "cheap" : "smart";
  const hint = role === "cheap"
    ? "用于快速评分和关键词生成，选择速度快、成本低的模型。"
    : "用于深度分析和内容理解，选择能力强的模型。";
  const presets = [
    ["openai", "OpenAI", "https://api.openai.com/v1"], ["deepseek", "DeepSeek", "https://api.deepseek.com/v1"], ["ollama", "Ollama（本地）", "http://127.0.0.1:11434/v1"], ["zhipu", "智谱 AI", "https://open.bigmodel.cn/api/paas/v4"], ["custom", "自定义", ""],
  ];
  const base = envValue(`${prefix}__BASE_URL`, "https://api.openai.com/v1");
  const detected = presets.find(([id, _label, url]) => url && base.includes(url))?.[0] || "custom";
  return section(title, `<p class="hint-text">${hint}</p><div class="form-grid two"><label class="form-field"><span>服务商预设</span><select id="${keyPrefix}-provider">${presets.map(([id, label]) => `<option value="${id}" ${id === detected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select></label>${field({ label: "Base URL", key: `${prefix}__BASE_URL`, scope: "env", fallback: base, placeholder: "https://api.example.com/v1" })}${field({ label: "API Key", key: `${prefix}__API_KEY`, scope: "env", type: "secret" })}${field({ label: "模型名称", key: `${prefix}__MODEL_NAME`, scope: "env", fallback: role === "cheap" ? "gpt-4o-mini" : "gpt-4o" })}${field({ label: "Temperature", key: `${prefix}__TEMPERATURE`, scope: "env", type: "range", min: 0, max: 2, step: 0.1, fallback: 0.3 })}</div><div class="action-row"><button class="secondary-button" data-test-llm="${role}">测试连接</button><span id="${keyPrefix}-test-result" class="inline-result"></span></div>`, { icon });
}

function mineruSection() {
  const selected = configValue("pdf_parser_mode", "pymupdf") === "mineru";
  if (!selected) return "";
  return `${divider()}${section("MinerU PDF 解析 API", `<p class="hint-text">MinerU 提供高质量云端 PDF 解析，Token 每 3 个月过期。点击测试可查看额度余量和过期时间。</p>${field({ label: "MinerU API Key", key: "MINERU_API_KEY", scope: "env", type: "secret", help: "登录 mineru.net 获取 API Token。" })}<div class="action-row"><button class="secondary-button" data-test-mineru="1">测试连接</button><span class="inline-result">点击测试可查看 Token 过期日期和剩余额度。</span><a href="https://mineru.net/apiManage/apiKey" target="_blank" rel="noreferrer">打开 MinerU API 控制面板 ↗</a><span id="mineru-test-result" class="inline-result"></span></div>`, { icon: "📄", hint: "仅在 PDF 解析器选择 MinerU 时显示。" })}`;
}

function thirdPartySection() {
  // Environment values are text.  Use the shared boolean parser rather than
  // treating every value except the literal string "false" as enabled: older
  // hand-written .env files commonly use 0, no, or off as well.  This matches
  // the Streamlit panel's _env_toggle behaviour and keeps the hidden fields
  // consistent with what the worker will actually call.
  const openAlexEnabled = booleanValue(envValue("ENABLE_OPENALEX"), true);
  const semanticEnabled = booleanValue(envValue("ENABLE_SEMANTIC_SCHOLAR_TLDR"), true);
  const openAlexFields = `<p class="hint-text">免费 API Key 可将每日 API 额度提高到匿名使用的 10 倍，并可查看用量。</p>${field({ label: "OpenAlex API Key", key: "OPENALEX_API_KEY", scope: "env", type: "secret" })}<div class="action-row"><button class="secondary-button" data-test-third="openalex">测试 OpenAlex 连接</button><a href="https://openalex.org/settings/api" target="_blank" rel="noreferrer">打开 OpenAlex API 控制台 ↗</a><span id="openalex-test-result" class="inline-result"></span></div>`;
  const semanticFields = `<p class="hint-text">用于可选 TL;DR 增强。匿名额度由所有用户共享；API Key 初始限额为每秒 1 次，应用会自动按此节奏请求。</p>${field({ label: "Semantic Scholar API Key", key: "SEMANTIC_SCHOLAR_API_KEY", scope: "env", type: "secret" })}<div class="action-row"><button class="secondary-button" data-test-third="semantic_scholar">测试 Semantic Scholar 连接</button><a href="https://www.semanticscholar.org/product/api#api-key-form" target="_blank" rel="noreferrer">打开 Semantic Scholar API 申请页 ↗</a><span id="semantic_scholar-test-result" class="inline-result"></span></div>`;
  const content = [
    '<p class="hint-text">开启来源后才会调用对应服务；关闭时会隐藏其配置，已保存的密钥不会被清除。</p>',
    `<div class="subsection"><h3>📚 OpenAlex</h3><label class="toggle-field"><span>启用 OpenAlex 来源<span class="field-help">关闭后不会请求 OpenAlex。开启后，还需在“数据源 → 额外数据源”中选择期刊来源。</span></span><input id="openalex-enabled" type="checkbox" ${openAlexEnabled ? "checked" : ""}/><i></i></label><div id="openalex-dependent" ${openAlexEnabled ? "" : "hidden"}>${openAlexFields}</div></div>`,
    `<div class="subsection"><h3>🧠 Semantic Scholar</h3><label class="toggle-field"><span>启用 Semantic Scholar TL;DR 增强<span class="field-help">关闭后不会请求 Semantic Scholar，也不会把其 TL;DR 用于后续处理。</span></span><input id="semantic-enabled" type="checkbox" ${semanticEnabled ? "checked" : ""}/><i></i></label><div id="semantic-dependent" ${semanticEnabled ? "" : "hidden"}>${semanticFields}</div></div>`,
  ].join("");
  return `${divider()}${section("第三方 API 密钥", content, { icon: "🔑" })}`;
}

async function renderApi(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${llmSection("cheap", "低成本 LLM", "💸")}${divider()}${llmSection("smart", "高性能 LLM", "🧠")}${mineruSection()}<div id="api-third-party">${thirdPartySection()}</div>`;
  bindCommon(root);
  $$("[data-test-llm]", root).forEach((button) => button.addEventListener("click", () => testLlm(button.dataset.testLlm)));
  $("[data-test-mineru]", root)?.addEventListener("click", () => testConnection("mineru", {
    api_key: state.draft.env.MINERU_API_KEY,
  }, "mineru-test-result"));
  const thirdPartyKeys = {
    openalex: "OPENALEX_API_KEY",
    semantic_scholar: "SEMANTIC_SCHOLAR_API_KEY",
  };
  $$("[data-test-third]", root).forEach((button) => button.addEventListener("click", () => {
    const kind = button.dataset.testThird;
    testConnection(kind, { api_key: state.draft.env[thirdPartyKeys[kind]] }, `${kind}-test-result`);
  }));
  const bindThirdPartyToggle = (selector, key, dependent) => $(selector, root)?.addEventListener("change", (event) => {
    state.draft.env[key] = event.target.checked ? "true" : "false";
    const fields = $(dependent, root);
    if (fields) fields.hidden = !event.target.checked;
    markConfigurationDirty();
  });
  bindThirdPartyToggle("#openalex-enabled", "ENABLE_OPENALEX", "#openalex-dependent");
  bindThirdPartyToggle("#semantic-enabled", "ENABLE_SEMANTIC_SCHOLAR_TLDR", "#semantic-dependent");
  ["cheap", "smart"].forEach((role) => $("#" + role + "-provider", root)?.addEventListener("change", (event) => {
    const urls = { openai: "https://api.openai.com/v1", deepseek: "https://api.deepseek.com/v1", ollama: "http://127.0.0.1:11434/v1", zhipu: "https://open.bigmodel.cn/api/paas/v4", custom: "" };
    const key = `${role === "cheap" ? "CHEAP_LLM" : "SMART_LLM"}__BASE_URL`;
    if (event.target.value !== "custom") {
      state.draft.env[key] = urls[event.target.value];
      const baseUrlInput = $(`[data-field="${key}"][data-scope="env"]`, root);
      if (baseUrlInput) baseUrlInput.value = urls[event.target.value];
      markConfigurationDirty();
    }
  }));
}

async function testLlm(role) {
  const prefix = role === "cheap" ? "CHEAP_LLM" : "SMART_LLM";
  const keyPrefix = role === "cheap" ? "cheap" : "smart";
  await testConnection(`${role}_llm`, {
    api_key: state.draft.env[`${prefix}__API_KEY`], base_url: envValue(`${prefix}__BASE_URL`), model: envValue(`${prefix}__MODEL_NAME`),
  }, `${keyPrefix}-test-result`);
}

async function testConnection(kind, payload, resultId) {
  const result = $("#" + resultId);
  if (result) result.textContent = "正在测试…";
  try {
    const response = await api(`/api/connections/${encodeURIComponent(kind)}`, { method: "POST", body: payload });
    if (result) { result.textContent = response.message || (response.ok ? "连接正常" : "连接失败"); result.className = `inline-result ${response.ok ? "success" : "error"}`; }
  } catch (error) { if (result) { result.textContent = error.message; result.className = "inline-result error"; } else toast(error.message, "error"); }
}

function renderNotifications() {
  const testAction = (channel) => `<div class="action-row notification-test-action"><button type="button" class="secondary-button" data-test-notification="${escapeAttribute(channel)}">发送测试通知</button><span id="notification-test-${escapeAttribute(channel)}" class="inline-result"></span></div>`;
  const channel = (key, label, channelId, fields) => `<details class="channel-card" ${configValue(key, false) ? "open" : ""}><summary>${escapeHtml(label)}</summary>${field({ label: `启用${label}`, key, type: "checkbox", fallback: false })}${fields}${testAction(channelId)}</details>`;
  const emailFields = `<div class="form-grid three">${field({ label: "SMTP 主机", key: "SMTP_HOST", scope: "env" })}${field({ label: "端口", key: "SMTP_PORT", scope: "env", type: "number", min: 1, max: 65535, fallback: 587 })}${field({ label: "使用 TLS", key: "SMTP_USE_TLS", scope: "env", type: "checkbox", fallback: true })}${field({ label: "用户名", key: "SMTP_USER", scope: "env" })}${field({ label: "密码", key: "SMTP_PASSWORD", scope: "env", type: "secret" })}${field({ label: "发件人", key: "SMTP_FROM", scope: "env" })}${field({ label: "收件人（逗号分隔）", key: "SMTP_TO", scope: "env" })}</div>`;
  const mainBody = `${field({ label: "启用通知", key: "notifications_enabled", type: "checkbox", fallback: false })}<div class="form-grid three">${field({ label: "任务成功通知", key: "notify_on_success", type: "checkbox", fallback: true })}${field({ label: "任务失败通知", key: "notify_on_failure", type: "checkbox", fallback: true })}${field({ label: "通知中展示论文数量", key: "notification_top_n", type: "number", min: 1, max: 50, fallback: 5 })}</div>${field({ label: "附加报告文件", key: "notify_attach_reports", type: "checkbox", fallback: false })}`;
  const channels = section("通知渠道", `${channel("notify_email_enabled", "邮件", "email", emailFields)}${channel("notify_wechat_enabled", "企业微信", "wechat_work", field({ label: "Webhook URL", key: "WECHAT_WEBHOOK_URL", scope: "env", type: "secret" }))}${channel("notify_dingtalk_enabled", "钉钉", "dingtalk", `${field({ label: "Webhook URL", key: "DINGTALK_WEBHOOK_URL", scope: "env", type: "secret" })}${field({ label: "签名密钥（可选）", key: "DINGTALK_SECRET", scope: "env", type: "secret" })}`)}${channel("notify_telegram_enabled", "Telegram", "telegram", `<div class="form-grid two">${field({ label: "Bot Token", key: "TELEGRAM_BOT_TOKEN", scope: "env", type: "secret" })}${field({ label: "Chat ID", key: "TELEGRAM_CHAT_ID", scope: "env" })}</div>`)}${channel("notify_slack_enabled", "Slack", "slack", field({ label: "Webhook URL", key: "SLACK_WEBHOOK_URL", scope: "env", type: "secret" }))}${channel("notify_generic_webhook_enabled", "通用 Webhook", "generic", field({ label: "Webhook URL", key: "GENERIC_WEBHOOK_URL", scope: "env", type: "secret" }))}`, { icon: "📣" });
  return `${section("通知设置", mainBody, { icon: "🔔" })}${divider()}${channels}`;
}

function notificationTestPayload(channel) {
  const fields = {
    email: ["SMTP_HOST", "SMTP_PORT", "SMTP_USE_TLS", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO"],
    wechat_work: ["WECHAT_WEBHOOK_URL"],
    dingtalk: ["DINGTALK_WEBHOOK_URL", "DINGTALK_SECRET"],
    telegram: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    slack: ["SLACK_WEBHOOK_URL"],
    generic: ["GENERIC_WEBHOOK_URL"],
  };
  return Object.fromEntries((fields[channel] || []).map((key) => [key, envValue(key)]));
}

async function sendNotificationTest(button) {
  const channel = button.dataset.testNotification;
  const result = $(`#notification-test-${channel}`);
  const originalLabel = button.textContent;
  button.disabled = true;
  if (result) { result.textContent = localeText("正在发送…", "Sending…"); result.className = "inline-result"; }
  try {
    const response = await api(`/api/notifications/${encodeURIComponent(channel)}/test`, {
      method: "POST",
      body: notificationTestPayload(channel),
    });
    if (result) { result.textContent = response.ok ? localeText("测试通知已发送。", "Test notification sent.") : (response.message || ""); result.className = `inline-result ${response.ok ? "success" : "error"}`; }
  } catch (error) {
    if (result) { result.textContent = error.message; result.className = "inline-result error"; }
    else toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

async function renderNotificationsPage(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${renderNotifications()}`;
  bindCommon(root);
  $$('[data-test-notification]', root).forEach((button) => button.addEventListener("click", () => void sendNotificationTest(button)));
}

function proxyNoProxyEditor() {
  const stored = String(configValue("proxy_no_proxy", "localhost,127.0.0.1") || "");
  const display = String(configValue("proxy_no_proxy_ui", stored)).replaceAll(",", "\n");
  return `<label class="form-field"><span>不使用代理的地址（每行一项）<span class="field-help">例如 localhost、127.0.0.1 或内网网段。</span></span><textarea data-field="proxy_no_proxy_ui" data-scope="config" rows="4" placeholder="localhost&#10;127.0.0.1&#10;192.168.1.0/24">${escapeHtml(display)}</textarea></label>`;
}

function renderProxySettings() {
  const enabled = Boolean(configValue("proxy_enabled", false));
  const fields = `<div class="proxy-settings-stack">${field({ label: "代理地址", key: "proxy_url", fallback: "", placeholder: "http://127.0.0.1:7890", help: "支持 HTTP 和 SOCKS5 代理。" })}${proxyNoProxyEditor()}</div>`;
  const dependent = `${fields}${divider()}<h3>🎯 代理范围</h3><div class="proxy-scope-list">${field({ label: "ArXiv API", key: "proxy_arxiv", type: "checkbox", fallback: true, help: "export.arxiv.org" })}${field({ label: "OpenAlex API", key: "proxy_openalex", type: "checkbox", fallback: false, help: "期刊论文数据源" })}${field({ label: "Hugging Face Papers API", key: "proxy_huggingface_papers", type: "checkbox", fallback: false, help: "可选补充论文流" })}${field({ label: "Semantic Scholar API", key: "proxy_semantic_scholar", type: "checkbox", fallback: false, help: "TL;DR 增强" })}${field({ label: "LLM API", key: "proxy_llm_api", type: "checkbox", fallback: false, help: "评分和分析" })}${field({ label: "通知 Webhook", key: "proxy_notifications", type: "checkbox", fallback: false, help: "通知推送" })}${field({ label: "WebDAV 同步", key: "proxy_webdav", type: "checkbox", fallback: true, help: "备份和恢复" })}${field({ label: "检查更新", key: "proxy_update_check", type: "checkbox", fallback: false, help: "GitHub Release" })}</div>`;
  return section("网络代理设置", `${field({ label: "启用网络代理", key: "proxy_enabled", type: "checkbox", fallback: false })}<div id="proxy-dependent" ${enabled ? "" : "hidden"}>${dependent}</div>`, { icon: "🌐" });
}

function keywordTrendDefaultDaysFields() {
  const presets = [7, 14, 30, 90, 365];
  const savedDays = Math.max(1, Math.round(Number(configValue("keyword_trend_default_days", 30)) || 30));
  const savedChoice = presets.includes(savedDays) ? String(savedDays) : "custom";
  const choice = String(configValue("keyword_trend_default_days_ui", savedChoice));
  const options = presets.map((value) => ({ value: String(value), label: `${value} ${localeText("天", "days")}` }));
  options.push({ value: "custom", label: localeText("自定义", "Custom") });
  const select = field({
    label: "默认趋势视图天数",
    key: "keyword_trend_default_days_ui",
    type: "select",
    choices: options,
    fallback: savedChoice,
  });
  return `${select}<div id="keyword-trend-custom-days" ${choice === "custom" ? "" : "hidden"}>${field({
    label: localeText("自定义趋势视图天数", "Custom trend view days"),
    key: "keyword_trend_default_days_custom",
    type: "number",
    min: 1,
    step: 1,
    fallback: savedDays,
  })}</div>`;
}

function renderAdvanced() {
  const mineru = configValue("pdf_parser_mode", "pymupdf") === "mineru";
  const tracker = Boolean(configValue("keyword_tracker_enabled", true));
  const normalizationEnabled = tracker && Boolean(configValue("keyword_normalization_enabled", true));
  const keywordReportEnabled = tracker && Boolean(configValue("keyword_report_enabled", true));
  const downloadLimit = Math.max(1, Math.floor(Number(configValue("pdf_download_max_bytes", 52428800)) / 1048576));
  const pdfParser = section("PDF 解析器", `<div class="advanced-settings-stack">${[
    '<p class="hint-text">选择解析研究论文 PDF 的方式。</p>',
    field({ label: "解析器模式", key: "pdf_parser_mode", type: "select", choices: [{ value: "pymupdf", label: "pymupdf" }, { value: "mineru", label: "mineru" }], fallback: "pymupdf", help: "mineru：云端 API（质量更高）｜pymupdf：本地（无需网络）。" }),
    `<div id="mineru-model-version" ${mineru ? "" : "hidden"}>${field({ label: "MinerU 模型版本", key: "mineru_model_version", type: "select", choices: [{ value: "pipeline", label: "pipeline" }, { value: "vlm", label: "vlm" }], fallback: "pipeline", help: "pipeline：速度快｜vlm：更精准（消耗更多配额）。" })}</div>`,
    field({ label: "PDF 下载大小上限（MB）", key: "pdf_download_max_mb_ui", type: "number", min: 1, max: 1024, fallback: downloadLimit, help: "深度分析下载的单个 PDF 上限；超限或非 PDF 响应会保留论文供后续重试。" }),
  ].join("")}</div>`, { icon: "📄", className: "advanced-settings-card" });
  const concurrency = section("并发设置", `<p class="hint-text">LLM 评分的并行处理，注意 API 速率限制。</p><div class="form-grid two">${field({ label: "启用并发处理", key: "concurrency_enabled", type: "checkbox", fallback: false })}${field({ label: "工作线程数", key: "concurrency_workers", type: "number", min: 1, max: 10, fallback: 3, help: "推荐：3–5，过高可能触发速率限制。" })}</div>`, { icon: "⚡" });
  const requestPool = section("LLM 请求池", `<p class="hint-text">全局限制 LLM 请求速率，避免并发任务触发 API 限流。</p><div class="form-grid three">${field({ label: "启用 LLM 请求池", key: "llm_request_pool_enabled", type: "checkbox", fallback: true })}${field({ label: "每分钟请求数", key: "llm_requests_per_minute", type: "number", min: 1, max: 600, fallback: 30 })}${field({ label: "慢等待日志阈值（秒）", key: "llm_request_pool_log_slow_wait_seconds", type: "number", min: 0, max: 120, step: 0.5, fallback: 5 })}</div>`, { icon: "🚦" });
  const persistence = section("每日研究持久化", `<p class="hint-text">保存论文级评分与分析进度，用于断点续跑和失败恢复。</p>${field({ label: "启用每日深度分析", key: "daily_enable_deep_analysis", type: "checkbox", fallback: true })}${field({ label: "持久化数据库路径", key: "daily_research_db_path", fallback: "data/daily_research/daily_research.db" })}`, { icon: "💾" });
  const featureToggles = section("功能开关", `${field({ label: "Token 用量追踪", key: "token_tracking_enabled", type: "checkbox", fallback: true })}${field({ label: "检查新版本并通知", key: "auto_update_enabled", type: "checkbox", fallback: true, help: "只检查 GitHub Release，不会自动拉取代码、重建镜像或重启容器。发现比当前版本更新的发布版时，经已启用的通知渠道提醒；若所有渠道未送达，后续检查会重试。" })}`, { icon: "📊" });
  const keywordTracker = section("关键词趋势追踪", `<div class="advanced-settings-stack">${field({ label: "启用关键词追踪", key: "keyword_tracker_enabled", type: "checkbox", fallback: true })}<div id="keyword-tracker-dependent" class="advanced-settings-stack" ${tracker ? "" : "hidden"}>${field({ label: "AI 归一化", key: "keyword_normalization_enabled", type: "checkbox", fallback: true })}<div id="keyword-normalization-dependent" class="trend-dependent-fields advanced-settings-stack" ${normalizationEnabled ? "" : "hidden"}><div class="form-grid two">${field({ label: "归一化批次大小", key: "keyword_normalization_batch_size", type: "number", min: 5, max: 100, fallback: 25 })}${field({ label: "归一化使用的 LLM", key: "keyword_normalization_llm_role", type: "select", choices: [{ value: "cheap", label: "低成本 LLM" }, { value: "smart", label: "高性能 LLM" }], fallback: "cheap", help: "该选择会用于每日关键词标准化，并同步记录到 LLM 健康统计。" })}</div></div>${keywordTrendDefaultDaysFields()}<div class="form-grid two">${field({ label: "柱状图 Top-N", key: "keyword_chart_top_n", type: "number", min: 5, max: 50, fallback: 15 })}${field({ label: "趋势图 Top-N", key: "keyword_trend_top_n", type: "number", min: 3, max: 20, fallback: 5 })}</div>${field({ label: "启用趋势报告", key: "keyword_report_enabled", type: "checkbox", fallback: true })}<div id="keyword-report-frequency" ${keywordReportEnabled ? "" : "hidden"}>${field({ label: "报告频率", key: "keyword_report_frequency", type: "select", choices: ["daily", "weekly", "monthly", "always"], fallback: "weekly" })}</div></div></div>`, { icon: "🧩", className: "advanced-settings-card" });
  const retryAndLogs = section("重试与日志", `<div class="form-grid three">${field({ label: "最大重试次数", key: "retry_max_attempts", type: "number", min: 1, max: 10, fallback: 3 })}${field({ label: "最短等待（秒）", key: "retry_min_wait", type: "number", min: 1, max: 60, fallback: 2 })}${field({ label: "最长等待（秒）", key: "retry_max_wait", type: "number", min: 5, max: 300, fallback: 30 })}</div>${field({ label: "运行锁超龄告警阈值（小时）", key: "run_lock_max_age_hours", type: "number", min: 1, max: 168, fallback: 12, help: "同一任务超过该时长时，后续同类任务会告警并跳过；不会按 PID 自动终止进程。" })}<div class="form-grid two">${field({ label: "日志轮转方式", key: "log_rotation_type", type: "select", choices: [{ value: "time", label: "time" }, { value: "size", label: "size" }], fallback: "time" })}${field({ label: "日志保留天数", key: "log_keep_days", type: "number", min: 1, max: 365, fallback: 30 })}</div>`, { icon: "♻️" });
  return [pdfParser, concurrency, requestPool, persistence, featureToggles, keywordTracker, retryAndLogs, renderProxySettings()].join(divider());
}

function bindAdvancedInteractions(root) {
  const toggle = (selector, target, visible) => {
    $(selector, root)?.addEventListener("change", (event) => {
      const host = $(target, root);
      if (host) host.hidden = !visible(event);
    });
  };
  toggle('[data-field="pdf_parser_mode"]', "#mineru-model-version", (event) => event.target.value === "mineru");
  toggle('[data-field="proxy_enabled"]', "#proxy-dependent", (event) => event.target.checked);
  toggle('[data-field="keyword_tracker_enabled"]', "#keyword-tracker-dependent", (event) => event.target.checked);
  toggle('[data-field="keyword_normalization_enabled"]', "#keyword-normalization-dependent", (event) => event.target.checked);
  toggle('[data-field="keyword_report_enabled"]', "#keyword-report-frequency", (event) => event.target.checked);
  toggle('[data-field="keyword_trend_default_days_ui"]', "#keyword-trend-custom-days", (event) => event.target.value === "custom");
}

async function renderAdvancedPage(_token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${renderAdvanced()}`;
  bindCommon(root);
  bindAdvancedInteractions(root);
}

function webdavScheduleTime(cron) {
  const parts = String(cron || "").trim().split(/\s+/);
  const minute = Number(parts[0]);
  const hour = Number(parts[1]);
  if (!Number.isInteger(hour) || hour < 0 || hour > 23 || !Number.isInteger(minute) || minute < 0 || minute > 59) return "23:00";
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function webdavOperationMessage(operation, payload) {
  if (operation === "test") return payload.ok ? "连接与目录权限正常。" : "连接或目录权限验证失败。";
  const summary = payload.result || {};
  const success = Number(summary.success || 0);
  const total = Number(summary.total || 0);
  const failed = Object.entries(summary.results || {}).filter(([, ok]) => !ok).map(([path]) => path);
  const verb = operation === "upload" ? "上传" : "下载";
  const detail = failed.length ? `；失败项目：${failed.join("、")}` : "";
  return `${verb}完成：${success}/${total}${summary.elapsed_seconds !== undefined ? `，耗时 ${summary.elapsed_seconds} 秒` : ""}${detail}`;
}

function localBackupMessage(result) {
  if (!result?.created) return `未创建备份：${result?.reason || "未知原因"}`;
  if (result.upload_error) return `本地备份已创建；WebDAV 上传失败：${result.upload_error}`;
  if (result.uploaded) return `本地备份已创建并已增量上传：${result.name}`;
  if (result.skipped_reason === "content_unchanged") return `本地备份已创建；WebDAV 数据未变化，已跳过上传：${result.name}`;
  if (result.webdav_skipped === "credentials_incomplete") return `本地备份已创建；WebDAV 凭据尚未配置完整：${result.name}`;
  return `已创建本地备份：${result.name}`;
}

function restoreBackupMessage(result) {
  const source = String(result?.source_member || "备份文件");
  const archived = String(result?.archived_previous || "").trim();
  if (!archived) return `已恢复：${source}。当前没有需要归档的旧数据库。`;
  // The archive has already been placed in the local backup directory. Show
  // its filename rather than leaking an absolute host/container path.
  const name = archived.split(/[\\/]/).filter(Boolean).pop() || archived;
  return `已恢复：${source}。此前数据库已归档为：${name}`;
}

function webdavOperationDraft() {
  const configKeys = [
    "webdav_enabled", "webdav_remote_path", "webdav_sync_configs",
    "webdav_sync_history", "webdav_sync_keywords", "webdav_sync_reports",
    "proxy_enabled", "proxy_webdav", "proxy_url",
  ];
  const config = Object.fromEntries(configKeys.map((key) => [key, configValue(key)]));
  const env = {
    WEBDAV_URL: envValue("WEBDAV_URL"),
    WEBDAV_USERNAME: envValue("WEBDAV_USERNAME"),
  };
  // A blank password input deliberately means "keep the saved password".
  // Only include an entered replacement in this non-persistent operation.
  if (state.draft.env.WEBDAV_PASSWORD) env.WEBDAV_PASSWORD = state.draft.env.WEBDAV_PASSWORD;
  return { config, env };
}

function localBackupDraft() {
  const draft = webdavOperationDraft();
  draft.config.backup_local_retention_days = configValue("backup_local_retention_days", 7);
  draft.config.backup_local_same_day_max_count = configValue("backup_local_same_day_max_count", 0);
  return draft;
}

function webdavSettings() {
  const enabled = Boolean(configValue("webdav_enabled", false));
  const backupEnabled = Boolean(configValue("backup_enabled", true));
  const database = configValue("daily_research_db_path", "data/daily_research/daily_research.db").split("/").pop();
  const historyLabel = localeText(`历史数据（${database}）`, `Historical data (${database})`);
  const scheduled = configValue("webdav_sync_mode", "after_report") === "scheduled";
  const webdavDependent = `<div class="form-grid two">${field({ label: "WebDAV URL", key: "WEBDAV_URL", scope: "env", placeholder: "https://dav.example.com/dav/" })}${field({ label: "用户名", key: "WEBDAV_USERNAME", scope: "env" })}${field({ label: "密码", key: "WEBDAV_PASSWORD", scope: "env", type: "secret" })}</div><div class="action-row"><button class="secondary-button" data-webdav="test">测试连接</button><button class="secondary-button" data-webdav="upload">上传</button><button class="secondary-button" data-webdav="download">下载</button><span id="webdav-result" class="inline-result"></span></div><h3>⚙️ 同步设置</h3><div class="form-grid two">${field({ label: "远程目录", key: "webdav_remote_path", fallback: "/arxiv-daily-researcher/" })}${field({ label: "同步时机", key: "webdav_sync_mode", type: "select", choices: [{ value: "manual", label: "手动" }, { value: "scheduled", label: "定时" }, { value: "after_report", label: "报告完成后" }], fallback: "after_report" })}<label id="webdav-scheduled-time-field" class="form-field" ${scheduled ? "" : "hidden"}><span>定时同步时间<span class="field-help">每天在此时间执行同步。</span></span><input id="webdav-scheduled-time" type="time" value="${escapeAttribute(webdavScheduleTime(configValue("webdav_cron_schedule", "0 23 * * *")))}" /></label></div><h3>📂 同步范围</h3><div class="form-grid two">${field({ label: "配置文件", key: "webdav_sync_configs", type: "checkbox", fallback: true })}${field({ label: historyLabel, key: "webdav_sync_history", type: "checkbox", fallback: true })}${field({ label: "关键词数据", key: "webdav_sync_keywords", type: "checkbox", fallback: true })}${field({ label: "报告文件", key: "webdav_sync_reports", type: "checkbox", fallback: false })}</div>`;
  const localBackupSettings = `<div id="local-backup-settings" class="form-grid two" ${backupEnabled ? "" : "hidden"}>${field({ label: "本地保存天数（0 永久保存）", key: "backup_local_retention_days", type: "number", min: 0, fallback: 7 })}${field({ label: "当天最多数量（0 不限）", key: "backup_local_same_day_max_count", type: "number", min: 0, fallback: 0 })}</div>`;
  return `${section("配置导出", `<p class="hint-text">导出当前 config.json 与 .env。导出文件含凭据，请妥善保存。</p><button id="config-export" class="secondary-button">导出配置</button>`, { icon: "📦" })}${divider()}${section("WebDAV", `<p class="hint-text">按需同步配置、SQLite 历史、关键词和报告文件。</p>${field({ label: "启用 WebDAV 同步", key: "webdav_enabled", type: "checkbox", fallback: false })}<div id="webdav-dependent" ${enabled ? "" : "hidden"}>${webdavDependent}</div><p id="webdav-disabled-hint" class="hint-text" ${enabled ? "hidden" : ""}>启用后可展开连接凭据、同步设置和同步范围。</p>`, { icon: "☁️" })}${divider()}${section("本地备份", `<p class="hint-text">本地备份按保留策略自动整理；启用 WebDAV 后会在本地快照成功后增量镜像到远端。</p>${field({ label: "启用自动备份", key: "backup_enabled", type: "checkbox", fallback: true })}${localBackupSettings}<div class="action-row"><button id="backup-create" class="primary-button">生成本地备份</button><button id="backup-export" class="secondary-button">导出备份</button></div><div class="action-row"><label class="file-button">导入备份<input id="backup-file" type="file" accept=".zip,.gz,.db" hidden /></label><button id="backup-restore" class="secondary-button" disabled>上传并恢复</button><span id="backup-result" class="inline-result"></span></div><div id="backup-list"><div class="loading">正在读取备份列表…</div></div>`, { icon: "🗄️" })}`;
}

async function renderBackupSync(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}${webdavSettings()}`;
  bindCommon(root);
  $('[data-field="webdav_enabled"]', root)?.addEventListener("change", (event) => {
    const enabled = event.target.checked;
    const dependent = $("#webdav-dependent", root);
    const hint = $("#webdav-disabled-hint", root);
    if (dependent) dependent.hidden = !enabled;
    if (hint) hint.hidden = enabled;
  });
  $('[data-field="webdav_sync_mode"]', root)?.addEventListener("change", (event) => {
    const timeField = $("#webdav-scheduled-time-field", root);
    if (timeField) timeField.hidden = event.target.value !== "scheduled";
  });
  $('[data-field="backup_enabled"]', root)?.addEventListener("change", (event) => {
    const settings = $("#local-backup-settings", root);
    if (settings) settings.hidden = !event.target.checked;
  });
  $("#webdav-scheduled-time", root)?.addEventListener("change", (event) => {
    const [hour, minute] = String(event.target.value || "").split(":").map(Number);
    if (Number.isInteger(hour) && hour >= 0 && hour <= 23 && Number.isInteger(minute) && minute >= 0 && minute <= 59) {
      state.draft.config.webdav_cron_schedule = `${minute} ${hour} * * *`;
      markConfigurationDirty();
    }
  });
  $("#config-export", root).addEventListener("click", () => { window.location.assign("/api/configuration/export"); });
  $$("[data-webdav]", root).forEach((button) => button.addEventListener("click", async () => {
    try { const result = await api("/api/webdav", { method: "POST", body: { operation: button.dataset.webdav, ...webdavOperationDraft() } }); $("#webdav-result").textContent = webdavOperationMessage(button.dataset.webdav, result); $("#webdav-result").className = `inline-result ${result.ok ? "success" : "error"}`; } catch (error) { toast(error.message, "error"); }
  }));
  $("#backup-create", root).addEventListener("click", async () => { try { const result = await api("/api/backups/create", { method: "POST", body: localBackupDraft() }); toast(localBackupMessage(result), result.created && !result.upload_error ? "success" : "error"); await refreshBackupList(root); } catch (error) { toast(error.message, "error"); } });
  $("#backup-export", root).addEventListener("click", () => { window.location.assign("/api/backups/export"); });
  $("#backup-file", root).addEventListener("change", (event) => { $("#backup-restore").disabled = !event.target.files?.[0]; });
  $("#backup-restore", root).addEventListener("click", async () => {
    const file = $("#backup-file").files?.[0]; if (!file) return;
    if (!window.confirm("确认恢复该 SQLite 备份？当前数据库会被归档后替换。")) return;
    try { const result = await api("/api/backups/restore", { method: "POST", body: file, headers: { "X-File-Name": file.name } }); toast(restoreBackupMessage(result), "success"); await refreshBackupList(root); } catch (error) { $("#backup-result").textContent = error.message; $("#backup-result").className = "inline-result error"; }
  });
  await refreshBackupList(root, token);
}

async function refreshBackupList(root = $("#page-root"), token = state.renderToken) {
  try {
    const backups = await api("/api/backups");
    if (token !== state.renderToken || state.page !== "backup_sync") return;
    const host = $("#backup-list", root);
    if (!host) return;
    host.innerHTML = pagedTable("backups", [{ label: "文件名", key: "name" }, { label: "大小", value: (row) => `${Math.round(Number(row.size_bytes) / 1024)} KB` }, { label: "时间", value: (row) => formatTime(row.modified_at) }], backups.items || [], { empty: "暂无本地备份" });
    bindPagers(host);
    applyLocale(host);
  } catch (error) {
    const host = $("#backup-list", root);
    if (host) host.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
  }
}

function importSummary(summary) {
  if (!summary) return '<p class="empty-state">尚无旧历史导入记录。</p>';
  const metric = (label, key, fallback = 0) => ({ label, value: formatNumber(summary[key] ?? fallback), help: "" });
  const missingParts = [
    [localeText("报告卡", "report cards"), "missing_cards"], ["TL;DR", "missing_tldr"],
    [localeText("翻译", "translations"), "missing_translation"], [localeText("分析", "analyses"), "missing_analysis"],
  ].map(([label, key]) => [label, Number(summary[key] || 0)]).filter(([, value]) => value > 0);
  const sources = summary.source_breakdown && typeof summary.source_breakdown === "object"
    ? Object.entries(summary.source_breakdown).filter(([name, value]) => name && Number.isFinite(Number(value))).map(([name, value]) => `${escapeHtml(name)}${localeText("：", ": ")}${formatNumber(value)}`).join(" · ")
    : "";
  const missing = missingParts.length
    ? `<p class="issue-box">${localeText("待补全：", "Missing data: ")}${missingParts.map(([label, value]) => `${escapeHtml(label)} ${formatNumber(value)}`).join(" · ")}</p>`
    : "";
  return `<p class="hint-text">${localeText("完成时间：", "Completed: ")}${escapeHtml(formatTime(summary.finished_at))} · ${summary.full_repair_enabled ? localeText("完整导入流程", "Full import workflow") : localeText("仅导入已有 HTML 论文", "Imported existing HTML papers only")}</p>${metrics([metric("扫描报告", "reports_scanned"), metric("导入论文卡", "cards_selected", summary.cards_found), metric("写入投递记录", "delivered_ledger_rows"), metric("补充任务", "backlog_queued")])}${missing}${sources ? `<p class="hint-text">${localeText("来源分布：", "Source breakdown: ")}${sources}</p>` : ""}`;
}

function historyTaskStateLabel(value) {
  return ({
    queued: "等待中", starting: "正在接手", running: "运行中", succeeded: "已完成",
    failed: "失败", rejected: "已拒绝", interrupted: "已中断", skipped_busy: "因互斥跳过",
  })[String(value || "")] || String(value || "—");
}

function historyTasks(data) {
  return Array.isArray(data?.tasks) ? data.tasks : [];
}

function historyIsLive(data) {
  return Boolean(data?.status?.is_active) || historyTasks(data).some((task) => ["queued", "starting", "running"].includes(task.state));
}

function historyFullRepairHint(enabled) {
  return enabled
    ? "导入后补全缺失数据并扫描遗漏。"
    : "仅导入 HTML 中已有的论文。";
}

const HISTORY_SCHEDULE_FIELDS = [
  "history_maintenance_max_papers_per_run",
  "history_maintenance_run_mode",
  "history_maintenance_time_window_start",
  "history_maintenance_time_window_end",
];

function historyScheduleHasUnsavedChanges() {
  return HISTORY_SCHEDULE_FIELDS.some((key) => Object.prototype.hasOwnProperty.call(state.draft.config, key));
}

function historyQueueMessage() {
  return localeText("历史维护任务已加入队列。", "History maintenance task queued.");
}

function requireSavedHistorySchedule() {
  if (!historyScheduleHasUnsavedChanges()) return true;
  toast(localeText("请先保存历史维护设置。", "Save history maintenance settings first."), "error");
  return false;
}

function historyActions(data) {
  const pendingModes = new Set(historyTasks(data)
    .filter((task) => ["queued", "starting", "running"].includes(task.state))
    .map((task) => task.mode));
  const fullRepair = Boolean(configValue("legacy_import_full_repair_enabled", false));
  const runMode = String(configValue("history_maintenance_run_mode", "idle")) === "time_window" ? "time_window" : "idle";
  const scheduleFields = `<div class="history-schedule-settings">${field({ label: "历史维护每次最多处理论文数（0 不限）", key: "history_maintenance_max_papers_per_run", type: "number", min: 0, max: 100000, step: 1, fallback: 200 })}${field({ label: "历史维护运行方式", key: "history_maintenance_run_mode", type: "select", fallback: "idle", choices: [{ value: "idle", label: "闲时运行" }, { value: "time_window", label: "指定时间段运行" }] })}<div id="history-time-window" class="form-grid two history-time-window" ${runMode === "time_window" ? "" : "hidden"}>${field({ label: "指定时段开始", key: "history_maintenance_time_window_start", type: "time", fallback: "00:00" })}${field({ label: "指定时段结束", key: "history_maintenance_time_window_end", type: "time", fallback: "06:00" })}</div></div>`;
  return `${field({ label: "启用完整补全流程", key: "legacy_import_full_repair_enabled", type: "checkbox", fallback: false })}<p id="history-full-repair-hint" class="hint-text">${historyFullRepairHint(fullRepair)}</p><div class="action-row history-maintenance-actions"><button id="history-import" class="primary-button" ${pendingModes.has("legacy_import") ? "disabled" : ""}>读取旧历史</button><button id="history-repair" class="secondary-button compact-button" ${pendingModes.has("history_data_repair") ? "disabled" : ""}>补全历史数据</button><button id="history-omission" class="secondary-button compact-button" ${pendingModes.has("history_omission_scan") ? "disabled" : ""}>扫描历史遗漏</button></div><h3>运行设置</h3>${scheduleFields}`;
}

function historyStatusPanel(data) {
  const status = data?.status || {};
  const tasks = historyTasks(data);
  const latestResult = historyIsLive(data)
    ? '<p class="hint-text">任务完成后显示最近导入结果。</p>'
    : importSummary(data?.last_import);
  return `${statusCard(status, { kind: "history", refresh: false, allowStop: false })}${divider()}<h3>最近一次导入结果</h3>${latestResult}${divider()}<h3>未完成任务</h3>${pagedTable("history-tasks", [{ label: "任务", value: (row) => row.label || row.mode || "—" }, { label: "状态", value: (row) => historyTaskStateLabel(row.state) }, { label: "进度", value: (row) => row.progress || "—" }, { label: "开始时间", value: (row) => formatTime(row.started_at || row.created_at) }, { label: "完成时间", value: (row) => formatTime(row.completed_at) }, { label: "问题摘要", value: (row) => row.issue || "—" }, { label: "操作", html: (row) => row.retryable ? `<button class="secondary-button compact-button" data-history-retry="${escapeAttribute(row.request_id)}">重试</button>` : "—" }], tasks, { empty: "没有未完成的历史维护任务。" })}`;
}

function bindHistoryLaunchers(root) {
  $("#history-import", root)?.addEventListener("click", async () => {
    try {
      if (!requireSavedHistorySchedule()) return;
      const selectedFullRepair = Boolean(configValue("legacy_import_full_repair_enabled", false));
      // Like the Streamlit control, this submits the live switch as a task
      // argument.  It must not persist unrelated configuration edits merely
      // because the operator starts an idle-time import.
      await api("/api/tasks/legacy_import", { method: "POST", body: { args: { full_repair: selectedFullRepair } } });
      toast(historyQueueMessage(), "success");
      await refreshHistoryStatus(root);
    } catch (error) { toast(error.message, "error"); }
  });
  $("#history-repair", root)?.addEventListener("click", async () => {
    try { if (!requireSavedHistorySchedule()) return; await api("/api/tasks/history_data_repair", { method: "POST", body: { args: {} } }); toast(historyQueueMessage(), "success"); await refreshHistoryStatus(root); } catch (error) { toast(error.message, "error"); }
  });
  $("#history-omission", root)?.addEventListener("click", async () => {
    try { if (!requireSavedHistorySchedule()) return; await api("/api/tasks/history_omission_scan", { method: "POST", body: { args: {} } }); toast(historyQueueMessage(), "success"); await refreshHistoryStatus(root); } catch (error) { toast(error.message, "error"); }
  });
}

function bindHistoryRetries(root) {
  $$('[data-history-retry]', root).forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/api/history/${encodeURIComponent(button.dataset.historyRetry)}/retry`, { method: "POST", body: {} }); toast(localeText("历史维护任务已重新加入队列。", "History maintenance task queued again.")); await refreshHistoryStatus(root); } catch (error) { toast(error.message, "error"); }
  }));
}

function updateHistoryActionAvailability(root, data) {
  const pendingModes = new Set(historyTasks(data)
    .filter((task) => ["queued", "starting", "running"].includes(task.state))
    .map((task) => task.mode));
  const availability = [
    ["#history-import", "legacy_import"],
    ["#history-repair", "history_data_repair"],
    ["#history-omission", "history_omission_scan"],
  ];
  availability.forEach(([selector, mode]) => {
    const button = $(selector, root);
    if (button) button.disabled = pendingModes.has(mode);
  });
}

function updateHistoryStatus(root, data) {
  const status = $("#history-status-content", root);
  if (!status) return false;
  // Unlike the live status table, the import controls can contain an
  // unsaved checkbox value.  Keep their DOM nodes and only update their
  // disabled state so an automatic refresh never discards an operator edit.
  updateHistoryActionAvailability(root, data);
  status.innerHTML = historyStatusPanel(data);
  bindPagers(status);
  bindHistoryRetries(status);
  applyLocale(status);
  return true;
}

async function refreshHistoryStatus() {
  if (state.page !== "history_tasks") return;
  const root = $("#page-root");
  try {
    const data = await api("/api/history");
    if (state.page !== "history_tasks" || !updateHistoryStatus(root, data)) return;
    if (historyIsLive(data) && state.pageData.historyAutoRefresh !== false) {
      scheduleRefresh("history", refreshHistoryStatus, 5000);
    }
  } catch (error) {
    toast(`历史状态刷新失败：${error.message}`, "error");
  }
}

async function renderHistory(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div class="loading">正在读取历史维护状态…</div>`;
  const data = await api("/api/history");
  if (token !== state.renderToken) return;
  const autoRefresh = state.pageData.historyAutoRefresh !== false;
  root.innerHTML = `${pageHeader()}${section("旧版本历史导入", `<div id="history-actions">${historyActions(data)}</div>`, { icon: "📜" })}${divider()}${section("状态面板", `<label class="toggle-field refresh-row"><span><strong>状态自动刷新</strong><small>运行中每 5 秒更新。</small></span><input id="history-auto-refresh" type="checkbox" ${autoRefresh ? "checked" : ""}/><i></i></label><div id="history-status-content" class="task-status-region">${historyStatusPanel(data)}</div>`, { icon: "📊" })}`;
  bindCommon(root);
  bindHistoryLaunchers(root);
  bindHistoryRetries(root);
  $('[data-field="legacy_import_full_repair_enabled"]', root)?.addEventListener("change", (event) => {
    const hint = $("#history-full-repair-hint", root);
    if (!hint) return;
    hint.textContent = historyFullRepairHint(event.target.checked);
    applyLocale(hint);
  });
  $('[data-field="history_maintenance_run_mode"]', root)?.addEventListener("change", (event) => {
    const timeWindow = $("#history-time-window", root);
    if (!timeWindow) return;
    timeWindow.hidden = event.target.value !== "time_window";
    applyLocale(timeWindow);
  });
  $("#history-auto-refresh", root)?.addEventListener("change", (event) => {
    state.pageData.historyAutoRefresh = event.target.checked;
    if (!event.target.checked) window.clearTimeout(state.timers.get("history"));
    else if (historyIsLive(data)) scheduleRefresh("history", refreshHistoryStatus, 5000);
  });
  if (historyIsLive(data) && autoRefresh) scheduleRefresh("history", refreshHistoryStatus, 5000);
}

function healthStatusLabel(status) {
  return String(status || "").toLowerCase() === "succeeded" ? "成功" : "失败";
}

function diagnosticRunStatusLabel(status) {
  return ({ completed: "已完成", failed: "失败", running: "运行中" })[String(status || "").toLowerCase()] || "未知";
}

function diagnosticTaskKindLabel(kind) {
  return ({
    daily: "每日研究", daily_research: "每日研究",
    backfill: "过去日报", backfill_run: "过去日报",
    history_import: "旧历史导入", legacy_history_import: "旧历史导入",
    history_data_repair: "历史数据补全", history_omission_scan: "历史遗漏扫描",
    supplement: "补充报告", trend_research: "趋势研究",
  })[String(kind || "").toLowerCase()] || String(kind || "—");
}

function healthFailureDetail(row) {
  if (!row?.last_error) return "—";
  const timestamp = formatTime(row.last_error_at);
  return timestamp === "—" ? String(row.last_error) : `${timestamp} · ${row.last_error}`;
}

function healthSuccessRate(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "—";
}

function healthTable(key, kind, rows) {
  if (kind === "llm") return pagedTable(key, [
    { label: "模型", key: "model" },
    { label: "用途", value: (row) => (row.roles || []).map((role) => ({ cheap: "低成本 LLM", smart: "高性能 LLM" })[String(role).toLowerCase()] || role).join(" / ") || "—" },
    { label: "最近调用", value: (row) => formatTime(row.last_event_at) },
    { label: "最新状态", value: (row) => healthStatusLabel(row.last_status) },
    { label: "成功/调用", value: (row) => `${formatNumber(row.succeeded_in_window)}/${formatNumber(row.events_in_window)}` },
    { label: "成功率", value: (row) => healthSuccessRate(row.success_rate) },
    { label: "最近失败信息（已脱敏）", value: healthFailureDetail },
  ], rows, { empty: "所选范围内没有 LLM 调用记录。" });
  return pagedTable(key, [
    { label: "来源", value: (row) => row.name || row.source },
    { label: "最近请求", value: (row) => formatTime(row.last_event_at) },
    { label: "最新状态", value: (row) => healthStatusLabel(row.last_status) },
    { label: "最近任务", value: (row) => diagnosticTaskKindLabel(row.last_task_kind) },
    { label: "成功/请求", value: (row) => `${formatNumber(row.succeeded)}/${formatNumber(row.events)}` },
    { label: "成功率", value: (row) => healthSuccessRate(row.success_rate) },
    { label: "最近新增", value: (row) => Number.isInteger(row.last_new_candidates) ? formatNumber(row.last_new_candidates) : "—" },
    { label: "最近失败信息（已脱敏）", value: healthFailureDetail },
  ], rows, { empty: "所选范围内没有数据源请求记录。" });
}

function diagnosticsRangeControl(id, value) {
  return `<label class="form-field narrow-field"><span>查看范围</span><select id="${escapeAttribute(id)}"><option value="3" ${value === "3" ? "selected" : ""}>近 3 天</option><option value="7" ${value === "7" ? "selected" : ""}>近 7 天</option><option value="14" ${value === "14" ? "selected" : ""}>近 14 天</option><option value="30" ${value === "30" ? "selected" : ""}>近 30 天</option><option value="all" ${value === "all" ? "selected" : ""}>全部</option></select></label>`;
}

async function refreshDiagnosticsContent(root = $("#page-root"), token = state.renderToken) {
  const host = $("#diagnostics-content", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("diagnostics-content");
  const ranges = { runs: "7", llm: "7", sources: "7", ...(state.pageData.diagnosticsRanges || {}) };
  host.innerHTML = '<div class="loading">正在读取运行诊断…</div>';
  // All three panels are backed by the same aggregate.  When their ranges
  // match (the common default), share one request instead of making three
  // identical SQLite reads and serialising them behind the server worker.
  const requests = new Map();
  const loadRange = (days) => {
    if (!requests.has(days)) requests.set(days, api(`/api/diagnostics?days=${days}`));
    return requests.get(days);
  };
  const [runData, llmData, sourceData] = await Promise.all([
    loadRange(ranges.runs),
    loadRange(ranges.llm),
    loadRange(ranges.sources),
  ]);
  if (token !== state.renderToken || state.page !== "diagnostics" || !isCurrentLocalRequest("diagnostics-content", requestVersion)) return;
  host.innerHTML = `${section("运行诊断", `${diagnosticsRangeControl("diagnostics-range", ranges.runs)}<p class="hint-text">显示所选时间范围内的每日研究与过去日报；旧历史维护任务请在“系统 → 历史维护”查看。</p>${pagedTable("operational-runs", [{ label: "任务", value: (row) => diagnosticTaskKindLabel(row.run_kind) }, { label: "状态", value: (row) => diagnosticRunStatusLabel(row.status) }, { label: "开始时间", value: (row) => formatTime(row.started_at) }, { label: "完成时间", value: (row) => formatTime(row.completed_at) }, { label: "论文数", key: "total_papers" }, { label: "问题摘要", value: (row) => row.error_summary || "—" }], runData.runs || [], { empty: "尚无每日研究或过去日报运行记录。" })}`, { icon: "🩺" })}${divider()}${section("LLM 健康", `${diagnosticsRangeControl("llm-range", ranges.llm)}<p class="hint-text">汇总所有真实任务（含历史维护）的 LLM 调用；查看不会发送探针请求，也不会额外消耗 Token。</p>${healthTable("llm-health", "llm", llmData.llm || [])}`, { icon: "🧠" })}${divider()}${section("数据源健康", `${diagnosticsRangeControl("source-range", ranges.sources)}<p class="hint-text">汇总所有真实任务（含历史维护）的数据源请求；查看不会发送探针请求。</p>${healthTable("source-health", "source", sourceData.sources || [])}`, { icon: "🌐" })}`;
  bindCommon(host);
  [["#diagnostics-range", "runs"], ["#llm-range", "llm"], ["#source-range", "sources"]].forEach(([selector, key]) => $(selector, host).addEventListener("change", (event) => {
    state.pageData.diagnosticsRanges = { ...ranges, [key]: event.target.value };
    runLocalRefresh(refreshDiagnosticsContent(root, token));
  }));
  applyLocale(host);
}

async function renderDiagnostics(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="diagnostics-content"><div class="loading">正在读取运行诊断…</div></div>`;
  await refreshDiagnosticsContent(root, token);
}

function localDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function tokenHeatmap(rows) {
  // Keep the same Monday-to-Sunday calendar layout as Streamlit's activity
  // view.  A simple uninterrupted cell sequence looked compact, but made it
  // much harder to associate a spike with a real weekday or month.
  const values = new Map((rows || []).map((row) => [String(row.date || ""), {
    total: Number(row.total || 0), runs: Number(row.runs || 0),
  }]));
  const today = new Date();
  const firstDay = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 364);
  const start = new Date(firstDay);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  const weeks = Math.floor((today - start) / 86400000 / 7) + 1;
  let maximum = 0;
  for (let offset = 0; offset < 365; offset += 1) {
    const day = new Date(firstDay.getFullYear(), firstDay.getMonth(), firstDay.getDate() + offset);
    maximum = Math.max(maximum, values.get(localDateKey(day))?.total || 0);
  }
  const levelFor = (total) => {
    if (!total || !maximum) return 0;
    const ratio = total / maximum;
    if (ratio <= .25) return 1;
    if (ratio <= .5) return 2;
    if (ratio <= .75) return 3;
    return 4;
  };
  const weekdayLabels = state.language === "en" ? ["Mon", "", "Wed", "", "Fri", "", "Sun"] : ["周一", "", "周三", "", "周五", "", "周日"];
  const monthCells = [];
  let previousMonth = -1;
  for (let week = 0; week < weeks; week += 1) {
    const weekStart = new Date(start.getFullYear(), start.getMonth(), start.getDate() + week * 7);
    const month = weekStart.getMonth();
    const monthLabel = state.language === "en"
      ? new Intl.DateTimeFormat("en-US", { month: "short" }).format(weekStart)
      : `${month + 1}月`;
    monthCells.push(`<th>${month !== previousMonth ? monthLabel : ""}</th>`);
    previousMonth = month;
  }
  const calendarRows = weekdayLabels.map((label, weekday) => {
    const cells = [];
    for (let week = 0; week < weeks; week += 1) {
      const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + week * 7 + weekday);
      const key = localDateKey(day);
      const record = values.get(key) || { total: 0, runs: 0 };
      const title = record.total
        ? (state.language === "en"
          ? `${key} · ${formatNumber(record.total)} tokens · ${formatNumber(record.runs)} run${record.runs === 1 ? "" : "s"}`
          : `${key} · ${formatNumber(record.total)} Token · ${formatNumber(record.runs)} 次运行`)
        : `${key} · ${localeText("暂无用量记录", "No usage recorded")}`;
      cells.push(`<td><i class="heat-cell level-${levelFor(record.total)}" title="${escapeAttribute(title)}"></i></td>`);
    }
    return `<tr><th class="heat-weekday">${label}</th>${cells.join("")}</tr>`;
  }).join("");
  return `<div class="heatmap-wrap"><div class="heatmap-calendar" aria-label="${localeText("近一年 Token 使用热力图", "Token usage heatmap for the past year")}"><table><thead><tr><th></th>${monthCells.join("")}</tr></thead><tbody>${calendarRows}</tbody></table></div><div class="heatmap-legend"><span>${localeText("少", "Low")}</span><i class="heat-cell level-0"></i><i class="heat-cell level-1"></i><i class="heat-cell level-2"></i><i class="heat-cell level-3"></i><i class="heat-cell level-4"></i><span>${localeText("多", "High")}</span></div></div>`;
}

function analyticsBucketKey(date, bucket) {
  const day = localDateKey(date);
  if (bucket !== "hour") return day;
  return `${day}T${String(date.getHours()).padStart(2, "0")}`;
}

function analyticsSeriesRows(rows, window) {
  const supplied = (rows || []).filter((row) => row && (row.bucket || row.date));
  if (!supplied.length) return [];
  const bucket = window?.bucket === "hour" ? "hour" : "day";
  const byBucket = new Map(supplied.map((row) => [String(row.bucket || row.date), row]));
  const start = new Date(String(window?.start || ""));
  const end = new Date(String(window?.end || ""));
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start >= end) {
    return supplied.slice().sort((left, right) => String(left.bucket || left.date).localeCompare(String(right.bucket || right.date)));
  }
  const values = [];
  const cursor = new Date(start);
  if (bucket === "hour") cursor.setMinutes(0, 0, 0);
  else cursor.setHours(0, 0, 0, 0);
  for (let guard = 0; cursor < end && guard < 9000; guard += 1) {
    const key = analyticsBucketKey(cursor, bucket);
    const row = byBucket.get(key) || {};
    values.push({ bucket: key, prompt: Number(row.prompt || 0), completion: Number(row.completion || 0), total: Number(row.total || 0), runs: Number(row.runs || 0) });
    if (bucket === "hour") cursor.setHours(cursor.getHours() + 1);
    else cursor.setDate(cursor.getDate() + 1);
  }
  return values;
}

function analyticsBucketLabel(value, bucket) {
  const text = String(value || "");
  if (bucket === "hour") return text.slice(5).replace("T", " ");
  return text.slice(5);
}

function tokenTrendChart(rows, window) {
  const values = analyticsSeriesRows(rows, window);
  if (!values.length) return '<p class="empty-state">所选范围内没有 Token 使用记录。</p>';
  const sampled = values.length > 366 ? values.filter((_, index) => index % Math.ceil(values.length / 366) === 0) : values;
  const width = 840; const height = 306; const left = 64; const right = 18; const top = 30; const bottom = 43;
  const plotWidth = width - left - right; const plotHeight = height - top - bottom;
  const rawMax = Math.max(0, ...sampled.map((row) => Math.max(row.prompt, row.completion, row.total)));
  const niceCeiling = (value) => {
    if (value <= 0) return 1;
    const exponent = Math.floor(Math.log10(value));
    const scale = 10 ** exponent;
    const normalized = value / scale;
    const step = [1, 2, 5, 10].find((item) => normalized <= item) || 10;
    return step * scale;
  };
  const maximum = niceCeiling(rawMax * 1.05);
  const x = (index) => sampled.length === 1 ? left + plotWidth / 2 : left + index * plotWidth / (sampled.length - 1);
  const y = (value) => top + plotHeight * (1 - value / maximum);
  const linePoints = (key) => sampled.map((row, index) => `${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`).join(" ");
  const points = (key) => sampled.map((row, index) => `<circle class="trend-point ${key}" cx="${x(index).toFixed(1)}" cy="${y(row[key]).toFixed(1)}" r="${sampled.length > 90 ? "1.5" : "2.25"}"><title>${escapeHtml(`${analyticsBucketLabel(row.bucket, window?.bucket)} · ${key === "prompt" ? "输入" : key === "completion" ? "输出" : "合计"} ${formatNumber(row[key])} ${state.language === "en" ? "tokens" : "Token"}`)}</title></circle>`).join("");
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = maximum * index / 4;
    return `<line x1="${left}" x2="${width - right}" y1="${y(value).toFixed(1)}" y2="${y(value).toFixed(1)}"/><text x="${left - 8}" y="${(y(value) + 4).toFixed(1)}" text-anchor="end">${escapeHtml(formatCompactNumber(value))}</text>`;
  }).join("");
  const labelCount = Math.min(6, sampled.length);
  const labels = Array.from({ length: labelCount }, (_, index) => labelCount === 1 ? 0 : Math.round(index * (sampled.length - 1) / (labelCount - 1)));
  const labelText = labels.map((index) => `<text x="${x(index).toFixed(1)}" y="${height - 16}" text-anchor="middle">${escapeHtml(analyticsBucketLabel(sampled[index].bucket, window?.bucket))}</text>`).join("");
  return `<div class="trend-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Token 使用趋势"><g class="trend-grid">${grid}</g><polyline class="trend-line total" points="${linePoints("total")}"/><polyline class="trend-line prompt" points="${linePoints("prompt")}"/><polyline class="trend-line completion" points="${linePoints("completion")}"/><g class="trend-points">${points("total")}${points("prompt")}${points("completion")}</g><g class="trend-labels">${labelText}</g><g class="trend-legend"><rect x="${left}" y="6" width="11" height="11" class="total"/><text x="${left + 17}" y="15">合计</text><rect x="${left + 74}" y="6" width="11" height="11" class="prompt"/><text x="${left + 91}" y="15">输入</text><rect x="${left + 148}" y="6" width="11" height="11" class="completion"/><text x="${left + 165}" y="15">输出</text></g></svg></div>`;
}

function formatCompactNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}k`;
  return String(Math.round(number));
}

function analyticsRangeControl(values) {
  const choices = [
    ["24h", "24 小时内"], ["today", "当天"], ["3d", "3 天"],
    ["7d", "7 天"], ["14d", "14 天"], ["30d", "30 天"], ["custom", "自定义时间段"],
  ];
  return `<div class="analytics-range-control"><div class="segmented-control" role="group" aria-label="时间段">${choices.map(([value, label]) => `<button type="button" class="segmented-button ${values.range === value ? "is-active" : ""}" data-analytics-range="${value}" aria-pressed="${values.range === value}">${escapeHtml(label)}</button>`).join("")}</div>${values.range === "custom" ? `<div class="form-grid two analytics-custom-range"><label class="form-field"><span>开始日期</span><input id="analytics-from" type="date" value="${escapeAttribute(values.date_from)}" /></label><label class="form-field"><span>结束日期</span><input id="analytics-to" type="date" value="${escapeAttribute(values.date_to)}" /></label></div><div class="action-row analytics-custom-action"><button id="analytics-custom-apply" type="button" class="secondary-button">应用时间段</button></div>` : ""}</div>`;
}

function usageSummaryTable(summary) {
  const metrics = [
    ["输入 Token", formatNumber(summary.prompt)],
    ["输出 Token", formatNumber(summary.completion)],
    ["合计 Token", formatNumber(summary.total)],
    ["涉及运行", formatNumber(summary.runs)],
  ];
  return `<div class="table-wrap usage-summary-table"><table><thead><tr>${metrics.map(([label]) => `<th scope="col">${escapeHtml(label)}</th>`).join("")}</tr></thead><tbody><tr>${metrics.map(([, value]) => `<td>${escapeHtml(value)}</td>`).join("")}</tr></tbody></table></div>`;
}

async function refreshAnalyticsContent(root = $("#page-root"), token = state.renderToken) {
  const host = $("#analytics-content", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("analytics-content");
  const fallback = { range: "7d", date_from: relativeLocalDateKey(-6), date_to: relativeLocalDateKey(0) };
  const values = { ...fallback, ...(state.pageData.analytics || {}) };
  const params = new URLSearchParams({ range: values.range });
  if (values.range === "custom") {
    params.set("date_from", values.date_from);
    params.set("date_to", values.date_to);
  }
  host.innerHTML = '<div class="loading">正在读取 Token 使用记录…</div>';
  const data = await api(`/api/analytics?${params.toString()}`);
  if (token !== state.renderToken || state.page !== "analytics" || !isCurrentLocalRequest("analytics-content", requestVersion)) return;
  const control = analyticsRangeControl(values);
  const summary = data.summary || { prompt: 0, completion: 0, total: 0, runs: 0 };
  const hasUsage = Boolean(data.available && ((data.series || []).length || Number(summary.runs) > 0));
  const usage = hasUsage
    ? usageSummaryTable(summary)
    : '<p class="empty-state">所选时间段内暂无用量数据。完成任一涉及 LLM 的任务后，这里会出现统计。</p>';
  const trend = hasUsage
    ? tokenTrendChart(data.series || [], data.window || {})
    : '<p class="empty-state">所选时间段内没有可绘制的 Token 使用趋势。</p>';
  const heatmap = (data.heatmap_daily || []).length
    ? tokenHeatmap(data.heatmap_daily || [])
    : '<p class="report-empty-state">暂无历史 Token 使用记录。</p>';
  const modelTable = pagedTable("analytics-models", [
    { label: "模型", key: "model" },
    { label: "输入 Token", value: (row) => formatNumber(row.prompt) },
    { label: "输出 Token", value: (row) => formatNumber(row.completion) },
    { label: "总 Token", value: (row) => formatNumber(row.total) },
  ], data.models || [], { empty: "所选时间段内暂无模型使用记录。" });
  host.innerHTML = `<section class="section-card analytics-statistics-card">${heatmap}<div class="analytics-statistics-block">${control}${usage}</div><div class="analytics-statistics-block trend-chart-block">${trend}</div><div class="analytics-statistics-block">${modelTable}</div></section>`;
  bindCommon(host);
  $$('[data-analytics-range]', host).forEach((button) => button.addEventListener("click", () => {
    state.pageData.analytics = { ...values, range: button.dataset.analyticsRange };
    runLocalRefresh(refreshAnalyticsContent(root, token));
  }));
  $("#analytics-custom-apply", host)?.addEventListener("click", () => {
    const dateFrom = $("#analytics-from", host).value;
    const dateTo = $("#analytics-to", host).value;
    if (!dateFrom || !dateTo || dateFrom > dateTo) {
      toast("请填写有效的开始和结束日期。", "error");
      return;
    }
    state.pageData.analytics = { ...values, date_from: dateFrom, date_to: dateTo };
    runLocalRefresh(refreshAnalyticsContent(root, token));
  });
  applyLocale(host);
}

async function renderAnalytics(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="analytics-content"><div class="loading">正在读取 Token 使用记录…</div></div>`;
  await refreshAnalyticsContent(root, token);
}

function logLabel(item) {
  return `${item.name}  [${formatTime(item.modified_at).slice(5, 16)}  ${Math.round(Number(item.size_bytes) / 1024)} KB]`;
}

function logWorkspaceMarkup(items, selected) {
  const group = (category) => items.filter((item) => (item.category || "other") === category);
  const selector = (id, title, rows) => `<div class="log-select-field"><span>${escapeHtml(title)}</span>${scrollSelect({ id, rows, selected, label: logLabel, optionAttribute: "data-log-select-option", valueAttribute: "data-log-id" })}</div>`;
  return `<section class="section-card log-selector-card"><div class="log-selector-grid">${selector("log-system-select", "📌 系统日志", group("system"))}${selector("log-run-select", "📀 运行日志", group("run"))}${selector("log-other-select", "📄 其他日志", group("other"))}</div></section><div id="log-content-region">${selected && !state.pageData.logClosed ? '<div class="loading">正在读取日志内容…</div>' : '<p class="report-empty-state">选择一个日志文件后可在这里查看内容。</p>'}</div>`;
}

function updateLogSelectorSelection(root, selected) {
  $$('[data-log-select-option]', root).forEach((option) => {
    const active = option.dataset.logId === selected;
    option.setAttribute("aria-selected", String(active));
    option.classList.toggle("is-selected", active);
  });
  const selectedOption = $$('[data-log-select-option]', root).find((item) => item.dataset.logId === selected);
  const selector = selectedOption?.closest(".scroll-select");
  if (selector) {
    const label = $("summary > span", selector);
    if (label) label.textContent = selectedOption.textContent || "—";
    selector.open = false;
  }
}

async function loadSelectedLog(root, items, selected, token) {
  const host = $("#log-content-region", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("log-content");
  if (!selected || state.pageData.logClosed) {
    host.innerHTML = '<p class="report-empty-state">选择一个日志文件后可在这里查看内容。</p>';
    return;
  }
  host.innerHTML = '<div class="loading">正在读取日志内容…</div>';
  try {
    const log = await api(`/api/logs/${encodeURIComponent(selected)}`);
    if (token !== state.renderToken || state.page !== "logs" || state.pageData.selectedLog !== selected || !isCurrentLocalRequest("log-content", requestVersion)) return;
    const selectedItem = items.find((item) => item.id === selected);
    host.innerHTML = `<section class="log-content"><div class="toolbar"><p class="report-file-info"><strong>${escapeHtml(log.name)}</strong>${selectedItem ? ` · ${Math.round(Number(selectedItem.size_bytes) / 1024)} KB · ${localeText("修改时间：", "Modified: ")}${escapeHtml(formatTime(selectedItem.modified_at))}` : ""}${log.truncated ? ` · ${localeText("仅显示最后 300 行", "last 300 lines only")}` : ""}</p><div class="action-row"><button id="log-refresh-latest" class="secondary-button compact-button">刷新最新日志</button><button id="log-close" class="secondary-button compact-button">关闭</button></div></div><pre class="log-viewer">${escapeHtml(log.content)}</pre></section>`;
    $("#log-refresh-latest", host)?.addEventListener("click", () => runLocalRefresh(refreshLogsWorkspace(root, token, { latest: true })));
    $("#log-close", host)?.addEventListener("click", () => {
      state.pageData.logClosed = true;
      loadSelectedLog(root, items, selected, token);
    });
    applyLocale(host);
  } catch (error) {
    host.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
  }
}

function bindLogWorkspace(root, items, token) {
  $$('[data-log-select-option]', root).forEach((button) => button.addEventListener("click", () => {
    const selected = button.dataset.logId;
    if (!selected) return;
    state.pageData.selectedLog = selected;
    state.pageData.logClosed = false;
    updateLogSelectorSelection(root, selected);
    loadSelectedLog(root, items, selected, token);
  }));
}

async function refreshLogsWorkspace(root = $("#page-root"), token = state.renderToken, options = {}) {
  const host = $("#logs-workspace", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("logs-workspace");
  host.innerHTML = '<div class="loading">正在读取日志列表…</div>';
  const data = await api("/api/logs");
  if (token !== state.renderToken || state.page !== "logs" || !isCurrentLocalRequest("logs-workspace", requestVersion)) return;
  const items = data.items || [];
  const nonSystemLogs = items.filter((item) => (item.category || "other") !== "system");
  let selected = options.latest ? nonSystemLogs[0]?.id || "" : state.pageData.selectedLog;
  if (!selected || !items.some((item) => item.id === selected)) selected = nonSystemLogs[0]?.id || "";
  state.pageData.selectedLog = selected;
  if (options.latest) state.pageData.logClosed = false;
  host.innerHTML = logWorkspaceMarkup(items, selected);
  bindLogWorkspace(root, items, token);
  applyLocale(host);
  await loadSelectedLog(root, items, selected, token);
}

async function renderLogs(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="logs-workspace"><div class="loading">正在读取日志列表…</div></div>`;
  await refreshLogsWorkspace(root, token);
}

function accountsContent(data) {
  if (!data.enabled) {
    return section("账户管理", '<p class="info-box">当前已跳过登录，仅建议在可信内网使用。可在 .env 设置 WEBUI_AUTH_ENABLED=true 后重新启用账户验证。</p>', { icon: "👤" });
  }
  const ownForm = `<form id="own-password-form" class="stack-form compact-form"><h3>修改我的密码</h3><label>当前密码<input name="current_password" type="password" required /></label><label>新密码<input name="new_password" type="password" minlength="6" required /></label><label>确认新密码<input name="password_confirmation" type="password" minlength="6" required /></label><button class="primary-button" type="submit">保存密码</button></form>`;
  const secondaryAccounts = (data.items || []).filter((item) => !item.is_owner);
  const accountOptions = secondaryAccounts.map((item) => `<option value="${escapeAttribute(item.username)}">${escapeHtml(item.username)}</option>`).join("");
  const ownerForms = data.is_owner
    ? `<form id="add-account-form" class="stack-form compact-form"><h3>新增管理员</h3><label>用户名<input name="username" required /></label><label>密码<input name="password" type="password" minlength="6" required /></label><label>确认密码<input name="password_confirmation" type="password" minlength="6" required /></label><button class="secondary-button" type="submit">新增账户</button></form>${secondaryAccounts.length ? `${divider()}<form id="reset-account-form" class="stack-form compact-form"><h3>重置管理员密码</h3><label>账户<select name="username">${accountOptions}</select></label><label>新密码<input name="new_password" type="password" minlength="6" required /></label><label>确认新密码<input name="password_confirmation" type="password" minlength="6" required /></label><button class="secondary-button" type="submit">重置密码</button></form>${divider()}<form id="delete-account-form" class="stack-form compact-form"><h3>删除管理员</h3><label>账户<select name="username">${accountOptions}</select></label><label class="toggle-field"><span>我已确认删除该管理员账户</span><input name="confirmed" type="checkbox"/><i></i></label><button class="danger-button" type="submit">删除管理员</button></form>` : '<p class="hint-text">尚无其他管理员账户可重置或删除。</p>'}`
    : '<p class="hint-text">普通管理员可以修改自己的密码；账户所有者可管理其他管理员。</p>';
  return `${section("账户列表", `${pagedTable("accounts", [{ label: "用户名", key: "username" }, { label: "角色", key: "role" }, { label: "当前账户", value: (row) => row.current ? "当前" : "—" }], data.items || [], { empty: "暂无账户" })}`, { icon: "👥" })}${divider()}${section("账户操作", `${ownForm}${divider()}${ownerForms}`, { icon: "🔐" })}`;
}

function bindAccounts(root, token) {
  bindAccountForm("#own-password-form", "/api/accounts/change-password", () => {
    toast("密码已修改，请使用新密码重新登录。", "success");
    clearTimers();
    window.setTimeout(() => window.location.reload(), 600);
  });
  bindAccountForm("#add-account-form", "/api/accounts/add", () => { toast("管理员账户已创建。", "success"); runLocalRefresh(refreshAccountsContent(root, token)); });
  bindAccountForm("#reset-account-form", "/api/accounts/reset", () => toast("管理员密码已重置。", "success"));
  bindAccountForm("#delete-account-form", "/api/accounts/delete", () => { toast("管理员已删除。", "success"); runLocalRefresh(refreshAccountsContent(root, token)); });
}

async function refreshAccountsContent(root = $("#page-root"), token = state.renderToken) {
  const host = $("#accounts-content", root);
  if (!host) return;
  const requestVersion = beginLocalRequest("accounts-content");
  host.innerHTML = '<div class="loading">正在读取账户列表…</div>';
  const data = await api("/api/accounts");
  if (token !== state.renderToken || state.page !== "accounts" || !isCurrentLocalRequest("accounts-content", requestVersion)) return;
  host.innerHTML = accountsContent(data);
  bindCommon(host);
  bindAccounts(root, token);
  applyLocale(host);
}

async function renderAccounts(token) {
  const root = $("#page-root");
  root.innerHTML = `${pageHeader()}<div id="accounts-content"><div class="loading">正在读取账户列表…</div></div>`;
  await refreshAccountsContent(root, token);
}

function bindAccountForm(selector, endpoint, onSuccess) {
  const form = $(selector); if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    try { await api(endpoint, { method: "POST", body: payload }); form.reset(); onSuccess(); } catch (error) { toast(error.message, "error"); }
  });
}

function normalizeForSave() {
  const config = { ...(state.settings?.config || {}), ...state.draft.config };
  if (Object.prototype.hasOwnProperty.call(config, "proxy_no_proxy_ui")) {
    config.proxy_no_proxy = String(config.proxy_no_proxy_ui || "")
      .split(/[\r\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .join(",");
    delete config.proxy_no_proxy_ui;
  }
  // UI-only helpers map back to the stable config.json contract.
  if (Object.prototype.hasOwnProperty.call(config, "pdf_download_max_mb_ui")) {
    config.pdf_download_max_bytes = Math.max(1, Number(config.pdf_download_max_mb_ui) || 50) * 1024 * 1024;
    delete config.pdf_download_max_mb_ui;
  }
  if (Object.prototype.hasOwnProperty.call(config, "keyword_trend_default_days_ui")) {
    const choice = String(config.keyword_trend_default_days_ui || "30");
    const rawDays = choice === "custom"
      ? Number(config.keyword_trend_default_days_custom)
      : Number(choice);
    config.keyword_trend_default_days = Math.max(1, Math.round(Number.isFinite(rawDays) ? rawDays : 30));
    delete config.keyword_trend_default_days_ui;
    delete config.keyword_trend_default_days_custom;
  }
  const sourceState = state.pageData.sources;
  if (sourceState) {
    const definitions = [
      ...sourceState.builtins.filter((code) => code !== "prl").map(sourceDefinition).filter(Boolean),
      ...sourceState.custom,
    ];
    // Keep the persisted meaning identical to the Streamlit collector: a
    // checked extra-source master switch with nothing selected is a no-op.
    // Persisting it as enabled makes worker validation and the visible state
    // disagree after a reload.
    const extraEnabled = Boolean(sourceState.extraEnabled && (sourceState.builtins.length || definitions.length));
    const enabled = [];
    if (sourceState.arxiv) enabled.push("arxiv");
    if (extraEnabled) {
      if (sourceState.builtins.includes("prl")) enabled.push("prl");
      enabled.push(...definitions.map((item) => item.code));
    }
    const validDomains = new Set(arxivCategories().map((item) => item.code));
    config.enabled_sources = enabled;
    // Streamlit drops legacy/invalid category values during collection rather
    // than persisting a source filter that arXiv cannot honour.
    config.domains = sourceState.domains.filter((code) => validDomains.has(code));
    config.extra_sources_enabled = extraEnabled;
    config.extra_source_definitions = definitions;
  }
  if (Object.prototype.hasOwnProperty.call(config, "trend_output_html") || Object.prototype.hasOwnProperty.call(config, "trend_output_md")) {
    const configuredFormats = Array.isArray(config.trend_output_formats)
      ? config.trend_output_formats
      : ["markdown", "html"];
    const formats = [];
    const markdownEnabled = Object.prototype.hasOwnProperty.call(config, "trend_output_md")
      ? config.trend_output_md === true
      : configuredFormats.includes("markdown");
    const htmlEnabled = Object.prototype.hasOwnProperty.call(config, "trend_output_html")
      ? config.trend_output_html === true
      : configuredFormats.includes("html");
    if (markdownEnabled) formats.push("markdown");
    if (htmlEnabled) formats.push("html");
    config.trend_output_formats = formats;
    delete config.trend_output_html;
    delete config.trend_output_md;
  }
  // Trend prompt/skill state belongs to the task page rather than a generic
  // field.  Persist it only after that page has been opened so an unrelated
  // save never replaces a configured template selection.
  if (state.pageData.trend) {
    const trend = state.pageData.trend;
    config.trend_analysis_prompt = String(trend.analysis_prompt || "");
    // 综合分析是趋势研究的固定阶段。保留这个兼容字段的规范值，避免旧
    // 配置中的空技能列表在 Worker 侧意外跳过分析。
    config.trend_enabled_skills = ["comprehensive_analysis"];
    // These two controls live beside the one-off launch form, but they are
    // also persisted preferences in the compatibility panel.  Keep their
    // browser constraints explicit here because native number inputs can be
    // edited outside their spinner range before the global Save action.
    const maxResults = Number(trend.max_results);
    if (Number.isFinite(maxResults)) {
      config.trend_max_results = Math.max(10, Math.min(5000, Math.round(maxResults)));
    }
    config.trend_sort_order = trend.sort_order === "descending" ? "descending" : "ascending";
  }
  // The compatibility panel intentionally keeps the SQLite research ledger
  // enabled on every save.  It is not a user-facing switch: disabling it
  // would silently break retries, history maintenance and report metadata.
  config.daily_research_persistence_enabled = true;
  config.score_strategy_explicit = true;
  return config;
}

async function saveAll(showMessage = true) {
  if (!state.settings) return null;
  try {
    const result = await api("/api/settings", { method: "PUT", body: { config: normalizeForSave(), env: state.draft.env, clear_env: Array.from(state.draft.clearEnv) } });
    state.settings = result;
    state.draft = { config: {}, env: {}, clearEnv: new Set() };
    state.configurationDirty = false;
    state.pageData.sources = undefined;
    updateConfigurationDirtyIndicator();
    if (showMessage) toast("配置已保存。", "success");
    return result;
  } catch (error) {
    if (showMessage) toast(error.message, "error");
    throw error;
  }
}

async function refreshActiveTaskPanels() {
  const root = $("#page-root");
  if (state.page === "daily_research") return refreshDailyStatus();
  if (state.page === "past_daily") return refreshPastDailyContent(root);
  if (state.page === "trend_tasks") return refreshTrendStatus(root);
  if (state.page === "history_tasks") return refreshHistoryStatus();
  return undefined;
}

function bindCommon(root = document) {
  bindFields(root);
  bindPagers(root);
  $$('[data-refresh-status]', root).forEach((button) => button.addEventListener("click", () => runLocalRefresh(refreshActiveTaskPanels())));
  $$('[data-start-task]', root).forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/api/tasks/${encodeURIComponent(button.dataset.startTask)}`, { method: "POST", body: { args: {} } }); toast("任务已加入队列。", "success"); await refreshActiveTaskPanels(); } catch (error) { toast(error.message, "error"); }
  }));
  $$('[data-stop-task]', root).forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("确认停止当前任务？已完成的阶段会保留，未完成论文将留队等待重试。")) return;
    try { await api("/api/tasks/stop", { method: "POST", body: { kind: button.dataset.stopTask } }); toast("已发送停止请求。", "success"); await refreshActiveTaskPanels(); } catch (error) { toast(error.message, "error"); }
  }));
  $$('[data-clear-stale-triggers]', root).forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("确认清除所有本地过期任务请求？未被工作进程接手的任务需要重新提交。")) return;
    try {
      const result = await api("/api/triggers/stale", { method: "POST", body: {} });
      toast(`已清除 ${Number(result.removed || 0)} 个过期请求。`, "success");
      await refreshActiveTaskPanels();
    } catch (error) { toast(error.message, "error"); }
  }));
}

const PAGE_RENDERERS = {
  daily_research: renderDaily,
  past_daily: renderPastDaily,
  trend_tasks: renderTrend,
  reports: renderReports,
  favorites: renderFavorites,
  paper_search: renderPaperSearch,
  keywords: renderKeywords,
  data_sources: renderDataSources,
  scoring: renderScoring,
  api: renderApi,
  notifications: renderNotificationsPage,
  advanced: renderAdvancedPage,
  accounts: renderAccounts,
  backup_sync: renderBackupSync,
  history_tasks: renderHistory,
  diagnostics: renderDiagnostics,
  analytics: renderAnalytics,
  logs: renderLogs,
};

async function renderPage(options = {}) {
  const preserveScroll = Boolean(options.preserveScroll);
  const scrollTop = preserveScroll ? window.scrollY : 0;
  clearTimers();
  state.pagedRenderers.clear();
  if (state.page !== "reports") {
    state.reportMarkAbortController?.abort();
    state.reportMarkAbortController = null;
  }
  setLocation();
  const token = ++state.renderToken;
  const renderer = PAGE_RENDERERS[state.page] || renderDaily;
  try {
    await renderer(token);
    if (token === state.renderToken) {
      applyLocale($("#page-root"));
      updateConfigurationDirtyIndicator();
      if (preserveScroll) {
        window.requestAnimationFrame(() => window.scrollTo(0, scrollTop));
      }
    }
  } catch (error) {
    if (token === state.renderToken) {
      $("#page-root").innerHTML = `${pageHeader()}<section class="section-card"><p class="error-message">${escapeHtml(error.message)}</p><button class="secondary-button" id="page-retry">重试</button></section>`;
      $("#page-retry")?.addEventListener("click", renderPage);
      applyLocale($("#page-root"));
    }
  }
}

function showApp() {
  $("#auth").hidden = true;
  $("#app").hidden = false;
  renderNavigation();
  updateConfigurationDirtyIndicator();
  applyLocale(document);
  applyTheme();
}

function showAuth(auth) {
  $("#app").hidden = true;
  $("#auth").hidden = false;
  $("#auth-error").textContent = "";
  const setup = $("#setup-form"); const login = $("#login-form");
  if (!auth.enabled) {
    $("#auth-title").textContent = "内网模式";
    $("#auth-hint").textContent = "登录验证已关闭，正在进入面板。";
    setup.hidden = true; login.hidden = true;
    applyLocale(document);
    return;
  }
  if (!auth.configured) {
    $("#auth-title").textContent = "初始化管理员账户";
    $("#auth-hint").textContent = "创建本地管理员账户，或在可信内网中跳过登录。";
    setup.hidden = false; login.hidden = true;
  } else {
    $("#auth-title").textContent = "登录管理面板";
    $("#auth-hint").textContent = "使用本机配置的管理员账户登录。";
    setup.hidden = true; login.hidden = false;
  }
  applyLocale(document);
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  $("#version-label").textContent = "现代管理面板";
}

async function loginSubmit(event) {
  event.preventDefault();
  try {
    await api("/api/auth/login", { method: "POST", body: { username: $("#login-username").value, password: $("#login-password").value } });
    $("#login-password").value = "";
    state.auth = await api("/api/auth/status"); await loadSettings(); showApp(); renderPage();
  } catch (error) { $("#auth-error").textContent = error.message; }
}

async function setupSubmit(event) {
  event.preventDefault();
  try {
    await api("/api/auth/setup", { method: "POST", body: { username: $("#setup-username").value, password: $("#setup-password").value, password_confirmation: $("#setup-password-confirmation").value } });
    state.auth = await api("/api/auth/status"); await loadSettings(); showApp(); renderPage();
  } catch (error) { $("#auth-error").textContent = error.message; }
}

async function skipAuth() {
  if (!window.confirm("确认跳过登录？仅建议在可信内网使用。")) return;
  try { await api("/api/auth/setup", { method: "POST", body: { action: "skip" } }); state.auth = await api("/api/auth/status"); await loadSettings(); showApp(); renderPage(); } catch (error) { $("#auth-error").textContent = error.message; }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST", body: {} }).catch(() => null);
  clearTimers(); window.location.reload();
}

async function initialize() {
  readLocation();
  $("#login-form").addEventListener("submit", loginSubmit);
  $("#setup-form").addEventListener("submit", setupSubmit);
  $("#skip-auth-button").addEventListener("click", skipAuth);
  $("#language-button").addEventListener("click", () => { void toggleLanguage(); });
  $("#theme-button").addEventListener("click", toggleTheme);
  $("#logout-button").addEventListener("click", logout);
  $("#save-button").addEventListener("click", () => saveAll(true));
  $("#reload-button").addEventListener("click", async () => { try { await loadSettings(); state.draft = { config: {}, env: {}, clearEnv: new Set() }; state.configurationDirty = false; state.pageData.sources = undefined; updateConfigurationDirtyIndicator(); toast("配置已重新加载。", "success"); renderPage(); } catch (error) { toast(error.message, "error"); } });
  $("#restart-worker-button").addEventListener("click", async (event) => {
    if (!window.confirm("确认请求重启研究容器？正在运行的任务会由容器重启策略处理。")) return;
    try {
      event.currentTarget.disabled = true;
      await api("/api/system/restart-worker", { method: "POST", body: {} });
      toast("已发送研究容器重启请求，正在刷新面板状态。", "success");
      clearTimers();
      window.setTimeout(() => window.location.reload(), 650);
    } catch (error) {
      event.currentTarget.disabled = false;
      toast(error.message, "error");
    }
  });
  window.addEventListener("hashchange", () => { readLocation(); renderNavigation(); renderPage(); });
  try {
    applyTheme();
    // Start authentication immediately. Chinese is already embedded in the
    // document, so the large shared catalogue is non-critical; English still
    // loads it in parallel with the auth round trip.
    const authRequest = api("/api/auth/status");
    if (state.language === "en") await loadTranslations().catch(() => null);
    state.auth = await authRequest;
    if (!state.auth.authenticated) { showAuth(state.auth); warmTranslations(); return; }
    await loadSettings(); showApp(); renderPage(); warmTranslations();
  } catch (error) {
    showAuth({ configured: false, enabled: true });
    $("#auth-hint").textContent = localizedString(error.message);
  }
}

initialize();
