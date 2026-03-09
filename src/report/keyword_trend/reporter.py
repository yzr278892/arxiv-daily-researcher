"""
关键词趋势报告生成器

生成 Markdown + HTML 格式的关键词趋势报告：
- Markdown: Mermaid 柱状图（序号X轴）+ 图例表格 + 趋势线图
- HTML: CSS 彩色水平柱状图 + 图例表格 + 趋势热图表格
"""

import html
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import settings

logger = logging.getLogger(__name__)

# 关键词颜色调色板（15色，确保视觉可区分）
COLOR_PALETTE = [
    "#3b82f6",  # blue
    "#22c55e",  # green
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#8b5cf6",  # purple
    "#ec4899",  # pink
    "#06b6d4",  # cyan
    "#f97316",  # orange
    "#14b8a6",  # teal
    "#6366f1",  # indigo
    "#84cc16",  # lime
    "#f43f5e",  # rose
    "#0ea5e9",  # sky
    "#7c3aed",  # violet
    "#10b981",  # emerald
]


class KeywordTrendReporter:
    """
    关键词趋势报告生成器。

    生成包含彩色图表和图例表格的 Markdown + HTML 报告。
    报告路径：
    - Markdown: data/reports/keyword_trend/markdown/keyword_trends_{date}.md
    - HTML:     data/reports/keyword_trend/html/keyword_trends_{date}.html
    """

    def render(
        self,
        top_keywords: List[Dict[str, Any]],
        trends,
        bar_chart: str,
        trend_chart: str,
        today: date,
        days: int,
    ) -> Dict[str, Path]:
        """
        生成关键词趋势报告。

        参数:
            top_keywords: [{"keyword": str, "count": int, "category": str|None}, ...]
            trends: KeywordTrendData 列表（趋势线图数据）
            bar_chart: Mermaid 柱状图代码（序号X轴）
            trend_chart: Mermaid 趋势线图代码
            today: 今天日期
            days: 统计天数

        返回:
            {"markdown": Path, "html": Path}
        """
        filename_base = f"keyword_trends_{today.isoformat()}"
        md_dir = settings.REPORTS_DIR / "keyword_trend" / "markdown"
        html_dir = settings.REPORTS_DIR / "keyword_trend" / "html"
        md_dir.mkdir(parents=True, exist_ok=True)
        html_dir.mkdir(parents=True, exist_ok=True)

        report_paths = {}

        md_path = md_dir / f"{filename_base}.md"
        self._generate_markdown(md_path, top_keywords, bar_chart, trend_chart, today, days)
        report_paths["markdown"] = md_path
        logger.info(f"关键词趋势 Markdown 报告已生成: {md_path}")

        html_path = html_dir / f"{filename_base}.html"
        self._generate_html(html_path, top_keywords, trends, today, days)
        report_paths["html"] = html_path
        logger.info(f"关键词趋势 HTML 报告已生成: {html_path}")

        return report_paths

    # ==================== Markdown ====================

    def _generate_markdown(
        self,
        filepath: Path,
        top_keywords: List[Dict[str, Any]],
        bar_chart: str,
        trend_chart: str,
        today: date,
        days: int,
    ):
        lines = []
        lines.append("# 关键词趋势分析报告")
        lines.append("")
        lines.append(f"> 生成日期: {today}")
        lines.append(f"> 统计周期: 最近 {days} 天")
        lines.append(f"> 关键词数量: {len(top_keywords)} 个")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 柱状图（序号X轴）
        if bar_chart:
            lines.append("## 热门关键词排名")
            lines.append("")
            lines.append(bar_chart)
            lines.append("")

        # 图例表格（序号 → 关键词映射）
        if top_keywords:
            lines.append("### 图例说明")
            lines.append("")
            lines.append("| # | 关键词 | 论文数 | 类别 |")
            lines.append("|---|--------|--------|------|")
            for i, kw in enumerate(top_keywords, 1):
                category = kw.get("category") or "-"
                lines.append(f"| {i} | {kw['keyword']} | {kw['count']} | {category} |")
            lines.append("")

        # 趋势线图
        if trend_chart:
            lines.append("## 关键词趋势变化")
            lines.append("")
            lines.append(trend_chart)
            lines.append("")

        lines.append("---")
        lines.append(f"*本报告由 ArXiv Daily Researcher 关键词趋势模块生成 | {today}*")
        lines.append("")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ==================== HTML ====================

    def _generate_html(
        self,
        filepath: Path,
        top_keywords: List[Dict[str, Any]],
        trends,
        today: date,
        days: int,
    ):
        h = html.escape
        max_count = max((kw["count"] for kw in top_keywords), default=1)

        parts = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="zh-CN"><head>')
        parts.append('<meta charset="UTF-8">')
        parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
        parts.append(f"<title>关键词趋势分析报告 {today}</title>")
        parts.append(f"<style>{self._get_css()}</style>")
        parts.append("</head><body>")

        parts.append("<h1>关键词趋势分析报告</h1>")
        parts.append(
            f'<p class="meta">生成日期: {today} | 统计周期: 最近 {days} 天 | '
            f"关键词数量: {len(top_keywords)} 个</p>"
        )

        # 统计栏
        parts.append('<div class="stats-bar">')
        parts.append(
            f'<div class="stat"><div class="num">{len(top_keywords)}</div>'
            f'<div class="label">关键词</div></div>'
        )
        if top_keywords:
            parts.append(
                f'<div class="stat"><div class="num">{top_keywords[0]["count"]}</div>'
                f'<div class="label">Top 1 论文数</div></div>'
            )
            total = sum(kw["count"] for kw in top_keywords)
            parts.append(
                f'<div class="stat"><div class="num">{total}</div>'
                f'<div class="label">总论文数</div></div>'
            )
        parts.append("</div>")

        # 水平柱状图
        if top_keywords:
            parts.append("<h2>热门关键词排名</h2>")
            parts.append('<div class="bar-chart">')
            for i, kw in enumerate(top_keywords):
                color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
                pct = kw["count"] / max_count * 100 if max_count > 0 else 0
                parts.append('<div class="bar-row">')
                parts.append(
                    f'<div class="bar-label">'
                    f'<span class="color-dot" style="background:{color};"></span>'
                    f'<span class="bar-num">{i + 1}</span></div>'
                )
                parts.append(
                    f'<div class="bar-track">'
                    f'<div class="bar-fill" style="width:{pct:.1f}%;background:{color};"></div>'
                    f"</div>"
                )
                parts.append(f'<div class="bar-count">{kw["count"]}</div>')
                parts.append("</div>")
            parts.append("</div>")

            # 图例表格
            parts.append("<h3>图例说明</h3>")
            parts.append('<table class="legend-table">')
            parts.append(
                "<thead><tr><th>#</th><th>颜色</th><th>关键词</th>"
                "<th>论文数</th><th>类别</th></tr></thead><tbody>"
            )
            for i, kw in enumerate(top_keywords):
                color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
                category = h(kw.get("category") or "-")
                parts.append(
                    f"<tr>"
                    f"<td>{i + 1}</td>"
                    f'<td><span class="color-badge" style="background:{color};"></span></td>'
                    f"<td>{h(kw['keyword'])}</td>"
                    f"<td>{kw['count']}</td>"
                    f"<td>{category}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table>")

        # 趋势热图表格
        if trends:
            parts.append("<h2>关键词趋势变化</h2>")
            parts.append(self._render_trend_table(trends))

        parts.append(
            f'<p class="meta" style="margin-top:40px;text-align:center;">'
            f"由 ArXiv Daily Researcher 关键词趋势模块生成 | {today}</p>"
        )
        parts.append("</body></html>")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))

    def _render_trend_table(self, trends) -> str:
        """渲染关键词趋势热图表格（按周聚合）"""
        h = html.escape
        if not trends:
            return ""

        # 收集所有日期
        all_dates = set()
        for t in trends:
            all_dates.update(t.daily_counts.keys())

        if not all_dates:
            return ""

        sorted_dates = sorted(all_dates)

        # 按7天聚合成周期
        periods = []
        cur = sorted_dates[0]
        end = sorted_dates[-1]
        while cur <= end:
            p_end = min(cur + timedelta(days=6), end)
            periods.append((cur, p_end))
            cur = p_end + timedelta(days=1)

        if not periods:
            return ""

        html_parts = ['<div style="overflow-x:auto;"><table class="trend-table">']

        # 表头
        header = "<thead><tr><th>关键词</th>"
        for p_start, p_end in periods:
            label = p_start.strftime("%m/%d")
            header += f"<th>{label}</th>"
        header += "</tr></thead>"
        html_parts.append(header)

        html_parts.append("<tbody>")
        for i, t in enumerate(trends):
            color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
            period_counts = [
                sum(t.daily_counts.get(d, 0) for d in self._date_range(ps, pe))
                for ps, pe in periods
            ]
            max_period = max(period_counts) if period_counts else 0

            row = (
                f'<tr><td><span class="color-dot" style="background:{color};"></span>'
                f" {h(t.keyword)}</td>"
            )
            rgb = self._hex_to_rgb(color)
            for count in period_counts:
                if max_period > 0 and count > 0:
                    alpha = min(count / max_period * 0.8 + 0.1, 0.9)
                    row += f'<td style="background:rgba({rgb},{alpha:.2f});">{count}</td>'
                else:
                    row += f"<td>{count}</td>"
            row += "</tr>"
            html_parts.append(row)

        html_parts.append("</tbody></table></div>")
        return "\n".join(html_parts)

    @staticmethod
    def _date_range(start: date, end: date) -> List[date]:
        days = []
        cur = start
        while cur <= end:
            days.append(cur)
            cur += timedelta(days=1)
        return days

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> str:
        """将 #rrggbb 转为 r,g,b 字符串"""
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"{r},{g},{b}"

    @staticmethod
    def _get_css() -> str:
        return """
:root {
    --bg: #0f172a;
    --surface: #1e293b;
    --border: #334155;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #38bdf8;
}
* { box-sizing: border-box; }
body {
    max-width: 1100px; margin: 0 auto; padding: 24px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px; color: var(--text); background: var(--bg);
}
h1 { font-size: 1.8rem; color: var(--accent); margin-bottom: 4px; }
h2 {
    font-size: 1.3rem; color: var(--accent); margin: 28px 0 12px;
    border-bottom: 1px solid var(--border); padding-bottom: 6px;
}
h3 { font-size: 1.05rem; color: var(--muted); margin: 20px 0 8px; }
.meta { color: var(--muted); font-size: 0.85rem; margin: 4px 0 20px; }

/* 统计栏 */
.stats-bar { display: flex; gap: 16px; flex-wrap: wrap; margin: 16px 0 24px; }
.stat {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 20px; text-align: center; min-width: 100px;
}
.stat .num { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
.stat .label { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }

/* 水平柱状图 */
.bar-chart { margin: 12px 0 20px; }
.bar-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.bar-label { display: flex; align-items: center; gap: 6px; min-width: 46px; }
.bar-num { font-weight: 600; font-size: 0.9rem; color: var(--muted); }
.color-dot {
    display: inline-block; width: 12px; height: 12px;
    border-radius: 50%; flex-shrink: 0;
}
.bar-track {
    flex: 1; background: var(--border); border-radius: 4px;
    height: 22px; overflow: hidden;
}
.bar-fill { height: 100%; border-radius: 4px; min-width: 2px; }
.bar-count { min-width: 40px; text-align: right; font-size: 0.9rem; color: var(--muted); }

/* 图例表格 */
.legend-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; margin: 8px 0 20px; }
.legend-table th {
    background: var(--surface); padding: 8px 12px;
    text-align: left; border-bottom: 2px solid var(--border);
}
.legend-table td { padding: 7px 12px; border-bottom: 1px solid var(--border); }
.legend-table tr:hover td { background: var(--surface); }
.color-badge {
    display: inline-block; width: 20px; height: 14px;
    border-radius: 3px; vertical-align: middle;
}

/* 趋势热图表格 */
.trend-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin: 8px 0; }
.trend-table th {
    background: var(--surface); padding: 7px 10px;
    text-align: center; border-bottom: 2px solid var(--border);
    font-size: 0.8rem; white-space: nowrap;
}
.trend-table th:first-child { text-align: left; }
.trend-table td {
    padding: 6px 10px; border-bottom: 1px solid var(--border);
    text-align: center;
}
.trend-table td:first-child { text-align: left; min-width: 150px; white-space: nowrap; }
"""
