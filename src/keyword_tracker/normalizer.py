"""
AI 关键词标准化模块

使用 LLM 进行关键词合并和标准化。
"""

import json
import re
import logging
from typing import List, Dict, Optional
from pydantic import BaseModel

from config import settings
from utils.llm_request_pool import call_chat_completion
from utils.llm_health import LLMHealthRecorder
from utils.llm_usage import record_token_usage as record_llm_token_usage

logger = logging.getLogger(__name__)


class NormalizationResult(BaseModel):
    """标准化结果"""

    canonical_form: str
    original_keywords: List[str]
    category: Optional[str] = None
    confidence: float = 1.0


def _extract_json(text: str) -> str:
    """从 LLM 响应中提取 JSON 内容，去除 markdown 代码块包裹"""
    text = text.strip()
    # 匹配 ```json ... ``` 或 ``` ... ``` 包裹的内容
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


class KeywordNormalizer:
    """
    AI 关键词标准化器

    使用 cheap_llm 进行：
    - 同义词合并
    - 缩写展开
    - 拼写变体统一
    """

    def __init__(self, health_recorder: Optional[LLMHealthRecorder] = None):
        """Initialize using the configured low-cost or high-capability LLM."""
        from config import settings
        from utils.llm_resilience import build_llm_client

        # 与全部 LLM 客户端共享超时/重试边界（config.json 的 llm 段）。
        role = str(getattr(settings, "KEYWORD_NORMALIZATION_LLM_ROLE", "cheap") or "cheap").strip().lower()
        self.role = role if role in {"cheap", "smart"} else "cheap"
        llm_config = settings.SMART_LLM if self.role == "smart" else settings.CHEAP_LLM
        self.client = build_llm_client(
            llm_config.api_key,
            llm_config.base_url,
        )
        self.model = llm_config.model_name
        self._health_recorder = health_recorder

    def _record_llm_health(
        self, success: bool, error: Optional[BaseException] = None
    ) -> None:
        recorder = getattr(self, "_health_recorder", None)
        if recorder is not None:
            recorder(self.role, self.model, success, error)

    def set_health_recorder(self, health_recorder: Optional[LLMHealthRecorder]) -> None:
        """Attach optional passive observability after construction."""
        self._health_recorder = health_recorder

    def normalize_batch(
        self,
        keywords: List[str],
        existing_canonical: Optional[List[str]] = None,
        batch_size: int = 25,
    ) -> List[NormalizationResult]:
        """
        批量标准化关键词

        Args:
            keywords: 待标准化的关键词列表
            existing_canonical: 已有的标准关键词（优先映射）
            batch_size: 每批处理数量

        Returns:
            NormalizationResult 列表
        """
        if not keywords:
            return []

        all_results = []

        # 分批处理
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i : i + batch_size]
            try:
                results = self._normalize_single_batch(batch, existing_canonical)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"标准化批次失败: {e}")
                # 失败时，每个关键词作为独立的标准形式
                for kw in batch:
                    all_results.append(
                        NormalizationResult(
                            canonical_form=kw, original_keywords=[kw], confidence=0.5
                        )
                    )

        return all_results

    def _normalize_single_batch(
        self, keywords: List[str], existing_canonical: Optional[List[str]] = None
    ) -> List[NormalizationResult]:
        """处理单个批次"""
        system_prompt, prompt = self._build_prompt_parts(keywords, existing_canonical)

        try:
            response = call_chat_completion(
                self.client,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            usage = getattr(response, "usage", None)
            if settings.TOKEN_TRACKING_ENABLED and usage:
                record_llm_token_usage(
                    self.model,
                    usage,
                    len(system_prompt + prompt) // 4,
                )
            content = response.choices[0].message.content

            # 🆕 优化：检查content是否为空
            if not content or not content.strip():
                logger.warning(f"LLM返回空内容，关键词: {keywords[:3]}...")
                raise ValueError("LLM返回空内容")

            # 🆕 优化：添加详细日志用于调试
            logger.debug(f"LLM返回内容前100字符: {content[:100]}")

            try:
                json_str = _extract_json(content)
                data = json.loads(json_str)
            except json.JSONDecodeError as e:
                # 🆕 优化：记录导致错误的原始内容
                logger.error(f"JSON 解析失败: {e}")
                logger.error(f"原始内容: {content[:500]}")
                raise

            results = []
            normalizations = data.get("normalizations", [])

            # 🆕 优化：检查返回数据格式
            if not normalizations:
                logger.warning(f"LLM返回空的normalizations列表")
                # 返回空结果，让上层处理
                raise ValueError("返回的normalizations为空")

            for norm in normalizations:
                results.append(
                    NormalizationResult(
                        canonical_form=norm.get("canonical_form", "").lower(),
                        original_keywords=[kw.lower() for kw in norm.get("original_keywords", [])],
                        category=norm.get("category"),
                        confidence=norm.get("confidence", 0.9),
                    )
                )

            self._record_llm_health(True)
            return results

        except Exception as e:
            self._record_llm_health(False, e)
            logger.error(f"LLM 调用失败: {e}")
            raise

    def _build_prompt_parts(
        self, keywords: List[str], existing_canonical: Optional[List[str]] = None
    ) -> tuple[str, str]:
        """Build a stable instruction prefix and one changing data message."""
        existing_str = ""
        if existing_canonical:
            existing_str = f"""
已知的规范关键词列表（优先映射到这些）：
{json.dumps(existing_canonical[:50], ensure_ascii=False, indent=2)}
"""

        system_prompt = """你是学术关键词标准化专家。请严格按照 JSON 格式输出。

请对用户提供的学术关键词进行标准化处理。

任务：
1. 识别同义词、缩写、拼写变体，将它们合并为规范形式
2. 选择最规范、最常用的形式作为 canonical_form
3. 如果可以归类，提供 category（如：quantum, machine_learning, optimization, neural_network 等）
4. 给出归并的置信度（0.5-1.0）

输出 JSON 格式：
{
  "normalizations": [
    {
      "canonical_form": "quantum computing",
      "original_keywords": ["QC", "quantum computation", "quantum computing"],
      "category": "quantum",
      "confidence": 0.95
    }
  ]
}

要求：
- 每个原始关键词必须且只能出现在一个组中
- 保持学术术语的准确性
- 英文关键词统一用小写（专有名词除外）
- 如果某个关键词无法归类，单独作为一组"""
        prompt = f"""{existing_str}
待处理关键词：
{json.dumps(keywords, ensure_ascii=False, indent=2)}"""
        return system_prompt, prompt

    def _build_prompt(
        self, keywords: List[str], existing_canonical: Optional[List[str]] = None
    ) -> str:
        """Return the user-data segment for callers using the legacy helper."""
        return self._build_prompt_parts(keywords, existing_canonical)[1]
