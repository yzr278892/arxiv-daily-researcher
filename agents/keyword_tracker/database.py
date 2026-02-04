"""
SQLite 数据库操作模块

提供关键词存储、查询和统计功能。
"""

import sqlite3
import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KeywordRecord:
    """原始关键词记录"""
    id: int
    keyword: str
    paper_id: str
    source: str
    extracted_date: date
    normalized_keyword_id: Optional[int] = None


@dataclass
class NormalizedKeyword:
    """标准化关键词"""
    id: int
    canonical_keyword: str
    category: Optional[str] = None


@dataclass
class KeywordTrendData:
    """关键词趋势数据"""
    keyword: str
    daily_counts: Dict[date, int]


class KeywordDatabase:
    """
    SQLite 数据库管理器

    用于存储和查询关键词数据，支持：
    - 原始关键词插入和查询
    - 标准化关键词管理
    - 别名映射
    - 趋势统计
    """

    def __init__(self, db_path: Path):
        """
        初始化数据库连接

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """创建数据库表（如不存在）"""
        with self._get_connection() as conn:
            conn.executescript("""
                -- 原始关键词表
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    extracted_date DATE NOT NULL,
                    normalized_keyword_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(keyword, paper_id)
                );

                -- 标准化关键词表
                CREATE TABLE IF NOT EXISTS normalized_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_keyword TEXT NOT NULL UNIQUE,
                    category TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- 别名映射表
                CREATE TABLE IF NOT EXISTS keyword_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_keyword TEXT NOT NULL UNIQUE,
                    normalized_keyword_id INTEGER NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (normalized_keyword_id) REFERENCES normalized_keywords(id)
                );

                -- 每日统计表
                CREATE TABLE IF NOT EXISTS keyword_daily_counts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_keyword_id INTEGER NOT NULL,
                    count_date DATE NOT NULL,
                    paper_count INTEGER DEFAULT 0,
                    FOREIGN KEY (normalized_keyword_id) REFERENCES normalized_keywords(id),
                    UNIQUE(normalized_keyword_id, count_date)
                );

                -- 索引
                CREATE INDEX IF NOT EXISTS idx_keywords_extracted_date ON keywords(extracted_date);
                CREATE INDEX IF NOT EXISTS idx_keywords_normalized ON keywords(normalized_keyword_id);
                CREATE INDEX IF NOT EXISTS idx_daily_counts_date ON keyword_daily_counts(count_date);
                CREATE INDEX IF NOT EXISTS idx_aliases_raw ON keyword_aliases(raw_keyword);
            """)
            conn.commit()

    def insert_keywords(
        self,
        keywords: List[str],
        paper_id: str,
        source: str,
        extracted_date: Optional[date] = None
    ) -> List[int]:
        """
        插入原始关键词

        Args:
            keywords: 关键词列表
            paper_id: 论文ID
            source: 数据源
            extracted_date: 提取日期（默认今天）

        Returns:
            插入的关键词ID列表
        """
        if extracted_date is None:
            extracted_date = date.today()

        inserted_ids = []
        with self._get_connection() as conn:
            for kw in keywords:
                kw_lower = kw.strip().lower()
                if not kw_lower:
                    continue

                try:
                    # 尝试自动关联到已知别名
                    normalized_id = self._find_normalized_id_by_alias(conn, kw_lower)

                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO keywords
                        (keyword, paper_id, source, extracted_date, normalized_keyword_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (kw_lower, paper_id, source, extracted_date.isoformat(), normalized_id)
                    )
                    if cursor.lastrowid:
                        inserted_ids.append(cursor.lastrowid)
                except sqlite3.Error as e:
                    logger.warning(f"插入关键词失败 '{kw}': {e}")

            conn.commit()

        return inserted_ids

    def _find_normalized_id_by_alias(self, conn: sqlite3.Connection, raw_keyword: str) -> Optional[int]:
        """通过别名表查找标准化ID"""
        cursor = conn.execute(
            "SELECT normalized_keyword_id FROM keyword_aliases WHERE raw_keyword = ?",
            (raw_keyword,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_unnormalized_keywords(self, limit: int = 100) -> List[KeywordRecord]:
        """
        获取未标准化的关键词

        Args:
            limit: 最大返回数量

        Returns:
            KeywordRecord 列表
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, keyword, paper_id, source, extracted_date, normalized_keyword_id
                FROM keywords
                WHERE normalized_keyword_id IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            return [
                KeywordRecord(
                    id=row['id'],
                    keyword=row['keyword'],
                    paper_id=row['paper_id'],
                    source=row['source'],
                    extracted_date=date.fromisoformat(row['extracted_date']),
                    normalized_keyword_id=row['normalized_keyword_id']
                )
                for row in cursor.fetchall()
            ]

    def get_unique_unnormalized_keywords(self, limit: int = 100) -> List[str]:
        """
        获取唯一的未标准化关键词（去重）

        Args:
            limit: 最大返回数量

        Returns:
            关键词字符串列表
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT keyword
                FROM keywords
                WHERE normalized_keyword_id IS NULL
                AND keyword NOT IN (SELECT raw_keyword FROM keyword_aliases)
                LIMIT ?
                """,
                (limit,)
            )
            return [row['keyword'] for row in cursor.fetchall()]

    def get_or_create_normalized_keyword(
        self,
        canonical_keyword: str,
        category: Optional[str] = None
    ) -> int:
        """
        获取或创建标准化关键词

        Args:
            canonical_keyword: 标准形式
            category: 分类

        Returns:
            标准化关键词ID
        """
        canonical_lower = canonical_keyword.strip().lower()

        with self._get_connection() as conn:
            # 先查找是否存在
            cursor = conn.execute(
                "SELECT id FROM normalized_keywords WHERE canonical_keyword = ?",
                (canonical_lower,)
            )
            row = cursor.fetchone()
            if row:
                return row[0]

            # 创建新的
            cursor = conn.execute(
                "INSERT INTO normalized_keywords (canonical_keyword, category) VALUES (?, ?)",
                (canonical_lower, category)
            )
            conn.commit()
            return cursor.lastrowid

    def add_keyword_alias(
        self,
        raw_keyword: str,
        normalized_id: int,
        confidence: float = 1.0
    ) -> None:
        """
        添加关键词别名映射

        Args:
            raw_keyword: 原始关键词
            normalized_id: 标准化关键词ID
            confidence: 置信度
        """
        raw_lower = raw_keyword.strip().lower()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO keyword_aliases
                (raw_keyword, normalized_keyword_id, confidence)
                VALUES (?, ?, ?)
                """,
                (raw_lower, normalized_id, confidence)
            )
            conn.commit()

    def link_keywords_to_normalized(
        self,
        raw_keyword: str,
        normalized_id: int
    ) -> int:
        """
        将所有匹配的原始关键词链接到标准化形式

        Args:
            raw_keyword: 原始关键词
            normalized_id: 标准化关键词ID

        Returns:
            更新的记录数
        """
        raw_lower = raw_keyword.strip().lower()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE keywords
                SET normalized_keyword_id = ?
                WHERE keyword = ? AND normalized_keyword_id IS NULL
                """,
                (normalized_id, raw_lower)
            )
            conn.commit()
            return cursor.rowcount

    def get_all_canonical_keywords(self) -> List[str]:
        """获取所有标准化关键词"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT canonical_keyword FROM normalized_keywords ORDER BY canonical_keyword"
            )
            return [row['canonical_keyword'] for row in cursor.fetchall()]

    def update_daily_counts(self, for_date: Optional[date] = None) -> None:
        """
        更新每日统计（基于已标准化的关键词）

        Args:
            for_date: 统计日期（默认今天）
        """
        if for_date is None:
            for_date = date.today()

        with self._get_connection() as conn:
            # 删除当天旧统计
            conn.execute(
                "DELETE FROM keyword_daily_counts WHERE count_date = ?",
                (for_date.isoformat(),)
            )

            # 插入新统计
            conn.execute(
                """
                INSERT INTO keyword_daily_counts (normalized_keyword_id, count_date, paper_count)
                SELECT normalized_keyword_id, ?, COUNT(DISTINCT paper_id)
                FROM keywords
                WHERE normalized_keyword_id IS NOT NULL
                AND extracted_date = ?
                GROUP BY normalized_keyword_id
                """,
                (for_date.isoformat(), for_date.isoformat())
            )
            conn.commit()

    def get_top_keywords(
        self,
        days: int = 30,
        limit: int = 20
    ) -> List[Tuple[str, int, Optional[str]]]:
        """
        获取热门关键词排名

        Args:
            days: 回溯天数
            limit: 返回数量

        Returns:
            列表 [(关键词, 总数, 分类), ...]
        """
        start_date = (date.today() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    nk.canonical_keyword,
                    SUM(kdc.paper_count) as total_count,
                    nk.category
                FROM keyword_daily_counts kdc
                JOIN normalized_keywords nk ON kdc.normalized_keyword_id = nk.id
                WHERE kdc.count_date >= ?
                GROUP BY nk.id
                ORDER BY total_count DESC
                LIMIT ?
                """,
                (start_date, limit)
            )
            return [(row['canonical_keyword'], row['total_count'], row['category'])
                    for row in cursor.fetchall()]

    def get_keyword_trends(
        self,
        days: int = 30,
        keywords: Optional[List[str]] = None,
        limit: int = 10
    ) -> List[KeywordTrendData]:
        """
        获取关键词趋势数据

        Args:
            days: 回溯天数
            keywords: 指定关键词列表（None则取热门）
            limit: 关键词数量限制

        Returns:
            KeywordTrendData 列表
        """
        start_date = date.today() - timedelta(days=days)

        with self._get_connection() as conn:
            # 确定要查询的关键词
            if keywords:
                kw_list = [kw.lower() for kw in keywords]
            else:
                # 获取热门关键词
                top = self.get_top_keywords(days=days, limit=limit)
                kw_list = [kw for kw, _, _ in top]

            if not kw_list:
                return []

            results = []
            for kw in kw_list:
                cursor = conn.execute(
                    """
                    SELECT kdc.count_date, kdc.paper_count
                    FROM keyword_daily_counts kdc
                    JOIN normalized_keywords nk ON kdc.normalized_keyword_id = nk.id
                    WHERE nk.canonical_keyword = ?
                    AND kdc.count_date >= ?
                    ORDER BY kdc.count_date
                    """,
                    (kw, start_date.isoformat())
                )

                daily_counts = {}
                for row in cursor.fetchall():
                    d = date.fromisoformat(row['count_date'])
                    daily_counts[d] = row['paper_count']

                if daily_counts:
                    results.append(KeywordTrendData(keyword=kw, daily_counts=daily_counts))

            return results

    def get_stats(self) -> Dict[str, int]:
        """获取数据库统计信息"""
        with self._get_connection() as conn:
            stats = {}

            cursor = conn.execute("SELECT COUNT(*) FROM keywords")
            stats['total_keywords'] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM keywords WHERE normalized_keyword_id IS NOT NULL")
            stats['normalized_keywords'] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM normalized_keywords")
            stats['canonical_keywords'] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM keyword_aliases")
            stats['aliases'] = cursor.fetchone()[0]

            return stats
