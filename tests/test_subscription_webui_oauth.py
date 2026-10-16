"""Local-only ChatGPT OAuth WebUI security and callback tests.

No external calls or real OAuth credentials are used.
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from modern_webui import app as webui
from utils import subscription_siwc as siwc


class LocalWebUIAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.env = {"WEBUI_AUTH_ENABLED": "true"}
        self.client = TestClient(webui.app)
        self.reader = patch.object(webui, "read_env", side_effect=lambda: dict(self.env))
        self.writer = patch.object(
            webui, "write_env", side_effect=lambda values: self.env.update(
                {str(k): str(v) for k, v in values.items()}
            )
        )
        self.reader.start()
        self.writer.start()
        self.addCleanup(self.reader.stop)
        self.addCleanup(self.writer.stop)
        self.addCleanup(self.client.close)
        with webui._SIWC_AUTH_ATTEMPTS_LOCK:
            webui._SIWC_AUTH_ATTEMPTS.clear()
        self.addCleanup(webui._SIWC_AUTH_ATTEMPTS.clear)

    def _setup_owner(self):
        res = self.client.post(
            "/api/auth/setup",
            json={
                "username": "subscription_owner",
                "password": "secret6",
                "password_confirmation": "secret6",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def test_subscription_routes_require_owner_and_authentication(self):
        for route, method in (
            ("/api/subscriptions/status", "get"),
            ("/api/subscriptions/models", "get"),
            ("/api/subscriptions/select", "post"),
            ("/api/subscriptions/disconnect", "post"),
            ("/api/subscriptions/authorize/start", "post"),
        ):
            with self.subTest(route=route):
                response = self.client.post(route, json={}) if method == "post" else self.client.get(route)
                self.assertNotEqual(response.status_code, 200)
        self.env["WEBUI_AUTH_ENABLED"] = "false"
        with patch.object(webui, "_is_local_siwc_webui", return_value=True):
            response = self.client.post("/api/subscriptions/authorize/start", json={})
        self.assertEqual(response.status_code, 403)

    def test_owner_guard_is_opt_in_for_subscription_billing_only(self):
        fake_request = object()
        with patch.object(webui, "_cached_env", return_value={}), patch.object(
            webui, "_require_subscription_owner"
        ) as require:
            webui._require_subscriber_owner_if_needed(fake_request)
            require.assert_not_called()
            webui._require_subscriber_owner_if_needed(
                fake_request, {"CHEAP_LLM__AUTH_MODE": "subscription"}
            )
            require.assert_called_once_with(fake_request)
        with patch.object(webui, "_cached_env", return_value={
            "SMART_LLM__AUTH_MODE": "subscription",
        }), patch.object(webui, "_require_subscription_owner") as require:
            webui._require_subscriber_owner_if_needed(fake_request, {
                "SMART_LLM__AUTH_MODE": "api_key",
            })
            require.assert_called_once_with(fake_request)

    def test_local_mode_rejects_remote_clients_and_proxies(self):
        target = SimpleNamespace(
            url=SimpleNamespace(scheme="http", hostname="127.0.0.1"),
            client=SimpleNamespace(host="127.0.0.1"),
            headers={},
        )
        with patch.dict("os.environ", {"ADR_SIWC_LOCAL_WEBUI_ENABLED": "true"}):
            self.assertTrue(webui._is_local_siwc_webui(target))
            target.client.host = "192.168.1.12"
            self.assertFalse(webui._is_local_siwc_webui(target))
            target.client.host = "127.0.0.1"
            target.headers = {"x-forwarded-for": "127.0.0.1"}
            self.assertFalse(webui._is_local_siwc_webui(target))
        with patch.dict("os.environ", {"ADR_SIWC_LOCAL_WEBUI_ENABLED": "false"}):
            target.headers = {}
            self.assertFalse(webui._is_local_siwc_webui(target))

    def test_owner_must_opt_in_to_local_login(self):
        self._setup_owner()
        with patch.object(webui, "_is_local_siwc_webui", return_value=False):
            response = self.client.post("/api/subscriptions/authorize/start", json={})
        self.assertEqual(response.status_code, 403)

    def test_start_and_callback_use_one_time_server_state(self):
        self._setup_owner()
        attempt = siwc.AuthorizationAttempt(
            client_id="dynamic_agent_client",
            callback="http://127.0.0.1:8501/auth/callback",
            state="unpredictable-state",
            nonce="nonce",
            verifier="hidden-verifier",
            issued_at=time.time(),
        )
        url = "https://auth.openai.com/api/accounts/authorize?state=unpredictable-state"
        with patch.object(webui, "_is_local_siwc_webui", return_value=True), patch.object(
            siwc, "begin_authorization", return_value=(attempt, url)
        ) as begin, patch.object(
            siwc, "finish_authorization", return_value={"accounts": []}
        ) as finish:
            response = self.client.post(
                "/api/subscriptions/authorize/start", json={"new_account": True}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"authorization_url": url})
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(begin.call_args.kwargs, {"new_account": True})
            self.assertIn("127.0.0.1", begin.call_args.args[0])
            bad = self.client.get(
                "/auth/callback?state=wrong&code=bad", follow_redirects=False
            )
            self.assertEqual(bad.status_code, 400)
            callback = self.client.get(
                "/auth/callback?state=unpredictable-state&code=one-time-code&client_id=oaiapp_new",
                follow_redirects=False,
            )
            self.assertEqual(callback.status_code, 303)
            self.assertNotIn("one-time-code", callback.headers["location"])
            self.assertIn("no-referrer", callback.headers.get("referrer-policy", ""))
            self.assertEqual(finish.call_count, 1)
            self.assertEqual(finish.call_args.args[0]["code"], "one-time-code")
            again = self.client.get(
                "/auth/callback?state=unpredictable-state&code=one-time-code",
                follow_redirects=False,
            )
            self.assertEqual(again.status_code, 400)
            self.assertEqual(finish.call_count, 1)

    def test_callback_without_begin_does_not_accept_tokens(self):
        with patch.object(webui, "_is_local_siwc_webui", return_value=True):
            response = self.client.get(
                "/auth/callback?state=unknown&code=not-an-oauth-session",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
