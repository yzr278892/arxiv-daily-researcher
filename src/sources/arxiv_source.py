"""
ArXiv 论文数据源

从 ArXiv 预印本服务器抓取论文，支持 PDF 下载和深度分析。
支持两种搜索模式：
- 分类搜索（daily_report）：按领域分类 + 时间范围
- 关键词搜索（trend_research）：按关键词 + 时间段
"""

import arxiv
import logging
import re
import signal
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .base_source import BasePaperSource, PaperMetadata

logger = logging.getLogger(__name__)


_ARXIV_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:[.-][A-Za-z0-9-]+)*$")


def normalize_arxiv_domains(domains: Optional[List[str]]) -> List[str]:
    """Return a safe, non-empty arXiv category list.

    ``None`` retains the long-standing direct-source default.  An explicit
    empty list is different: it almost always means a configuration mistake,
    and treating it as a successful zero-result scan would advance the daily
    checkpoint while querying no arXiv category at all.

    Category values are also kept deliberately narrow before they are placed
    in an arXiv query string.  This turns malformed UI/manual configuration
    into a visible error instead of a surprising broad or empty query.
    """
    if domains is None:
        return ["quant-ph"]
    if isinstance(domains, (str, bytes)):
        raise ValueError("ArXiv 目标领域必须是分类列表，不能是单个字符串")

    try:
        configured_domains = list(domains)
    except TypeError as exc:
        raise ValueError("ArXiv 目标领域必须是非空分类列表") from exc

    normalized = []
    seen = set()
    for configured_domain in configured_domains:
        if not isinstance(configured_domain, str):
            raise ValueError(
                f"ArXiv 领域代码必须是非空字符串: {configured_domain!r}"
            )
        domain = configured_domain.strip()
        if not domain or not _ARXIV_CATEGORY_PATTERN.fullmatch(domain):
            raise ValueError(
                f"无效的 ArXiv 领域代码: {configured_domain!r}。"
                "示例: quant-ph、cs.AI、physics.optics"
            )
        if domain not in seen:
            normalized.append(domain)
            seen.add(domain)

    if not normalized:
        raise ValueError(
            "ArXiv 已启用但未配置目标领域；请至少设置一个分类，例如 quant-ph 或 cs.AI"
        )
    return normalized


class _ArxivTimeoutError(TimeoutError):
    """ArXiv 抓取超时异常。"""


# 领域重试全部失败后、扫描下一领域前的冷却时间（秒）
_POST_FAILURE_COOLDOWN_SECONDS = 60

# ``cat:<domain>`` 的 updated 查询没有时间下界，只靠降序排序在越过截止日期时
# 停止；活跃领域在恢复性长窗口下可能连续翻很多页，而每页还要强制约 6 秒间隔。
# 给单次 updated 查询一个页数预算，触达上限时记录 WARNING 诊断（正常范围行为不变）。
_ARXIV_MAX_UPDATED_QUERY_PAGES = 100

# SIGALRM 看门狗只能在 POSIX 主线程安装；不可用时原实现会静默失效，
# 因此改为首次禁用时记录一条明确 WARNING（进程内只记一次）。
_TIMEOUT_GUARD_WARNED = False


class ArxivFetchError(RuntimeError):
    """ArXiv 抓取失败异常。

    当 ArXiv API 返回服务端错误（5xx）或其他致命错误，且经过多次重试仍无法获取任何论文时抛出。
    不同于超时或速率限制（这些会自动重试），本异常表示操作已彻底失败。
    """


class ArxivScanReceiptError(ArxivFetchError):
    """Raised when a complete arXiv scan cannot produce an audit receipt."""


class ArxivPageLimitError(ArxivFetchError):
    """A bounded query ended before it reached its time boundary."""


