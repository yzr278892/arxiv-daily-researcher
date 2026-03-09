#!/usr/bin/env python3
"""
ArXiv Daily Researcher - Interactive Setup Wizard

Usage: python src/utils/setup_wizard.py

Guides first-time users through configuring .env and configs/config.json
with a step-by-step terminal wizard.
"""

import sys
from pathlib import Path

# Add src to path: src/utils/ -> src/ (which contains utils/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import questionary
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("Setup wizard requires 'questionary' and 'rich' packages.")
    print("Install them with: pip install questionary rich")
    sys.exit(1)

from utils.config_io import (
    ALL_DATA_SOURCES,
    LLM_PROVIDERS,
    DEFAULT_ENV_PATH,
    DEFAULT_CONFIG_PATH,
    read_env,
    read_config_json,
    write_env,
    write_config_json,
    build_config_dict,
    flatten_config_dict,
    validate_llm_connection,
)

console = Console()

# Style for questionary
WIZARD_STYLE = questionary.Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "fg:white bold"),
        ("answer", "fg:green bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green"),
    ]
)


def print_header():
    """Print wizard header."""
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]ArXiv Daily Researcher[/bold cyan]\n" "[dim]Interactive Setup Wizard[/dim]",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()
    console.print("[dim]This wizard will guide you through configuring the system.[/dim]")
    console.print("[dim]Press Ctrl+C at any time to abort without saving.[/dim]")
    console.print()


def section_header(number: int, title: str, description: str = ""):
    """Print a section header."""
    console.print()
    console.print(f"[bold cyan]{'=' * 60}[/]")
    console.print(f"[bold cyan]  [{number}/7] {title}[/]")
    if description:
        console.print(f"[dim]  {description}[/]")
    console.print(f"[bold cyan]{'=' * 60}[/]")
    console.print()


def mask_secret(value: str, show_chars: int = 4) -> str:
    """Mask a secret value for display."""
    if not value:
        return "[dim](not set)[/dim]"
    if len(value) <= show_chars:
        return "*" * len(value)
    return value[:show_chars] + "*" * (len(value) - show_chars)


# ==================== Section Functions ====================


