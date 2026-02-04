import json
import logging
import requests
import fitz  # pymupdf
from typing import Optional, Dict, Any, List
from pathlib import Path
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from config import settings

logger = logging.getLogger(__name__)

# ======================================================================
# Pydantic数据模型：用于验证和结构化LLM输出
# ======================================================================

class WeightedScoreResponse(BaseModel):
    """
    加权评分响应模型（新策略）。

    属性:
        total_score (float): 总分（关键词加权分 + 作者附加分）
        keyword_scores (Dict[str, float]): 每个关键词的相关度评分（0-10）
        author_bonus (float): 作者附加分
        expert_authors_found (List[str]): 发现的专家作者列表
        passing_score (float): 动态及格分
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

class Stage2Response(BaseModel):
    """
    深度分析响应模型（可配置字段）。

    属性根据 settings.ENABLED_ANALYSIS_FIELDS 动态使用。
    """
    chinese_title: Optional[str] = None
    summary: Optional[str] = None
    innovations: Optional[List[str]] = None
    methodology: Optional[str] = None
    key_results: Optional[str] = None
    tech_stack: Optional[List[str]] = None
    strengths: Optional[List[str]] = None
    limitations: Optional[List[str]] = None
    relevance_to_keywords: Optional[str] = None
    future_work: Optional[str] = None
    custom_answers: Optional[Dict[str, str]] = None

class AnalysisAgent:
    """
    论文分析Agent（新策略：加权评分系统）。

    职责:
    - 基于关键词权重对论文进行加权评分
    - 检测专家作者并给予附加分
    - 计算动态及格分并判断是否合格
    - 对及格论文进行深度分析（使用可配置模板）
    """
    def __init__(self):
        # 初始化两个不同性能LLM客户端
        self.cheap_client = OpenAI(
            api_key=settings.CHEAP_LLM.api_key,
            base_url=settings.CHEAP_LLM.base_url
        )
        self.smart_client = OpenAI(
            api_key=settings.SMART_LLM.api_key,
            base_url=settings.SMART_LLM.base_url
        )

        # 加载报告模板以获取prompt配置
        self.basic_template = settings.load_report_template("basic_report_template.json")
        self.deep_template = settings.load_report_template("deep_analysis_template.json")

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
                if content[i] == '\\':
                    if i + 1 < len(content):
                        next_char = content[i + 1]
                        # 合法的转义字符
                        if next_char in ['"', '\\', '/', 'b', 'f', 'n', 'r', 't']:
                            result += content[i:i+2]
                            i += 2
                        # Unicode转义
                        elif next_char == 'u' and i + 5 < len(content):
                            result += content[i:i+6]
                            i += 6
                        # 非法转义，转义反斜杠本身
                        else:
                            result += '\\\\'
                            i += 1
                    else:
                        result += '\\\\'
                        i += 1
                else:
                    result += content[i]
                    i += 1
            return f'"{result}"'

        # 匹配JSON字符串值（简化版，不处理嵌套）
        json_str = re.sub(r'"((?:[^"\\]|\\.)*)\"', fix_escapes_in_match, json_str)

        return json_str

    # ======================================================================
    # 新策略：加权评分系统
    # ======================================================================

    def score_paper_with_keywords(
        self,
        title: str,
        authors: str,
        abstract: str,
        keywords_dict: Dict[str, float]
    ) -> WeightedScoreResponse:
        """
        使用加权关键词系统对论文进行评分。

        评分公式:
        总分 = Σ(关键词相关度 × 关键词权重) + 作者附加分

        参数:
            title (str): 论文标题
            authors (str): 作者列表（逗号分隔）
            abstract (str): 论文摘要
            keywords_dict (Dict[str, float]): 关键词-权重字典

        返回:
            WeightedScoreResponse: 包含详细评分信息的响应对象
        """
        # 计算总权重和及格分
        total_weight = sum(keywords_dict.values())
        passing_score = settings.calculate_passing_score(total_weight)

        # 构建关键词列表字符串
        keywords_list = "\n".join([
            f"  - {kw} (权重: {weight:.1f})"
            for kw, weight in keywords_dict.items()
        ])

        # 构建专家作者列表字符串
        expert_authors_str = ", ".join(settings.EXPERT_AUTHORS) if settings.EXPERT_AUTHORS else "无"

        prompt = f"""你是一名学术论文评审专家。请基于以下关键词对论文进行相关性评分，并提取论文信息。

