import fitz  # pymupdf
import logging
import json
import hashlib
from pathlib import Path
from typing import Dict, Set, List
from openai import OpenAI
from config import settings
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

class KeywordAgent:
    """
    关键词提取Agent。

    职责:
    - 从参考PDF中提取带权重的关键词
    - 根据重要性分配不同权重（高/中/低）
    - 使用LLM自动提取专业概念
    - 返回带权重的关键词字典
    - 缓存已提取的关键词，避免重复提取
    - 支持增量更新（只提取新PDF）
    - 智能去重相似关键词
    """
    def __init__(self):
        # 初始化低成本LLM客户端
        self.client = OpenAI(
            api_key=settings.CHEAP_LLM.api_key,
            base_url=settings.CHEAP_LLM.base_url
        )

        # 缓存文件路径
        self.cache_file = settings.DATA_DIR / "keywords_cache.json"

    def _calculate_pdf_hash(self, pdf_path: Path) -> str:
        """
        计算PDF文件的MD5哈希值，用于检测文件是否变化。

        参数:
            pdf_path: PDF文件路径

        返回值:
            str: 文件的MD5哈希值
        """
        try:
            with open(pdf_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"计算PDF哈希失败 {pdf_path}: {e}")
            return ""

    def _load_cache(self) -> Dict:
        """
        加载关键词缓存。

        返回值:
            dict: 缓存数据，格式：
            {
                "pdf_hashes": {"filename": "hash_value"},
                "pdf_keywords": {"filename": {"keyword": weight}},
                "keywords": {"keyword": weight}  # 兼容旧版本
            }
        """
        if not self.cache_file.exists():
            return {"pdf_hashes": {}, "pdf_keywords": {}, "keywords": {}}

        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 兼容旧版本缓存格式
                if "pdf_keywords" not in data:
                    data["pdf_keywords"] = {}
                return data
        except Exception as e:
            logger.warning(f"加载缓存失败: {e}，将重新提取")
            return {"pdf_hashes": {}, "pdf_keywords": {}, "keywords": {}}

    def _save_cache(self, cache_data: Dict):
        """
        保存关键词缓存。

        参数:
            cache_data: 要保存的缓存数据
        """
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            logger.info(f"关键词缓存已保存到 {self.cache_file}")
        except Exception as e:
            logger.error(f"保存缓存失败: {e}")

    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """
        计算两个字符串的相似度（0-1之间）。

        参数:
            str1: 字符串1
            str2: 字符串2

        返回值:
            float: 相似度分数，1.0表示完全相同
        """
        # 转换为小写进行比较
        s1 = str1.lower().strip()
        s2 = str2.lower().strip()

        # 完全相同
        if s1 == s2:
            return 1.0

        # 使用SequenceMatcher计算相似度
        return SequenceMatcher(None, s1, s2).ratio()

    def _deduplicate_keywords(self, keywords_dict: Dict[str, float]) -> Dict[str, float]:
        """
        去除相似的关键词，保留权重更高的那个。

        参数:
            keywords_dict: 原始关键词字典 {关键词: 权重}

        返回值:
            Dict[str, float]: 去重后的关键词字典
        """
        if not keywords_dict:
            return {}

        threshold = settings.SIMILARITY_THRESHOLD
        keywords_list = list(keywords_dict.items())
        # 按权重降序排序，优先保留高权重关键词
        keywords_list.sort(key=lambda x: x[1], reverse=True)

        deduplicated = {}
        removed_count = 0

        for keyword, weight in keywords_list:
            # 检查是否与已保留的关键词相似
            is_duplicate = False
            for existing_keyword in deduplicated.keys():
                similarity = self._calculate_similarity(keyword, existing_keyword)
                if similarity >= threshold:
                    is_duplicate = True
                    logger.debug(f"去重: '{keyword}' 与 '{existing_keyword}' 相似度 {similarity:.2f} >= {threshold}，已跳过")
                    removed_count += 1
                    break

            if not is_duplicate:
                deduplicated[keyword] = weight

        if removed_count > 0:
            logger.info(f"关键词去重: 移除了 {removed_count} 个相似关键词")

        return deduplicated

    def _extract_text_from_pdf(self, pdf_path) -> str:
        """
        从 PDF 的前两页提取文本。

        优化：仅提取前两页以减少文本处理量，
        通过快速提取摘要和引言页面的核心概念。

        参数:
            pdf_path: PDF文件的路径

        返回值:
            str: 提取的文本，失败时返回空串
        """
        try:
            doc = fitz.open(pdf_path)
            text = ""
            # 只提取前两页
            for i, page in enumerate(doc):
                if i >= 2:
                    break
                text += page.get_text()
            doc.close()
            return text
        except Exception as e:
            logger.error(f"PDF无法读取 {pdf_path}: {e}")
            return ""

    def generate_weighted_keywords(self) -> Dict[str, float]:
        """
        从参考PDF中提取带权重的关键词字典（支持缓存、增量更新和PDF删除检测）。

        流程:
        1. 加载缓存，检查哪些PDF是新增的、修改的或删除的
        2. 删除已删除PDF对应的关键词
        3. 只对新增PDF使用LLM提取关键词
        4. 合并所有现存PDF的关键词并去重
        5. 根据重要性分配不同权重
        6. 保存缓存供下次使用

        返回值:
            Dict[str, float]: 关键词-权重字典，例如 {"quantum computing": 0.8, "qubit": 0.5}

        降级策略:
            - 如果未启用reference提取，则返回空字典
            - 如果没有找到参考PDF，则返回空字典
            - 如果LLM调用失败，则返回缓存的关键词
        """
        # 检查是否启用reference关键词提取
        if not settings.ENABLE_REFERENCE_EXTRACTION:
            logger.info("Reference关键词提取未启用，跳过。")
            return {}

        pdf_files = list(settings.REF_PDF_DIR.glob("*.pdf"))

        if not pdf_files:
            logger.warning("未找到参考PDF。无法提取reference关键词。")
            return {}

        logger.info(f"发现 {len(pdf_files)} 个参考PDF。正在检查缓存...")

        # 加载缓存
        cache = self._load_cache()
        cached_hashes = cache.get("pdf_hashes", {})
        cached_pdf_keywords = cache.get("pdf_keywords", {})

        # 当前PDF文件名集合
        current_pdf_names = {pdf.name for pdf in pdf_files}
        cached_pdf_names = set(cached_hashes.keys())

        # 检测删除的PDF
        deleted_pdfs = cached_pdf_names - current_pdf_names
        if deleted_pdfs:
            logger.info(f"检测到 {len(deleted_pdfs)} 个PDF已被删除:")
            for pdf_name in deleted_pdfs:
                removed_keywords = cached_pdf_keywords.get(pdf_name, {})
                logger.info(f"  - {pdf_name} (包含 {len(removed_keywords)} 个关键词)")
                # 从缓存中删除
                cached_hashes.pop(pdf_name, None)
                cached_pdf_keywords.pop(pdf_name, None)

        # 检测新增或修改的PDF
        new_pdfs = []
        current_hashes = {}

        for pdf in pdf_files:
            pdf_hash = self._calculate_pdf_hash(pdf)
            current_hashes[pdf.name] = pdf_hash

            # 如果PDF不在缓存中，或哈希值变化了，则视为新PDF
            if pdf.name not in cached_hashes or cached_hashes[pdf.name] != pdf_hash:
                new_pdfs.append(pdf)

        # 如果没有新增PDF且没有删除PDF，直接返回缓存
        if not new_pdfs and not deleted_pdfs:
            logger.info("所有PDF均已缓存，无需重新处理。")
            # 从所有PDF的关键词合并生成最终关键词
            merged_keywords = {}
            for pdf_name in current_pdf_names:
                pdf_keywords = cached_pdf_keywords.get(pdf_name, {})
                for kw, weight in pdf_keywords.items():
                    if kw in merged_keywords:
                        merged_keywords[kw] = max(merged_keywords[kw], weight)
                    else:
                        merged_keywords[kw] = weight
            logger.info(f"缓存的关键词数量: {len(merged_keywords)} 个")
            return merged_keywords

        if deleted_pdfs:
            logger.info(f"由于PDF删除，需要重新生成关键词集合")

        if new_pdfs:
            logger.info(f"检测到 {len(new_pdfs)} 个新增/修改的PDF，需要提取关键词")

            # 聚合简化的文本上下文（限制总长度以避免令牌溢出）
            context_text = ""
            # 最多分析5个新PDF以节省时间和成本
            for pdf in new_pdfs[:5]:
                context_text += f"---\n论文: {pdf.name}\n{self._extract_text_from_pdf(pdf)[:2000]}\n"
                logger.info(f"  提取文本: {pdf.name}")

            # 构建提示词，要求LLM按重要性分层提取关键词
            prompt = f"""
你是一名研究助理。基于以下学术论文摘录，提取真正核心的技术概念和术语。

论文摘录:
{context_text}

研究背景:
{settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"}

请按重要性分为三个层级提取关键词：
1. 高重要性关键词（最多{settings.REFERENCE_COUNT_HIGH}个）：核心技术、主要算法、关键问题
2. 中重要性关键词（最多{settings.REFERENCE_COUNT_MEDIUM}个）：相关技术、辅助方法、应用领域
3. 低重要性关键词（最多{settings.REFERENCE_COUNT_LOW}个）：周边概念、通用术语、背景知识

**重要原则**：
- 只提取真正核心和重要的关键词，不要为了凑数而提取不重要的内容
- 如果论文在某个层级没有足够核心的内容，宁可少提取或不提取
- 每个层级的关键词数量可以少于最大数量，甚至可以为空
- 关键词质量比数量更重要

要求：
- 关键词应为专业术语或短语
- 关键词应具体且有辨识度，能够代表论文的核心贡献
- 避免过于宽泛的词汇（如"机器学习"、"人工智能"、"优化"等）
- 优先使用英文术语
- 避免提取相似或重复的关键词
- 关键词之间应有明显区别
- 只提取与研究背景高度相关的核心概念

输出格式: 仅输出JSON对象，包含三个数组。如果某个层级没有足够核心的关键词，数组可以为空或包含较少元素。
示例:
{{
  "high_importance": ["quantum error correction", "topological qubits"],
  "medium_importance": ["decoherence"],
  "low_importance": []
}}
"""

            try:
                response = self.client.chat.completions.create(
                    model=settings.CHEAP_LLM.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    response_format={"type": "json_object"}
                )
                content = response.choices[0].message.content

                # 处理可能的非标准JSON输出包装
                if "```json" in content:
                    content = content.replace("```json", "").replace("```", "").strip()

                data = json.loads(content)

                # 构建新提取的关键词字典
                new_keywords = {}

                # 添加高重要性关键词
                high_keywords = data.get("high_importance", [])
                for kw in high_keywords[:settings.REFERENCE_COUNT_HIGH]:
                    new_keywords[kw] = settings.REFERENCE_WEIGHT_HIGH

                # 添加中重要性关键词
                medium_keywords = data.get("medium_importance", [])
                for kw in medium_keywords[:settings.REFERENCE_COUNT_MEDIUM]:
                    new_keywords[kw] = settings.REFERENCE_WEIGHT_MEDIUM

                # 添加低重要性关键词
                low_keywords = data.get("low_importance", [])
                for kw in low_keywords[:settings.REFERENCE_COUNT_LOW]:
                    new_keywords[kw] = settings.REFERENCE_WEIGHT_LOW

                logger.info(f"从新PDF中提取了 {len(new_keywords)} 个新关键词:")
                logger.info(f"  高权重({settings.REFERENCE_WEIGHT_HIGH}): {len(high_keywords[:settings.REFERENCE_COUNT_HIGH])} 个")
                logger.info(f"  中权重({settings.REFERENCE_WEIGHT_MEDIUM}): {len(medium_keywords[:settings.REFERENCE_COUNT_MEDIUM])} 个")
                logger.info(f"  低权重({settings.REFERENCE_WEIGHT_LOW}): {len(low_keywords[:settings.REFERENCE_COUNT_LOW])} 个")

                # 为每个新PDF保存其关键词
                for pdf in new_pdfs:
                    cached_pdf_keywords[pdf.name] = new_keywords.copy()

            except Exception as e:
                logger.error(f"通过LLM提取关键词失败: {e}")
                import traceback
                traceback.print_exc()
                logger.info("继续使用现有缓存的关键词")

        # 从所有现存PDF的关键词合并生成最终关键词
        merged_keywords = {}
        for pdf_name in current_pdf_names:
            pdf_keywords = cached_pdf_keywords.get(pdf_name, {})
            for kw, weight in pdf_keywords.items():
                # 如果关键词已存在，保留更高的权重
                if kw in merged_keywords:
                    merged_keywords[kw] = max(merged_keywords[kw], weight)
                else:
                    merged_keywords[kw] = weight

        logger.info(f"合并后关键词总数: {len(merged_keywords)} 个")

        # 去除相似关键词
        deduplicated_keywords = self._deduplicate_keywords(merged_keywords)
        logger.info(f"去重后关键词总数: {len(deduplicated_keywords)} 个")

        # 保存缓存
        cache_data = {
            "pdf_hashes": current_hashes,
            "pdf_keywords": cached_pdf_keywords,
            "keywords": deduplicated_keywords  # 保留用于向后兼容
        }
        self._save_cache(cache_data)

        return deduplicated_keywords

    def get_all_keywords(self) -> Dict[str, float]:
        """
        获取所有关键词（主要关键词 + reference关键词）的合并字典。

        返回值:
            Dict[str, float]: 合并后的关键词-权重字典
        """
        # 从配置获取主要关键词
        all_keywords = settings.get_merged_keywords()

        # 提取并合并reference关键词
        reference_keywords = self.generate_weighted_keywords()

        # 合并（主要关键词优先，不会被覆盖）
        for kw, weight in reference_keywords.items():
            if kw not in all_keywords:
                all_keywords[kw] = weight

        logger.info(f"关键词总数: {len(all_keywords)} 个")
        logger.info(f"总权重: {sum(all_keywords.values()):.2f}")

        return all_keywords