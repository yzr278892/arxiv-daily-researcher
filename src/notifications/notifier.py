"""
通知代理模块 - 多渠道通知系统

支持的通知渠道：
- Email: SMTP 邮件通知
- 企业微信: Webhook 机器人（Markdown 模板）
- 钉钉: Webhook 机器人（支持签名验证）
- Telegram: Bot API
- Slack: Incoming Webhook
- 通用 Webhook: 自定义 URL

支持的通知类型：
- 运行成功/失败通知（基于可自定义模板）
- 错误告警通知（MinerU、LLM、网络、通用错误）
"""

import json
import logging
import smtplib
import hashlib
import hmac
import base64
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Dict, Optional, Any

import requests

logger = logging.getLogger(__name__)

# 模板目录
TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "templates" / "notifications"
)
EMAIL_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "templates" / "email"
)


def _load_template(name: str) -> Optional[str]:
    """
    加载通知模板文件。

    模板文件存放于 configs/templates/notifications/ 目录，
    以 '# ' 开头（单个 #）的行视为注释，不会出现在最终消息中。
    '## ' 及更多 # 开头的行保留为 Markdown 标题。

    参数:
        name: 模板文件名（不含扩展名），如 'success'、'error_mineru'

    返回:
        去除注释后的模板内容，文件不存在时返回 None
    """
    path = TEMPLATE_DIR / f"{name}.md"
    if not path.exists():
        logger.debug(f"模板文件不存在: {path}")
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        content_lines = []
        for line in lines:
            stripped = line.lstrip()
            # 单 # 开头且不是 ## 的行视为注释
            if stripped.startswith("# ") and not stripped.startswith("## "):
                continue
            if stripped == "#":
                continue
            content_lines.append(line)
        return "\n".join(content_lines).strip()
    except Exception as e:
        logger.warning(f"加载模板失败 ({path}): {e}")
        return None


def _render_template(template: str, **kwargs) -> str:
    """渲染模板，将 {变量名} 替换为实际值。未提供的变量保留原样。"""
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def _load_email_template(name: str) -> Optional[str]:
    """
    加载 HTML 邮件通知模板文件。

    模板文件存放于 configs/templates/email/ 目录，以 .html 为扩展名。
    HTML 文件开头的 HTML 注释（<!-- ... -->）会被保留，不做处理。

    参数:
        name: 模板文件名（不含扩展名），如 'success'、'error_llm'

    返回:
        模板 HTML 内容，文件不存在时返回 None
    """
    path = EMAIL_TEMPLATE_DIR / f"{name}.html"
    if not path.exists():
        logger.debug(f"HTML 邮件模板不存在: {path}")
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"加载 HTML 邮件模板失败 ({path}): {e}")
        return None


@dataclass
class RunResult:
    """管道运行结果摘要"""

    run_timestamp: str = ""
    total_papers_fetched: int = 0
    papers_by_source: Dict[str, int] = field(default_factory=dict)
    qualified_by_source: Dict[str, int] = field(default_factory=dict)
    analyzed_by_source: Dict[str, int] = field(default_factory=dict)
    report_paths: Dict[str, str] = field(default_factory=dict)
    total_qualified: int = 0
    total_analyzed: int = 0
    success: bool = True
    error_message: Optional[str] = None
    top_papers: List[Dict[str, Any]] = field(default_factory=list)
    token_usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrendRunResult:
    """研究趋势分析运行结果摘要"""

    run_timestamp: str = ""
    keywords: List[str] = field(default_factory=list)
    date_from: str = ""
    date_to: str = ""
    total_papers: int = 0
    tldr_count: int = 0
    trend_skills_count: int = 0
    report_paths: Dict[str, str] = field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None
    token_usage: Dict[str, Any] = field(default_factory=dict)


class BaseNotifier(ABC):
    """通知器抽象基类"""

    @abstractmethod
    def send(self, subject: str, body: str, attachments: Optional[List[Path]] = None) -> bool:
        """发送通知，成功返回 True"""
        ...


