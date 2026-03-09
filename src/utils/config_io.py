"""
Config I/O module - shared read/write logic for .env and configs/config.json.

Used by: src/utils/setup_wizard.py, src/webui/config_panel.py
"""

import json
import json5
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==================== Path Constants ====================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.json"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

# ==================== Data Source Options ====================

ALL_DATA_SOURCES = [
    "arxiv",
    "prl",
    "pra",
    "prb",
    "prc",
    "prd",
    "pre",
    "prx",
    "prxq",
    "rmp",
    "nature",
    "nature_physics",
    "nature_communications",
    "science",
    "science_advances",
    "npj_quantum_information",
    "quantum",
    "new_journal_of_physics",
]

# ==================== LLM Provider Presets ====================

LLM_PROVIDERS = {
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "cheap_model": "gpt-4o-mini",
        "smart_model": "gpt-4o",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "cheap_model": "deepseek-chat",
        "smart_model": "deepseek-chat",
    },
    "Ollama (Local)": {
        "base_url": "http://127.0.0.1:11434/v1",
        "cheap_model": "qwen2.5:7b",
        "smart_model": "qwen2.5:14b",
    },
    "Zhipu AI": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "cheap_model": "glm-4-flash",
        "smart_model": "glm-4",
    },
    "Custom": {
        "base_url": "",
        "cheap_model": "",
        "smart_model": "",
    },
}

# ==================== .env Field Definitions ====================

ENV_FIELDS = [
    # (key, label, is_secret, default_value)
    # LLM - Cheap
    ("CHEAP_LLM__API_KEY", "Low-cost LLM API Key", True, ""),
    ("CHEAP_LLM__BASE_URL", "Low-cost LLM Base URL", False, "https://api.openai.com/v1"),
    ("CHEAP_LLM__MODEL_NAME", "Low-cost LLM Model", False, "gpt-4o-mini"),
    ("CHEAP_LLM__TEMPERATURE", "Low-cost LLM Temperature", False, "0.3"),
    # LLM - Smart
    ("SMART_LLM__API_KEY", "High-perf LLM API Key", True, ""),
    ("SMART_LLM__BASE_URL", "High-perf LLM Base URL", False, "https://api.openai.com/v1"),
    ("SMART_LLM__MODEL_NAME", "High-perf LLM Model", False, "gpt-4o"),
    ("SMART_LLM__TEMPERATURE", "High-perf LLM Temperature", False, "0.3"),
    # Third-party APIs
    ("OPENALEX_EMAIL", "OpenAlex Email", False, ""),
    ("OPENALEX_API_KEY", "OpenAlex API Key", True, ""),
    ("SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar API Key", True, ""),
    ("MINERU_API_KEY", "MinerU API Key", True, ""),
    # SMTP
    ("SMTP_HOST", "SMTP Host", False, ""),
    ("SMTP_PORT", "SMTP Port", False, "587"),
    ("SMTP_USER", "SMTP User", False, ""),
    ("SMTP_PASSWORD", "SMTP Password", True, ""),
    ("SMTP_FROM", "SMTP From Address", False, ""),
    ("SMTP_TO", "SMTP To Addresses", False, ""),
    ("SMTP_USE_TLS", "SMTP Use TLS", False, "true"),
    # Webhooks
    ("WECHAT_WEBHOOK_URL", "WeChat Work Webhook URL", True, ""),
    ("DINGTALK_WEBHOOK_URL", "DingTalk Webhook URL", True, ""),
    ("DINGTALK_SECRET", "DingTalk Secret", True, ""),
    ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token", True, ""),
    ("TELEGRAM_CHAT_ID", "Telegram Chat ID", False, ""),
    ("SLACK_WEBHOOK_URL", "Slack Webhook URL", True, ""),
    ("GENERIC_WEBHOOK_URL", "Generic Webhook URL", True, ""),
]

# ==================== Config JSON Section Comments ====================