class _timeout_guard:
    """使用 SIGALRM 对阻塞调用设置硬超时（Linux 主线程可用）。

    超时语义是"无进展"看门狗：只要持续收到结果（``touch()`` 重置闹钟），
    多页完整扫描可以合法地跑超过 ``seconds``；只有真正停摆（单次请求
    卡死、分页间无响应）才触发超时。arxiv 客户端在分页之间强制 6 秒
    间隔，把整个扫描窗口当成单次请求的硬超时会在大时间窗下必然误杀。
    """

    def __init__(self, seconds: int):
        self.seconds = max(0, int(seconds or 0))
        self._old_handler = None
        self._enabled = False

    def _disable(self, reason: str) -> None:
        """标记看门狗不可用并说明原因（每个进程只报告一次）。"""
        global _TIMEOUT_GUARD_WARNED
        self._enabled = False
        if _TIMEOUT_GUARD_WARNED:
            return
        _TIMEOUT_GUARD_WARNED = True
        logger.warning(
            "⚠️  [ArXiv] 抓取超时看门狗不可用（%s）：本次抓取不会在请求无进展时"
            "强制超时，卡住的请求只能依赖 arXiv 客户端自身的重试与网络超时。",
            reason,
        )

    def __enter__(self):
        if self.seconds <= 0:
            return self
        if not hasattr(signal, "SIGALRM"):
            self._disable("当前平台不支持 signal.SIGALRM")
            return self
        try:
            self._old_handler = signal.getsignal(signal.SIGALRM)

            def _handler(signum, frame):
                raise _ArxivTimeoutError(f"ArXiv 请求超时（>{self.seconds}s 无进展）")

            signal.signal(signal.SIGALRM, _handler)
            signal.alarm(self.seconds)
            self._enabled = True
        except Exception as exc:
            # ``signal.signal`` only works on the main thread; a silently
            # missing watchdog would hide the loss of that protection.
            self._disable(f"无法安装 SIGALRM 处理器（可能不在主线程）：{exc}")
        return self

    def touch(self):
        """有新结果到达时重置倒计时（仅在启用时生效）。"""
        if self._enabled:
            signal.alarm(self.seconds)

    def __exit__(self, exc_type, exc, tb):
        if self._enabled:
            signal.alarm(0)
            if self._old_handler is not None:
                signal.signal(signal.SIGALRM, self._old_handler)
        return False


def _is_rate_limit_error(exc: BaseException) -> bool:
    """识别 arXiv 限流（429 / Too Many Requests）。"""
    if getattr(exc, "code", None) == 429:
        return True
    error_msg = str(exc)
    return "429" in error_msg or "Too Many Requests" in error_msg


