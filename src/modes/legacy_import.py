"""v3.2 historical import with an explicit lightweight/full-repair switch.

The default import is intentionally small and safe: it reads arXiv cards
already present in old HTML reports and records their exact deliveries in
SQLite. A full repair additionally imports compatibility metadata, repairs
missing fields in historical reports, scans SQLite-covered dates for omissions
and produces natural-week supplement reports.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from notifications import NotifierAgent, WorkflowResult
from utils.daily_research_store import DailyResearchStore
from utils.history_maintenance import resolve_history_maintenance_paper_limit
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY, import_legacy_history
from utils.run_lock import daily_workflow_gate, legacy_import_activity_gate, run_lock

logger = logging.getLogger("LegacyImport")


def _save_summary(store: DailyResearchStore, summary: dict[str, Any]) -> None:
    """Persist a user-visible summary without hiding already imported data."""
    try:
        store.set_app_state(
            LEGACY_IMPORT_STATE_KEY, json.dumps(summary, ensure_ascii=False)
        )
    except Exception as exc:
        logger.warning("旧历史导入汇总写入失败: %s", exc)


def _load_summary(store: DailyResearchStore) -> dict[str, Any]:
    """Read the latest summary while tolerating legacy/corrupt values."""
    try:
        raw = store.get_app_state(LEGACY_IMPORT_STATE_KEY)
        loaded = json.loads(raw) if raw else None
    except (TypeError, ValueError, json.JSONDecodeError):
        loaded = None
    return loaded if isinstance(loaded, dict) else {}


def _make_progress_callback(store: DailyResearchStore, run_id: str):
    """Build a throttled durable heartbeat for the status panel and logs."""
    last_phase: Optional[str] = None
    last_write_at = 0.0

    def report(*, phase: str, detail: str = "", current=None, total=None) -> None:
        nonlocal last_phase, last_write_at
        now = time.monotonic()
        complete = (
            isinstance(current, int)
            and isinstance(total, int)
            and total >= 0
            and current >= total
        )
        if phase == last_phase and not complete and now - last_write_at < 1.0:
            return
        try:
            store.record_run_phase(
                run_id,
                phase,
                detail=detail,
                current=current,
                total=total,
            )
            last_phase = phase
            last_write_at = now
        except Exception as exc:  # pragma: no cover - diagnostics must not stop import
            logger.debug("旧历史导入进度写入失败: %s", exc)

    return report


def _effective_full_repair(full_repair: Optional[bool]) -> bool:
    if full_repair is None:
        return bool(getattr(settings, "LEGACY_IMPORT_FULL_REPAIR_ENABLED", False))
    if not isinstance(full_repair, bool):
        raise ValueError("完整修复开关必须是布尔值")
    return full_repair


def run_import(
    full_repair: Optional[bool] = None,
) -> tuple[int, str, dict[str, Any]]:
    """Run HTML/compatibility import, leaving optional repair steps to caller.

    Keeping the parent ``legacy_import`` run open lets the WebUI and run log
    show one continuous user action while nested repair/report tasks keep
    their own durable rows and retry state.
    """
    enabled = _effective_full_repair(full_repair)
    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    run_id = store.start_run(0, run_kind="legacy_import")
    progress_callback = _make_progress_callback(store, run_id)
    mode_label = "完整修复" if enabled else "轻量建账"
    progress_callback(phase="legacy_import", detail=f"开始读取旧版本历史（{mode_label}）")
    summary: dict[str, Any] = {
        "full_repair_enabled": enabled,
        "history_repair": {"state": "pending" if enabled else "skipped"},
        "supplement": {"state": "pending" if enabled else "skipped"},
        "omission_scan": {"state": "pending" if enabled else "skipped"},
    }
    try:
        imported = import_legacy_history(
            store,
            history_dir=settings.HISTORY_DIR,
            reports_html_dir=settings.REPORTS_DIR / "daily_research" / "html",
            delivery_run_id=run_id,
            legacy_keywords_db_path=settings.KEYWORD_DB_PATH,
            progress_logger=logger,
            progress_callback=progress_callback,
            full_repair=enabled,
        )
        summary.update(imported)
        summary["full_repair_enabled"] = enabled
        _save_summary(store, summary)
        logger.info(
            "[LegacyImport] %s 导入阶段完成：报告 %s，卡片 %s/%s，交付账本 %s",
            mode_label,
            summary.get("reports_scanned", 0),
            summary.get("cards_selected", 0),
            summary.get("cards_found", 0),
            summary.get("delivered_ledger_rows", 0),
        )
        return 0, run_id, summary
    except KeyboardInterrupt:
        error = "用户中断旧历史导入；已写入的数据会保留，后续可继续读取"
        logger.warning(error)
        summary["error"] = error
        summary["finished_at"] = datetime.now().isoformat()
        try:
            store.fail_run(run_id, error)
        except Exception as finish_exc:
            logger.warning("旧历史导入中断状态写入失败: %s", finish_exc)
        _save_summary(store, summary)
        return 130, run_id, summary
    except Exception as exc:
        error = str(exc)
        logger.exception("旧历史导入失败")
        summary["error"] = error
        summary["finished_at"] = datetime.now().isoformat()
        try:
            store.fail_run(run_id, error)
        except Exception as finish_exc:
            logger.warning("旧历史导入失败状态写入失败: %s", finish_exc)
        _save_summary(store, summary)
        return 1, run_id, summary


def _run_automatic_supplement(
    store: DailyResearchStore,
    summary: dict[str, Any],
    *,
    progress_callback=None,
    paper_limit: Optional[int] = None,
) -> int:
    """Drain a bounded non-omission compatibility backlog into reports.

    Missing report cards from legacy JSON history have no original HTML to
    patch, so they remain ordinary supplement reports. ``missed_scan`` rows
    are deliberately excluded: their own workflow groups reports by real
    calendar week and must not be consumed by unordered generic batches.
    The caller owns both workflow gates.
    """
    reasons = {"missing_data", "missing_translation", "missing_analysis"}
    effective_limit = resolve_history_maintenance_paper_limit(paper_limit)
    try:
        pending_before = int(
            store.supplement_backlog_summary(reasons=reasons).get("pending", 0) or 0
        )
    except Exception as exc:
        logger.warning("无法读取补充积压，跳过自动补充报告: %s", exc)
        pending_before = 0

    supplement = summary.setdefault("supplement", {})
    supplement.update(
        {
            "reasons": sorted(reasons),
            "pending_before": pending_before,
            "processed": 0,
            "batches": [],
            "paper_limit": effective_limit,
            "deferred_by_limit": False,
        }
    )
    if pending_before <= 0:
        supplement.update({"state": "not_needed", "pending_after": 0})
        _save_summary(store, summary)
        logger.info("旧历史完整修复没有无报告兼容数据，无需生成普通补充报告")
        return 0

    supplement["state"] = "running"
    _save_summary(store, summary)
    logger.info(
        "[LegacySupplement] 自动处理 %s 条无报告历史数据；本次历史维护上限为 %s",
        pending_before,
        effective_limit or "不限",
    )

    from modes.daily_research import DailyResearchPipeline

    batch_index = 0
    try:
        while True:
            batch_pending_before = int(
                store.supplement_backlog_summary(reasons=reasons).get("pending", 0) or 0
            )
            if batch_pending_before <= 0:
                supplement.update({"state": "completed", "pending_after": 0})
                break
            if effective_limit and supplement["processed"] >= effective_limit:
                supplement.update(
                    {
                        "state": "deferred_by_limit",
                        "pending_after": batch_pending_before,
                        "deferred_by_limit": True,
                    }
                )
                logger.info(
                    "[LegacySupplement] 已达到本次历史维护上限 %s 篇，保留 %s 条积压",
                    effective_limit,
                    batch_pending_before,
                )
                break

            batch_index += 1
            batch_limit = (
                0 if not effective_limit else effective_limit - supplement["processed"]
            )
            if progress_callback is not None:
                progress_callback(
                    phase="legacy_supplement",
                    detail=f"运行第 {batch_index} 份无报告历史补充报告，待处理 {batch_pending_before} 篇",
                    current=batch_index - 1,
                )
            logger.info(
                "[LegacySupplement] 第 %s 批开始：待处理 %s 篇",
                batch_index,
                batch_pending_before,
            )
            result = DailyResearchPipeline().run(
                run_kind="supplement",
                supplement_reasons=reasons,
                paper_limit=batch_limit,
            )
            batch = {
                "pending_before": batch_pending_before,
                "processed": int(getattr(result, "total_papers_fetched", 0) or 0),
                "report_paths": {
                    key: str(value)
                    for key, value in (getattr(result, "report_paths", {}) or {}).items()
                },
            }
            supplement["batches"].append(batch)
            supplement["processed"] += batch["processed"]

            if getattr(result, "interrupted", False):
                supplement.update(
                    {
                        "state": "interrupted",
                        "error": "补充报告被中断；无报告历史数据会在下次完整修复时重试",
                    }
                )
                _save_summary(store, summary)
                return 130
            if getattr(result, "success", None) is not True:
                supplement.update(
                    {
                        "state": "failed",
                        "error": str(
                            getattr(result, "error_message", "补充报告未成功完成")
                        )[:4000],
                    }
                )
                _save_summary(store, summary)
                logger.error("[LegacySupplement] 第 %s 批失败：%s", batch_index, supplement["error"])
                return 1

            batch_pending_after = int(
                store.supplement_backlog_summary(reasons=reasons).get("pending", 0) or 0
            )
            batch["pending_after"] = batch_pending_after
            supplement["pending_after"] = batch_pending_after
            _save_summary(store, summary)
            logger.info(
                "[LegacySupplement] 第 %s 批完成：处理 %s 篇，剩余 %s 篇",
                batch_index,
                batch["processed"],
                batch_pending_after,
            )
            if batch_pending_after >= batch_pending_before:
                supplement.update(
                    {
                        "state": "retry_pending",
                        "error": "本批没有完成可交付论文；剩余数据已保留，后续完整修复会重试",
                    }
                )
                _save_summary(store, summary)
                logger.warning("[LegacySupplement] 本批无进展，停止避免重复空跑")
                break
    except KeyboardInterrupt:
        supplement.update(
            {
                "state": "interrupted",
                "error": "补充报告被中断；无报告历史数据会在下次完整修复时重试",
            }
        )
        _save_summary(store, summary)
        return 130
    except Exception as exc:
        supplement.update({"state": "failed", "error": str(exc)[:4000]})
        _save_summary(store, summary)
        logger.exception("[LegacySupplement] 自动补充报告异常终止")
        return 1

    supplement.setdefault(
        "pending_after",
        int(store.supplement_backlog_summary(reasons=reasons).get("pending", 0) or 0),
    )
    _save_summary(store, summary)
    return 0


def _run_full_repair_steps(
    store: DailyResearchStore,
    run_id: str,
    summary: dict[str, Any],
    progress_callback,
) -> int:
    """Run repair, cardless supplements and natural-week omission reports."""
    from modes.history_data_repair import run_history_data_repair
    from modes.history_omission_scan import run_history_omission_scan

    exit_codes: list[int] = []
    progress_callback(phase="history_repair", detail="检查 SQLite 中已交付报告的缺失字段")
    repair_code, repair_run_id, repair_summary = run_history_data_repair(
        store=store, notify=False
    )
    summary["history_repair"] = {
        "state": "completed" if repair_code == 0 else "degraded",
        "run_id": repair_run_id,
        **repair_summary,
    }
    exit_codes.append(repair_code)
    _save_summary(store, summary)
    logger.info(
        "[LegacyImport] 历史数据补全结束：候选 %s，待重试 %s，退出码 %s",
        repair_summary.get("candidates", 0),
        repair_summary.get("pending_after", 0),
        repair_code,
    )
    if repair_code == 130:
        return 130

    progress_callback(phase="legacy_supplement", detail="处理旧 JSON 中没有报告卡片的论文")
    supplement_code = _run_automatic_supplement(
        store, summary, progress_callback=progress_callback
    )
    exit_codes.append(supplement_code)
    if supplement_code == 130:
        return 130

    progress_callback(phase="history_omission_scan", detail="扫描 SQLite 历史范围中的遗漏论文")
    omission_code, omission_run_id, omission_summary = run_history_omission_scan(
        store=store, notify=False
    )
    summary["omission_scan"] = {
        "state": "completed" if omission_code == 0 else "degraded",
        "run_id": omission_run_id,
        **omission_summary,
    }
    exit_codes.append(omission_code)
    _save_summary(store, summary)
    logger.info(
        "[LegacyImport] 历史遗漏扫描结束：发现 %s，剩余 %s，退出码 %s",
        (omission_summary.get("scan") or {}).get("missed_found", 0),
        omission_summary.get("pending_after", 0),
        omission_code,
    )
    return 1 if any(code != 0 for code in exit_codes) else 0


def _legacy_import_notification_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Build the concise summary for the one complete-import notification."""
    fields: dict[str, Any] = {
        "导入模式": "完整修复" if summary.get("full_repair_enabled") else "轻量建账",
        "扫描 HTML 报告": summary.get("reports_scanned", 0),
        "读取报告卡片": summary.get("cards_selected", summary.get("cards_found", 0)),
        "写入 SQLite": summary.get("imported", 0),
        "交付账本": summary.get("delivered_ledger_rows", 0),
        "保留已有新数据": summary.get("skipped_existing_newer", 0),
    }
    repair = summary.get("history_repair")
    if isinstance(repair, dict) and summary.get("full_repair_enabled"):
        fields["历史数据补全"] = (
            f"检查 {repair.get('candidates', 0)} 篇；"
            f"待重试 {repair.get('pending_after', 0)} 篇"
        )
    supplement = summary.get("supplement")
    if isinstance(supplement, dict) and summary.get("full_repair_enabled"):
        fields["无报告历史补充"] = (
            f"{supplement.get('state', 'unknown')}；处理 {supplement.get('processed', 0)} 篇；"
            f"剩余 {supplement.get('pending_after', supplement.get('pending_before', 0))} 篇"
        )
    omission = summary.get("omission_scan")
    if isinstance(omission, dict) and summary.get("full_repair_enabled"):
        scan = omission.get("scan") if isinstance(omission.get("scan"), dict) else {}
        fields["遗漏自然周补充"] = (
            f"扫描遗漏 {scan.get('missed_found', 0)} 篇；"
            f"剩余 {omission.get('pending_after', 0)} 篇"
        )
    return fields


