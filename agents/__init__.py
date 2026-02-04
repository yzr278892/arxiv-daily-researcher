"""
agents模块：包含系统的核心Agent和数据源。

各Agent职责：
- KeywordAgent：从参考PDF中提取关键概念，自动生成搜索关键词
- SearchAgent：统一搜索调度器，管理多个数据源（ArXiv、期刊等）
- AnalysisAgent：分两个阶段对论文进行分析（快速筛选和深度分析）
- Reporter：将分析结果生成Markdown格式的报告（支持按数据源分目录）

数据源模块 (sources/)：
- BasePaperSource：论文数据源抽象基类
- PaperMetadata：统一的论文元数据格式
- ArxivSource：ArXiv预印本数据源（支持PDF下载）
- CrossrefSource：Crossref期刊数据源（仅元数据）
"""
from .keyword_agent import KeywordAgent
from .search_agent import SearchAgent
from .analysis_agent import AnalysisAgent
from .reporter import Reporter

__all__ = ["KeywordAgent", "SearchAgent", "AnalysisAgent", "Reporter"]
