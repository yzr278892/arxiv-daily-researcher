"""HTTP-level regression tests for the standalone modern WebUI.

These tests intentionally keep all account state in memory.  They verify the
presentation layer's authentication boundary without reading or changing a
developer's real ``.env`` file.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from modern_webui import app as modern_app
from modern_webui import auth as modern_auth
from utils.daily_research_store import DailyResearchStore


class ModernWebUIAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env: dict[str, str] = {"WEBUI_AUTH_ENABLED": "true"}
        self.client = TestClient(modern_app.app)
        self.read_env = patch.object(
            modern_app, "read_env", side_effect=lambda: dict(self.env)
        )
        self.write_env = patch.object(
            modern_app,
            "write_env",
            side_effect=lambda values: self.env.update(
                {str(key): str(value) for key, value in values.items()}
            ),
        )
        self.read_env.start()
        self.write_env.start()
        self.addCleanup(self.read_env.stop)
        self.addCleanup(self.write_env.stop)
        self.addCleanup(self.client.close)

    def test_health_is_public_and_protected_settings_require_a_session(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.text, "ok")

        settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 503)
        self.assertIn("尚未初始化", settings.json()["detail"])

    def test_shared_translation_catalogue_is_available_before_sign_in(self) -> None:
        response = self.client.get("/api/i18n")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"]["nav_reports"], {
            "zh": "📄 报告",
            "en": "📄 Reports",
        })

    def test_large_frontend_assets_use_http_compression(self) -> None:
        """Remote/LAN clients should not fetch the full plain JS payload."""
        response = self.client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        # httpx transparently decodes the body for TestClient callers.
        self.assertIn("const NAVIGATION", response.text)

    def test_reading_the_account_registry_does_not_derive_a_test_password(self) -> None:
        """Authenticated requests must not run PBKDF2 just to parse .env."""
        owner = modern_auth.Account(
            "owner_user", modern_auth.hash_password("secret6"), is_owner=True
        )
        values = {
            "WEBUI_AUTH_ENABLED": "true",
            "WEBUI_ACCOUNTS": modern_auth.serialize_accounts((owner,)),
        }
        with patch.object(
            modern_auth,
            "verify_password_hash",
            side_effect=AssertionError("registry parsing must be structural only"),
        ):
            config = modern_auth.read_auth_config(values)

        self.assertTrue(config.enabled)
        self.assertEqual(config.accounts, (owner,))

    def test_report_preview_drops_the_loading_placeholder_style(self) -> None:
        """Loaded previews must not retain the loading state's dashed frame."""
        response = self.client.get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(
            response.text.count('preview.className = "report-preview-host";'), 2
        )

    def test_report_navigation_follows_each_report_batch_not_calendar_dates(self) -> None:
        script = self.client.get("/assets/app.js").text
        start = script.index("function findAdjacentDailyReport")
        end = script.index("function reportInfoHtml", start)
        navigation = script[start:end]

        self.assertIn("item.id === report.id", navigation)
        self.assertIn('relation === "previous" ? 1 : -1', navigation)
        self.assertNotIn("new Set(sameSource.map((item) => item.date))", navigation)
        self.assertIn("← 上一份报告", script)
        self.assertIn("下一份报告", script)

    def test_report_browser_groups_keywords_and_supplements_under_other_reports(self) -> None:
        script = self.client.get("/assets/app.js").text
        start = script.index("function reportDirectoryMarkup")
        end = script.index("function updateReportPickerSelection", start)
        directory = script[start:end]
        preview_start = script.index("async function loadReportPreview")
        preview_end = script.index("async function renderFavorites", preview_start)
        preview = script[preview_start:preview_end]

        self.assertIn('reports.other', directory)
        self.assertIn('localeText("其他报告", "Other Reports")', directory)
        self.assertNotIn("reports.keyword_trend", directory)
        self.assertIn('supplement: localeText("补充报告", "Supplement Report")', script)
        self.assertIn('["daily", "supplement"].includes(report.type)', preview)
        self.assertIn('item.type === "supplement"', preview)

    def test_favorite_papers_use_the_shared_paged_table(self) -> None:
        response = self.client.get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn('pagedTable("favorite-papers"', response.text)
        self.assertNotIn('class="favorite-card"', response.text)

    def test_favorites_page_uses_sidebar_save_and_has_batch_collect_action(self) -> None:
        script = self.client.get("/assets/app.js").text

        self.assertIn("function favoritesSettingsCard", script)
        self.assertIn("auto_favorite_qualified_papers", script)
        self.assertIn('id="favorite-collect-qualified"', script)
        self.assertIn('"/api/favorites/collect"', script)
        self.assertNotIn('id="favorite-settings-save"', script)
        self.assertNotIn("function saveFavoriteSettings", script)

    def test_favorite_collection_endpoint_requires_a_session_and_forwards_result(self) -> None:
        self.assertEqual(self.client.post("/api/favorites/collect", json={}).status_code, 503)
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "favorite_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        expected = {"ok": True, "scanned": 4, "qualified": 2, "added": 1, "preserved": 1}
        with patch.object(modern_app.backend, "collect_qualified_favorites", return_value=expected) as collect:
            response = self.client.post("/api/favorites/collect", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        collect.assert_called_once_with()

    def test_notification_test_endpoint_uses_the_selected_channel(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "notification_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        with patch.object(
            modern_app.backend,
            "test_notification",
            return_value={"ok": True, "message": "测试通知已发送。"},
        ) as send:
            response = self.client.post(
                "/api/notifications/telegram/test", json={"TELEGRAM_CHAT_ID": "draft-chat"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        send.assert_called_once_with("telegram", {"TELEGRAM_CHAT_ID": "draft-chat"})

    def test_usage_summary_has_horizontal_headers_and_one_value_row(self) -> None:
        response = self.client.get("/assets/app.js")
        start = response.text.index("function usageSummaryTable")
        end = response.text.index("function refreshAnalyticsContent", start)
        summary = response.text[start:end]

        self.assertIn('<thead><tr>${metrics.map(([label])', summary)
        self.assertIn('<tbody><tr>${metrics.map(([, value])', summary)

    def test_usage_range_refresh_keeps_the_rendered_statistics_in_place(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text
        start = script.index("async function refreshAnalyticsContent")
        end = script.index("async function renderAnalytics", start)
        refresh = script[start:end]

        self.assertIn(
            'const hasRenderedContent = Boolean($(".analytics-statistics-card", host));',
            refresh,
        )
        self.assertIn('host.classList.add("is-refreshing");', refresh)
        self.assertIn('host.setAttribute("aria-busy", "true");', refresh)
        self.assertIn("button.disabled = false", refresh)
        self.assertIn("if (hasRenderedContent) {", refresh)
        self.assertIn('host.classList.add("refresh-region");', refresh)
        self.assertIn(".refresh-region.is-refreshing > *", stylesheet)
        self.assertIn(".refresh-region.is-refreshing::after", stylesheet)

    def test_historical_token_import_endpoint_requires_a_session_and_forwards_result(self) -> None:
        self.assertEqual(
            self.client.post("/api/analytics/import-history", json={}).status_code, 503
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "usage_import_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        expected = {
            "ok": True,
            "reports": 4,
            "imported": 2,
            "updated": 0,
            "unchanged": 1,
            "already_recorded": 1,
            "conflicted": 0,
            "unreadable": 0,
        }
        with patch.object(
            modern_app.backend, "import_historical_report_token_usage", return_value=expected
        ) as import_usage:
            response = self.client.post("/api/analytics/import-history", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        import_usage.assert_called_once_with()

    def test_analytics_history_import_uses_a_local_refresh(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text
        start = script.index("function analyticsMarkup")
        end = script.index("async function renderAnalytics", start)
        analytics = script[start:end]

        self.assertIn('id="analytics-import-history"', analytics)
        self.assertIn('"/api/analytics/import-history"', analytics)
        self.assertIn("await refreshAnalyticsContent(root, token);", analytics)
        self.assertIn("#analytics-import-history", analytics)
        self.assertIn("Import Historical Report Token Usage", analytics)
        self.assertIn(".analytics-history-import", stylesheet)

    def test_analytics_renders_cache_input_as_a_separate_metric(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text
        start = script.index("function analyticsSeriesRows")
        end = script.index("function analyticsMarkup", start)
        analytics = script[start:end]

        self.assertIn("cached_prompt", analytics)
        self.assertIn("缓存输入 Token", analytics)
        self.assertIn("Non-cached input tokens", analytics)
        self.assertIn('linePoints("cached_prompt")', analytics)
        self.assertIn(".trend-line.cached_prompt", stylesheet)
        self.assertIn(".trend-point.cached_prompt", stylesheet)

    def test_analytics_trend_legend_reserves_width_for_cjk_labels(self) -> None:
        script = self.client.get("/assets/app.js").text
        start = script.index("function tokenTrendChart")
        end = script.index("function formatCompactNumber", start)
        chart = script[start:end]

        self.assertIn("const legendTextWidth", chart)
        self.assertIn("/[^\\x00-\\xFF]/.test(character) ? 11 : 6.2", chart)
        self.assertIn("legendX += 29 + legendTextWidth(label);", chart)
        self.assertNotIn("label.length * 6.2", chart)

    def test_proxy_exclusions_use_chip_editor_and_save_a_compatible_value(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text
        start = script.index("function normalizeProxyNoProxyEntries")
        end = script.index("function renderProxySettings", start)
        editor = script[start:end]
        save_start = script.index("function normalizeForSave")
        save_end = script.index("async function saveAll", save_start)
        normalize_for_save = script[save_start:save_end]

        self.assertIn("function proxyNoProxyEditor", editor)
        self.assertIn("weighted-entry-box", editor)
        self.assertIn("proxy_no_proxy_entries", editor)
        self.assertIn("data-proxy-no-proxy-add", editor)
        self.assertNotIn("不使用代理的地址（每行一项）", script)
        self.assertIn('config.proxy_no_proxy = normalizeProxyNoProxyEntries(', normalize_for_save)
        self.assertIn(".proxy-scope-list .toggle-field { padding: 0; border: 0; }", stylesheet)
        self.assertIn(".proxy-scope-list { display: grid; row-gap: 28px;", stylesheet)

    def test_dynamic_forms_reapply_localization_and_cover_input_hints(self) -> None:
        script = self.client.get("/assets/app.js").text
        expected_translations = {
            "自定义 OpenAlex 期刊来源": "Custom OpenAlex journal sources",
            "例如 Alice Smith": "e.g. Alice Smith",
            "描述你的研究问题、方法与关注方向": "Describe your research question, methods, and focus areas",
            "标题、摘要、TL;DR 或关键词": "Title, abstract, TL;DR, or keyword",
            "已配置；留空则保持不变": "Configured; leave blank to keep unchanged",
            "正在读取偏好词库…": "Loading preference data…",
        }
        for chinese, english in expected_translations.items():
            self.assertIn(f'"{chinese}": "{english}"', script)

        weighted_start = script.index("function replaceWeightedEntryEditor")
        weighted_end = script.index("function bindWeightedEntryEditor", weighted_start)
        self.assertIn("applyLocale", script[weighted_start:weighted_end])

        search_start = script.index("async function loadSearchResults")
        search_end = script.index("function weightedEntries", search_start)
        self.assertGreaterEqual(script[search_start:search_end].count("applyLocale(target)"), 3)

        scoring_start = script.index("async function renderScoring")
        scoring_end = script.index("function strategyDescription", scoring_start)
        self.assertIn("applyLocale(host);", script[scoring_start:scoring_end])

        preview_start = script.index("async function loadReportPreview")
        preview_end = script.index("async function renderFavorites", preview_start)
        self.assertIn("applyLocale(preview);", script[preview_start:preview_end])

    def test_dynamic_worker_states_and_api_errors_have_english_fallbacks(self) -> None:
        """Runtime transitions must not revert an English UI to Chinese."""
        script = self.client.get("/assets/app.js").text

        self.assertIn("const DYNAMIC_EN_TRANSLATIONS", script)
        expected_translations = {
            "等待工作进程的请求已过期": "The request waiting for the worker has expired",
            "正在运行，等待进度写入": "Running; waiting for a progress update",
            "工作进程正在接手任务": "The worker is claiming the task",
            "等待后端空闲": "Waiting for the worker to become idle",
            "请查看问题摘要后重试": "Review the issue summary, then retry",
            "Docker 部署请保留请求并检查或重启研究容器。": "On Docker, keep the request and check or restart the research container.",
            "测试通知未发送：": "Test notification was not sent: ",
        }
        for chinese, english in expected_translations.items():
            self.assertIn(f'"{chinese}": "{english}"', script)

        localized_start = script.index("function localizedString")
        localized_end = script.index("function localizedError", localized_start)
        localized = script[localized_start:localized_end]
        self.assertIn("DYNAMIC_EN_TRANSLATIONS[text]", localized)

    def test_extracted_keywords_use_a_paged_table_with_group_spacing(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text

        self.assertIn('pagedTable("reference-extracted-keywords"', script)
        self.assertNotIn("native-scroll-list", script)
        self.assertIn(".reference-extraction-fields { display: grid; gap: 24px;", stylesheet)

    def test_task_status_regions_share_deliberate_spacing(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text

        self.assertEqual(script.count('class="task-status-region"'), 3)
        self.assertIn(".task-status-region { margin-top: 28px; }", stylesheet)

    def test_history_maintenance_has_an_independent_paper_limit_setting(self) -> None:
        script = self.client.get("/assets/app.js").text

        self.assertIn("history_maintenance_max_papers_per_run", script)
        self.assertIn("历史维护每次最多处理论文数（0 不限）", script)

    def test_history_maintenance_exposes_shared_schedule_and_plain_buttons(self) -> None:
        script = self.client.get("/assets/app.js").text
        start = script.index("function historyActions")
        end = script.index("function historyStatusPanel", start)
        history_actions = script[start:end]

        self.assertIn("history_maintenance_run_mode", history_actions)
        self.assertIn("history_maintenance_time_window_start", history_actions)
        self.assertIn("history_maintenance_time_window_end", history_actions)
        self.assertIn('id="history-time-window" class="form-grid two history-time-window"', history_actions)
        self.assertIn(
            '<button id="history-import" class="primary-button"', history_actions
        )
        self.assertIn(
            '<button id="history-repair" class="secondary-button compact-button"',
            history_actions,
        )
        self.assertIn(
            '<button id="history-omission" class="secondary-button compact-button"',
            history_actions,
        )
        self.assertIn('id="history-migrate-supplements"', history_actions)
        self.assertNotIn("<span>→</span>", script)
        self.assertNotIn("后一天 →", script)
        document = self.client.get("/").text
        self.assertNotIn("<span>→</span>", document)
        self.assertNotIn("运行方式修改后，请使用顶部", script)

    def test_supplement_report_migration_requires_a_session_and_forwards_result(self) -> None:
        self.assertEqual(
            self.client.post("/api/history/supplement-reports/migrate", json={}).status_code,
            503,
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "migration_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        expected = {
            "ok": True,
            "html_moved": 2,
            "markdown_moved": 2,
            "database_runs": 1,
            "database_deliveries": 4,
        }
        with patch.object(
            modern_app.backend, "migrate_supplement_reports", return_value=expected
        ) as migrate:
            response = self.client.post("/api/history/supplement-reports/migrate", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        migrate.assert_called_once_with()

    def test_migration_button_reorganizes_v42_supplement_reports_end_to_end(self) -> None:
        """Exercise the authenticated button endpoint against a v4.2 archive copy.

        v4.2 wrote supplements beside daily reports with the usual
        ``<SOURCE>_Report_<timestamp>`` filenames.  This fixture uses its
        actual HTML and Markdown title markers, the source-directory layout,
        a normal daily report that must remain untouched, and SQLite paths
        from the worker container.  It keeps the entire archive in a temporary
        directory so the test cannot modify an operator's data.
        """
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "v42_migration_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            reports_dir = data_dir / "reports"
            stamp = "2026-08-30_23-10-08_123456"
            old_html = (
                reports_dir
                / "daily_research"
                / "html"
                / "arxiv"
                / f"ARXIV_Report_{stamp}.html"
            )
            old_markdown = (
                reports_dir
                / "daily_research"
                / "markdown"
                / "arxiv"
                / f"ARXIV_Report_{stamp}.md"
            )
            normal_daily = (
                reports_dir
                / "daily_research"
                / "html"
                / "arxiv"
                / "ARXIV_Report_2026-08-30_08-00-00_000000.html"
            )
            flat_stamp = "2026-08-31_00-01-02_654321"
            flat_html = (
                reports_dir
                / "daily_research"
                / "html"
                / f"PRL_Report_{flat_stamp}.html"
            )
            flat_markdown = (
                reports_dir
                / "daily_research"
                / "markdown"
                / f"PRL_Report_{flat_stamp}.md"
            )
            old_html.parent.mkdir(parents=True)
            old_markdown.parent.mkdir(parents=True)
            flat_html.parent.mkdir(parents=True, exist_ok=True)
            flat_markdown.parent.mkdir(parents=True, exist_ok=True)
            old_html.write_text(
                """<!DOCTYPE html><html lang=\"zh-CN\"><head>
                <title>arXiv Report 2026-08-30 Supplement Report</title>
                </head><body><h1>arXiv 补充报告 (Supplement Report)</h1>
                <div class=\"card pass\"><div class=\"card-title\"><a href=\"https://arxiv.org/abs/2608.12345v2\">1. v4.2 supplement paper</a></div>
                <span class=\"badge pass\">Pass</span>
                <div class=\"field\"><span class=\"field-label\">Authors:</span> Alice</div>
                <div class=\"field\"><span class=\"field-label\">Version:</span> v2</div></div>
                </body></html>""",
                encoding="utf-8",
            )
            old_markdown.write_text(
                "# 📊 arXiv 研究报告 (2026-08-30) · 补充报告\n\n"
                f"> 生成时间: {stamp}\n",
                encoding="utf-8",
            )
            normal_daily.write_text(
                "<title>arXiv Report 2026-08-30</title>"
                "<h1>arXiv Research Report</h1>",
                encoding="utf-8",
            )
            flat_html.write_text(
                "<title>PRL Report 2026-08-31 Supplement Report</title>"
                "<h1>PRL 补充报告 (Supplement Report)</h1>",
                encoding="utf-8",
            )
            flat_markdown.write_text(
                "# 📊 PRL 研究报告 (2026-08-31) · 补充报告\n",
                encoding="utf-8",
            )

            store = DailyResearchStore(
                data_dir / "daily_research" / "daily_research.db"
            )
            run_id = store.start_run(1, run_kind="supplement")
            old_html_reference = f"/app/data/reports/daily_research/html/arxiv/ARXIV_Report_{stamp}.html"
            old_markdown_reference = f"/app/data/reports/daily_research/markdown/arxiv/ARXIV_Report_{stamp}.md"
            store.complete_run(
                run_id,
                {
                    "arxiv_html": old_html_reference,
                    "arxiv": old_markdown_reference,
                },
            )
            with store._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO paper_deliveries(
                        run_id, source, paper_id, canonical_id, version,
                        report_path, delivered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        "arxiv",
                        "2608.12345v2",
                        "2608.12345",
                        2,
                        old_html_reference,
                        "2026-08-30T23:10:08",
                    ),
                )

            with patch.object(
                modern_app.backend, "flat_config", return_value={}
            ), patch.object(
                modern_app.backend, "configured_reports_dir", return_value=reports_dir
            ), patch.object(
                modern_app.backend, "configured_data_dir", return_value=data_dir
            ), patch.object(modern_app.backend, "open_store", return_value=store):
                response = self.client.post(
                    "/api/history/supplement-reports/migrate", json={}
                )
                repeated = self.client.post(
                    "/api/history/supplement-reports/migrate", json={}
                )
                report_groups = modern_app.backend.list_reports(show_non_arxiv=True)
                supplement = next(
                    row
                    for row in report_groups["other"]
                    if row["type"] == "supplement" and row["source"] == "arxiv"
                )
                report_papers = modern_app.backend.report_papers(supplement["id"])

            new_html = (
                reports_dir
                / "other_reports"
                / "supplement"
                / "html"
                / "arxiv"
                / f"Supplement_Report_{stamp}.html"
            )
            new_markdown = (
                reports_dir
                / "other_reports"
                / "supplement"
                / "markdown"
                / "arxiv"
                / f"Supplement_Report_{stamp}.md"
            )
            self.assertEqual(
                response.json(),
                {
                    "ok": True,
                    "html_moved": 2,
                    "markdown_moved": 2,
                    "database_runs": 1,
                    "database_deliveries": 1,
                },
            )
            self.assertEqual(
                repeated.json(),
                {
                    "ok": True,
                    "html_moved": 0,
                    "markdown_moved": 0,
                    "database_runs": 0,
                    "database_deliveries": 0,
                },
            )
            self.assertFalse(old_html.exists())
            self.assertFalse(old_markdown.exists())
            self.assertTrue(new_html.is_file())
            self.assertTrue(new_markdown.is_file())
            self.assertFalse(flat_html.exists())
            self.assertFalse(flat_markdown.exists())
            self.assertTrue(
                (
                    reports_dir
                    / "other_reports"
                    / "supplement"
                    / "html"
                    / "prl"
                    / f"Supplement_Report_{flat_stamp}.html"
                ).is_file()
            )
            self.assertTrue(
                (
                    reports_dir
                    / "other_reports"
                    / "supplement"
                    / "markdown"
                    / "prl"
                    / f"Supplement_Report_{flat_stamp}.md"
                ).is_file()
            )
            self.assertTrue(normal_daily.is_file())
            self.assertEqual([row["name"] for row in report_groups["daily"]], [normal_daily.name])
            self.assertEqual(
                {
                    (row["type"], row["source"])
                    for row in report_groups["other"]
                },
                {("supplement", "arxiv"), ("supplement", "prl")},
            )
            self.assertEqual(supplement["name"], new_html.name)
            self.assertEqual(report_papers[0]["paper_id"], "2608.12345v2")

            with store._connect() as conn:
                saved_paths = json.loads(
                    conn.execute(
                        "SELECT report_paths_json FROM daily_runs WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()["report_paths_json"]
                )
                delivery_path = conn.execute(
                    "SELECT report_path FROM paper_deliveries WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["report_path"]
            self.assertEqual(
                saved_paths["arxiv_html"],
                f"/app/data/reports/other_reports/supplement/html/arxiv/Supplement_Report_{stamp}.html",
            )
            self.assertEqual(
                saved_paths["arxiv"],
                f"/app/data/reports/other_reports/supplement/markdown/arxiv/Supplement_Report_{stamp}.md",
            )
            self.assertEqual(delivery_path, saved_paths["arxiv_html"])

    def test_native_selects_share_the_source_chevron_treatment(self) -> None:
        stylesheet = self.client.get("/assets/app.css").text

        self.assertIn("select:not([multiple]) {", stylesheet)
        self.assertIn("-webkit-appearance: none;", stylesheet)
        self.assertIn("background-image: var(--select-chevron);", stylesheet)
        self.assertIn(".pager select { box-sizing: border-box; min-width: 86px; min-height: 27px;", stylesheet)
        self.assertIn(".pager select:hover { background-image: var(--select-chevron-active); }", stylesheet)
        self.assertIn("text-align-last: center;", stylesheet)

    def test_language_switch_rerenders_dynamic_locale_text(self) -> None:
        script = self.client.get("/assets/app.js").text
        start = script.index("async function toggleLanguage")
        end = script.index("function escapeHtml", start)
        language_toggle = script[start:end]

        self.assertIn("await renderPage({ preserveScroll: true });", language_toggle)
        self.assertIn("function pageSizeLabel", script)

    def test_configuration_forms_keep_explicit_vertical_spacing(self) -> None:
        script = self.client.get("/assets/app.js").text
        stylesheet = self.client.get("/assets/app.css").text

        self.assertIn('class="proxy-settings-stack"', script)
        self.assertIn(".history-schedule-settings { display: grid; row-gap: 32px; }", stylesheet)
        self.assertIn("#proxy-dependent, #webdav-dependent { margin-top: 28px; }", stylesheet)
        self.assertIn("#backup-list { margin-top: 28px; }", stylesheet)

    def test_stale_trigger_cleanup_uses_the_authenticated_api_boundary(self) -> None:
        self.assertEqual(self.client.post("/api/triggers/stale", json={}).status_code, 503)
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "trigger_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        with patch.object(modern_app.backend, "clear_stale_triggers", return_value={"removed": 2}):
            response = self.client.post("/api/triggers/stale", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": 2})

    def test_stop_endpoint_forwards_the_status_card_task_scope(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "stop_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        with patch.object(modern_app.backend, "stop_active_tasks", return_value=[42]) as stop:
            response = self.client.post("/api/tasks/stop", json={"kind": "trend"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "pids": [42]})
        stop.assert_called_once_with("trend")

    def test_setup_login_and_settings_use_the_same_authenticated_session(self) -> None:
        setup = self.client.post(
            "/api/auth/setup",
            json={
                "username": "admin_user",
                "password": "secret6",
                "password_confirmation": "secret6",
            },
        )
        self.assertEqual(setup.status_code, 200)
        self.assertTrue(self.env["WEBUI_ADMIN_PASSWORD_HASH"].startswith("pbkdf2_sha256:"))

        with patch.object(
            modern_app.backend,
            "public_settings",
            return_value={"config": {}, "env": {}, "secrets": {}, "builtin_sources": []},
        ):
            settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json()["config"], {})

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        login = self.client.post(
            "/api/auth/login", json={"username": "admin_user", "password": "secret6"}
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["username"], "admin_user")

    def test_skip_auth_allows_a_trusted_intranet_installation(self) -> None:
        self.env.update(
            {
                "WEBUI_ADMIN_USERNAME": "!stale-owner",
                "WEBUI_ADMIN_PASSWORD_HASH": "stale_hash",
                "WEBUI_ACCOUNTS": "stale_registry",
            }
        )
        response = self.client.post("/api/auth/setup", json={"action": "skip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.env["WEBUI_AUTH_ENABLED"], "false")
        self.assertEqual(self.env["WEBUI_ADMIN_USERNAME"], "")
        self.assertEqual(self.env["WEBUI_ADMIN_PASSWORD_HASH"], "")
        self.assertEqual(self.env["WEBUI_ACCOUNTS"], "")

        with patch.object(
            modern_app.backend,
            "public_settings",
            return_value={"config": {}, "env": {}, "secrets": {}, "builtin_sources": []},
        ):
            settings = self.client.get("/api/settings")
        self.assertEqual(settings.status_code, 200)

    def test_trend_template_routes_require_the_same_session(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "template_admin",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        with patch.object(
            modern_app.backend,
            "list_trend_prompt_templates",
            return_value=[{"name": "模板", "text": "内容"}],
        ):
            listed = self.client.get("/api/trend/templates")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["name"], "模板")

        with patch.object(
            modern_app.backend,
            "save_trend_prompt_template",
            return_value=[{"name": "模板", "text": "内容"}],
        ) as save:
            saved = self.client.put(
                "/api/trend/templates", json={"name": "模板", "text": "内容"}
            )
        self.assertEqual(saved.status_code, 200)
        save.assert_called_once_with("模板", "内容")

        with patch.object(
            modern_app.backend,
            "delete_trend_prompt_template",
            return_value=[],
        ) as delete:
            deleted = self.client.post("/api/trend/templates/delete", json={"name": "模板"})
        self.assertEqual(deleted.status_code, 200)
        delete.assert_called_once_with("模板")

    def test_account_removal_requires_confirmation_and_password_change_logs_out(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/auth/setup",
                json={
                    "username": "owner_user",
                    "password": "secret6",
                    "password_confirmation": "secret6",
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/accounts/add",
                json={
                    "username": "other_user",
                    "password": "secret7",
                    "password_confirmation": "secret7",
                },
            ).status_code,
            200,
        )

        missing_confirmation = self.client.post(
            "/api/accounts/delete", json={"username": "other_user"}
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertIn("确认", missing_confirmation.json()["detail"])
        self.assertEqual(
            self.client.post(
                "/api/accounts/delete",
                json={"username": "other_user", "confirmed": True},
            ).status_code,
            200,
        )

        changed = self.client.post(
            "/api/accounts/change-password",
            json={
                "current_password": "secret6",
                "new_password": "newsecret6",
                "password_confirmation": "newsecret6",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get("/api/settings").status_code, 401)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
