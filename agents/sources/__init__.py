"""
论文数据源模块

提供多种论文数据源的统一接口：
- ArxivSource: ArXiv预印本数据源（支持PDF下载）
- OpenAlexSource: OpenAlex期刊数据源（元数据 + 摘要）
- SemanticScholarEnricher: Semantic Scholar数据增强器（TLDR + arXiv链接）
"""

from .base_source import BasePaperSource, PaperMetadata
from .arxiv_source import ArxivSource
from .openalex_source import OpenAlexSource
from .semantic_scholar_enricher import SemanticScholarEnricher

__all__ = [
    "BasePaperSource",
    "PaperMetadata",
    "ArxivSource",
    "OpenAlexSource",
    "SemanticScholarEnricher",
]
