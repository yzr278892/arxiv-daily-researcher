"""
各模块渲染器实现

包含基本报告和深度分析报告的所有模块渲染器。
"""

from typing import List, Dict, Any, Optional
from .base_module import BaseModuleRenderer, FormatHelper
from .trend_renderer import TrendRenderer


class MetadataRenderer(BaseModuleRenderer):
    """论文元数据渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        """
        渲染论文元数据。

        参数:
            data: 论文数据，包含 paper_metadata 或直接字段
            config: 模块配置

        返回:
            List[str]: 渲染后的Markdown行列表
        """
        if not self.is_enabled(config):
            return []

        lines = []
        paper_meta = data.get('paper_metadata')
        fields_config = config.get('fields', {})
        fmt = self.get_format(config)

        # 提取各字段值（使用有序字典保持顺序）
        field_values = {}

        # 标题
        if fields_config.get('title', {}).get('enabled', True):
            title = paper_meta.title if paper_meta else data.get('title', 'Unknown')
            field_values['title'] = title

            # 标题特殊处理：可以作为标题显示
            if fields_config.get('title', {}).get('as_heading', False):
                level = fields_config.get('title', {}).get('heading_level', 3)
                lines.extend(self.format_helper.format_as_heading(title, level))

        # 作者
        if fields_config.get('authors', {}).get('enabled', True):
            authors = paper_meta.get_authors_string() if paper_meta else data.get('authors', 'Unknown')
            field_values['authors'] = authors

        # 期刊/来源
        if fields_config.get('journal', {}).get('enabled', True):
            journal = data.get('source', '')
            if paper_meta and hasattr(paper_meta, 'source'):
                journal = paper_meta.source
            field_values['journal'] = journal

        # 发布日期
        if fields_config.get('published_date', {}).get('enabled', True):
            if paper_meta and paper_meta.published_date:
                published = paper_meta.published_date.strftime('%Y-%m-%d')
            else:
                published = data.get('published', 'N/A')
            field_values['published_date'] = published

        # 判断数据源类型
        source = data.get('source', '')
        if paper_meta and hasattr(paper_meta, 'source'):
            source = paper_meta.source
        is_arxiv_source = (source == 'arxiv')

        # 链接处理逻辑：
        # - arxiv来源：显示 "arXiv链接"
        # - 期刊来源：必须显示DOI，如果有arxiv链接则额外显示在DOI上方
        if fields_config.get('url', {}).get('enabled', True):
            if is_arxiv_source:
                # ArXiv 来源：直接显示 arXiv 链接
                if paper_meta and paper_meta.url:
                    url = paper_meta.url
                    field_values['arxiv_url'] = f"[{url}]({url})"
                else:
                    url = data.get('url', '#')
                    field_values['arxiv_url'] = f"[{url}]({url})"
            else:
                # 期刊来源：如果有arXiv链接，显示在DOI上方
                if paper_meta and paper_meta.arxiv_url:
                    url = paper_meta.arxiv_url
                    field_values['arxiv_url'] = f"[{url}]({url})"

                # 期刊来源：必须显示DOI
                doi = paper_meta.doi if paper_meta and hasattr(paper_meta, 'doi') else data.get('doi', '')
                if doi:
                    # 清理 DOI 格式
                    clean_doi = doi.replace('https://doi.org/', '').replace('DOI:', '').strip()
                    doi_url = f"https://doi.org/{clean_doi}"
                    field_values['doi_link'] = f"[{clean_doi}]({doi_url})"

        # 根据格式输出
        if fmt == "table":
            rows = []
            headers = ["字段", "值"]
            for field_id, value in field_values.items():
                if field_id == 'title' and fields_config.get('title', {}).get('as_heading', False):
                    continue  # 标题已经单独处理
                # 确定标签
                if field_id == 'arxiv_url':
                    label = "arXiv链接"
                elif field_id == 'doi_link':
                    label = "DOI"
                else:
                    label = fields_config.get(field_id, {}).get('label', field_id)
                rows.append((label, value))
            if rows:
                lines.extend(self.format_helper.format_as_table(rows, headers))
        elif fmt == "list":
            for field_id, value in field_values.items():
                if field_id == 'title' and fields_config.get('title', {}).get('as_heading', False):
                    continue
                # 确定标签
                if field_id == 'arxiv_url':
                    label = "arXiv链接"
                elif field_id == 'doi_link':
                    label = "DOI"
                else:
                    label = fields_config.get(field_id, {}).get('label', field_id)
                lines.append(f"**{label}**: {value}")
            lines.append("")
        else:  # inline
            parts = []
            for field_id, value in field_values.items():
                if field_id == 'title' and fields_config.get('title', {}).get('as_heading', False):
                    continue
                if field_id == 'arxiv_url':
                    label = "arXiv链接"
                elif field_id == 'doi_link':
                    label = "DOI"
                else:
                    label = fields_config.get(field_id, {}).get('label', field_id)
                parts.append(f"{label}: {value}")
            lines.append(" | ".join(parts))
            lines.append("")

        return lines


class AbstractOriginalRenderer(BaseModuleRenderer):
    """原文摘要渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        if not self.is_enabled(config):
            return []

        paper_meta = data.get('paper_metadata')
        abstract = paper_meta.abstract if paper_meta else data.get('abstract', '')

        if not abstract:
            return []

        label = self.get_label(config)
        lines = []

        # 添加标签（如果不是admonition格式且不是collapsible）
        if label and self.get_format(config) not in ["admonition"] and not self.should_collapsible(config):
            lines.append(f"**{label}**:")
            lines.append("")

        # 应用格式
        content_lines = self.apply_format(abstract, config)
        lines.extend(content_lines)

        return lines


