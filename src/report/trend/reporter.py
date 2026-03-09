"""
研究趋势报告生成器

生成 Markdown + HTML 格式的研究趋势报告。
与每日报告不同：
- 无评分系统
- 按时间排序展示论文
- 包含 LLM 趋势分析板块（可配置位置：开头或末尾）
- 报告存储于 research_reports/{keyword_slug}/{date_range}/
"""

import html
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import settings

logger = logging.getLogger(__name__)


def _keyword_slug(keywords: List[str]) -> str:
    """将关键词列表转为目录名：空格→连字符，逗号分隔，小写"""
    parts = []
    for kw in keywords:
        parts.append(kw.strip().lower().replace(" ", "-"))
    return "_".join(parts)


class TrendReporter:
    """
    研究趋势报告生成器。

    职责:
    - 按时间顺序渲染每篇论文（无评分）
    - 在配置位置插入趋势分析板块
    - 输出 Markdown + HTML
    - 保存到 keyword/date_range 子目录
    """

    def __init__(self):
        self.css = settings.load_report_css("html_report.css")

    def render(
        self,
        papers: list,
        tldrs: Dict[str, str],
        trend_analysis: Dict[str, str],
        keywords: List[str],
        date_from: date,
        date_to: date,
        sort_order: str = "ascending",
        token_usage: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Path]:
        """
        生成报告文件。

        参数:
            papers: PaperMetadata 列表（已排序）
            tldrs: {paper_id: tldr} 字典
            trend_analysis: {skill_name: 分析结果 Markdown}
            keywords: 搜索关键词
            date_from: 搜索起始日期
            date_to: 搜索截至日期
            sort_order: 排序方向

        返回:
            Dict[str, Path]: 报告文件路径 {"markdown": path, "html": path, "metadata": path}
        """
        # 创建报告目录（markdown 和 html 分开存放）
        slug = _keyword_slug(keywords)
        date_range_str = f"{date_from}_{date_to}"
        md_dir = settings.RESEARCH_REPORTS_DIR / "markdown" / slug / date_range_str
        html_dir = settings.RESEARCH_REPORTS_DIR / "html" / slug / date_range_str
        md_dir.mkdir(parents=True, exist_ok=True)
        html_dir.mkdir(parents=True, exist_ok=True)

        report_paths = {}

        # 生成 Markdown 报告
        if "markdown" in settings.RESEARCH_OUTPUT_FORMATS:
            md_path = md_dir / "report.md"
            self._generate_markdown(
                md_path,
                papers,
                tldrs,
                trend_analysis,
                keywords,
                date_from,
                date_to,
                sort_order,
                token_usage,
            )
            report_paths["markdown"] = md_path
            logger.info(f"Markdown 报告已生成: {md_path}")

        # 生成 HTML 报告
        if "html" in settings.RESEARCH_OUTPUT_FORMATS:
            html_path = html_dir / "report.html"
            self._generate_html(
                html_path,
                papers,
                tldrs,
                trend_analysis,
                keywords,
                date_from,
                date_to,
                sort_order,
                token_usage,
            )
            report_paths["html"] = html_path
            logger.info(f"HTML 报告已生成: {html_path}")

        # 生成元数据快照（存放在 markdown 目录旁）
        meta_path = md_dir / "metadata.json"
        self._save_metadata(
            meta_path,
            keywords,
            date_from,
            date_to,
            len(papers),
            sort_order,
        )
        report_paths["metadata"] = meta_path

        return report_paths

    # ==================== Markdown 报告 ====================

    def _generate_markdown(
        self,
        filepath: Path,
        papers: list,
        tldrs: Dict[str, str],
        trend_analysis: Dict[str, str],
        keywords: List[str],
        date_from: date,
        date_to: date,
        sort_order: str,
        token_usage: Optional[Dict[str, Any]] = None,
    ):
        """生成 Markdown 格式报告"""
        lines = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        keywords_str = ", ".join(keywords)
        date_range = f"{date_from} ~ {date_to}"

        # 标题
        lines.append(f"# 研究趋势报告 — {keywords_str}")
        lines.append("")
        lines.append(f"> 生成时间: {timestamp}")
        lines.append(f"> 搜索关键词: {keywords_str}")
        lines.append(f"> 时间范围: {date_range}")
        lines.append(f"> 论文数量: {len(papers)} 篇")
        lines.append(f"> 排序方式: {'旧→新' if sort_order == 'ascending' else '新→旧'}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 趋势分析（如果放在开头）
        if settings.RESEARCH_REPORT_POSITION == "beginning" and trend_analysis:
            lines.extend(
                self._render_trend_analysis_md(trend_analysis, keywords, date_range, len(papers))
            )

        # 论文列表
        lines.append(f"## 论文列表 ({len(papers)} 篇)")
        lines.append("")

        for idx, paper in enumerate(papers, 1):
            lines.extend(self._render_paper_md(paper, idx, tldrs))

        # 趋势分析（如果放在末尾，默认）
        if settings.RESEARCH_REPORT_POSITION != "beginning" and trend_analysis:
            lines.extend(
                self._render_trend_analysis_md(trend_analysis, keywords, date_range, len(papers))
            )

        # Token 消耗统计（如果启用）
        if settings.TOKEN_TRACKING_ENABLED and token_usage and token_usage.get("has_data"):
            total = token_usage.get("total", 0)
            tp = token_usage.get("total_prompt", 0)
            tc = token_usage.get("total_completion", 0)
            lines.append("## Token 消耗统计")
            lines.append("")
            lines.append(
                f"- **总计**: {total:,} tokens（输入 {tp:,} / 输出 {tc:,}）"
            )
            by_model = token_usage.get("by_model", {})
            if len(by_model) > 1:
                lines.append("")
                lines.append("| 模型 | 输入 | 输出 | 合计 |")
                lines.append("|------|------|------|------|")
                for model, usage in by_model.items():
                    lines.append(
                        f"| {model} | {usage['prompt']:,} | {usage['completion']:,} | {usage['total']:,} |"
                    )
            lines.append("")

        # 页脚
        lines.append("---")
        lines.append(f"*本报告由 ArXiv Daily Researcher 研究趋势模式生成 | {timestamp}*")
        lines.append("")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _render_paper_md(self, paper, idx: int, tldrs: Dict[str, str]) -> List[str]:
        """渲染单篇论文 Markdown"""
        lines = []
        pub_date = paper.published_date.strftime("%Y-%m-%d") if paper.published_date else "N/A"
        authors = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors += f" 等 {len(paper.authors)} 位作者"
        categories = ", ".join(paper.categories) if paper.categories else ""

        # 标题
        lines.append(f"### {idx}. {paper.title}")
        lines.append("")

        # 元数据
        lines.append(f"**作者**: {authors}")
        lines.append(f"**发布日期**: {pub_date}")
        if categories:
            lines.append(f"**分类**: {categories}")
        lines.append(f"**链接**: [{paper.url}]({paper.url})")
        lines.append("")

        # TLDR
        tldr = tldrs.get(paper.paper_id, "")
        if tldr:
            lines.append(f"!!! tip AI 摘要")
            lines.append("")
            for tl in tldr.split("\n"):
                lines.append(f"    {tl}")
            lines.append("")

        # 原文摘要（折叠）
        if paper.abstract:
            lines.append("<details>")
            lines.append("<summary>Abstract</summary>")
            lines.append("")
            for al in paper.abstract.split("\n"):
                lines.append(f"> {al}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")
        return lines

    def _render_trend_analysis_md(
        self,
        trend_analysis: Dict[str, str],
        keywords: List[str],
        date_range: str,
        total_papers: int,
    ) -> List[str]:
        """渲染趋势分析板块 Markdown"""
        lines = []
        lines.append("## 研究趋势分析")
        lines.append("")
        lines.append(
            f"基于 {total_papers} 篇论文对 \"{', '.join(keywords)}\" 领域的研究趋势进行分析。"
        )
        lines.append("")

        # 技能名称到中文标题的映射
        skill_titles = {
            "temporal_evolution": "一、技术发展时间线",
            "hot_topics": "二、热点话题聚类",
            "key_authors": "三、核心研究者分析",
            "research_gaps": "四、研究空白与机会识别",
            "methodology_trends": "五、方法论趋势分析",
        }

        for skill_name, content in trend_analysis.items():
            title = skill_titles.get(skill_name, skill_name)
            lines.append(f"### {title}")
            lines.append("")
            lines.append(content)
            lines.append("")

        lines.append("---")
        lines.append("")
        return lines

    # ==================== HTML 报告 ====================

    def _generate_html(
        self,
        filepath: Path,
        papers: list,
        tldrs: Dict[str, str],
        trend_analysis: Dict[str, str],
        keywords: List[str],
        date_from: date,
        date_to: date,
        sort_order: str,
        token_usage: Optional[Dict[str, Any]] = None,
    ):
        """生成 HTML 格式报告"""
        h = html.escape
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        keywords_str = ", ".join(keywords)
        date_range = f"{date_from} ~ {date_to}"

        parts = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="zh-CN"><head>')
        parts.append('<meta charset="UTF-8">')
        parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
        parts.append(f"<title>研究趋势报告 — {h(keywords_str)}</title>")
        parts.append(f"<style>{self.css}</style>")
        parts.append("</head><body>")

        # 标题
        parts.append(f"<h1>研究趋势报告 — {h(keywords_str)}</h1>")
        parts.append(
            f'<p class="meta">生成时间: {h(timestamp)} | 时间范围: {h(date_range)} | '
            f'排序: {"旧→新" if sort_order == "ascending" else "新→旧"}</p>'
        )

        # 统计栏
        parts.append('<div class="stats-bar">')
        parts.append(
            f'<div class="stat"><div class="num">{len(papers)}</div>'
            f'<div class="label">论文总数</div></div>'
        )
        parts.append(
            f'<div class="stat"><div class="num">{len(keywords)}</div>'
            f'<div class="label">搜索关键词</div></div>'
        )
        parts.append(
            f'<div class="stat"><div class="num">{len(trend_analysis)}</div>'
            f'<div class="label">分析维度</div></div>'
        )
        parts.append("</div>")

        # 趋势分析（开头位置）
        if settings.RESEARCH_REPORT_POSITION == "beginning" and trend_analysis:
            parts.append(self._render_trend_analysis_html(trend_analysis, keywords_str))

        # 论文列表
        parts.append("<h2>论文列表</h2>")
        for idx, paper in enumerate(papers, 1):
            parts.append(self._render_paper_html(paper, idx, tldrs))

        # 趋势分析（末尾位置，默认）
        if settings.RESEARCH_REPORT_POSITION != "beginning" and trend_analysis:
            parts.append(self._render_trend_analysis_html(trend_analysis, keywords_str))

        # Token 消耗统计
        if settings.TOKEN_TRACKING_ENABLED and token_usage and token_usage.get("has_data"):
            total = token_usage.get("total", 0)
            tp = token_usage.get("total_prompt", 0)
            tc = token_usage.get("total_completion", 0)
            by_model = token_usage.get("by_model", {})
            model_rows = ""
            if len(by_model) > 1:
                for mdl, u in by_model.items():
                    model_rows += (
                        f"<tr><td style='padding:4px 8px;color:#6b7280;'>{h(mdl)}</td>"
                        f"<td style='padding:4px 8px;text-align:right;color:#6b7280;'>{u['prompt']:,}</td>"
                        f"<td style='padding:4px 8px;text-align:right;color:#6b7280;'>{u['completion']:,}</td>"
                        f"<td style='padding:4px 8px;text-align:right;font-weight:600;color:#374151;'>{u['total']:,}</td></tr>"
                    )
            token_table = (
                f"<table style='font-size:12px;border-collapse:collapse;margin-top:4px;'>"
                f"<tr><th style='padding:4px 8px;text-align:left;color:#6b7280;border-bottom:1px solid #e5e7eb;'>模型</th>"
                f"<th style='padding:4px 8px;text-align:right;color:#6b7280;border-bottom:1px solid #e5e7eb;'>输入</th>"
                f"<th style='padding:4px 8px;text-align:right;color:#6b7280;border-bottom:1px solid #e5e7eb;'>输出</th>"
                f"<th style='padding:4px 8px;text-align:right;color:#6b7280;border-bottom:1px solid #e5e7eb;'>合计</th></tr>"
                f"{model_rows}</table>"
            ) if len(by_model) > 1 else ""
            parts.append(
                f'<p style="font-size:13px;color:#9ca3af;margin-top:24px;border-top:1px solid #e5e7eb;padding-top:12px;">'
                f'Token 消耗: <strong style="color:#6b7280;">{total:,}</strong> tokens'
                f'（输入 {tp:,} / 输出 {tc:,}）</p>'
                f'{token_table}'
            )

        # 页脚
        parts.append(
            f'<p class="meta" style="margin-top:40px;text-align:center;">'
            f"由 ArXiv Daily Researcher 研究趋势模式生成 | {h(timestamp)}</p>"
        )
        parts.append("</body></html>")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))

    def _render_paper_html(self, paper, idx: int, tldrs: Dict[str, str]) -> str:
        """渲染单篇论文 HTML 卡片"""
        h = html.escape
        pub_date = paper.published_date.strftime("%Y-%m-%d") if paper.published_date else "N/A"
        authors = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors += f" 等 {len(paper.authors)} 位作者"
        categories = ", ".join(paper.categories) if paper.categories else ""

        card = f'<div class="card pass">'
        # 标题
        card += f'<div class="card-title"><a href="{h(paper.url)}" target="_blank">{idx}. {h(paper.title)}</a></div>'
        # 元数据
        card += f'<div class="field"><span class="field-label">作者:</span> {h(authors)}</div>'
        card += f'<div class="field"><span class="field-label">日期:</span> {h(pub_date)}</div>'
        if categories:
            card += (
                f'<div class="field"><span class="field-label">分类:</span> {h(categories)}</div>'
            )

        # TLDR
        tldr = tldrs.get(paper.paper_id, "")
        if tldr:
            card += f'<div class="tldr"><strong>AI 摘要:</strong> {h(tldr)}</div>'

        # 摘要折叠
        if paper.abstract:
            card += f"<details><summary>Abstract</summary>"
            card += f'<div class="analysis-content"><p>{h(paper.abstract)}</p></div></details>'

        card += "</div>"
        return card

    def _render_trend_analysis_html(self, trend_analysis: Dict[str, str], keywords_str: str) -> str:
        """渲染趋势分析 HTML 板块"""
        h = html.escape
        skill_titles = {
            "temporal_evolution": "技术发展时间线",
            "hot_topics": "热点话题聚类",
            "key_authors": "核心研究者分析",
            "research_gaps": "研究空白与机会识别",
            "methodology_trends": "方法论趋势分析",
        }

        parts = "<h2>研究趋势分析</h2>"
        for skill_name, content in trend_analysis.items():
            title = skill_titles.get(skill_name, skill_name)
            # 简单处理 Markdown 到 HTML（基本转换）
            content_html = self._markdown_to_html_simple(content)
            parts += f'<div class="card pass">'
            parts += f"<h3>{h(title)}</h3>"
            parts += f'<div class="analysis-content">{content_html}</div>'
            parts += "</div>"

        return parts

    @staticmethod
    def _markdown_to_html_simple(md_text: str) -> str:
        """简单的 Markdown → HTML 转换（用于趋势分析内容）"""
        import re

        h = html.escape

        lines = md_text.split("\n")
        html_lines = []
        list_type = None  # None, "ul", or "ol"
        in_table = False

        for line in lines:
            stripped = line.strip()

            # 表格处理
            if stripped.startswith("|") and "|" in stripped[1:]:
                if not in_table:
                    html_lines.append(
                        "<table style='width:100%;border-collapse:collapse;margin:8px 0;font-size:0.9em;'>"
                    )
                    in_table = True
                # 跳过分隔行
                if re.match(r"^\|[\s\-:|]+\|$", stripped):
                    continue
                cells = [c.strip() for c in stripped.split("|")[1:-1]]
                tag = "th" if not any(c for c in cells if not c.startswith("**")) else "td"
                row = (
                    "<tr>"
                    + "".join(
                        f"<{tag} style='padding:6px 8px;border:1px solid #e0e0e0;'>{h(c.strip('*'))}</{tag}>"
                        for c in cells
                    )
                    + "</tr>"
                )
                html_lines.append(row)
                continue
            elif in_table:
                html_lines.append("</table>")
                in_table = False

            # 标题
            if stripped.startswith("####"):
                html_lines.append(f"<h5>{h(stripped[4:].strip())}</h5>")
            elif stripped.startswith("###"):
                html_lines.append(f"<h4>{h(stripped[3:].strip())}</h4>")
            elif stripped.startswith("##"):
                html_lines.append(f"<h3>{h(stripped[2:].strip())}</h3>")
            elif stripped.startswith("- ") or stripped.startswith("* "):
                if list_type != "ul":
                    if list_type == "ol":
                        html_lines.append("</ol>")
                    html_lines.append("<ul>")
                    list_type = "ul"
                content = stripped[2:]
                # 粗体
                content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h(content))
                html_lines.append(f"<li>{content}</li>")
            elif re.match(r"^\d+\.\s", stripped):
                if list_type != "ol":
                    if list_type == "ul":
                        html_lines.append("</ul>")
                    html_lines.append("<ol>")
                    list_type = "ol"
                content = re.sub(r"^\d+\.\s", "", stripped)
                content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h(content))
                html_lines.append(f"<li>{content}</li>")
            else:
                if list_type is not None:
                    html_lines.append(f"</{list_type}>")
                    list_type = None
                if stripped:
                    content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h(stripped))
                    html_lines.append(f"<p>{content}</p>")

        if list_type is not None:
            html_lines.append(f"</{list_type}>")
        if in_table:
            html_lines.append("</table>")

        return "\n".join(html_lines)

    # ==================== 元数据 ====================

    def _save_metadata(
        self,
        filepath: Path,
        keywords: List[str],
        date_from: date,
        date_to: date,
        total_papers: int,
        sort_order: str,
    ):
        """保存搜索参数快照"""
        metadata = {
            "generated_at": datetime.now().isoformat(),
            "keywords": keywords,
            "date_from": str(date_from),
            "date_to": str(date_to),
            "total_papers": total_papers,
            "sort_order": sort_order,
            "llm_config": {
                "tldr_model": settings.CHEAP_LLM.model_name,
                "analysis_model": settings.SMART_LLM.model_name,
            },
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
