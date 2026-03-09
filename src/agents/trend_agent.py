"""
研究趋势分析 LLM Agent

职责:
- 为单篇论文生成 LLM TLDR（使用 CHEAP_LLM）
- 对所有论文进行趋势分析（使用 SMART_LLM + Skills 系统）
"""

import json
import json5
import logging
from datetime import date
from typing import List, Dict, Any, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from config import settings

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
    wait=wait_exponential(min=settings.RETRY_MIN_WAIT, max=settings.RETRY_MAX_WAIT),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _llm_call_with_retry(client, model_name: str, temperature: float, prompt: str) -> str:
    """带自动重试的 LLM 调用（模块级，避免每次调用重建 retry 装饰器）。"""
    resp = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    if settings.TOKEN_TRACKING_ENABLED and resp.usage:
        from utils.token_counter import token_counter
        token_counter.add(model_name, resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return resp.choices[0].message.content.strip()


class TrendAgent:
    """
    研究趋势分析 LLM Agent。

    使用 CHEAP_LLM 生成每篇论文的 TLDR，
    使用 SMART_LLM + Skills 系统进行整体趋势分析。
    """

    def __init__(self):
        self.cheap_client = OpenAI(
            api_key=settings.CHEAP_LLM.api_key,
            base_url=settings.CHEAP_LLM.base_url,
        )
        self.smart_client = OpenAI(
            api_key=settings.SMART_LLM.api_key,
            base_url=settings.SMART_LLM.base_url,
        )
        self.skills = self._load_skills()

    def _load_skills(self) -> Dict[str, Any]:
        """加载趋势分析技能库"""
        skills_path = settings.REPORT_TEMPLATES_DIR / "trend_skills.json"
        if not skills_path.exists():
            logger.warning(f"趋势分析技能文件不存在: {skills_path}")
            return {"skills": []}
        try:
            with open(skills_path, "r", encoding="utf-8") as f:
                return json5.load(f)
        except Exception as e:
            logger.error(f"加载趋势分析技能失败: {e}")
            return {"skills": []}

    def _call_cheap_llm_plain(self, prompt: str) -> str:
        """调用低成本LLM（纯文本模式），带自动重试。"""
        return _llm_call_with_retry(
            self.cheap_client, settings.CHEAP_LLM.model_name, settings.CHEAP_LLM.temperature, prompt
        )

    def _call_smart_llm_plain(self, prompt: str) -> str:
        """调用高性能LLM（纯文本模式），带自动重试。"""
        return _llm_call_with_retry(
            self.smart_client, settings.SMART_LLM.model_name, settings.SMART_LLM.temperature, prompt
        )

    # ======================================================================
    # TLDR 生成
    # ======================================================================

    def generate_tldr(self, paper) -> str:
        """
        为单篇论文生成 LLM TLDR。

        使用 CHEAP_LLM 生成，输入论文全量元数据（标题、作者、日期、类别、摘要）。

        参数:
            paper: PaperMetadata 实例

        返回:
            str: 生成的 TLDR 文本，失败返回空字符串
        """
        authors_str = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors_str += f" 等 {len(paper.authors)} 位作者"

        categories_str = ", ".join(paper.categories) if paper.categories else "未分类"
        pub_date = paper.published_date.strftime("%Y-%m-%d") if paper.published_date else "未知"

        prompt = f"""你是一位学术论文分析助手。请为以下 arXiv 论文生成一段简洁的中文摘要（TLDR）。

要求：
- 用 2-3 句话概括论文的核心贡献
- 突出技术创新点或实验结果
- 语言简洁、技术准确
- 直接输出 TLDR 内容，不要加任何前缀或标记

论文信息：
标题：{paper.title}
作者：{authors_str}
发表时间：{pub_date}
类别：{categories_str}
原始摘要：{paper.abstract}"""

        try:
            tldr = self._call_cheap_llm_plain(prompt)
            logger.debug(f"TLDR 生成成功: {paper.title[:40]}...")
            return tldr
        except Exception as e:
            logger.error(f"TLDR 生成失败 ({paper.title[:40]}...): {e}")
            return ""

    # ======================================================================
    # 趋势分析
    # ======================================================================

    def analyze_trends(
        self,
        keywords: List[str],
        papers: list,
        date_from: date,
        date_to: date,
        tldrs: Dict[str, str],
    ) -> Dict[str, str]:
        """
        对所有论文进行趋势分析。

        使用 SMART_LLM + Skills 系统，传入完整论文元数据（标题、作者、日期、类别、摘要、TLDR）。
        当论文数量超过 LLM 上下文限制时，按时间分批处理。

        参数:
            keywords: 搜索关键词
            papers: PaperMetadata 列表
            date_from: 搜索起始日期
            date_to: 搜索截至日期
            tldrs: {paper_id: tldr} 字典

        返回:
            Dict[str, str]: {skill_name: 分析结果 Markdown}
        """
        # 获取启用的技能
        enabled_skill_ids = settings.RESEARCH_ENABLED_SKILLS
        all_skills = {s["name"]: s for s in self.skills.get("skills", [])}
        active_skills = [all_skills[sid] for sid in enabled_skill_ids if sid in all_skills]

        if not active_skills:
            logger.warning("没有可用的趋势分析技能")
            return {}

        # 序列化论文元数据（完整信息，不仅仅是标题+TLDR）
        papers_data = self._serialize_papers(papers, tldrs)

        # 检查论文数据量，决定是否分批
        papers_json = json.dumps(papers_data, ensure_ascii=False)
        # 粗略估计 token 数（1 英文词 ≈ 1.3 token，4 字符 ≈ 1 token）
        estimated_tokens = len(papers_json) // 4

        results = {}

        if estimated_tokens > 80000 or len(papers_data) > 100:
            # 分批处理：按时间段切分
            logger.info(f"论文数据量较大 (~{estimated_tokens} tokens)，启用分批趋势分析")
            results = self._analyze_trends_batched(
                keywords, papers, tldrs, date_from, date_to, active_skills
            )
        else:
            # 单次处理
            for skill in active_skills:
                logger.info(f"  执行趋势分析技能: {skill['label']}")
                result = self._run_single_skill(
                    skill, keywords, papers_data, date_from, date_to, len(papers)
                )
                if result:
                    results[skill["name"]] = result

        return results

    def _serialize_papers(self, papers: list, tldrs: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        序列化论文元数据为 LLM 输入格式。

        包含完整信息：ID、标题、作者、日期、类别、摘要、TLDR。
        """
        papers_data = []
        for paper in papers:
            pub_date = paper.published_date.strftime("%Y-%m-%d") if paper.published_date else "未知"
            papers_data.append(
                {
                    "id": paper.paper_id,
                    "title": paper.title,
                    "authors": paper.authors[:5],
                    "date": pub_date,
                    "categories": paper.categories,
                    "abstract": paper.abstract[:500],  # 截断过长摘要
                    "tldr": tldrs.get(paper.paper_id, ""),
                }
            )
        return papers_data

    def _run_single_skill(
        self,
        skill: Dict[str, Any],
        keywords: List[str],
        papers_data: List[Dict[str, Any]],
        date_from: date,
        date_to: date,
        total_count: int,
    ) -> Optional[str]:
        """执行单个趋势分析技能"""
        papers_json = json.dumps(papers_data, ensure_ascii=False, separators=(",", ":"))

        prompt = f"""你是一位资深学术研究分析专家。以下是关键词 "{', '.join(keywords)}" 在 {date_from} ~ {date_to} 期间的 {total_count} 篇 arXiv 论文数据。

请根据以下技能要求进行分析：

### 技能：{skill['label']}

{skill['instruction']}

### 论文数据（JSON 格式）

```json
{papers_json}
```

重要提示：
- 所有分析必须基于提供的论文数据，不要引用数据集以外的论文
- 提及具体论文时请使用 arXiv ID 和标题
- 使用 Markdown 格式输出
- 分析应有数据支撑，避免空泛论述
- 直接输出分析结果，不要重复技能要求"""

        try:
            result = self._call_smart_llm_plain(prompt)
            return result
        except Exception as e:
            logger.error(f"趋势分析技能 '{skill['name']}' 执行失败: {e}")
            return None

    def _analyze_trends_batched(
        self,
        keywords: List[str],
        papers: list,
        tldrs: Dict[str, str],
        date_from: date,
        date_to: date,
        active_skills: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """
        分批进行趋势分析。

        将论文按时间均分为 N 批，每批独立分析，最后合并。
        """
        # 按时间排序的论文分成批次（每批最多 150 篇）
        batch_size = 150
        batches = []
        for i in range(0, len(papers), batch_size):
            batches.append(papers[i : i + batch_size])

        logger.info(f"  分成 {len(batches)} 批进行分析")

        # 每个技能独立处理
        results = {}
        for skill in active_skills:
            logger.info(f"  执行趋势分析技能: {skill['label']} ({len(batches)} 批)")
            batch_results = []

            for batch_idx, batch in enumerate(batches):
                batch_data = self._serialize_papers(batch, tldrs)
                # 确定批次时间范围
                batch_dates = [p.published_date for p in batch if p.published_date]
                if batch_dates:
                    b_from = min(batch_dates).strftime("%Y-%m-%d")
                    b_to = max(batch_dates).strftime("%Y-%m-%d")
                else:
                    b_from = str(date_from)
                    b_to = str(date_to)

                logger.info(
                    f"    批次 {batch_idx + 1}/{len(batches)}: {len(batch)} 篇 ({b_from} ~ {b_to})"
                )

                result = self._run_single_skill(
                    skill,
                    keywords,
                    batch_data,
                    date_from=date.fromisoformat(b_from),
                    date_to=date.fromisoformat(b_to),
                    total_count=len(batch),
                )
                if result:
                    batch_results.append(result)

            # 合并批次结果
            if len(batch_results) == 1:
                results[skill["name"]] = batch_results[0]
            elif len(batch_results) > 1:
                merged = self._merge_batch_results(
                    skill, keywords, batch_results, date_from, date_to, len(papers)
                )
                results[skill["name"]] = merged

        return results

    def _merge_batch_results(
        self,
        skill: Dict[str, Any],
        keywords: List[str],
        batch_results: List[str],
        date_from: date,
        date_to: date,
        total_count: int,
    ) -> str:
        """合并多批次分析结果"""
        batches_text = ""
        for i, result in enumerate(batch_results, 1):
            batches_text += f"\n### 批次 {i} 分析结果\n\n{result}\n"

        prompt = f"""你是一位资深学术研究分析专家。以下是对关键词 "{', '.join(keywords)}" 在 {date_from} ~ {date_to} 期间共 {total_count} 篇论文的分批分析结果。

由于论文数量较多，之前按时间段分批进行了分析。现在请你将以下各批次的分析结果合并为一份完整、连贯的分析报告。

### 技能：{skill['label']}

{skill['instruction']}

### 各批次分析结果
{batches_text}

要求：
- 整合各批次的发现，消除重复内容
- 保持全局视角，跨批次进行综合分析
- 明确指出跨时间段的趋势变化
- 使用 Markdown 格式输出
- 直接输出合并后的分析结果"""

        try:
            return self._call_smart_llm_plain(prompt)
        except Exception as e:
            logger.error(f"合并分析结果失败: {e}")
            # 降级：简单拼接
            return "\n\n---\n\n".join(batch_results)