def _retry_after_seconds(exc: BaseException) -> Optional[int]:
    """从 HTTPError 风格异常里提取 Retry-After 提示（秒）。"""
    headers = getattr(exc, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for key in ("Retry-After", "retry-after"):
        try:
            raw = getter(key)
        except Exception:
            raw = None
        if raw:
            try:
                return max(1, int(str(raw).strip()))
            except ValueError:
                continue
    return None


def _arxiv_retry_wait(exc: BaseException, retry_count: int) -> int:
    """按错误类别计算下一次重试前的等待秒数（含 Retry-After 遵从）。

    - 超时/一般错误（含 503 服务端错误）：线性 30s×n，封顶 90s
    - 速率限制：指数 60s×2^(n-1)，封顶 480s
    - 响应头带 Retry-After 且更长时，优先遵从（封顶 600s）
    """
    if isinstance(exc, _ArxivTimeoutError):
        wait = min(30 * retry_count, 90)
    elif _is_rate_limit_error(exc):
        wait = min(60 * (2 ** (retry_count - 1)), 480)
    else:
        wait = min(30 * retry_count, 90)
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        wait = max(wait, min(retry_after, 600))
    return wait


class ArxivSource(BasePaperSource):
    """
    ArXiv 论文数据源。

    特点：
    - 支持按领域分类（如 quant-ph, cs.AI）抓取
    - 支持 PDF 下载，可进行深度分析
    - 使用官方 arxiv Python 库
    - 支持网络代理
    """

    def __init__(
        self,
        history_dir: Path,
        max_results: int = 100,
        proxy_dict: dict = None,
        announcement_lookback_grace_days: int = 2,
        load_legacy_history: bool = True,
    ):
        """
        初始化 ArXiv 数据源。

        参数:
            history_dir: 历史记录存储目录
            max_results: 兼容旧配置的参数。日报抓取不再按数量截断，始终扫描时间窗口内的全部结果。
            proxy_dict: 代理配置字典，如 {"http": "...", "https": "..."}
            announcement_lookback_grace_days: 为公告/API 索引延迟额外回看的天数。
        """
        super().__init__(
            "arxiv", history_dir, load_legacy_history=load_legacy_history
        )
        self.max_results = max_results
        self.announcement_lookback_grace_days = max(
            0, int(announcement_lookback_grace_days)
        )
        # arXiv API 对分页请求有严格的速率要求。max_results 只保留用于兼容旧配置，
        # 日报查询使用 max_results=None，不能因为候选数量达到配置值而漏掉论文。
        self.client = arxiv.Client(page_size=100, delay_seconds=6.0, num_retries=3)

        # 注入代理配置到 arxiv.Client 的内部 requests.Session
        if proxy_dict:
            self.client._session.proxies.update(proxy_dict)
            logger.info(f"[ArXiv] 已配置网络代理: {proxy_dict.get('https', proxy_dict.get('http', 'N/A'))}")

    @property
    def display_name(self) -> str:
        return "ArXiv"

    def can_download_pdf(self) -> bool:
        return True

    @staticmethod
    def _format_api_timestamp(value: datetime) -> str:
        """格式化为 arXiv API 使用的 UTC 时间戳。"""
        return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M")

    @staticmethod
    def _metadata_from_result(result) -> PaperMetadata:
        """将 arXiv 客户端结果转换为统一元数据。"""
        return PaperMetadata(
            paper_id=result.get_short_id(),
            title=result.title,
            authors=[author.name for author in result.authors],
            abstract=result.summary,
            published_date=result.published,
            url=result.entry_id,
            source="arxiv",
            pdf_url=result.pdf_url,
            doi=result.doi,
            categories=list(result.categories) if result.categories else [],
            updated_date=result.updated,
        )

    def _fetch_query_results(
        self,
        search: arxiv.Search,
        cutoff_date: datetime,
        boundary_field: str,
        fetch_timeout_seconds: int,
        page_size: Optional[int] = None,
        max_pages: Optional[int] = None,
        context: str = "",
    ) -> tuple[list, Dict[str, Any]]:
        """
        获取一个无数量上限的查询结果。

        arxiv.Client 会按 page_size 自动分页；这里仅在排序字段早于时间边界时
        停止，因此不会因为历史记录数量或 max_results 配置提前结束。
        ``max_pages`` 为 None 时保持该行为；传入页数预算后，触达预算即停止并
        记录诊断（用于没有时间下界的 updated 查询）。
        """
        results = []
        api_total = 0
        in_window_count = 0
        page_count = 0
        truncated = False
        # arxiv.py does not expose page callbacks.  Its Client results iterator
        # still walks pages in fixed ``client.page_size`` chunks, so derive the
        # number of observed result pages from consumed API entries.  This is
        # exact for full pages and intentionally reports one final
        # partial/boundary page when we stop at the first older record.
        configured_page_size = max(
            1, int(page_size or getattr(self.client, "page_size", 100))
        )
        guard = _timeout_guard(fetch_timeout_seconds)
        with guard:
            for result in self.client.results(search):
                guard.touch()
                api_total += 1
                page_count = ((api_total - 1) // configured_page_size) + 1
                boundary = getattr(result, boundary_field)
                if boundary < cutoff_date:
                    # A boundary result on the next page proves the requested
                    # window was completely scanned, even at the page budget.
                    break
                if max_pages is not None and page_count > max_pages:
                    api_total -= 1
                    page_count = max_pages
                    truncated = True
                    logger.warning(
                        "    ⚠️  [ArXiv] 领域 %s 的 updated 查询达到页数上限 %d 页，"
                        "已停止继续分页（已检查约 %d 条 API 条目，截止日期 %s）",
                        context or "?",
                        max_pages,
                        api_total,
                        cutoff_date.isoformat(),
                    )
                    break
                in_window_count += 1
                results.append(result)
        return results, {
            "api_entries_checked": api_total,
            "pages_observed": page_count,
            "window_entries": in_window_count,
            "truncated": truncated,
        }

    @staticmethod
    def _new_domain_receipt(domain: str, searches: tuple) -> Dict[str, Any]:
        """Build a JSON-safe receipt before any request is attempted."""
        return {
            "domain": domain,
            "status": "running",
            "queries": {
                query_kind: {
                    "boundary_field": boundary_field,
                    "api_entries_checked": 0,
                    "pages_observed": 0,
                    "window_entries": 0,
                    "attempts": 0,
                    "error": None,
                }
                for query_kind, _search, boundary_field in searches
            },
            "deduplicated_within_domain": 0,
            "skipped_legacy_history": 0,
            "skipped_already_collected": 0,
            "new_candidates": 0,
            "error": None,
        }

    def _build_scan_receipt(
        self,
        *,
        fetched_at: datetime,
        normal_days: int,
        effective_days: int,
        cutoff_date: datetime,
        domains: List[str],
        domain_receipts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return a complete, non-secret receipt for one arXiv source scan."""
        return {
            "source": "arxiv",
            "status": "succeeded"
            if all(item.get("status") == "succeeded" for item in domain_receipts)
            else "failed",
            "scanned_at": fetched_at.isoformat(),
            # ``requested_scan_days`` is the recovery-aware interval passed by
            # the daily pipeline.  The source-level announcement grace below
            # is additional and is deliberately shown separately.
            "requested_scan_days": normal_days,
            "announcement_lookback_grace_days": self.announcement_lookback_grace_days,
            "effective_days": effective_days,
            "window_start": cutoff_date.isoformat(),
            "window_end": fetched_at.isoformat(),
            "domains": list(domains),
            "domain_receipts": domain_receipts,
            "total_new_candidates": sum(
                int(item.get("new_candidates", 0)) for item in domain_receipts
            ),
        }

    @staticmethod
    def _notify_scan_receipt(
        callback: Optional[Callable[[Dict[str, Any]], None]], receipt: Dict[str, Any]
    ) -> None:
        """Persist a receipt through the caller without hiding callback errors."""
        if callback is not None:
            callback(receipt)

    def fetch_papers(self, days: int, domains: List[str] = None, **kwargs) -> List[PaperMetadata]:
        """
        从 ArXiv 抓取指定领域最近 N 天的论文。

        参数:
            days: 搜索最近 N 天的论文
            domains: ArXiv 领域分类列表，如 ["quant-ph", "cs.AI"]

        返回:
            List[PaperMetadata]: 论文元数据列表
        """
        domains = normalize_arxiv_domains(domains)

        all_papers = {}
        fetched_at = datetime.now(timezone.utc)
        normal_days = max(1, int(days))
        effective_days = normal_days + self.announcement_lookback_grace_days
        cutoff_date = fetched_at - timedelta(days=effective_days)
        now_date = fetched_at

        try:
            from config import settings as _settings

            fetch_timeout_seconds = int(
                kwargs.get(
                    "fetch_timeout_seconds",
                    getattr(_settings, "ARXIV_FETCH_TIMEOUT_SECONDS", 180),
                )
            )
        except Exception:
            fetch_timeout_seconds = int(kwargs.get("fetch_timeout_seconds", 180))

        logger.info("[ArXiv] 开始抓取论文")
        logger.info(f"  目标领域: {domains}")
        logger.info(
            "  时间范围: 配置 %s 天 + 公告延迟回看 %s 天（实际回看 %s 天）",
            normal_days,
            self.announcement_lookback_grace_days,
            effective_days,
        )
        logger.info("  抓取策略: 按提交时间和最后更新时间完整分页（不受 max_results 限制）")

        scan_receipt_callback = kwargs.get("scan_receipt_callback")
        domain_receipts: List[Dict[str, Any]] = []

        # 记录因严重错误失败的领域及其最后错误信息
        failed_domains: list = []

        for domain in domains:
            query = f"cat:{domain}"
            logger.info(f"  正在抓取领域 {domain}...")

            # 第一个查询覆盖时间窗口内首次提交的论文。查询本身带边界，且
            # max_results=None 让 arxiv.Client 继续请求全部分页。
            submitted_query = (
                f"{query} AND submittedDate:"
                f"[{self._format_api_timestamp(cutoff_date)} TO "
                f"{self._format_api_timestamp(now_date)}]"
            )
            searches = (
                (
                    "submitted",
                    arxiv.Search(
                        query=submitted_query,
                        max_results=None,
                        sort_by=arxiv.SortCriterion.SubmittedDate,
                        sort_order=arxiv.SortOrder.Descending,
                    ),
                    "published",
                ),
                (
                    "updated",
                    arxiv.Search(
                        query=query,
                        max_results=None,
                        sort_by=arxiv.SortCriterion.LastUpdatedDate,
                        sort_order=arxiv.SortOrder.Descending,
                    ),
                    "updated",
                ),
            )
            domain_receipt = self._new_domain_receipt(domain, searches)
            domain_receipts.append(domain_receipt)

            # 添加重试机制
            max_retries = 3
            retry_count = 0
            domain_failed = False
            page_budget_failed = False
            last_error_msg = ""

            while retry_count <= max_retries:
                try:
                    domain_papers = {}
                    count = 0
                    skipped_processed = 0
                    skipped_already_collected = 0
                    duplicate_within_domain = 0
                    active_query_kind = None
                    for query_kind, search, boundary_field in searches:
                        active_query_kind = query_kind
                        domain_receipt["queries"][query_kind]["attempts"] += 1
                        # submitted 查询自带时间下界，页数本就有限；updated 查询
                        # 没有下界、只靠降序早停，必须给它一个页数预算。
                        query_results, query_receipt = self._fetch_query_results(
                            search,
                            cutoff_date,
                            boundary_field,
                            fetch_timeout_seconds,
                            max_pages=(
                                _ARXIV_MAX_UPDATED_QUERY_PAGES
                                if query_kind == "updated"
                                else None
                            ),
                            context=domain,
                        )
                        domain_receipt["queries"][query_kind].update(query_receipt)
                        if query_receipt.get("truncated"):
                            raise ArxivPageLimitError(
                                f"领域 {domain} 的 {query_kind} 查询超过 "
                                f"{_ARXIV_MAX_UPDATED_QUERY_PAGES} 页，时间窗口未扫描完整"
                            )
                        domain_receipt["queries"][query_kind]["error"] = None
                        for result in query_results:
                            paper_id = result.get_short_id()

                            # 同一版本可能同时出现在 submitted/updated 查询中。
                            if paper_id in domain_papers:
                                duplicate_within_domain += 1
                                continue

                            # 历史记录只跳过已经成功处理的精确版本；不能用连续已处理
                            # 的数量作为早停条件，否则会漏掉后续的新版本。
                            if self.is_processed(paper_id):
                                skipped_processed += 1
                                continue

                            # A paper can belong to more than one configured
                            # category.  Retain it once but report that cross-
                            # domain de-duplication explicitly, instead of
                            # making a lower per-domain result look mysterious.
                            if paper_id in all_papers:
                                skipped_already_collected += 1
                                continue

                            domain_papers[paper_id] = self._metadata_from_result(result)

                    all_papers.update(domain_papers)
                    count = len(domain_papers)
                    domain_receipt.update(
                        {
                            "status": "succeeded",
                            "deduplicated_within_domain": duplicate_within_domain,
                            "skipped_legacy_history": skipped_processed,
                            "skipped_already_collected": skipped_already_collected,
                            "new_candidates": count,
                            "error": None,
                        }
                    )

                    api_total = sum(
                        query["api_entries_checked"]
                        for query in domain_receipt["queries"].values()
                    )

                    # 增强诊断日志
                    logger.info(f"    领域 {domain}: 发现 {count} 篇新论文（提交/更新查询 API 结果 {api_total} 条）")
                    if api_total > 0 and count == 0:
                        logger.info(
                            f"    诊断信息: API 返回 {api_total} 篇，"
                            f"已处理跳过 {skipped_processed} 篇，"
                            f"跨领域去重 {skipped_already_collected} 篇"
                        )
                    domain_failed = False
                    break  # 成功则退出重试循环

                except Exception as e:
                    error_msg = str(e)
                    last_error_msg = error_msg
                    if active_query_kind is not None:
                        domain_receipt["queries"][active_query_kind]["error"] = error_msg[:1000]
                    retry_count += 1
                    if isinstance(e, ArxivPageLimitError):
                        logger.error("    领域 %s 抓取不完整: %s", domain, error_msg)
                        domain_failed = True
                        page_budget_failed = True
                        break
                    if retry_count <= max_retries:
                        wait_time = _arxiv_retry_wait(e, retry_count)
                        if isinstance(e, _ArxivTimeoutError):
                            reason = f"抓取超时（{fetch_timeout_seconds}s 无进展）"
                        elif _is_rate_limit_error(e):
                            reason = "遇到速率限制"
                        else:
                            reason = f"抓取出错: {error_msg}"
                        logger.warning(
                            f"    领域 {domain} {reason}，"
                            f"{wait_time} 秒后重试 ({retry_count}/{max_retries})"
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(
                            f"    领域 {domain} 抓取失败（已重试 {max_retries} 次）: {error_msg}"
                        )
                        domain_failed = True
                        break

            if domain_failed:
                domain_receipt.update(
                    {
                        "status": "failed",
                        "error": last_error_msg or "ArXiv domain scan failed",
                    }
                )
                failed_domains.append((domain, last_error_msg))
                # arXiv 限流按 IP 计；一个领域打满重试仍失败时，先冷却
                # 再扫描下一领域，避免连环触发限流把剩余领域也拖垮。
                if domain != domains[-1] and not page_budget_failed:
                    logger.warning(
                        f"    领域 {domain} 失败后冷却 {_POST_FAILURE_COOLDOWN_SECONDS}s "
                        f"再继续下一领域"
                    )
                    time.sleep(_POST_FAILURE_COOLDOWN_SECONDS)

        # The per-domain `new_candidates` counters intentionally show how
        # many entries were unique at the point that category was scanned.
        # The source-level count below is the authoritative cross-category
        # total, because one paper may be listed in several categories.

        papers = list(all_papers.values())
        receipt = self._build_scan_receipt(
            fetched_at=fetched_at,
            normal_days=normal_days,
            effective_days=effective_days,
            cutoff_date=cutoff_date,
            domains=domains,
            domain_receipts=domain_receipts,
        )
        receipt["total_new_candidates"] = len(papers)
        try:
            self._notify_scan_receipt(scan_receipt_callback, receipt)
        except Exception as exc:
            # Without the receipt there is no durable evidence that every
            # configured category was scanned.  Fail closed before reports or
            # watermarks can be committed.
            raise ArxivScanReceiptError(f"无法持久化 ArXiv 扫描收据: {exc}") from exc
        logger.info(f"[ArXiv] 总计发现 {len(papers)} 篇新论文")

        # 任意领域失败都必须明确报错。部分领域成功不能伪装成完整日报，
        # 否则漏抓会被当作“当天没有论文”，并在之后失去补抓机会。
        if failed_domains:
            domain_errors = "; ".join(f"{d}({e})" for d, e in failed_domains)
            raise ArxivFetchError(
                f"ArXiv 抓取未完成，失败领域及错误: {domain_errors}；"
                f"已暂存 {len(papers)} 篇结果，下一次运行应重试失败领域"
            )

        return papers

    def search_by_keywords(
        self,
        keywords: List[str],
        date_from: date,
        date_to: date,
        sort_order: str = "ascending",
        max_results: int = 500,
        categories: Optional[List[str]] = None,
    ) -> List[PaperMetadata]:
        """
        按关键词和时间范围搜索 ArXiv 论文（研究趋势模式专用）。

        使用 all: 字段搜索（标题+摘要+全文），多个关键词用 AND 连接。
        时间范围通过 submittedDate:[YYYYMMDD TO YYYYMMDD] 过滤。
        可选地通过 cat: 限制搜索分类，多个分类用 OR 连接。
        不查询历史记录，不去重，每次独立执行。

        参数:
            keywords: 搜索关键词列表
            date_from: 开始日期
            date_to: 结束日期
            sort_order: 排序方向，"ascending"(旧→新) 或 "descending"(新→旧)
            max_results: 最大结果数（0 = 不限制）
            categories: ArXiv 分类列表，如 ["quant-ph", "cond-mat"]；空列表则不限制分类

        返回:
            按发表时间排序的论文列表
        """
        # 构建查询：多个关键词用 AND 连接，每个关键词用 all: 搜索
        keyword_parts = []
        for kw in keywords:
            # 如果关键词包含空格，用引号包裹做短语匹配
            if " " in kw:
                keyword_parts.append(f'all:"{kw}"')
            else:
                keyword_parts.append(f"all:{kw}")
        keyword_query = " AND ".join(keyword_parts)

        # 分类过滤（可选）：多个分类用 OR 连接
        if categories:
            cat_parts = [f"cat:{c}" for c in categories]
            if len(cat_parts) == 1:
                cat_query = cat_parts[0]
            else:
                cat_query = f"({' OR '.join(cat_parts)})"
            keyword_query = f"({keyword_query}) AND {cat_query}"

        # 时间范围过滤（ArXiv 格式：YYYYMMDDTTTT）
        date_from_str = date_from.strftime("%Y%m%d") + "0000"
        date_to_str = date_to.strftime("%Y%m%d") + "2359"
        date_filter = f"submittedDate:[{date_from_str} TO {date_to_str}]"

        full_query = f"({keyword_query}) AND {date_filter}"

        arxiv_sort_order = (
            arxiv.SortOrder.Ascending if sort_order == "ascending" else arxiv.SortOrder.Descending
        )

        logger.debug(f"[ArXiv] 关键词查询: {full_query}")

        search = arxiv.Search(
            query=full_query,
            max_results=max_results if max_results > 0 else None,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv_sort_order,
        )

        papers = []
        try:
            from config import settings as _settings

            fetch_timeout_seconds = int(getattr(_settings, "ARXIV_FETCH_TIMEOUT_SECONDS", 180))
        except Exception:
            fetch_timeout_seconds = 180

        max_retries = 3
        retry_count = 0
        last_error: Exception | None = None

        while retry_count <= max_retries:
            papers = []  # 每次重试前清空，防止重复积累
            try:
                guard = _timeout_guard(fetch_timeout_seconds)
                with guard:
                    for result in self.client.results(search):
                        guard.touch()
                        paper_id = result.get_short_id()

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
                            categories=list(result.categories) if result.categories else [],
                        )
                        papers.append(metadata)

                logger.info(f"[ArXiv] 关键词搜索完成: 共 {len(papers)} 篇论文")
                last_error = None
                break

            except Exception as e:
                error_msg = str(e)
                last_error = e
                # 与领域扫描同一套退避策略：超时/限流/服务端错误都重试，
                # 退避时长遵从 Retry-After（存在且更长时）。
                retry_count += 1
                if retry_count <= max_retries:
                    wait_time = _arxiv_retry_wait(e, retry_count)
                    if isinstance(e, _ArxivTimeoutError):
                        reason = f"关键词搜索超时（{fetch_timeout_seconds}s 无进展）"
                    elif _is_rate_limit_error(e):
                        reason = "关键词搜索遇到速率限制"
                    else:
                        reason = f"关键词搜索出错: {error_msg}"
                    logger.warning(
                        f"  {reason}，{wait_time} 秒后重试 ({retry_count}/{max_retries})"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"  关键词搜索失败（已重试 {max_retries} 次）: {error_msg}"
                    )
                    break

        if last_error is not None:
            raise ArxivFetchError(f"ArXiv 关键词搜索失败: {last_error}") from last_error

        return papers

    def fetch_domain_papers_between(
        self,
        date_from: date,
        date_to: date,
        domains: Optional[List[str]] = None,
    ) -> List[PaperMetadata]:
        """按提交日期闭区间完整扫描领域论文（旧历史时间段扫描/过去日期补跑）。

        与每日抓取同一套退避策略和分页看门狗；结果按 paper_id 跨领域去重，
        不做任何已处理过滤（调用方负责与账本比对）。
        """
        domains = normalize_arxiv_domains(domains)
        window_start = datetime.combine(
            date_from, datetime.min.time(), tzinfo=timezone.utc
        )
        date_to_str = date_to.strftime("%Y%m%d") + "2359"
        date_from_str = date_from.strftime("%Y%m%d") + "0000"

        try:
            from config import settings as _settings

            fetch_timeout_seconds = int(getattr(_settings, "ARXIV_FETCH_TIMEOUT_SECONDS", 180))
        except Exception:
            fetch_timeout_seconds = 180

        logger.info(
            "[ArXiv] 时间段扫描: %s 至 %s（领域: %s）",
            date_from.isoformat(), date_to.isoformat(), domains,
        )

        all_papers: Dict[str, PaperMetadata] = {}
        max_retries = 3
        for domain in domains:
            retry_count = 0
            last_error: Exception | None = None
            while retry_count <= max_retries:
                try:
                    search = arxiv.Search(
                        query=(
                            f"cat:{domain} AND submittedDate:"
                            f"[{date_from_str} TO {date_to_str}]"
                        ),
                        max_results=None,
                        sort_by=arxiv.SortCriterion.SubmittedDate,
                        sort_order=arxiv.SortOrder.Descending,
                    )
                    results, _receipt = self._fetch_query_results(
                        search, window_start, "published", fetch_timeout_seconds
                    )
                    for result in results:
                        paper_id = result.get_short_id()
                        if paper_id in all_papers:
                            continue
                        all_papers[paper_id] = self._metadata_from_result(result)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    retry_count += 1
                    if retry_count <= max_retries:
                        wait_time = _arxiv_retry_wait(e, retry_count)
                        reason = (
                            f"时间段扫描超时（{fetch_timeout_seconds}s 无进展）"
                            if isinstance(e, _ArxivTimeoutError)
                            else f"时间段扫描出错: {e}"
                        )
                        logger.warning(
                            f"  {reason}，{wait_time} 秒后重试 ({retry_count}/{max_retries})"
                        )
                        time.sleep(wait_time)
            if last_error is not None:
                raise ArxivFetchError(
                    f"ArXiv 时间段扫描失败（领域 {domain}）: {last_error}"
                ) from last_error

        logger.info("[ArXiv] 时间段扫描完成: 共 %s 篇", len(all_papers))
        return list(all_papers.values())

    def fetch_papers_by_ids(
        self, paper_ids: List[str]
    ) -> Dict[str, PaperMetadata]:
        """按 arXiv ID 批量抓取元数据（补充积压重试缺卡片论文）。

        返回 {canonical_id: metadata}；请求了但 API 未返回的 ID 不在结果里，
        由调用方决定记失败还是跳过。
        """
        wanted = [str(pid).strip() for pid in paper_ids if str(pid).strip()]
        if not wanted:
            return {}

        try:
            from config import settings as _settings

            fetch_timeout_seconds = int(getattr(_settings, "ARXIV_FETCH_TIMEOUT_SECONDS", 180))
        except Exception:
            fetch_timeout_seconds = 180

        found: Dict[str, PaperMetadata] = {}
        max_retries = 3
        # arXiv id_list 单次请求上限宽松，分块保持与分页一致的请求规模。
        for offset in range(0, len(wanted), 50):
            chunk = wanted[offset : offset + 50]
            retry_count = 0
            last_error: Exception | None = None
            while retry_count <= max_retries:
                try:
                    search = arxiv.Search(id_list=chunk)
                    guard = _timeout_guard(fetch_timeout_seconds)
                    with guard:
                        for result in self.client.results(search):
                            guard.touch()
                            metadata = self._metadata_from_result(result)
                            canonical = metadata.canonical_id or metadata.paper_id
                            found[canonical] = metadata
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    retry_count += 1
                    if retry_count <= max_retries:
                        wait_time = _arxiv_retry_wait(e, retry_count)
                        logger.warning(
                            f"  按 ID 抓取出错: {e}，{wait_time} 秒后重试 "
                            f"({retry_count}/{max_retries})"
                        )
                        time.sleep(wait_time)
            if last_error is not None:
                raise ArxivFetchError(
                    f"ArXiv 按 ID 抓取失败: {last_error}"
                ) from last_error
        return found
