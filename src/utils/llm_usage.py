"""Provider-compatible token usage extraction for LLM calls.

OpenAI Chat Completions exposes cache reads under
``prompt_tokens_details.cached_tokens`` while the Responses API uses
``input_tokens_details.cached_tokens``.  Compatible gateways vary, so this
module reads the established nested fields and a small set of safe aliases.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.token_counter import token_counter


def _value(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def usage_value(usage: Any, *names: str) -> Optional[int]:
    """Return the first valid non-negative top-level usage value."""
    for name in names:
        value = _nonnegative_int(_value(usage, name))
        if value is not None:
            return value
    return None


def _nested_usage_value(usage: Any, *paths: tuple[str, str]) -> Optional[int]:
    for parent_name, child_name in paths:
        parent = _value(usage, parent_name)
        value = _nonnegative_int(_value(parent, child_name))
        if value is not None:
            return value
    return None


def extract_token_usage(
    usage: Any, estimated_prompt_tokens: int = 0
) -> tuple[int, int, int]:
    """Return ``(ordinary_input, cached_input, output)`` for one response.

    Standard OpenAI-compatible APIs report input tokens *including* cached
    reads.  The ordinary bucket is therefore ``input - cached``.  When a
    response does not expose cache details, every reported input token stays
    in the ordinary bucket.  If a request failed before a usage payload was
    received, its local estimate is also ordinary input because cache usage is
    unknowable rather than safely inferable.
    """
    estimated = max(0, int(estimated_prompt_tokens or 0))
    reported_input = usage_value(usage, "prompt_tokens", "input_tokens")
    output = usage_value(usage, "completion_tokens", "output_tokens") or 0
    cached = _nested_usage_value(
        usage,
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
        ("prompt_tokens_details", "cached_input_tokens"),
        ("input_tokens_details", "cached_input_tokens"),
        ("prompt_tokens_details", "cache_read_input_tokens"),
        ("input_tokens_details", "cache_read_input_tokens"),
    )
    if cached is None:
        cached = usage_value(
            usage,
            "cached_prompt_tokens",
            "cached_input_tokens",
            "cache_read_input_tokens",
            "cache_read_tokens",
            "cached_content_tokens",
            "cached_tokens",
        )
    cached = cached or 0

    if reported_input is None:
        return estimated, 0, output

    # A malformed gateway must not create negative ordinary input or let a
    # cache field inflate a provider's reported total.
    cached = min(cached, reported_input)
    return reported_input - cached, cached, output


def record_token_usage(
    model: str, usage: Any, estimated_prompt_tokens: int = 0
) -> tuple[int, int, int]:
    """Split provider usage, add it to the process counter, and return it."""
    ordinary, cached, completion = extract_token_usage(usage, estimated_prompt_tokens)
    token_counter.add(
        model,
        ordinary,
        completion,
        cached_prompt_tokens=cached,
    )
    return ordinary, cached, completion
