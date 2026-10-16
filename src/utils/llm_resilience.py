"""
Shared LLM client construction and retry policy.

Relay providers routinely throttle concurrency and TPS, so every OpenAI
client in this project is built here with an explicit per-request timeout,
and every LLM call goes through the same retry policy: bounded attempts,
exponential backoff with jitter, and no retry on fatal provider errors
(auth/permission/schema) where waiting cannot help.
"""

import logging
from typing import Any

from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)


class LLMEndpointCapabilityError(RuntimeError):
    """A provider has no usable API endpoint for this request shape.

    Retrying a confirmed route mismatch only delays the same 404/405 failure,
    so this marker lets the common retry policy stop immediately.
    """

# 网络层瞬态错误：总是值得重试
from openai import APIConnectionError, APITimeoutError  # noqa: E402

# 供应商侧致命错误：重试只会推迟失败
from openai import (  # noqa: E402
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)

_FATAL_OPENAI_ERRORS = (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)

_TRANSIENT_OPENAI_ERRORS = (APITimeoutError, APIConnectionError)


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Classify an LLM failure as transient (retry) or fatal (fail fast)."""
    seen = set()
    current: BaseException | None = exc
    # Wrapper errors retain provider failures as ``__cause__``. Inspect that
    # short chain so an OpenAI-compatible relay's 404 does not turn into a
    # generic retryable ``LLMResponseError`` at the outer tenacity boundary.
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LLMEndpointCapabilityError):
            return False
        from utils.subscription_state import SubscriptionError
        if isinstance(current, SubscriptionError):
            return current.retryable
        if isinstance(current, _FATAL_OPENAI_ERRORS):
            return False
        current = current.__cause__ or current.__context__
    if isinstance(exc, _FATAL_OPENAI_ERRORS):
        return False
    if isinstance(exc, _TRANSIENT_OPENAI_ERRORS):
        return True
    # APIStatusError 家族（限流、网关 5xx 等）携带 HTTP 状态码
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    # 未知错误（网关抖动、空正文等）默认按瞬态处理
    return True


def build_llm_client(api_key: str, base_url: str, *, model_config: Any = None) -> OpenAI:
    """Select an explicit, authorized subscription adapter or legacy API key."""
    if getattr(model_config, "auth_mode", "api_key") == "subscription":
        provider = getattr(model_config, "subscription_provider", "")
        if provider != "openai_siwc":
            from utils.subscription_state import SubscriptionError
            raise SubscriptionError("Only official ChatGPT SIWC subscription mode is supported.")
        from utils.subscription_siwc import SIWCClient
        return SIWCClient(timeout=float(settings.LLM_TIMEOUT_SECONDS))
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(settings.LLM_TIMEOUT_SECONDS),
        max_retries=max(0, int(settings.LLM_SDK_MAX_RETRIES)),
    )


def llm_retry():
    """Return the shared tenacity decorator for LLM calls.

    Reads settings at decoration time so config reloads take effect for
    clients constructed afterwards; jittered exponential backoff avoids
    synchronized retries when several workers hit one throttled relay.
    """
    from tenacity import (
        before_sleep_log,
        retry,
        retry_if_exception,
        stop_after_attempt,
        wait_exponential_jitter,
    )

    return retry(
        stop=stop_after_attempt(max(1, int(settings.LLM_RETRY_MAX_ATTEMPTS))),
        wait=wait_exponential_jitter(
            initial=max(1, int(settings.LLM_RETRY_MIN_WAIT)),
            max=max(1, int(settings.LLM_RETRY_MAX_WAIT)),
        ),
        retry=retry_if_exception(is_retryable_llm_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