def _legacy_import_issues(summary: dict[str, Any]) -> list[str]:
    """Expose granular degradations without embedding full run logs."""
    issues: list[str] = []
    errors = summary.get("errors")
    if isinstance(errors, list):
        issues.extend(str(error)[:1000] for error in errors if error)
    for key, label in (
        ("history_repair", "历史数据补全"),
        ("supplement", "无报告历史补充"),
        ("omission_scan", "历史遗漏扫描"),
    ):
        stage = summary.get(key)
        if not isinstance(stage, dict):
            continue
        error = stage.get("error")
        if error:
            issues.append(f"{label}：{str(error)[:1000]}")
        raw_issues = stage.get("issues")
        if isinstance(raw_issues, list):
            issues.extend(
                f"{label}：{str(item)[:1000]}" for item in raw_issues[:6] if item
            )
        if stage.get("pending_after"):
            issues.append(f"{label}仍有 {stage['pending_after']} 条待重试")
    return issues


def _notify_legacy_import_result(run_id: str, summary: dict[str, Any], exit_code: int) -> None:
    """Persist/send one notification for the user-clicked import workflow."""
    if not settings.ENABLE_NOTIFICATIONS:
        return
    result = WorkflowResult(
        workflow="旧历史导入",
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        success=exit_code == 0,
        interrupted=exit_code == 130,
        summary=_legacy_import_notification_summary(summary),
        issues=_legacy_import_issues(summary),
        error_message=str(summary.get("error") or "") or None,
    )
    try:
        store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
        notifier = NotifierAgent()
        created = notifier.enqueue_workflow_result(store, run_id, result)
        delivery = notifier.deliver_pending_workflow_results(store)
        logger.info(
            "旧历史导入通知：新建 %s，发送 %s，待补发 %s",
            created,
            delivery["sent"],
            delivery["deferred"],
        )
    except Exception as exc:
        logger.warning("旧历史导入通知写入/发送失败: %s", exc)