def section_llm(existing_env: dict) -> dict:
    """Section 1: LLM Configuration. Returns env values dict."""
    section_header(1, "LLM Configuration", "Configure language model API connections")

    console.print("[bold]CHEAP_LLM[/] — Used for quick scoring and keyword generation")
    console.print("[bold]SMART_LLM[/] — Used for deep analysis and paper understanding")
    console.print()

    env_values = {}

    # Cheap LLM
    console.print("[bold green]-- Low-Cost LLM (CHEAP_LLM) --[/]")
    provider_name = questionary.select(
        "Select LLM provider:",
        choices=list(LLM_PROVIDERS.keys()),
        style=WIZARD_STYLE,
    ).ask()
    if provider_name is None:
        raise KeyboardInterrupt

    provider = LLM_PROVIDERS[provider_name]

    default_base = existing_env.get("CHEAP_LLM__BASE_URL", provider["base_url"])
    default_model = existing_env.get("CHEAP_LLM__MODEL_NAME", provider["cheap_model"])
    default_key = existing_env.get("CHEAP_LLM__API_KEY", "")

    cheap_key = questionary.password(
        "CHEAP_LLM API Key:",
        default=default_key,
        style=WIZARD_STYLE,
    ).ask()
    if cheap_key is None:
        raise KeyboardInterrupt

    cheap_base = questionary.text(
        "CHEAP_LLM Base URL:",
        default=default_base,
        style=WIZARD_STYLE,
    ).ask()
    if cheap_base is None:
        raise KeyboardInterrupt

    cheap_model = questionary.text(
        "CHEAP_LLM Model Name:",
        default=default_model,
        style=WIZARD_STYLE,
    ).ask()
    if cheap_model is None:
        raise KeyboardInterrupt

    cheap_temp = questionary.text(
        "CHEAP_LLM Temperature:",
        default=existing_env.get("CHEAP_LLM__TEMPERATURE", "0.3"),
        validate=lambda x: True if _is_float(x) else "Please enter a valid number",
        style=WIZARD_STYLE,
    ).ask()
    if cheap_temp is None:
        raise KeyboardInterrupt

    env_values["CHEAP_LLM__API_KEY"] = cheap_key
    env_values["CHEAP_LLM__BASE_URL"] = cheap_base
    env_values["CHEAP_LLM__MODEL_NAME"] = cheap_model
    env_values["CHEAP_LLM__TEMPERATURE"] = cheap_temp

    # Test connection?
    if cheap_key and cheap_key != "sk-dummy":
        if questionary.confirm(
            "Test CHEAP_LLM connection?", default=True, style=WIZARD_STYLE
        ).ask():
            with console.status("[cyan]Testing connection...[/]"):
                ok, msg = validate_llm_connection(cheap_key, cheap_base, cheap_model)
            if ok:
                console.print(f"  [green]{msg}[/]")
            else:
                console.print(f"  [red]{msg}[/]")

    # Smart LLM
    console.print()
    console.print("[bold green]-- High-Performance LLM (SMART_LLM) --[/]")

    use_same = questionary.confirm(
        "Use same provider for SMART_LLM?",
        default=True,
        style=WIZARD_STYLE,
    ).ask()
    if use_same is None:
        raise KeyboardInterrupt

    if use_same:
        smart_base = cheap_base
        smart_key = cheap_key
    else:
        smart_provider_name = questionary.select(
            "Select SMART_LLM provider:",
            choices=list(LLM_PROVIDERS.keys()),
            style=WIZARD_STYLE,
        ).ask()
        if smart_provider_name is None:
            raise KeyboardInterrupt
        smart_provider = LLM_PROVIDERS[smart_provider_name]
        smart_base = questionary.text(
            "SMART_LLM Base URL:",
            default=smart_provider["base_url"],
            style=WIZARD_STYLE,
        ).ask()
        if smart_base is None:
            raise KeyboardInterrupt
        smart_key = questionary.password(
            "SMART_LLM API Key:",
            default=existing_env.get("SMART_LLM__API_KEY", ""),
            style=WIZARD_STYLE,
        ).ask()
        if smart_key is None:
            raise KeyboardInterrupt

    default_smart_model = existing_env.get(
        "SMART_LLM__MODEL_NAME",
        LLM_PROVIDERS.get(provider_name, {}).get("smart_model", "gpt-4o"),
    )
    smart_model = questionary.text(
        "SMART_LLM Model Name:",
        default=default_smart_model,
        style=WIZARD_STYLE,
    ).ask()
    if smart_model is None:
        raise KeyboardInterrupt

    smart_temp = questionary.text(
        "SMART_LLM Temperature:",
        default=existing_env.get("SMART_LLM__TEMPERATURE", "0.3"),
        validate=lambda x: True if _is_float(x) else "Please enter a valid number",
        style=WIZARD_STYLE,
    ).ask()
    if smart_temp is None:
        raise KeyboardInterrupt

    env_values["SMART_LLM__API_KEY"] = smart_key
    env_values["SMART_LLM__BASE_URL"] = smart_base
    env_values["SMART_LLM__MODEL_NAME"] = smart_model
    env_values["SMART_LLM__TEMPERATURE"] = smart_temp

    return env_values


def section_search(existing_config: dict) -> dict:
    """Section 2: Search Settings. Returns flat config values."""
    section_header(2, "Search Settings", "Control search scope and limits")

    flat = flatten_config_dict(existing_config) if existing_config else {}

    search_days = questionary.text(
        "Search recent N days of papers:",
        default=str(flat.get("search_days", 7)),
        validate=lambda x: True if _is_positive_int(x) else "Please enter a positive integer",
        style=WIZARD_STYLE,
    ).ask()
    if search_days is None:
        raise KeyboardInterrupt

    max_results = questionary.text(
        "Max results per source:",
        default=str(flat.get("max_results", 100)),
        validate=lambda x: True if _is_positive_int(x) else "Please enter a positive integer",
        style=WIZARD_STYLE,
    ).ask()
    if max_results is None:
        raise KeyboardInterrupt

    return {
        "search_days": int(search_days),
        "max_results": int(max_results),
    }


