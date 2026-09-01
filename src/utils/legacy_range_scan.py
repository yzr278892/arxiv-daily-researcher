"""SQLite 驱动的历史时间段扫描：寻找各来源漏掉的论文。

历史 HTML 只在首次导入时被解析并写入 SQLite。后续的覆盖范围、已知论文
身份与遗漏积压全部以 SQLite 为准，避免报告目录发生移动、清理或格式变化
后改变扫描结果。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# 单次查询的时间窗口：按月分块，避免超大区间的深层分页。
SCAN_CHUNK_DAYS = 31
# Do not retain an archive-sized list of paper payloads while scanning.  A
# historical range can legitimately contain many results even though normal
# operation processes them later in small supplement batches.
BACKLOG_WRITE_BATCH_SIZE = 250


def _known_source_identities(store: Any, source: str) -> Set[Tuple[str, int]]:
    """SQLite 中已知的某一来源身份（论文行、交付账本、补充积压）。"""
    known: Set[Tuple[str, int]] = set()
    with store._connect() as conn:
        for table in ("daily_papers", "paper_deliveries", "supplement_backlog"):
            rows = conn.execute(
                f"SELECT canonical_id, version FROM {table} "
                "WHERE source = ? AND canonical_id != ''",
                (source,),
            ).fetchall()
            for row in rows:
                known.add((row["canonical_id"], int(row["version"] or 0)))
    return known


def _known_arxiv_identities(store: Any) -> Set[Tuple[str, int]]:
    """Backward-compatible arXiv identity helper."""
    return _known_source_identities(store, "arxiv")


def _month_chunks(start: date, end: date) -> List[Tuple[date, date]]:
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=SCAN_CHUNK_DAYS - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def scan_source_range(
    store: Any,
    *,
    source: str = "arxiv",
    history_dir: Optional[Path] = None,
    fetch_between: Callable[[date, date], List[Any]],
    logger_override: Optional[Any] = None,
    idle_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Scan one source's imported report-time range and queue unknown papers.

    ``fetch_between(date_from, date_to)`` returns metadata for the inclusive
    interval. The caller supplies the provider-specific implementation so the
    same scanner works for arXiv, OpenAlex journals and dated feeds.
    """
    normalized_source = str(source or "").strip().lower()
    if not normalized_source:
        raise ValueError("source must be non-empty")
    log = logger_override or logger
    summary: Dict[str, Any] = {
        "range_start": None,
        "range_end": None,
        "chunks_scanned": 0,
        "papers_scanned": 0,
        "missed_found": 0,
        "backlog_queued": 0,
        "skipped_reason": None,
        "failed_chunks": 0,
        "errors": [],
    }

    def emit(detail: str, current: Optional[int] = None, total: Optional[int] = None) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(
                phase="legacy_scan",
                detail=detail,
                current=current,
                total=total,
            )
        except Exception as exc:  # pragma: no cover - UI observation is optional
            log.debug("[LegacyScan] 进度回调失败: %s", exc)

    # ``history_dir`` is kept as a no-op compatibility argument for callers
    # from v3.2/v4.0. It must never become an input to the range again: the
    # durable delivery ledger's imported report timestamps are authoritative.
    del history_dir
    date_range = store.historical_delivery_date_range(normalized_source)
    if date_range is None:
        summary["skipped_reason"] = (
            f"SQLite 中没有已交付的 {normalized_source} 历史，无法确定扫描时间段"
        )
        log.info("[LegacyScan] %s", summary["skipped_reason"])
        emit(summary["skipped_reason"], 0, 0)
        return summary
    start, end = date_range
    summary["range_start"] = start.isoformat()
    summary["range_end"] = end.isoformat()

    known = _known_source_identities(store, normalized_source)
    chunks = _month_chunks(start, end)
    log.info(
        "[LegacyScan][%s] 扫描 %s 至 %s（共 %s 个分块，SQLite 已知身份 %s 个）",
        normalized_source,
        start,
        end,
        len(chunks),
        len(known),
    )
    emit(
        f"扫描 {start} 至 {end}（共 {len(chunks)} 个分块）",
        0,
        len(chunks),
    )

    pending_backlog: List[Dict[str, Any]] = []

    def flush_backlog() -> None:
        """Persist discoveries incrementally so a large scan stays bounded."""
        if not pending_backlog:
            return
        entries = list(pending_backlog)
        pending_backlog.clear()
        try:
            summary["backlog_queued"] += store.record_supplement_backlog(entries)
        except Exception as exc:
            # Some earlier batches may already be durable. Stop rather than
            # silently continuing with an incomplete set of omissions.
            raise RuntimeError(f"遗漏论文写入积压失败: {exc}") from exc

    for chunk_index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        if idle_check is not None:
            idle_check()
        emit(
            f"扫描第 {chunk_index}/{len(chunks)} 个分块：{chunk_start} 至 {chunk_end}",
            chunk_index - 1,
            len(chunks),
        )
        try:
            papers = fetch_between(chunk_start, chunk_end)
        except Exception as exc:
            # A historical range can span years. One temporary DNS/API outage
            # should not discard the already imported archive or prevent its
            # automatically queued supplement reports. Keep failed chunks
            # explicit so the next import click can retry the missing range.
            summary["failed_chunks"] += 1
            error = f"{chunk_start} 至 {chunk_end}: {exc}"
            summary["errors"].append(error)
            log.exception(
                "[LegacyScan][%s] 分块 %s/%s 失败（%s），继续后续分块",
                normalized_source,
                chunk_index,
                len(chunks),
                error,
            )
            emit(
                f"第 {chunk_index}/{len(chunks)} 个分块失败，已记录待下次重试",
                chunk_index,
                len(chunks),
            )
            continue
        summary["chunks_scanned"] += 1
        summary["papers_scanned"] += len(papers)
        chunk_missed = 0
        for paper in papers:
            paper_source = str(getattr(paper, "source", "") or "").strip().lower()
            if paper_source != normalized_source:
                raise ValueError(
                    f"来源历史扫描返回错误来源：请求 {normalized_source}，得到 {paper_source or '空'}"
                )
            canonical = (getattr(paper, "canonical_id", None) or paper.paper_id).strip()
            version = int(getattr(paper, "version", None) or 0)
            if (canonical, version) in known:
                continue
            known.add((canonical, version))
            chunk_missed += 1
            summary["missed_found"] += 1
            pending_backlog.append(
                {
                    "source": normalized_source,
                    "canonical_id": canonical,
                    "version": version,
                    "paper_id": paper.paper_id,
                    "reason": "missed_scan",
                    "detail": f"时间段扫描发现（{chunk_start}~{chunk_end} 提交）",
                    "paper_json": paper.to_dict(),
                }
            )
            if len(pending_backlog) >= BACKLOG_WRITE_BATCH_SIZE:
                flush_backlog()
        log.info(
            "[LegacyScan][%s] 分块 %s/%s（%s~%s）: %s 篇，本块遗漏 %s 篇",
            normalized_source,
            chunk_index,
            len(chunks),
            chunk_start,
            chunk_end,
            len(papers),
            chunk_missed,
        )
        emit(
            f"已完成第 {chunk_index}/{len(chunks)} 个分块（{len(papers)} 篇，遗漏 {chunk_missed} 篇）",
            chunk_index,
            len(chunks),
        )

    flush_backlog()
    log.info(
        "[LegacyScan][%s] 扫描完成: 成功分块 %s/%s，失败 %s；%s 篇论文中遗漏 %s 篇，新入积压 %s 篇",
        normalized_source,
        summary["chunks_scanned"],
        len(chunks),
        summary["failed_chunks"],
        summary["papers_scanned"],
        summary["missed_found"],
        summary["backlog_queued"],
    )
    if summary["failed_chunks"]:
        summary["skipped_reason"] = (
            f"{summary['failed_chunks']} 个时间段扫描失败，已记录，后续历史遗漏扫描会重试"
        )
    emit(
        f"时间段扫描完成：遗漏 {summary['missed_found']} 篇，积压 {summary['backlog_queued']} 条"
        + (f"，失败分块 {summary['failed_chunks']} 个" if summary["failed_chunks"] else ""),
        len(chunks),
        len(chunks),
    )
    return summary


def scan_legacy_range(
    store: Any,
    *,
    history_dir: Optional[Path] = None,
    fetch_between: Callable[[date, date], List[Any]],
    logger_override: Optional[Any] = None,
    idle_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for the former arXiv-only scanner."""
    return scan_source_range(
        store,
        source="arxiv",
        history_dir=history_dir,
        fetch_between=fetch_between,
        logger_override=logger_override,
        idle_check=idle_check,
        progress_callback=progress_callback,
    )
