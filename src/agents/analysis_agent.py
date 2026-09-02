import json
import logging
import hashlib
import math
import re
import threading
import unicodedata
import requests
import fitz  # pymupdf
from typing import Callable, Optional, Dict, Any, List, Mapping, Union
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from config import settings
from parsers.mineru_parser import MineruParser
from utils.llm_request_pool import call_chat_completion, call_responses
from utils.llm_resilience import (
    LLMEndpointCapabilityError,
    build_llm_client,
    llm_retry,
)
from utils.llm_endpoint_capabilities import (
    endpoint_is_known_unsupported,
    is_unsupported_endpoint_error,
    record_endpoint_capability,
)
from utils.llm_health import LLMHealthRecorder
from utils.llm_usage import record_token_usage as record_llm_token_usage
from utils.safe_download import download_external_bytes
from utils.deep_analysis_contract import (
    ANALYSIS_META_KEY,
    CONTENT_SOURCE_ABSTRACT_FALLBACK,
    CONTENT_SOURCE_KEY,
    CONTENT_SOURCE_PDF,
    FULL_TEXT_TLDR_FIELD,
)
from scoring_policy import (
    CORE_RELEVANCE_V2,
    LEGACY_WEIGHTED_KEYWORD_V1,
    LEARNED_PREFERENCE_V1,
    compute_learned_adjustment,
)

logger = logging.getLogger(__name__)


class ScoreValidationError(ValueError):
    """Raised when a screening-model response cannot safely be used as a score.

    A malformed score must fail the paper stage instead of being silently
    persisted.  The daily pipeline can then retry that exact version without
    reporting an arbitrary recommendation.
    """


class LLMResponseError(RuntimeError):
    """Raised when an LLM provider returned no usable response text."""


class LLMEndpointUnsupportedError(LLMResponseError, LLMEndpointCapabilityError):
    """Raised when a compatible gateway exposes neither usable request route."""


def _safe_llm_failure_detail(exc: Optional[BaseException]) -> str:
    """Return useful nested provider diagnostics without exposing credentials.

    Compatible OpenAI clients often wrap a socket/DNS failure in a terse
    ``APIConnectionError('Connection error.')``.  The useful reason sits on
    its chained exception, which used to be discarded when we raised the
    generic empty-response error.  Retain that context for retry records and
    the WebUI, but redact credentials before it leaves the exception chain.
    """
    details = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(details) < 4:
        seen.add(id(current))
        detail = str(current).strip()
        if detail and detail not in details:
            details.append(detail)
        current = current.__cause__ or current.__context__

    rendered = " -> ".join(details)
    if not rendered:
        return ""
    # Avoid persisting a key if a proxy/client included it in an error URL or
    # header.  The patterns intentionally cover common OpenAI-style keys and
    # key=value/header wording without trying to reinterpret arbitrary text.
    rendered = re.sub(
        r"(?i)(api[_-]?key|authorization|token|password)\\s*([:=])\\s*[^,;\\s]+",
        r"\\1\\2***",
        rendered,
    )
    rendered = re.sub(r"(?i)sk-[A-Za-z0-9_-]+", "sk-***", rendered)
    rendered = re.sub(r"(https?://)[^/@\\s]+@", r"\\1***@", rendered)
    return rendered[:600]


def _normalized_person_name(value: Any) -> str:
    """Return a deterministic comparison key for a human name.

    This intentionally remains an *exact* name comparison after harmless
    presentation differences (case, whitespace, punctuation and Unicode
    width) are removed.  It is not fuzzy matching: similar-looking authors
    must not receive an expert bonus accidentally.
    """
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _finite_number(value: Any, field_name: str) -> float:
    """Parse one JSON numeric value without accepting bool/NaN/infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreValidationError(f"{field_name} 必须是有限数字")
    number = float(value)
    if not math.isfinite(number):
        raise ScoreValidationError(f"{field_name} 必须是有限数字")
    return number


# ======================================================================
# Pydantic数据模型：用于验证和结构化LLM输出
# ======================================================================


class WeightedScoreResponse(BaseModel):
    """
    加权评分响应模型（新策略）。

    属性:
        total_score (float): 展示用总分；V2 中等于排序分，旧策略中为原始总分
        keyword_scores (Dict[str, float]): 每个关键词的相关度评分（0-10）
        author_bonus (float): 作者附加分
        expert_authors_found (List[str]): 发现的专家作者列表
        passing_score (float): 兼容旧报告的及格阈值别名
        is_qualified (bool): 是否及格
        reasoning (str): 评分理由和分析
        tldr (str): 一句话总结论文的研究问题和结果
        extracted_keywords (List[str]): 从标题和摘要中提取的关键词
    """

    total_score: float
    keyword_scores: Dict[str, float]
    author_bonus: float
    expert_authors_found: List[str]
    passing_score: float
    is_qualified: bool
    reasoning: str
    tldr: str
    extracted_keywords: List[str]
    # Fields below were introduced by ``core_relevance_v2``.  Defaults make
    # Pydantic hydration of pre-V2 SQLite score_json fully backwards
    # compatible; callers use explicit legacy fallbacks when they are absent.
    strategy_id: str = LEGACY_WEIGHTED_KEYWORD_V1
    relevance_score: Optional[float] = None
    qualification_threshold: Optional[float] = None
    core_keyword_min_score: Optional[float] = None
    core_keyword_scores: Dict[str, float] = Field(default_factory=dict)
    core_keywords_used: List[str] = Field(default_factory=list)
    reference_score: Optional[float] = None
    author_preference_bonus: float = 0.0
    ranking_score: Optional[float] = None
    qualification_reason: str = ""
    # ``learned_preference_v1`` only. Defaults keep hydration of rows written
    # by the other strategies fully backwards compatible.
    learned_adjustment: Optional[float] = None
    learned_keywords_matched: List[str] = Field(default_factory=list)
    learned_authors_matched: List[str] = Field(default_factory=list)


class Stage2Response(BaseModel):
    """
    深度分析响应模型（可配置字段）。

    属性根据 settings.ENABLED_ANALYSIS_FIELDS 动态使用。
    """

    chinese_title: Optional[str] = None
    summary: Optional[str] = None
    # List-oriented template fields historically appeared as a single string
    # because the old prompt rendered every field as ``"..."``.  Preserve
    # those renderable historical values while new prompts explicitly request
    # arrays for list/inline modules.
    innovations: Optional[Union[List[str], str]] = None
    methodology: Optional[str] = None
    key_results: Optional[Union[List[str], str]] = None
    tech_stack: Optional[Union[List[str], str]] = None
    strengths: Optional[Union[List[str], str]] = None
    limitations: Optional[Union[List[str], str]] = None
    relevance_to_keywords: Optional[str] = None
    future_work: Optional[Union[List[str], str]] = None
    custom_answers: Optional[Dict[str, str]] = None
    full_text_tldr: Optional[str] = None


def _has_usable_analysis_content(value: Any) -> bool:
    """Whether one deep-analysis field contains renderable, nonempty data."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(
            isinstance(item, str) and bool(item.strip()) for item in value
        )
    if isinstance(value, dict):
        return bool(value) and all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(item, str)
            and bool(item.strip())
            for key, item in value.items()
        )
    return False


