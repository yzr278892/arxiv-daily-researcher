"""
研究趋势分析 LLM Agent

职责:
- 为单篇论文生成 LLM TLDR（使用 CHEAP_LLM）
- 对所有论文进行趋势分析（使用 SMART_LLM + Skills 系统）
"""

import json
import json5
import logging
import time
from datetime import date
from typing import List, Dict, Any, Optional

from config import settings
from utils.llm_request_pool import call_chat_completion
from utils.llm_resilience import build_llm_client, llm_retry
from utils.llm_health import LLMHealthRecorder
from utils.llm_usage import record_token_usage as record_llm_token_usage

logger = logging.getLogger(__name__)


@llm_retry()
def _llm_call_once(
    client,
    model_name: str,
    temperature: float,
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
) -> str:
    """带自动重试的 LLM 调用（模块级，避免每次调用重建 retry 装饰器）。"""
    estimated_prompt_tokens = len((system_prompt or "") + prompt) // 4
    messages = []
    if isinstance(system_prompt, str) and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = call_chat_completion(
            client,
            model=model_name,
            messages=messages,
            temperature=temperature,
        )
    except Exception:
        # 请求已发出但未收到响应（超时/网络错误），API 已消耗输入 token
        if settings.TOKEN_TRACKING_ENABLED:
            from utils.token_counter import token_counter

            token_counter.add(model_name, estimated_prompt_tokens, 0)
        raise
    usage = getattr(resp, "usage", None)
    if settings.TOKEN_TRACKING_ENABLED and usage:
        record_llm_token_usage(model_name, usage, estimated_prompt_tokens)
    return resp.choices[0].message.content.strip()


def _llm_call_with_retry(
    client,
    model_name: str,
    temperature: float,
    prompt: str,
    *,
    role: str,
    health_recorder: Optional[LLMHealthRecorder] = None,
    system_prompt: Optional[str] = None,
) -> str:
    """Retry a call, then record only its final user-visible outcome."""
    try:
        result = _llm_call_once(
            client,
            model_name,
            temperature,
            prompt,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        if health_recorder is not None:
            health_recorder(role, model_name, False, exc)
        raise
    if health_recorder is not None:
        health_recorder(role, model_name, True, None)
    return result


class TrendAgent:
    """
    研究趋势分析 LLM Agent。

    使用 CHEAP_LLM 生成每篇论文的 TLDR，
    使用 SMART_LLM + Skills 系统进行整体趋势分析。
    """

    def __init__(self, health_recorder: Optional[LLMHealthRecorder] = None):
        self.cheap_client = build_llm_client(
            settings.CHEAP_LLM.api_key,
            settings.CHEAP_LLM.base_url,
        )
        self.smart_client = build_llm_client(
            settings.SMART_LLM.api_key,
            settings.SMART_LLM.base_url,
        )
        self.skills = self._load_skills()
        self._health_recorder = health_recorder

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

    def set_health_recorder(self, health_recorder: Optional[LLMHealthRecorder]) -> None:
        """Attach optional passive observability after agent construction."""
        self._health_recorder = health_recorder

    def _call_cheap_llm_plain(
        self, prompt: str, *, system_prompt: Optional[str] = None
    ) -> str:
        """调用低成本LLM（纯文本模式），带自动重试。"""
        return _llm_call_with_retry(
            self.cheap_client,
            settings.CHEAP_LLM.model_name,
            settings.CHEAP_LLM.temperature,
            prompt,
            role="cheap",
            health_recorder=getattr(self, "_health_recorder", None),
            system_prompt=system_prompt,
        )

    def _call_smart_llm_plain(
        self, prompt: str, *, system_prompt: Optional[str] = None
    ) -> str:
        """调用高性能LLM（纯文本模式），带自动重试。"""
        return _llm_call_with_retry(
            self.smart_client,
            settings.SMART_LLM.model_name,
            settings.SMART_LLM.temperature,
            prompt,
            role="smart",
            health_recorder=getattr(self, "_health_recorder", None),
            system_prompt=system_prompt,
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

        system_prompt = """你是一位学术论文分析助手。请为用户提供的 arXiv 论文生成一段简洁的中文摘要（TLDR）。

要求：
- 用 2-3 句话概括论文的核心贡献
- 突出技术创新点或实验结果
- 语言简洁、技术准确
- 直接输出 TLDR 内容，不要加任何前缀或标记
"""

        prompt = f"""论文信息：
标题：{paper.title}
作者：{authors_str}
发表时间：{pub_date}
类别：{categories_str}
原始摘要：{paper.abstract}"""

        try:
            tldr = self._call_cheap_llm_plain(prompt, system_prompt=system_prompt)
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
        # 综合分析是趋势研究的固定阶段。保留 ``RESEARCH_ENABLED_SKILLS``
        # 仅用于读取旧配置，不能让旧实例保存的空列表跳过核心结果。
        enabled_skill_ids = ["comprehensive_analysis"]
        all_skills = {s["name"]: s for s in self.skills.get("skills", [])}
        active_skills = [all_skills[sid] for sid in enabled_skill_ids if sid in all_skills]

        # 自定义深度分析提示词：非空时替换综合分析的内置指令
        custom_prompt = (getattr(settings, "RESEARCH_ANALYSIS_PROMPT", "") or "").strip()
        if custom_prompt:
            active_skills = [
                {**skill, "instruction": custom_prompt}
                if skill["name"] == "comprehensive_analysis"
                else skill
                for skill in active_skills
            ]

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

        system_prompt = f"""你是一位资深学术研究分析专家。请根据用户提供的论文数据完成趋势分析。

请根据以下技能要求进行分析：

### 技能：{skill['label']}

{skill['instruction']}

重要提示：
- 所有分析必须基于提供的论文数据，不要引用数据集以外的论文
- 提及具体论文时请使用 arXiv ID 和标题
- 使用 Markdown 格式输出
- 分析应有数据支撑，避免空泛论述
- 直接输出分析结果，不要重复技能要求"""
        prompt = f"""任务范围：
关键词：{', '.join(keywords)}
日期：{date_from} ~ {date_to}
论文数：{total_count}

### 论文数据（JSON 格式）

```json
{papers_json}
```"""

        try:
            logger.info(f"  正在调用 {settings.SMART_LLM.model_name} 进行「{skill['label']}」，请稍候...")
            _t0 = time.time()
            result = self._call_smart_llm_plain(prompt, system_prompt=system_prompt)
            elapsed = int(time.time() - _t0)
            logger.info(f"  分析完成，用时 {elapsed} 秒")
            return result
        except Exception as e:
            elapsed = int(time.time() - _t0) if "_t0" in dir() else 0
            logger.error(f"趋势分析技能 '{skill['name']}' 执行失败（用时 {elapsed} 秒）: {e}")
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

        system_prompt = f"""你是一位资深学术研究分析专家。请将用户提供的分批分析结果合并为一份完整、连贯的分析报告。

### 技能：{skill['label']}

{skill['instruction']}

要求：
- 整合各批次的发现，消除重复内容
- 保持全局视角，跨批次进行综合分析
- 明确指出跨时间段的趋势变化
- 使用 Markdown 格式输出
- 直接输出合并后的分析结果"""
        prompt = f"""任务范围：
关键词：{', '.join(keywords)}
日期：{date_from} ~ {date_to}
论文总数：{total_count}

### 各批次分析结果
{batches_text}"""

        try:
            return self._call_smart_llm_plain(prompt, system_prompt=system_prompt)
        except Exception as e:
            logger.error(f"合并分析结果失败: {e}")
            # 降级：简单拼接
            return "\n\n---\n\n".join(batch_results)
