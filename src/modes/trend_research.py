"""
研究趋势分析主流程

独立于每日研究模式（modes/daily_research.py），实现完整的研究趋势分析流水线：
1. 按关键词 + 时间范围从 ArXiv 搜索论文
2. 为每篇论文生成 LLM TLDR（无评分）
3. 使用 Skills 系统进行整体趋势分析
4. 生成 Markdown + HTML 报告
5. 发送通知
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Any

from tqdm import tqdm

from config import settings
from utils.logger import setup_logger
from utils.token_counter import token_counter
from sources.arxiv_source import ArxivSource
from agents.trend_agent import TrendAgent
from report.trend.reporter import TrendReporter
from notifications import NotifierAgent

logger = setup_logger("TrendResearch")


class TrendResearchPipeline:
    """
    研究趋势分析流水线。

    参数:
        settings: 全局配置对象
        keywords: 搜索关键词列表
        date_from: 搜索起始日期
        date_to: 搜索截至日期
        sort_order: 排序方向 (ascending / descending)
        max_results: 最大论文数
    """

    def __init__(
        self,
        settings,
        keywords: List[str],
        date_from: date,
        date_to: date,
        sort_order: str = "ascending",
        max_results: int = 500,
    ):
        self.settings = settings
        self.keywords = keywords
        self.date_from = date_from
        self.date_to = date_to
        self.sort_order = sort_order
        self.max_results = max_results

    def run(self):
        """执行研究趋势分析完整流程"""
        try:
            print("\n" + "=" * 80)
            print("🔬 研究趋势分析模式启动")
            print("=" * 80 + "\n")

            logger.info("=" * 80)
            logger.info("启动研究趋势分析模式")
            logger.info(f"  关键词: {self.keywords}")
            logger.info(f"  时间范围: {self.date_from} ~ {self.date_to}")
            logger.info(f"  排序方式: {self.sort_order}")
            logger.info(f"  最大结果数: {self.max_results}")
            logger.info("=" * 80)

            if settings.TOKEN_TRACKING_ENABLED:
                token_counter.reset()

            # ==================== 阶段1: 搜索论文 ====================
            logger.info(">>> 阶段1: 从 ArXiv 搜索论文...")

            arxiv_source = ArxivSource(
                history_dir=self.settings.HISTORY_DIR,
                max_results=self.max_results,
            )

            papers = arxiv_source.search_by_keywords(
                keywords=self.keywords,
                date_from=self.date_from,
                date_to=self.date_to,
                sort_order=self.sort_order,
                max_results=self.max_results,
            )

            if not papers:
                logger.info("未搜索到任何论文。")
                print("\n未搜索到任何论文，程序退出。")
                self._send_result_notification(total_papers=0, report_paths={}, success=True)
                return

            logger.info(f"搜索到 {len(papers)} 篇论文")
            print(f"  搜索到 {len(papers)} 篇论文")

            # ==================== 阶段2: 生成 TLDR ====================
            tldrs: Dict[str, str] = {}
            trend_agent = TrendAgent()

            if self.settings.RESEARCH_GENERATE_TLDR:
                logger.info(">>> 阶段2: 生成论文 TLDR...")

                if self.settings.ENABLE_CONCURRENCY and len(papers) > 1:
                    tldrs = self._generate_tldrs_concurrent(trend_agent, papers)
                else:
                    tldrs = self._generate_tldrs_sequential(trend_agent, papers)
            else:
                logger.info(">>> 阶段2: 跳过 TLDR 生成（配置关闭）")

            tldr_count = sum(1 for v in tldrs.values() if v)
            if self.settings.RESEARCH_GENERATE_TLDR:
                logger.info(f"  TLDR 生成完成: {tldr_count}/{len(papers)} 篇成功")

            # ==================== 阶段3: 趋势分析 ====================
            logger.info(">>> 阶段3: 执行趋势分析...")
            print(f"  执行趋势分析 ({len(self.settings.RESEARCH_ENABLED_SKILLS)} 个技能)...")

            trend_analysis = trend_agent.analyze_trends(
                keywords=self.keywords,
                papers=papers,
                date_from=self.date_from,
                date_to=self.date_to,
                tldrs=tldrs,
            )

            analysis_count = len(trend_analysis)
            logger.info(f"  趋势分析完成: {analysis_count} 个技能产生了结果")

            # ==================== 阶段4: 生成报告 ====================
            logger.info(">>> 阶段4: 生成研究趋势报告...")

            reporter = TrendReporter()
            report_paths = reporter.render(
                papers=papers,
                tldrs=tldrs,
                trend_analysis=trend_analysis,
                keywords=self.keywords,
                date_from=self.date_from,
                date_to=self.date_to,
                sort_order=self.sort_order,
                token_usage=token_counter.get_summary() if settings.TOKEN_TRACKING_ENABLED else None,
            )

            # ==================== 阶段5: 发送通知 ====================
            self._send_result_notification(
                total_papers=len(papers),
                report_paths=report_paths,
                success=True,
                trend_skills_count=analysis_count,
                tldr_count=tldr_count,
                token_usage=token_counter.get_summary() if settings.TOKEN_TRACKING_ENABLED else None,
            )

            # ==================== 完成 ====================
            logger.info("=" * 80)
            logger.info("✅ 研究趋势分析完成！")
            logger.info("=" * 80)

            print("\n" + "=" * 80)
            print("🎉 研究趋势分析完成！")
            print("=" * 80)
            print("📊 统计信息:")
            print(f"   • 关键词: {', '.join(self.keywords)}")
            print(f"   • 时间范围: {self.date_from} ~ {self.date_to}")
            print(f"   • 搜索到论文: {len(papers)} 篇")
            print(f"   • TLDR 生成: {tldr_count} 篇")
            print(f"   • 趋势分析维度: {analysis_count} 个")
            print("\n📁 报告位置:")
            for fmt, path in report_paths.items():
                print(f"   • [{fmt}] {path}")
            print("=" * 80 + "\n")

        except KeyboardInterrupt:
            logger.warning("\n用户中断程序执行")
            print("\n⚠️  程序已被用户中断")
        except Exception as e:
            logger.error(f"研究趋势分析出错: {e}", exc_info=True)
            print(f"\n❌ 研究趋势分析失败: {e}")
            import traceback

            traceback.print_exc()

            self._send_error_notification(str(e))
            raise

    # ==================== TLDR 生成辅助 ====================

    def _generate_tldrs_sequential(self, agent: TrendAgent, papers: list) -> Dict[str, str]:
        """顺序生成 TLDR"""
        tldrs = {}
        with tqdm(total=len(papers), desc="📝 生成 TLDR", unit="篇", ncols=100) as pbar:
            for paper in papers:
                tldr = agent.generate_tldr(paper)
                if tldr:
                    tldrs[paper.paper_id] = tldr
                pbar.update(1)
        return tldrs

    def _generate_tldrs_concurrent(self, agent: TrendAgent, papers: list) -> Dict[str, str]:
        """并发生成 TLDR"""
        tldrs = {}
        workers = min(self.settings.CONCURRENCY_WORKERS, self.settings.RESEARCH_TLDR_BATCH_SIZE)
        logger.info(f"  使用并发模式 (workers={workers})")

        with tqdm(total=len(papers), desc="📝 生成 TLDR", unit="篇", ncols=100) as pbar:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(agent.generate_tldr, paper): paper for paper in papers}
                for future in as_completed(futures):
                    paper = futures[future]
                    try:
                        tldr = future.result()
                        if tldr:
                            tldrs[paper.paper_id] = tldr
                    except Exception as e:
                        logger.error(f"TLDR 生成异常 ({paper.title[:30]}...): {e}")
                    pbar.update(1)
        return tldrs

    # ==================== 通知 ====================

    def _send_result_notification(
        self,
        total_papers: int,
        report_paths: Dict[str, Any],
        success: bool,
        trend_skills_count: int = 0,
        tldr_count: int = 0,
        token_usage: Dict[str, Any] = None,
    ):
        """发送研究趋势分析结果通知"""
        if not self.settings.ENABLE_NOTIFICATIONS:
            return

        try:
            from notifications.notifier import _load_template, _render_template, TrendRunResult

            result = TrendRunResult(
                run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                keywords=self.keywords,
                date_from=str(self.date_from),
                date_to=str(self.date_to),
                total_papers=total_papers,
                tldr_count=tldr_count,
                trend_skills_count=trend_skills_count,
                report_paths={k: str(v) for k, v in report_paths.items()},
                success=success,
                token_usage=token_usage or {},
            )

            notifier = NotifierAgent()
            notifier.notify_trend(result)
        except Exception as e:
            logger.warning(f"通知发送失败: {e}")

    def _send_error_notification(self, error_msg: str):
        """发送错误通知"""
        if not self.settings.ENABLE_NOTIFICATIONS:
            return

        try:
            notifier = NotifierAgent()
            notifier.notify_error(
                "error_generic",
                error_type="研究趋势分析错误",
                error_message=error_msg,
                context=f"关键词: {', '.join(self.keywords)}, 时间: {self.date_from}~{self.date_to}",
            )
        except Exception:
            pass
