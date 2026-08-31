import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notifications.notifier import (  # noqa: E402
    EmailNotifier,
    NotifierAgent,
    RunResult,
    WorkflowResult,
    WebhookNotifier,
    send_test_notification,
)
from utils.daily_research_store import DailyResearchStore  # noqa: E402


class _Response:
    def __init__(self, payload=None, text="ok"):
        self.payload = payload
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def raise_for_status(self):
        return None


class NotificationOutboxTests(unittest.TestCase):
    def _store(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return DailyResearchStore(Path(temp_dir.name) / "daily.db")

    @staticmethod
    def _notifier(channels):
        notifier = NotifierAgent.__new__(NotifierAgent)
        notifier.settings = SimpleNamespace(
            RETRY_MAX_ATTEMPTS=2,
            RETRY_MIN_WAIT=1,
            RETRY_MAX_WAIT=2,
            NOTIFY_ON_SUCCESS=True,
            NOTIFY_ON_FAILURE=True,
            NOTIFY_ATTACH_REPORTS=False,
        )
        notifier.notifiers_by_channel = {channel: object() for channel in channels}
        notifier.notifiers = list(notifier.notifiers_by_channel.values())
        return notifier

    def test_outbox_is_idempotent_and_recovers_stale_claims(self):
        store = self._store()
        payload = {"result": {"run_timestamp": "2026-08-12 12:00:00"}}
        self.assertTrue(store.enqueue_notification("run-1", "daily_run_result", "email", payload))
        self.assertFalse(store.enqueue_notification("run-1", "daily_run_result", "email", payload))

        claimed = store.claim_due_notifications(event_type="daily_run_result")
        self.assertEqual(len(claimed), 1)
        row = claimed[0]
        self.assertEqual(row["status"], "sending")
        self.assertEqual(row["attempt_count"], 1)

        # A normal second process cannot claim it. Simulate a sender that died
        # long ago before recording a result, then recover the stale claim.
        self.assertEqual(store.claim_due_notifications(event_type="daily_run_result"), [])
        with store._connect() as conn:
            conn.execute(
                "UPDATE notification_outbox SET claimed_at = ? WHERE outbox_id = ?",
                ((datetime.now() - timedelta(seconds=10)).isoformat(), row["outbox_id"]),
            )
        recovered = store.claim_due_notifications(
            event_type="daily_run_result", stale_claim_seconds=1
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["attempt_count"], 2)

        store.mark_notification_sent(recovered[0]["outbox_id"])
        sent = store.get_notification_outbox(recovered[0]["outbox_id"])
        self.assertEqual(sent["status"], "sent")
        self.assertIsNotNone(sent["sent_at"])
        self.assertEqual(store.get_pending_notification_count(), 0)

    def test_delivery_retries_only_the_failed_channel_and_keeps_it_pending(self):
        store = self._store()
        notifier = self._notifier(["email", "wechat_work"])
        result = RunResult(run_timestamp="2026-08-12 12:00:00", success=True)

        self.assertEqual(notifier.enqueue_run_result(store, "run-1", result), 2)
        calls = []

        def send(channel, _result):
            calls.append(channel)
            if channel == "wechat_work":
                raise RuntimeError("robot rejected message")

        with patch.object(notifier, "send_run_result_to_channel", side_effect=send), patch(
            "notifications.notifier.time.sleep"
        ):
            summary = notifier.deliver_pending_run_results(store)

        self.assertEqual(summary, {"claimed": 2, "sent": 1, "deferred": 1})
        self.assertEqual(calls.count("email"), 1)
        self.assertEqual(calls.count("wechat_work"), 2)

        rows = store.claim_due_notifications(event_type="daily_run_result", limit=10)
        # The failed row has a future retry time and must not be immediately sent
        # again as part of a second report or a duplicate channel delivery.
        self.assertEqual(rows, [])

        with store._connect() as conn:
            status_rows = conn.execute(
                "SELECT channel, status, attempt_count, last_error FROM notification_outbox "
                "ORDER BY channel"
            ).fetchall()
        by_channel = {row["channel"]: row for row in status_rows}
        self.assertEqual(by_channel["email"]["status"], "sent")
        self.assertEqual(by_channel["wechat_work"]["status"], "pending")
        self.assertEqual(by_channel["wechat_work"]["attempt_count"], 2)
        self.assertIn("robot rejected", by_channel["wechat_work"]["last_error"])

    def test_outbox_reuses_existing_rows_after_restart_without_duplicate_enqueue(self):
        store = self._store()
        first = self._notifier(["email"])
        second = self._notifier(["email"])
        result = RunResult(run_timestamp="2026-08-12 12:00:00", success=True)

        self.assertEqual(first.enqueue_run_result(store, "run-1", result), 1)
        self.assertEqual(second.enqueue_run_result(store, "run-1", result), 0)

        with patch.object(second, "send_run_result_to_channel") as send:
            summary = second.deliver_pending_run_results(store)
        self.assertEqual(summary, {"claimed": 1, "sent": 1, "deferred": 0})
        send.assert_called_once_with("email", result)

    def test_workflow_outbox_sends_one_consolidated_result_per_channel(self):
        store = self._store()
        notifier = self._notifier(["email", "telegram"])
        result = WorkflowResult(
            workflow="旧历史导入",
            run_timestamp="2026-08-25 12:00:00",
            success=False,
            summary={"自动补充报告": "failed；处理 3 篇，剩余 1 篇"},
            error_message="temporary LLM outage",
        )

        self.assertEqual(notifier.enqueue_workflow_result(store, "legacy-1", result), 2)
        self.assertEqual(notifier.enqueue_workflow_result(store, "legacy-1", result), 0)
        calls = []

        def send(channel, delivered_result):
            calls.append((channel, delivered_result))
            if channel == "telegram":
                raise RuntimeError("chat unavailable")

        with patch.object(notifier, "send_workflow_result_to_channel", side_effect=send), patch(
            "notifications.notifier.time.sleep"
        ):
            summary = notifier.deliver_pending_workflow_results(store)

        self.assertEqual(summary, {"claimed": 2, "sent": 1, "deferred": 1})
        self.assertEqual(calls.count(("email", result)), 1)
        self.assertEqual(calls.count(("telegram", result)), 2)
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT channel, status, attempt_count FROM notification_outbox "
                "WHERE event_type = 'workflow_result' ORDER BY channel"
            ).fetchall()
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(rows[1]["status"], "pending")
        self.assertEqual(rows[1]["attempt_count"], 2)

    def test_workflow_format_uses_the_same_summary_on_markdown_telegram_and_email(self):
        notifier = self._notifier([])
        result = WorkflowResult(
            workflow="过去日报补跑",
            run_timestamp="2026-08-25 12:00:00",
            success=False,
            summary={"日期范围": "2026-08-01 至 2026-08-03"},
            issues=["OpenAlex 扫描跳过：rate limited"],
            error_message="bad <response>",
        )

        markdown = notifier._format_workflow_body_for_platform(result, None)
        telegram = notifier._format_workflow_body_for_platform(result, "telegram")
        email = notifier._format_workflow_html_body(result)

        self.assertIn("过去日报补跑：失败", markdown)
        self.assertIn("日期范围", markdown)
        self.assertIn("OpenAlex 扫描跳过", markdown)
        self.assertIn("注意事项", telegram)
        self.assertIn("bad &lt;response&gt;", telegram)
        self.assertIn("bad &lt;response&gt;", email)
        self.assertIn("运行摘要", email)
        self.assertIn("后台任务汇总", email)
        self.assertIn("ArXiv Daily Researcher", email)

    def test_email_notification_test_sends_the_standard_card(self):
        values = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USE_TLS": "true",
            "SMTP_USER": "sender@example.test",
            "SMTP_PASSWORD": "password",
            "SMTP_FROM": "sender@example.test",
            "SMTP_TO": "reader@example.test",
        }
        with patch.object(EmailNotifier, "send", return_value=True) as send:
            send_test_notification("email", values)

        subject, body = send.call_args.args
        self.assertEqual(subject, "ArXiv Daily Researcher · 通知测试")
        self.assertIn("通知测试", body)
        self.assertIn("该渠道可正常接收通知", send.call_args.kwargs["html_body"])

    def test_telegram_notification_test_uses_the_configured_proxy(self):
        values = {"TELEGRAM_BOT_TOKEN": "123456:token", "TELEGRAM_CHAT_ID": "42"}
        proxy = {"https": "http://proxy.example.test:7890"}
        with patch("notifications.notifier.WebhookNotifier") as notifier:
            send_test_notification(
                "telegram", values, proxies=proxy
            )

        notifier.assert_called_once_with(
            "telegram",
            "https://api.telegram.org/bot123456:token/sendMessage",
            proxies=proxy,
            chat_id="42",
        )
        subject, body = notifier.return_value.send.call_args.args
        self.assertEqual(subject, "ArXiv Daily Researcher · 通知测试")
        self.assertIn("<b>通知测试</b>", body)

    def test_webhook_application_errors_are_not_accepted_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "errcode=93000"):
            WebhookNotifier("wechat_work", "https://example.invalid")._validate_platform_response(
                _Response({"errcode": 93000, "errmsg": "invalid webhook"})
            )
        with self.assertRaisesRegex(RuntimeError, "description"):
            WebhookNotifier("telegram", "https://example.invalid")._validate_platform_response(
                _Response({"ok": False, "description": "chat not found"})
            )
        with self.assertRaisesRegex(RuntimeError, "invalid_payload"):
            WebhookNotifier("slack", "https://example.invalid")._validate_platform_response(
                _Response(text="invalid_payload")
            )

        WebhookNotifier("dingtalk", "https://example.invalid")._validate_platform_response(
            _Response({"errcode": 0, "errmsg": "ok"})
        )
        WebhookNotifier("telegram", "https://example.invalid")._validate_platform_response(
            _Response({"ok": True, "result": {}})
        )
        WebhookNotifier("slack", "https://example.invalid")._validate_platform_response(
            _Response(text="ok")
        )

    def test_webhook_uses_configured_proxy_and_never_follows_redirects(self):
        notifier = WebhookNotifier(
            "generic",
            "http://127.0.0.1:8080/relay",
            proxies={"https": "http://proxy.invalid:3128"},
        )
        with patch("notifications.notifier.requests.post", return_value=_Response({})) as post:
            notifier.send("Subject", "Body")

        _args, kwargs = post.call_args
        self.assertEqual(kwargs["timeout"], 30)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["proxies"], {"https": "http://proxy.invalid:3128"})

    def test_invalid_webhook_configuration_is_skipped_without_disabling_other_channels(self):
        settings = SimpleNamespace(
            NOTIFY_EMAIL_ENABLED=False,
            NOTIFY_WECHAT_ENABLED=False,
            NOTIFY_DINGTALK_ENABLED=False,
            NOTIFY_TELEGRAM_ENABLED=False,
            NOTIFY_SLACK_ENABLED=False,
            NOTIFY_GENERIC_WEBHOOK_ENABLED=True,
            GENERIC_WEBHOOK_URL="javascript:alert(1)",
            get_proxy_dict=lambda _service: {"https": "http://proxy.invalid:3128"},
        )
        agent = NotifierAgent.__new__(NotifierAgent)
        agent.settings = settings
        agent.notifiers = []
        agent.notifiers_by_channel = {}

        agent._setup_notifiers()

        self.assertEqual(agent.configured_channels(), [])

    def test_dingtalk_signature_is_added_to_a_url_without_an_existing_query(self):
        notifier = WebhookNotifier("dingtalk", "https://robot.example.test/send", secret="secret")
        with patch("notifications.notifier.time.time", return_value=1.234):
            url, _payload, _headers = notifier._format_dingtalk("Subject", "Body")
        self.assertIn("?timestamp=1234&sign=", url)

    def test_v2_notification_formats_show_core_qualification_and_ranking(self):
        """A V2 Top-N must not present its ranking score as the pass evidence."""
        notifier = self._notifier([])
        notifier.settings.TOKEN_TRACKING_ENABLED = False
        result = RunResult(
            run_timestamp="2026-08-12 12:00:00",
            top_papers=[
                {
                    "title": "Core qualified paper",
                    "source": "arxiv",
                    "tldr": "A test summary.",
                    "score": 10.0,
                    "relevance_score": 7.0,
                    "qualification_threshold": 6.0,
                    "has_separate_relevance_score": True,
                    "url": "https://arxiv.org/abs/2608.00001",
                },
                {
                    "title": "Legacy paper",
                    "source": "arxiv",
                    "tldr": "A legacy summary.",
                    "score": 8.0,
                    "url": "https://arxiv.org/abs/2608.00002",
                },
            ],
        )

        _, _, markdown_top = notifier._format_daily_markdown_fragments(result)
        fallback = notifier._format_body_fallback(result)
        telegram = notifier._format_telegram_body(result)
        email_cards = notifier._build_top_papers_html(result)

        self.assertIn("Core relevance: 7.0/6.0 | Ranking: 10.0", markdown_top)
        self.assertIn("Core relevance: 7.0/6.0 | Ranking: 10.0", fallback)
        self.assertIn(
            "Core relevance: <b>7.0</b> / 6.0 | Ranking: <b>10.0</b>", telegram
        )
        self.assertIn("Core relevance:", email_cards)
        self.assertIn("Ranking:", email_cards)
        self.assertIn("Score: 8.0", markdown_top)
        self.assertIn("Score: 8.0", fallback)

    def test_daily_success_notification_includes_retryable_stage_issues(self):
        notifier = self._notifier([])
        notifier.settings.TOKEN_TRACKING_ENABLED = False
        result = RunResult(
            run_timestamp="2026-08-12 12:00:00",
            success=True,
            issues=["深度分析未完成：arxiv:2608.1v1；LLM 未返回可用正文"],
        )

        markdown = notifier._format_body(result)
        telegram = notifier._format_telegram_body(result)
        fallback = notifier._format_body_fallback(result)
        email = notifier._format_html_body(result)

        self.assertIn("注意事项", markdown)
        self.assertIn("深度分析未完成", markdown)
        self.assertIn("注意事项", telegram)
        self.assertIn("深度分析未完成", fallback)
        self.assertIn("注意事项", email)


if __name__ == "__main__":
    unittest.main()
