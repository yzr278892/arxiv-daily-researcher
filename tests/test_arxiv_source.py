import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import arxiv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.arxiv_source import ArxivSource, normalize_arxiv_domains  # noqa: E402
from sources.search_agent import SearchAgent, SourceScanReceiptError  # noqa: E402


class _FakeResult:
    def __init__(self, paper_id: str, published: datetime, updated: datetime):
        self.entry_id = f"https://arxiv.org/abs/{paper_id}"
        self.published = published
        self.updated = updated
        self.title = paper_id
        self.summary = f"Abstract for {paper_id}"
        self.authors = [SimpleNamespace(name="Test Author")]
        self.pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"
        self.doi = None
        self.categories = ["cs.AI"]

    def get_short_id(self):
        return self.entry_id.rsplit("/", 1)[-1]


class _FakeClient:
    def __init__(self, submitted, updated):
        self.submitted = submitted
        self.updated = updated
        self.searches = []

    def results(self, search):
        self.searches.append(search)
        if search.sort_by is arxiv.SortCriterion.SubmittedDate:
            return iter(self.submitted)
        return iter(self.updated)


class _FailingSource:
    display_name = "fake"

    def fetch_papers(self, **_kwargs):
        raise RuntimeError("network unavailable")


class _StaticSource:
    display_name = "fake"

    def __init__(self, papers):
        self.papers = papers

    def fetch_papers(self, **_kwargs):
        return list(self.papers)


