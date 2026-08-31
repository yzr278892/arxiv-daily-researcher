"""Find SQLite-covered historical omissions and build natural-week supplements.

The v3.2 HTML importer is the only component that reads archived reports.
Once a card has been written to SQLite, this workflow uses the delivery ledger,
paper metadata and supplement backlog exclusively.  Each missed paper is
handled with the normal daily-research pipeline, while report batches are
bounded by ``history_maintenance.max_papers_per_run`` and grouped by ISO
calendar week (Monday through Sunday).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from config import settings
from notifications import NotifierAgent, WorkflowResult
from utils.daily_research_store import DailyResearchStore
from utils.history_maintenance import resolve_history_maintenance_paper_limit
from utils.legacy_range_scan import scan_source_range
from utils.token_counter import token_counter

logger = logging.getLogger("HistoryOmissionScan")

HISTORY_OMISSION_SCAN_STATE_KEY = "history_omission_scan_summary"


def _save_summary(store: DailyResearchStore, summary: Dict[str, Any]) -> None:
    try:
        store.set_app_state(
            HISTORY_OMISSION_SCAN_STATE_KEY,
            json.dumps(summary, ensure_ascii=False),
        )
    except Exception as exc:  # pragma: no cover - observability must not lose queue state
        logger.warning("历史遗漏扫描汇总写入失败: %s", exc)


def _notify_result(
    store: DailyResearchStore,
    run_id: str,
    summary: Dict[str, Any],
    exit_code: int,
) -> None:
    if not settings.ENABLE_NOTIFICATIONS:
        return
    scan = summary.get("scan") if isinstance(summary.get("scan"), dict) else {}
    weeks = summary.get("weeks") if isinstance(summary.get("weeks"), list) else []
    batches = sum(int(week.get("batches", 0) or 0) for week in weeks if isinstance(week, dict))
    completed_weeks = sum(
        1 for week in weeks if isinstance(week, dict) and week.get("state") == "completed"
    )
    issues = list(summary.get("issues") or [])
    if summary.get("deferred_by_limit"):
        issues.append(
            f"已达到本次历史维护上限 {summary.get('paper_limit')} 篇，剩余遗漏论文会在下次运行时继续"
        )
    if scan.get("failed_chunks"):
        issues.append(f"{scan['failed_chunks']} 个历史扫描分块失败，后续可再次执行本任务重试")
    result = WorkflowResult(
        workflow="历史遗漏扫描与补充报告",
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        success=exit_code == 0,
        interrupted=exit_code == 130,
        summary={
            "扫描范围": (
                f"{scan.get('range_start') or '—'} 至 {scan.get('range_end') or '—'}"
            ),
            "扫描论文": int(scan.get("papers_scanned", 0) or 0),
            "发现遗漏": int(scan.get("missed_found", 0) or 0),
            "自然周": len(weeks),
            "完成自然周": completed_weeks,
            "补充报告批次": batches,
            "仍待处理": int(summary.get("pending_after", 0) or 0),
        },
        issues=issues,
        error_message=str(summary.get("error") or "") or None,
    )
    try:
        notifier = NotifierAgent()
        created = notifier.enqueue_workflow_result(store, run_id, result)
        delivery = notifier.deliver_pending_workflow_results(store)
        logger.info(
            "历史遗漏扫描通知：新建 %s，发送 %s，待补发 %s",
            created,
            delivery["sent"],
            delivery["deferred"],
        )
    except Exception as exc:
        logger.warning("历史遗漏扫描通知写入/发送失败: %s", exc)


def _progress_callback(store: DailyResearchStore, run_id: str):
    def emit(*, phase: str, detail: str = "", current=None, total=None) -> None:
        try:
            store.record_run_phase(
                run_id,
                phase,
                detail=detail,
                current=current,
                total=total,
            )
        except Exception as exc:  # pragma: no cover - UI heartbeat is optional
            logger.debug("历史遗漏扫描进度写入失败: %s", exc)

    return emit


def _scan_sqlite_history(
    store: DailyResearchStore,
    *,
    run_id: str,
    progress_callback: Callable[..., None],
) -> Dict[str, Any]:
    """Scan every currently available report source without cross-source failure."""
    aggregate: Dict[str, Any] = {
        "range_start": None,
        "range_end": None,
        "chunks_scanned": 0,
        "papers_scanned": 0,
        "missed_found": 0,
        "backlog_queued": 0,
        "failed_chunks": 0,
        "errors": [],
        "sources": {},
        "skipped_reason": None,
    }
    # A complete legacy import is still useful when the operator has paused
    # every live source: it can index cards and repair SQLite fields without
    # making outbound requests.  SearchAgent deliberately rejects an empty
    # source list for normal daily research, but a history omission scan can
    # safely become a successful no-op in that configuration.
    enabled_sources = [
        str(source).strip()
        for source in (getattr(settings, "ENABLED_SOURCES", []) or [])
        if str(source).strip()
    ]
    if not enabled_sources:
        aggregate["skipped_reason"] = "当前未启用数据源，已跳过新的历史遗漏扫描"
        aggregate["skipped_no_enabled_sources"] = True
        return aggregate

    from sources.search_agent import SearchAgent

    def record_optional_source(
        source: str,
        success: bool,
        candidate_count: Optional[int] = None,
        error: Optional[BaseException | str] = None,
    ) -> None:
        try:
            store.record_source_health_event(
                source,
                success,
                run_id=run_id,
                task_kind="history_omission_scan",
                candidate_count=candidate_count,
                error_summary=error,
            )
        except Exception:
            logger.debug("历史扫描来源健康事件写入失败: %s", source, exc_info=True)

    agent = SearchAgent(
        history_dir=settings.HISTORY_DIR,
        enabled_sources=enabled_sources,
        arxiv_domains=settings.TARGET_DOMAINS,
        journals=settings.TARGET_JOURNALS,
        enable_openalex=getattr(settings, "ENABLE_OPENALEX", True),
        openalex_api_key=settings.OPENALEX_API_KEY,
        enable_semantic_scholar=settings.ENABLE_SEMANTIC_SCHOLAR_TLDR,
        semantic_scholar_api_key=settings.SEMANTIC_SCHOLAR_API_KEY,
        extra_source_definitions=getattr(settings, "EXTRA_SOURCE_DEFINITIONS", []),
        use_legacy_history_filter=False,
        source_health_recorder=record_optional_source,
    )
    try:
        for source in agent.get_enabled_sources():
            logger.info("[HistoryOmission][%s] 开始扫描 SQLite 历史范围", source)

            def source_progress(*, phase, detail="", current=None, total=None, _source=source):
                progress_callback(
                    phase=phase,
                    detail=f"[{_source}] {detail}",
                    current=current,
                    total=total,
                )

            def fetch_between(start, end, _source=source):
                def record_receipt(receipt: Dict[str, Any]) -> None:
                    status = receipt.get("status") if isinstance(receipt, dict) else None
                    try:
                        store.record_source_health_event(
                            _source,
                            status == "succeeded",
                            run_id=run_id,
                            task_kind="history_omission_scan",
                            candidate_count=(
                                receipt.get("total_new_candidates")
                                if isinstance(receipt, dict)
                                else None
                            ),
                            error_summary=(
                                DailyResearchStore._extract_receipt_error(receipt)
                                if isinstance(receipt, dict) and status == "failed"
                                else None
                            ),
                            origin_key=(
                                f"history-range:{run_id}:{_source}:"
                                f"{start.isoformat()}:{end.isoformat()}"
                            ),
                        )
                    except Exception:
                        logger.debug(
                            "历史范围扫描收据健康事件写入失败: %s", _source,
                            exc_info=True,
                        )

                return agent.fetch_source_papers_between(
                    _source,
                    start,
                    end,
                    scan_receipt_callback=record_receipt,
                )

            try:
                source_summary = scan_source_range(
                    store,
                    source=source,
                    fetch_between=fetch_between,
                    logger_override=logger,
                    progress_callback=source_progress,
                )
            except Exception as exc:
                logger.exception("[HistoryOmission][%s] 来源扫描失败，继续其他来源", source)
                source_summary = {
                    "range_start": None,
                    "range_end": None,
                    "chunks_scanned": 0,
                    "papers_scanned": 0,
                    "missed_found": 0,
                    "backlog_queued": 0,
                    "failed_chunks": 1,
                    "errors": [str(exc)],
                    "skipped_reason": str(exc),
                }
            aggregate["sources"][source] = source_summary
            for field in (
                "chunks_scanned",
                "papers_scanned",
                "missed_found",
                "backlog_queued",
                "failed_chunks",
            ):
                aggregate[field] += int(source_summary.get(field, 0) or 0)
            for error in source_summary.get("errors", []) or []:
                aggregate["errors"].append(f"{source}: {error}")
            start = source_summary.get("range_start")
            end = source_summary.get("range_end")
            if start and (aggregate["range_start"] is None or start < aggregate["range_start"]):
                aggregate["range_start"] = start
            if end and (aggregate["range_end"] is None or end > aggregate["range_end"]):
                aggregate["range_end"] = end
    finally:
        agent.close()

    if aggregate["failed_chunks"]:
        aggregate["skipped_reason"] = (
            f"{aggregate['failed_chunks']} 个来源时间段扫描失败，后续运行会重试"
        )
    elif aggregate["range_start"] is None:
        aggregate["skipped_reason"] = "SQLite 中没有当前启用来源的已交付历史"
    return aggregate


def run_history_omission_scan(
    *,
    store: Optional[DailyResearchStore] = None,
    notify: bool = True,
    pipeline_factory: Optional[Callable[[], Any]] = None,
    paper_limit: Optional[int] = None,
) -> tuple[int, str, Dict[str, Any]]:
    """Scan SQLite delivery coverage and drain omission rows week by week.

    Callers that expose this as a user task must hold both the daily workflow
    gate and the legacy-import activity gate.  ``notify=False`` is used by a
    full legacy import, which sends one consolidated notification for its
    nested import, repair and omission phases.
    """
    store = store or DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    effective_limit = resolve_history_maintenance_paper_limit(paper_limit)
    run_id = store.start_run(0, run_kind="history_omission_scan")
    progress = _progress_callback(store, run_id)
    summary: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "scan": {},
        "weeks": [],
        "pending_after": 0,
        "issues": [],
        "paper_limit": effective_limit,
        "processed": 0,
        "deferred_by_limit": False,
    }
    if pipeline_factory is None:
        from modes.daily_research import DailyResearchPipeline

        pipeline_factory = DailyResearchPipeline

    try:
        if settings.TOKEN_TRACKING_ENABLED:
            token_counter.reset()

        progress(phase="history_omission_scan", detail="根据 SQLite 交付账本确定历史扫描范围")
        logger.info("[HistoryOmission] 开始按 SQLite 历史范围扫描各启用来源漏项")
        try:
            scan = _scan_sqlite_history(
                store, run_id=run_id, progress_callback=progress
            )
        except Exception as exc:
            # The existing backlog is still useful.  Keep the failure visible
            # and continue draining any previously found natural-week groups.
            logger.exception("[HistoryOmission] 历史范围扫描初始化或执行失败")
            scan = {"errors": [str(exc)], "failed_chunks": 1, "skipped_reason": str(exc)}
        summary["scan"] = scan
        for error in scan.get("errors", []) if isinstance(scan.get("errors"), list) else []:
            summary["issues"].append(f"历史范围扫描：{error}")
        if scan.get("skipped_reason"):
            logger.warning("[HistoryOmission] 扫描说明：%s", scan["skipped_reason"])

        groups = store.missed_scan_week_groups()
        total_pending = sum(groups.values())
        store.set_run_total(
            run_id,
            min(total_pending, effective_limit) if effective_limit else total_pending,
        )
        if not groups:
            summary["pending_after"] = store.supplement_backlog_summary(
                reasons={"missed_scan"}
            )["pending"]
            summary["finished_at"] = datetime.now().isoformat()
            exit_code = 0 if not scan.get("failed_chunks") else 1
            if exit_code == 0:
                store.complete_run(run_id, {})
            else:
                summary.setdefault(
                    "error",
                    "历史遗漏扫描有分块未完成；已保留后续可重试的扫描与补充任务",
                )
                store.fail_run(run_id, summary["error"])
            _save_summary(store, summary)
            if notify:
                _notify_result(store, run_id, summary, exit_code)
            logger.info("[HistoryOmission] 没有可生成补充报告的历史遗漏论文")
            return exit_code, run_id, summary

        logger.info(
            "[HistoryOmission] 待补充遗漏论文 %s 篇，分布在 %s 个自然周",
            total_pending,
            len(groups),
        )
        completed_weeks = 0
        had_batch_failure = False
        processed_total = 0
        for week_index, (week_start, expected_count) in enumerate(groups.items(), start=1):
            if effective_limit and processed_total >= effective_limit:
                summary["deferred_by_limit"] = True
                logger.info(
                    "[HistoryOmission] 已达到本次历史维护上限 %s 篇，保留其余自然周积压",
                    effective_limit,
                )
                break
            week_end = week_start + timedelta(days=6)
            week_summary: Dict[str, Any] = {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "initial_pending": expected_count,
                "batches": 0,
                "processed": 0,
                "report_paths": [],
                "state": "running",
            }
            summary["weeks"].append(week_summary)
            progress(
                phase="history_omission_week",
                detail=f"处理第 {week_index}/{len(groups)} 个自然周：{week_start} 至 {week_end}",
                current=week_index - 1,
                total=len(groups),
            )
            logger.info(
                "[HistoryOmission] 自然周 %s/%s：%s 至 %s，初始积压 %s 篇",
                week_index,
                len(groups),
                week_start,
                week_end,
                expected_count,
            )

            while True:
                pending_before = int(
                    store.supplement_backlog_summary(
                        reasons={"missed_scan"},
                        published_from=week_start,
                        published_to=week_end,
                    )["pending"]
                    or 0
                )
                if pending_before <= 0:
                    week_summary["state"] = "completed"
                    completed_weeks += 1
                    break

                if effective_limit and processed_total >= effective_limit:
                    week_summary["state"] = "deferred_by_limit"
                    week_summary["remaining"] = pending_before
                    summary["deferred_by_limit"] = True
                    break

                week_summary["batches"] += 1
                batch_index = week_summary["batches"]
                batch_limit = (
                    0 if not effective_limit else effective_limit - processed_total
                )
                progress(
                    phase="history_omission_week",
                    detail=(
                        f"自然周 {week_start} 至 {week_end}：运行第 {batch_index} 份补充报告，"
                        f"待处理 {pending_before} 篇"
                    ),
                    current=week_index - 1,
                    total=len(groups),
                )
                # The report's date follows the real calendar week, while
                # its clock is the actual execution time as requested.
                report_stamp = datetime.combine(week_end, datetime.now().time())
                result = pipeline_factory().run(
                    run_kind="supplement",
                    supplement_reasons={"missed_scan"},
                    supplement_week_start=week_start,
                    supplement_week_end=week_end,
                    report_timestamp=report_stamp,
                    paper_limit=batch_limit,
                )
                batch_processed = int(
                    getattr(result, "total_papers_fetched", 0) or 0
                )
                week_summary["processed"] += batch_processed
                processed_total += batch_processed
                summary["processed"] = processed_total
                paths = getattr(result, "report_paths", {}) or {}
                if isinstance(paths, dict) and paths:
                    week_summary["report_paths"].append(
                        {key: str(value) for key, value in paths.items()}
                    )

                if getattr(result, "interrupted", False):
                    week_summary["state"] = "interrupted"
                    summary["error"] = "用户中断历史遗漏补充报告；未完成论文会在下次运行时继续"
                    summary["finished_at"] = datetime.now().isoformat()
                    store.fail_run(run_id, summary["error"])
                    _save_summary(store, summary)
                    if notify:
                        _notify_result(store, run_id, summary, 130)
                    return 130, run_id, summary

                pending_after = int(
                    store.supplement_backlog_summary(
                        reasons={"missed_scan"},
                        published_from=week_start,
                        published_to=week_end,
                    )["pending"]
                    or 0
                )
                week_summary["remaining"] = pending_after
                if getattr(result, "success", None) is not True:
                    had_batch_failure = True
                    week_summary["state"] = "failed"
                    detail = str(
                        getattr(result, "error_message", "补充报告未成功完成")
                        or "补充报告未成功完成"
                    )
                    summary["issues"].append(
                        f"自然周 {week_start} 至 {week_end} 第 {batch_index} 批失败：{detail[:1000]}"
                    )
                    logger.error("[HistoryOmission] %s", summary["issues"][-1])
                    break
                if pending_after >= pending_before:
                    # A report can complete with zero papers when every
                    # candidate is temporarily unfetchable. Do not spin; the
                    # durable failed/pending rows remain retryable.
                    had_batch_failure = True
                    week_summary["state"] = "retry_pending"
                    detail = (
                        f"自然周 {week_start} 至 {week_end} 第 {batch_index} 批没有推进积压；"
                        "已保留待重试数据"
                    )
                    summary["issues"].append(detail)
                    logger.warning("[HistoryOmission] %s", detail)
                    break
                logger.info(
                    "[HistoryOmission] 自然周 %s 至 %s 第 %s 批完成：处理 %s 篇，剩余 %s 篇",
                    week_start,
                    week_end,
                    batch_index,
                    getattr(result, "total_papers_fetched", 0),
                    pending_after,
                )

            progress(
                phase="history_omission_week",
                detail=f"自然周 {week_start} 至 {week_end}：{week_summary['state']}",
                current=week_index,
                total=len(groups),
            )
            _save_summary(store, summary)
            if week_summary["state"] == "deferred_by_limit":
                break

        summary["pending_after"] = int(
            store.supplement_backlog_summary(reasons={"missed_scan"})["pending"] or 0
        )
        summary["finished_at"] = datetime.now().isoformat()
        exit_code = 0 if not (had_batch_failure or scan.get("failed_chunks")) else 1
        if exit_code == 0:
            store.complete_run(run_id, {})
        else:
            summary.setdefault(
                "error",
                "历史遗漏扫描或补充报告有步骤未完成；已保留遗漏论文供下次重试",
            )
            store.fail_run(run_id, summary["error"])
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, exit_code)
        logger.info(
            "[HistoryOmission] 完成：扫描遗漏 %s 篇，完成自然周 %s/%s，剩余 %s 篇",
            scan.get("missed_found", 0),
            completed_weeks,
            len(groups),
            summary["pending_after"],
        )
        return exit_code, run_id, summary
    except KeyboardInterrupt:
        summary["error"] = "用户中断历史遗漏扫描；已入库的遗漏和未完成补充报告会在下次继续"
        summary["finished_at"] = datetime.now().isoformat()
        try:
            store.fail_run(run_id, summary["error"])
        except Exception:
            logger.warning("历史遗漏扫描中断状态写入失败", exc_info=True)
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, 130)
        return 130, run_id, summary
    except Exception as exc:
        summary["error"] = str(exc)
        summary["finished_at"] = datetime.now().isoformat()
        logger.exception("历史遗漏扫描异常终止")
        try:
            store.fail_run(run_id, str(exc))
        except Exception:
            logger.warning("历史遗漏扫描失败状态写入失败", exc_info=True)
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, 1)
        return 1, run_id, summary
    finally:
        try:
            usage = token_counter.get_summary().get("by_model") or {}
            if usage:
                store.record_token_usage(run_id, usage, mode="history_omission_scan")
        except Exception:
            logger.debug("历史遗漏扫描 Token 用量记录失败", exc_info=True)
