"""HTTP-level regression tests for the standalone modern WebUI.

These tests intentionally keep all account state in memory.  They verify the
presentation layer's authentication boundary without reading or changing a
developer's real ``.env`` file.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from modern_webui import app as modern_app


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

    def test_report_preview_drops_the_loading_placeholder_style(self) -> None:
        """Loaded previews must not retain the loading state's dashed frame."""
        response = self.client.get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(
            response.text.count('preview.className = "report-preview-host";'), 2
        )

    def test_favorite_papers_use_the_shared_paged_table(self) -> None:
        response = self.client.get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn('pagedTable("favorite-papers"', response.text)
        self.assertNotIn('class="favorite-card"', response.text)

    def test_favorites_page_has_an_auto_favorite_settings_card(self) -> None:
        script = self.client.get("/assets/app.js").text

        self.assertIn("function favoritesSettingsCard", script)
        self.assertIn("auto_favorite_qualified_papers", script)
        self.assertIn('id="favorite-settings-save"', script)
        self.assertIn("function saveFavoriteSettings", script)

    def test_usage_summary_has_horizontal_headers_and_one_value_row(self) -> None:
        response = self.client.get("/assets/app.js")
        start = response.text.index("function usageSummaryTable")
        end = response.text.index("function refreshAnalyticsContent", start)
        summary = response.text[start:end]

        self.assertIn('<thead><tr>${metrics.map(([label])', summary)
        self.assertIn('<tbody><tr>${metrics.map(([, value])', summary)

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