class ArxivFetchTests(unittest.TestCase):
    def test_empty_or_malformed_arxiv_domains_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "未配置目标领域"):
            normalize_arxiv_domains([])
        with self.assertRaisesRegex(ValueError, "无效的 ArXiv 领域代码"):
            normalize_arxiv_domains(["cs.AI OR all:electron"])

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "未配置目标领域"):
                source.fetch_papers(days=1, domains=[], fetch_timeout_seconds=10)

    def test_search_agent_rejects_empty_enabled_sources_or_arxiv_domains(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "至少启用一个"):
                SearchAgent(
                    history_dir=Path(temp_dir),
                    enabled_sources=[],
                    enable_semantic_scholar=False,
                )
            with self.assertRaisesRegex(ValueError, "未配置目标领域"):
                SearchAgent(
                    history_dir=Path(temp_dir),
                    enabled_sources=["arxiv"],
                    arxiv_domains=[],
                    enable_semantic_scholar=False,
                )

    @patch("sources.search_agent.ArxivSource")
    def test_search_agent_uses_default_domain_only_when_omitted(self, arxiv_source_cls):
        fake_source = arxiv_source_cls.return_value
        fake_source.display_name = "ArXiv"
        fake_source.fetch_papers.return_value = []
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = SearchAgent(
                history_dir=Path(temp_dir),
                enabled_sources=["arxiv"],
                enable_semantic_scholar=False,
            )
            agent.fetch_all_papers(days=1)

        fake_source.fetch_papers.assert_called_once_with(
            days=1, domains=["quant-ph"], scan_receipt_callback=None
        )

    def test_daily_scan_is_unbounded_and_includes_recent_revision(self):
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=1)
        old = now - timedelta(days=3)

        submitted = [
            _FakeResult(f"new-{index}", recent, recent)
            for index in range(120)
        ] + [_FakeResult("old-submission", old, old)]
        updated = [
            _FakeResult("old-paper-v2", now - timedelta(days=30), recent),
            _FakeResult("old-update", old, old),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            # This regression exercises the normal scan boundary itself.  The
            # delayed-announcement grace behaviour is covered separately.
            source = ArxivSource(
                Path(temp_dir), max_results=1, announcement_lookback_grace_days=0
            )
            source.history = {f"new-{index}": "complete" for index in range(60)}
            fake_client = _FakeClient(submitted, updated)
            source.client = fake_client

            papers = source.fetch_papers(days=2, domains=["cs.AI"], fetch_timeout_seconds=10)

        paper_ids = {paper.paper_id for paper in papers}
        self.assertEqual(len(papers), 61)
        self.assertIn("old-paper-v2", paper_ids)
        self.assertNotIn("old-submission", paper_ids)
        self.assertTrue(all(search.max_results is None for search in fake_client.searches))
        self.assertEqual(
            [search.sort_by for search in fake_client.searches],
            [arxiv.SortCriterion.SubmittedDate, arxiv.SortCriterion.LastUpdatedDate],
        )
        self.assertIn("submittedDate:[", fake_client.searches[0].query)

    def test_scan_receipt_records_query_scope_paging_and_deduplication(self):
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=6)
        old = now - timedelta(days=9)
        submitted = [
            _FakeResult("same-v1", recent, recent),
            _FakeResult("submitted-v1", recent, recent),
            _FakeResult("older-v1", old, old),
        ]
        updated = [
            _FakeResult("same-v1", recent, recent),
            _FakeResult("updated-v2", now - timedelta(days=30), recent),
            _FakeResult("older-update-v1", old, old),
        ]

        receipts = []
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(
                Path(temp_dir), announcement_lookback_grace_days=2
            )
            source.client = _FakeClient(submitted, updated)
            papers = source.fetch_papers(
                days=3,
                domains=["cs.AI"],
                fetch_timeout_seconds=10,
                scan_receipt_callback=receipts.append,
            )

        self.assertEqual({"same-v1", "submitted-v1", "updated-v2"}, {p.paper_id for p in papers})
        self.assertEqual(1, len(receipts))
        receipt = receipts[0]
        self.assertEqual("succeeded", receipt["status"])
        self.assertEqual(3, receipt["requested_scan_days"])
        self.assertEqual(2, receipt["announcement_lookback_grace_days"])
        self.assertEqual(5, receipt["effective_days"])
        self.assertEqual(["cs.AI"], receipt["domains"])
        domain = receipt["domain_receipts"][0]
        self.assertEqual("succeeded", domain["status"])
        self.assertEqual(1, domain["deduplicated_within_domain"])
        self.assertEqual(3, domain["new_candidates"])
        self.assertEqual(3, domain["queries"]["submitted"]["api_entries_checked"])
        self.assertEqual(3, domain["queries"]["updated"]["api_entries_checked"])
        self.assertEqual(2, domain["queries"]["submitted"]["window_entries"])
        self.assertEqual(2, domain["queries"]["updated"]["window_entries"])
        self.assertEqual(1, domain["queries"]["submitted"]["pages_observed"])
        self.assertEqual(1, domain["queries"]["submitted"]["attempts"])

    def test_failed_domain_still_emits_failed_scan_receipt(self):
        now = datetime.now(timezone.utc)

        class _AlwaysFailingClient:
            page_size = 100

            def results(self, _search):
                raise RuntimeError("upstream unavailable")

        receipts = []
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "sources.arxiv_source.time.sleep"
        ):
            source = ArxivSource(Path(temp_dir))
            source.client = _AlwaysFailingClient()
            with self.assertRaisesRegex(Exception, "抓取未完成"):
                source.fetch_papers(
                    days=1,
                    domains=["cs.AI"],
                    fetch_timeout_seconds=10,
                    scan_receipt_callback=receipts.append,
                )

        self.assertEqual(1, len(receipts))
        domain = receipts[0]["domain_receipts"][0]
        self.assertEqual("failed", receipts[0]["status"])
        self.assertEqual("failed", domain["status"])
        self.assertIn("upstream unavailable", domain["error"])
        self.assertEqual(4, domain["queries"]["submitted"]["attempts"])

    def test_scan_receipt_persistence_callback_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            source.client = _FakeClient([], [])
            with self.assertRaisesRegex(Exception, "无法持久化 ArXiv 扫描收据"):
                source.fetch_papers(
                    days=1,
                    domains=["cs.AI"],
                    fetch_timeout_seconds=10,
                    scan_receipt_callback=lambda _receipt: (_ for _ in ()).throw(
                        OSError("database read-only")
                    ),
                )

    def test_search_agent_propagates_source_failures(self):
        agent = SearchAgent.__new__(SearchAgent)
        agent.sources = {"fake": _FailingSource()}
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            agent.fetch_all_papers(days=1)

    def test_non_arxiv_sources_emit_terminal_summary_receipts(self):
        hf_agent = SearchAgent.__new__(SearchAgent)
        hf_agent.sources = {"huggingface_papers": _StaticSource([object(), object()])}
        hf_receipts = []
        hf_result = hf_agent.fetch_all_papers(
            days=1, scan_receipt_callbacks={"huggingface_papers": hf_receipts.append}
        )

        self.assertEqual(2, len(hf_result["huggingface_papers"]))
        self.assertEqual(
            {
                "source": "huggingface_papers",
                "status": "succeeded",
                "receipt_kind": "source_summary_v1",
                "total_new_candidates": 2,
            },
            {
                key: hf_receipts[0][key]
                for key in (
                    "source",
                    "status",
                    "receipt_kind",
                    "total_new_candidates",
                )
            },
        )
        self.assertEqual([], hf_receipts[0]["domain_receipts"])

        openalex_agent = SearchAgent.__new__(SearchAgent)
        openalex_agent.sources = {
            "openalex": _StaticSource([SimpleNamespace(source="prl"), SimpleNamespace(source="prl")])
        }
        openalex_agent._journal_codes = ["prl", "pra"]
        openalex_agent.enable_semantic_scholar = False
        openalex_agent.semantic_scholar_enricher = None
        openalex_receipts = {"prl": [], "pra": []}
        openalex_agent.fetch_all_papers(
            days=1,
            scan_receipt_callbacks={
                source: receipts.append for source, receipts in openalex_receipts.items()
            },
        )

        self.assertEqual(2, openalex_receipts["prl"][0]["total_new_candidates"])
        self.assertEqual(0, openalex_receipts["pra"][0]["total_new_candidates"])
        self.assertTrue(
            all(receipt["status"] == "succeeded" for receipts in openalex_receipts.values() for receipt in receipts)
        )

    def test_non_arxiv_failure_emits_failed_receipt_and_callback_failure_is_fatal(self):
        agent = SearchAgent.__new__(SearchAgent)
        agent.sources = {"huggingface_papers": _FailingSource()}
        receipts = []
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            agent.fetch_all_papers(
                days=1, scan_receipt_callbacks={"huggingface_papers": receipts.append}
            )
        self.assertEqual("failed", receipts[0]["status"])
        self.assertNotIn("total_new_candidates", receipts[0])

        persistence_agent = SearchAgent.__new__(SearchAgent)
        persistence_agent.sources = {"huggingface_papers": _StaticSource([])}
        with self.assertRaisesRegex(SourceScanReceiptError, "无法持久化"):
            persistence_agent.fetch_all_papers(
                days=1,
                scan_receipt_callbacks={
                    "huggingface_papers": lambda _receipt: (_ for _ in ()).throw(
                        OSError("database read-only")
                    )
                },
            )

    def test_announcement_grace_catches_late_indexed_submission_without_result_cap(self):
        now = datetime.now(timezone.utc)
        delayed = _FakeResult("late-paper-v1", now - timedelta(days=4), now - timedelta(days=4))

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(
                Path(temp_dir), max_results=1, announcement_lookback_grace_days=2
            )
            client = _FakeClient([delayed], [])
            source.client = client

            papers = source.fetch_papers(days=3, domains=["cs.AI"], fetch_timeout_seconds=10)

        self.assertEqual(["late-paper-v1"], [paper.paper_id for paper in papers])
        self.assertIsNone(client.searches[0].max_results)
        self.assertIn("submittedDate:[", client.searches[0].query)

    def test_no_announcement_grace_does_not_widen_normal_submission_window(self):
        now = datetime.now(timezone.utc)
        delayed = _FakeResult("late-paper-v1", now - timedelta(days=4), now - timedelta(days=4))

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(
                Path(temp_dir), announcement_lookback_grace_days=0
            )
            source.client = _FakeClient([delayed], [])

            papers = source.fetch_papers(days=3, domains=["cs.AI"], fetch_timeout_seconds=10)

        self.assertEqual([], papers)

    @patch("sources.search_agent.ArxivSource")
    def test_search_agent_passes_announcement_grace_to_arxiv_source(self, arxiv_source_cls):
        fake_settings = SimpleNamespace(
            ARXIV_ANNOUNCEMENT_LOOKBACK_GRACE_DAYS=4,
            get_proxy_dict=lambda _source: None,
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.settings", fake_settings):
            SearchAgent(
                history_dir=Path(temp_dir),
                enabled_sources=["arxiv"],
                enable_semantic_scholar=False,
            )

        arxiv_source_cls.assert_called_once_with(
            history_dir=Path(temp_dir),
            proxy_dict=None,
            announcement_lookback_grace_days=4,
        )

    @patch("sources.search_agent.ArxivSource")
    def test_search_agent_can_bypass_legacy_history_when_sqlite_is_authoritative(
        self, arxiv_source_cls
    ):
        fake_source = arxiv_source_cls.return_value
        fake_source.display_name = "ArXiv"
        with tempfile.TemporaryDirectory() as temp_dir:
            SearchAgent(
                history_dir=Path(temp_dir),
                enabled_sources=["arxiv"],
                enable_semantic_scholar=False,
                use_legacy_history_filter=False,
            )

        fake_source.set_history_filtering_enabled.assert_called_once_with(False)


    def test_updated_query_stops_at_the_page_budget(self):
        """An updated query has no lower bound and must not page forever."""
        from sources import arxiv_source

        class _Client:
            page_size = 2

            def results(self, _search):
                # Every result is inside the window, so without a budget this
                # iterator would keep paging through the whole domain.
                for index in range(20):
                    moment = datetime(2026, 9, 1, tzinfo=timezone.utc)
                    yield _FakeResult(f"2501.{index:05d}v1", moment, moment)

        source = ArxivSource.__new__(ArxivSource)
        source.client = _Client()
        with self.assertLogs("sources.arxiv_source", level="WARNING") as logs:
            results, receipt = source._fetch_query_results(
                object(),
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                "updated",
                0,
                max_pages=2,
                context="cs.AI",
            )

        self.assertEqual(len(results), 4)
        self.assertEqual(receipt["pages_observed"], 2)
        self.assertTrue(any("页数上限" in line for line in logs.output))


class ArxivTimeoutGuardTests(unittest.TestCase):
    """超时守卫是"无进展"看门狗：持续到达的结果应持续续期。"""

    def test_ongoing_progress_outlives_the_guard_window(self):
        import time as _time

        from sources.arxiv_source import _timeout_guard

        def slow_generator():
            # 总耗时 1.6s，超过 1s 守卫窗口；每 0.2s 到达一个结果。
            for index in range(8):
                _time.sleep(0.2)
                yield index

        with _timeout_guard(1) as guard:
            received = []
            for item in slow_generator():
                guard.touch()
                received.append(item)
        self.assertEqual(received, list(range(8)))

    def test_stalled_stream_raises_timeout(self):
        import time as _time

        from sources.arxiv_source import _ArxivTimeoutError, _timeout_guard

        def stalled_generator():
            _time.sleep(1.5)
            yield "never"

        with self.assertRaises(_ArxivTimeoutError):
            with _timeout_guard(1) as guard:
                for item in stalled_generator():
                    guard.touch()


def _http_error(status: int, message: str, retry_after=None):
    """构造带响应头的 urllib HTTPError，模拟 arXiv 网络错误。"""
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPError(
        "https://export.arxiv.org/api/query",
        status,
        message,
        headers,
        BytesIO(b""),
    )


    def test_unavailable_watchdog_explains_why_it_is_disabled(self):
        """A silently disabled watchdog would hide the lost protection."""
        from sources import arxiv_source

        arxiv_source._TIMEOUT_GUARD_WARNED = False
        with patch.object(
            arxiv_source.signal,
            "signal",
            side_effect=ValueError("signal only works in main thread"),
        ), self.assertLogs("sources.arxiv_source", level="WARNING") as logs:
            with arxiv_source._timeout_guard(5) as guard:
                self.assertFalse(guard._enabled)

        self.assertTrue(any("看门狗不可用" in line for line in logs.output))


class ArxivRetryBackoffTests(unittest.TestCase):
    """领域/关键词两条抓取路径共享的退避分类。"""

    def test_timeout_backs_off_linearly(self):
        from sources.arxiv_source import _ArxivTimeoutError, _arxiv_retry_wait

        exc = _ArxivTimeoutError("ArXiv 请求超时（>180s 无进展）")
        self.assertEqual(_arxiv_retry_wait(exc, 1), 30)
        self.assertEqual(_arxiv_retry_wait(exc, 2), 60)
        self.assertEqual(_arxiv_retry_wait(exc, 3), 90)
        self.assertEqual(_arxiv_retry_wait(exc, 9), 90)

    def test_rate_limit_backs_off_exponentially(self):
        from sources.arxiv_source import _arxiv_retry_wait, _is_rate_limit_error

        exc = _http_error(429, "Too Many Requests")
        self.assertTrue(_is_rate_limit_error(exc))
        self.assertEqual(_arxiv_retry_wait(exc, 1), 60)
        self.assertEqual(_arxiv_retry_wait(exc, 2), 120)
        self.assertEqual(_arxiv_retry_wait(exc, 3), 240)
        self.assertEqual(_arxiv_retry_wait(exc, 9), 480)

    def test_rate_limit_detection_accepts_plain_messages(self):
        from sources.arxiv_source import _is_rate_limit_error

        self.assertTrue(_is_rate_limit_error(RuntimeError("HTTP Error 429: Too Many Requests")))
        self.assertFalse(_is_rate_limit_error(RuntimeError("HTTP Error 503: Service Unavailable")))

    def test_generic_server_errors_back_off_linearly(self):
        from sources.arxiv_source import _arxiv_retry_wait

        exc = _http_error(503, "Service Unavailable")
        self.assertEqual(_arxiv_retry_wait(exc, 1), 30)
        self.assertEqual(_arxiv_retry_wait(exc, 3), 90)

    def test_retry_after_header_extends_the_wait(self):
        from sources.arxiv_source import _arxiv_retry_wait, _retry_after_seconds

        exc = _http_error(503, "Service Unavailable", retry_after=120)
        self.assertEqual(_retry_after_seconds(exc), 120)
        # 响应头要求 120s，比线性退避的 30s 更长 → 遵从响应头
        self.assertEqual(_arxiv_retry_wait(exc, 1), 120)

    def test_retry_after_is_capped(self):
        from sources.arxiv_source import _arxiv_retry_wait

        exc = _http_error(429, "Too Many Requests", retry_after=3600)
        self.assertEqual(_arxiv_retry_wait(exc, 1), 600)

    def test_missing_retry_after_returns_none(self):
        from sources.arxiv_source import _retry_after_seconds

        self.assertIsNone(_retry_after_seconds(_http_error(500, "boom")))
        self.assertIsNone(_retry_after_seconds(RuntimeError("no headers")))


if __name__ == "__main__":
    unittest.main()
