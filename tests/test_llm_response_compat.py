import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import (  # noqa: E402
    AnalysisAgent,
    LLMEndpointUnsupportedError,
    LLMResponseError,
    validate_deep_analysis_payload,
)
from config import settings  # noqa: E402
from utils.llm_endpoint_capabilities import (  # noqa: E402
    clear_endpoint_capability_cache_for_tests,
    endpoint_is_known_unsupported,
)


def _chat_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
    )


class LLMResponseCompatibilityTests(unittest.TestCase):
    def test_chat_content_arrays_and_reasoning_content_are_normalized(self):
        response = _chat_response(
            [{"type": "text", "text": "first"}, SimpleNamespace(text="second")]
        )
        self.assertEqual(AnalysisAgent._extract_chat_text(response), "first\nsecond")

        reasoning_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, reasoning_content="fallback")
                )
            ]
        )
        self.assertEqual(AnalysisAgent._extract_chat_text(reasoning_response), "fallback")
        self.assertEqual(
            AnalysisAgent._extract_chat_text(
                {"choices": [{"message": {"content": {"text": "dict"}}}]}
            ),
            "dict",
        )

    def test_responses_output_shapes_are_normalized(self):
        response = SimpleNamespace(
            output=[
                SimpleNamespace(content=[SimpleNamespace(text="one")]),
                {"content": [{"type": "output_text", "text": "two"}]},
            ]
        )
        self.assertEqual(AnalysisAgent._extract_responses_text(response), "one\ntwo")
        self.assertEqual(
            AnalysisAgent._extract_responses_text(SimpleNamespace(output_text="direct")),
            "direct",
        )
        self.assertEqual(
            AnalysisAgent._extract_responses_text({"output_text": "dict-direct"}),
            "dict-direct",
        )

    def test_empty_chat_response_falls_back_to_responses_api(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        responses_result = SimpleNamespace(
            output_text="{\"answer\": \"ok\"}",
            usage={"input_tokens": 12, "output_tokens": 4},
        )
        with patch.object(settings, "RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ) as chat_call, patch(
            "agents.analysis_agent.call_responses", return_value=responses_result
        ) as responses_call:
            self.assertEqual(agent._call_cheap_llm("prompt"), '{"answer": "ok"}')

        chat_call.assert_called_once()
        responses_call.assert_called_once()
        self.assertEqual(
            responses_call.call_args.kwargs["text"],
            {"format": {"type": "json_object"}},
        )

    def test_responses_fallback_retries_with_portable_arguments(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        response = SimpleNamespace(output_text='{"answer": "ok"}')
        with patch.object(settings, "RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ), patch(
            "agents.analysis_agent.call_responses",
            side_effect=[TypeError("unsupported optional argument"), response],
        ) as responses_call:
            self.assertEqual(agent._call_cheap_llm("prompt"), '{"answer": "ok"}')

        self.assertEqual(responses_call.call_count, 2)
        self.assertIn("temperature", responses_call.call_args_list[0].kwargs)
        self.assertIn("text", responses_call.call_args_list[0].kwargs)
        self.assertEqual(
            responses_call.call_args_list[1].kwargs,
            {"model": settings.CHEAP_LLM.model_name, "input": "prompt"},
        )

    def test_responses_fallback_uses_native_stable_instructions(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        response = SimpleNamespace(output_text='{"answer": "ok"}')
        with patch.object(settings, "RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ), patch(
            "agents.analysis_agent.call_responses", return_value=response
        ) as responses_call:
            self.assertEqual(
                agent._call_cheap_llm(
                    "paper-specific data", system_prompt="stable instructions"
                ),
                '{"answer": "ok"}',
            )

        self.assertEqual(
            responses_call.call_args.kwargs["input"], "paper-specific data"
        )
        self.assertEqual(
            responses_call.call_args.kwargs["instructions"], "stable instructions"
        )

    def test_empty_provider_responses_raise_and_remain_retryable(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        with patch.object(settings, "LLM_RETRY_MAX_ATTEMPTS", 2), patch.object(
            settings, "LLM_RETRY_MIN_WAIT", 1
        ), patch.object(settings, "LLM_RETRY_MAX_WAIT", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ) as chat_call, patch(
            "agents.analysis_agent.call_responses",
            return_value=SimpleNamespace(output_text=""),
        ) as responses_call:
            with self.assertRaises(LLMResponseError):
                agent._call_cheap_llm("prompt")

        self.assertEqual(chat_call.call_count, 2)
        self.assertEqual(responses_call.call_count, 2)

    def test_unsupported_responses_endpoint_is_cached_and_not_retried(self):
        """A Chat-only gateway must not receive repeated /responses probes."""
        class _EndpointNotFound(RuntimeError):
            status_code = 404

        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(
            responses=SimpleNamespace(create=lambda **_: None)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            endpoint_base = "https://chat-only.example.test/v1"
            with (
                patch.object(settings, "DATA_DIR", Path(temp_dir)),
                patch.object(settings.CHEAP_LLM, "base_url", endpoint_base),
                patch.object(settings, "LLM_RETRY_MAX_ATTEMPTS", 5),
                patch.object(settings, "TOKEN_TRACKING_ENABLED", False),
                patch(
                    "agents.analysis_agent.call_chat_completion",
                    return_value=_chat_response(None),
                ) as chat_call,
                patch(
                    "agents.analysis_agent.call_responses",
                    side_effect=_EndpointNotFound("404 not found or method not allowed"),
                ) as responses_call,
            ):
                clear_endpoint_capability_cache_for_tests()
                with self.assertRaises(LLMEndpointUnsupportedError):
                    agent._call_cheap_llm("prompt")
                self.assertTrue(
                    endpoint_is_known_unsupported(endpoint_base, "responses")
                )
                # The second request still tries the provider's primary Chat
                # API once, but the failed Responses route is never called.
                with self.assertRaises(LLMEndpointUnsupportedError):
                    agent._call_cheap_llm("prompt")

            self.assertEqual(chat_call.call_count, 2)
            self.assertEqual(responses_call.call_count, 1)
            clear_endpoint_capability_cache_for_tests()

    def test_non_endpoint_chat_failure_does_not_probe_responses(self):
        """Connection/auth failures stay on Chat Completions for retry logic."""
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(
            responses=SimpleNamespace(create=lambda **_: None)
        )
        with patch.object(settings, "LLM_RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            side_effect=RuntimeError("temporary relay failure"),
        ), patch("agents.analysis_agent.call_responses") as responses_call:
            with self.assertRaisesRegex(LLMResponseError, "temporary relay failure"):
                agent._call_cheap_llm("prompt")

        responses_call.assert_not_called()

    def test_provider_connection_failure_is_preserved_without_leaking_a_key(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=None)
        provider_error = RuntimeError("relay unavailable: api_key=sk-very-secret")
        provider_error.__cause__ = ConnectionRefusedError("[Errno 111] Connection refused")

        with patch(
            "agents.analysis_agent.call_chat_completion",
            side_effect=provider_error,
        ):
            with self.assertRaisesRegex(LLMResponseError, "relay unavailable") as raised:
                agent._call_llm_with_fallback(
                    agent.cheap_client,
                    "model",
                    "prompt",
                    temperature=None,
                )

        self.assertIn("Connection refused", str(raised.exception))
        self.assertNotIn("very-secret", str(raised.exception))

    def test_token_usage_accepts_chat_and_responses_field_names(self):
        with patch.object(settings, "TOKEN_TRACKING_ENABLED", True), patch(
            "utils.token_counter.token_counter.add"
        ) as add:
            AnalysisAgent._record_token_usage(
                "model-a",
                7,
                {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 5},
                },
            )
            AnalysisAgent._record_token_usage(
                "model-b", 7, SimpleNamespace(prompt_tokens=9, completion_tokens=3)
            )

        self.assertEqual(add.call_args_list[0].args, ("model-a", 7, 4))
        self.assertEqual(add.call_args_list[0].kwargs, {"cached_prompt_tokens": 5})
        self.assertEqual(add.call_args_list[1].args, ("model-b", 9, 3))
        self.assertEqual(add.call_args_list[1].kwargs, {"cached_prompt_tokens": 0})

    def test_deep_analysis_rejects_metadata_only_but_keeps_custom_template_fields(self):
        template = {
            "modules": [
                {"id": "summary", "enabled": True},
                {"id": "full_text_tldr", "enabled": True},
            ]
        }
        with self.assertRaisesRegex(ValueError, "可渲染内容"):
            validate_deep_analysis_payload({"provider_error": "temporary"}, template)

        payload = {"full_text_tldr": "基于全文的可渲染总结"}
        self.assertEqual(validate_deep_analysis_payload(payload, template), payload)

    def test_deep_analysis_treats_metadata_only_provider_output_as_retryable_failure(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.deep_template = {
            "modules": [
                {
                    "id": "summary",
                    "enabled": True,
                    "format": "quote",
                    "prompt": "概括论文内容",
                }
            ],
            "prompts": {},
        }
        agent._download_and_parse_pdf = lambda _url: "paper text"
        agent._call_smart_llm = lambda _prompt, **_kwargs: '{"provider_error": "empty output"}'

        self.assertIsNone(
            agent.deep_analyze(
                "A paper",
                "https://arxiv.org/pdf/2501.12345v1.pdf",
                "abstract",
            )
        )

    def test_deep_analysis_prompt_explicitly_describes_list_fields(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.deep_template = {
            "modules": [
                {
                    "id": "summary",
                    "enabled": True,
                    "format": "quote",
                    "prompt": "概括论文内容",
                },
                {
                    "id": "innovations",
                    "enabled": True,
                    "format": "list",
                    "prompt": "列出创新点",
                },
            ],
            "prompts": {"analysis_template": "{field_prompts}"},
        }
        agent._download_and_parse_pdf = lambda _url: "paper text"
        prompts = []
        system_prompts = []

        def _response(prompt, **kwargs):
            prompts.append(prompt)
            system_prompts.append(kwargs.get("system_prompt", ""))
            return '{"summary": "内容", "innovations": ["创新"]}'

        agent._call_smart_llm = _response
        result = agent.deep_analyze(
            "A paper",
            "https://arxiv.org/pdf/2501.12345v1.pdf",
            "abstract",
        )

        self.assertEqual(result["innovations"], ["创新"])
        self.assertIn('"innovations": ["...", "..."]', system_prompts[0])
        self.assertIn("论文内容", prompts[0])


if __name__ == "__main__":
    unittest.main()