class AbstractCnRenderer(BaseModuleRenderer):
    """摘要中文翻译渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        if not self.is_enabled(config):
            return []

        abstract_cn = data.get('abstract_cn', '')

        if not abstract_cn:
            return []

        label = self.get_label(config)
        lines = []

        # 添加标签（如果不是admonition格式且不是collapsible）
        if label and self.get_format(config) not in ["admonition"] and not self.should_collapsible(config):
            lines.append(f"**{label}**:")
            lines.append("")

        content_lines = self.apply_format(abstract_cn, config)
        lines.extend(content_lines)

        return lines


class TldrSemanticScholarRenderer(BaseModuleRenderer):
    """Semantic Scholar TLDR渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        if not self.is_enabled(config):
            return []

        # 从paper_metadata获取semantic_scholar_tldr
        paper_meta = data.get('paper_metadata')
        tldr = None

        if paper_meta and hasattr(paper_meta, 'semantic_scholar_tldr'):
            tldr = paper_meta.semantic_scholar_tldr
        elif 'semantic_scholar_tldr' in data:
            tldr = data.get('semantic_scholar_tldr')

        if not tldr:
            return []

        label = self.get_label(config)
        lines = []

        if self.get_format(config) == "inline":
            lines.append(f"**{label}**: {tldr}")
            lines.append("")
        else:
            content_lines = self.apply_format(tldr, config)
            lines.extend(content_lines)

        return lines


class TldrAiRenderer(BaseModuleRenderer):
    """AI生成TLDR渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        if not self.is_enabled(config):
            return []

        # 从score_response获取tldr
        score_resp = data.get('score_response')
        tldr = None

        if score_resp and hasattr(score_resp, 'tldr'):
            tldr = score_resp.tldr
        elif 'tldr' in data:
            tldr = data.get('tldr')

        if not tldr:
            return []

        # 获取模型名称作为标签
        from config import settings
        model_name = settings.CHEAP_LLM.model_name
        label = f"{model_name} TL;DR"

        lines = []

        if self.get_format(config) == "inline":
            lines.append(f"**{label}**: {tldr}")
            lines.append("")
        else:
            # 使用动态标签替换config中的label
            modified_config = config.copy()
            modified_config['label'] = label
            content_lines = self.apply_format(tldr, modified_config)
            lines.extend(content_lines)

        return lines


class ScoringRenderer(BaseModuleRenderer):
    """评分结果渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        if not self.is_enabled(config):
            return []

        score_resp = data.get('score_response')
        if not score_resp:
            return []

        keywords_dict = data.get('keywords_dict', {})
        label = self.get_label(config)
        fmt = self.get_format(config)
        show_details = config.get('show_details', True)
        show_reasoning = config.get('show_reasoning', True)

        lines = []
        status_icon = "✅" if score_resp.is_qualified else "❌"

        # 总分行
        lines.append(f"**评分**: {score_resp.total_score:.1f} / {score_resp.passing_score:.1f} {status_icon}")
        lines.append("")

        if show_details:
            detail_lines = []

            if fmt == "table":
                # 表格格式
                rows = []
                for kw, score in score_resp.keyword_scores.items():
                    weight = keywords_dict.get(kw, 0)
                    weighted = score * weight
                    rows.append((kw, f"{weight:.1f}", f"{score:.1f}/10", f"{weighted:.1f}"))

                if score_resp.author_bonus > 0:
                    experts = ", ".join(score_resp.expert_authors_found)
                    rows.append(("作者加分", "-", f"+{score_resp.author_bonus:.1f}", f"专家: {experts}"))

                if rows:
                    detail_lines.extend(self.format_helper.format_as_table(
                        rows,
                        ["关键词", "权重", "相关度", "得分"]
                    ))
            else:
                # 列表格式
                for kw, score in score_resp.keyword_scores.items():
                    weight = keywords_dict.get(kw, 0)
                    weighted = score * weight
                    detail_lines.append(f"- **{kw}** (权重{weight:.1f}): {score:.1f}/10 → {weighted:.1f}")

                if score_resp.author_bonus > 0:
                    experts = ", ".join(score_resp.expert_authors_found)
                    detail_lines.append(f"- **作者加分**: +{score_resp.author_bonus:.1f}（专家: {experts}）")

                detail_lines.append("")

            if show_reasoning and score_resp.reasoning:
                detail_lines.append(f"**评分理由**: {score_resp.reasoning}")
                detail_lines.append("")

            # 应用折叠
            if self.should_collapsible(config):
                default_open = self.get_collapsible_default_open(config)
                lines.extend(self.format_helper.wrap_collapsible(detail_lines, label, default_open))
            else:
                lines.extend(detail_lines)

        return lines