def main(full_repair: Optional[bool] = None) -> int:
    """Run one queued legacy-import request after related work becomes idle."""
    enabled = _effective_full_repair(full_repair)
    with run_lock("legacy_import"):
        # Use the same lock order as daily/backfill/supplement workers. This
        # is a real queue point: a click while another task is active waits and
        # starts automatically once it is safe to touch shared SQLite data.
        with daily_workflow_gate(
            logger=logger,
            wait_note="旧历史导入等待每日研究、补充报告或过去日报完成",
        ):
            with legacy_import_activity_gate(
                exclusive=True,
                logger=logger,
                wait_note="旧历史导入等待趋势分析及其他任务空闲",
            ):
                import_code, run_id, summary = run_import(enabled)
                if import_code == 0 and enabled:
                    store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
                    progress = _make_progress_callback(store, run_id)
                    workflow_code = _run_full_repair_steps(store, run_id, summary, progress)
                else:
                    workflow_code = import_code
                if import_code == 0 and not enabled:
                    summary.setdefault("history_repair", {"state": "skipped"})
                    summary.setdefault("supplement", {"state": "skipped"})
                    summary.setdefault("omission_scan", {"state": "skipped"})

                store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
                if workflow_code == 130:
                    summary.setdefault("error", "用户中断旧历史导入流程；未完成数据会在下次继续")
                    try:
                        store.fail_run(run_id, summary["error"])
                    except Exception:
                        logger.warning("旧历史导入中断收尾状态写入失败", exc_info=True)
                elif workflow_code == 0:
                    try:
                        store.complete_run(run_id, {})
                    except Exception:
                        logger.warning("旧历史导入运行收尾失败", exc_info=True)
                else:
                    # The HTML ledger may already be durable, but a complete
                    # repair is still a single user-visible workflow.  Do not
                    # show it as successful when a nested repair/scan/report
                    # step returned a failure; its detailed summary remains
                    # retryable and the parent run records a concise cause.
                    summary.setdefault(
                        "error",
                        "旧历史完整修复有步骤未完成；已保留已导入数据和待重试项",
                    )
                    try:
                        store.fail_run(run_id, summary["error"])
                    except Exception:
                        logger.warning("旧历史导入失败收尾状态写入失败", exc_info=True)
                summary["finished_at"] = datetime.now().isoformat()
                _save_summary(store, summary)

        _notify_legacy_import_result(run_id, summary, workflow_code)
        return workflow_code


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    sys.exit(main())