def section_data_sources(existing_config: dict) -> dict:
    """Section 3: Data Sources. Returns flat config values."""
    section_header(3, "Data Sources", "Select paper sources to monitor")

    flat = flatten_config_dict(existing_config) if existing_config else {}
    current = flat.get("enabled_sources", ["arxiv"])

    choices = []
    for src in ALL_DATA_SOURCES:
        choices.append(questionary.Choice(src, checked=src in current))

    enabled = questionary.checkbox(
        "Select data sources to enable:",
        choices=choices,
        style=WIZARD_STYLE,
    ).ask()
    if enabled is None:
        raise KeyboardInterrupt

    if not enabled:
        console.print("[yellow]No sources selected, defaulting to 'arxiv'[/]")
        enabled = ["arxiv"]

    result = {"enabled_sources": enabled}

    # ArXiv domains
    if "arxiv" in enabled:
        domains_str = questionary.text(
            "ArXiv target domains (comma-separated):",
            default=", ".join(flat.get("domains", ["quant-ph"])),
            style=WIZARD_STYLE,
        ).ask()
        if domains_str is None:
            raise KeyboardInterrupt
        result["domains"] = [d.strip() for d in domains_str.split(",") if d.strip()]

    # OpenAlex config
    console.print()
    console.print("[dim]OpenAlex provides journal paper data. An email improves rate limits.[/]")
    openalex_email = questionary.text(
        "OpenAlex email (optional, press Enter to skip):",
        default="",
        style=WIZARD_STYLE,
    ).ask()
    openalex_key = questionary.text(
        "OpenAlex API Key (optional):",
        default="",
        style=WIZARD_STYLE,
    ).ask()

    result["_env"] = {}
    if openalex_email:
        result["_env"]["OPENALEX_EMAIL"] = openalex_email
    if openalex_key:
        result["_env"]["OPENALEX_API_KEY"] = openalex_key

    return result


def section_keywords(existing_config: dict) -> dict:
    """Section 4: Keywords. Returns flat config values."""
    section_header(4, "Keywords", "Define research keywords and weights")

    flat = flatten_config_dict(existing_config) if existing_config else {}

    console.print("[dim]Primary keywords are used for paper scoring.[/]")
    console.print("[dim]Higher weight = more importance in relevance scoring.[/]")
    console.print()

    kw_str = questionary.text(
        "Primary keywords (comma-separated):",
        default=", ".join(flat.get("primary_keywords", [])),
        style=WIZARD_STYLE,
    ).ask()
    if kw_str is None:
        raise KeyboardInterrupt
    keywords = [k.strip() for k in kw_str.split(",") if k.strip()]

    weight = questionary.text(
        "Primary keyword weight:",
        default=str(flat.get("primary_keyword_weight", 1.0)),
        validate=lambda x: True if _is_float(x) else "Please enter a valid number",
        style=WIZARD_STYLE,
    ).ask()
    if weight is None:
        raise KeyboardInterrupt

    enable_ref = questionary.confirm(
        "Enable keyword extraction from reference PDFs?",
        default=flat.get("enable_reference_extraction", False),
        style=WIZARD_STYLE,
    ).ask()
    if enable_ref is None:
        raise KeyboardInterrupt

    context = questionary.text(
        "Research context (describe your research area, optional):",
        default=flat.get("research_context", ""),
        style=WIZARD_STYLE,
    ).ask()
    if context is None:
        raise KeyboardInterrupt

    return {
        "primary_keywords": keywords,
        "primary_keyword_weight": float(weight),
        "enable_reference_extraction": enable_ref,
        "research_context": context,
    }