SECTION_COMMENTS = {
    "search_settings": "Search Configuration",
    "data_sources": "Data Source Configuration",
    "target_domains": "ArXiv Target Domain Configuration",
    "keywords": "Keyword Configuration",
    "scoring_settings": "Scoring Configuration",
    "paths": "Path Configuration",
    "keyword_tracker": "Keyword Trend Tracking Configuration",
    "notifications": "Notification Configuration",
    "retry": "Retry Configuration",
    "logging": "Logging Configuration",
    "concurrency": "Concurrency Configuration",
    "pdf_parser": "PDF Parser Configuration",
    "report_settings": "Report Configuration",
    "auto_update": "Auto-update Configuration",
    "token_tracking": "Token Tracking Configuration",
    "trend_research": "Trend Research Mode Configuration",
}


# ==================== .env Read / Write ====================


def read_env(path: Optional[Path] = None) -> Dict[str, str]:
    """Read .env file into a flat dict. Skips comments and blank lines."""
    if path is None:
        path = DEFAULT_ENV_PATH
    path = Path(path)
    result = {}
    if not path.exists():
        return result

    content = path.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            # Remove surrounding quotes if present
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value
    return result


def write_env(values: Dict[str, str], path: Optional[Path] = None) -> None:
    """
    Write .env file using .env.example as structural template.

    Active keys are written uncommented, empty keys stay commented.
    Creates .env.bak backup before writing.
    """
    if path is None:
        path = DEFAULT_ENV_PATH
    path = Path(path)

    # Backup existing
    if path.exists():
        shutil.copy2(path, path.parent / ".env.bak")

    # If .env.example exists, use it as template
    if ENV_EXAMPLE_PATH.exists():
        template = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    else:
        # Fallback: just write all values flat
        lines = []
        for key, value in values.items():
            if value:
                lines.append(f"{key}={value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    written_keys = set()
    output_lines = []

    for line in template.splitlines():
        stripped = line.strip()

        # Blank or pure comment lines - preserve as-is
        if not stripped:
            output_lines.append(line)
            continue

        if stripped.startswith("#"):
            # Check if this is a commented-out KEY=VALUE
            comment_body = stripped.lstrip("#").strip()
            if "=" in comment_body:
                potential_key = comment_body.split("=", 1)[0].strip()
                if potential_key in values and values[potential_key]:
                    output_lines.append(f"{potential_key}={values[potential_key]}")
                    written_keys.add(potential_key)
                    continue
            output_lines.append(line)
            continue

        # Active KEY=VALUE line
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                val = values[key]
                if val:
                    output_lines.append(f"{key}={val}")
                else:
                    output_lines.append(f"# {key}=")
                written_keys.add(key)
                continue
            output_lines.append(line)
            continue

        output_lines.append(line)

    # Append any values not in template
    extra = {k: v for k, v in values.items() if k not in written_keys and v}
    if extra:
        output_lines.append("")
        output_lines.append("# Additional configuration")
        for key, val in extra.items():
            output_lines.append(f"{key}={val}")

    path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


# ==================== config.json Read / Write ====================


def read_config_json(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read config.json using json5 (supports comments)."""
    if path is None:
        path = DEFAULT_CONFIG_PATH
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json5.load(f)


def _indent_value(value_str: str, indent_level: int = 2) -> str:
    """Indent all lines of a multi-line JSON value except the first."""
    lines = value_str.split("\n")
    if len(lines) <= 1:
        return value_str
    prefix = " " * indent_level
    result = [lines[0]]
    for line in lines[1:]:
        result.append(prefix + line)
    return "\n".join(result)


def write_config_json(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    """
    Write config.json with section comment headers.

    Inline comments from the original file are lost, but block-level section
    headers are added. Creates .json.bak backup before writing.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH
    path = Path(path)

    # Backup existing
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))

    lines = ["{"]
    keys = list(config.keys())

    for i, key in enumerate(keys):
        # Add section comment header
        if key in SECTION_COMMENTS:
            if i > 0:
                lines.append("")
            lines.append(f"  // {'=' * 50}")
            lines.append(f"  // {SECTION_COMMENTS[key]}")
            lines.append(f"  // {'=' * 50}")

        value_str = json.dumps(config[key], indent=2, ensure_ascii=False)
        indented = _indent_value(value_str, indent_level=2)
        comma = "," if i < len(keys) - 1 else ""
        lines.append(f'  "{key}": {indented}{comma}')

    lines.append("}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ==================== Config Structure Builders ====================


def build_config_dict(
    search_days: int = 7,
    max_results: int = 100,
    max_results_per_source: Optional[Dict[str, int]] = None,
    enabled_sources: Optional[List[str]] = None,
    journals: Optional[List[str]] = None,
    reports_by_source: bool = True,
    domains: Optional[List[str]] = None,
    primary_keywords: Optional[List[str]] = None,
    primary_keyword_weight: float = 1.0,
    enable_reference_extraction: bool = False,
    max_reference_keywords: int = 10,
    similarity_threshold: float = 0.75,
    ref_weight_high: float = 1.0,
    ref_count_high: int = 3,
    ref_weight_medium: float = 0.2,
    ref_count_medium: int = 5,
    ref_weight_low: float = 0.1,
    ref_count_low: int = 2,
    research_context: str = "",
    max_score_per_keyword: int = 10,
    enable_author_bonus: bool = False,
    expert_authors: Optional[List[str]] = None,
    author_bonus_points: float = 5.0,
    passing_score_base: float = 5.0,
    passing_score_weight_coefficient: float = 3.0,
    include_all_in_report: bool = True,
    keyword_tracker_enabled: bool = True,
    keyword_db_path: str = "data/keywords/keywords.db",
    keyword_normalization_enabled: bool = True,
    keyword_normalization_batch_size: int = 25,
    keyword_trend_default_days: int = 30,
    keyword_chart_top_n: int = 15,
    keyword_trend_top_n: int = 5,
    keyword_report_enabled: bool = True,
    keyword_report_frequency: str = "weekly",
    notifications_enabled: bool = False,
    notify_on_success: bool = True,
    notify_on_failure: bool = True,
    notify_attach_reports: bool = False,
    notification_top_n: int = 5,
    notify_email_enabled: bool = False,
    notify_wechat_enabled: bool = False,
    notify_dingtalk_enabled: bool = False,
    notify_telegram_enabled: bool = False,
    notify_slack_enabled: bool = False,
    notify_generic_webhook_enabled: bool = False,
    retry_max_attempts: int = 3,
    retry_min_wait: int = 2,
    retry_max_wait: int = 30,
    log_rotation_type: str = "time",
    log_keep_days: int = 30,
    concurrency_enabled: bool = False,
    concurrency_workers: int = 3,
    pdf_parser_mode: str = "mineru",
    mineru_model_version: str = "pipeline",
    mineru_poll_interval: int = 3,
    mineru_poll_timeout: int = 300,
    enable_html_report: bool = True,
    auto_update_enabled: bool = True,
    token_tracking_enabled: bool = True,
    trend_default_date_range_days: int = 365,
    trend_max_results: int = 500,
    trend_sort_order: str = "ascending",
    trend_report_position: str = "end",
    trend_generate_tldr: bool = True,
    trend_tldr_batch_size: int = 10,
    trend_output_formats: Optional[List[str]] = None,
    trend_enabled_skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a nested config.json dict from flat parameters."""

    config = {
        "search_settings": {
            "search_days": search_days,
            "max_results": max_results,
            "max_results_per_source": max_results_per_source or {},
        },
        "data_sources": {
            "enabled": enabled_sources or ["arxiv"],
            "journals": journals or [],
            "reports_by_source": reports_by_source,
        },
        "target_domains": {
            "domains": domains or ["quant-ph"],
        },
        "keywords": {
            "primary_keywords": {
                "weight": primary_keyword_weight,
                "keywords": primary_keywords or [],
            },
            "enable_reference_extraction": enable_reference_extraction,
            "reference_keywords_config": {
                "max_keywords": max_reference_keywords,
                "similarity_threshold": similarity_threshold,
                "weight_distribution": {
                    "high_importance": {
                        "weight": ref_weight_high,
                        "count": ref_count_high,
                    },
                    "medium_importance": {
                        "weight": ref_weight_medium,
                        "count": ref_count_medium,
                    },
                    "low_importance": {
                        "weight": ref_weight_low,
                        "count": ref_count_low,
                    },
                },
            },
            "research_context": research_context,
        },
        "scoring_settings": {
            "keyword_relevance_score": {
                "max_score_per_keyword": max_score_per_keyword,
            },
            "author_bonus": {
                "enabled": enable_author_bonus,
                "expert_authors": expert_authors or [],
                "bonus_points": author_bonus_points,
            },
            "passing_score_formula": {
                "base_score": passing_score_base,
                "weight_coefficient": passing_score_weight_coefficient,
            },
            "include_all_in_report": include_all_in_report,
        },
        "paths": {
            "data_dir": "data",
            "reference_pdfs": "data/reference_pdfs",
            "reports": "data/reports",
            "downloaded_pdfs": "data/downloaded_pdfs",
            "history_dir": "data/history",
        },
        "keyword_tracker": {
            "enabled": keyword_tracker_enabled,
            "database": {
                "path": keyword_db_path,
            },
            "normalization": {
                "enabled": keyword_normalization_enabled,
                "batch_size": keyword_normalization_batch_size,
            },
            "trend_view": {
                "default_days": keyword_trend_default_days,
            },
            "charts": {
                "bar_chart": {"top_n": keyword_chart_top_n},
                "trend_chart": {"top_n": keyword_trend_top_n},
            },
            "report": {
                "enabled": keyword_report_enabled,
                "frequency": keyword_report_frequency,
            },
        },
        "notifications": {
            "enabled": notifications_enabled,
            "on_success": notify_on_success,
            "on_failure": notify_on_failure,
            "attach_reports": notify_attach_reports,
            "top_n": notification_top_n,
            "channels": {
                "email": {"enabled": notify_email_enabled},
                "wechat_work": {"enabled": notify_wechat_enabled},
                "dingtalk": {"enabled": notify_dingtalk_enabled},
                "telegram": {"enabled": notify_telegram_enabled},
                "slack": {"enabled": notify_slack_enabled},
                "generic_webhook": {"enabled": notify_generic_webhook_enabled},
            },
        },
        "retry": {
            "max_attempts": retry_max_attempts,
            "min_wait": retry_min_wait,
            "max_wait": retry_max_wait,
        },
        "logging": {
            "rotation_type": log_rotation_type,
            "keep_days": log_keep_days,
        },
        "concurrency": {
            "enabled": concurrency_enabled,
            "workers": concurrency_workers,
        },
        "pdf_parser": {
            "mode": pdf_parser_mode,
            "mineru_model_version": mineru_model_version,
            "poll_interval": mineru_poll_interval,
            "poll_timeout": mineru_poll_timeout,
        },
        "report_settings": {
            "enable_html_report": enable_html_report,
        },
        "auto_update": {
            "enabled": auto_update_enabled,
        },
        "token_tracking": {
            "enabled": token_tracking_enabled,
        },
        "trend_research": {
            "default_date_range_days": trend_default_date_range_days,
            "max_results": trend_max_results,
            "sort_order": trend_sort_order,
            "report_position": trend_report_position,
            "generate_tldr": trend_generate_tldr,
            "tldr_batch_size": trend_tldr_batch_size,
            "output_formats": trend_output_formats or ["markdown", "html"],
            "enabled_skills": trend_enabled_skills
            or [
                "temporal_evolution",
                "hot_topics",
                "key_authors",
                "research_gaps",
                "methodology_trends",
            ],
        },
    }

    return config


def flatten_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert nested config.json structure into flat dict for UI display.

    Returns a dict with descriptive keys matching build_config_dict parameters.
    """
    flat = {}

    # Search settings
    ss = config.get("search_settings", {})
    flat["search_days"] = ss.get("search_days", 7)
    flat["max_results"] = ss.get("max_results", 100)
    flat["max_results_per_source"] = ss.get("max_results_per_source", {})

    # Data sources
    ds = config.get("data_sources", {})
    flat["enabled_sources"] = ds.get("enabled", ["arxiv"])
    flat["journals"] = ds.get("journals", [])
    flat["reports_by_source"] = ds.get("reports_by_source", True)

    # Target domains
    td = config.get("target_domains", {})
    flat["domains"] = td.get("domains", ["quant-ph"])

    # Keywords
    kw = config.get("keywords", {})
    pk = kw.get("primary_keywords", {})
    flat["primary_keywords"] = pk.get("keywords", [])
    flat["primary_keyword_weight"] = pk.get("weight", 1.0)
    flat["enable_reference_extraction"] = kw.get("enable_reference_extraction", False)

    ref = kw.get("reference_keywords_config", {})
    flat["max_reference_keywords"] = ref.get("max_keywords", 10)
    flat["similarity_threshold"] = ref.get("similarity_threshold", 0.75)

    wd = ref.get("weight_distribution", {})
    hi = wd.get("high_importance", {})
    flat["ref_weight_high"] = hi.get("weight", 1.0)
    flat["ref_count_high"] = hi.get("count", 3)
    mi = wd.get("medium_importance", {})
    flat["ref_weight_medium"] = mi.get("weight", 0.2)
    flat["ref_count_medium"] = mi.get("count", 5)
    lo = wd.get("low_importance", {})
    flat["ref_weight_low"] = lo.get("weight", 0.1)
    flat["ref_count_low"] = lo.get("count", 2)
    flat["research_context"] = kw.get("research_context", "")

    # Scoring
    sc = config.get("scoring_settings", {})
    krs = sc.get("keyword_relevance_score", {})
    flat["max_score_per_keyword"] = krs.get("max_score_per_keyword", 10)
    ab = sc.get("author_bonus", {})
    flat["enable_author_bonus"] = ab.get("enabled", False)
    flat["expert_authors"] = ab.get("expert_authors", [])
    flat["author_bonus_points"] = ab.get("bonus_points", 5.0)
    ps = sc.get("passing_score_formula", {})
    flat["passing_score_base"] = ps.get("base_score", 5.0)
    flat["passing_score_weight_coefficient"] = ps.get("weight_coefficient", 3.0)
    flat["include_all_in_report"] = sc.get("include_all_in_report", True)

    # Keyword tracker
    kt = config.get("keyword_tracker", {})
    flat["keyword_tracker_enabled"] = kt.get("enabled", True)
    flat["keyword_db_path"] = kt.get("database", {}).get("path", "data/keywords/keywords.db")
    norm = kt.get("normalization", {})
    flat["keyword_normalization_enabled"] = norm.get("enabled", True)
    flat["keyword_normalization_batch_size"] = norm.get("batch_size", 25)
    flat["keyword_trend_default_days"] = kt.get("trend_view", {}).get("default_days", 30)
    charts = kt.get("charts", {})
    flat["keyword_chart_top_n"] = charts.get("bar_chart", {}).get("top_n", 15)
    flat["keyword_trend_top_n"] = charts.get("trend_chart", {}).get("top_n", 5)
    rpt = kt.get("report", {})
    flat["keyword_report_enabled"] = rpt.get("enabled", True)
    flat["keyword_report_frequency"] = rpt.get("frequency", "weekly")

    # Notifications
    nt = config.get("notifications", {})
    flat["notifications_enabled"] = nt.get("enabled", False)
    flat["notify_on_success"] = nt.get("on_success", True)
    flat["notify_on_failure"] = nt.get("on_failure", True)
    flat["notify_attach_reports"] = nt.get("attach_reports", False)
    flat["notification_top_n"] = nt.get("top_n", 5)
    ch = nt.get("channels", {})
    flat["notify_email_enabled"] = ch.get("email", {}).get("enabled", False)
    flat["notify_wechat_enabled"] = ch.get("wechat_work", {}).get("enabled", False)
    flat["notify_dingtalk_enabled"] = ch.get("dingtalk", {}).get("enabled", False)
    flat["notify_telegram_enabled"] = ch.get("telegram", {}).get("enabled", False)
    flat["notify_slack_enabled"] = ch.get("slack", {}).get("enabled", False)
    flat["notify_generic_webhook_enabled"] = ch.get("generic_webhook", {}).get("enabled", False)

    # Retry
    rt = config.get("retry", {})
    flat["retry_max_attempts"] = rt.get("max_attempts", 3)
    flat["retry_min_wait"] = rt.get("min_wait", 2)
    flat["retry_max_wait"] = rt.get("max_wait", 30)

    # Logging
    lg = config.get("logging", {})
    flat["log_rotation_type"] = lg.get("rotation_type", "time")
    flat["log_keep_days"] = lg.get("keep_days", 30)

    # Concurrency
    cc = config.get("concurrency", {})
    flat["concurrency_enabled"] = cc.get("enabled", False)
    flat["concurrency_workers"] = cc.get("workers", 3)

    # PDF parser
    pp = config.get("pdf_parser", {})
    flat["pdf_parser_mode"] = pp.get("mode", "mineru")
    flat["mineru_model_version"] = pp.get("mineru_model_version", "pipeline")
    flat["mineru_poll_interval"] = pp.get("poll_interval", 3)
    flat["mineru_poll_timeout"] = pp.get("poll_timeout", 300)

    # Report
    rs = config.get("report_settings", {})
    flat["enable_html_report"] = rs.get("enable_html_report", True)

    # Auto-update
    au = config.get("auto_update", {})
    flat["auto_update_enabled"] = au.get("enabled", True)

    # Token tracking
    tt = config.get("token_tracking", {})
    flat["token_tracking_enabled"] = tt.get("enabled", True)

    # Trend research
    tr = config.get("trend_research", {})
    flat["trend_default_date_range_days"] = tr.get("default_date_range_days", 365)
    flat["trend_max_results"] = tr.get("max_results", 500)
    flat["trend_sort_order"] = tr.get("sort_order", "ascending")
    flat["trend_report_position"] = tr.get("report_position", "end")
    flat["trend_generate_tldr"] = tr.get("generate_tldr", True)
    flat["trend_tldr_batch_size"] = tr.get("tldr_batch_size", 10)
    flat["trend_output_formats"] = tr.get("output_formats", ["markdown", "html"])
    flat["trend_enabled_skills"] = tr.get(
        "enabled_skills",
        [
            "temporal_evolution",
            "hot_topics",
            "key_authors",
            "research_gaps",
            "methodology_trends",
        ],
    )

    return flat


# ==================== Validation ====================


def validate_llm_connection(api_key: str, base_url: str, model_name: str) -> Tuple[bool, str]:
    """Test LLM connection with a minimal request. Returns (success, message)."""
    if not api_key or not base_url or not model_name:
        return False, "API Key, Base URL, and Model Name are all required."

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=15)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
        )
        return True, f"Connection successful! Model: {response.model}"
    except ImportError:
        return False, "openai package not installed. Cannot test connection."
    except Exception as e:
        return False, f"Connection failed: {e}"


def validate_smtp_connection(
    host: str, port: int, user: str, password: str, use_tls: bool = True
) -> Tuple[bool, str]:
    """Test SMTP connection. Returns (success, message)."""
    if not host or not user:
        return False, "SMTP host and user are required."

    try:
        import smtplib

        if use_tls:
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(host, port, timeout=10)

        if user and password:
            server.login(user, password)
        server.quit()
        return True, "SMTP connection successful!"
    except Exception as e:
        return False, f"SMTP connection failed: {e}"
