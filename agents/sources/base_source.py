"""
论文数据源抽象基类

定义所有论文数据源必须实现的统一接口。
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class PaperMetadata:
    """
    统一的论文元数据格式。

    所有数据源返回的论文都使用这个统一格式，
    便于后续的评分、分析和报告生成。
    """
    paper_id: str                          # 唯一标识符（ArXiv ID 或 DOI）
    title: str                             # 论文标题
    authors: List[str]                     # 作者列表
    abstract: str                          # 摘要
    published_date: datetime               # 发布日期
    url: str                               # 论文页面URL
    source: str                            # 数据源标识（如 "arxiv", "prl", "pra"）
    pdf_url: Optional[str] = None          # PDF下载链接（如果可用）
    doi: Optional[str] = None              # DOI
    journal: Optional[str] = None          # 期刊名称
    categories: List[str] = field(default_factory=list)  # 分类/领域
    semantic_scholar_tldr: Optional[str] = None  # Semantic Scholar AI生成的TLDR
    arxiv_id: Optional[str] = None         # arXiv ID（期刊论文可能也有arXiv版本）
    arxiv_url: Optional[str] = None        # arXiv论文页面URL

    def has_pdf_access(self) -> bool:
        """是否可以下载PDF进行深度分析"""
        # 优先使用原始PDF链接，否则使用arXiv PDF
        return (self.pdf_url is not None and self.pdf_url != "") or self.arxiv_id is not None

    def get_arxiv_pdf_url(self) -> Optional[str]:
        """获取arXiv PDF下载链接"""
        if self.arxiv_id:
            return f"http://arxiv.org/pdf/{self.arxiv_id}.pdf"
        return None

    def get_best_pdf_url(self) -> Optional[str]:
        """获取最佳的PDF下载链接（优先原始PDF，否则arXiv）"""
        if self.pdf_url:
            return self.pdf_url
        return self.get_arxiv_pdf_url()

    def get_authors_string(self) -> str:
        """获取作者字符串（逗号分隔）"""
        return ", ".join(self.authors)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "url": self.url,
            "source": self.source,
            "pdf_url": self.pdf_url,
            "doi": self.doi,
            "journal": self.journal,
            "categories": self.categories,
            "semantic_scholar_tldr": self.semantic_scholar_tldr,
            "arxiv_id": self.arxiv_id,
            "arxiv_url": self.arxiv_url,
        }


class BasePaperSource(ABC):
    """
    论文数据源抽象基类。

    所有具体的数据源（ArXiv、Crossref等）都必须继承此类并实现抽象方法。

    职责：
    - 定义统一的论文抓取接口
    - 管理历史记录（避免重复处理）
    - 提供数据源元信息
    """

    def __init__(self, source_name: str, history_dir: Path):
        """
        初始化数据源。

        参数:
            source_name: 数据源名称（如 "arxiv", "crossref"）
            history_dir: 历史记录存储目录
        """
        self.source_name = source_name
        self.history_dir = history_dir
        self.history_file = history_dir / f"{source_name}_history.json"
        self.history: Dict[str, str] = {}
        self._load_history()

    @abstractmethod
    def fetch_papers(self, days: int, **kwargs) -> List[PaperMetadata]:
        """
        抓取论文的抽象方法。

        参数:
            days: 搜索最近N天的论文
            **kwargs: 数据源特定的参数

        返回:
            List[PaperMetadata]: 统一格式的论文列表
        """
        pass

    @abstractmethod
    def can_download_pdf(self) -> bool:
        """
        该数据源是否支持PDF下载。

        返回:
            bool: True表示支持PDF下载，可以进行深度分析
        """
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """数据源的显示名称（用于报告）"""
        pass

    def is_processed(self, paper_id: str) -> bool:
        """检查论文是否已处理过"""
        return paper_id in self.history

    def mark_as_processed(self, paper_id: str):
        """标记论文为已处理"""
        self.history[paper_id] = datetime.now().isoformat()
        self._save_history()

    def _load_history(self):
        """从文件加载历史记录"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
                logger.debug(f"[{self.source_name}] 加载历史记录: {len(self.history)} 条")
            except Exception as e:
                logger.warning(f"[{self.source_name}] 加载历史记录失败: {e}")
                self.history = {}
        else:
            self.history = {}

    def _save_history(self):
        """保存历史记录到文件"""
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[{self.source_name}] 保存历史记录失败: {e}")

    def get_history_count(self) -> int:
        """获取历史记录数量"""
        return len(self.history)

    def clear_history(self):
        """清空历史记录"""
        self.history = {}
        self._save_history()
        logger.info(f"[{self.source_name}] 历史记录已清空")
