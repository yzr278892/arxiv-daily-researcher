"""
运行模式包

提供两种运行模式的流水线：
- DailyResearchPipeline：每日论文监控与研究（daily_research 模式）
- TrendResearchPipeline：关键词驱动的研究趋势分析（trend_research 模式）
"""

from .daily_research import DailyResearchPipeline
from .trend_research import TrendResearchPipeline

__all__ = ["DailyResearchPipeline", "TrendResearchPipeline"]