def validate_deep_analysis_payload(
    payload: Any, deep_template: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """Validate a persisted or fresh deep-analysis result without dropping custom fields.

    ``Stage2Response`` validates the built-in field types, while report templates
    can introduce future/custom module IDs.  Keep those IDs intact, but require
    at least one enabled module to have usable content.  Otherwise a gateway's
    ``{\"error\": ...}``/metadata object can be marked as successful analysis and
    later render as an apparently missing analysis.

    Historical partial analyses remain readable: an enabled module does not
    need every sibling field to be present, but a result with no usable report
    field is never accepted as a completed stage.
    """
    if not isinstance(payload, dict) or not payload:
        raise ValueError("深度分析必须是非空 JSON 对象")

    Stage2Response.model_validate(payload)

    modules = deep_template.get("modules", []) if isinstance(deep_template, Mapping) else []
    enabled_ids = set()
    for module in modules:
        if not isinstance(module, Mapping) or not module.get("enabled", True):
            continue
        module_id = module.get("id")
        if not isinstance(module_id, str) or not module_id.strip():
            continue
        # A custom-question module without questions is intentionally absent
        # from the generated prompt and therefore cannot produce a response.
        if module_id == "custom_questions" and not module.get("questions"):
            continue
        enabled_ids.add(module_id)

    # Old templates and unit-test fixtures may not list modules.  In that
    # case known Stage2 fields still provide a stable backward-compatible
    # definition of renderable analysis content.
    candidate_ids = enabled_ids or set(Stage2Response.model_fields)
    if not any(
        field_id in payload and _has_usable_analysis_content(payload[field_id])
        for field_id in candidate_ids
    ):
        expected = ", ".join(sorted(candidate_ids)) or "已启用模板字段"
        raise ValueError(f"深度分析未包含可渲染内容（期望字段: {expected}）")

    return payload


class AnalysisAgent:
    """
    论文分析Agent（新策略：加权评分系统）。

    职责:
    - 基于关键词权重对论文进行加权评分
    - 检测专家作者并给予附加分
    - 计算动态及格分并判断是否合格
    - 对及格论文进行深度分析（使用可配置模板）
    """

    def __init__(self, health_recorder: Optional[LLMHealthRecorder] = None):
        # 初始化两个不同性能LLM客户端
        self.cheap_client = build_llm_client(
            settings.CHEAP_LLM.api_key, settings.CHEAP_LLM.base_url
        )
        self.smart_client = build_llm_client(
            settings.SMART_LLM.api_key, settings.SMART_LLM.base_url
        )

        # 初始化 MinerU PDF 解析器
        self.mineru_parser = MineruParser()

        # 加载报告模板以获取prompt配置
        self.basic_template = settings.load_report_template("basic_report_template.json")
        self.deep_template = settings.load_report_template("deep_analysis_template.json")
        # The recorder is injected only by real workflow entry points.  This
        # keeps the agent reusable in isolated tooling/tests and prevents a
        # health observation from ever becoming a dependency of analysis.
        self._health_recorder = health_recorder

    def _record_llm_health(
        self,
        role: str,
        model: str,
        success: bool,
        error: Optional[BaseException] = None,
    ) -> None:
        recorder = getattr(self, "_health_recorder", None)
        if recorder is not None:
            recorder(role, model, success, error)

    def set_health_recorder(self, health_recorder: Optional[LLMHealthRecorder]) -> None:
        """Attach optional passive observability after agent construction."""
        self._health_recorder = health_recorder

    # ======================================================================
    # 带重试的 LLM / HTTP 调用封装
    # ======================================================================

    @staticmethod
    def _text_from_parts(value: Any) -> Optional[str]:
        """Extract text from SDK strings, content arrays, or typed objects."""
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, dict):
            candidate = value.get("text")
            return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None
        if not isinstance(value, (list, tuple)):
            return None

        parts = []
        for part in value:
            if isinstance(part, dict):
                candidate = part.get("text")
            else:
                candidate = getattr(part, "text", None)
            if isinstance(candidate, str) and candidate.strip():
                parts.append(candidate.strip())
        return "\n".join(parts).strip() or None

    @classmethod
    def _extract_chat_text(cls, response: Any) -> Optional[str]:
        """Extract final text from a chat-completions response."""
        choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
        if not isinstance(choices, (list, tuple)) or not choices:
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else getattr(choices[0], "message", None)
        if message is None and isinstance(choices[0], dict):
            message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            reasoning = message.get("reasoning_content")
        else:
            content = getattr(message, "content", None)
            reasoning = getattr(message, "reasoning_content", None)

        text = cls._text_from_parts(content)
        if text:
            return text
        # A few reasoning-model gateways expose the only generated text under
        # reasoning_content.  It is still validated by the caller (JSON/schema
        # parsing), so it cannot silently become a successful fake result.
        return cls._text_from_parts(reasoning)

    @classmethod
    def _extract_responses_text(cls, response: Any) -> Optional[str]:
        """Extract text from the OpenAI Responses API and compatible gateways."""
        output_text = response.get("output_text") if isinstance(response, dict) else getattr(response, "output_text", None)
        text = cls._text_from_parts(output_text)
        if text:
            return text

        output = response.get("output") if isinstance(response, dict) else getattr(response, "output", None)
        if not isinstance(output, (list, tuple)):
            return None
        parts = []
        for item in output:
            content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
            item_text = cls._text_from_parts(content)
            if item_text:
                parts.append(item_text)
        return "\n".join(parts).strip() or None

    @staticmethod
    def _usage_value(usage: Any, *names: str) -> Optional[int]:
        if usage is None:
            return None
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, bool):
                continue
            try:
                if value is not None:
                    return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _response_usage(response: Any) -> Any:
        return response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)

    @classmethod
    def _record_token_usage(cls, model_name: str, estimated_prompt_tokens: int, usage: Any) -> None:
        if not settings.TOKEN_TRACKING_ENABLED or usage is None:
            return
        record_llm_token_usage(
            model_name,
            usage,
            estimated_prompt_tokens,
        )

    def _call_llm_with_fallback(
        self,
        client: Any,
        model: str,
        prompt: str,
        temperature: Optional[float],
        response_format: Optional[Dict[str, str]] = None,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, Any]:
        """Call the provider's supported endpoint without blind route fallback.

        Chat Completions remains the portable first choice.  A Responses call
        is attempted only after a *successful but empty* chat response or an
        explicitly unsupported Chat route, and a 404/405/501 is cached by
        base URL.  That turns an OpenAI-SDK attribute check into actual
        endpoint detection: gateways such as qnaigc that expose no Responses
        API stop receiving the same failing fallback for every paper/retry.
        """
        last_error: Optional[BaseException] = None
        chat_returned_empty = False
        messages = []
        if isinstance(system_prompt, str) and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": prompt})
        chat_kwargs = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            chat_kwargs["temperature"] = temperature
        if response_format is not None:
            chat_kwargs["response_format"] = response_format

        # ``client.base_url`` is an httpx URL in newer SDKs; preserve the
        # configured string as the stable compatibility key.
        configured_base_url = str(
            getattr(client, "base_url", None) or ""
        )
        if not configured_base_url:
            configured_base_url = ""

        # The caller supplies one concrete client/model pair. Infer its base
        # URL from identity only as a last resort; AnalysisAgent always builds
        # clients from these two settings, while unit-test doubles need no URL.
        if client is getattr(self, "cheap_client", None):
            base_url = settings.CHEAP_LLM.base_url
        elif client is getattr(self, "smart_client", None):
            base_url = settings.SMART_LLM.base_url
        else:
            base_url = configured_base_url
        chat_is_unavailable = endpoint_is_known_unsupported(base_url, "chat_completions")

        if not chat_is_unavailable:
            try:
                response = call_chat_completion(client, **chat_kwargs)
                text = self._extract_chat_text(response)
                if text:
                    record_endpoint_capability(base_url, "chat_completions", "supported")
                    return text, self._response_usage(response)
                # A 2xx response with no usable text can legitimately be a
                # Responses-only gateway, so it is the one safe fallback case.
                last_error = LLMResponseError("chat.completions 返回空正文")
                chat_returned_empty = True
            except Exception as exc:
                if is_unsupported_endpoint_error(exc):
                    record_endpoint_capability(
                        base_url, "chat_completions", "unsupported", reason=exc
                    )
                    chat_is_unavailable = True
                    last_error = exc
                else:
                    # Authentication, schema, rate-limit and network failures
                    # belong to the original route. Trying another endpoint
                    # would duplicate work or mask the real diagnosis.
                    detail = _safe_llm_failure_detail(exc)
                    message = "Chat Completions 调用失败"
                    if detail:
                        message += f"（{detail}）"
                    raise LLMResponseError(message) from exc

        responses = getattr(client, "responses", None)
        responses_is_unavailable = endpoint_is_known_unsupported(base_url, "responses")
        if (
            not responses_is_unavailable
            and responses is not None
            and callable(getattr(responses, "create", None))
        ):
            # ``instructions`` is the native Responses API system-instruction
            # field. Keep ``input`` as a portable string: some compatible
            # gateways accept Responses but do not accept Chat's message-item
            # shape here.
            response_kwargs = {"model": model, "input": prompt}
            if isinstance(system_prompt, str) and system_prompt.strip():
                response_kwargs["instructions"] = system_prompt.strip()
            if temperature is not None:
                response_kwargs["temperature"] = temperature
            if response_format is not None:
                # Responses API names the equivalent of chat.com's
                # ``response_format`` as ``text.format``.
                response_kwargs["text"] = {"format": response_format}
            try:
                response = call_responses(client, **response_kwargs)
                text = self._extract_responses_text(response)
                if text:
                    record_endpoint_capability(base_url, "responses", "supported")
                    return text, self._response_usage(response)
                last_error = LLMResponseError("Responses API 返回空正文")
            except TypeError as exc:
                # Some compatible gateways accept the endpoint but reject
                # optional OpenAI parameters. Retry once with only the
                # portable model/input shape; a real empty response remains a
                # failure for the outer retry policy.
                last_error = exc
                minimal_response_kwargs = {
                    key: value
                    for key, value in response_kwargs.items()
                    if key not in {"temperature", "text", "instructions"}
                }
                if isinstance(system_prompt, str) and system_prompt.strip():
                    # A Responses-compatible gateway that rejects
                    # ``instructions`` still receives the original task
                    # semantics through the universally accepted text input.
                    minimal_response_kwargs["input"] = (
                        f"{system_prompt.strip()}\n\n{prompt}"
                    )
                if minimal_response_kwargs != response_kwargs:
                    try:
                        response = call_responses(client, **minimal_response_kwargs)
                        text = self._extract_responses_text(response)
                        if text:
                            record_endpoint_capability(base_url, "responses", "supported")
                            return text, self._response_usage(response)
                        last_error = LLMResponseError("Responses API 返回空正文")
                    except Exception as retry_exc:
                        if is_unsupported_endpoint_error(retry_exc):
                            record_endpoint_capability(
                                base_url, "responses", "unsupported", reason=retry_exc
                            )
                        last_error = retry_exc
            except Exception as exc:
                if is_unsupported_endpoint_error(exc):
                    record_endpoint_capability(
                        base_url, "responses", "unsupported", reason=exc
                    )
                last_error = exc

        # A provider that has already rejected Responses and has just returned
        # no usable Chat text has no viable route for this request. Mark it
        # fatal so tenacity does not spend several exponential-backoff cycles
        # reissuing the same Chat request without any possible fallback.
        if chat_returned_empty and endpoint_is_known_unsupported(base_url, "responses"):
            detail = _safe_llm_failure_detail(last_error)
            message = "Chat Completions 未返回可用正文，且 Responses API 已确认不受此服务支持"
            if detail:
                message += f"（{detail}）"
            raise LLMEndpointUnsupportedError(message) from last_error

        if chat_is_unavailable and (
            responses_is_unavailable
            or endpoint_is_known_unsupported(base_url, "responses")
        ):
            detail = _safe_llm_failure_detail(last_error)
            message = "LLM 服务未提供可用的 Chat Completions 或 Responses 端点"
            if detail:
                message += f"（最后错误：{detail}）"
            raise LLMEndpointUnsupportedError(message) from last_error

        detail = _safe_llm_failure_detail(last_error)
        message = "LLM 未返回可用正文"
        if detail:
            message += f"（最后错误：{detail}）"
        raise LLMResponseError(message) from last_error

    def _call_cheap_llm(
        self, prompt: str, *, system_prompt: Optional[str] = None
    ) -> str:
        """调用低成本LLM（JSON模式），带自动重试。"""
        estimated_prompt_tokens = len((system_prompt or "") + prompt) // 4

        @llm_retry()
        def _do_call():
            try:
                content, usage = self._call_llm_with_fallback(
                    self.cheap_client,
                    settings.CHEAP_LLM.model_name,
                    prompt,
                    settings.CHEAP_LLM.temperature,
                    {"type": "json_object"},
                    system_prompt,
                )
            except Exception:
                if settings.TOKEN_TRACKING_ENABLED:
                    from utils.token_counter import token_counter

                    token_counter.add(settings.CHEAP_LLM.model_name, estimated_prompt_tokens, 0)
                raise
            self._record_token_usage(
                settings.CHEAP_LLM.model_name, estimated_prompt_tokens, usage
            )
            return content

        try:
            result = _do_call()
        except Exception as exc:
            self._record_llm_health("cheap", settings.CHEAP_LLM.model_name, False, exc)
            raise
        self._record_llm_health("cheap", settings.CHEAP_LLM.model_name, True)
        return result

    def _call_cheap_llm_plain(
        self, prompt: str, *, system_prompt: Optional[str] = None
    ) -> str:
        """调用低成本LLM（纯文本模式），带自动重试。"""
        estimated_prompt_tokens = len((system_prompt or "") + prompt) // 4

        @llm_retry()
        def _do_call():
            try:
                content, usage = self._call_llm_with_fallback(
                    self.cheap_client,
                    settings.CHEAP_LLM.model_name,
                    prompt,
                    0.3,
                    system_prompt=system_prompt,
                )
            except Exception:
                if settings.TOKEN_TRACKING_ENABLED:
                    from utils.token_counter import token_counter

                    token_counter.add(settings.CHEAP_LLM.model_name, estimated_prompt_tokens, 0)
                raise
            self._record_token_usage(
                settings.CHEAP_LLM.model_name, estimated_prompt_tokens, usage
            )
            return content.strip()

        try:
            result = _do_call()
        except Exception as exc:
            self._record_llm_health("cheap", settings.CHEAP_LLM.model_name, False, exc)
            raise
        self._record_llm_health("cheap", settings.CHEAP_LLM.model_name, True)
        return result

    def _call_smart_llm(
        self, prompt: str, *, system_prompt: Optional[str] = None
    ) -> str:
        """调用高性能LLM（JSON模式），带自动重试。"""
        estimated_prompt_tokens = len((system_prompt or "") + prompt) // 4

        @llm_retry()
        def _do_call():
            try:
                content, usage = self._call_llm_with_fallback(
                    self.smart_client,
                    settings.SMART_LLM.model_name,
                    prompt,
                    settings.SMART_LLM.temperature,
                    {"type": "json_object"},
                    system_prompt,
                )
            except Exception:
                if settings.TOKEN_TRACKING_ENABLED:
                    from utils.token_counter import token_counter

                    token_counter.add(settings.SMART_LLM.model_name, estimated_prompt_tokens, 0)
                raise
            self._record_token_usage(
                settings.SMART_LLM.model_name, estimated_prompt_tokens, usage
            )
            return content

        try:
            result = _do_call()
        except Exception as exc:
            self._record_llm_health("smart", settings.SMART_LLM.model_name, False, exc)
            raise
        self._record_llm_health("smart", settings.SMART_LLM.model_name, True)
        return result

    def _download_pdf_bytes(self, pdf_url: str) -> bytes:
        """Download a bounded, redirect-validated PDF with retries."""

        @retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=settings.RETRY_MIN_WAIT, max=settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_download():
            headers = {
                "User-Agent": "ArxivDailyResearcher/2.0 (https://github.com/yzr278892/arxiv-daily-researcher; yzr278892@gmail.com)"
            }
            return download_external_bytes(
                pdf_url,
                requests.get,
                max_bytes=max(1, int(settings.PDF_DOWNLOAD_MAX_BYTES)),
                request_kwargs={"headers": headers, "timeout": 30},
                # A PDF header can be preceded by a small binary comment, so
                # inspect its initial KiB rather than requiring offset zero.
                required_magic=b"%PDF-",
            )

        return _do_download()

    def _clean_json_string(self, json_str: str) -> str:
        """清理LLM响应中的Markdown代码块标记和非法转义字符。"""
        # 移除Markdown代码块标记
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        json_str = json_str.strip()

        # 修复常见的非法转义字符（LaTeX符号等）
        # 使用原始字符串处理，避免Python本身的转义问题
        import re

        # 找到所有字符串值（在双引号内的内容）
        def fix_escapes_in_match(match):
            content = match.group(1)
            # 只保留合法的JSON转义序列：\" \\ \/ \b \f \n \r \t \uXXXX
            # 将其他反斜杠转义为双反斜杠
            result = ""
            i = 0
            while i < len(content):
                if content[i] == "\\":
                    if i + 1 < len(content):
                        next_char = content[i + 1]
                        # 合法的转义字符
                        if next_char in ['"', "\\", "/", "b", "f", "n", "r", "t"]:
                            result += content[i : i + 2]
                            i += 2
                        # Unicode转义
                        elif next_char == "u" and i + 5 < len(content):
                            result += content[i : i + 6]
                            i += 6
                        # 非法转义，转义反斜杠本身
                        else:
                            result += "\\\\"
                            i += 1
                    else:
                        result += "\\\\"
                        i += 1
                else:
                    result += content[i]
                    i += 1
            return f'"{result}"'

        # 匹配JSON字符串值（简化版，不处理嵌套）
        json_str = re.sub(r'"((?:[^"\\]|\\.)*)"', fix_escapes_in_match, json_str)

        return json_str

    # ======================================================================
    # 评分策略：旧加权兼容模式与核心相关性 V2
    # ======================================================================

    def score_paper_with_keywords(
        self,
        title: str,
        authors: str | List[str],
        abstract: str,
        keywords_dict: Dict[str, float],
        learned_terms: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> WeightedScoreResponse:
        """
        使用已配置的关键词策略对论文进行评分。

        ``legacy_weighted_keyword_v1`` 保留原公式以支持可逆迁移。
        ``core_relevance_v2`` 以核心主关键词的归一化内容相关度决定
        资格；参考关键词和作者偏好只影响已合格论文的排序。
        ``learned_preference_v1`` 在旧公式之上叠加学习库修正：从收藏
        偏好与 v1 及格历史学到的关键词/作者权重经过限幅与衰减后调
        整总分，直接配置的关键词权重始终高于学习权重。

        参数:
            title (str): 论文标题
            authors (str | List[str]): 作者列表。调用方应尽量传入原始作者列表，
                以便确定性校验专家作者加分。
            abstract (str): 论文摘要
            keywords_dict (Dict[str, float]): 关键词-权重字典
            learned_terms (Optional[Dict[str, Dict[str, float]]]): 学习库
                {"keyword": {词: 权重}, "author": {作者: 权重}}，仅学习模式使用。

        返回:
            WeightedScoreResponse: 包含详细评分信息的响应对象
        """
        # 评分输入也是配置的一部分。先校验它，避免异常配置把 NaN 或
        # 负权重一路传到报告和排序逻辑中。
        try:
            strategy_id = settings.normalized_score_strategy()
        except ValueError as exc:
            raise ScoreValidationError(str(exc)) from exc
        max_score = _finite_number(
            settings.MAX_SCORE_PER_KEYWORD, "MAX_SCORE_PER_KEYWORD"
        )
        if max_score <= 0:
            raise ScoreValidationError("MAX_SCORE_PER_KEYWORD 必须大于 0")

        normalized_keywords: Dict[str, float] = {}
        for keyword, weight in keywords_dict.items():
            if not isinstance(keyword, str) or not keyword.strip():
                raise ScoreValidationError("评分关键词必须是非空字符串")
            numeric_weight = _finite_number(weight, f"关键词 {keyword!r} 的权重")
            if numeric_weight < 0:
                raise ScoreValidationError(f"关键词 {keyword!r} 的权重不能为负数")
            normalized_keywords[keyword] = numeric_weight
        if not normalized_keywords:
            raise ScoreValidationError("至少需要一个评分关键词")

        if isinstance(authors, str):
            author_names = [name.strip() for name in authors.split(",") if name.strip()]
        elif isinstance(authors, list) and all(isinstance(name, str) for name in authors):
            author_names = [name.strip() for name in authors if name.strip()]
        else:
            raise ScoreValidationError("论文作者必须是字符串或字符串列表")
        authors_text = ", ".join(author_names)

        # V2 can determine a stable core set only from explicitly configured
        # primary keywords.  Reference extraction is intentionally auxiliary:
        # adding low-weight reference terms must never make a paper easier to
        # qualify.  Some existing installations use reference extraction
        # without primaries, so retain a visible, conservative all-keyword
        # fallback rather than making their daily pipeline unusable.
        configured_primary = {
            keyword.strip()
            for keyword in settings.PRIMARY_KEYWORDS
            if isinstance(keyword, str) and keyword.strip()
        }
        primary_keywords = [
            keyword for keyword in normalized_keywords if keyword in configured_primary
        ]
        used_primary_fallback = False
        if strategy_id == CORE_RELEVANCE_V2 and not primary_keywords:
            primary_keywords = list(normalized_keywords)
            used_primary_fallback = True
            logger.warning(
                "core_relevance_v2 未配置可用 PRIMARY_KEYWORDS；"
                "本次以全部关键词作为核心集合降级。请配置主要关键词以获得稳定资格门槛。"
            )

        # 旧策略的阈值只属于旧判定模式。V2 不应因为遗留公式的错误
        # 配置而无法执行；它使用自己的归一化资格门槛。
        total_weight = math.fsum(normalized_keywords.values())
        legacy_passing_score = None
        if strategy_id in (LEGACY_WEIGHTED_KEYWORD_V1, LEARNED_PREFERENCE_V1):
            legacy_passing_score = _finite_number(
                settings.calculate_passing_score(total_weight), "动态及格分"
            )
            if legacy_passing_score < 0:
                raise ScoreValidationError("动态及格分不能为负数")

        relevance_threshold = None
        core_keyword_min_score = None
        reference_ranking_weight = None
        if strategy_id == CORE_RELEVANCE_V2:
            relevance_threshold = _finite_number(
                settings.CORE_RELEVANCE_THRESHOLD, "核心相关性门槛"
            )
            core_keyword_min_score = _finite_number(
                settings.CORE_KEYWORD_MIN_SCORE, "核心关键词强匹配门槛"
            )
            reference_ranking_weight = _finite_number(
                settings.REFERENCE_RANKING_WEIGHT, "参考关键词排序权重"
            )
            if relevance_threshold < 0 or relevance_threshold > max_score:
                raise ScoreValidationError(
                    f"CORE_RELEVANCE_THRESHOLD 必须在 0-{max_score:g} 之间"
                )
            if core_keyword_min_score < 0 or core_keyword_min_score > max_score:
                raise ScoreValidationError(
                    f"CORE_KEYWORD_MIN_SCORE 必须在 0-{max_score:g} 之间"
                )
            if reference_ranking_weight < 0:
                raise ScoreValidationError("REFERENCE_RANKING_WEIGHT 不能为负数")

        author_bonus_points = 0.0
        author_bonus_by_normalized_name: Dict[str, float] = {}
        if settings.ENABLE_AUTHOR_BONUS:
            author_bonus_points = _finite_number(
                settings.AUTHOR_BONUS_POINTS, "AUTHOR_BONUS_POINTS"
            )
            if author_bonus_points < 0:
                raise ScoreValidationError("AUTHOR_BONUS_POINTS 不能为负数")
            configured_author_names = getattr(settings, "EXPERT_AUTHORS", [])
            if not isinstance(configured_author_names, list):
                raise ScoreValidationError("EXPERT_AUTHORS 必须是列表")
            for name in configured_author_names:
                normalized_name = _normalized_person_name(name)
                if normalized_name:
                    author_bonus_by_normalized_name[normalized_name] = author_bonus_points
            configured_author_points = getattr(settings, "AUTHOR_BONUS_BY_AUTHOR", {})
            if configured_author_points is None:
                configured_author_points = {}
            if not isinstance(configured_author_points, Mapping):
                raise ScoreValidationError("AUTHOR_BONUS_BY_AUTHOR 必须是对象")
            for name, points in configured_author_points.items():
                normalized_name = _normalized_person_name(name)
                # A stale map must not award a bonus to a name which is no
                # longer listed as an expert author.  This keeps legacy
                # patches and hand-edited settings safely deterministic.
                if not normalized_name or normalized_name not in author_bonus_by_normalized_name:
                    continue
                value = _finite_number(points, f"作者 {name!r} 的加分")
                if value < 0:
                    raise ScoreValidationError(f"作者 {name!r} 的加分不能为负数")
                author_bonus_by_normalized_name[normalized_name] = value

        learned_weight_dampening = None
        learned_term_weight_cap = None
        if strategy_id == LEARNED_PREFERENCE_V1:
            learned_weight_dampening = _finite_number(
                settings.LEARNED_WEIGHT_DAMPENING, "LEARNED_WEIGHT_DAMPENING"
            )
            learned_term_weight_cap = _finite_number(
                settings.LEARNED_TERM_WEIGHT_CAP, "LEARNED_TERM_WEIGHT_CAP"
            )
            if not 0 <= learned_weight_dampening <= 1:
                raise ScoreValidationError(
                    "LEARNED_WEIGHT_DAMPENING 必须在 0-1 之间"
                )
            if learned_term_weight_cap <= 0:
                raise ScoreValidationError("LEARNED_TERM_WEIGHT_CAP 必须大于 0")

        # 构建关键词列表字符串
        keywords_list = "\n".join(
            [f"  - {kw} (权重: {weight:.1f})" for kw, weight in normalized_keywords.items()]
        )

        primary_keywords_text = "、".join(primary_keywords) or "（无）"
        if strategy_id == CORE_RELEVANCE_V2:
            scoring_policy_text = f"""
评分决策规则（由系统计算，不要自行判定是否及格）：
- 核心关键词: {primary_keywords_text}
- 核心相关度阈值: {relevance_threshold:.1f}/{max_score:g}
- 至少一个核心关键词强匹配: {core_keyword_min_score:.1f}/{max_score:g}
- Reference 关键词仅作排序辅助，不能替代核心相关性。
"""
        else:
            scoring_policy_text = f"""
旧版加权判定（由系统计算，不要自行判定是否及格）：
- 关键词总权重: {total_weight:.1f}
- 动态及格分: {legacy_passing_score:.1f}
"""
            if strategy_id == LEARNED_PREFERENCE_V1:
                scoring_policy_text += (
                    "- 学习模式：系统会在加权总分上叠加衰减后的学习偏好项"
                    "（历史收藏与 v1 及格信号学得的关键词/作者），模型无需考虑。\n"
                )

        system_prompt = f"""你是一名学术论文评审专家。请基于以下关键词对论文进行相关性评分，并提取论文信息。

研究背景:
{settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"}

评分关键词及权重:
{keywords_list}

评分任务:
1. 理解论文的研究内容和主题
2. 对每个关键词评估相关度（0-{max_score:g}分）:
   - 0分: 完全无关
   - {max_score / 2:g}分: 有一定关联
   - {max_score:g}分: 高度相关，核心内容
3. 用一句话总结论文研究的问题和结果（TLDR）
4. 从标题和摘要中提取5-8个核心关键词（英文）

作者加分由系统根据原始作者列表做确定性精确校验；不要猜测专家作者，
也不要输出作者加分或 expert_authors_found 字段。

{scoring_policy_text}
每个关键词最高相关度: {max_score:g} 分

输出格式: JSON对象，包含以下字段:
{{
  "keyword_scores": {{"关键词1": 8.0, "关键词2": 5.0, ...}},
  "reasoning": "详细的评分理由和分析",
  "tldr": "一句话总结论文研究的核心问题和主要结果",
  "extracted_keywords": ["keyword1", "keyword2", "keyword3", ...]
}}

要求:
- keyword_scores 必须包含所有给定的关键词
- keyword_scores 必须且只能包含给定的所有关键词，键名必须逐字一致
- 每个关键词的评分范围: 0-{max_score:g}
- reasoning 应简明扼要地说明论文与关键词的相关性
- tldr 应该是一句完整的话，包含研究问题和主要结果
- extracted_keywords 应提取5-8个最能代表论文内容的关键词或短语
"""
        prompt = f"""论文信息:
标题: {title}
作者: {authors_text}
摘要: {abstract}
"""

        try:
            content = self._call_cheap_llm(prompt, system_prompt=system_prompt)
            content = self._clean_json_string(content)

            try:
                data = json.loads(content)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON解析失败: {json_err}")
                logger.error(f"原始内容（前500字符）: {content[:500]}")
                raise

            if not isinstance(data, dict):
                raise ScoreValidationError("评分模型返回的 JSON 顶层必须是对象")

            # Do not substitute defaults here.  A missing TLDR/reasoning used
            # to become a seemingly successful, permanently cached score and
            # was the primary source of lost report content after a restart.
            raw_keyword_scores = data.get("keyword_scores")
            if not isinstance(raw_keyword_scores, dict):
                raise ScoreValidationError("keyword_scores 必须是对象")

            expected_keywords = set(normalized_keywords)
            returned_keywords = set(raw_keyword_scores)
            missing_keywords = expected_keywords.difference(returned_keywords)
            unexpected_keywords = returned_keywords.difference(expected_keywords)
            if missing_keywords or unexpected_keywords:
                details = []
                if missing_keywords:
                    details.append("缺少关键词: " + ", ".join(sorted(missing_keywords)))
                if unexpected_keywords:
                    details.append("包含未配置关键词: " + ", ".join(sorted(unexpected_keywords)))
                raise ScoreValidationError("keyword_scores 键集合无效（" + "；".join(details) + "）")

            keyword_scores: Dict[str, float] = {}
            for keyword in normalized_keywords:
                score = _finite_number(
                    raw_keyword_scores[keyword], f"关键词 {keyword!r} 的相关度"
                )
                if score < 0 or score > max_score:
                    raise ScoreValidationError(
                        f"关键词 {keyword!r} 的相关度必须在 0-{max_score:g} 之间"
                    )
                keyword_scores[keyword] = score

            reasoning = data.get("reasoning")
            if not isinstance(reasoning, str) or not reasoning.strip():
                raise ScoreValidationError("reasoning 必须是非空字符串")
            reasoning = reasoning.strip()

            tldr = data.get("tldr")
            if not isinstance(tldr, str) or not tldr.strip():
                raise ScoreValidationError("tldr 必须是非空字符串")
            tldr = tldr.strip()

            extracted_keywords = data.get("extracted_keywords", [])
            if not isinstance(extracted_keywords, list) or not all(
                isinstance(keyword, str) and keyword.strip() for keyword in extracted_keywords
            ):
                raise ScoreValidationError("extracted_keywords 必须是非空字符串列表")
            extracted_keywords = [keyword.strip() for keyword in extracted_keywords]

            # Expert-author evidence generated by an LLM is intentionally not
            # authoritative.  Restrict bonuses to the exact intersection of
            # configured names and the source's original author list.  This
            # eliminates both hallucinated and duplicate authors from score
            # calculation while still tolerating an older model that emits the
            # now-ignored field.
            configured_experts = set(author_bonus_by_normalized_name)
            verified_experts: List[str] = []
            seen_expert_names = set()
            if settings.ENABLE_AUTHOR_BONUS:
                for author_name in author_names:
                    normalized_name = _normalized_person_name(author_name)
                    if (
                        normalized_name
                        and normalized_name in configured_experts
                        and normalized_name not in seen_expert_names
                    ):
                        verified_experts.append(author_name)
                        seen_expert_names.add(normalized_name)

            claimed_experts = data.get("expert_authors_found")
            if claimed_experts is not None:
                if not isinstance(claimed_experts, list) or not all(
                    isinstance(name, str) for name in claimed_experts
                ):
                    logger.warning("忽略评分模型返回的无效 expert_authors_found 字段")
                else:
                    claimed_normalized = {
                        _normalized_person_name(name)
                        for name in claimed_experts
                        if _normalized_person_name(name)
                    }
                    verified_normalized = {
                        _normalized_person_name(name) for name in verified_experts
                    }
                    if claimed_normalized != verified_normalized:
                        logger.warning(
                            "评分模型的专家作者声明与确定性校验不一致，已忽略模型声明"
                        )

            # 计算加权总分，既用于 legacy 决策，也保留为审核证据。
            weighted_score = math.fsum(
                keyword_scores[kw] * weight for kw, weight in normalized_keywords.items()
            )

            # Calculate the configured preference once.  In V2 it is applied
            # only after content qualification; legacy retains its original
            # behavior where the same value participates in the pass score.
            matched_author_bonus = 0.0
            if settings.ENABLE_AUTHOR_BONUS and verified_experts:
                matched_author_bonus = math.fsum(
                    author_bonus_by_normalized_name[_normalized_person_name(name)]
                    for name in verified_experts
                )

            # Learned-preference fields default to "not applicable" for the
            # other strategies; only the legacy-family branch fills them in.
            learned_adjustment = None
            learned_keywords_matched: List[str] = []
            learned_authors_matched: List[str] = []

            if strategy_id == CORE_RELEVANCE_V2:
                core_weight = math.fsum(normalized_keywords[kw] for kw in primary_keywords)
                if core_weight <= 0:
                    raise ScoreValidationError("核心关键词总权重必须大于 0")
                relevance_score = math.fsum(
                    keyword_scores[kw] * normalized_keywords[kw] for kw in primary_keywords
                ) / core_weight
                strongest_core_score = max(keyword_scores[kw] for kw in primary_keywords)
                core_match = strongest_core_score >= core_keyword_min_score
                relevance_match = relevance_score >= relevance_threshold
                is_qualified = relevance_match and core_match

                reference_keywords = [
                    keyword for keyword in normalized_keywords if keyword not in primary_keywords
                ]
                reference_weight = math.fsum(
                    normalized_keywords[keyword] for keyword in reference_keywords
                )
                reference_score = (
                    math.fsum(
                        keyword_scores[keyword] * normalized_keywords[keyword]
                        for keyword in reference_keywords
                    )
                    / reference_weight
                    if reference_weight > 0
                    else 0.0
                )
                # Ranking exists for every paper for a useful full-report
                # order, but qualification was frozen above before either
                # reference evidence or author preference is added.
                ranking_score = relevance_score + reference_ranking_weight * reference_score
                author_preference_bonus = matched_author_bonus if is_qualified else 0.0
                ranking_score += author_preference_bonus

                total_score = ranking_score
                # Both fields describe the amount actually applied to the
                # ranking.  A verified expert on an unqualified paper remains
                # visible in ``expert_authors_found``, but receives zero.
                author_bonus = author_preference_bonus
                passing_score = relevance_threshold
                qualification_reason = (
                    f"核心相关度 {relevance_score:.1f}/{max_score:g} "
                    f"（门槛 {relevance_threshold:.1f}）；"
                    f"最高核心词分 {strongest_core_score:.1f}/{max_score:g} "
                    f"（强匹配门槛 {core_keyword_min_score:.1f}）"
                )
                if used_primary_fallback:
                    qualification_reason += "；未配置主要关键词，本次使用全部关键词作为核心集合"
                if not relevance_match:
                    qualification_reason += "；核心平均相关度不足"
                if not core_match:
                    qualification_reason += "；没有核心关键词达到强匹配门槛"
                logger.info(
                    "论文评分完成 [%s]: 核心相关度=%.1f/%.1f，排序分=%.1f，%s",
                    title[:50],
                    relevance_score,
                    relevance_threshold,
                    ranking_score,
                    "✅及格" if is_qualified else "❌未及格",
                )
            else:
                author_bonus = matched_author_bonus
                if strategy_id == LEARNED_PREFERENCE_V1:
                    learned = compute_learned_adjustment(
                        extracted_keywords=extracted_keywords,
                        author_names=author_names,
                        learned_terms=learned_terms or {},
                        configured_keywords=normalized_keywords.keys(),
                        dampening=learned_weight_dampening,
                        term_weight_cap=learned_term_weight_cap,
                    )
                    learned_adjustment = learned["adjustment"]
                    learned_keywords_matched = learned["keywords"]
                    learned_authors_matched = learned["authors"]
                total_score = weighted_score + author_bonus + (learned_adjustment or 0.0)
                passing_score = legacy_passing_score
                is_qualified = total_score >= passing_score
                relevance_score = None
                reference_score = None
                ranking_score = total_score
                author_preference_bonus = author_bonus
                qualification_reason = "旧版加权总分判定"
                if strategy_id == LEARNED_PREFERENCE_V1:
                    qualification_reason = (
                        "旧版加权总分 + 学习偏好修正"
                        f"（学习项 {learned_adjustment or 0.0:+.1f}；"
                        f"关键词 {len(learned_keywords_matched)} 个、"
                        f"作者 {len(learned_authors_matched)} 个匹配，"
                        "学习权重经限幅与衰减）"
                    )
                logger.info(
                    f"论文评分完成 [{title[:50]}]: 总分={total_score:.1f}, 及格分={passing_score:.1f}, {'✅及格' if is_qualified else '❌未及格'}"
                )

            return WeightedScoreResponse(
                total_score=total_score,
                keyword_scores=keyword_scores,
                author_bonus=author_bonus,
                expert_authors_found=verified_experts,
                passing_score=passing_score,
                is_qualified=is_qualified,
                reasoning=reasoning,
                tldr=tldr,
                extracted_keywords=extracted_keywords,
                strategy_id=strategy_id,
                relevance_score=relevance_score,
                qualification_threshold=passing_score,
                core_keyword_min_score=core_keyword_min_score,
                core_keyword_scores={kw: keyword_scores[kw] for kw in primary_keywords}
                if strategy_id == CORE_RELEVANCE_V2
                else {},
                core_keywords_used=primary_keywords if strategy_id == CORE_RELEVANCE_V2 else [],
                reference_score=reference_score,
                author_preference_bonus=author_preference_bonus,
                ranking_score=ranking_score,
                qualification_reason=qualification_reason,
                learned_adjustment=learned_adjustment,
                learned_keywords_matched=learned_keywords_matched,
                learned_authors_matched=learned_authors_matched,
            )

        except Exception as e:
            # The daily pipeline persists this as a retryable stage failure.
            # A provider hiccup must not imply that an entire daily report is
            # unusable, so keep the leaf-level diagnostic at warning level.
            logger.warning(f"论文评分本次失败，将由流水线重试 [{title[:50]}]: {e}")
            # A synthetic zero score hides outages and permanently loses TLDR
            # data. Let the pipeline persist the failure and retry the stage.
            raise RuntimeError(f"论文评分失败 [{title[:50]}]: {e}") from e

    # ======================================================================
    # 摘要翻译
    # ======================================================================

    def generate_tldr(self, title: str, abstract: str) -> str:
        """Generate only a missing one-sentence TL;DR for history repair.

        A persisted relevance score remains authoritative.  This intentionally
        uses the lightweight plain-text route instead of rebuilding the full
        score JSON, so repairing one omitted field does not alter the original
        qualification decision or consume a full scoring call.
        """
        system_prompt = """请为学术论文写一条中文 TL;DR。

要求：
1. 只输出一句完整中文，不要标题、引号、列表或解释；
2. 说明研究问题、方法或主要结果中的关键信息；
3. 不要臆造摘要中未出现的实验结果。
"""

        prompt = f"""论文信息：
论文标题：{title}
论文摘要：{abstract or '（原始摘要缺失，请只根据标题谨慎概括）'}
"""
        try:
            result = self._call_cheap_llm_plain(prompt, system_prompt=system_prompt)
        except Exception as exc:
            logger.warning("TL;DR 补全失败 [%s]: %s", str(title)[:50], exc)
            raise RuntimeError(f"TL;DR 补全失败: {exc}") from exc
        text = str(result or "").strip()
        if not text:
            raise RuntimeError("TL;DR 补全返回空结果")
        # A gateway occasionally emits a short heading on a separate line.
        # Keep a compact one-line report field while preserving meaningful
        # punctuation in Chinese and English scientific names.
        text = " ".join(text.split())
        return text[:1200]

    def translate_abstract(self, abstract: str) -> str:
        """
        将英文摘要翻译为中文。

        参数:
            abstract (str): 英文摘要

        返回:
            str: 中文翻译，失败时返回空字符串
        """
        system_prompt = """请将学术论文摘要翻译为中文。

要求：
1. 保持学术术语的准确性
2. 语句通顺流畅
3. 保留专业名词的英文（可在首次出现时标注）

请直接输出中文翻译，不要添加任何说明或标记。"""

        prompt = f"""英文摘要：
{abstract}
"""

        try:
            translation = self._call_cheap_llm_plain(prompt, system_prompt=system_prompt)
            if not translation or not translation.strip():
                raise RuntimeError("LLM 返回空摘要翻译")
            logger.info(f"摘要翻译完成 [{abstract[:30]}...]")
            return translation.strip()

        except Exception as e:
            logger.warning(f"摘要翻译本次失败，将由流水线重试: {e}")
            raise RuntimeError(f"摘要翻译失败: {e}") from e

    # ======================================================================
    # 深度分析（使用新模板系统）
    # ======================================================================

    def deep_analyze(
        self, title: str, pdf_url: str, abstract: str, fallback_to_abstract: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        对论文进行深度分析（使用新的模板系统）。

        参数:
            title (str): 论文标题
            pdf_url (str): PDF下载URL
            abstract (str): 论文摘要（作为降级方案）
            fallback_to_abstract (bool): PDF下载失败时是否使用摘要

        返回:
            Optional[Dict]: 分析结果字典，失败时返回None
        """
        # 尝试下载并解析PDF。来源标记必须由本地代码写入，不能相信
        # 模型自行声明的 ``content_source``；它决定全文 TL;DR 是否能
        # 出现在日报中。
        pdf_text = self._download_and_parse_pdf(pdf_url)
        content_source = CONTENT_SOURCE_PDF

        if not pdf_text:
            if fallback_to_abstract:
                logger.warning(f"PDF解析失败 [{title[:50]}]，使用摘要作为降级方案")
                pdf_text = abstract
                content_source = CONTENT_SOURCE_ABSTRACT_FALLBACK
            else:
                logger.error(f"PDF解析失败 [{title[:50]}]，且未启用降级方案")
                return None

        # 从新模板获取配置
        modules = self.deep_template.get("modules", [])
        prompts_config = self.deep_template.get("prompts", {})

        # 获取启用的模块
        enabled_modules = [m for m in modules if m.get("enabled", True)]
        if content_source != CONTENT_SOURCE_PDF:
            # Do not ask the model to manufacture a full-text conclusion from
            # an abstract fallback.  The renderer has the same guard for old
            # or provider-injected fields as a defense in depth.
            enabled_modules = [
                module
                for module in enabled_modules
                if module.get("id") != FULL_TEXT_TLDR_FIELD
            ]

        # 构建字段提示词字符串
        field_prompts_lines = []
        output_fields = []

        for module in enabled_modules:
            module_id = module.get("id")
            module_prompt = module.get("prompt", "")

            if module_id == "custom_questions":
                # 处理自定义问题
                questions = module.get("questions", [])
                if questions:
                    field_prompts_lines.append(f"\n自定义问题:")
                    for i, q in enumerate(questions, 1):
                        field_prompts_lines.append(f"{i}. {q}")
                    output_fields.append(
                        f'  "custom_answers": {{"问题1": "回答1", "问题2": "回答2", ...}}'
                    )
            else:
                # 普通模块
                field_prompts_lines.append(f"\n{module_id}: {module_prompt}")
                # The renderer supports list values for both list and inline
                # modules.  Tell the model that explicitly; the former
                # blanket string example made valid list-oriented content
                # needlessly ambiguous and could later disappear on cache
                # hydration after a restart.
                if module.get("format") in {"list", "inline"}:
                    output_fields.append(f'  "{module_id}": ["...", "..."]')
                else:
                    output_fields.append(f'  "{module_id}": "..."')

        fields_str = ",\n".join(output_fields)
        field_prompts_str = "\n".join(field_prompts_lines)

        # Put the reusable template and schema before paper-specific data.
        # Prefix-cache providers can then reuse the instruction prefix across
        # qualified papers without weakening the user-configurable template.
        configured_system_prompt = prompts_config.get(
            "analysis_system", "你是一名学术论文分析专家。"
        )
        analysis_template = prompts_config.get("analysis_template", "")
        research_context = (
            settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"
        )

        # Build the stable instruction prefix. Dynamic placeholders are kept
        # as explicit references to the following user message rather than
        # embedding variable paper text in the cacheable prefix.
        if analysis_template:
            instructions = analysis_template.format(
                title="（见下方论文标题）",
                content="（见下方论文内容）",
                research_context=research_context,
                field_prompts=field_prompts_str,
            )
            # The default template lists field instructions but not an actual
            # JSON shape.  Include one here so list/inline modules cannot be
            # mistaken for the old blanket string contract.
            instructions += f"\n\n输出 JSON 对象字段示例:\n{{\n{fields_str}\n}}"
        else:
            instructions = f"""研究背景:
{research_context}

分析要求:
{field_prompts_str}

输出格式（JSON）:
{{
{fields_str}
}}
"""

        stable_parts = []
        if isinstance(configured_system_prompt, str) and configured_system_prompt.strip():
            stable_parts.append(configured_system_prompt.strip())
        stable_parts.append(instructions.strip())
        stable_parts.append(
            str(prompts_config.get("field_output_format", "使用JSON格式输出。")).strip()
        )
        system_prompt = "\n\n".join(part for part in stable_parts if part)
        prompt = f"""论文标题: {title}

论文内容:
{pdf_text[:15000]}"""

        try:
            content = self._call_smart_llm(prompt, system_prompt=system_prompt)
            content = self._clean_json_string(content)

            try:
                result = json.loads(content)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON解析失败: {json_err}")
                logger.error(f"原始内容（前500字符）: {content[:500]}")
                raise

            result = validate_deep_analysis_payload(result, self.deep_template)
            # Override any provider-supplied metadata after validation. This
            # is a provenance assertion about locally observed PDF parsing,
            # not an LLM claim.
            result[ANALYSIS_META_KEY] = {CONTENT_SOURCE_KEY: content_source}

            logger.info(f"深度分析完成 [{title[:50]}]")
            return result

        except Exception as e:
            logger.error(f"深度分析失败 [{title[:50]}]: {e}")
            return None

    def _download_and_parse_pdf(self, pdf_url: str) -> Optional[str]:
        """
        下载PDF并提取文本内容。

        根据配置选择解析方式:
        - mineru: 优先使用 MinerU 云端 API 解析，失败时自动降级到 PyMuPDF
        - pymupdf: 直接使用 PyMuPDF 本地解析

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        # 根据配置决定解析方式
        if settings.PDF_PARSER_MODE == "mineru":
            # 尝试 MinerU 云端解析
            text = self._parse_pdf_with_mineru(pdf_url)
            if text:
                return text
            # MinerU 失败，降级到 PyMuPDF
            logger.info("降级使用 PyMuPDF 本地解析")

        return self._parse_pdf_with_pymupdf(pdf_url)

    def _parse_pdf_with_mineru(self, pdf_url: str) -> Optional[str]:
        """
        使用 MinerU API 解析 PDF。

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        if not self.mineru_parser.is_available():
            if not self.mineru_parser.is_configured():
                logger.warning("MinerU API 未配置（MINERU_API_KEY 为空），使用 PyMuPDF 本地解析")
            return None

        text = self.mineru_parser.parse_pdf(pdf_url)
        if text:
            logger.info(f"MinerU 解析成功，获取 {len(text)} 字符")
        return text

    def _parse_pdf_with_pymupdf(self, pdf_url: str) -> Optional[str]:
        """
        使用 PyMuPDF 本地解析 PDF。

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        try:
            # 下载PDF（带自动重试）
            pdf_bytes = self._download_pdf_bytes(pdf_url)

            # 保存到临时文件
            settings.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            temp_pdf = (
                settings.DOWNLOAD_DIR
                / f"temp_{hashlib.md5(pdf_url.encode()).hexdigest()[:16]}_{threading.get_ident()}.pdf"
            )
            with open(temp_pdf, "wb") as f:
                f.write(pdf_bytes)

            # 解析PDF（前20页），使用 try/finally 确保资源释放和临时文件清理
            try:
                with fitz.open(temp_pdf) as doc:
                    text = ""
                    for i, page in enumerate(doc):
                        if i >= 20:  # 只读前20页
                            break
                        text += page.get_text()
            finally:
                # 无论解析成功与否均清理临时文件
                if temp_pdf.exists():
                    temp_pdf.unlink()

            logger.info(f"PyMuPDF 解析成功，提取 {len(text)} 字符")
            return text

        except Exception as e:
            logger.error(f"PyMuPDF PDF下载/解析失败: {e}")
            return None
