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
import html
import logging
import smtplib
import hashlib
import hmac
import base64
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Dict, Optional, Any

import requests

from utils.safe_url import safe_configured_http_url, safe_http_url
from utils.safe_markdown import markdown_link, markdown_text

logger = logging.getLogger(__name__)

# 模板目录
TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "templates" / "notifications"
)
EMAIL_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "configs" / "templates" / "email"
)


def _load_template(name: str, platform: Optional[str] = None) -> Optional[str]:
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
    # A successful past-date report may still leave papers for the same date
    # in the durable queue because the per-report cap was reached or an
    # individual paper needs a stage retry.  The queue runner uses this value
    # to continue that date rather than incorrectly marking it complete.
    deferred_paper_count: int = 0
    papers_by_source: Dict[str, int] = field(default_factory=dict)
    qualified_by_source: Dict[str, int] = field(default_factory=dict)
    analyzed_by_source: Dict[str, int] = field(default_factory=dict)
    report_paths: Dict[str, str] = field(default_factory=dict)
    total_qualified: int = 0
    total_analyzed: int = 0
    success: bool = True
    interrupted: bool = False
    error_message: Optional[str] = None
    # A report can be safely delivered while some individual papers/stages are
    # deferred for retry.  Keep those bounded details visible to the user
    # instead of presenting a misleading all-clear notification.
    issues: List[str] = field(default_factory=list)
    top_papers: List[Dict[str, Any]] = field(default_factory=list)
    token_usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    """Summary for one user-visible, multi-step background workflow.

    Daily reports have their own richer notification format.  Long-running
    operations such as legacy imports and historical-report queues instead
    use this compact, generic shape so their internal batches do not create a
    burst of duplicate notifications.
    """

    workflow: str
    run_timestamp: str = ""
    success: bool = True
    interrupted: bool = False
    summary: Dict[str, Any] = field(default_factory=dict)
    # Non-terminal degradation details: a task may finish successfully while
    # a retryable repair, skipped source, or capped sub-batch remains.
    issues: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


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
    interrupted: bool = False
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

    def __init__(
        self,
        platform: str,
        webhook_url: str,
        *,
        proxies: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        normalized_url = safe_configured_http_url(webhook_url)
        if not normalized_url:
            raise ValueError("Webhook URL 必须是无内嵌凭据的有效 HTTP(S) 地址")
        self.platform = platform
        self.webhook_url = normalized_url
        self.proxies = dict(proxies) if proxies else None
        self.extra = kwargs  # secret, chat_id 等

    def send(self, subject: str, body: str, attachments: Optional[List[Path]] = None) -> bool:
        formatter = getattr(self, f"_format_{self.platform}", self._format_generic)
        url, payload, headers = formatter(subject, body)
        # Formatter-generated URLs can add a DingTalk signature, so validate
        # again immediately before the network request.  Do not follow a
        # webhook redirect: the final endpoint should be explicitly configured
        # and a 307/308 could otherwise resend the full notification body.
        if not safe_configured_http_url(url):
            raise ValueError("Webhook 格式化后生成了无效 URL")
        request_kwargs = {
            "json": payload,
            "headers": headers,
            "timeout": 30,
            "allow_redirects": False,
        }
        if self.proxies:
            request_kwargs["proxies"] = self.proxies
        resp = requests.post(url, **request_kwargs)
        resp.raise_for_status()
        self._validate_platform_response(resp)
        logger.info(f"Webhook [{self.platform}] 通知已发送")
        return True

    def _validate_platform_response(self, response) -> None:
        """Reject application-level webhook errors hidden behind HTTP 2xx."""
        if self.platform == "generic":
            return

        if self.platform == "slack":
            # Slack incoming webhooks conventionally return a bare `ok` body,
            # rather than JSON.
            if response.text.strip().lower() != "ok":
                raise RuntimeError(f"Webhook [slack] 业务失败: {response.text[:500]!r}")
            return

        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Webhook [{self.platform}] 返回了无法验证的非 JSON 成功响应"
            ) from exc

        if not isinstance(body, dict):
            raise RuntimeError(f"Webhook [{self.platform}] 返回了无效响应: {body!r}")

        if self.platform in ("wechat_work", "dingtalk"):
            if body.get("errcode") != 0:
                raise RuntimeError(
                    f"Webhook [{self.platform}] 业务失败: "
                    f"errcode={body.get('errcode')!r}, errmsg={body.get('errmsg', '')!r}"
                )
        elif self.platform == "telegram":
            if body.get("ok") is not True:
                raise RuntimeError(
                    f"Webhook [telegram] 业务失败: "
                    f"description={body.get('description', '')!r}"
                )

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
            # Add query parameters structurally.  The old string concatenation
            # generated an invalid URL when a custom endpoint had no existing
            # query, and double-escaped some signatures.
            parsed = urllib.parse.urlsplit(url)
            query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            query_pairs.extend(
                [
                    ("timestamp", timestamp),
                    ("sign", base64.b64encode(hmac_code).decode("ascii")),
                ]
            )
            url = urllib.parse.urlunsplit(
                parsed._replace(query=urllib.parse.urlencode(query_pairs))
            )

        payload = {"msgtype": "markdown", "markdown": {"title": subject, "text": body}}
        return url, payload, {"Content-Type": "application/json"}

    def _format_telegram(self, subject: str, body: str):
        """Telegram Bot"""
        chat_id = self.extra.get("chat_id", "")
        text = f"<b>{html.escape(subject)}</b>\n\n{body}"
        # Telegram 消息限 4096 字符
        if len(text) > 4000:
            text = text[:3900] + "\n\n...(内容已截断)"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
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


