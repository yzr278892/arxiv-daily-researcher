"""
报告模块渲染器包

提供模块化的报告生成功能，支持通过JSON模板配置报告的结构和格式。
"""

from .base_module import BaseModuleRenderer, FormatHelper
from .renderers import (
    MetadataRenderer,
    AbstractOriginalRenderer,
    AbstractCnRenderer,
    TldrSemanticScholarRenderer,
    TldrAiRenderer,
    ScoringRenderer,
    ExtractedKeywordsRenderer,
    DeepAnalysisRenderer,
)

__all__ = [
    "BaseModuleRenderer",
    "FormatHelper",
    "MetadataRenderer",
    "AbstractOriginalRenderer",
    "AbstractCnRenderer",
    "TldrSemanticScholarRenderer",
    "TldrAiRenderer",
    "ScoringRenderer",
    "ExtractedKeywordsRenderer",
    "DeepAnalysisRenderer",
]
