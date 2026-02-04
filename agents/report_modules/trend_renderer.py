"""
关键词趋势渲染器

用于在报告中展示关键词趋势图表。
"""

from typing import List, Dict, Any
from .base_module import BaseModuleRenderer, FormatHelper


class TrendRenderer(BaseModuleRenderer):
    """
    关键词趋势渲染器

    在报告中渲染：
    - 柱状图（热门关键词）
    - 趋势线图（关键词变化）
    - 统计表格
    """

    def __init__(self, format_helper: FormatHelper):
        """初始化渲染器"""
        super().__init__(format_helper)
        self._tracker = None

    @property
    def tracker(self):
        """延迟加载 KeywordTracker"""
        if self._tracker is None:
            try:
                from agents.keyword_tracker import KeywordTracker
                self._tracker = KeywordTracker()
            except Exception:
                return None
        return self._tracker

    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        """
        渲染关键词趋势部分

        Args:
            data: 报告数据
            config: 模块配置

        Returns:
            Markdown 行列表
        """
        if not self.is_enabled(config):
            return []

        if self.tracker is None:
            return []

        # 检查是否有数据
        stats = self.tracker.get_stats()
        if stats.get('normalized_keywords', 0) == 0:
            return []

        lines = []
        days = config.get("days", 30)
        top_n = config.get("top_n", 15)
        trend_n = config.get("trend_n", 5)
        chart_type = config.get("chart_type", "both")
        show_table = config.get("show_table", True)

        # 标题
        label = self.get_label(config)
        lines.append(f"## {label}")
        lines.append("")

        # 柱状图
        if chart_type in ("bar", "both"):
            bar_chart = self.tracker.generate_bar_chart(
                days=days,
                limit=top_n,
                title="Top Research Keywords"
            )
            if bar_chart:
                lines.append("### Top Keywords")
                lines.append("")
                lines.append(bar_chart)
                lines.append("")

        # 趋势线图
        if chart_type in ("line", "both"):
            trend_chart = self.tracker.generate_trend_chart(
                days=days,
                limit=trend_n,
                title="Keyword Trends"
            )
            if trend_chart:
                lines.append("### Keyword Trends Over Time")
                lines.append("")
                lines.append(trend_chart)
                lines.append("")

        # 统计表格
        if show_table:
            table_lines = self._render_table(days, top_n)
            if table_lines:
                lines.extend(table_lines)

        return lines

    def _render_table(self, days: int, limit: int) -> List[str]:
        """渲染关键词统计表格"""
        lines = []

        top_keywords = self.tracker.get_top_keywords(days=days, limit=limit)

        if top_keywords:
            lines.append("### Keyword Statistics")
            lines.append("")
            lines.append("| Rank | Keyword | Count | Category |")
            lines.append("|------|---------|-------|----------|")

            for i, kw_data in enumerate(top_keywords, 1):
                keyword = kw_data.get('keyword', '')
                count = kw_data.get('count', 0)
                category = kw_data.get('category') or '-'
                lines.append(f"| {i} | {keyword} | {count} | {category} |")

            lines.append("")

        return lines
