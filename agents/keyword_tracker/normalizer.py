"""
AI 关键词标准化模块

使用 LLM 进行关键词合并和标准化。
"""

import json
import logging
from typing import List, Dict, Optional
from pydantic import BaseModel
from openai import OpenAI

logger = logging.getLogger(__name__)


class NormalizationResult(BaseModel):
    """标准化结果"""
    canonical_form: str
    original_keywords: List[str]
    category: Optional[str] = None
    confidence: float = 1.0


class KeywordNormalizer:
    """
    AI 关键词标准化器

    使用 cheap_llm 进行：
    - 同义词合并
    - 缩写展开
    - 拼写变体统一
    """

    def __init__(self):
        """初始化，使用 settings 中的 cheap_llm 配置"""
        from config import settings
        self.client = OpenAI(
            api_key=settings.CHEAP_LLM.api_key,
            base_url=settings.CHEAP_LLM.base_url
        )
        self.model = settings.CHEAP_LLM.model_name

    def normalize_batch(
        self,
        keywords: List[str],
        existing_canonical: Optional[List[str]] = None,
        batch_size: int = 50
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
            batch = keywords[i:i + batch_size]
            try:
                results = self._normalize_single_batch(batch, existing_canonical)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"标准化批次失败: {e}")
                # 失败时，每个关键词作为独立的标准形式
                for kw in batch:
                    all_results.append(NormalizationResult(
                        canonical_form=kw,
                        original_keywords=[kw],
                        confidence=0.5
                    ))

        return all_results

    def _normalize_single_batch(
        self,
        keywords: List[str],
        existing_canonical: Optional[List[str]] = None
    ) -> List[NormalizationResult]:
        """处理单个批次"""
        prompt = self._build_prompt(keywords, existing_canonical)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是学术关键词标准化专家。请严格按照JSON格式输出。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content

            # 🆕 优化：检查content是否为空
            if not content or not content.strip():
                logger.warning(f"LLM返回空内容，关键词: {keywords[:3]}...")
                raise ValueError("LLM返回空内容")

            # 🆕 优化：添加详细日志用于调试
            logger.debug(f"LLM返回内容前100字符: {content[:100]}")

            try:
                data = json.loads(content)
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
                results.append(NormalizationResult(
                    canonical_form=norm.get("canonical_form", "").lower(),
                    original_keywords=[kw.lower() for kw in norm.get("original_keywords", [])],
                    category=norm.get("category"),
                    confidence=norm.get("confidence", 0.9)
                ))

            return results

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            raise
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    def _build_prompt(
        self,
        keywords: List[str],
        existing_canonical: Optional[List[str]] = None
    ) -> str:
        """构建标准化提示"""
        existing_str = ""
        if existing_canonical:
            existing_str = f"""
已知的规范关键词列表（优先映射到这些）：
{json.dumps(existing_canonical[:50], ensure_ascii=False, indent=2)}
"""

        return f"""请对以下学术关键词进行标准化处理。

任务：
1. 识别同义词、缩写、拼写变体，将它们合并为规范形式
2. 选择最规范、最常用的形式作为 canonical_form
3. 如果可以归类，提供 category（如：quantum, machine_learning, optimization, neural_network 等）
4. 给出归并的置信度（0.5-1.0）
{existing_str}
待处理关键词：
{json.dumps(keywords, ensure_ascii=False, indent=2)}

输出 JSON 格式：
{{
  "normalizations": [
    {{
      "canonical_form": "quantum computing",
      "original_keywords": ["QC", "quantum computation", "quantum computing"],
      "category": "quantum",
      "confidence": 0.95
    }}
  ]
}}

要求：
- 每个原始关键词必须且只能出现在一个组中
- 保持学术术语的准确性
- 英文关键词统一用小写（专有名词除外）
- 如果某个关键词无法归类，单独作为一组"""
