"""
关键词趋势追踪器主模块

协调数据库、标准化器和图表生成器。
"""

import logging
from datetime import date
from pathlib import Path
from typing import List, Dict, Any, Optional

from .database import KeywordDatabase, KeywordTrendData
from .normalizer import KeywordNormalizer
from .mermaid_generator import MermaidGenerator

logger = logging.getLogger(__name__)


class KeywordTracker:
    """
    关键词趋势追踪器

    主要功能：
    - 记录论文提取的关键词
    - 每日自动 AI 标准化
    - 生成趋势图表
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        enable_auto_normalize: bool = True
    ):
        """
        初始化追踪器

        Args:
            db_path: 数据库路径（默认从 settings 获取）
            enable_auto_normalize: 是否启用自动标准化
        """
        from config import settings

        if db_path is None:
            db_path = getattr(settings, 'KEYWORD_DB_PATH', settings.DATA_DIR / "keywords.db")

        self.db = KeywordDatabase(db_path)
        self.normalizer = KeywordNormalizer()
        self.mermaid = MermaidGenerator()
        self.enable_auto_normalize = enable_auto_normalize

        # 从 settings 获取配置
        self.default_days = getattr(settings, 'KEYWORD_TREND_DEFAULT_DAYS', 30)
        self.chart_top_n = getattr(settings, 'KEYWORD_CHART_TOP_N', 15)
        self.trend_top_n = getattr(settings, 'KEYWORD_TREND_TOP_N', 5)
        self.batch_size = getattr(settings, 'KEYWORD_NORMALIZATION_BATCH_SIZE', 50)

    def record_keywords(
        self,
        keywords: List[str],
        paper_id: str,
        source: str,
        extracted_date: Optional[date] = None
    ) -> None:
        """
        记录论文提取的关键词

        Args:
            keywords: 关键词列表
            paper_id: 论文ID
            source: 数据源
            extracted_date: 提取日期
        """
        if not keywords:
            return

        try:
            inserted = self.db.insert_keywords(
                keywords=keywords,
                paper_id=paper_id,
                source=source,
                extracted_date=extracted_date
            )
            logger.debug(f"记录 {len(inserted)} 个关键词 (论文: {paper_id})")
        except Exception as e:
            logger.error(f"记录关键词失败: {e}")

    def run_daily_normalization(self, batch_size: Optional[int] = None) -> Dict[str, Any]:
        """
        运行每日 AI 标准化

        Args:
            batch_size: 每批处理数量

        Returns:
            统计信息 {"processed": N, "new_canonical": M, "merged": K}
        """
        if batch_size is None:
            batch_size = self.batch_size

        stats = {
            "processed": 0,
            "new_canonical": 0,
            "merged": 0
        }

        try:
            # 获取未标准化的唯一关键词
            unnormalized = self.db.get_unique_unnormalized_keywords(limit=batch_size * 2)

            if not unnormalized:
                logger.info("没有待标准化的关键词")
                return stats

            logger.info(f"开始标准化 {len(unnormalized)} 个关键词...")

            # 获取现有的标准关键词
            existing_canonical = self.db.get_all_canonical_keywords()

            # AI 批量标准化
            results = self.normalizer.normalize_batch(
                keywords=unnormalized,
                existing_canonical=existing_canonical,
                batch_size=batch_size
            )

            # 处理结果
            for result in results:
                if result.confidence < 0.5:
                    continue

                # 获取或创建标准关键词
                normalized_id = self.db.get_or_create_normalized_keyword(
                    canonical_keyword=result.canonical_form,
                    category=result.category
                )

                # 检查是否是新创建的
                if result.canonical_form.lower() not in [c.lower() for c in existing_canonical]:
                    stats["new_canonical"] += 1

                # 为每个原始关键词添加别名并链接
                for raw_kw in result.original_keywords:
                    self.db.add_keyword_alias(
                        raw_keyword=raw_kw,
                        normalized_id=normalized_id,
                        confidence=result.confidence
                    )

                    # 链接已有记录
                    linked = self.db.link_keywords_to_normalized(
                        raw_keyword=raw_kw,
                        normalized_id=normalized_id
                    )
                    stats["merged"] += linked

                stats["processed"] += len(result.original_keywords)

            # 更新每日统计
            self.db.update_daily_counts()

            logger.info(f"标准化完成: 处理 {stats['processed']}, "
                       f"新增规范词 {stats['new_canonical']}, 合并 {stats['merged']}")

        except Exception as e:
            logger.error(f"标准化失败: {e}")

        return stats

    def get_top_keywords(
        self,
        days: Optional[int] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取热门关键词

        Args:
            days: 回溯天数
            limit: 返回数量

        Returns:
            [{"keyword": str, "count": int, "category": str|None}, ...]
        """
        if days is None:
            days = self.default_days
        if limit is None:
            limit = self.chart_top_n

        top = self.db.get_top_keywords(days=days, limit=limit)

        return [
            {"keyword": kw, "count": count, "category": cat}
            for kw, count, cat in top
        ]

    def get_trends(
        self,
        days: Optional[int] = None,
        keywords: Optional[List[str]] = None,
        limit: Optional[int] = None
    ) -> List[KeywordTrendData]:
        """
        获取关键词趋势数据

        Args:
            days: 回溯天数
            keywords: 指定关键词
            limit: 关键词数量

        Returns:
            KeywordTrendData 列表
        """
        if days is None:
            days = self.default_days
        if limit is None:
            limit = self.trend_top_n

        return self.db.get_keyword_trends(
            days=days,
            keywords=keywords,
            limit=limit
        )

    def generate_bar_chart(
        self,
        days: Optional[int] = None,
        limit: Optional[int] = None,
        title: str = "Top Research Keywords"
    ) -> str:
        """
        生成柱状图

        Args:
            days: 回溯天数
            limit: 关键词数量
            title: 图表标题

        Returns:
            Mermaid 代码
        """
        if days is None:
            days = self.default_days
        if limit is None:
            limit = self.chart_top_n

        top = self.db.get_top_keywords(days=days, limit=limit)

        if not top:
            return ""

        data = [(kw, count) for kw, count, _ in top]
        full_title = f"{title} (Last {days} Days)"

        return self.mermaid.generate_bar_chart(
            data=data,
            title=full_title
        )

    def generate_trend_chart(
        self,
        days: Optional[int] = None,
        keywords: Optional[List[str]] = None,
        limit: Optional[int] = None,
        title: str = "Keyword Trends"
    ) -> str:
        """
        生成趋势线图

        Args:
            days: 回溯天数
            keywords: 指定关键词
            limit: 关键词数量
            title: 图表标题

        Returns:
            Mermaid 代码
        """
        if days is None:
            days = self.default_days
        if limit is None:
            limit = self.trend_top_n

        trends = self.db.get_keyword_trends(
            days=days,
            keywords=keywords,
            limit=limit
        )

        if not trends:
            return ""

        # 转换为 mermaid_generator 期望的格式
        from .mermaid_generator import KeywordTrendData as MermaidTrendData
        mermaid_trends = [
            MermaidTrendData(keyword=t.keyword, daily_counts=t.daily_counts)
            for t in trends
        ]

        return self.mermaid.generate_line_chart(
            trends=mermaid_trends,
            title=title,
            days=days
        )

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.db.get_stats()
