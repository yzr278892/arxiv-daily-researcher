import os
import json
import math
import re

import json5  # 用于加载带注释的配置文件
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from utils.source_registry import (
    CORE_SOURCE_CODES,
    definitions_for_builtin_codes,
    source_codes_from_definitions,
    validate_source_definitions,
)
from utils.config_io import ConfigMigrationError, ensure_runtime_config_path

# 1. 定义基础路径：获取项目根目录（src/ 的上级目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationLoadError(RuntimeError):
    """An existing user configuration could not be safely loaded.

    A daily scan must never silently fall back to its built-in scope after a
    user has supplied a config file.  In particular, doing so could turn a
    malformed ``target_domains`` section into an unintended ``quant-ph``
    scan.  Callers should treat this as a startup failure and leave the file
    untouched for the user to repair or restore from its backup.
    """


def resolve_project_relative_path(project_root: Path, value: object, *, label: str) -> Path:
    """Resolve one configured path while keeping it inside the project tree.

    Configuration files are intentionally portable and may be exported from
    the WebUI.  They are not an authority to redirect an unattended worker to
    arbitrary host paths, so absolute paths, parent traversal and symlinked
    ancestors outside ``project_root`` are rejected before directory creation.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空项目相对路径")
    raw_path = Path(value.strip())
    if raw_path.is_absolute():
        raise ValueError(f"{label} 必须是项目相对路径，不能使用绝对路径")
    if any(part == ".." for part in raw_path.parts):
        raise ValueError(f"{label} 不能包含父目录遍历（..）")
    if any(part in {"", "."} for part in raw_path.parts):
        raise ValueError(f"{label} 包含无效路径段")

    root = Path(project_root).resolve()
    candidate = root / raw_path
    # ``strict=False`` still resolves any existing ancestor symlink.  That is
    # exactly what is needed before the subsequent ensure_directories() call
    # could create a child through a link that points outside the repository.
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于项目目录内") from exc
    return resolved


def _weighted_entries_from_config(
    raw_entries: object,
    *,
    name_key: str,
    value_key: str,
    fallback_names: object,
    fallback_value: object,
    label: str,
) -> Tuple[List[str], Dict[str, float]]:
    """Read v4.1 per-item weights while retaining the older list format.

    ``config.json`` remains portable and can be hand-edited, so the worker
    validates this payload before a run starts instead of silently reverting to
    a shared default weight.  The returned list preserves operator order for
    prompts and reports; the mapping is used by the scorer.
    """
    if raw_entries is None:
        if not isinstance(fallback_names, list):
            raise ValueError(f"{label}列表必须是列表")
        try:
            numeric_fallback = float(fallback_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}默认数值必须是非负数字") from exc
        if not math.isfinite(numeric_fallback) or numeric_fallback < 0:
            raise ValueError(f"{label}默认数值必须是非负数字")
        entries = [{name_key: item, value_key: numeric_fallback} for item in fallback_names]
    else:
        if not isinstance(raw_entries, list):
            raise ValueError(f"{label}条目必须是列表")
        entries = raw_entries

    names: List[str] = []
    weights: Dict[str, float] = {}
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{label}中的每一项必须是对象")
        name = entry.get(name_key)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}名称不能为空")
        normalized_name = name.strip()
        dedupe_key = normalized_name.casefold()
        if dedupe_key in seen:
            raise ValueError(f"{label}不能重复：{normalized_name}")
        value = entry.get(value_key)
        if isinstance(value, bool):
            raise ValueError(f"{label}数值必须是非负数字")
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}数值必须是非负数字") from exc
        if not math.isfinite(numeric_value) or numeric_value < 0:
            raise ValueError(f"{label}数值必须是非负数字")
        names.append(normalized_name)
        weights[normalized_name] = numeric_value
        seen.add(dedupe_key)
    return names, weights


class LLMConfig(BaseModel):
    """
    语言模型配置类，定义单个LLM实例的参数。

    属性:
        api_key: LLM服务的API密钥
        base_url: LLM API的基础URL，默认为OpenAI官方地址
        model_name: 使用的具体模型名称，如gpt-4o
        temperature: 模型的温度参数，控制输出的随机性（0.3为较低随机性）
    """

    api_key: str = Field(..., description="LLM服务的API密钥")
    base_url: str = Field("https://api.openai.com/v1", description="LLM API的基础URL地址")
    model_name: str = Field("gpt-4o", description="要使用的模型名称标识")
    temperature: float = 0.3


class Settings(BaseSettings):
    """
    系统全局配置类，集中管理所有应用配置参数。

    优先级：runtime/config.json > .env文件 > 默认值
    """

    # ==================== 路径配置 ====================
    PROJECT_ROOT: Path = PROJECT_ROOT
    DATA_DIR: Path = PROJECT_ROOT / "data"

    # 核心数据存储目录
    REF_PDF_DIR: Path = DATA_DIR / "reference_pdfs"  # 参考论文PDF存储路径
    REPORTS_DIR: Path = DATA_DIR / "reports"  # 报告根目录（含各类型子目录）

    # 配置目录
    CONFIGS_DIR: Path = PROJECT_ROOT / "configs"

    # 报告模板目录
    REPORT_TEMPLATES_DIR: Path = CONFIGS_DIR / "templates" / "reports"

    # 从Arxiv下载的临时PDF存储目录
    DOWNLOAD_DIR: Path = DATA_DIR / "downloaded_pdfs"

    HISTORY_FILE: Path = DATA_DIR / "history.json"  # 已处理论文的历史记录文件

    # ==================== 搜索配置 ====================
    # Legacy compatibility only.  Daily scans always exhaust their configured
    # time window, so these values are never a fetch/LLM budget.
    MAX_RESULTS: Optional[int] = None
    # 日报固定回看最近 3 天；更早日期的论文由「过去时间段每日报告」补跑。
    DAILY_SCAN_WINDOW_DAYS: int = 3
    TARGET_DOMAINS: List[str] = ["quant-ph"]  # 目标领域列表

    # ==================== 数据源配置 ====================
    ENABLED_SOURCES: List[str] = ["arxiv"]  # 启用的数据源列表
    TARGET_JOURNALS: List[str] = []  # 目标期刊列表（如 ["prl", "pra"]）
    EXTRA_SOURCES_ENABLED: bool = False
    EXTRA_SOURCE_DEFINITIONS: List[Dict[str, Any]] = []
    REPORTS_BY_SOURCE: bool = True  # 是否按数据源分目录存放报告
    HISTORY_DIR: Path = DATA_DIR / "history"  # 历史记录目录

    # OpenAlex 配置
    ENABLE_OPENALEX: bool = True  # 是否启用 OpenAlex 期刊来源
    OPENALEX_API_KEY: str = ""  # 免费 Key 提高每日 API 额度并便于查看用量

    # ArXiv 抓取配置
    ARXIV_FETCH_TIMEOUT_SECONDS: int = 180  # 单次抓取硬超时，避免无限阻塞
    # arXiv 公告和 API 索引偶尔会晚于论文的 submittedDate。日报在正常
    # 扫描窗口之外额外回看这段时间；精确版本交付账本会去除重叠结果。
    ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS: int = 2

    # Hugging Face Papers 配置。该日榜是可选的补充发现源，不是 arXiv
    # 分类的全量替代；默认延迟读取已形成的榜单，并对近期日期重扫以抵御
    # 上游索引/展示延迟。所有结果仍完整分页，不存在结果数量预算。
    HUGGINGFACE_PAPERS_AVAILABILITY_LAG_DAYS: int = 2
    HUGGINGFACE_PAPERS_LOOKBACK_GRACE_DAYS: int = 2
    HUGGINGFACE_PAPERS_REQUEST_TIMEOUT_SECONDS: int = 30
    HUGGINGFACE_PAPERS_REQUEST_INTERVAL_SECONDS: float = 0.25

    # Semantic Scholar 配置
    ENABLE_SEMANTIC_SCHOLAR_TLDR: bool = True  # 是否获取AI生成的TLDR
    SEMANTIC_SCHOLAR_API_KEY: str = ""  # Semantic Scholar API Key（可选）

    # ==================== 关键词配置 ====================
    # 主要关键词（手动指定，高权重）
    PRIMARY_KEYWORDS: List[str] = []
    PRIMARY_KEYWORD_WEIGHT: float = 1.0
    PRIMARY_KEYWORD_WEIGHTS: Dict[str, float] = Field(default_factory=dict)
    PRIMARY_KEYWORD_WEIGHTS_EXPLICIT: bool = False

    # 是否启用从参考文献提取关键词
    ENABLE_REFERENCE_EXTRACTION: bool = False

    # Reference 关键词配置
    MAX_REFERENCE_KEYWORDS: int = 12
    SIMILARITY_THRESHOLD: float = 0.75  # 关键词相似度阈值
    REFERENCE_WEIGHT_HIGH: float = 0.8
    REFERENCE_WEIGHT_MEDIUM: float = 0.5
    REFERENCE_WEIGHT_LOW: float = 0.3
    REFERENCE_COUNT_HIGH: int = 3
    REFERENCE_COUNT_MEDIUM: int = 6
    REFERENCE_COUNT_LOW: int = 3

    # 研究背景上下文
    RESEARCH_CONTEXT: str = ""

    # ==================== 关键词追踪配置 ====================
    KEYWORD_TRACKER_ENABLED: bool = True
    KEYWORD_DB_PATH: Path = DATA_DIR / "keywords" / "keywords.db"
    KEYWORD_NORMALIZATION_ENABLED: bool = True
    KEYWORD_NORMALIZATION_BATCH_SIZE: int = 50
    # Keyword normalization is a background workload.  It uses the low-cost
    # model by default, but installations that value normalization quality can
    # explicitly route it to the high-capability model.
    KEYWORD_NORMALIZATION_LLM_ROLE: str = "cheap"
    KEYWORD_TREND_DEFAULT_DAYS: int = 30
    KEYWORD_CHART_TOP_N: int = 15
    KEYWORD_TREND_TOP_N: int = 5
    KEYWORD_REPORT_ENABLED: bool = True
    KEYWORD_REPORT_FREQUENCY: str = "weekly"  # daily, weekly, monthly, always

    # ==================== 通知配置 ====================
    ENABLE_NOTIFICATIONS: bool = False

    # SMTP 邮件配置
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""  # 发件人地址，默认使用 SMTP_USER
    SMTP_TO: str = ""  # 收件人地址，逗号分隔多个
    SMTP_USE_TLS: bool = True

    # Webhook 配置
    WECHAT_WEBHOOK_URL: str = ""  # 企业微信机器人 Webhook URL
    DINGTALK_WEBHOOK_URL: str = ""  # 钉钉机器人 Webhook URL
    DINGTALK_SECRET: str = ""  # 钉钉机器人签名密钥（可选）
    TELEGRAM_BOT_TOKEN: str = ""  # Telegram Bot Token
    TELEGRAM_CHAT_ID: str = ""  # Telegram Chat ID
    SLACK_WEBHOOK_URL: str = ""  # Slack Incoming Webhook URL
    GENERIC_WEBHOOK_URL: str = ""  # 通用 Webhook URL

    # 通知偏好
    NOTIFY_ON_SUCCESS: bool = True  # 成功时发送通知
    NOTIFY_ON_FAILURE: bool = True  # 失败时发送通知
    NOTIFY_ATTACH_REPORTS: bool = False  # 邮件是否附带报告文件

    # 各渠道独立开关（需同时在 .env 中配置对应密钥才会生效）
    NOTIFY_EMAIL_ENABLED: bool = True
    NOTIFY_WECHAT_ENABLED: bool = True
    NOTIFY_DINGTALK_ENABLED: bool = True
    NOTIFY_TELEGRAM_ENABLED: bool = True
    NOTIFY_SLACK_ENABLED: bool = True
    NOTIFY_GENERIC_WEBHOOK_ENABLED: bool = True

    # ==================== 重试配置 ====================
    RETRY_MAX_ATTEMPTS: int = 3  # 最大重试次数
    RETRY_MIN_WAIT: int = 2  # 最小等待时间（秒），指数退避起始值
    RETRY_MAX_WAIT: int = 30  # 最大等待时间（秒）

    # ==================== 日志配置 ====================
    LOG_KEEP_DAYS: int = 30  # 日志保留天数
    LOG_ROTATION_TYPE: str = "time"  # "time" (按天轮换) 或 "size" (按大小轮换)

    # ==================== 并发配置 ====================
    ENABLE_CONCURRENCY: bool = False  # 是否启用并发
    CONCURRENCY_WORKERS: int = 3  # 并发线程数（建议不超过5）

    # ==================== LLM 请求池配置 ====================
    LLM_REQUEST_POOL_ENABLED: bool = True  # 是否启用全局 LLM 请求限速
    LLM_REQUESTS_PER_MINUTE: int = 30  # 全局每分钟 LLM 请求上限
    LLM_REQUEST_POOL_LOG_SLOW_WAIT_SECONDS: float = 5.0  # 等待超过该秒数时记录日志

    # ==================== LLM 超时与重试配置 ====================
    # 低并发/低 TPS 的中转供应商很容易超时或限流，所有 OpenAI 客户端
    # 共享这里的边界：单请求超时、SDK 层快速重试、应用层指数退避重试。
    LLM_TIMEOUT_SECONDS: float = 300.0  # 单次 LLM HTTP 请求超时（秒）
    LLM_SDK_MAX_RETRIES: int = 1  # OpenAI SDK 内部重试次数（连接抖动/Retry-After）
    LLM_RETRY_MAX_ATTEMPTS: int = 5  # 应用层最大尝试次数
    LLM_RETRY_MIN_WAIT: int = 5  # 应用层退避起始等待（秒）
    LLM_RETRY_MAX_WAIT: int = 120  # 应用层退避等待上限（秒）

    # ==================== 报告配置 ====================
    ENABLE_HTML_REPORT: bool = True  # 是否同时生成HTML格式报告
    ENABLE_MARKDOWN_REPORT: bool = True  # 是否生成Markdown格式报告
    TOKEN_TRACKING_ENABLED: bool = True  # 是否在报告和通知中显示 token 消耗统计

    # ==================== Daily Research 模式配置 ====================
    DAILY_ENABLE_DEEP_ANALYSIS: bool = True  # 是否在每日研究模式中执行深度分析
    # Compatibility field only. SQLite is mandatory for daily research.
    DAILY_RESEARCH_PERSISTENCE_ENABLED: bool = True
    DAILY_RESEARCH_DB_PATH: Path = DATA_DIR / "daily_research" / "daily_research.db"
    # 0 means process the complete pending queue. The default 200 protects a
    # fresh deployment whose first scan collects weeks of backlog; the excess
    # stays queued and is drained by subsequent runs, after which a normal
    # day's new papers all fit below the cap.
    DAILY_MAX_PAPERS_PER_RUN: int = 200
    # 每日研究运行时间（HH:MM，本地时区）。entrypoint 在容器启动时据此安装
    # cron；显式设置的 CRON_SCHEDULE 环境变量优先于该值。
    DAILY_RUN_TIME: str = "12:00"

    # Historical repair, legacy supplements and omission-report generation
    # have their own workload budget. Older config files fall back to the
    # daily cap when loaded so upgrading does not unexpectedly change work.
    HISTORY_MAINTENANCE_MAX_PAPERS_PER_RUN: int = 200

    # v3.2 archives can be imported in a lightweight ledger-only mode.  The
    # optional full workflow additionally repairs missing SQLite fields and
    # scans the covered historical range for omitted arXiv papers.
    LEGACY_IMPORT_FULL_REPAIR_ENABLED: bool = False

    # ==================== PDF 解析配置 ====================
    PDF_PARSER_MODE: str = "pymupdf"  # PDF 解析模式: "pymupdf" (本地解析) 或 "mineru" (云端API)
    MINERU_API_KEY: str = ""  # MinerU API Token
    MINERU_MODEL_VERSION: str = "pipeline"  # MinerU 模型版本: pipeline 或 vlm
    MINERU_POLL_INTERVAL: int = 3  # MinerU 任务状态轮询间隔（秒）
    MINERU_POLL_TIMEOUT: int = 300  # MinerU 任务超时时间（秒）
    # PDF URL fields come from external metadata. Keep local downloads
    # bounded even when an upstream server omits or lies about Content-Length.
    PDF_DOWNLOAD_MAX_BYTES: int = 50 * 1024 * 1024

    # ==================== 版本更新通知配置 ====================
    # 仅检测 GitHub Release 并发送提醒；绝不由运行中的程序自行拉取代码或重启容器。
    AUTO_UPDATE_ENABLED: bool = True

    # ==================== 网络代理配置 ====================
    PROXY_ENABLED: bool = False  # 是否启用网络代理
    PROXY_URL: str = ""  # 代理地址，如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080
    PROXY_NO_PROXY: str = "localhost,127.0.0.1"  # 不使用代理的地址列表
    # 各服务独立代理开关
    PROXY_ARXIV: bool = True  # ArXiv API 是否使用代理
    PROXY_OPENALEX: bool = False  # OpenAlex API 是否使用代理
    PROXY_HUGGINGFACE_PAPERS: bool = False  # Hugging Face Papers API 是否使用代理
    PROXY_SEMANTIC_SCHOLAR: bool = False  # Semantic Scholar API 是否使用代理
    PROXY_LLM_API: bool = False  # LLM API 是否使用代理
    PROXY_NOTIFICATIONS: bool = False  # 通知 Webhook 是否使用代理
    # Keep the historic global-proxy behavior for WebDAV unless the user
    # explicitly turns its new per-service toggle off.
    PROXY_WEBDAV: bool = True  # WebDAV 同步是否使用代理
    PROXY_UPDATE_CHECK: bool = False  # 检查更新是否使用代理

    # ==================== WebDAV 同步配置 ====================
    WEBDAV_ENABLED: bool = False  # 是否启用 WebDAV 同步
    WEBDAV_URL: str = ""  # WebDAV 服务器地址（从 .env 加载）
    WEBDAV_USERNAME: str = ""  # WebDAV 用户名（从 .env 加载）
    WEBDAV_PASSWORD: str = ""  # WebDAV 密码（从 .env 加载）
    WEBDAV_REMOTE_PATH: str = "/arxiv-daily-researcher/"  # 远程存储根路径
    WEBDAV_SYNC_MODE: str = "after_report"  # 同步模式: manual / scheduled / after_report
    WEBDAV_CRON_SCHEDULE: str = "0 23 * * *"  # 定时同步 cron 表达式
    WEBDAV_SYNC_CONFIGS: bool = True  # 是否同步配置文件
    # Compatibility name: this now synchronizes only the authoritative SQLite
    # history. v3.2 JSON files remain local legacy-import input and are never
    # used by normal runtime/WebDAV sync.
    WEBDAV_SYNC_HISTORY: bool = True
    WEBDAV_SYNC_KEYWORDS: bool = True  # 是否同步关键词数据
    WEBDAV_SYNC_REPORTS: bool = False  # 是否同步报告（体积较大）

    # ==================== 数据库备份配置 ====================
    BACKUP_ENABLED: bool = True  # 每日运行结束后自动做一次 gzip 数据库备份
    # 本地全量备份按年龄自动清理；0 表示永久保留。WebDAV 增量归档始终不删除。
    BACKUP_LOCAL_RETENTION_DAYS: int = 7
    # 当天可保留的本地快照上限；0 表示保留当天全部备份。
    BACKUP_LOCAL_SAME_DAY_MAX_COUNT: int = 0

    # ==================== 运行锁配置 ====================
    RUN_LOCK_MAX_AGE_HOURS: int = 12  # 锁超龄告警阈值（小时），不会按 PID 自动终止任务

    # ==================== 通知扩展 ====================
    NOTIFICATION_TOP_N: int = 5  # 通知中包含的Top-N高分论文数量

    # ==================== 搜索扩展 ====================
    MAX_RESULTS_PER_SOURCE: Dict[str, int] = {}  # 旧配置兼容字段；日报不使用

    # ==================== 评分配置 ====================
    # 关键词相关度评分
    MAX_SCORE_PER_KEYWORD: int = 10

    # 作者附加分
    ENABLE_AUTHOR_BONUS: bool = True
    EXPERT_AUTHORS: List[str] = []
    AUTHOR_BONUS_POINTS: float = 5.0
    AUTHOR_BONUS_BY_AUTHOR: Dict[str, float] = Field(default_factory=dict)
    AUTHOR_BONUS_BY_AUTHOR_EXPLICIT: bool = False

    # 动态及格分公式参数
    PASSING_SCORE_BASE: float = 3.0
    PASSING_SCORE_WEIGHT_COEFFICIENT: float = 2.5

    # 评分策略。legacy_weighted_keyword_v1 保留旧的加权总分判定，
    # core_relevance_v2 将内容资格与排序偏好分离。新安装默认 V2；
    # 旧 config.json 未声明策略时仍按 legacy 读取，确保可逆升级。
    # Keep an existing config file that predates ``strategy`` on its original
    # semantics.  Newly generated configs explicitly select V2 in config_io.
    SCORE_STRATEGY: str = "legacy_weighted_keyword_v1"
    # V2: 相关性是主关键词（缺失时安全降级到全部关键词）的 0..max 分
    # 加权平均。该门槛从不因参考关键词数量改变。
    CORE_RELEVANCE_THRESHOLD: float = 6.0
    # V2: 至少一个核心关键词达到此分数，防止多个弱关联累积成推荐。
    CORE_KEYWORD_MIN_SCORE: float = 7.0
    # V2: 参考关键词只给已合格论文的排序带来有限辅助，不参与资格。
    REFERENCE_RANKING_WEIGHT: float = 0.25
    # 学习模式（learned_preference_v1）：学习到的关键词/作者权重先限幅
    # 再乘以衰减系数，保证单个学习项的影响低于直接配置的评分关键词。
    LEARNED_WEIGHT_DAMPENING: float = 0.5
    LEARNED_TERM_WEIGHT_CAP: float = 2.0

    # 报告配置
    INCLUDE_ALL_IN_REPORT: bool = True

    # ==================== LLM配置 ====================
    # 低成本LLM：用于快速初步筛选和关键词生成
    CHEAP_LLM: LLMConfig = Field(default_factory=lambda: LLMConfig(api_key="sk-dummy"))
    # 高性能LLM：用于深层论文分析和内容理解
    SMART_LLM: LLMConfig = Field(default_factory=lambda: LLMConfig(api_key="sk-dummy"))

    # ==================== 研究趋势模式配置 ====================
    RESEARCH_REPORTS_DIR: Path = DATA_DIR / "reports" / "trend_research"  # 研究趋势报告存储路径
    RESEARCH_DEFAULT_DATE_RANGE_DAYS: int = 365  # 默认搜索时间范围（天）
    RESEARCH_MAX_RESULTS: int = 500  # 最大论文数（安全上限）
    RESEARCH_SORT_ORDER: str = "ascending"  # 时间排序："ascending"(旧→新) 或 "descending"(新→旧)
    RESEARCH_REPORT_POSITION: str = "end"  # 趋势分析在报告中的位置："beginning" 或 "end"
    RESEARCH_GENERATE_TLDR: bool = True  # 是否为每篇论文生成 LLM TLDR
    RESEARCH_TLDR_BATCH_SIZE: int = 10  # TLDR 批量并发大小
    RESEARCH_OUTPUT_FORMATS: List[str] = ["markdown", "html"]  # 输出格式
    # 综合分析是趋势研究的固定阶段。此兼容字段仍会写入旧配置，以便
    # 已部署实例平滑升级，但不再作为用户可关闭的开关。
    RESEARCH_ENABLED_SKILLS: List[str] = [
        "comprehensive_analysis",
    ]
    RESEARCH_ANALYSIS_PROMPT: str = ""  # 综合分析模板文本；空则使用内置模板

    # ==================== Pydantic Settings配置 ====================
    # 指定从.env文件加载配置，支持嵌套参数用双下划线分隔
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # 嵌套配置使用__分隔符，如CHEAP_LLM__API_KEY
        extra="ignore",  # 忽略.env中未定义的额外参数
    )

    def load_from_search_config(self, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        从 runtime/config.json 加载配置并覆盖默认值。

        注意：LLM 配置完全从 .env 文件加载，不从此配置文件加载。

        参数:
            config_path: 配置文件路径，默认为 PROJECT_ROOT/runtime/config.json。
                首次升级时会从旧的 configs/config.json 复制一份，旧文件保留。

        返回:
            dict: 配置字典
        """
        if config_path is None:
            try:
                config_path = ensure_runtime_config_path(self.PROJECT_ROOT)
            except ConfigMigrationError as exc:
                raise ConfigurationLoadError(str(exc)) from exc
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            print(f"警告: 未找到配置文件 {config_path}，使用默认配置")
            return {}

        # Configuration application below performs many field assignments.
        # Keep a deep snapshot so a malformed later section cannot leave this
        # long-lived settings singleton partly configured when a caller catches
        # the startup error (for example, a WebUI validation view).
        original_settings = self.model_copy(deep=True)

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json5.load(f)  # 使用json5支持注释
            if not isinstance(config, dict):
                raise ValueError("配置文件根节点必须是 JSON 对象")

            # 加载搜索设置
            if "search_settings" in config:
                # ``search_days``/``max_results``/``max_results_per_source``
                # are accepted in legacy files but deliberately ignored.  The
                # daily window is a fixed 3-day lookback; older papers are
                # handled by the past-date backfill mode.
                pass

            # 加载目标领域
            if "target_domains" in config:
                domains = config["target_domains"].get("domains", [])
                # Preserve an explicit empty list.  SearchAgent validates it
                # fail-closed when arXiv is enabled; silently retaining the
                # old default here would make a user believe they had changed
                # the scan scope while the worker kept querying quant-ph.
                if domains is not None:
                    self.TARGET_DOMAINS = domains

            # 加载数据源配置
            if "data_sources" in config:
                ds_config = config["data_sources"]
                if not isinstance(ds_config, dict):
                    raise ValueError("data_sources 必须是对象")
                configured_enabled = ds_config.get("enabled", ["arxiv"])
                if not isinstance(configured_enabled, list):
                    raise ValueError("data_sources.enabled 必须是列表")
                legacy_journals = ds_config.get("journals", [])
                if not isinstance(legacy_journals, list):
                    raise ValueError("data_sources.journals 必须是列表")

                normalized_configured = []
                for source in configured_enabled:
                    if not isinstance(source, str) or not source.strip():
                        raise ValueError("data_sources.enabled 只能包含非空字符串")
                    source_code = source.strip().lower()
                    if source_code not in normalized_configured:
                        normalized_configured.append(source_code)

                normalized_legacy_journals = []
                for source in legacy_journals:
                    if not isinstance(source, str) or not source.strip():
                        raise ValueError("data_sources.journals 只能包含非空字符串")
                    source_code = source.strip().lower()
                    if source_code not in normalized_legacy_journals:
                        normalized_legacy_journals.append(source_code)

                self.REPORTS_BY_SOURCE = ds_config.get("reports_by_source", True)
                if "extra_sources" in ds_config:
                    # In the v4 format this switch is authoritative. Stale
                    # ``enabled``/``journals`` values must not reactivate an
                    # explicitly disabled extra source.
                    extra_sources = ds_config.get("extra_sources")
                    if extra_sources is None:
                        extra_sources = {}
                    if not isinstance(extra_sources, dict):
                        raise ValueError("data_sources.extra_sources 必须是对象")
                    requested_extra_sources_enabled = extra_sources.get("enabled", False)
                    if not isinstance(requested_extra_sources_enabled, bool):
                        raise ValueError("data_sources.extra_sources.enabled 必须是布尔值")
                    self.EXTRA_SOURCE_DEFINITIONS = validate_source_definitions(
                        extra_sources.get("definitions", [])
                    )
                    # ``prl`` is retained as a core registry code for
                    # backwards compatibility, but the v4 WebUI presents it
                    # inside the extra-source group.  A checked switch with
                    # neither PRL nor a declarative definition is therefore
                    # a no-op and must behave exactly like an unchecked one.
                    # This prevents a misleading "enabled" state from being
                    # carried into workers, backfills, or omission scans.
                    has_selected_extra_source = bool(
                        self.EXTRA_SOURCE_DEFINITIONS
                    ) or "prl" in normalized_configured
                    self.EXTRA_SOURCES_ENABLED = bool(
                        requested_extra_sources_enabled and has_selected_extra_source
                    )
                    core_sources = [
                        source
                        for source in normalized_configured
                        if source == "arxiv"
                    ]
                    if self.EXTRA_SOURCES_ENABLED and "prl" in normalized_configured:
                        core_sources.append("prl")
                else:
                    # One-time in-memory compatibility for pre-v4 source
                    # lists. Saving through the wizard/WebUI writes the new
                    # declarative shape; no executable source code is loaded.
                    legacy_scope = [
                        *normalized_configured,
                        *normalized_legacy_journals,
                    ]
                    core_sources = []
                    legacy_extra_codes = []
                    for source in legacy_scope:
                        if source in CORE_SOURCE_CODES:
                            if source not in core_sources:
                                core_sources.append(source)
                        elif source not in legacy_extra_codes:
                            legacy_extra_codes.append(source)
                    self.EXTRA_SOURCE_DEFINITIONS = definitions_for_builtin_codes(
                        legacy_extra_codes
                    )
                    self.EXTRA_SOURCES_ENABLED = bool(
                        self.EXTRA_SOURCE_DEFINITIONS or "prl" in core_sources
                    )

                self.ENABLED_SOURCES = list(core_sources)
                if self.EXTRA_SOURCES_ENABLED:
                    for source_code in source_codes_from_definitions(
                        self.EXTRA_SOURCE_DEFINITIONS
                    ):
                        if source_code not in self.ENABLED_SOURCES:
                            self.ENABLED_SOURCES.append(source_code)
                # v4 expresses every source in ``enabled`` plus the
                # declarative block. Keeping the legacy journal side channel
                # populated would let it bypass the master switch.
                self.TARGET_JOURNALS = []
                if "arxiv" in ds_config:
                    arxiv_cfg = ds_config["arxiv"]
                    if isinstance(arxiv_cfg, dict):
                        self.ARXIV_FETCH_TIMEOUT_SECONDS = arxiv_cfg.get(
                            "fetch_timeout_seconds", self.ARXIV_FETCH_TIMEOUT_SECONDS
                        )
                        self.ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS = arxiv_cfg.get(
                            "announcement_lookback_grace_days",
                            self.ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS,
                        )
                if "huggingface_papers" in ds_config:
                    hf_cfg = ds_config["huggingface_papers"]
                    if isinstance(hf_cfg, dict):
                        self.HUGGINGFACE_PAPERS_AVAILABILITY_LAG_DAYS = hf_cfg.get(
                            "availability_lag_days",
                            self.HUGGINGFACE_PAPERS_AVAILABILITY_LAG_DAYS,
                        )
                        self.HUGGINGFACE_PAPERS_LOOKBACK_GRACE_DAYS = hf_cfg.get(
                            "lookback_grace_days",
                            self.HUGGINGFACE_PAPERS_LOOKBACK_GRACE_DAYS,
                        )
                        self.HUGGINGFACE_PAPERS_REQUEST_TIMEOUT_SECONDS = hf_cfg.get(
                            "request_timeout_seconds",
                            self.HUGGINGFACE_PAPERS_REQUEST_TIMEOUT_SECONDS,
                        )
                        self.HUGGINGFACE_PAPERS_REQUEST_INTERVAL_SECONDS = hf_cfg.get(
                            "request_interval_seconds",
                            self.HUGGINGFACE_PAPERS_REQUEST_INTERVAL_SECONDS,
                        )

            # 加载关键词配置
            if "keywords" in config:
                kw_config = config["keywords"]

                # 主要关键词
                if "primary_keywords" in kw_config:
                    pk = kw_config["primary_keywords"]
                    if not isinstance(pk, dict):
                        raise ValueError("keywords.primary_keywords 必须是对象")
                    self.PRIMARY_KEYWORD_WEIGHT = pk.get("weight", 1.0)
                    self.PRIMARY_KEYWORD_WEIGHTS_EXPLICIT = "entries" in pk
                    self.PRIMARY_KEYWORDS, self.PRIMARY_KEYWORD_WEIGHTS = _weighted_entries_from_config(
                        pk.get("entries"),
                        name_key="keyword",
                        value_key="weight",
                        fallback_names=pk.get("keywords", []),
                        fallback_value=self.PRIMARY_KEYWORD_WEIGHT,
                        label="主关键词",
                    )

                # Reference 提取配置
                self.ENABLE_REFERENCE_EXTRACTION = kw_config.get(
                    "enable_reference_extraction", False
                )

                if "reference_keywords_config" in kw_config:
                    ref_cfg = kw_config["reference_keywords_config"]
                    self.MAX_REFERENCE_KEYWORDS = ref_cfg.get("max_keywords", 12)
                    self.SIMILARITY_THRESHOLD = ref_cfg.get("similarity_threshold", 0.75)

                    weight_dist = ref_cfg.get("weight_distribution", {})
                    if "high_importance" in weight_dist:
                        self.REFERENCE_WEIGHT_HIGH = weight_dist["high_importance"].get(
                            "weight", 0.8
                        )
                        self.REFERENCE_COUNT_HIGH = weight_dist["high_importance"].get("count", 3)
                    if "medium_importance" in weight_dist:
                        self.REFERENCE_WEIGHT_MEDIUM = weight_dist["medium_importance"].get(
                            "weight", 0.5
                        )
                        self.REFERENCE_COUNT_MEDIUM = weight_dist["medium_importance"].get(
                            "count", 6
                        )
                    if "low_importance" in weight_dist:
                        self.REFERENCE_WEIGHT_LOW = weight_dist["low_importance"].get("weight", 0.3)
                        self.REFERENCE_COUNT_LOW = weight_dist["low_importance"].get("count", 3)

                # 研究背景
                self.RESEARCH_CONTEXT = kw_config.get("research_context", "")

            # 加载评分设置
            if "scoring_settings" in config:
                score_cfg = config["scoring_settings"]

                # 关键词相关度评分
                if "keyword_relevance_score" in score_cfg:
                    self.MAX_SCORE_PER_KEYWORD = score_cfg["keyword_relevance_score"].get(
                        "max_score_per_keyword", 10
                    )

                # 作者附加分
                if "author_bonus" in score_cfg:
                    ab = score_cfg["author_bonus"]
                    if not isinstance(ab, dict):
                        raise ValueError("scoring_settings.author_bonus 必须是对象")
                    self.ENABLE_AUTHOR_BONUS = ab.get("enabled", True)
                    self.AUTHOR_BONUS_POINTS = ab.get("bonus_points", 5.0)
                    self.AUTHOR_BONUS_BY_AUTHOR_EXPLICIT = "entries" in ab
                    self.EXPERT_AUTHORS, self.AUTHOR_BONUS_BY_AUTHOR = _weighted_entries_from_config(
                        ab.get("entries"),
                        name_key="author",
                        value_key="points",
                        fallback_names=ab.get("expert_authors", []),
                        fallback_value=self.AUTHOR_BONUS_POINTS,
                        label="作者加分",
                    )

                # 动态及格分公式
                if "passing_score_formula" in score_cfg:
                    psf = score_cfg["passing_score_formula"]
                    self.PASSING_SCORE_BASE = psf.get("base_score", 3.0)
                    self.PASSING_SCORE_WEIGHT_COEFFICIENT = psf.get("weight_coefficient", 2.5)

                strategy_cfg = score_cfg.get("strategy")
                if isinstance(strategy_cfg, dict):
                    self.SCORE_STRATEGY = strategy_cfg.get(
                        "id", self.SCORE_STRATEGY
                    )
                    self.CORE_RELEVANCE_THRESHOLD = strategy_cfg.get(
                        "core_relevance_threshold", self.CORE_RELEVANCE_THRESHOLD
                    )
                    self.CORE_KEYWORD_MIN_SCORE = strategy_cfg.get(
                        "core_keyword_min_score", self.CORE_KEYWORD_MIN_SCORE
                    )
                    self.REFERENCE_RANKING_WEIGHT = strategy_cfg.get(
                        "reference_ranking_weight", self.REFERENCE_RANKING_WEIGHT
                    )
                    self.LEARNED_WEIGHT_DAMPENING = strategy_cfg.get(
                        "learned_weight_dampening",
                        self.LEARNED_WEIGHT_DAMPENING,
                    )
                    self.LEARNED_TERM_WEIGHT_CAP = strategy_cfg.get(
                        "learned_term_weight_cap", self.LEARNED_TERM_WEIGHT_CAP
                    )

                # 报告配置
                self.INCLUDE_ALL_IN_REPORT = score_cfg.get("include_all_in_report", True)

            # 加载路径配置
            if "paths" in config:
                paths = config["paths"]
                if "data_dir" in paths:
                    self.DATA_DIR = resolve_project_relative_path(
                        self.PROJECT_ROOT, paths["data_dir"], label="paths.data_dir"
                    )
                    # A custom data root is expected to move the complete
                    # default state tree.  Explicit sibling path settings
                    # below still win, but keeping implicit defaults under the
                    # new root avoids splitting history, reports and SQLite
                    # delivery state across two unrelated directories.
                    self.REF_PDF_DIR = self.DATA_DIR / "reference_pdfs"
                    self.REPORTS_DIR = self.DATA_DIR / "reports"
                    self.RESEARCH_REPORTS_DIR = self.REPORTS_DIR / "trend_research"
                    self.DOWNLOAD_DIR = self.DATA_DIR / "downloaded_pdfs"
                    self.HISTORY_FILE = self.DATA_DIR / "history.json"
                    self.HISTORY_DIR = self.DATA_DIR / "history"
                    self.KEYWORD_DB_PATH = self.DATA_DIR / "keywords" / "keywords.db"
                    self.DAILY_RESEARCH_DB_PATH = self.DATA_DIR / "daily_research" / "daily_research.db"
                if "reference_pdfs" in paths:
                    self.REF_PDF_DIR = resolve_project_relative_path(
                        self.PROJECT_ROOT,
                        paths["reference_pdfs"],
                        label="paths.reference_pdfs",
                    )
                if "reports" in paths:
                    self.REPORTS_DIR = resolve_project_relative_path(
                        self.PROJECT_ROOT, paths["reports"], label="paths.reports"
                    )
                    self.RESEARCH_REPORTS_DIR = self.REPORTS_DIR / "trend_research"
                if "downloaded_pdfs" in paths:
                    self.DOWNLOAD_DIR = resolve_project_relative_path(
                        self.PROJECT_ROOT,
                        paths["downloaded_pdfs"],
                        label="paths.downloaded_pdfs",
                    )
                if "history_file" in paths:
                    self.HISTORY_FILE = resolve_project_relative_path(
                        self.PROJECT_ROOT,
                        paths["history_file"],
                        label="paths.history_file",
                    )
                if "history_dir" in paths:
                    self.HISTORY_DIR = resolve_project_relative_path(
                        self.PROJECT_ROOT, paths["history_dir"], label="paths.history_dir"
                    )

            # 加载关键词追踪配置
            if "keyword_tracker" in config:
                kt = config["keyword_tracker"]
                self.KEYWORD_TRACKER_ENABLED = kt.get("enabled", True)

                if "database" in kt:
                    db_path = kt["database"].get("path", "data/keywords/keywords.db")
                    self.KEYWORD_DB_PATH = resolve_project_relative_path(
                        self.PROJECT_ROOT,
                        db_path,
                        label="keyword_tracker.database.path",
                    )

                if "normalization" in kt:
                    norm = kt["normalization"]
                    self.KEYWORD_NORMALIZATION_ENABLED = norm.get("enabled", True)
                    self.KEYWORD_NORMALIZATION_BATCH_SIZE = norm.get("batch_size", 25)
                    role = str(norm.get("llm_role", "cheap") or "cheap").strip().lower()
                    if role not in {"cheap", "smart"}:
                        role = "cheap"
                    self.KEYWORD_NORMALIZATION_LLM_ROLE = role

                if "trend_view" in kt:
                    self.KEYWORD_TREND_DEFAULT_DAYS = kt["trend_view"].get("default_days", 30)

                if "charts" in kt:
                    charts = kt["charts"]
                    if "bar_chart" in charts:
                        self.KEYWORD_CHART_TOP_N = charts["bar_chart"].get("top_n", 15)
                    if "trend_chart" in charts:
                        self.KEYWORD_TREND_TOP_N = charts["trend_chart"].get("top_n", 5)

                if "report" in kt:
                    report_cfg = kt["report"]
                    self.KEYWORD_REPORT_ENABLED = report_cfg.get("enabled", True)
                    self.KEYWORD_REPORT_FREQUENCY = report_cfg.get("frequency", "weekly")

            # 加载通知配置
            if "notifications" in config:
                notif = config["notifications"]
                self.ENABLE_NOTIFICATIONS = notif.get("enabled", False)
                self.NOTIFY_ON_SUCCESS = notif.get("on_success", True)
                self.NOTIFY_ON_FAILURE = notif.get("on_failure", True)
                self.NOTIFY_ATTACH_REPORTS = notif.get("attach_reports", False)
                self.NOTIFICATION_TOP_N = notif.get("top_n", 5)

                # 各渠道独立开关
                channels = notif.get("channels", {})
                self.NOTIFY_EMAIL_ENABLED = channels.get("email", {}).get("enabled", True)
                self.NOTIFY_WECHAT_ENABLED = channels.get("wechat_work", {}).get("enabled", True)
                self.NOTIFY_DINGTALK_ENABLED = channels.get("dingtalk", {}).get("enabled", True)
                self.NOTIFY_TELEGRAM_ENABLED = channels.get("telegram", {}).get("enabled", True)
                self.NOTIFY_SLACK_ENABLED = channels.get("slack", {}).get("enabled", True)
                self.NOTIFY_GENERIC_WEBHOOK_ENABLED = channels.get("generic_webhook", {}).get(
                    "enabled", True
                )

            # 加载重试配置
            if "retry" in config:
                retry_cfg = config["retry"]
                self.RETRY_MAX_ATTEMPTS = retry_cfg.get("max_attempts", 3)
                self.RETRY_MIN_WAIT = retry_cfg.get("min_wait", 2)
                self.RETRY_MAX_WAIT = retry_cfg.get("max_wait", 30)

            # 加载日志配置
            if "logging" in config:
                log_cfg = config["logging"]
                self.LOG_KEEP_DAYS = log_cfg.get("keep_days", 30)
                self.LOG_ROTATION_TYPE = log_cfg.get("rotation_type", "time")

            # 加载并发配置
            if "concurrency" in config:
                conc_cfg = config["concurrency"]
                self.ENABLE_CONCURRENCY = conc_cfg.get("enabled", False)
                self.CONCURRENCY_WORKERS = conc_cfg.get("workers", 3)

            # 加载 LLM 请求池配置
            if "llm_request_pool" in config:
                pool_cfg = config["llm_request_pool"]
                self.LLM_REQUEST_POOL_ENABLED = pool_cfg.get(
                    "enabled", self.LLM_REQUEST_POOL_ENABLED
                )
                self.LLM_REQUESTS_PER_MINUTE = pool_cfg.get(
                    "requests_per_minute", self.LLM_REQUESTS_PER_MINUTE
                )
                self.LLM_REQUEST_POOL_LOG_SLOW_WAIT_SECONDS = pool_cfg.get(
                    "log_slow_wait_seconds", self.LLM_REQUEST_POOL_LOG_SLOW_WAIT_SECONDS
                )

            # 加载 LLM 超时与重试配置
            if "llm" in config:
                llm_cfg = config["llm"]
                self.LLM_TIMEOUT_SECONDS = float(
                    llm_cfg.get("timeout_seconds", self.LLM_TIMEOUT_SECONDS)
                )
                self.LLM_SDK_MAX_RETRIES = int(
                    llm_cfg.get("sdk_max_retries", self.LLM_SDK_MAX_RETRIES)
                )
                self.LLM_RETRY_MAX_ATTEMPTS = int(
                    llm_cfg.get("retry_max_attempts", self.LLM_RETRY_MAX_ATTEMPTS)
                )
                self.LLM_RETRY_MIN_WAIT = int(
                    llm_cfg.get("retry_min_wait", self.LLM_RETRY_MIN_WAIT)
                )
                self.LLM_RETRY_MAX_WAIT = int(
                    llm_cfg.get("retry_max_wait", self.LLM_RETRY_MAX_WAIT)
                )

            # 加载报告设置
            if "report_settings" in config:
                rpt_cfg = config["report_settings"]
                self.ENABLE_HTML_REPORT = rpt_cfg.get("enable_html_report", False)
                self.ENABLE_MARKDOWN_REPORT = rpt_cfg.get("enable_markdown_report", True)

            # 加载 daily research 模式配置
            if "daily_research" in config:
                daily_cfg = config["daily_research"]
                self.DAILY_ENABLE_DEEP_ANALYSIS = daily_cfg.get(
                    "enable_deep_analysis", True
                )
                # ``persistence_enabled`` was an early migration switch. Exact
                # version delivery and resumable stages now require SQLite, so
                # legacy false values are normalized rather than re-enabling
                # JSON-only history.
                self.DAILY_RESEARCH_PERSISTENCE_ENABLED = True
                max_papers_per_run = daily_cfg.get(
                    "max_papers_per_run", self.DAILY_MAX_PAPERS_PER_RUN
                )
                if (
                    isinstance(max_papers_per_run, bool)
                    or not isinstance(max_papers_per_run, int)
                    or max_papers_per_run < 0
                ):
                    raise ValueError(
                        "daily_research.max_papers_per_run 必须是非负整数（0 表示不限）"
                    )
                self.DAILY_MAX_PAPERS_PER_RUN = max_papers_per_run
                run_time = daily_cfg.get("run_time", self.DAILY_RUN_TIME)
                if not isinstance(run_time, str) or not re.fullmatch(
                    r"\d{1,2}:\d{2}", run_time.strip()
                ):
                    raise ValueError("daily_research.run_time 必须是 HH:MM 格式")
                hour, minute = (int(part) for part in run_time.strip().split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError("daily_research.run_time 超出有效时间范围")
                self.DAILY_RUN_TIME = f"{hour:02d}:{minute:02d}"
                if "db_path" in daily_cfg:
                    self.DAILY_RESEARCH_DB_PATH = resolve_project_relative_path(
                        self.PROJECT_ROOT,
                        daily_cfg["db_path"],
                        label="daily_research.db_path",
                    )

            # 历史维护与每日研究使用独立的论文处理上限。旧配置没有该段时
            # 保持与旧版一致：回退到已经加载的每日研究上限。
            history_maintenance_cfg = config.get("history_maintenance")
            if history_maintenance_cfg is None:
                self.HISTORY_MAINTENANCE_MAX_PAPERS_PER_RUN = (
                    self.DAILY_MAX_PAPERS_PER_RUN
                )
            else:
                if not isinstance(history_maintenance_cfg, dict):
                    raise ValueError("history_maintenance 配置段必须是对象")
                history_limit = history_maintenance_cfg.get(
                    "max_papers_per_run", self.DAILY_MAX_PAPERS_PER_RUN
                )
                if (
                    isinstance(history_limit, bool)
                    or not isinstance(history_limit, int)
                    or history_limit < 0
                ):
                    raise ValueError(
                        "history_maintenance.max_papers_per_run 必须是非负整数（0 表示不限）"
                    )
                self.HISTORY_MAINTENANCE_MAX_PAPERS_PER_RUN = history_limit

            if "legacy_history" in config:
                legacy_cfg = config["legacy_history"]
                if not isinstance(legacy_cfg, dict):
                    raise ValueError("legacy_history 必须是对象")
                full_repair = legacy_cfg.get(
                    "full_repair_enabled", self.LEGACY_IMPORT_FULL_REPAIR_ENABLED
                )
                if not isinstance(full_repair, bool):
                    raise ValueError("legacy_history.full_repair_enabled 必须是布尔值")
                self.LEGACY_IMPORT_FULL_REPAIR_ENABLED = full_repair

            # 加载 PDF 解析配置
            if "pdf_parser" in config:
                pdf_cfg = config["pdf_parser"]
                self.PDF_PARSER_MODE = pdf_cfg.get("mode", "pymupdf")
                self.MINERU_MODEL_VERSION = pdf_cfg.get("mineru_model_version", "pipeline")
                self.MINERU_POLL_INTERVAL = pdf_cfg.get("poll_interval", 3)
                self.MINERU_POLL_TIMEOUT = pdf_cfg.get("poll_timeout", 300)
                self.PDF_DOWNLOAD_MAX_BYTES = pdf_cfg.get(
                    "download_max_bytes", self.PDF_DOWNLOAD_MAX_BYTES
                )
                try:
                    self.PDF_DOWNLOAD_MAX_BYTES = int(self.PDF_DOWNLOAD_MAX_BYTES)
                except (TypeError, ValueError) as exc:
                    raise ValueError("pdf_parser.download_max_bytes 必须是正整数") from exc
                if self.PDF_DOWNLOAD_MAX_BYTES <= 0:
                    raise ValueError("pdf_parser.download_max_bytes 必须是正整数")

            # 加载自动更新配置
            if "auto_update" in config:
                au_cfg = config["auto_update"]
                self.AUTO_UPDATE_ENABLED = au_cfg.get("enabled", True)

            # 加载运行锁配置
            if "run_lock" in config:
                lock_cfg = config["run_lock"]
                self.RUN_LOCK_MAX_AGE_HOURS = lock_cfg.get(
                    "max_age_hours", self.RUN_LOCK_MAX_AGE_HOURS
                )

            # 加载 token 追踪配置
            if "token_tracking" in config:
                tt_cfg = config["token_tracking"]
                self.TOKEN_TRACKING_ENABLED = tt_cfg.get("enabled", True)

            # 加载网络代理配置
            if "proxy" in config:
                px_cfg = config["proxy"]
                self.PROXY_ENABLED = px_cfg.get("enabled", False)
                self.PROXY_URL = px_cfg.get("url", "")
                self.PROXY_NO_PROXY = px_cfg.get("no_proxy", "localhost,127.0.0.1")
                scope = px_cfg.get("scope", {})
                self.PROXY_ARXIV = scope.get("arxiv", True)
                self.PROXY_OPENALEX = scope.get("openalex", False)
                self.PROXY_HUGGINGFACE_PAPERS = scope.get("huggingface_papers", False)
                self.PROXY_SEMANTIC_SCHOLAR = scope.get("semantic_scholar", False)
                self.PROXY_LLM_API = scope.get("llm_api", False)
                self.PROXY_NOTIFICATIONS = scope.get("notifications", False)
                self.PROXY_WEBDAV = scope.get("webdav", True)
                self.PROXY_UPDATE_CHECK = scope.get("update_check", False)

            # 加载 WebDAV 同步配置（仅同步设置，凭据从 .env 加载）
            if "webdav" in config:
                wd_cfg = config["webdav"]
                self.WEBDAV_ENABLED = wd_cfg.get("enabled", False)
                self.WEBDAV_REMOTE_PATH = wd_cfg.get("remote_path", "/arxiv-daily-researcher/")
                self.WEBDAV_SYNC_MODE = wd_cfg.get("sync_mode", "after_report")
                self.WEBDAV_CRON_SCHEDULE = wd_cfg.get("cron_schedule", "0 23 * * *")
                if self.WEBDAV_SYNC_MODE not in {"manual", "scheduled", "after_report"}:
                    raise ValueError(
                        "webdav.sync_mode 必须是 manual、scheduled 或 after_report"
                    )
                if self.WEBDAV_SYNC_MODE == "scheduled":
                    from utils.webdav_sync import validate_cron_schedule

                    self.WEBDAV_CRON_SCHEDULE = validate_cron_schedule(
                        self.WEBDAV_CRON_SCHEDULE
                    )
                self.WEBDAV_SYNC_CONFIGS = wd_cfg.get("sync_configs", True)
                self.WEBDAV_SYNC_HISTORY = wd_cfg.get("sync_history", True)
                self.WEBDAV_SYNC_KEYWORDS = wd_cfg.get("sync_keywords", True)
                self.WEBDAV_SYNC_REPORTS = wd_cfg.get("sync_reports", False)

            # 加载数据库备份配置
            if "backup" in config:
                bk_cfg = config["backup"]
                if not isinstance(bk_cfg, dict):
                    raise ValueError("backup 必须是对象")
                self.BACKUP_ENABLED = bk_cfg.get("enabled", self.BACKUP_ENABLED)
                from utils.backup import (
                    validate_local_backup_retention_days,
                    validate_local_backup_same_day_max_count,
                )

                self.BACKUP_LOCAL_RETENTION_DAYS = (
                    validate_local_backup_retention_days(
                        bk_cfg.get(
                            "local_retention_days",
                            self.BACKUP_LOCAL_RETENTION_DAYS,
                        )
                    )
                )
                self.BACKUP_LOCAL_SAME_DAY_MAX_COUNT = (
                    validate_local_backup_same_day_max_count(
                        bk_cfg.get(
                            "same_day_max_count",
                            self.BACKUP_LOCAL_SAME_DAY_MAX_COUNT,
                        )
                    )
                )
                # Legacy ``keep`` counts are ignored. Local rotation now uses
                # an explicit age window and optional current-day cap; WebDAV
                # keeps every incremental upload.

            # 加载研究趋势模式配置
            if "trend_research" in config:
                tr = config["trend_research"]
                self.RESEARCH_DEFAULT_DATE_RANGE_DAYS = tr.get("default_date_range_days", 365)
                self.RESEARCH_MAX_RESULTS = tr.get("max_results", 500)
                self.RESEARCH_SORT_ORDER = tr.get("sort_order", "ascending")
                self.RESEARCH_REPORT_POSITION = tr.get("report_position", "end")
                self.RESEARCH_GENERATE_TLDR = tr.get("generate_tldr", True)
                self.RESEARCH_TLDR_BATCH_SIZE = tr.get("tldr_batch_size", 10)
                self.RESEARCH_OUTPUT_FORMATS = tr.get("output_formats", ["markdown", "html"])
                # ``enabled_skills`` 是 v4.0 及之前的兼容字段。趋势研究
                # 始终执行综合分析，因此历史配置中的空数组也不能跳过它。
                self.RESEARCH_ENABLED_SKILLS = ["comprehensive_analysis"]
                analysis_prompt = tr.get("analysis_prompt", "")
                self.RESEARCH_ANALYSIS_PROMPT = (
                    analysis_prompt.strip() if isinstance(analysis_prompt, str) else ""
                )

            # Assignments on a Pydantic model are deliberately lightweight in
            # this class.  Validate the final, fully merged setting set in
            # strict mode before accepting it, so wrong JSON types cannot be
            # coerced into a surprising daily scan configuration.
            validated_settings = type(self).model_validate(
                self.model_dump(mode="python", warnings="error"), strict=True
            )
            for field_name in type(self).model_fields:
                object.__setattr__(self, field_name, getattr(validated_settings, field_name))

            return config

        except Exception as exc:
            # Restore every model field before raising.  This includes fields
            # assigned before a later section failed validation.
            for field_name in type(self).model_fields:
                object.__setattr__(self, field_name, getattr(original_settings, field_name))
            raise ConfigurationLoadError(
                f"配置文件 {config_path} 无法安全加载；已拒绝使用默认配置继续运行: {exc}"
            ) from exc

    def get_proxy_dict(self, service: str = "") -> Optional[Dict[str, str]]:
        """
        获取指定服务的代理配置字典，适用于 requests.Session.proxies。

        参数:
            service: 服务名，如 "arxiv"、"openalex"、"semantic_scholar"、"llm_api"、"notifications"
                     空字符串表示仅检查全局开关

        返回:
            Dict[str, str]: 代理字典 {"http": url, "https": url}，未启用时返回 None
        """
        if not self.PROXY_ENABLED or not self.PROXY_URL:
            return None

        # 检查服务级别的开关
        scope_map = {
            "arxiv": self.PROXY_ARXIV,
            "openalex": self.PROXY_OPENALEX,
            "huggingface_papers": self.PROXY_HUGGINGFACE_PAPERS,
            "semantic_scholar": self.PROXY_SEMANTIC_SCHOLAR,
            "llm_api": self.PROXY_LLM_API,
            "notifications": self.PROXY_NOTIFICATIONS,
            "webdav": self.PROXY_WEBDAV,
            "update_check": self.PROXY_UPDATE_CHECK,
        }

        if service and not scope_map.get(service, False):
            return None

        proxy_url = self.PROXY_URL.strip()
        return {
            "http": proxy_url,
            "https": proxy_url,
        }

    def get_merged_keywords(self) -> Dict[str, float]:
        """
        获取合并后的关键词字典（关键词 -> 权重）

        返回:
            dict: {关键词: 权重}
        """
        keywords_dict = {}

        # 添加主要关键词
        for kw in self.PRIMARY_KEYWORDS:
            keywords_dict[kw] = self.PRIMARY_KEYWORD_WEIGHTS.get(
                kw, self.PRIMARY_KEYWORD_WEIGHT
            )

        return keywords_dict

    def calculate_passing_score(self, total_keyword_weight: float) -> float:
        """
        计算动态及格分

        公式: 及格分 = base_score + coefficient × Σ(关键词权重)

        参数:
            total_keyword_weight: 所有关键词权重之和

        返回:
            float: 及格分数
        """
        return (
            self.PASSING_SCORE_BASE + self.PASSING_SCORE_WEIGHT_COEFFICIENT * total_keyword_weight
        )

    def normalized_score_strategy(self) -> str:
        """Return a supported strategy or fail before an LLM request.

        Keeping this validation near settings avoids a typo silently changing
        daily recommendation decisions.  The error is intentionally explicit:
        a malformed policy must fail the run and preserve retryability.
        """
        from scoring_policy import SUPPORTED_SCORE_STRATEGIES

        value = str(self.SCORE_STRATEGY or "").strip()
        if value not in SUPPORTED_SCORE_STRATEGIES:
            raise ValueError(
                "SCORE_STRATEGY 必须是 " + ", ".join(sorted(SUPPORTED_SCORE_STRATEGIES))
            )
        return value

    def ensure_directories(self):
        """
        确保所有必需的目录存在。
        如果目录不存在则自动创建（递归创建上级目录）。
        """
        self.REF_PDF_DIR.mkdir(parents=True, exist_ok=True)
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self.RESEARCH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (self.REPORTS_DIR / "keyword_trend").mkdir(parents=True, exist_ok=True)
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self.DAILY_RESEARCH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.REPORT_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    def load_report_css(self, css_name: str = "html_report.css") -> str:
        """
        加载 HTML 报告的 CSS 样式文件。

        参数:
            css_name: CSS 文件名，默认为 html_report.css

        返回:
            str: CSS 样式字符串，文件不存在时返回空字符串
        """
        css_path = self.REPORT_TEMPLATES_DIR / css_name

        if not css_path.exists():
            print(f"警告: 未找到 CSS 样式文件 {css_path}，将使用空样式")
            return ""

        try:
            with open(css_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"加载 CSS 样式文件 {css_name} 失败: {e}")
            return ""

    def load_report_template(
        self, template_name: str = "basic_report_template.json"
    ) -> Dict[str, Any]:
        """
        加载报告模板配置。

        参数:
            template_name: 模板文件名

        返回:
            dict: 模板配置字典
        """
        template_path = self.REPORT_TEMPLATES_DIR / template_name

        if not template_path.exists():
            print(f"警告: 未找到报告模板文件 {template_path}")
            return {}

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return json5.load(f)  # 使用json5支持注释
        except Exception as e:
            print(f"加载报告模板 {template_name} 失败: {e}")
            return {}


# 实例化全局配置单例对象，应用程序全局共享
settings = Settings()

# 从 runtime/config.json 加载配置（会覆盖默认值；旧路径首次启动时自动迁移）
settings.load_from_search_config()

# 自动创建所有必需的工作目录
settings.ensure_directories()
