import os
import json
import json5  # 用于加载带注释的配置文件
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 1. 定义基础路径：获取当前脚本所在目录作为项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent

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

    优先级：search_config.json > .env文件 > 默认值
    """
    # ==================== 路径配置 ====================
    PROJECT_ROOT: Path = PROJECT_ROOT
    DATA_DIR: Path = PROJECT_ROOT / "data"

    # 核心数据存储目录
    REF_PDF_DIR: Path = DATA_DIR / "reference_pdfs"  # 参考论文PDF存储路径
    REPORTS_DIR: Path = DATA_DIR / "reports"  # 生成的研究报告存储路径

    # 报告模板目录
    REPORT_TEMPLATES_DIR: Path = PROJECT_ROOT / "report_templates"

    # 从Arxiv下载的临时PDF存储目录
    DOWNLOAD_DIR: Path = DATA_DIR / "downloaded_pdfs"

    HISTORY_FILE: Path = DATA_DIR / "history.json"  # 已处理论文的历史记录文件

    # ==================== 搜索配置 ====================
    MAX_RESULTS: int = 100  # 单次搜索的最大返回结果数
    SEARCH_DAYS: int = 7  # 搜索最近N天的论文
    TARGET_DOMAINS: List[str] = ["quant-ph"]  # 目标领域列表

    # ==================== 数据源配置 ====================
    ENABLED_SOURCES: List[str] = ["arxiv"]  # 启用的数据源列表
    TARGET_JOURNALS: List[str] = []  # 目标期刊列表（如 ["prl", "pra"]）
    REPORTS_BY_SOURCE: bool = True  # 是否按数据源分目录存放报告
    HISTORY_DIR: Path = DATA_DIR / "history"  # 历史记录目录

    # OpenAlex 配置
    OPENALEX_EMAIL: str = ""  # OpenAlex 礼貌池邮箱（可选，提高速率限制）
    OPENALEX_API_KEY: str = ""  # OpenAlex API Key（可选，2026年2月后必需）

    # Semantic Scholar 配置
    ENABLE_SEMANTIC_SCHOLAR_TLDR: bool = True  # 是否获取AI生成的TLDR
    SEMANTIC_SCHOLAR_API_KEY: str = ""  # Semantic Scholar API Key（可选）

    # ==================== 关键词配置 ====================
    # 主要关键词（手动指定，高权重）
    PRIMARY_KEYWORDS: List[str] = []
    PRIMARY_KEYWORD_WEIGHT: float = 1.0

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
    KEYWORD_DB_PATH: Path = DATA_DIR / "keywords.db"
    KEYWORD_NORMALIZATION_ENABLED: bool = True
    KEYWORD_NORMALIZATION_BATCH_SIZE: int = 50
    KEYWORD_TREND_DEFAULT_DAYS: int = 30
    KEYWORD_CHART_TOP_N: int = 15
    KEYWORD_TREND_TOP_N: int = 5
    KEYWORD_REPORT_ENABLED: bool = True
    KEYWORD_REPORT_FREQUENCY: str = "weekly"  # daily, weekly, monthly, always

    # ==================== 评分配置 ====================
    # 关键词相关度评分
    MAX_SCORE_PER_KEYWORD: int = 10

    # 作者附加分
    ENABLE_AUTHOR_BONUS: bool = True
    EXPERT_AUTHORS: List[str] = []
    AUTHOR_BONUS_POINTS: float = 5.0

    # 动态及格分公式参数
    PASSING_SCORE_BASE: float = 3.0
    PASSING_SCORE_WEIGHT_COEFFICIENT: float = 2.5

    # 报告配置
    INCLUDE_ALL_IN_REPORT: bool = True

    # ==================== LLM配置 ====================
    # 低成本LLM：用于快速初步筛选和关键词生成
    CHEAP_LLM: LLMConfig = Field(default_factory=lambda: LLMConfig(api_key="sk-dummy"))
    # 高性能LLM：用于深层论文分析和内容理解
    SMART_LLM: LLMConfig = Field(default_factory=lambda: LLMConfig(api_key="sk-dummy"))

    # ==================== Pydantic Settings配置 ====================
    # 指定从.env文件加载配置，支持嵌套参数用双下划线分隔
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # 嵌套配置使用__分隔符，如CHEAP_LLM__API_KEY
        extra="ignore"  # 忽略.env中未定义的额外参数
    )

    def load_from_search_config(self, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        从 search_config.json 加载配置并覆盖默认值。

        注意：LLM 配置完全从 .env 文件加载，不从此配置文件加载。

        参数:
            config_path: 配置文件路径，默认为 PROJECT_ROOT/search_config.json

        返回:
            dict: 配置字典
        """
        if config_path is None:
            config_path = self.PROJECT_ROOT / "search_config.json"

        if not config_path.exists():
            print(f"警告: 未找到配置文件 {config_path}，使用默认配置")
            return {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json5.load(f)  # 使用json5支持注释

            # 加载搜索设置
            if "search_settings" in config:
                settings = config["search_settings"]
                self.SEARCH_DAYS = settings.get("search_days", self.SEARCH_DAYS)
                self.MAX_RESULTS = settings.get("max_results", self.MAX_RESULTS)

            # 加载目标领域
            if "target_domains" in config:
                domains = config["target_domains"].get("domains", [])
                if domains:
                    self.TARGET_DOMAINS = domains

            # 加载数据源配置
            if "data_sources" in config:
                ds_config = config["data_sources"]
                self.ENABLED_SOURCES = ds_config.get("enabled", ["arxiv"])
                self.TARGET_JOURNALS = ds_config.get("journals", [])
                self.REPORTS_BY_SOURCE = ds_config.get("reports_by_source", True)

            # 加载关键词配置
            if "keywords" in config:
                kw_config = config["keywords"]

                # 主要关键词
                if "primary_keywords" in kw_config:
                    pk = kw_config["primary_keywords"]
                    self.PRIMARY_KEYWORDS = pk.get("keywords", [])
                    self.PRIMARY_KEYWORD_WEIGHT = pk.get("weight", 1.0)

                # Reference 提取配置
                self.ENABLE_REFERENCE_EXTRACTION = kw_config.get("enable_reference_extraction", False)

                if "reference_keywords_config" in kw_config:
                    ref_cfg = kw_config["reference_keywords_config"]
                    self.MAX_REFERENCE_KEYWORDS = ref_cfg.get("max_keywords", 12)
                    self.SIMILARITY_THRESHOLD = ref_cfg.get("similarity_threshold", 0.75)

                    weight_dist = ref_cfg.get("weight_distribution", {})
                    if "high_importance" in weight_dist:
                        self.REFERENCE_WEIGHT_HIGH = weight_dist["high_importance"].get("weight", 0.8)
                        self.REFERENCE_COUNT_HIGH = weight_dist["high_importance"].get("count", 3)
                    if "medium_importance" in weight_dist:
                        self.REFERENCE_WEIGHT_MEDIUM = weight_dist["medium_importance"].get("weight", 0.5)
                        self.REFERENCE_COUNT_MEDIUM = weight_dist["medium_importance"].get("count", 6)
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
                    self.ENABLE_AUTHOR_BONUS = ab.get("enabled", True)
                    self.EXPERT_AUTHORS = ab.get("expert_authors", [])
                    self.AUTHOR_BONUS_POINTS = ab.get("bonus_points", 5.0)

                # 动态及格分公式
                if "passing_score_formula" in score_cfg:
                    psf = score_cfg["passing_score_formula"]
                    self.PASSING_SCORE_BASE = psf.get("base_score", 3.0)
                    self.PASSING_SCORE_WEIGHT_COEFFICIENT = psf.get("weight_coefficient", 2.5)

                # 报告配置
                self.INCLUDE_ALL_IN_REPORT = score_cfg.get("include_all_in_report", True)

            # 加载路径配置
            if "paths" in config:
                paths = config["paths"]
                if "data_dir" in paths:
                    self.DATA_DIR = self.PROJECT_ROOT / paths["data_dir"]
                if "reference_pdfs" in paths:
                    self.REF_PDF_DIR = self.PROJECT_ROOT / paths["reference_pdfs"]
                if "reports" in paths:
                    self.REPORTS_DIR = self.PROJECT_ROOT / paths["reports"]
                if "downloaded_pdfs" in paths:
                    self.DOWNLOAD_DIR = self.PROJECT_ROOT / paths["downloaded_pdfs"]
                if "history_file" in paths:
                    self.HISTORY_FILE = self.PROJECT_ROOT / paths["history_file"]

            # 加载关键词追踪配置
            if "keyword_tracker" in config:
                kt = config["keyword_tracker"]
                self.KEYWORD_TRACKER_ENABLED = kt.get("enabled", True)

                if "database" in kt:
                    db_path = kt["database"].get("path", "data/keywords.db")
                    self.KEYWORD_DB_PATH = self.PROJECT_ROOT / db_path

                if "normalization" in kt:
                    norm = kt["normalization"]
                    self.KEYWORD_NORMALIZATION_ENABLED = norm.get("enabled", True)
                    self.KEYWORD_NORMALIZATION_BATCH_SIZE = norm.get("batch_size", 50)

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

            return config

        except Exception as e:
            print(f"加载 search_config.json 失败: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def get_merged_keywords(self) -> Dict[str, float]:
        """
        获取合并后的关键词字典（关键词 -> 权重）

        返回:
            dict: {关键词: 权重}
        """
        keywords_dict = {}

        # 添加主要关键词
        for kw in self.PRIMARY_KEYWORDS:
            keywords_dict[kw] = self.PRIMARY_KEYWORD_WEIGHT

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
        return self.PASSING_SCORE_BASE + self.PASSING_SCORE_WEIGHT_COEFFICIENT * total_keyword_weight

    def ensure_directories(self):
        """
        确保所有必需的目录存在。
        如果目录不存在则自动创建（递归创建上级目录）。
        """
        self.REF_PDF_DIR.mkdir(parents=True, exist_ok=True)
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self.REPORT_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    def load_report_template(self, template_name: str = "basic_report_template.json") -> Dict[str, Any]:
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
            with open(template_path, 'r', encoding='utf-8') as f:
                return json5.load(f)  # 使用json5支持注释
        except Exception as e:
            print(f"加载报告模板 {template_name} 失败: {e}")
            return {}

# 实例化全局配置单例对象，应用程序全局共享
settings = Settings()

# 从 search_config.json 加载配置（会覆盖默认值）
settings.load_from_search_config()

# 自动创建所有必需的工作目录
settings.ensure_directories()