def _test_notification_content(platform: Optional[str] = None) -> tuple[str, str, str]:
    """Return one small, recognizable notification used by channel tests."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = "ArXiv Daily Researcher · 通知测试"
    if platform == "telegram":
        body = "\n".join(
            [
                "<b>ArXiv Daily Researcher</b>",
                "<b>通知测试</b>",
                f"<blockquote>时间：{html.escape(timestamp)}\n该渠道可正常接收通知。</blockquote>",
            ]
        )
    else:
        status = (
            '<font color="info">**通知测试**</font>'
            if platform in {"wechat_work", "dingtalk"}
            else "**通知测试**"
        )
        body = "\n".join(
            [
                "## ArXiv Daily Researcher",
                "",
                status,
                f"> 时间：{timestamp}",
                "> 该渠道可正常接收通知。",
            ]
        )
    html_body = (
        '<!doctype html><html lang="zh-CN"><body style="margin:0;padding:0;'
        'background:#f0f4f8;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Arial,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f0f4f8;">'
        '<tr><td align="center" style="padding:32px 16px;"><table width="600" cellpadding="0" cellspacing="0" '
        'role="presentation" style="max-width:600px;width:100%;overflow:hidden;background:#fff;border-radius:12px;">'
        '<tr><td style="padding:28px 32px;background:linear-gradient(135deg,#1a1f36,#2d3561);">'
        '<p style="margin:0 0 6px;color:#a8b2d8;font-size:12px;letter-spacing:1.2px;text-transform:uppercase;">'
        "ArXiv Daily Researcher</p>"
        '<h1 style="margin:0;color:#fff;font-size:23px;line-height:1.3;">通知测试</h1></td></tr>'
        '<tr><td style="padding:28px 32px 32px;"><table width="100%" cellpadding="0" cellspacing="0" '
        'role="presentation" style="border:1px solid #d9e0ff;border-radius:9px;background:#f7f8ff;"><tr><td '
        'style="padding:16px 18px;color:#344160;font-size:14px;line-height:1.65;">'
        f'<strong style="color:#465fd5;">该渠道可正常接收通知。</strong><br>时间：{html.escape(timestamp)}'
        '</td></tr></table></td></tr>'
        '<tr><td style="padding:0 32px 26px;color:#98a3b5;font-size:12px;text-align:center;">'
        "ArXiv Daily Researcher</td></tr></table></td></tr></table></body></html>"
    )
    return subject, body, html_body


def send_test_notification(
    channel: str,
    values: Dict[str, str],
    *,
    proxies: Optional[Dict[str, str]] = None,
) -> None:
    """Send a real test message without depending on enabled-channel switches.

    The WebUI can therefore validate credentials before the operator saves or
    enables a channel.  ``values`` is deliberately supplied by the caller so
    no secret needs to be returned to the browser or written to disk first.
    """
    channel = str(channel or "").strip().lower()

    def value(key: str, *, required: bool = False) -> str:
        text = str(values.get(key) or "").strip()
        if required and not text:
            raise ValueError(f"请填写 {key}。")
        return text

    if channel == "email":
        host = value("SMTP_HOST", required=True)
        raw_port = value("SMTP_PORT") or "587"
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("SMTP_PORT 必须是 1 到 65535 的整数。") from exc
        if not 1 <= port <= 65535:
            raise ValueError("SMTP_PORT 必须是 1 到 65535 的整数。")
        user = value("SMTP_USER")
        from_addr = value("SMTP_FROM") or user
        if not from_addr:
            raise ValueError("请填写 SMTP_FROM 或 SMTP_USER。")
        recipients = [item.strip() for item in value("SMTP_TO", required=True).split(",") if item.strip()]
        if not recipients:
            raise ValueError("请至少填写一个收件人。")
        subject, body, html_body = _test_notification_content()
        tls_value = values.get("SMTP_USE_TLS")
        if tls_value in (None, ""):
            tls_value = "true"
        EmailNotifier(
            host=host,
            port=port,
            user=user,
            password=value("SMTP_PASSWORD"),
            from_addr=from_addr,
            to_addrs=recipients,
            use_tls=str(tls_value).strip().lower() in {"1", "true", "yes", "on"},
        ).send(subject, body, html_body=html_body)
        return

    platform = {
        "wechat_work": "wechat_work",
        "dingtalk": "dingtalk",
        "telegram": "telegram",
        "slack": "slack",
        "generic": "generic",
    }.get(channel)
    if platform is None:
        raise ValueError("不支持的通知渠道。")

    if channel == "wechat_work":
        notifier = WebhookNotifier(platform, value("WECHAT_WEBHOOK_URL", required=True), proxies=proxies)
    elif channel == "dingtalk":
        notifier = WebhookNotifier(
            platform,
            value("DINGTALK_WEBHOOK_URL", required=True),
            proxies=proxies,
            secret=value("DINGTALK_SECRET"),
        )
    elif channel == "telegram":
        token = value("TELEGRAM_BOT_TOKEN", required=True)
        if any(character.isspace() for character in token):
            raise ValueError("TELEGRAM_BOT_TOKEN 格式无效。")
        notifier = WebhookNotifier(
            platform,
            f"https://api.telegram.org/bot{token}/sendMessage",
            proxies=proxies,
            chat_id=value("TELEGRAM_CHAT_ID", required=True),
        )
    elif channel == "slack":
        notifier = WebhookNotifier(platform, value("SLACK_WEBHOOK_URL", required=True), proxies=proxies)
    else:
        notifier = WebhookNotifier(platform, value("GENERIC_WEBHOOK_URL", required=True), proxies=proxies)

    subject, body, _html_body = _test_notification_content(platform)
    notifier.send(subject, body)


class NotifierAgent:
    """通知编排代理，管理所有已配置的通知渠道"""

    def __init__(self):
        from config import settings

        self.settings = settings
        self.notifiers: List[BaseNotifier] = []
        self.notifiers_by_channel: Dict[str, BaseNotifier] = {}
        self._setup_notifiers()

    def _register_notifier(self, channel: str, notifier: BaseNotifier) -> None:
        """Register a uniquely addressable notifier for direct outbox dispatch."""
        if channel in self.notifiers_by_channel:
            raise ValueError(f"重复的通知渠道配置: {channel}")
        self.notifiers.append(notifier)
        self.notifiers_by_channel[channel] = notifier

    def _setup_notifiers(self):
        """根据配置初始化通知渠道"""
        s = self.settings
        try:
            notification_proxies = s.get_proxy_dict("notifications")
        except (AttributeError, TypeError):
            notification_proxies = None

        def register_webhook(channel: str, platform: str, url: str, **kwargs) -> None:
            try:
                self._register_notifier(
                    channel,
                    WebhookNotifier(platform, url, proxies=notification_proxies, **kwargs),
                )
            except ValueError as exc:
                # A malformed optional channel must never prevent report
                # finalization or poison all other configured channels.  The
                # user gets a visible, non-secret log message and can correct
                # it in the WebUI/config file.
                logger.error("未启用通知渠道 %s：%s", channel, exc)

        # Email
        if s.NOTIFY_EMAIL_ENABLED and s.SMTP_HOST and s.SMTP_TO:
            to_addrs = [a.strip() for a in s.SMTP_TO.split(",") if a.strip()]
            self._register_notifier(
                "email",
                EmailNotifier(
                    host=s.SMTP_HOST,
                    port=s.SMTP_PORT,
                    user=s.SMTP_USER,
                    password=s.SMTP_PASSWORD,
                    from_addr=s.SMTP_FROM,
                    to_addrs=to_addrs,
                    use_tls=s.SMTP_USE_TLS,
                ),
            )
            logger.info("已启用邮件通知")

        # 企业微信
        if s.NOTIFY_WECHAT_ENABLED and s.WECHAT_WEBHOOK_URL:
            register_webhook("wechat_work", "wechat_work", s.WECHAT_WEBHOOK_URL)
            if "wechat_work" in self.notifiers_by_channel:
                logger.info("已启用企业微信通知")

        # 钉钉
        if s.NOTIFY_DINGTALK_ENABLED and s.DINGTALK_WEBHOOK_URL:
            register_webhook(
                "dingtalk",
                "dingtalk",
                s.DINGTALK_WEBHOOK_URL,
                secret=s.DINGTALK_SECRET,
            )
            if "dingtalk" in self.notifiers_by_channel:
                logger.info("已启用钉钉通知")

        # Telegram
        if s.NOTIFY_TELEGRAM_ENABLED and s.TELEGRAM_BOT_TOKEN and s.TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage"
            register_webhook("telegram", "telegram", url, chat_id=s.TELEGRAM_CHAT_ID)
            if "telegram" in self.notifiers_by_channel:
                logger.info("已启用 Telegram 通知")

        # Slack
        if s.NOTIFY_SLACK_ENABLED and s.SLACK_WEBHOOK_URL:
            register_webhook("slack", "slack", s.SLACK_WEBHOOK_URL)
            if "slack" in self.notifiers_by_channel:
                logger.info("已启用 Slack 通知")

        # 通用 Webhook
        if s.NOTIFY_GENERIC_WEBHOOK_ENABLED and s.GENERIC_WEBHOOK_URL:
            register_webhook("generic", "generic", s.GENERIC_WEBHOOK_URL)
            if "generic" in self.notifiers_by_channel:
                logger.info("已启用通用 Webhook 通知")

    # ------------------------------------------------------------------
    # 运行结果通知
    # ------------------------------------------------------------------

    def configured_channels(self) -> List[str]:
        """Return stable outbox channel identifiers for currently configured senders."""
        return list(self.notifiers_by_channel)

    def send_run_result_to_channel(self, channel: str, result: RunResult) -> None:
        """Send one daily result through exactly one configured channel.

        Unlike :meth:`notify`, errors intentionally propagate to the caller so
        the durable outbox can retain and retry the specific failed channel.
        """
        notifier = self.notifiers_by_channel.get(channel)
        if notifier is None:
            raise LookupError(f"通知渠道当前未配置: {channel}")

        subject = self._format_subject(result)
        html_body = self._format_html_body(result)
        attachments = self._collect_attachments(result) if self.settings.NOTIFY_ATTACH_REPORTS else []
        platform = self._platform_for_notifier(notifier)
        body = self._format_body_for_platform(result, platform)
        if isinstance(notifier, EmailNotifier) and html_body:
            notifier.send(subject, body, attachments, html_body=html_body)
        else:
            notifier.send(subject, body, attachments)

    def notify(self, result: RunResult) -> Dict[str, Optional[str]]:
        """Best-effort backwards-compatible direct notification API.

        Returns one result per channel instead of hiding individual failures.
        Daily report delivery uses the durable outbox methods below.
        """
        if not self.notifiers:
            logger.debug("未配置任何通知渠道，跳过")
            return {}

        if result.success and not self.settings.NOTIFY_ON_SUCCESS:
            return {}
        if not result.success and not self.settings.NOTIFY_ON_FAILURE:
            return {}

        outcomes: Dict[str, Optional[str]] = {}
        for channel in self.configured_channels():
            try:
                self.send_run_result_to_channel(channel, result)
                outcomes[channel] = None
            except Exception as e:
                outcomes[channel] = str(e)
                logger.warning("通知发送失败 (%s): %s", channel, e)
        return outcomes

    def enqueue_run_result(self, store, run_id: str, result: RunResult) -> int:
        """Queue an eligible daily result once per configured channel.

        The payload contains report metadata, not credentials.  It stays usable
        after process restart and is rendered again immediately before delivery.
        """
        if result.success and not self.settings.NOTIFY_ON_SUCCESS:
            return 0
        if not result.success and not self.settings.NOTIFY_ON_FAILURE:
            return 0

        payload = {"result": asdict(result)}
        created = 0
        for channel in self.configured_channels():
            if store.enqueue_notification(run_id, "daily_run_result", channel, payload):
                created += 1
        return created

    def deliver_pending_run_results(self, store, limit: int = 100) -> Dict[str, int]:
        """Deliver due daily-report notifications, preserving per-channel state.

        A failed channel is rescheduled in the outbox; it never changes the
        completed-paper state or reopens an already delivered report as new work.
        """
        rows = store.claim_due_notifications(event_type="daily_run_result", limit=limit)
        summary = {"claimed": len(rows), "sent": 0, "deferred": 0}
        max_attempts = max(1, int(getattr(self.settings, "RETRY_MAX_ATTEMPTS", 3)))

        for row in rows:
            outbox_id = row["outbox_id"]
            channel = row["channel"]
            try:
                payload = json.loads(row["payload_json"])
                result = RunResult(**payload["result"])
            except Exception as exc:
                store.reschedule_notification(
                    outbox_id,
                    f"通知 payload 无法恢复: {exc}",
                    self._retry_delay(max_attempts),
                )
                logger.error("通知 outbox payload 无法恢复 (id=%s): %s", outbox_id, exc)
                summary["deferred"] += 1
                continue

            if channel not in self.notifiers_by_channel:
                delay = max(60, self._retry_delay(max_attempts))
                store.reschedule_notification(
                    outbox_id,
                    f"通知渠道当前未配置: {channel}",
                    delay,
                )
                logger.warning(
                    "通知 outbox 暂不发送 (id=%s, channel=%s)：渠道未配置，%ss 后重试",
                    outbox_id,
                    channel,
                    delay,
                )
                summary["deferred"] += 1
                continue

            sent = False
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    self.send_run_result_to_channel(channel, result)
                    store.mark_notification_sent(outbox_id)
                    summary["sent"] += 1
                    sent = True
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max_attempts:
                        wait_seconds = self._retry_delay(attempt)
                        logger.warning(
                            "通知发送失败，将重试 (outbox=%s, channel=%s, %s/%s, %ss): %s",
                            outbox_id,
                            channel,
                            attempt,
                            max_attempts,
                            wait_seconds,
                            exc,
                        )
                        store.increment_notification_attempt(outbox_id)
                        time.sleep(wait_seconds)

            if not sent:
                retry_after = self._retry_delay(max_attempts)
                store.reschedule_notification(outbox_id, str(last_error), retry_after)
                logger.error(
                    "通知多次发送失败，已保留待补发 (outbox=%s, channel=%s, %ss 后重试): %s",
                    outbox_id,
                    channel,
                    retry_after,
                    last_error,
                )
                summary["deferred"] += 1

        return summary

    # ------------------------------------------------------------------
    # 长任务汇总通知
    # ------------------------------------------------------------------

    def send_workflow_result_to_channel(self, channel: str, result: WorkflowResult) -> None:
        """Send one consolidated background-workflow result to one channel.

        These messages deliberately have no report attachments: a legacy
        import can touch hundreds of historical reports, while the useful
        notification is the compact status/count summary.
        """
        notifier = self.notifiers_by_channel.get(channel)
        if notifier is None:
            raise LookupError(f"通知渠道当前未配置: {channel}")

        subject = self._format_workflow_subject(result)
        platform = self._platform_for_notifier(notifier)
        body = self._format_workflow_body_for_platform(result, platform)
        if isinstance(notifier, EmailNotifier):
            notifier.send(subject, body, html_body=self._format_workflow_html_body(result))
        else:
            notifier.send(subject, body)

    def notify_workflow(self, result: WorkflowResult) -> Dict[str, Optional[str]]:
        """Best-effort fallback for a workflow without a writable SQLite store."""
        if not self.notifiers:
            logger.debug("未配置任何通知渠道，跳过")
            return {}
        if result.success and not self.settings.NOTIFY_ON_SUCCESS:
            return {}
        if not result.success and not self.settings.NOTIFY_ON_FAILURE:
            return {}

        outcomes: Dict[str, Optional[str]] = {}
        for channel in self.configured_channels():
            try:
                self.send_workflow_result_to_channel(channel, result)
                outcomes[channel] = None
            except Exception as exc:
                outcomes[channel] = str(exc)
                logger.warning("长任务通知发送失败 (%s): %s", channel, exc)
        return outcomes

    def enqueue_workflow_result(self, store, run_id: str, result: WorkflowResult) -> int:
        """Persist one consolidated large-task notification per channel.

        The generic ``workflow_result`` event is shared by legacy imports,
        automatic supplement workflows, and historical-report queues.  Its
        uniqueness key keeps a retry/restart from sending a second summary.
        """
        if result.success and not self.settings.NOTIFY_ON_SUCCESS:
            return 0
        if not result.success and not self.settings.NOTIFY_ON_FAILURE:
            return 0

        payload = {"result": asdict(result)}
        created = 0
        for channel in self.configured_channels():
            if store.enqueue_notification(run_id, "workflow_result", channel, payload):
                created += 1
        return created

    def deliver_pending_workflow_results(self, store, limit: int = 100) -> Dict[str, int]:
        """Deliver durable long-task summaries with the same retry policy as reports."""
        rows = store.claim_due_notifications(event_type="workflow_result", limit=limit)
        summary = {"claimed": len(rows), "sent": 0, "deferred": 0}
        max_attempts = max(1, int(getattr(self.settings, "RETRY_MAX_ATTEMPTS", 3)))

        for row in rows:
            outbox_id = row["outbox_id"]
            channel = row["channel"]
            try:
                payload = json.loads(row["payload_json"])
                result = WorkflowResult(**payload["result"])
            except Exception as exc:
                store.reschedule_notification(
                    outbox_id,
                    f"长任务通知 payload 无法恢复: {exc}",
                    self._retry_delay(max_attempts),
                )
                logger.error("长任务通知 outbox payload 无法恢复 (id=%s): %s", outbox_id, exc)
                summary["deferred"] += 1
                continue

            if channel not in self.notifiers_by_channel:
                delay = max(60, self._retry_delay(max_attempts))
                store.reschedule_notification(
                    outbox_id,
                    f"通知渠道当前未配置: {channel}",
                    delay,
                )
                logger.warning(
                    "长任务通知 outbox 暂不发送 (id=%s, channel=%s)：渠道未配置，%ss 后重试",
                    outbox_id,
                    channel,
                    delay,
                )
                summary["deferred"] += 1
                continue

            sent = False
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    self.send_workflow_result_to_channel(channel, result)
                    store.mark_notification_sent(outbox_id)
                    summary["sent"] += 1
                    sent = True
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max_attempts:
                        wait_seconds = self._retry_delay(attempt)
                        logger.warning(
                            "长任务通知发送失败，将重试 (outbox=%s, channel=%s, %s/%s, %ss): %s",
                            outbox_id,
                            channel,
                            attempt,
                            max_attempts,
                            wait_seconds,
                            exc,
                        )
                        store.increment_notification_attempt(outbox_id)
                        time.sleep(wait_seconds)

            if not sent:
                retry_after = self._retry_delay(max_attempts)
                store.reschedule_notification(outbox_id, str(last_error), retry_after)
                logger.error(
                    "长任务通知多次发送失败，已保留待补发 (outbox=%s, channel=%s, %ss 后重试): %s",
                    outbox_id,
                    channel,
                    retry_after,
                    last_error,
                )
                summary["deferred"] += 1

        return summary

    def _retry_delay(self, attempt: int) -> int:
        """Use the project's bounded exponential retry policy for outbox rows."""
        minimum = max(1, int(getattr(self.settings, "RETRY_MIN_WAIT", 2)))
        maximum = max(minimum, int(getattr(self.settings, "RETRY_MAX_WAIT", 30)))
        return min(minimum * (2 ** max(0, attempt - 1)), maximum)

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
        html_body = self._format_trend_html_body(result)
        attachments = (
            self._collect_trend_attachments(result) if self.settings.NOTIFY_ATTACH_REPORTS else []
        )

        for notifier in self.notifiers:
            try:
                platform = self._platform_for_notifier(notifier)
                body = self._format_trend_body_for_platform(result, platform)
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

        subject = (
            "ArXiv Daily Researcher - ERROR "
            f"({markdown_text(kwargs.get('timestamp', ''), multiline=False)})"
        )

        for notifier in self.notifiers:
            try:
                platform = self._platform_for_notifier(notifier)
                body = self._format_error_body_for_platform(template_name, platform, **kwargs)
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

    @staticmethod
    def _workflow_value_text(value: Any) -> str:
        """Render a bounded, serializable workflow metric without leaking structure."""
        if isinstance(value, (dict, list, tuple)):
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
        else:
            text = str(value)
        text = " ".join(text.split())
        return text[:600] + ("…" if len(text) > 600 else "")

    def _workflow_summary_items(self, result: WorkflowResult) -> list[tuple[str, str]]:
        """Return only meaningful, bounded workflow summary fields in input order."""
        items: list[tuple[str, str]] = []
        for raw_label, raw_value in result.summary.items():
            if raw_value is None or raw_value == "":
                continue
            label = self._workflow_value_text(raw_label)
            value = self._workflow_value_text(raw_value)
            if label and value:
                items.append((label, value))
        return items[:20]

    def _workflow_issues(self, result: WorkflowResult) -> list[str]:
        """Keep actionable degraded-step details without turning a notice into a log."""
        issues = []
        for issue in result.issues:
            text = self._workflow_value_text(issue)
            if text:
                issues.append(text)
        return issues[:8] + ([f"另有 {len(issues) - 8} 项问题，详见运行日志"] if len(issues) > 8 else [])

    def _format_workflow_subject(self, result: WorkflowResult) -> str:
        status = "INTERRUPTED" if result.interrupted else ("SUCCESS" if result.success else "FAILED")
        workflow = markdown_text(result.workflow, multiline=False)
        timestamp = markdown_text(result.run_timestamp, multiline=False)
        return f"ArXiv Daily Researcher - {workflow} {status} ({timestamp})"

    @staticmethod
    def _workflow_status_label(result: WorkflowResult) -> str:
        if result.interrupted:
            return "已中断"
        return "已完成" if result.success else "失败"

    @staticmethod
    def _workflow_status_theme(result: WorkflowResult) -> tuple[str, str, str, str, str]:
        """Return the shared status label and email palette for one workflow."""
        if result.interrupted:
            return "已中断", "⏸ 已中断", "#5a3811", "#d97706", "#fff7ed"
        if result.success:
            return "已完成", "✓ 已完成", "#1a1f36", "#27ae60", "#eaf6f0"
        return "失败", "✕ 失败", "#4a1f1f", "#e53e3e", "#fff5f5"

    def _format_workflow_body_for_platform(
        self, result: WorkflowResult, platform: Optional[str]
    ) -> str:
        """Render the same compact workflow notice for every chat platform."""
        status = self._workflow_status_label(result)
        if platform == "telegram":
            summary = self._workflow_summary_items(result)
            lines = [
                "<b>ArXiv Daily Researcher</b>",
                f"<b>{self._html_escape(result.workflow)}：{status}</b>",
                f"<blockquote>时间：<code>{self._html_escape(result.run_timestamp)}</code></blockquote>",
            ]
            if summary:
                summary_lines = "\n".join(
                    f"<b>{self._html_escape(label)}</b>：{self._html_escape(value)}"
                    for label, value in summary
                )
                lines.extend(["<b>运行摘要</b>", f"<blockquote>{summary_lines}</blockquote>"])
            issues = self._workflow_issues(result)
            if issues:
                lines.extend(
                    [
                        "<b>注意事项</b>",
                        f"<blockquote>{self._html_escape(chr(10).join('• ' + issue for issue in issues))}</blockquote>",
                    ]
                )
            if result.error_message:
                lines.extend(
                    [
                        "<b>错误摘要</b>",
                        f"<blockquote>{self._html_escape(self._workflow_value_text(result.error_message))}</blockquote>",
                    ]
                )
            return "\n".join(lines)

        color = "info" if result.success and not result.interrupted else "warning"
        colored_status = (
            f'<font color="{color}">**{markdown_text(result.workflow, multiline=False)}：{status}**</font>'
            if platform in {"wechat_work", "dingtalk"}
            else f"**{markdown_text(result.workflow, multiline=False)}：{status}**"
        )
        lines = [
            "## ArXiv Daily Researcher",
            "",
            colored_status,
            f"> 时间：{markdown_text(result.run_timestamp, multiline=False)}",
        ]
        summary = self._workflow_summary_items(result)
        if summary:
            lines.extend(["", "**运行摘要**"])
        for label, value in summary:
            lines.append(
                f"> **{markdown_text(label, multiline=False)}**："
                f"{markdown_text(value, multiline=False)}"
            )
        issues = self._workflow_issues(result)
        if issues:
            lines.extend(["", "**注意事项**"])
            lines.extend(f"- {markdown_text(issue, multiline=False)}" for issue in issues)
        if result.error_message:
            lines.extend(
                [
                    "",
                    "**错误摘要**",
                    f"> {markdown_text(self._workflow_value_text(result.error_message))}",
                ]
            )
        return "\n".join(lines)

    def _format_workflow_html_body(self, result: WorkflowResult) -> str:
        """Build a status-card email matching the daily-report visual language."""
        status, badge, header_color, accent_color, surface_color = self._workflow_status_theme(result)
        row_html = "".join(
            f'<tr style="background:{"#ffffff" if index % 2 == 0 else "#fafbfe"};">'
            f'<th style="width:38%;padding:11px 14px;text-align:left;color:#526078;font-size:12px;'
            f'font-weight:650;border-bottom:1px solid #e8ebf0;vertical-align:top;">{self._html_escape(label)}</th>'
            f'<td style="padding:11px 14px;color:#1f2937;font-size:13px;line-height:1.55;'
            f'word-break:break-word;border-bottom:1px solid #e8ebf0;">{self._html_escape(value)}</td></tr>'
            for index, (label, value) in enumerate(self._workflow_summary_items(result))
        )
        summary_html = ""
        if row_html:
            summary_html = (
                '<tr><td style="padding:26px 32px 0;">'
                '<h2 style="margin:0 0 12px;font-size:14px;font-weight:700;color:#1a1f36;'
                f'text-transform:uppercase;letter-spacing:1px;border-left:3px solid {accent_color};padding-left:10px;">运行摘要</h2>'
                '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
                'style="border-collapse:collapse;border:1px solid #e8ebf0;border-radius:8px;overflow:hidden;">'
                f"{row_html}</table></td></tr>"
            )
        error_html = ""
        if result.error_message:
            error_html = (
                '<tr><td style="padding:20px 32px 0;"><table width="100%" cellpadding="0" cellspacing="0" '
                'role="presentation" style="background:#fff5f5;border:1px solid #fed7d7;border-left:4px solid #e53e3e;border-radius:7px;">'
                '<tr><td style="padding:15px 17px;color:#742a2a;font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word;">'
                '<p style="margin:0 0 5px;font-weight:700;color:#c53030;">错误摘要</p>'
                f"{self._html_escape(self._workflow_value_text(result.error_message))}"
                "</td></tr></table></td></tr>"
            )
        issues = self._workflow_issues(result)
        issues_html = ""
        if issues:
            issues_html = (
                '<tr><td style="padding:20px 32px 0;"><table width="100%" cellpadding="0" cellspacing="0" '
                'role="presentation" style="background:#fffbeb;border:1px solid #fde68a;border-left:4px solid #d97706;border-radius:7px;">'
                '<tr><td style="padding:15px 17px;color:#92400e;font-size:13px;line-height:1.6;word-break:break-word;">'
                '<p style="margin:0 0 5px;font-weight:700;">注意事项</p><ul style="margin:0;padding-left:18px;">'
                + "".join(f"<li>{self._html_escape(issue)}</li>" for issue in issues)
                + "</ul></td></tr></table></td></tr>"
            )
        return (
            '<!doctype html><html lang="zh-CN"><body style="margin:0;padding:0;background:#f0f4f8;'
            'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Arial,sans-serif;">'
            '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f0f4f8;">'
            '<tr><td align="center" style="padding:32px 16px;"><table width="600" cellpadding="0" cellspacing="0" '
            'role="presentation" style="max-width:600px;width:100%;overflow:hidden;background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);">'
            f'<tr><td style="padding:28px 32px;background:linear-gradient(135deg,{header_color},#2d3561);">'
            '<table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr><td>'
            '<p style="margin:0 0 6px;color:#a8b2d8;font-size:12px;letter-spacing:1.2px;text-transform:uppercase;">ArXiv Daily Researcher</p>'
            f'<h1 style="margin:0;color:#fff;font-size:24px;line-height:1.3;">{self._html_escape(result.workflow)}</h1>'
            '</td><td align="right" valign="top">'
            f'<span style="display:inline-block;padding:6px 13px;border-radius:16px;background:{accent_color};color:#fff;font-size:12px;font-weight:700;white-space:nowrap;">{badge}</span>'
            '</td></tr><tr><td colspan="2" style="padding-top:15px;color:#a8b2d8;font-size:13px;">'
            f'⏱ {self._html_escape(result.run_timestamp)}</td></tr></table></td></tr>'
            f'<tr><td style="padding:17px 32px;background:{surface_color};border-bottom:1px solid #e8ebf0;color:#475569;font-size:13px;">'
            f'<strong style="color:{accent_color};">{status}</strong> · 后台任务汇总</td></tr>'
            f"{summary_html}{issues_html}{error_html}"
            '<tr><td style="padding:26px 32px 30px;"><p style="margin:0;border-top:1px solid #e8ebf0;padding-top:18px;color:#98a3b5;font-size:12px;text-align:center;">'
            'ArXiv Daily Researcher</p></td></tr>'
            '</table></td></tr></table></body></html>'
        )

    def _run_issues(self, result: RunResult) -> list[str]:
        """Return a bounded list of retryable/degraded daily-report stages."""
        issues = []
        for issue in result.issues:
            text = self._workflow_value_text(issue)
            if text:
                issues.append(text)
        return issues[:8] + ([f"另有 {len(issues) - 8} 项问题，详见运行日志"] if len(issues) > 8 else [])

    def _format_run_issues_markdown(self, result: RunResult) -> str:
        issues = self._run_issues(result)
        if not issues:
            return ""
        lines = ["**注意事项**"]
        lines.extend(f"> {markdown_text(issue, multiline=False)}" for issue in issues)
        return "\n".join(lines)

    def _format_run_issues_html(self, result: RunResult) -> str:
        issues = self._run_issues(result)
        if not issues:
            return ""
        items = "".join(f"<li>{self._html_escape(issue)}</li>" for issue in issues)
        return (
            '<tr><td style="padding:24px 32px 0;">'
            '<div style="padding:14px 16px;background:#fffbeb;border:1px solid #fde68a;'
            'border-left:4px solid #d97706;border-radius:6px;color:#92400e;">'
            '<p style="margin:0 0 6px;font-size:13px;font-weight:700;">注意事项</p>'
            f'<ul style="margin:0;padding-left:20px;font-size:13px;line-height:1.6;">{items}</ul>'
            "</div></td></tr>"
        )

    def _format_subject(self, result: RunResult) -> str:
        status = "SUCCESS" if result.success else "FAILED"
        return (
            f"ArXiv Daily Researcher - {status} "
            f"({markdown_text(result.run_timestamp, multiline=False)})"
        )

    @staticmethod
    def _format_daily_markdown_fragments(result: RunResult) -> tuple[str, str, str]:
        """Render external daily-result fields into safe Markdown fragments."""
        source_lines = []
        for source in sorted(result.papers_by_source.keys()):
            fetched = result.papers_by_source.get(source, 0)
            qualified = result.qualified_by_source.get(source, 0)
            analyzed = result.analyzed_by_source.get(source, 0)
            source_lines.append(
                f"> `{markdown_text(source.upper(), multiline=False)}` 抓取 **{fetched}** | "
                f"及格 **{qualified}** | 分析 **{analyzed}**"
            )

        report_lines = []
        if result.report_paths:
            report_lines.append("**报告路径**")
            for source, path in result.report_paths.items():
                report_lines.append(
                    f"> `{markdown_text(source, multiline=False)}` "
                    f"{markdown_text(path, multiline=False)}"
                )

        top_lines = []
        if result.top_papers:
            top_lines.append(f"**Top {len(result.top_papers)} 论文**")
            for i, paper in enumerate(result.top_papers, 1):
                title = markdown_text(paper.get("title", "")[:60], multiline=False)
                score = paper.get("score", 0)
                relevance_score = paper.get("relevance_score")
                qualification_threshold = paper.get("qualification_threshold")
                has_separate_relevance = bool(paper.get("has_separate_relevance_score"))
                source = markdown_text(paper.get("source", "").upper(), multiline=False)
                tldr = markdown_text(paper.get("tldr", "")[:80], multiline=False)
                link = markdown_link("查看原文", paper.get("url", ""))
                top_lines.append(f"> **{i}.** `{source}` {title}")
                if has_separate_relevance and isinstance(relevance_score, (int, float)) and isinstance(
                    qualification_threshold, (int, float)
                ):
                    score_label = (
                        f"Core relevance: {relevance_score:.1f}/{qualification_threshold:.1f} "
                        f"| Ranking: {score:.1f}"
                    )
                else:
                    score_label = f"Score: {score:.1f}"
                top_lines.append(
                    f'> <font color="comment">{score_label} | {tldr}</font>'
                )
                if link:
                    top_lines.append(f"> {link}")

        return "\n".join(source_lines), "\n".join(report_lines), "\n".join(top_lines)

    def _format_body(self, result: RunResult) -> str:
        """向后兼容：默认使用通用模板（非 Telegram 专用）"""
        template_name = "success" if result.success else "failure"
        template = _load_template(template_name)

        source_summary, report_list, top_papers = self._format_daily_markdown_fragments(result)

        if template:
            return _render_template(
                template,
                status="SUCCESS" if result.success else "FAILED",
                timestamp=markdown_text(result.run_timestamp, multiline=False),
                total_fetched=result.total_papers_fetched,
                total_qualified=result.total_qualified,
                total_analyzed=result.total_analyzed,
                source_summary=source_summary,
                report_list=report_list,
                top_papers=top_papers,
                error_message=markdown_text(result.error_message or "无"),
                issues_section=self._format_run_issues_markdown(result),
                token_usage_section=self._format_token_section_md(result.token_usage),
            )

        # 模板不存在时降级为纯文本
        return self._format_body_fallback(result)

    def _format_body_for_platform(self, result: RunResult, platform: Optional[str]) -> str:
        """使用模板格式化运行结果通知正文，模板不存在时降级为纯文本"""
        if platform == "telegram":
            return self._format_telegram_body(result)

        template_name = "success" if result.success else "failure"
        template = _load_template(template_name)

        source_summary, report_list, top_papers = self._format_daily_markdown_fragments(result)

        if template:
            return _render_template(
                template,
                status="SUCCESS" if result.success else "FAILED",
                timestamp=markdown_text(result.run_timestamp, multiline=False),
                total_fetched=result.total_papers_fetched,
                total_qualified=result.total_qualified,
                total_analyzed=result.total_analyzed,
                source_summary=source_summary,
                report_list=report_list,
                top_papers=top_papers,
                error_message=markdown_text(result.error_message or "无"),
                issues_section=self._format_run_issues_markdown(result),
                token_usage_section=self._format_token_section_md(result.token_usage),
            )

        # 模板不存在时降级为纯文本
        return self._format_body_fallback(result)

    def _format_telegram_body(self, result: RunResult) -> str:
        """Telegram 专用 HTML 正文，使用 Bot API 支持的实体标签。"""
        status_label = "运行成功" if result.success else "运行失败"
        source_lines = []
        for source in sorted(result.papers_by_source.keys()):
            fetched = result.papers_by_source.get(source, 0)
            qualified = result.qualified_by_source.get(source, 0)
            analyzed = result.analyzed_by_source.get(source, 0)
            source_lines.append(
                f"<code>{self._html_escape(source.upper())}</code> 抓取 <b>{fetched}</b> | 及格 <b>{qualified}</b> | 分析 <b>{analyzed}</b>"
            )

        top_cards = []
        if result.top_papers:
            for i, paper in enumerate(result.top_papers, 1):
                title = self._html_escape(paper.get("title", "")[:80])
                score = paper.get("score", 0)
                relevance_score = paper.get("relevance_score")
                qualification_threshold = paper.get("qualification_threshold")
                has_separate_relevance = bool(paper.get("has_separate_relevance_score"))
                src = self._html_escape(paper.get("source", "").upper())
                tldr = self._html_escape(paper.get("tldr", "")[:140])
                url = safe_http_url(paper.get("url", ""))
                score_label = f"Score: <b>{score:.1f}</b>"
                if has_separate_relevance and isinstance(relevance_score, (int, float)) and isinstance(
                    qualification_threshold, (int, float)
                ):
                    score_label = (
                        f"Core relevance: <b>{relevance_score:.1f}</b> / {qualification_threshold:.1f} "
                        f"| Ranking: <b>{score:.1f}</b>"
                    )
                card_lines = [
                    f"<b>{i}. <code>{src}</code> {title}</b>",
                    f"<blockquote>{score_label}",
                    f"{tldr}</blockquote>",
                ]
                if url:
                    card_lines.append(f'<a href="{self._html_escape(url)}">查看原文</a>')
                top_cards.append("\n".join(card_lines))

        report_lines = []
        for source, path in sorted(result.report_paths.items()):
            report_lines.append(f"<code>{self._html_escape(source)}</code> {self._html_escape(path)}")

        sections = [
            "<b>ArXiv Daily Researcher</b>",
            f"<b>{status_label}</b> | {self._html_escape(result.run_timestamp)}",
            "",
            "<b>本次运行统计</b>",
            "\n".join(source_lines) if source_lines else "暂无数据",
        ]

        if result.token_usage and result.token_usage.get("has_data") and self.settings.TOKEN_TRACKING_ENABLED:
            total = result.token_usage.get("total", 0)
            tp = result.token_usage.get("total_prompt", 0)
            tc = result.token_usage.get("total_completion", 0)
            sections.extend(
                [
                    "<b>Token 消耗</b>",
                    (
                        f"<blockquote>总计 <b>{total:,}</b> tokens"
                        f"（输入 {tp:,} / 输出 {tc:,}）</blockquote>"
                    ),
                ]
            )

        issues = self._run_issues(result)
        if issues:
            sections.extend(
                [
                    "<b>注意事项</b>",
                    f"<blockquote>{self._html_escape(chr(10).join('• ' + issue for issue in issues))}</blockquote>",
                ]
            )

        if top_cards:
            sections.extend(["<b>Top 论文</b>", *top_cards])

        if report_lines:
            sections.extend(["<b>报告路径</b>", "\n".join(report_lines)])

        if not result.success and result.error_message:
            sections.extend(["<b>错误信息</b>", f"<blockquote>{self._html_escape(result.error_message)}</blockquote>"])

        return "\n".join(sections)

    def _format_body_fallback(self, result: RunResult) -> str:
        """模板不存在时的兜底纯文本格式（保持向后兼容）"""
        status_icon = "OK" if result.success else "ERROR"
        lines = [
            f"Status: {status_icon}",
            f"Time: {markdown_text(result.run_timestamp, multiline=False)}",
            "",
        ]

        if result.error_message:
            lines.append(f"Error: {markdown_text(result.error_message)}")
            lines.append("")

        issues = self._run_issues(result)
        if issues:
            lines.append("Issues:")
            lines.extend(f"  - {markdown_text(issue, multiline=False)}" for issue in issues)
            lines.append("")

        lines.append("Papers Summary:")
        for source in sorted(result.papers_by_source.keys()):
            fetched = result.papers_by_source.get(source, 0)
            qualified = result.qualified_by_source.get(source, 0)
            analyzed = result.analyzed_by_source.get(source, 0)
            lines.append(
                f"  [{markdown_text(source.upper(), multiline=False)}] Fetched: {fetched} | "
                f"Qualified: {qualified} | Analyzed: {analyzed}"
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
                lines.append(
                    f"  [{markdown_text(source, multiline=False)}] "
                    f"{markdown_text(path, multiline=False)}"
                )

        if result.top_papers:
            lines.append("")
            lines.append(f"Top {len(result.top_papers)} Papers:")
            for i, p in enumerate(result.top_papers, 1):
                title = markdown_text(p.get("title", "")[:80], multiline=False)
                score = p.get("score", 0)
                relevance_score = p.get("relevance_score")
                qualification_threshold = p.get("qualification_threshold")
                has_separate_relevance = bool(p.get("has_separate_relevance_score"))
                src = markdown_text(p.get("source", "").upper(), multiline=False)
                tldr = markdown_text(p.get("tldr", "")[:120], multiline=False)
                url = safe_http_url(p.get("url", ""))
                lines.append(f"  {i}. [{src}] {title}")
                if has_separate_relevance and isinstance(relevance_score, (int, float)) and isinstance(
                    qualification_threshold, (int, float)
                ):
                    score_label = (
                        f"Core relevance: {relevance_score:.1f}/{qualification_threshold:.1f} "
                        f"| Ranking: {score:.1f}"
                    )
                else:
                    score_label = f"Score: {score:.1f}"
                lines.append(f"     {score_label} | {tldr}")
                if url:
                    lines.append(f"     {markdown_text(url, multiline=False)}")

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
            timestamp=self._html_escape(str(result.run_timestamp)),
            total_fetched=result.total_papers_fetched,
            total_qualified=result.total_qualified,
            total_analyzed=result.total_analyzed,
            source_rows=source_rows,
            top_papers_html=top_papers_html,
            report_list_html=report_list_html,
            error_message=self._html_escape(result.error_message or "无"),
            issues_html=self._format_run_issues_html(result),
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
            relevance_score = p.get("relevance_score")
            qualification_threshold = p.get("qualification_threshold")
            has_separate_relevance = bool(p.get("has_separate_relevance_score"))
            src = self._html_escape(p.get("source", "").upper())
            tldr = self._html_escape(p.get("tldr", "")[:200])
            url = safe_http_url(p.get("url", ""))
            link_html = (
                (
                    f'<p style="margin:8px 0 0;">'
                    f'<a href="{self._html_escape(url)}" '
                    f'style="color:#5b6af0;font-size:12px;text-decoration:none;">查看原文 →</a></p>'
                )
                if url
                else ""
            )

            score_label = f'Score: <strong style="color:#1a7a4a;">{score:.1f}</strong>'
            if has_separate_relevance and isinstance(relevance_score, (int, float)) and isinstance(
                qualification_threshold, (int, float)
            ):
                score_label = (
                    f'Core relevance: <strong style="color:#1a7a4a;">{relevance_score:.1f}</strong>'
                    f' / {qualification_threshold:.1f} | Ranking: <strong style="color:#1a7a4a;">{score:.1f}</strong>'
                )
            cards.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
                f'style="margin-bottom:10px;border:1px solid #e8ebf0;border-radius:8px;'
                f'overflow:hidden;border-collapse:separate;">'
                f'<tr><td style="padding:14px 16px;background-color:#fafafa;border-bottom:1px solid #e8ebf0;">'
                f'<p style="margin:0;font-size:12px;color:#6b7280;">'
                f'<span style="background-color:#e0e7ff;color:#3730a3;font-size:11px;'
                f'font-weight:600;padding:1px 6px;border-radius:3px;margin-right:6px;">{src}</span>'
                f'{score_label}</p></td></tr>'
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
        keywords_str = ", ".join(
            markdown_text(keyword, multiline=False) for keyword in result.keywords
        )
        return (
            f"ArXiv Trend Research - {status} ({keywords_str}) "
            f"({markdown_text(result.run_timestamp, multiline=False)})"
        )

    def _format_trend_body(self, result: TrendRunResult) -> str:
        """向后兼容：默认使用通用模板（非 Telegram 专用）"""
        return self._format_trend_body_for_platform(result, None)

    def _format_trend_body_for_platform(
        self, result: TrendRunResult, platform: Optional[str]
    ) -> str:
        """使用模板格式化趋势分析通知正文"""
        if platform == "telegram":
            return self._format_telegram_trend_body(result)

        template_name = "research_success" if result.success else "research_failure"
        template = _load_template(template_name, platform=platform)

        keywords_str = ", ".join(
            markdown_text(keyword, multiline=False) for keyword in result.keywords
        )
        date_range = (
            f"{markdown_text(result.date_from, multiline=False)} ~ "
            f"{markdown_text(result.date_to, multiline=False)}"
        )

        # 报告路径
        report_lines = []
        if result.report_paths:
            report_lines.append("**报告路径**")
            for fmt, path in result.report_paths.items():
                report_lines.append(
                    f"> `{markdown_text(fmt, multiline=False)}` "
                    f"{markdown_text(path, multiline=False)}"
                )
        report_list = "\n".join(report_lines)

        if template:
            return _render_template(
                template,
                status="SUCCESS" if result.success else "FAILED",
                timestamp=markdown_text(result.run_timestamp, multiline=False),
                keywords=keywords_str,
                date_range=date_range,
                total_papers=result.total_papers,
                tldr_count=result.tldr_count,
                trend_skills_count=result.trend_skills_count,
                report_list=report_list,
                error_message=markdown_text(result.error_message or "无"),
                token_usage_section=self._format_token_section_md(result.token_usage),
            )

        # 降级为纯文本
        return self._format_trend_body_fallback(result)

    def _format_telegram_trend_body(self, result: TrendRunResult) -> str:
        """Telegram 专用趋势分析 HTML 正文。"""
        status_label = "运行成功" if result.success else "运行失败"
        keywords_str = self._html_escape(", ".join(str(keyword) for keyword in result.keywords))
        date_range = self._html_escape(f"{result.date_from} ~ {result.date_to}")

        report_lines = []
        for fmt, path in sorted(result.report_paths.items()):
            report_lines.append(f"<code>{self._html_escape(fmt.upper())}</code> {self._html_escape(path)}")

        sections = [
            "<b>ArXiv Trend Research</b>",
            f"<b>{status_label}</b> | {self._html_escape(result.run_timestamp)}",
            f"<b>关键词</b> {keywords_str}",
            f"<b>时间范围</b> {date_range}",
            (
                f"<b>统计</b> 论文 <b>{result.total_papers}</b> | TLDR <b>{result.tldr_count}</b> | "
                f"Skills <b>{result.trend_skills_count}</b>"
            ),
        ]

        if result.token_usage and result.token_usage.get("has_data") and self.settings.TOKEN_TRACKING_ENABLED:
            total = result.token_usage.get("total", 0)
            tp = result.token_usage.get("total_prompt", 0)
            tc = result.token_usage.get("total_completion", 0)
            sections.extend(
                [
                    "<b>Token 消耗</b>",
                    (
                        f"<blockquote>总计 <b>{total:,}</b> tokens"
                        f"（输入 {tp:,} / 输出 {tc:,}）</blockquote>"
                    ),
                ]
            )

        if report_lines:
            sections.extend(["<b>报告路径</b>", "\n".join(report_lines)])

        if not result.success and result.error_message:
            sections.extend(["<b>错误信息</b>", f"<blockquote>{self._html_escape(result.error_message)}</blockquote>"])

        return "\n".join(sections)

    def _format_error_body_for_platform(
        self, template_name: str, platform: Optional[str], **kwargs
    ) -> str:
        """按平台格式化错误告警正文，模板缺失时使用纯文本兜底。"""
        if platform == "telegram":
            return self._format_telegram_error_body(template_name, **kwargs)

        template = _load_template(template_name, platform=platform)
        if template:
            safe_kwargs = {key: markdown_text(value) for key, value in kwargs.items()}
            return _render_template(template, **safe_kwargs)

        lines = [
            "## ArXiv Daily Researcher",
            "",
            f"**错误告警** | {markdown_text(kwargs.get('timestamp', ''), multiline=False)}",
            "",
        ]
        for key, value in kwargs.items():
            if key != "timestamp":
                lines.append(
                    f"> {markdown_text(key, multiline=False)}: {markdown_text(value)}"
                )
        return "\n".join(lines)

    def _format_telegram_error_body(self, template_name: str, **kwargs) -> str:
        """Telegram 专用错误通知 HTML 正文。"""
        title_map = {
            "error_mineru": "MinerU 错误告警",
            "error_llm": "LLM 错误告警",
            "error_network": "网络错误告警",
            "error_generic": "通用错误告警",
        }
        title = title_map.get(template_name, "错误告警")

        timestamp = self._html_escape(str(kwargs.get("timestamp", "")))
        sections = [
            "<b>ArXiv Daily Researcher</b>",
            f"<b>{title}</b> | {timestamp}",
        ]
        for key, value in kwargs.items():
            if key == "timestamp":
                continue
            sections.append(f"<b>{self._html_escape(str(key))}</b>")
            sections.append(f"<blockquote>{self._html_escape(str(value))}</blockquote>")
        return "\n".join(sections)

    @staticmethod
    def _platform_for_notifier(notifier: BaseNotifier) -> Optional[str]:
        if isinstance(notifier, WebhookNotifier):
            return notifier.platform
        return None

    def _format_trend_body_fallback(self, result: TrendRunResult) -> str:
        """趋势通知模板不存在时的兜底纯文本"""
        status_icon = "OK" if result.success else "ERROR"
        lines = [
            f"Status: {status_icon}",
            f"Time: {markdown_text(result.run_timestamp, multiline=False)}",
            f"Keywords: {', '.join(markdown_text(keyword, multiline=False) for keyword in result.keywords)}",
            f"Date Range: {markdown_text(result.date_from, multiline=False)} ~ "
            f"{markdown_text(result.date_to, multiline=False)}",
            "",
            f"Papers Found: {result.total_papers}",
            f"TLDRs Generated: {result.tldr_count}",
            f"Trend Skills: {result.trend_skills_count}",
        ]

        if result.error_message:
            lines.append("")
            lines.append(f"Error: {markdown_text(result.error_message)}")

        if result.report_paths:
            lines.append("")
            lines.append("Reports:")
            for fmt, path in result.report_paths.items():
                lines.append(
                    f"  [{markdown_text(fmt, multiline=False)}] "
                    f"{markdown_text(path, multiline=False)}"
                )

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
