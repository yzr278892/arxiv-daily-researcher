"""
研究报告生成Agent（模块化版本）

支持通过JSON模板配置报告的结构和格式：
- 基本报告：每篇论文的元数据、摘要、TLDR、评分等
- 深度分析报告：及格论文的详细分析

模块化设计：
- 每个信息块作为独立模块
- 可配置模块的启用/禁用、顺序、格式、折叠等
- 支持自定义提示词
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from config import settings
from .report_modules.base_module import FormatHelper
from .report_modules.renderers import ModuleRendererFactory

logger = logging.getLogger(__name__)

# 数据源显示名称映射
SOURCE_DISPLAY_NAMES = {
    "arxiv": "ArXiv",
    "prl": "Physical Review Letters",
    "pra": "Physical Review A",
    "prb": "Physical Review B",
    "prc": "Physical Review C",
    "prd": "Physical Review D",
    "pre": "Physical Review E",
    "prx": "Physical Review X",
    "prxq": "PRX Quantum",
    "rmp": "Reviews of Modern Physics",
    "nature": "Nature",
    "nature_physics": "Nature Physics",
    "nature_communications": "Nature Communications",
    "science": "Science",
    "science_advances": "Science Advances",
    "npj_quantum_information": "npj Quantum Information",
    "quantum": "Quantum",
    "new_journal_of_physics": "New Journal of Physics",
}


class Reporter:
    """
    研究报告生成Agent（模块化版本）。

    职责:
    - 加载报告模板配置
    - 按数据源分别生成报告
    - 使用模块化渲染器生成各部分内容
    - 支持自定义格式和布局
    """

    def __init__(self):
        self.report_base_dir = settings.REPORTS_DIR

        # 加载模板
        self.basic_template = settings.load_report_template("basic_report_template.json")
        self.deep_template = settings.load_report_template("deep_analysis_template.json")

        # 初始化格式化工具和渲染器工厂
        admonition_style = self.basic_template.get('global', {}).get('admonition_style', 'mkdocs')
        self.format_helper = FormatHelper(admonition_style)
        self.renderer_factory = ModuleRendererFactory(self.format_helper, self.deep_template)

    def get_source_display_name(self, source: str) -> str:
        """获取数据源的显示名称"""
        return SOURCE_DISPLAY_NAMES.get(source, source.upper())

    def generate_reports_by_source(
        self,
        scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
        keywords_dict: Dict[str, float],
        analyses_by_source: Dict[str, List[Dict[str, Any]]] = None
    ) -> Dict[str, Path]:
        """
        按数据源生成分开的报告。

        参数:
            scored_papers_by_source: {数据源: 论文列表}
            keywords_dict: 关键词-权重字典
            analyses_by_source: {数据源: 深度分析列表}（可选）

        返回:
            Dict[str, Path]: {数据源: 报告文件路径}
        """
        if analyses_by_source is None:
            analyses_by_source = {}

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        report_paths = {}

        for source, papers in scored_papers_by_source.items():
            if not papers:
                continue

            # 创建数据源子目录
            if settings.REPORTS_BY_SOURCE:
                source_dir = self.report_base_dir / source
            else:
                source_dir = self.report_base_dir

            source_dir.mkdir(parents=True, exist_ok=True)

            # 生成报告文件名
            display_name = self.get_source_display_name(source)
            filename = f"{source.upper()}_Report_{timestamp}.md"
            filepath = source_dir / filename

            # 获取该数据源的深度分析（如果有）
            analyses = analyses_by_source.get(source, [])
            # 如果该数据源有深度分析结果，则显示深度分析
            has_deep_analysis = len(analyses) > 0

            # 生成报告
            self._generate_single_source_report(
                filepath=filepath,
                source=source,
                display_name=display_name,
                papers=papers,
                keywords_dict=keywords_dict,
                analyses=analyses,
                has_deep_analysis=has_deep_analysis
            )

            report_paths[source] = filepath
            logger.info(f"[{source}] 报告已生成: {filepath}")

        return report_paths

    def _generate_single_source_report(
        self,
        filepath: Path,
        source: str,
        display_name: str,
        papers: List[Dict[str, Any]],
        keywords_dict: Dict[str, float],
        analyses: List[Dict[str, Any]],
        has_deep_analysis: bool
    ):
        """生成单个数据源的报告"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")

        # 计算统计信息
        total_papers = len(papers)
        qualified_count = sum(1 for p in papers if p['score_response'].is_qualified)
        analyzed_count = len(analyses)

        total_weight = sum(keywords_dict.values())
        passing_score = settings.calculate_passing_score(total_weight)

        # 按总分排序
        sorted_papers = sorted(
            papers,
            key=lambda x: x['score_response'].total_score,
            reverse=True
        )

        # 获取布局配置
        layout = self.basic_template.get('layout', {})

        # 开始生成报告
        lines = []

        # 报告标题
        title_template = layout.get('report_title_template', '📊 {source_name} 研究报告 ({date})')
        report_title = title_template.format(source_name=display_name, date=today)
        lines.append(f"# {report_title}")
        lines.append("")
        lines.append(f"> 生成时间: {timestamp}")
        lines.append(f"> 数据源: {display_name}")
        lines.append("")

        # 数据源说明
        if source != "arxiv":
            lines.append("> ⚠️ **注意**: 该数据源不支持PDF下载，仅提供评分和摘要翻译，无深度分析")
            lines.append("")

        # ========== 配置信息 ==========
        if layout.get('show_config_section', True):
            lines.extend(self._generate_config_section(keywords_dict, passing_score))

        # ========== 统计汇总 ==========
        if layout.get('show_stats_section', True):
            lines.extend(self._generate_stats_section(
                total_papers, qualified_count, analyzed_count, has_deep_analysis
            ))

        # ========== 及格论文详细信息 ==========
        if layout.get('show_qualified_section', True) and qualified_count > 0:
            section_title = layout.get('qualified_section_title', '⭐ 及格论文详细分析')
            lines.append(f"## {section_title}")
            lines.append("")

            qualified_papers = [p for p in sorted_papers if p['score_response'].is_qualified]

            for idx, paper in enumerate(qualified_papers, 1):
                paper_lines = self._render_paper_section(
                    paper, keywords_dict, analyses, idx, is_qualified_section=True
                )
                lines.extend(paper_lines)

        # ========== 所有论文详细信息 ==========
        if layout.get('show_all_papers_section', True):
            section_title = layout.get('all_papers_section_title', '📋 所有论文列表')
            lines.append(f"## {section_title}")
            lines.append("")

            qualified_icon = layout.get('qualified_icon', '✅')
            unqualified_icon = layout.get('unqualified_icon', '❌')

            for idx, paper in enumerate(sorted_papers, 1):
                paper_lines = self._render_paper_section(
                    paper, keywords_dict, [], idx,
                    is_qualified_section=False,
                    qualified_icon=qualified_icon,
                    unqualified_icon=unqualified_icon
                )
                lines.extend(paper_lines)

        # 写入文件
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            logger.info(f"  - 总论文数: {total_papers}")
            logger.info(f"  - 及格论文: {qualified_count}")
            if has_deep_analysis:
                logger.info(f"  - 深度分析: {analyzed_count}")
        except Exception as e:
            logger.error(f"报告生成失败: {e}")
            import traceback
            traceback.print_exc()

    def _generate_config_section(
        self,
        keywords_dict: Dict[str, float],
        passing_score: float
    ) -> List[str]:
        """生成配置信息部分"""
        lines = []
        total_weight = sum(keywords_dict.values())

        lines.append("## 📌 配置信息")
        lines.append("")

        # 关键词列表
        lines.append(f"### 关键词列表（共 {len(keywords_dict)} 个，总权重 {total_weight:.1f}）")
        lines.append("")
        lines.append("| 关键词 | 权重 | 类型 |")
        lines.append("|--------|------|------|")
        for kw, weight in sorted(keywords_dict.items(), key=lambda x: x[1], reverse=True):
            kw_type = "主要" if weight >= 1.0 else "次要"
            lines.append(f"| {kw} | {weight:.1f} | {kw_type} |")
        lines.append("")

        # 评分设置
        lines.append("### 评分设置")
        lines.append("")
        lines.append(f"- **每个关键词最大分**: {settings.MAX_SCORE_PER_KEYWORD}")
        lines.append(f"- **及格分公式**: {settings.PASSING_SCORE_BASE} + {settings.PASSING_SCORE_WEIGHT_COEFFICIENT} × 总权重")
        lines.append(f"- **当前及格分**: {passing_score:.1f}")
        if settings.ENABLE_AUTHOR_BONUS:
            lines.append(f"- **作者加分**: 启用（{settings.AUTHOR_BONUS_POINTS}分/专家）")
            if settings.EXPERT_AUTHORS:
                lines.append(f"- **专家作者**: {', '.join(settings.EXPERT_AUTHORS)}")
        lines.append("")

        return lines

    def _generate_stats_section(
        self,
        total_papers: int,
        qualified_count: int,
        analyzed_count: int,
        has_deep_analysis: bool
    ) -> List[str]:
        """生成统计汇总部分"""
        lines = []
        lines.append("## 📈 论文统计")
        lines.append("")
        lines.append(f"- **总抓取**: {total_papers} 篇")
        if total_papers > 0:
            lines.append(f"- **及格论文**: {qualified_count} 篇 ({qualified_count / total_papers * 100:.1f}%)")
        else:
            lines.append(f"- **及格论文**: {qualified_count} 篇")
        if has_deep_analysis:
            lines.append(f"- **深度分析**: {analyzed_count} 篇")
        lines.append("")
        lines.append("---")
        lines.append("")
        return lines

    def _render_paper_section(
        self,
        paper: Dict[str, Any],
        keywords_dict: Dict[str, float],
        analyses: List[Dict[str, Any]],
        idx: int,
        is_qualified_section: bool = False,
        qualified_icon: str = "✅",
        unqualified_icon: str = "❌"
    ) -> List[str]:
        """
        使用模块化渲染器渲染单篇论文。

        参数:
            paper: 论文数据
            keywords_dict: 关键词字典
            analyses: 深度分析列表
            idx: 序号
            is_qualified_section: 是否在及格论文部分
            qualified_icon: 及格图标
            unqualified_icon: 未及格图标

        返回:
            List[str]: 渲染后的行列表
        """
        lines = []
        score_resp = paper['score_response']
        paper_meta = paper.get('paper_metadata')

        # 准备数据（添加keywords_dict供scoring模块使用）
        paper_data = {
            **paper,
            'keywords_dict': keywords_dict
        }

        # 获取论文标题用于标题行
        title = paper_meta.title if paper_meta else paper.get('title', 'Unknown')

        # 生成标题行
        if is_qualified_section:
            lines.append(f"### {idx}. {title[:100]}")
        else:
            status_icon = qualified_icon if score_resp.is_qualified else unqualified_icon
            lines.append(f"### {idx}. {status_icon} {title}")
        lines.append("")

        # 获取模块配置
        modules = self.basic_template.get('modules', [])

        # 使用渲染器工厂渲染各模块
        module_lines = self.renderer_factory.render_modules(paper_data, modules)
        lines.extend(module_lines)

        # 如果是及格论文部分，添加深度分析
        if is_qualified_section and analyses:
            paper_id = paper_meta.paper_id if paper_meta else paper.get('paper_id')
            analysis = next((a['analysis'] for a in analyses if a['paper_id'] == paper_id), None)
            if analysis:
                analysis_data = {'analysis': analysis}
                analysis_lines = self.renderer_factory.get_renderer('deep_analysis').render(
                    analysis_data, {}
                )
                lines.extend(analysis_lines)

        lines.append("---")
        lines.append("")

        return lines

    # ==================== 向后兼容接口 ====================

    def generate_comprehensive_report(
        self,
        all_papers_with_scores: List[Dict[str, Any]],
        keywords_dict: Dict[str, float],
        qualified_papers_with_analysis: List[Dict[str, Any]] = None
    ):
        """
        生成综合研究报告（向后兼容接口）。

        此方法保留以支持旧版代码，新代码请使用 generate_reports_by_source()。
        """
        # 转换为新格式
        scored_papers_by_source = {"arxiv": all_papers_with_scores}
        analyses_by_source = {"arxiv": qualified_papers_with_analysis or []}

        self.generate_reports_by_source(
            scored_papers_by_source=scored_papers_by_source,
            keywords_dict=keywords_dict,
            analyses_by_source=analyses_by_source
        )
