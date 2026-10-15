"""Regression tests for per-model generation parameter negotiation."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import LLMConfig, settings
from utils.llm_parameters import (
    effective_parameter_mode,
    chat_generation_parameters,
    responses_generation_parameters,
)


class ModelParameterTests(unittest.TestCase):
    def test_auto_defaults_to_reasoning_for_openai_reasoners_only(self):
        for model in ("gpt-5", "gpt-5.2", "gpt-6", "gpt-6.1-sol", "o1", "o3-mini", "o4-mini"):
            with self.subTest(model=model):
                self.assertEqual(effective_parameter_mode(model), "reasoning")
        for model in ("gpt-4o", "gpt-4.1-mini", "deepseek-reasoner", "glm-4", "qwen2.5:7b"):
            with self.subTest(model=model):
                self.assertEqual(effective_parameter_mode(model), "temperature")

    def test_reasoning_never_sends_temperature(self):
        self.assertEqual(
            chat_generation_parameters("gpt-6", 0.3),
            {"reasoning_effort": "medium"},
        )
        self.assertEqual(
            responses_generation_parameters("gpt-6", 0.3),
            {"reasoning": {"effort": "medium"}},
        )

    def test_legacy_models_retain_temperature(self):
        self.assertEqual(chat_generation_parameters("gpt-4o", 0.3), {"temperature": 0.3})
        self.assertEqual(responses_generation_parameters("gpt-4o", 0.3), {"temperature": 0.3})

    def test_explicit_override_for_compatible_gateway(self):
        self.assertEqual(
            chat_generation_parameters("custom", 0.7, parameter_mode="reasoning", reasoning_effort="high"),
            {"reasoning_effort": "high"},
        )
        self.assertEqual(
            chat_generation_parameters("gpt-6", 0.7, parameter_mode="temperature"),
            {"temperature": 0.7},
        )

    def test_config_env_fields_are_validated(self):
        model = LLMConfig(api_key="k")
        self.assertEqual(model.parameter_mode, "auto")
        self.assertEqual(model.reasoning_effort, "medium")

    def test_analysis_chat_and_responses_receive_endpoint_specific_parameters(self):
        from agents.analysis_agent import AnalysisAgent

        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(base_url="https://example.test/v1", responses=SimpleNamespace(create=lambda **_: None))
        agent.smart_client = object()
        chat_result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        with patch.object(settings.CHEAP_LLM, "model_name", "gpt-6"), patch.object(
            settings.CHEAP_LLM, "parameter_mode", "auto"
        ), patch("agents.analysis_agent.call_chat_completion", return_value=chat_result) as call:
            result, _ = agent._call_llm_with_fallback(agent.cheap_client, "gpt-6", "prompt", 0.3)
        self.assertEqual(result, "ok")
        self.assertNotIn("temperature", call.call_args.kwargs)
        self.assertEqual(call.call_args.kwargs["reasoning_effort"], "medium")

        with patch.object(settings.CHEAP_LLM, "parameter_mode", "auto"), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
        ), patch(
            "agents.analysis_agent.call_responses",
            return_value=SimpleNamespace(output_text="ok"),
        ) as responses_call:
            agent._call_llm_with_fallback(agent.cheap_client, "gpt-6", "prompt", 0.3)
        kwargs = responses_call.call_args.kwargs
        self.assertEqual(kwargs["reasoning"], {"effort": "medium"})
        self.assertNotIn("temperature", kwargs)


if __name__ == "__main__":
    unittest.main()