class ExtractedKeywordsRenderer(BaseModuleRenderer):
    """提取的关键词渲染器"""

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        if not self.is_enabled(config):
            return []

        score_resp = data.get('score_response')
        keywords = []

        if score_resp and hasattr(score_resp, 'extracted_keywords'):
            keywords = score_resp.extracted_keywords
        elif 'extracted_keywords' in data:
            keywords = data.get('extracted_keywords', [])

        if not keywords:
            return []

        label = self.get_label(config)
        fmt = self.get_format(config)
        lines = []

        if fmt == "inline":
            lines.append(f"**{label}**: {', '.join(keywords)}")
            lines.append("")
        else:
            lines.append(f"**{label}**:")
            content_lines = self.apply_format(keywords, config)
            lines.extend(content_lines)

        return lines


class DeepAnalysisRenderer(BaseModuleRenderer):
    """
    深度分析渲染器

    根据deep_analysis_template.json中的模块配置渲染深度分析内容。
    """

    def __init__(self, format_helper: FormatHelper, deep_template: Dict[str, Any]):
        """
        初始化深度分析渲染器。

        参数:
            format_helper: 格式化工具
            deep_template: 深度分析模板配置
        """
        super().__init__(format_helper)
        self.deep_template = deep_template

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        """
        渲染深度分析内容。

        参数:
            data: 包含 analysis 字段的数据
            config: 渲染配置（可选覆盖模板配置）

        返回:
            List[str]: 渲染后的Markdown行列表
        """
        analysis = data.get('analysis', {})
        if not analysis:
            return []

        lines = []
        section_title = self.deep_template.get('layout', {}).get('section_title', '深度分析')
        lines.append(f"**{section_title}**:")
        lines.append("")

        # 获取模块配置并按order排序
        modules = self.deep_template.get('modules', [])
        enabled_modules = sorted(
            [m for m in modules if m.get('enabled', True)],
            key=lambda x: x.get('order', 999)
        )

        for module in enabled_modules:
            module_id = module.get('id')
            module_lines = self._render_module(module_id, analysis, module)
            lines.extend(module_lines)

        return lines

    def _render_module(
        self,
        module_id: str,
        analysis: Dict[str, Any],
        config: Dict[str, Any]
    ) -> List[str]:
        """
        渲染单个深度分析模块。

        参数:
            module_id: 模块ID
            analysis: 分析数据
            config: 模块配置

        返回:
            List[str]: 渲染后的行列表
        """
        content = analysis.get(module_id)
        if not content:
            return []

        label = config.get('label', config.get('name', module_id))
        fmt = config.get('format', 'plain')
        is_collapsible = config.get('collapsible', False)
        lines = []

        # 根据格式渲染（如果是collapsible，内容不加标签，避免重复）
        if fmt == "heading":
            level = config.get('heading_level', 4)
            lines.extend(self.format_helper.format_as_heading(str(content), level))
        elif fmt == "quote":
            if isinstance(content, str):
                if not is_collapsible:
                    lines.append(f"**{label}**:")
                    lines.append("")
                lines.extend(self.format_helper.format_as_quote(content))
            else:
                if is_collapsible:
                    lines.append(str(content))
                else:
                    lines.append(f"**{label}**: {content}")
                lines.append("")
        elif fmt == "admonition":
            # 如果是collapsible，不传label给admonition
            admonition_type = config.get('admonition_type', 'note')
            admonition_label = "" if is_collapsible else label
            if isinstance(content, str):
                lines.extend(self.format_helper.format_as_admonition(content, admonition_label, admonition_type))
            elif isinstance(content, list):
                lines.extend(self.format_helper.format_as_admonition("\n".join(f"- {item}" for item in content), admonition_label, admonition_type))
        elif fmt == "list":
            if isinstance(content, list):
                if not is_collapsible:
                    lines.append(f"**{label}**:")
                list_style = config.get('list_style', 'bullet')
                lines.extend(self.format_helper.format_as_list(content, list_style))
            else:
                if is_collapsible:
                    lines.append(str(content))
                else:
                    lines.append(f"**{label}**: {content}")
                lines.append("")
        elif fmt == "inline":
            if isinstance(content, list):
                if is_collapsible:
                    lines.append(', '.join(str(c) for c in content))
                else:
                    lines.append(f"**{label}**: {', '.join(str(c) for c in content)}")
            else:
                if is_collapsible:
                    lines.append(str(content))
                else:
                    lines.append(f"**{label}**: {content}")
            lines.append("")
        elif fmt == "qa":
            # 问答格式（用于custom_questions）
            if isinstance(content, dict):
                if not is_collapsible:
                    lines.append(f"**{label}**:")
                for q, a in content.items():
                    lines.append(f"- **{q}**: {a}")
                lines.append("")
        else:  # plain
            lines.append(f"**{label}**: {content}")
            lines.append("")

        # 应用折叠
        if config.get('collapsible', False):
            default_open = config.get('collapsible_default_open', False)
            lines = self.format_helper.wrap_collapsible(lines, label, default_open)

        return lines


