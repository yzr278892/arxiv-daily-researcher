"""Subscription-mode safety, OAuth and streaming inference regression tests.

All network operations are mocked. No ChatGPT credentials are needed in CI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import LLMConfig
from utils import subscription_siwc as siwc
from utils import subscription_state as state


def _record(**overrides):
    base = {
        "client_id": "oaiapp_test", "subject": "sub-test",
        "issuer": siwc.ISSUER, "email": "user@example.test",
        "access_token": "access-private", "refresh_token": "refresh-private",
        "id_token": "id-private",
        "scopes": ["chatgpt.tokens.use.direct", "offline_access"],
        "expires_at": time.time() + 3600,
    }
    base.update(overrides)
    return base


def _response_event(kind, *, delta=None, error=None, usage=None):
    resp = SimpleNamespace(error=error, usage=usage)
    return SimpleNamespace(type=kind, delta=delta, response=resp)


class _Stream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self.events

    def __exit__(self, *args):
        return False


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved_env = os.environ.get("ADR_SUBSCRIPTION_DIR")
        os.environ["ADR_SUBSCRIPTION_DIR"] = self.temp.name

    def tearDown(self):
        if self.saved_env is None:
            os.environ.pop("ADR_SUBSCRIPTION_DIR", None)
        else:
            os.environ["ADR_SUBSCRIPTION_DIR"] = self.saved_env
        self.temp.cleanup()

    def _persist(self, account=None):
        record = account or _record()
        with state.locked_store():
            state.write_accounts({
                "schema": 1, "active": "oaiapp_test",
                "accounts": {"oaiapp_test": record},
            })

    def test_defaults_remain_api_key_and_unsupported_mode_is_rejected(self):
        model = LLMConfig(api_key="legacy")
        self.assertEqual(model.auth_mode, "api_key")
        self.assertEqual(model.subscription_provider, "openai_siwc")
        from utils.llm_resilience import build_llm_client
        with self.assertRaises(state.SubscriptionError):
            build_llm_client(
                "unused", "unused",
                model_config=SimpleNamespace(auth_mode="subscription",
                                             subscription_provider="anthropic_claude"),
            )
        client = build_llm_client(
            "unused", "unused",
            model_config=SimpleNamespace(auth_mode="subscription",
                                         subscription_provider="openai_siwc"),
        )
        self.assertIsInstance(client, siwc.SIWCClient)

    def test_host_id_persists_without_exposing_credentials(self):
        self.assertEqual(state.host_id(), state.host_id())
        self.assertTrue(state.host_id().startswith("urn:uuid:"))
        self._persist()
        status = state.public_status()
        self.assertTrue(status["active"]["authorized"])
        self.assertTrue(status["active"]["connected"])
        serialized = json.dumps(status)
        for value in ("access-private", "refresh-private", "id-private", "sub-test"):
            self.assertNotIn(value, serialized)
        if os.name != "nt":
            path = Path(self.temp.name) / "openai.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_storage_rejects_group_readable_credentials(self):
        self._persist()
        path = Path(self.temp.name) / "openai.json"
        if os.name != "nt":
            path.chmod(0o644)
            with self.assertRaisesRegex(state.SubscriptionError, "0600"):
                state.read_accounts()

    def test_callback_requires_state_and_issued_client(self):
        code, client = siwc.validate_callback(
            {"state": "valid", "code": "authorization-code",
             "client_id": "oaiapp_assigned"},
            expected_state="valid", expected_client_id="dynamic_agent_client",
        )
        self.assertEqual((code, client), ("authorization-code", "oaiapp_assigned"))
        with self.assertRaises(state.SubscriptionError):
            siwc.validate_callback({"state": "other", "code": "x"}, expected_state="valid",
                                   expected_client_id="oaiapp_old")
        with self.assertRaises(state.SubscriptionError):
            siwc.validate_callback({"state": "valid", "code": "x",
                                    "client_id": "oaiapp_attacker"},
                                   expected_state="valid", expected_client_id="oaiapp_old")
        with self.assertRaises(state.SubscriptionError):
            siwc.validate_callback({"state": "valid", "error": "access_denied"},
                                   expected_state="valid", expected_client_id="oaiapp_old")

    def test_authorize_url_uses_public_dynamic_registration_and_pkce(self):
        from urllib.parse import parse_qs, urlparse
        url = siwc.build_authorize_url(
            client_id="dynamic_agent_client", host="urn:uuid:test",
            callback="http://127.0.0.1:1234/auth/callback",
            state="state", nonce="nonce", challenge="challenge",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["resource"], [siwc.RESOURCE])
        self.assertEqual(query["agent_name_hint"], [siwc.AGENT_NAME])
        self.assertIn("chatgpt.tokens.use.direct", query["scope"][0])
        self.assertEqual(query["code_challenge_method"], ["S256"])

    def test_refresh_rotates_token_in_single_protected_record(self):
        self._persist(_record(expires_at=time.time() - 100))
        refreshed = {
            "access_token": "access-new", "refresh_token": "refresh-new",
            "expires_in": 3600, "token_type": "Bearer",
            "scope": "openid offline_access chatgpt.tokens.use.direct",
        }
        with patch.object(siwc, "_post_form", return_value=refreshed) as request:
            self.assertEqual(siwc.get_access_token(), "access-new")
        self.assertEqual(request.call_args.args[0], siwc.TOKEN_URL)
        self.assertEqual(request.call_args.args[1]["grant_type"], "refresh_token")
        data = state.read_accounts()
        self.assertEqual(data["accounts"]["oaiapp_test"]["refresh_token"], "refresh-new")
        self.assertEqual(data["accounts"]["oaiapp_test"]["access_token"], "access-new")

    def test_missing_granted_scope_never_infers_subscription_eligibility(self):
        self._persist(_record(scopes=["openid", "offline_access"]))
        with self.assertRaisesRegex(state.SubscriptionError, "consent"):
            siwc.get_access_token()

    def test_confirmed_invalid_refresh_revokes_local_tokens_but_keeps_account(self):
        self._persist(_record(expires_at=time.time() - 10))
        with patch.object(siwc, "_post_form", side_effect=state.SubscriptionError(
            "ChatGPT authorization expired; sign in again."
        )):
            with self.assertRaises(state.SubscriptionError):
                siwc.get_access_token()
        row = state.read_accounts()["accounts"]["oaiapp_test"]
        self.assertEqual(row["client_id"], "oaiapp_test")
        self.assertFalse(row["refresh_token"])

    def test_stream_completion_required_and_forbidden_args_omitted(self):
        self._persist()
        events = [
            _response_event("response.output_text.delta", delta='{"ok":'),
            _response_event("response.output_text.delta", delta="true}"),
            _response_event("response.completed", usage=SimpleNamespace(
                input_tokens=12, output_tokens=4,
            )),
        ]
        calls = []
        class _Completions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return _Stream(events)
        class _Client:
            def __init__(self, **kwargs):
                self.responses = _Completions()
        with patch.object(siwc, "get_access_token", return_value="private-bearer"), patch(
            "openai.OpenAI", _Client
        ):
            response = siwc.SIWCClient().generate_chat(
                model="gpt-6.1-sol", messages=[
                    {"role": "system", "content": "Reply JSON."},
                    {"role": "user", "content": "Describe the paper."},
                ],
                temperature=0.3, response_format={"type": "json_object"},
            )
        self.assertEqual(response.choices[0].message.content, '{"ok":true}')
        self.assertEqual(response.usage.input_tokens, 12)
        self.assertTrue(calls[0]["stream"])
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["instructions"], "Reply JSON.")
        self.assertIsInstance(calls[0]["input"], list)
        self.assertNotIn("temperature", calls[0])
        self.assertNotIn("messages", calls[0])

    def test_usage_limit_stream_error_is_not_retryable(self):
        with patch.object(siwc, "get_access_token", return_value="mock-token"):
            class _Client:
                def __init__(self, **kwargs):
                    self.responses = SimpleNamespace(create=lambda **kwargs: _Stream([
                        _response_event("response.failed", error=SimpleNamespace(
                            code="subscription_sharing_usage_limit_exceeded"
                        )),
                    ]))
            with patch("openai.OpenAI", _Client):
                with self.assertRaises(state.SubscriptionLimitError):
                    siwc.SIWCClient().generate_chat(
                        model="gpt-6", messages=[{"role": "user", "content": "hello"}],
                    )
        from utils.llm_resilience import is_retryable_llm_error
        self.assertFalse(is_retryable_llm_error(state.SubscriptionLimitError("limit")))
        self.assertTrue(is_retryable_llm_error(state.SubscriptionTransientError("temporary")))

    def test_stream_without_completed_is_failure(self):
        class _Client:
            def __init__(self, **kwargs):
                self.responses = SimpleNamespace(create=lambda **kwargs: _Stream([
                    _response_event("response.output_text.delta", delta="partial"),
                ]))
        with patch.object(siwc, "get_access_token", return_value="mock"), patch("openai.OpenAI", _Client):
            with self.assertRaises(state.SubscriptionTransientError):
                siwc.SIWCClient().generate_chat(
                    model="gpt-6", messages=[{"role": "user", "content": "hello"}],
                )

    def test_global_request_pool_dispatches_to_subscription_adapter(self):
        from utils.llm_request_pool import LLMRequestPool
        from config import settings
        fake = SimpleNamespace(
            subscription_provider="openai_siwc",
            generate_chat=lambda **kwargs: kwargs["model"],
            generate_responses=lambda **kwargs: kwargs["model"],
        )
        with patch.object(settings, "LLM_REQUEST_POOL_ENABLED", False):
            pool = LLMRequestPool()
            self.assertEqual(pool.call_chat_completion(fake, model="a"), "a")
            self.assertEqual(pool.call_responses(fake, model="b"), "b")


if __name__ == "__main__":
    unittest.main()