def section_scoring(existing_config: dict) -> dict:
    """Section 5: Scoring. Returns flat config values."""
    section_header(5, "Scoring", "Configure paper relevance scoring")

    flat = flatten_config_dict(existing_config) if existing_config else {}

    console.print("[dim]Passing score = base_score + weight_coefficient * sum(keyword_weights)[/]")
    console.print()

    base = questionary.text(
        "Passing score base:",
        default=str(flat.get("passing_score_base", 5.0)),
        validate=lambda x: True if _is_float(x) else "Please enter a valid number",
        style=WIZARD_STYLE,
    ).ask()
    if base is None:
        raise KeyboardInterrupt

    coeff = questionary.text(
        "Passing score weight coefficient:",
        default=str(flat.get("passing_score_weight_coefficient", 3.0)),
        validate=lambda x: True if _is_float(x) else "Please enter a valid number",
        style=WIZARD_STYLE,
    ).ask()
    if coeff is None:
        raise KeyboardInterrupt

    enable_bonus = questionary.confirm(
        "Enable author bonus scoring?",
        default=flat.get("enable_author_bonus", False),
        style=WIZARD_STYLE,
    ).ask()
    if enable_bonus is None:
        raise KeyboardInterrupt

    result = {
        "passing_score_base": float(base),
        "passing_score_weight_coefficient": float(coeff),
        "enable_author_bonus": enable_bonus,
    }

    if enable_bonus:
        authors_str = questionary.text(
            "Expert authors (comma-separated):",
            default=", ".join(flat.get("expert_authors", [])),
            style=WIZARD_STYLE,
        ).ask()
        if authors_str is None:
            raise KeyboardInterrupt
        result["expert_authors"] = [a.strip() for a in authors_str.split(",") if a.strip()]

        bonus = questionary.text(
            "Author bonus points:",
            default=str(flat.get("author_bonus_points", 5.0)),
            validate=lambda x: True if _is_float(x) else "Please enter a valid number",
            style=WIZARD_STYLE,
        ).ask()
        if bonus is None:
            raise KeyboardInterrupt
        result["author_bonus_points"] = float(bonus)

    include_all = questionary.confirm(
        "Include all papers in report (not just passing)?",
        default=flat.get("include_all_in_report", True),
        style=WIZARD_STYLE,
    ).ask()
    if include_all is None:
        raise KeyboardInterrupt
    result["include_all_in_report"] = include_all

    return result