研究背景:
{settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"}

评分关键词及权重:
{keywords_list}

论文信息:
标题: {title}
作者: {authors}
摘要: {abstract}

评分任务:
1. 理解论文的研究内容和主题
2. 对每个关键词评估相关度（0-10分）:
   - 0分: 完全无关
   - 5分: 有一定关联
   - 10分: 高度相关，核心内容
3. 计算加权总分: Σ(关键词相关度 × 关键词权重)
4. 检查作者列表是否包含以下专家: {expert_authors_str}
   - 如果包含，每位专家加 {settings.AUTHOR_BONUS_POINTS} 分
5. 用一句话总结论文研究的问题和结果（TLDR）
6. 从标题和摘要中提取5-8个核心关键词（英文）

评分标准:
- 关键词总权重: {total_weight:.1f}
- 动态及格分: {passing_score:.1f}
- 每个关键词最高相关度: {settings.MAX_SCORE_PER_KEYWORD} 分

输出格式: JSON对象，包含以下字段:
{{
  "keyword_scores": {{"关键词1": 8.0, "关键词2": 5.0, ...}},
  "expert_authors_found": ["Author1", "Author2"],
  "reasoning": "详细的评分理由和分析",
  "tldr": "一句话总结论文研究的核心问题和主要结果",
  "extracted_keywords": ["keyword1", "keyword2", "keyword3", ...]
}}

要求:
- keyword_scores 必须包含所有给定的关键词
- 每个关键词的评分范围: 0-{settings.MAX_SCORE_PER_KEYWORD}
- reasoning 应简明扼要地说明论文与关键词的相关性
- tldr 应该是一句完整的话，包含研究问题和主要结果
- extracted_keywords 应提取5-8个最能代表论文内容的关键词或短语
"""

        try:
            response = self.cheap_client.chat.completions.create(
                model=settings.CHEAP_LLM.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=settings.CHEAP_LLM.temperature,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            content = self._clean_json_string(content)

            try:
                data = json.loads(content)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON解析失败: {json_err}")
                logger.error(f"原始内容（前500字符）: {content[:500]}")
                raise

            # 解析评分结果
            keyword_scores = data.get("keyword_scores", {})
            expert_authors_found = data.get("expert_authors_found", [])
            reasoning = data.get("reasoning", "无详细理由")
            tldr = data.get("tldr", "无摘要")
            extracted_keywords = data.get("extracted_keywords", [])

            # 计算加权总分
            weighted_score = sum(
                keyword_scores.get(kw, 0) * weight
                for kw, weight in keywords_dict.items()
            )

            # 计算作者附加分
            author_bonus = 0.0
            if settings.ENABLE_AUTHOR_BONUS and expert_authors_found:
                author_bonus = len(expert_authors_found) * settings.AUTHOR_BONUS_POINTS

            # 计算总分
            total_score = weighted_score + author_bonus

            # 判断是否及格
            is_qualified = total_score >= passing_score

            logger.info(f"论文评分完成: 总分={total_score:.1f}, 及格分={passing_score:.1f}, {'✅及格' if is_qualified else '❌未及格'}")

            return WeightedScoreResponse(
                total_score=total_score,
                keyword_scores=keyword_scores,
                author_bonus=author_bonus,
                expert_authors_found=expert_authors_found,
                passing_score=passing_score,
                is_qualified=is_qualified,
                reasoning=reasoning,
                tldr=tldr,
                extracted_keywords=extracted_keywords
            )

        except Exception as e:
            logger.error(f"论文评分失败: {e}")
            import traceback
            traceback.print_exc()

            # 返回默认低分
            return WeightedScoreResponse(
                total_score=0.0,
                keyword_scores={kw: 0.0 for kw in keywords_dict.keys()},
                author_bonus=0.0,
                expert_authors_found=[],
                passing_score=passing_score,
                is_qualified=False,
                reasoning=f"评分失败: {str(e)}",
                tldr="评分失败，无法生成摘要",
                extracted_keywords=[]
            )

    # ======================================================================
    # 摘要翻译
    # ======================================================================

    def translate_abstract(self, abstract: str) -> str:
        """
        将英文摘要翻译为中文。

        参数:
            abstract (str): 英文摘要

        返回:
            str: 中文翻译，失败时返回空字符串
        """
        prompt = f"""请将以下学术论文摘要翻译为中文。要求：
1. 保持学术术语的准确性
2. 语句通顺流畅
3. 保留专业名词的英文（可在首次出现时标注）

英文摘要：
{abstract}

请直接输出中文翻译，不要添加任何说明或标记。"""

        try:
            response = self.cheap_client.chat.completions.create(
                model=settings.CHEAP_LLM.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            translation = response.choices[0].message.content.strip()
            logger.info("摘要翻译完成")
            return translation

        except Exception as e:
            logger.error(f"摘要翻译失败: {e}")
            return ""

    # ======================================================================
    # 深度分析（使用新模板系统）
    # ======================================================================

    def deep_analyze(
        self,
        title: str,
        pdf_url: str,
        abstract: str,
        fallback_to_abstract: bool = True
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
        # 尝试下载并解析PDF
        pdf_text = self._download_and_parse_pdf(pdf_url)

        if not pdf_text:
            if fallback_to_abstract:
                logger.warning("PDF解析失败，使用摘要作为降级方案")
                pdf_text = abstract
            else:
                logger.error("PDF解析失败，且未启用降级方案")
                return None

        # 从新模板获取配置
        modules = self.deep_template.get('modules', [])
        prompts_config = self.deep_template.get('prompts', {})

        # 获取启用的模块
        enabled_modules = [m for m in modules if m.get('enabled', True)]

        # 构建字段提示词字符串
        field_prompts_lines = []
        output_fields = []

        for module in enabled_modules:
            module_id = module.get('id')
            module_prompt = module.get('prompt', '')

            if module_id == 'custom_questions':
                # 处理自定义问题
                questions = module.get('questions', [])
                if questions:
                    field_prompts_lines.append(f"\n自定义问题:")
                    for i, q in enumerate(questions, 1):
                        field_prompts_lines.append(f"{i}. {q}")
                    output_fields.append(f"  \"custom_answers\": {{\"问题1\": \"回答1\", \"问题2\": \"回答2\", ...}}")
            else:
                # 普通模块
                field_prompts_lines.append(f"\n{module_id}: {module_prompt}")
                output_fields.append(f"  \"{module_id}\": \"...\"")

        fields_str = ",\n".join(output_fields)
        field_prompts_str = "\n".join(field_prompts_lines)

        # 使用模板中的系统提示词和用户提示词模板
        system_prompt = prompts_config.get('analysis_system', '你是一名学术论文分析专家。')
        analysis_template = prompts_config.get('analysis_template', '')

        # 构建最终prompt
        if analysis_template:
            # 使用模板中的格式
            prompt = analysis_template.format(
                title=title,
                content=pdf_text[:15000],
                research_context=settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究",
                field_prompts=field_prompts_str
            )
        else:
            # 备用格式
            prompt = f"""论文标题: {title}

论文内容:
{pdf_text[:15000]}

研究背景:
{settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"}

分析要求:
{field_prompts_str}

输出格式（JSON）:
{{
{fields_str}
}}
"""

        # 添加输出格式说明
        prompt += f"\n\n{prompts_config.get('field_output_format', '使用JSON格式输出。')}"

        try:
            response = self.smart_client.chat.completions.create(
                model=settings.SMART_LLM.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=settings.SMART_LLM.temperature,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            content = self._clean_json_string(content)

            try:
                result = json.loads(content)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON解析失败: {json_err}")
                logger.error(f"原始内容（前500字符）: {content[:500]}")
                raise

            logger.info("深度分析完成")
            return result

        except Exception as e:
            logger.error(f"深度分析失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _download_and_parse_pdf(self, pdf_url: str) -> Optional[str]:
        """
        下载PDF并提取文本内容。

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        try:
            # 下载PDF
            headers = {
                "User-Agent": "ArxivDailyResearcher/2.0 (https://github.com/yzr278892/arxiv-daily-researcher; yzr278892@gmail.com)"
            }
            response = requests.get(pdf_url, headers=headers, timeout=30)
            response.raise_for_status()

            # 保存到临时文件
            temp_pdf = settings.DOWNLOAD_DIR / f"temp_{hash(pdf_url)}.pdf"
            with open(temp_pdf, 'wb') as f:
                f.write(response.content)

            # 解析PDF（前20页）
            doc = fitz.open(temp_pdf)
            text = ""
            for i, page in enumerate(doc):
                if i >= 20:  # 只读前20页
                    break
                text += page.get_text()
            doc.close()

            # 清理临时文件
            temp_pdf.unlink()

            logger.info(f"PDF解析成功，提取 {len(text)} 字符")
            return text

        except Exception as e:
            logger.error(f"PDF下载/解析失败: {e}")
            return None