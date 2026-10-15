"""
Global LLM request rate limiter.

The OpenAI SDK is used from several agents, sometimes concurrently.  This
module provides a small process-wide throttle so those calls can share one
requests-per-minute budget.
"""

import logging
import threading
import time
from collections import deque
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


class LLMRequestPool:
    """Thread-safe sliding-window limiter for chat completion requests."""

    def __init__(self):
        self._lock = threading.Lock()
        self._request_times = deque()

    def _wait_for_slot(self):
        if not settings.LLM_REQUEST_POOL_ENABLED:
            return

        rpm = max(1, int(settings.LLM_REQUESTS_PER_MINUTE))
        window_seconds = 60.0

        while True:
            with self._lock:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= window_seconds:
                    self._request_times.popleft()

                if len(self._request_times) < rpm:
                    self._request_times.append(now)
                    return

                wait_seconds = window_seconds - (now - self._request_times[0])

            if wait_seconds > settings.LLM_REQUEST_POOL_LOG_SLOW_WAIT_SECONDS:
                logger.info(
                    "LLM 请求池限速中，等待 %.1f 秒后继续（%s requests/min）",
                    wait_seconds,
                    rpm,
                )
            time.sleep(max(0.1, wait_seconds))

    def call_chat_completion(self, client: Any, **kwargs):
        """Call ``client.chat.completions.create`` after acquiring a rate slot."""
        self._wait_for_slot()
        if getattr(client, "subscription_provider", None):
            return client.generate_chat(**kwargs)
        return client.chat.completions.create(**kwargs)

    def call_responses(self, client: Any, **kwargs):
        """Call an optional Responses API through the same global limiter."""
        self._wait_for_slot()
        if getattr(client, "subscription_provider", None):
            return client.generate_responses(**kwargs)
        return client.responses.create(**kwargs)


llm_request_pool = LLMRequestPool()


def call_chat_completion(client: Any, **kwargs):
    """Convenience wrapper for callers that do not need the pool instance."""
    return llm_request_pool.call_chat_completion(client, **kwargs)


def call_responses(client: Any, **kwargs):
    """Convenience wrapper for clients exposing ``responses.create``."""
    return llm_request_pool.call_responses(client, **kwargs)