def section_notifications(existing_env: dict, existing_config: dict) -> tuple:
    """Section 6: Notifications. Returns (env_values, config_values)."""
    section_header(6, "Notifications", "Configure notification channels")

    flat = flatten_config_dict(existing_config) if existing_config else {}

    enable = questionary.confirm(
        "Enable notifications?",
        default=flat.get("notifications_enabled", False),
        style=WIZARD_STYLE,
    ).ask()
    if enable is None:
        raise KeyboardInterrupt

    config_values = {"notifications_enabled": enable}
    env_values = {}

    if not enable:
        return env_values, config_values

    # Channel selection
    channel_choices = [
        questionary.Choice("Email (SMTP)", value="email"),
        questionary.Choice("WeChat Work", value="wechat"),
        questionary.Choice("DingTalk", value="dingtalk"),
        questionary.Choice("Telegram", value="telegram"),
        questionary.Choice("Slack", value="slack"),
        questionary.Choice("Generic Webhook", value="webhook"),
    ]

    channels = questionary.checkbox(
        "Select notification channels:",
        choices=channel_choices,
        style=WIZARD_STYLE,
    ).ask()
    if channels is None:
        raise KeyboardInterrupt

    if "email" in channels:
        config_values["notify_email_enabled"] = True
        console.print("\n[bold]Email (SMTP) Configuration:[/]")
        env_values["SMTP_HOST"] = (
            questionary.text(
                "SMTP Host:",
                default=existing_env.get("SMTP_HOST", "smtp.gmail.com"),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )
        env_values["SMTP_PORT"] = (
            questionary.text(
                "SMTP Port:",
                default=existing_env.get("SMTP_PORT", "587"),
                style=WIZARD_STYLE,
            ).ask()
            or "587"
        )
        env_values["SMTP_USER"] = (
            questionary.text(
                "SMTP User:",
                default=existing_env.get("SMTP_USER", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )
        env_values["SMTP_PASSWORD"] = (
            questionary.password(
                "SMTP Password:",
                default=existing_env.get("SMTP_PASSWORD", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )
        env_values["SMTP_FROM"] = (
            questionary.text(
                "From address:",
                default=existing_env.get("SMTP_FROM", env_values.get("SMTP_USER", "")),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )
        env_values["SMTP_TO"] = (
            questionary.text(
                "To addresses (comma-separated):",
                default=existing_env.get("SMTP_TO", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )

    if "wechat" in channels:
        config_values["notify_wechat_enabled"] = True
        console.print("\n[bold]WeChat Work Configuration:[/]")
        env_values["WECHAT_WEBHOOK_URL"] = (
            questionary.password(
                "WeChat Webhook URL:",
                default=existing_env.get("WECHAT_WEBHOOK_URL", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )

    if "dingtalk" in channels:
        config_values["notify_dingtalk_enabled"] = True
        console.print("\n[bold]DingTalk Configuration:[/]")
        env_values["DINGTALK_WEBHOOK_URL"] = (
            questionary.password(
                "DingTalk Webhook URL:",
                default=existing_env.get("DINGTALK_WEBHOOK_URL", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )
        env_values["DINGTALK_SECRET"] = (
            questionary.password(
                "DingTalk Secret (optional):",
                default=existing_env.get("DINGTALK_SECRET", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )

    if "telegram" in channels:
        config_values["notify_telegram_enabled"] = True
        console.print("\n[bold]Telegram Configuration:[/]")
        env_values["TELEGRAM_BOT_TOKEN"] = (
            questionary.password(
                "Telegram Bot Token:",
                default=existing_env.get("TELEGRAM_BOT_TOKEN", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )
        env_values["TELEGRAM_CHAT_ID"] = (
            questionary.text(
                "Telegram Chat ID:",
                default=existing_env.get("TELEGRAM_CHAT_ID", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )

    if "slack" in channels:
        config_values["notify_slack_enabled"] = True
        console.print("\n[bold]Slack Configuration:[/]")
        env_values["SLACK_WEBHOOK_URL"] = (
            questionary.password(
                "Slack Webhook URL:",
                default=existing_env.get("SLACK_WEBHOOK_URL", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )

    if "webhook" in channels:
        config_values["notify_generic_webhook_enabled"] = True
        console.print("\n[bold]Generic Webhook Configuration:[/]")
        env_values["GENERIC_WEBHOOK_URL"] = (
            questionary.password(
                "Webhook URL:",
                default=existing_env.get("GENERIC_WEBHOOK_URL", ""),
                style=WIZARD_STYLE,
            ).ask()
            or ""
        )

    top_n = questionary.text(
        "Top-N papers in notification:",
        default=str(flat.get("notification_top_n", 5)),
        validate=lambda x: True if _is_positive_int(x) else "Please enter a positive integer",
        style=WIZARD_STYLE,
    ).ask()
    if top_n is not None:
        config_values["notification_top_n"] = int(top_n)

    return env_values, config_values


def section_advanced(existing_env: dict, existing_config: dict) -> tuple:
    """Section 7: Advanced settings. Returns (env_values, config_values)."""
    section_header(7, "Advanced Settings", "Optional advanced configuration")

    skip = questionary.confirm(
        "Skip advanced settings and use defaults?",
        default=True,
        style=WIZARD_STYLE,
    ).ask()
    if skip is None:
        raise KeyboardInterrupt

    if skip:
        return {}, {}

    flat = flatten_config_dict(existing_config) if existing_config else {}
    env_values = {}
    config_values = {}

    # PDF Parser
    pdf_mode = questionary.select(
        "PDF parser mode:",
        choices=[
            questionary.Choice("mineru (cloud API, higher quality)", value="mineru"),
            questionary.Choice("pymupdf (local, no network needed)", value="pymupdf"),
        ],
        default="mineru" if flat.get("pdf_parser_mode", "mineru") == "mineru" else "pymupdf",
        style=WIZARD_STYLE,
    ).ask()
    if pdf_mode is None:
        raise KeyboardInterrupt
    config_values["pdf_parser_mode"] = pdf_mode

    if pdf_mode == "mineru":
        mineru_key = questionary.password(
            "MinerU API Key (optional):",
            default=existing_env.get("MINERU_API_KEY", ""),
            style=WIZARD_STYLE,
        ).ask()
        if mineru_key:
            env_values["MINERU_API_KEY"] = mineru_key

    # Concurrency
    enable_conc = questionary.confirm(
        "Enable concurrent processing?",
        default=flat.get("concurrency_enabled", False),
        style=WIZARD_STYLE,
    ).ask()
    if enable_conc is None:
        raise KeyboardInterrupt
    config_values["concurrency_enabled"] = enable_conc

    if enable_conc:
        workers = questionary.text(
            "Number of concurrent workers (max 5 recommended):",
            default=str(flat.get("concurrency_workers", 3)),
            validate=lambda x: True if _is_positive_int(x) else "Please enter a positive integer",
            style=WIZARD_STYLE,
        ).ask()
        if workers is not None:
            config_values["concurrency_workers"] = int(workers)

    # HTML report
    html_report = questionary.confirm(
        "Generate HTML reports?",
        default=flat.get("enable_html_report", True),
        style=WIZARD_STYLE,
    ).ask()
    if html_report is not None:
        config_values["enable_html_report"] = html_report

    # Auto-update
    auto_update = questionary.confirm(
        "Enable auto-update check?",
        default=flat.get("auto_update_enabled", True),
        style=WIZARD_STYLE,
    ).ask()
    if auto_update is not None:
        config_values["auto_update_enabled"] = auto_update

    # Log retention
    log_days = questionary.text(
        "Log retention days:",
        default=str(flat.get("log_keep_days", 30)),
        validate=lambda x: True if _is_positive_int(x) else "Please enter a positive integer",
        style=WIZARD_STYLE,
    ).ask()
    if log_days is not None:
        config_values["log_keep_days"] = int(log_days)

    return env_values, config_values


# ==================== Summary & Write ====================


def show_summary(env_values: dict, config_values: dict):
    """Display a summary table of all configured values."""
    console.print()
    console.print(Panel("[bold]Configuration Summary[/]", border_style="cyan"))

    # Env summary
    if env_values:
        env_table = Table(title="Environment Variables (.env)", show_header=True)
        env_table.add_column("Key", style="cyan", width=30)
        env_table.add_column("Value", style="green")

        secret_keys = {f[0] for f in ENV_FIELDS_LOOKUP if f[2]}

        for key, value in sorted(env_values.items()):
            if key in secret_keys:
                env_table.add_row(key, mask_secret(value))
            else:
                env_table.add_row(key, value or "[dim](empty)[/]")

        console.print(env_table)
        console.print()

    # Config summary
    if config_values:
        cfg_table = Table(title="Config Settings (config.json)", show_header=True)
        cfg_table.add_column("Setting", style="cyan", width=35)
        cfg_table.add_column("Value", style="green")

        for key, value in sorted(config_values.items()):
            if key.startswith("_"):
                continue
            display_val = str(value)
            if isinstance(value, list):
                display_val = ", ".join(str(v) for v in value) if value else "[dim](empty)[/]"
            elif isinstance(value, bool):
                display_val = "[green]Yes[/]" if value else "[red]No[/]"
            cfg_table.add_row(key, display_val)

        console.print(cfg_table)


# Build lookup set for secret detection
ENV_FIELDS_LOOKUP = [
    ("CHEAP_LLM__API_KEY", "Low-cost LLM API Key", True),
    ("SMART_LLM__API_KEY", "High-perf LLM API Key", True),
    ("OPENALEX_API_KEY", "OpenAlex API Key", True),
    ("SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar API Key", True),
    ("MINERU_API_KEY", "MinerU API Key", True),
    ("SMTP_PASSWORD", "SMTP Password", True),
    ("WECHAT_WEBHOOK_URL", "WeChat Webhook URL", True),
    ("DINGTALK_WEBHOOK_URL", "DingTalk Webhook URL", True),
    ("DINGTALK_SECRET", "DingTalk Secret", True),
    ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token", True),
    ("SLACK_WEBHOOK_URL", "Slack Webhook URL", True),
    ("GENERIC_WEBHOOK_URL", "Generic Webhook URL", True),
]


# ==================== Validators ====================


def _is_float(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return False


def _is_positive_int(x: str) -> bool:
    try:
        return int(x) > 0
    except ValueError:
        return False


# ==================== Main ====================


def main():
    try:
        print_header()

        # Load existing config for defaults
        existing_env = read_env()
        existing_config = read_config_json()

        all_env = {}
        all_config_flat = {}

        # Section 1: LLM
        llm_env = section_llm(existing_env)
        all_env.update(llm_env)

        # Section 2: Search
        search_cfg = section_search(existing_config)
        all_config_flat.update(search_cfg)

        # Section 3: Data Sources
        ds_cfg = section_data_sources(existing_config)
        # Extract env values from data source section
        if "_env" in ds_cfg:
            all_env.update(ds_cfg.pop("_env"))
        all_config_flat.update(ds_cfg)

        # Section 4: Keywords
        kw_cfg = section_keywords(existing_config)
        all_config_flat.update(kw_cfg)

        # Section 5: Scoring
        score_cfg = section_scoring(existing_config)
        all_config_flat.update(score_cfg)

        # Section 6: Notifications
        notif_env, notif_cfg = section_notifications(existing_env, existing_config)
        all_env.update(notif_env)
        all_config_flat.update(notif_cfg)

        # Section 7: Advanced
        adv_env, adv_cfg = section_advanced(existing_env, existing_config)
        all_env.update(adv_env)
        all_config_flat.update(adv_cfg)

        # Show summary
        show_summary(all_env, all_config_flat)

        # Confirm and write
        console.print()
        write_confirm = questionary.confirm(
            "Write configuration files?",
            default=True,
            style=WIZARD_STYLE,
        ).ask()

        if write_confirm:
            # Build the full config dict from flat values
            # Start from existing config as base, then override with wizard values
            existing_flat = flatten_config_dict(existing_config) if existing_config else {}
            existing_flat.update(all_config_flat)

            config_dict = build_config_dict(**existing_flat)
            write_config_json(config_dict)
            console.print(f"  [green]Saved:[/] {DEFAULT_CONFIG_PATH}")

            # Merge env: preserve existing values, override with wizard values
            merged_env = {**existing_env, **all_env}
            write_env(merged_env)
            console.print(f"  [green]Saved:[/] {DEFAULT_ENV_PATH}")

            console.print()
            console.print(
                Panel.fit(
                    "[bold green]Configuration complete![/]\n\n"
                    "Next steps:\n"
                    "  [cyan]python main.py[/]                          Run daily research\n"
                    "  [cyan]streamlit run webui/config_panel.py[/]     Open config panel\n"
                    "  [cyan]docker compose up -d[/]                    Deploy with Docker",
                    border_style="green",
                    padding=(1, 2),
                )
            )
        else:
            console.print("[yellow]Configuration cancelled. No files were modified.[/]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
