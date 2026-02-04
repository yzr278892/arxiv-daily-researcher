"""
ArXiv 论文数据源

从 ArXiv 预印本服务器抓取论文，支持 PDF 下载和深度分析。
"""

import arxiv
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .base_source import BasePaperSource, PaperMetadata

logger = logging.getLogger(__name__)


class ArxivSource(BasePaperSource):
    """
    ArXiv 论文数据源。

    特点：
    - 支持按领域分类（如 quant-ph, cs.AI）抓取
    - 支持 PDF 下载，可进行深度分析
    - 使用官方 arxiv Python 库
    """

    def __init__(self, history_dir: Path, max_results: int = 100):
        """
        初始化 ArXiv 数据源。

        参数:
            history_dir: 历史记录存储目录
            max_results: 每个领域最多抓取的论文数
        """
        super().__init__("arxiv", history_dir)
        self.max_results = max_results
        self.client = arxiv.Client(
            page_size=100,
            delay_seconds=6.0,  # 避免 429 错误
            num_retries=3
        )

    @property
    def display_name(self) -> str:
        return "ArXiv"

    def can_download_pdf(self) -> bool:
        return True

    def fetch_papers(
        self,
        days: int,
        domains: List[str] = None,
        **kwargs
    ) -> List[PaperMetadata]:
        """
        从 ArXiv 抓取指定领域最近 N 天的论文。

        参数:
            days: 搜索最近 N 天的论文
            domains: ArXiv 领域分类列表，如 ["quant-ph", "cs.AI"]

        返回:
            List[PaperMetadata]: 论文元数据列表
        """
        if domains is None:
            domains = ["quant-ph"]

        all_papers = {}
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        logger.info(f"[ArXiv] 开始抓取论文")
        logger.info(f"  目标领域: {domains}")
        logger.info(f"  时间范围: 最近 {days} 天")

        for domain in domains:
            query = f"cat:{domain}"
            logger.info(f"  正在抓取领域 {domain}...")

            search = arxiv.Search(
                query=query,
                max_results=self.max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate
            )

            # 添加重试机制
            max_retries = 3
            retry_count = 0
            base_wait_time = 60

            while retry_count <= max_retries:
                try:
                    count = 0
                    for result in self.client.results(search):
                        paper_id = result.get_short_id()

                        # 去重：跳过已处理的论文
                        if self.is_processed(paper_id):
                            continue

                        # 去重：跳过本次已抓取的论文
                        if paper_id in all_papers:
                            continue

                        # 时间过滤
                        if result.published < cutoff_date:
                            continue

                        # 转换为统一格式
                        metadata = PaperMetadata(
                            paper_id=paper_id,
                            title=result.title,
                            authors=[author.name for author in result.authors],
                            abstract=result.summary,
                            published_date=result.published,
                            url=result.entry_id,
                            source="arxiv",
                            pdf_url=result.pdf_url,
                            doi=result.doi,
                            categories=list(result.categories) if result.categories else []
                        )
                        all_papers[paper_id] = metadata
                        count += 1

                    logger.info(f"    领域 {domain}: 发现 {count} 篇新论文")
                    break  # 成功则退出重试循环

                except Exception as e:
                    error_msg = str(e)
                    if "429" in error_msg or "Too Many Requests" in error_msg:
                        retry_count += 1
                        if retry_count <= max_retries:
                            wait_time = base_wait_time * (2 ** (retry_count - 1))
                            logger.warning(f"    遇到速率限制，等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                        else:
                            logger.error(f"    领域 {domain} 抓取失败: 超过最大重试次数")
                            break
                    else:
                        logger.error(f"    领域 {domain} 抓取失败: {e}")
                        break

        papers = list(all_papers.values())
        logger.info(f"[ArXiv] 总计发现 {len(papers)} 篇新论文")
        return papers
