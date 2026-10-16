"""Keyword normalization uses the model role selected in config.json."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from keyword_tracker.normalizer import KeywordNormalizer  # noqa: E402


class KeywordNormalizationModelTests(unittest.TestCase):
    def test_keyword_normalization_uses_subscription_adapter_when_opted_in(self):
        """The secondary keyword-normalization pipeline must not call paid API."""
        from utils.subscription_siwc import SIWCClient

        model = SimpleNamespace(
            auth_mode="subscription",
            subscription_provider="openai_siwc",
            api_key="unused-paid-api-key",
            base_url="https://untrusted-relay.example/v1",
            model_name="gpt-6.1-sol",
        )
        fake_settings = SimpleNamespace(
            KEYWORD_NORMALIZATION_LLM_ROLE="cheap",
            CHEAP_LLM=model,
            SMART_LLM=model,
        )
        with patch("config.settings", fake_settings):
            normalizer = KeywordNormalizer()
        self.assertIsInstance(normalizer.client, SIWCClient)
        self.assertEqual(normalizer.model, "gpt-6.1-sol")
        self.assertEqual(normalizer.role, "cheap")
        self.assertEqual(normalizer.client.base_url, "https://api.openai.com/v1")
        # Never use a configured custom gateway or API key in this mode.
        from unittest.mock import Mock
        response = SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"normalizations":[{"canonical_form":"quantum",'
                '"original_keywords":["Quantum"],"confidence":0.98}]}'
            )))],
        )
        with patch("keyword_tracker.normalizer.call_chat_completion",
                   return_value=response) as chat:
            data = normalizer._normalize_single_batch(["Quantum"])
        self.assertEqual(data[0].canonical_form, "quantum")
        self.assertEqual(chat.call_args.args[0], normalizer.client)
        self.assertEqual(chat.call_args.kwargs["model"], "gpt-6.1-sol")

    def test_smart_role_uses_the_high_capability_llm_and_records_it(self):
        fake_settings = SimpleNamespace(
            KEYWORD_NORMALIZATION_LLM_ROLE="smart",
            CHEAP_LLM=SimpleNamespace(
                api_key="cheap-key", base_url="https://cheap.example/v1", model_name="cheap-model"
            ),
            SMART_LLM=SimpleNamespace(
                api_key="smart-key", base_url="https://smart.example/v1", model_name="smart-model"
            ),
        )
        events = []
        with patch("config.settings", fake_settings), patch(
            "utils.llm_resilience.build_llm_client", return_value=object()
        ) as build_client:
            normalizer = KeywordNormalizer(
                health_recorder=lambda role, model, success, error: events.append(
                    (role, model, success, error)
                )
            )

        build_client.assert_called_once_with(
            "smart-key", "https://smart.example/v1", model_config=fake_settings.SMART_LLM
        )
        normalizer._record_llm_health(True)
        self.assertEqual(events, [("smart", "smart-model", True, None)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