class EmailNotifier(BaseNotifier):
    """SMTP 邮件通知"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
        to_addrs: List[str],
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr or user
        self.to_addrs = to_addrs
        self.use_tls = use_tls

    def send(
        self,
        subject: str,
        body: str,
        attachments: Optional[List[Path]] = None,
        html_body: Optional[str] = None,
    ) -> bool:
        # 根据是否有附件和 HTML 选择合适的 MIME 结构
        if attachments:
            # 有附件：外层 mixed，内层 alternative（如有 HTML）
            msg = MIMEMultipart("mixed")
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = subject
            if html_body:
                alt_part = MIMEMultipart("alternative")
                alt_part.attach(MIMEText(body, "plain", "utf-8"))
                alt_part.attach(MIMEText(html_body, "html", "utf-8"))
                msg.attach(alt_part)
            else:
                msg.attach(MIMEText(body, "plain", "utf-8"))
        elif html_body:
            # 无附件 + HTML：直接用 alternative
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            # 仅纯文本
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

        # 附件
        if attachments:
            for filepath in attachments:
                if filepath.exists() and filepath.is_file():
                    part = MIMEBase("application", "octet-stream")
                    with open(filepath, "rb") as f:
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={filepath.name}")
                    msg.attach(part)

        # 发送
        if self.port == 465:
            # SSL 直连
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as server:
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
        else:
            # STARTTLS
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())

        logger.info(f"邮件已发送至: {', '.join(self.to_addrs)}")
        return True


class WebhookNotifier(BaseNotifier):
    """多平台 Webhook 通知"""

    def __init__(self, platform: str, webhook_url: str, **kwargs):
        self.platform = platform
        self.webhook_url = webhook_url
        self.extra = kwargs  # secret, chat_id 等

    def send(self, subject: str, body: str, attachments: Optional[List[Path]] = None) -> bool:
        formatter = getattr(self, f"_format_{self.platform}", self._format_generic)
        url, payload, headers = formatter(subject, body)
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        logger.info(f"Webhook [{self.platform}] 通知已发送")
        return True

    def _format_wechat_work(self, subject: str, body: str):
        """企业微信机器人 — body 已含完整 Markdown 模板内容"""
        content = body
        # 企业微信 markdown 限制 4096 字节
        if len(content.encode("utf-8")) > 4000:
            content = content[:1300] + "\n\n...(内容已截断)"
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        return self.webhook_url, payload, {"Content-Type": "application/json"}

    def _format_dingtalk(self, subject: str, body: str):
        """钉钉机器人（支持签名验证）"""
        url = self.webhook_url
        secret = self.extra.get("secret", "")
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url = f"{url}&timestamp={timestamp}&sign={sign}"

        payload = {"msgtype": "markdown", "markdown": {"title": subject, "text": body}}
        return url, payload, {"Content-Type": "application/json"}

    def _format_telegram(self, subject: str, body: str):
        """Telegram Bot"""
        chat_id = self.extra.get("chat_id", "")
        text = f"*{subject}*\n\n{body}"
        # Telegram 消息限 4096 字符
        if len(text) > 4000:
            text = text[:3900] + "\n\n...(内容已截断)"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        return self.webhook_url, payload, {"Content-Type": "application/json"}

    def _format_slack(self, subject: str, body: str):
        """Slack Incoming Webhook"""
        payload = {
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": subject}},
                {"type": "section", "text": {"type": "mrkdwn", "text": body}},
            ]
        }
        return self.webhook_url, payload, {"Content-Type": "application/json"}

    def _format_generic(self, subject: str, body: str):
        """通用 Webhook"""
        payload = {"subject": subject, "body": body, "timestamp": datetime.now().isoformat()}
        return self.webhook_url, payload, {"Content-Type": "application/json"}


class NotifierAgent:
    """通知编排代理，管理所有已配置的通知渠道"""

    def __init__(self):
        from config import settings

        self.settings = settings
        self.notifiers: List[BaseNotifier] = []
        self._setup_notifiers()

    def _setup_notifiers(self):
        """根据配置初始化通知渠道"""
        s = self.settings

        # Email
        if s.NOTIFY_EMAIL_ENABLED and s.SMTP_HOST and s.SMTP_TO:
            to_addrs = [a.strip() for a in s.SMTP_TO.split(",") if a.strip()]
            self.notifiers.append(
                EmailNotifier(
                    host=s.SMTP_HOST,
                    port=s.SMTP_PORT,
                    user=s.SMTP_USER,
                    password=s.SMTP_PASSWORD,
                    from_addr=s.SMTP_FROM,
                    to_addrs=to_addrs,
                    use_tls=s.SMTP_USE_TLS,
                )
            )
            logger.info("已启用邮件通知")

        # 企业微信
        if s.NOTIFY_WECHAT_ENABLED and s.WECHAT_WEBHOOK_URL:
            self.notifiers.append(WebhookNotifier("wechat_work", s.WECHAT_WEBHOOK_URL))
            logger.info("已启用企业微信通知")

        # 钉钉
        if s.NOTIFY_DINGTALK_ENABLED and s.DINGTALK_WEBHOOK_URL:
            self.notifiers.append(
                WebhookNotifier("dingtalk", s.DINGTALK_WEBHOOK_URL, secret=s.DINGTALK_SECRET)
            )
            logger.info("已启用钉钉通知")

        # Telegram
        if s.NOTIFY_TELEGRAM_ENABLED and s.TELEGRAM_BOT_TOKEN and s.TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage"
            self.notifiers.append(WebhookNotifier("telegram", url, chat_id=s.TELEGRAM_CHAT_ID))
            logger.info("已启用 Telegram 通知")

        # Slack
        if s.NOTIFY_SLACK_ENABLED and s.SLACK_WEBHOOK_URL:
            self.notifiers.append(WebhookNotifier("slack", s.SLACK_WEBHOOK_URL))
            logger.info("已启用 Slack 通知")

        # 通用 Webhook
        if s.NOTIFY_GENERIC_WEBHOOK_ENABLED and s.GENERIC_WEBHOOK_URL:
            self.notifiers.append(WebhookNotifier("generic", s.GENERIC_WEBHOOK_URL))
            logger.info("已启用通用 Webhook 通知")

    # ------------------------------------------------------------------
    # 运行结果通知
    # ------------------------------------------------------------------

    def notify(self, result: RunResult) -> None:
        """格式化并发送运行结果通知到所有已配置的渠道"""
        if not self.notifiers:
            logger.debug("未配置任何通知渠道，跳过")
            return

        if result.success and not self.settings.NOTIFY_ON_SUCCESS:
            return
        if not result.success and not self.settings.NOTIFY_ON_FAILURE:
            return

        subject = self._format_subject(result)
        body = self._format_body(result)
        html_body = self._format_html_body(result)
        attachments = (
            self._collect_attachments(result) if self.settings.NOTIFY_ATTACH_REPORTS else []
        )

        for notifier in self.notifiers:
            try:
                if isinstance(notifier, EmailNotifier) and html_body:
                    notifier.send(subject, body, attachments, html_body=html_body)
                else:
                    notifier.send(subject, body, attachments)
            except Exception as e:
                logger.warning(f"通知发送失败 ({type(notifier).__name__}): {e}")

    # ------------------------------------------------------------------
    # 研究趋势分析结果通知
    # ------------------------------------------------------------------

    def notify_trend(self, result: TrendRunResult) -> None:
        """格式化并发送研究趋势分析结果通知到所有已配置的渠道"""
        if not self.notifiers:
            logger.debug("未配置任何通知渠道，跳过")
            return

        if result.success and not self.settings.NOTIFY_ON_SUCCESS:
            return
        if not result.success and not self.settings.NOTIFY_ON_FAILURE:
            return

        subject = self._format_trend_subject(result)
        body = self._format_trend_body(result)
        html_body = self._format_trend_html_body(result)
        attachments = (
            self._collect_trend_attachments(result) if self.settings.NOTIFY_ATTACH_REPORTS else []
        )

        for notifier in self.notifiers:
            try:
                if isinstance(notifier, EmailNotifier) and html_body:
                    notifier.send(subject, body, attachments, html_body=html_body)
                else:
                    notifier.send(subject, body, attachments)
            except Exception as e:
                logger.warning(f"趋势通知发送失败 ({type(notifier).__name__}): {e}")

    # ------------------------------------------------------------------
    # 错误告警通知
    # ------------------------------------------------------------------

    def notify_error(self, template_name: str, **kwargs) -> None:
        """
        发送错误告警通知。

        使用 configs/templates/notifications/ 下的错误模板文件渲染消息并发送。
        仅在 on_failure 为 True 时发送。模板或渠道不存在时静默跳过。

        参数:
            template_name: 模板名称（如 'error_mineru'、'error_llm'、'error_network'、'error_generic'）
            **kwargs: 模板变量
        """
        if not self.notifiers:
            return
        if not self.settings.NOTIFY_ON_FAILURE:
            return

        if "timestamp" not in kwargs:
            kwargs["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        template = _load_template(template_name)
        if template:
            body = _render_template(template, **kwargs)
        else:
            body = f"## ArXiv Daily Researcher\n\n"
            body += (
                f"<font color=\"warning\">**错误告警**</font> | {kwargs.get('timestamp', '')}\n\n"
            )
            for k, v in kwargs.items():
                if k != "timestamp":
                    body += f"> {k}: {v}\n"

        subject = f"ArXiv Daily Researcher - ERROR ({kwargs.get('timestamp', '')})"

        for notifier in self.notifiers:
            try:
                if isinstance(notifier, EmailNotifier):
                    html_body = self._format_html_error_body(template_name, **kwargs)
                    notifier.send(subject, body, html_body=html_body)
                else:
                    notifier.send(subject, body)
            except Exception as e:
                logger.warning(f"错误告警发送失败 ({type(notifier).__name__}): {e}")

    # ------------------------------------------------------------------
    # 格式化辅助方法
    # ------------------------------------------------------------------

    def _format_token_section_md(self, token_usage: Dict[str, Any]) -> str:
        """格式化 token 消耗为 Markdown 片段（tracking 未开启或无数据返回空字符串）"""
        if not self.settings.TOKEN_TRACKING_ENABLED:
            return ""
        if not token_usage or not token_usage.get("has_data"):
            return ""
        total = token_usage.get("total", 0)
        tp = token_usage.get("total_prompt", 0)
        tc = token_usage.get("total_completion", 0)
        return f"> Token 消耗: **{total:,}** tokens（输入 {tp:,} / 输出 {tc:,}）"

    def _format_token_section_html(self, token_usage: Dict[str, Any]) -> str:
        """格式化 token 消耗为 HTML 行片段（tracking 未开启或无数据返回空字符串）"""
        if not self.settings.TOKEN_TRACKING_ENABLED:
            return ""
        if not token_usage or not token_usage.get("has_data"):
            return ""
        total = token_usage.get("total", 0)
        tp = token_usage.get("total_prompt", 0)
        tc = token_usage.get("total_completion", 0)
        return (
            f'<tr><td style="padding:4px 32px 16px;">'
            f'<p style="margin:0;font-size:12px;color:#9ca3af;">'
            f'Token 消耗: <strong style="color:#6b7280;">{total:,}</strong> tokens'
            f'（输入 {tp:,} / 输出 {tc:,}）</p></td></tr>'
        )

    def _format_subject(self, result: RunResult) -> str:
        status = "SUCCESS" if result.success else "FAILED"
        return f"ArXiv Daily Researcher - {status} ({result.run_timestamp})"

    def _format_body(self, result: RunResult) -> str:
        """使用模板格式化运行结果通知正文，模板不存在时降级为纯文本"""
        template_name = "success" if result.success else "failure"
        template = _load_template(template_name)

        # 构建各数据源统计文本
        source_lines = []
        for source in sorted(result.papers_by_source.keys()):
            fetched = result.papers_by_source.get(source, 0)
            qualified = result.qualified_by_source.get(source, 0)
            analyzed = result.analyzed_by_source.get(source, 0)
            source_lines.append(
                f"> `{source.upper()}` 抓取 **{fetched}** | 及格 **{qualified}** | 分析 **{analyzed}**"
            )
        source_summary = "\n".join(source_lines)

        # 构建报告路径文本
        report_lines = []
        if result.report_paths:
            report_lines.append("**报告路径**")
            for source, path in result.report_paths.items():
                report_lines.append(f"> `{source}` {path}")
        report_list = "\n".join(report_lines)

        # 构建 Top-N 论文文本
        top_lines = []
        if result.top_papers:
            top_lines.append(f"**Top {len(result.top_papers)} 论文**")
            for i, p in enumerate(result.top_papers, 1):
                title = p.get("title", "")[:60]
                score = p.get("score", 0)
                src = p.get("source", "").upper()
                tldr = p.get("tldr", "")[:80]
                url = p.get("url", "")
                top_lines.append(f"> **{i}.** `{src}` {title}")
                top_lines.append(f'> <font color="comment">Score: {score:.1f} | {tldr}</font>')
                if url:
                    top_lines.append(f"> [查看原文]({url})")
        top_papers = "\n".join(top_lines)

        if template:
            return _render_template(
                template,
                status="SUCCESS" if result.success else "FAILED",
                timestamp=result.run_timestamp,
                total_fetched=result.total_papers_fetched,
                total_qualified=result.total_qualified,
                total_analyzed=result.total_analyzed,
                source_summary=source_summary,
                report_list=report_list,
                top_papers=top_papers,
                error_message=result.error_message or "无",
                token_usage_section=self._format_token_section_md(result.token_usage),
            )

        # 模板不存在时降级为纯文本
        return self._format_body_fallback(result)

    def _format_body_fallback(self, result: RunResult) -> str:
        """模板不存在时的兜底纯文本格式（保持向后兼容）"""
        status_icon = "OK" if result.success else "ERROR"
        lines = [
            f"Status: {status_icon}",
            f"Time: {result.run_timestamp}",
            "",
        ]

        if result.error_message:
            lines.append(f"Error: {result.error_message}")
            lines.append("")

        lines.append("Papers Summary:")
        for source in sorted(result.papers_by_source.keys()):
            fetched = result.papers_by_source.get(source, 0)
            qualified = result.qualified_by_source.get(source, 0)
            analyzed = result.analyzed_by_source.get(source, 0)
            lines.append(
                f"  [{source.upper()}] Fetched: {fetched} | Qualified: {qualified} | Analyzed: {analyzed}"
            )

        lines.append("")
        lines.append(
            f"Total: Fetched {result.total_papers_fetched} | "
            f"Qualified {result.total_qualified} | "
            f"Analyzed {result.total_analyzed}"
        )

        if result.report_paths:
            lines.append("")
            lines.append("Reports:")
            for source, path in result.report_paths.items():
                lines.append(f"  [{source}] {path}")

        if result.top_papers:
            lines.append("")
            lines.append(f"Top {len(result.top_papers)} Papers:")
            for i, p in enumerate(result.top_papers, 1):
                title = p.get("title", "")[:80]
                score = p.get("score", 0)
                src = p.get("source", "").upper()
                tldr = p.get("tldr", "")[:120]
                url = p.get("url", "")
                lines.append(f"  {i}. [{src}] {title}")
                lines.append(f"     Score: {score:.1f} | {tldr}")
                if url:
                    lines.append(f"     {url}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # HTML 邮件正文构建
    # ------------------------------------------------------------------

    def _format_html_body(self, result: RunResult) -> Optional[str]:
        """使用 HTML 模板生成邮件正文，模板不存在时返回 None"""
        template_name = "success" if result.success else "failure"
        template = _load_email_template(template_name)
        if not template:
            return None

        source_rows = self._build_source_rows_html(result)
        top_papers_html = self._build_top_papers_html(result)
        report_list_html = self._build_report_list_html(result)

        return _render_template(
            template,
            timestamp=result.run_timestamp,
            total_fetched=result.total_papers_fetched,
            total_qualified=result.total_qualified,
            total_analyzed=result.total_analyzed,
            source_rows=source_rows,
            top_papers_html=top_papers_html,
            report_list_html=report_list_html,
            error_message=self._html_escape(result.error_message or "无"),
            token_usage_html=self._format_token_section_html(result.token_usage),
        )

    def _format_html_error_body(self, template_name: str, **kwargs) -> Optional[str]:
        """使用 HTML 模板生成错误告警邮件正文，模板不存在时返回 None"""
        template = _load_email_template(template_name)
        if not template:
            return None
        escaped = {k: self._html_escape(str(v)) for k, v in kwargs.items()}
        return _render_template(template, **escaped)

    @staticmethod
    def _html_escape(text: str) -> str:
        """对文本进行 HTML 转义，防止特殊字符破坏结构"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _build_source_rows_html(self, result: RunResult) -> str:
        """构建数据来源统计表格行 HTML"""
        rows = []
        row_colors = ["#ffffff", "#f9fafb"]
        for i, source in enumerate(sorted(result.papers_by_source.keys())):
            fetched = result.papers_by_source.get(source, 0)
            qualified = result.qualified_by_source.get(source, 0)
            analyzed = result.analyzed_by_source.get(source, 0)
            bg = row_colors[i % 2]
            rows.append(
                f'<tr style="background-color:{bg};">'
                f'<td style="padding:10px 14px;font-size:13px;color:#374151;border-bottom:1px solid #f0f0f0;">'
                f'<span style="display:inline-block;background-color:#e0e7ff;color:#3730a3;'
                f'font-size:11px;font-weight:600;padding:2px 7px;border-radius:4px;">'
                f"{self._html_escape(source.upper())}</span></td>"
                f'<td style="padding:10px 14px;text-align:center;font-size:13px;font-weight:600;'
                f'color:#374151;border-bottom:1px solid #f0f0f0;">{fetched}</td>'
                f'<td style="padding:10px 14px;text-align:center;font-size:13px;font-weight:600;'
                f'color:#374151;border-bottom:1px solid #f0f0f0;">{qualified}</td>'
                f'<td style="padding:10px 14px;text-align:center;font-size:13px;font-weight:600;'
                f'color:#374151;border-bottom:1px solid #f0f0f0;">{analyzed}</td>'
                f"</tr>"
            )
        return (
            "\n".join(rows)
            if rows
            else (
                '<tr><td colspan="4" style="padding:14px;text-align:center;'
                'font-size:13px;color:#9ca3af;">暂无数据</td></tr>'
            )
        )

    def _build_top_papers_html(self, result: RunResult) -> str:
        """构建 Top-N 论文卡片 HTML（作为完整的 <tr> 块返回）"""
        if not result.top_papers:
            return ""

        cards = []
        for i, p in enumerate(result.top_papers, 1):
            title = self._html_escape(p.get("title", "")[:100])
            score = p.get("score", 0)
            src = self._html_escape(p.get("source", "").upper())
            tldr = self._html_escape(p.get("tldr", "")[:200])
            url = p.get("url", "")
            link_html = (
                (
                    f'<p style="margin:8px 0 0;">'
                    f'<a href="{self._html_escape(url)}" '
                    f'style="color:#5b6af0;font-size:12px;text-decoration:none;">查看原文 →</a></p>'
                )
                if url
                else ""
            )

            cards.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
                f'style="margin-bottom:10px;border:1px solid #e8ebf0;border-radius:8px;'
                f'overflow:hidden;border-collapse:separate;">'
                f'<tr><td style="padding:14px 16px;background-color:#fafafa;border-bottom:1px solid #e8ebf0;">'
                f'<p style="margin:0;font-size:12px;color:#6b7280;">'
                f'<span style="background-color:#e0e7ff;color:#3730a3;font-size:11px;'
                f'font-weight:600;padding:1px 6px;border-radius:3px;margin-right:6px;">{src}</span>'
                f'Score: <strong style="color:#1a7a4a;">{score:.1f}</strong></p></td></tr>'
                f'<tr><td style="padding:14px 16px;">'
                f'<p style="margin:0 0 6px;font-size:14px;font-weight:600;color:#1a1f36;'
                f'line-height:1.4;">{i}. {title}</p>'
                f'<p style="margin:0;font-size:13px;color:#4b5563;line-height:1.6;">{tldr}</p>'
                f"{link_html}</td></tr>"
                f"</table>"
            )

        cards_html = "\n".join(cards)
        return (
            f'<tr><td style="padding:28px 32px 0;">'
            f'<h2 style="margin:0 0 14px;font-size:14px;font-weight:700;color:#1a1f36;'
            f"text-transform:uppercase;letter-spacing:1px;border-left:3px solid #5b6af0;"
            f'padding-left:10px;">Top {len(result.top_papers)} 论文</h2>'
            f"{cards_html}"
            f"</td></tr>"
        )

    def _build_report_list_html(self, result: RunResult) -> str:
        """构建报告路径列表 HTML（作为完整的 <tr> 块返回）"""
        if not result.report_paths:
            return ""

        rows = []
        row_colors = ["#ffffff", "#f9fafb"]
        for i, (source, path_str) in enumerate(sorted(result.report_paths.items())):
            bg = row_colors[i % 2]
            rows.append(
                f'<tr style="background-color:{bg};">'
                f'<td style="padding:10px 14px;font-size:12px;border-bottom:1px solid #f0f0f0;">'
                f'<span style="background-color:#e0e7ff;color:#3730a3;font-size:11px;'
                f'font-weight:600;padding:2px 7px;border-radius:4px;">'
                f"{self._html_escape(source.upper())}</span></td>"
                f'<td style="padding:10px 14px;font-size:12px;color:#6b7280;'
                f'font-family:monospace;word-break:break-all;border-bottom:1px solid #f0f0f0;">'
                f"{self._html_escape(path_str)}</td>"
                f"</tr>"
            )

        rows_html = "\n".join(rows)
        return (
            f'<tr><td style="padding:20px 32px 0;">'
            f'<h2 style="margin:0 0 12px;font-size:14px;font-weight:700;color:#1a1f36;'
            f"text-transform:uppercase;letter-spacing:1px;border-left:3px solid #5b6af0;"
            f'padding-left:10px;">报告路径</h2>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;border:1px solid #e8ebf0;border-radius:8px;overflow:hidden;">'
            f"{rows_html}"
            f"</table></td></tr>"
        )

    def _collect_attachments(self, result: RunResult) -> List[Path]:
        """收集报告文件作为邮件附件"""
        attachments = []
        for source, path_str in result.report_paths.items():
            path = Path(path_str)
            if path.exists() and path.is_file():
                attachments.append(path)
        return attachments

    # ------------------------------------------------------------------
    # 研究趋势通知格式化
    # ------------------------------------------------------------------

    def _format_trend_subject(self, result: TrendRunResult) -> str:
        status = "SUCCESS" if result.success else "FAILED"
        keywords_str = ", ".join(result.keywords)
        return f"ArXiv Trend Research - {status} ({keywords_str}) ({result.run_timestamp})"

    def _format_trend_body(self, result: TrendRunResult) -> str:
        """使用模板格式化趋势分析通知正文"""
        template_name = "research_success" if result.success else "research_failure"
        template = _load_template(template_name)

        keywords_str = ", ".join(result.keywords)
        date_range = f"{result.date_from} ~ {result.date_to}"

        # 报告路径
        report_lines = []
        if result.report_paths:
            report_lines.append("**报告路径**")
            for fmt, path in result.report_paths.items():
                report_lines.append(f"> `{fmt}` {path}")
        report_list = "\n".join(report_lines)

        if template:
            return _render_template(
                template,
                status="SUCCESS" if result.success else "FAILED",
                timestamp=result.run_timestamp,
                keywords=keywords_str,
                date_range=date_range,
                total_papers=result.total_papers,
                tldr_count=result.tldr_count,
                trend_skills_count=result.trend_skills_count,
                report_list=report_list,
                error_message=result.error_message or "无",
                token_usage_section=self._format_token_section_md(result.token_usage),
            )

        # 降级为纯文本
        return self._format_trend_body_fallback(result)

    def _format_trend_body_fallback(self, result: TrendRunResult) -> str:
        """趋势通知模板不存在时的兜底纯文本"""
        status_icon = "OK" if result.success else "ERROR"
        lines = [
            f"Status: {status_icon}",
            f"Time: {result.run_timestamp}",
            f"Keywords: {', '.join(result.keywords)}",
            f"Date Range: {result.date_from} ~ {result.date_to}",
            "",
            f"Papers Found: {result.total_papers}",
            f"TLDRs Generated: {result.tldr_count}",
            f"Trend Skills: {result.trend_skills_count}",
        ]

        if result.error_message:
            lines.append("")
            lines.append(f"Error: {result.error_message}")

        if result.report_paths:
            lines.append("")
            lines.append("Reports:")
            for fmt, path in result.report_paths.items():
                lines.append(f"  [{fmt}] {path}")

        return "\n".join(lines)

    def _format_trend_html_body(self, result: TrendRunResult) -> Optional[str]:
        """使用 HTML 模板生成趋势分析邮件正文"""
        template_name = "research_success" if result.success else "research_failure"
        template = _load_email_template(template_name)
        if not template:
            return None

        keywords_str = self._html_escape(", ".join(result.keywords))
        date_range = self._html_escape(f"{result.date_from} ~ {result.date_to}")

        # 报告路径 HTML
        report_rows = []
        row_colors = ["#ffffff", "#f9fafb"]
        for i, (fmt, path_str) in enumerate(sorted(result.report_paths.items())):
            bg = row_colors[i % 2]
            report_rows.append(
                f'<tr style="background-color:{bg};">'
                f'<td style="padding:10px 14px;font-size:12px;border-bottom:1px solid #f0f0f0;">'
                f'<span style="background-color:#e0e7ff;color:#3730a3;font-size:11px;'
                f'font-weight:600;padding:2px 7px;border-radius:4px;">'
                f"{self._html_escape(fmt.upper())}</span></td>"
                f'<td style="padding:10px 14px;font-size:12px;color:#6b7280;'
                f'font-family:monospace;word-break:break-all;border-bottom:1px solid #f0f0f0;">'
                f"{self._html_escape(path_str)}</td>"
                f"</tr>"
            )
        report_rows_html = (
            "\n".join(report_rows)
            if report_rows
            else (
                '<tr><td colspan="2" style="padding:14px;text-align:center;'
                'font-size:13px;color:#9ca3af;">暂无报告</td></tr>'
            )
        )

        return _render_template(
            template,
            timestamp=self._html_escape(result.run_timestamp),
            keywords=keywords_str,
            date_range=date_range,
            total_papers=result.total_papers,
            tldr_count=result.tldr_count,
            trend_skills_count=result.trend_skills_count,
            report_rows_html=report_rows_html,
            error_message=self._html_escape(result.error_message or "无"),
            token_usage_html=self._format_token_section_html(result.token_usage),
        )

    def _collect_trend_attachments(self, result: TrendRunResult) -> List[Path]:
        """收集趋势报告文件作为邮件附件"""
        attachments = []
        for fmt, path_str in result.report_paths.items():
            path = Path(path_str)
            if path.exists() and path.is_file():
                attachments.append(path)
        return attachments
