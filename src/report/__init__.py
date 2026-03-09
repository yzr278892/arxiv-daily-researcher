"""
报告生成模块

包含三种报告生成器：
- daily：每日论文监控报告（评分排序、深度分析）
- trend：研究趋势报告（时间排序、LLM 趋势分析）
- keyword_trend：关键词趋势报告（柱状图、趋势线图、统计表格）
"""

from .daily.reporter import Reporter
from .trend.reporter import TrendReporter
from .keyword_trend.reporter import KeywordTrendReporter

__all__ = ["Reporter", "TrendReporter", "KeywordTrendReporter"]