class ModuleRendererFactory:
    """
    模块渲染器工厂

    根据模块ID创建对应的渲染器实例。
    """

    def __init__(self, format_helper: FormatHelper, deep_template: Optional[Dict[str, Any]] = None):
        """
        初始化工厂。

        参数:
            format_helper: 格式化工具
            deep_template: 深度分析模板（可选）
        """
        self.format_helper = format_helper
        self.deep_template = deep_template or {}

        # 注册基本报告渲染器
        self._renderers = {
            "metadata": MetadataRenderer(format_helper),
            "abstract_original": AbstractOriginalRenderer(format_helper),
            "abstract_cn": AbstractCnRenderer(format_helper),
            "tldr_semantic_scholar": TldrSemanticScholarRenderer(format_helper),
            "tldr_ai": TldrAiRenderer(format_helper),
            "scoring": ScoringRenderer(format_helper),
            "extracted_keywords": ExtractedKeywordsRenderer(format_helper),
            "deep_analysis": DeepAnalysisRenderer(format_helper, self.deep_template),
            "keyword_trends": TrendRenderer(format_helper),
        }

    def get_renderer(self, module_id: str) -> Optional[BaseModuleRenderer]:
        """
        获取模块渲染器。

        参数:
            module_id: 模块ID

        返回:
            BaseModuleRenderer: 渲染器实例，不存在则返回None
        """
        return self._renderers.get(module_id)

    def render_modules(
        self,
        data: Dict[str, Any],
        modules: List[Dict[str, Any]]
    ) -> List[str]:
        """
        按配置渲染所有模块。

        参数:
            data: 论文数据
            modules: 模块配置列表

        返回:
            List[str]: 渲染后的所有行
        """
        lines = []

        # 按order排序已启用的模块
        enabled_modules = sorted(
            [m for m in modules if m.get('enabled', True)],
            key=lambda x: x.get('order', 999)
        )

        for module in enabled_modules:
            module_id = module.get('id')
            renderer = self.get_renderer(module_id)
            if renderer:
                module_lines = renderer.render(data, module)
                lines.extend(module_lines)

        return lines
