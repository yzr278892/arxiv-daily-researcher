"""Build compatible generation parameters for chat and Responses requests.

Auto selects reasoning effort for known OpenAI reasoning families, leaving
temperature in place for legacy and unrecognized third-party models.
"""

from __future__ import annotations

import re
from typing import Any

PARAMETER_MODES = ("auto", "reasoning", "temperature")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_OPENAI_REASONING_MODEL = re.compile(
    r"(?:gpt-(?:5|6)(?:[.\-][\w.\-]+)?|o(?:1|3|4)(?:[.\-][\w.\-]+)?)",
    re.IGNORECASE,
)


def effective_parameter_mode(model_name: str, parameter_mode: str = "auto") -> str:
    """Only infer wire-parameter support for known OpenAI reasoning names."""
    if parameter_mode not in PARAMETER_MODES:
        raise ValueError(f"Invalid parameter mode: {parameter_mode}")
    if parameter_mode != "auto":
        return parameter_mode
    name = str(model_name or "").strip().split("/")[-1]
    return "reasoning" if _OPENAI_REASONING_MODEL.fullmatch(name) else "temperature"


def _reasoning_effort(reasoning_effort: str) -> str:
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"Invalid reasoning effort: {reasoning_effort}")
    return reasoning_effort


def chat_generation_parameters(
    model_name: str,
    temperature: float | None,
    *,
    parameter_mode: str = "auto",
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    """Chat Completions accepts reasoning_effort as a top-level parameter."""
    if effective_parameter_mode(model_name, parameter_mode) == "reasoning":
        return {"reasoning_effort": _reasoning_effort(reasoning_effort)}
    return {"temperature": temperature} if temperature is not None else {}


def responses_generation_parameters(
    model_name: str,
    temperature: float | None,
    *,
    parameter_mode: str = "auto",
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    """Responses API uses reasoning.effort rather than reasoning_effort."""
    if effective_parameter_mode(model_name, parameter_mode) == "reasoning":
        return {"reasoning": {"effort": _reasoning_effort(reasoning_effort)}}
    return {"temperature": temperature} if temperature is not None else {}


def effective_model_parameters(
    model_name: str,
    temperature: float | None,
    *,
    parameter_mode: str = "auto",
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    """A non-secret snapshot of parameters actually sent by the worker."""
    return {
        "model_name": model_name,
        **chat_generation_parameters(
            model_name,
            temperature,
            parameter_mode=parameter_mode,
            reasoning_effort=reasoning_effort,
        ),
    }
