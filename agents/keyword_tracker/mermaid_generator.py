"""
Mermaid 图表生成模块

生成 GitHub 兼容的 Mermaid xychart-beta 图表。
"""

from datetime import date, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class KeywordTrendData:
    """关键词趋势数据"""
    keyword: str
    daily_counts: Dict[date, int]


class MermaidGenerator:
    """
    Mermaid 图表生成器

    支持生成：
    - 柱状图（Top N 关键词）
    - 趋势线图（关键词随时间变化）
    """

    def generate_bar_chart(
        self,
        data: List[Tuple[str, int]],
        title: str = "Top Keywords",
        y_label: str = "Paper Count"
    ) -> str:
        """
        生成柱状图

        Args:
            data: [(关键词, 计数), ...]
            title: 图表标题
            y_label: Y轴标签

        Returns:
            Mermaid 代码块
        """
        if not data:
            return ""

        # 限制关键词长度，避免图表过宽
        keywords = [self._truncate_keyword(kw, 20) for kw, _ in data]
        counts = [count for _, count in data]

        # 计算Y轴范围
        max_count = max(counts) if counts else 10
        y_max = self._round_up(max_count)

        # 构建 Mermaid 代码
        x_axis = ", ".join(f'"{kw}"' for kw in keywords)
        bar_data = ", ".join(str(c) for c in counts)

        chart = f"""```mermaid
xychart-beta
    title "{title}"
    x-axis [{x_axis}]
    y-axis "{y_label}" 0 --> {y_max}
    bar [{bar_data}]
```"""
        return chart

    def generate_line_chart(
        self,
        trends: List[KeywordTrendData],
        title: str = "Keyword Trends",
        days: int = 30,
        aggregate_days: int = 7
    ) -> str:
        """
        生成趋势线图

        Args:
            trends: KeywordTrendData 列表
            title: 图表标题
            days: 回溯天数
            aggregate_days: 聚合周期（天）

        Returns:
            Mermaid 代码块
        """
        if not trends:
            return ""

        # 生成日期范围
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # 按周聚合日期
        date_ranges = self._generate_date_ranges(start_date, end_date, aggregate_days)
        if not date_ranges:
            return ""

        # 生成X轴标签
        x_labels = [f'"{self._format_date_range(start, end)}"' for start, end in date_ranges]

        # 为每个关键词计算聚合值
        lines = []
        all_values = []

        for trend in trends:
            values = []
            for range_start, range_end in date_ranges:
                count = sum(
                    trend.daily_counts.get(d, 0)
                    for d in self._date_range(range_start, range_end)
                )
                values.append(count)
                all_values.append(count)

            kw_name = self._truncate_keyword(trend.keyword, 18)
            line_data = ", ".join(str(v) for v in values)
            lines.append(f'    line "{kw_name}" [{line_data}]')

        # 计算Y轴范围
        max_val = max(all_values) if all_values else 10
        y_max = self._round_up(max_val)

        # 构建 Mermaid 代码
        chart = f"""```mermaid
xychart-beta
    title "{title}"
    x-axis [{", ".join(x_labels)}]
    y-axis "Papers" 0 --> {y_max}
{chr(10).join(lines)}
```"""
        return chart

    def _truncate_keyword(self, keyword: str, max_len: int) -> str:
        """截断关键词"""
        if len(keyword) <= max_len:
            return keyword
        return keyword[:max_len - 2] + ".."

    def _round_up(self, value: int) -> int:
        """向上取整到合适的刻度"""
        if value <= 10:
            return 10
        elif value <= 20:
            return 20
        elif value <= 50:
            return 50
        elif value <= 100:
            return 100
        else:
            # 向上取整到最近的50
            return ((value // 50) + 1) * 50

    def _generate_date_ranges(
        self,
        start_date: date,
        end_date: date,
        aggregate_days: int
    ) -> List[Tuple[date, date]]:
        """生成日期范围列表"""
        ranges = []
        current = start_date

        while current <= end_date:
            range_end = min(current + timedelta(days=aggregate_days - 1), end_date)
            ranges.append((current, range_end))
            current = range_end + timedelta(days=1)

        return ranges

    def _format_date_range(self, start: date, end: date) -> str:
        """格式化日期范围为标签"""
        if start == end:
            return start.strftime("%m/%d")
        return f"{start.strftime('%m/%d')}"

    def _date_range(self, start: date, end: date) -> List[date]:
        """生成日期列表"""
        days = []
        current = start
        while current <= end:
            days.append(current)
            current += timedelta(days=1)
        return days
