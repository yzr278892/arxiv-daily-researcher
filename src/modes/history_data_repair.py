"""SQLite-driven repair of missing fields in already delivered reports.

This task never discovers missing fields by reading HTML.  It queries delivered
SQLite rows, fills only the absent stage (TL;DR / translation / analysis), then
patches the report artifacts recorded in the delivery ledger.  Failed stages
and failed file patches remain durable retry candidates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from agents import AnalysisAgent, KeywordAgent
from config import settings
from notifications import NotifierAgent, WorkflowResult
from sources.base_source import PaperMetadata
from utils.daily_research_fingerprints import (
    build_score_audit_metadata,
    build_stage_input_fingerprints,
)
from utils.daily_research_store import DailyResearchStore
from utils.history_maintenance import resolve_history_maintenance_paper_limit
from utils.history_report_patch import patch_historical_reports
from utils.llm_health import make_llm_health_recorder
from utils.token_counter import token_counter

logger = logging.getLogger("HistoryDataRepair")

HISTORY_DATA_REPAIR_STATE_KEY = "history_data_repair_summary"


def _safe_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if value else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _paper_metadata(payload: Dict[str, Any], source: str) -> PaperMetadata:
    """Restore a stored metadata record with a clear failure for bad rows."""
    data = dict(payload or {})
    data["source"] = str(data.get("source") or source)
    try:
        return PaperMetadata.from_dict(data)
    except Exception as exc:
        raise ValueError(f"SQLite 论文元数据不完整: {exc}") from exc


def _score_payload(record: Any) -> Dict[str, Any]:
    try:
        raw = record["score_json"]
    except (KeyError, IndexError, TypeError):
        raw = None
    return _safe_payload(raw)


def _analysis_payload(record: Any) -> Dict[str, Any]:
    try:
        raw = record["analysis_json"]
    except (KeyError, IndexError, TypeError):
        raw = None
    return _safe_payload(raw)


def _current_repairs_needed(record: Any, paper: PaperMetadata) -> set[str]:
    """Derive stage needs from current SQLite values after each repair."""
    score = _score_payload(record)
    needs: set[str] = set()
    score_ready = bool(record and record["score_status"] == "succeeded" and score)
    if not score_ready:
        needs.add("score")
        return needs
    if record["tldr_status"] != "succeeded" or not str(score.get("tldr") or "").strip():
        needs.add("tldr")
    if paper.abstract and paper.abstract.strip() and (
        record["translation_status"] != "succeeded"
        or not str(record["abstract_cn"] or "").strip()
    ):
        needs.add("translation")
    if (
        settings.DAILY_ENABLE_DEEP_ANALYSIS
        and bool(score.get("is_qualified", False))
        and paper.has_pdf_access()
        and (
            record["analysis_status"] != "succeeded"
            or not _analysis_payload(record)
        )
    ):
        needs.add("analysis")
    return needs


def _save_summary(store: DailyResearchStore, summary: Dict[str, Any]) -> None:
    try:
        store.set_app_state(HISTORY_DATA_REPAIR_STATE_KEY, json.dumps(summary, ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - observability must not lose repaired data
        logger.warning("历史数据补全汇总写入失败: %s", exc)


def _notify_result(
    store: DailyResearchStore,
    run_id: str,
    summary: Dict[str, Any],
    exit_code: int,
) -> None:
    if not settings.ENABLE_NOTIFICATIONS:
        return
    issues = list(summary.get("issues") or [])
    if summary.get("deferred_by_limit"):
        issues.append(
            f"已达到本次历史维护上限 {summary.get('paper_limit')} 篇，剩余论文会在下次运行时继续"
        )
    if summary.get("pending_after"):
        issues.append(f"仍有 {summary['pending_after']} 篇历史论文待补全或待写回报告")
    result = WorkflowResult(
        workflow="历史数据补全",
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        success=exit_code == 0,
        interrupted=exit_code == 130,
        summary={
            "检查论文": summary.get("candidates", 0),
            "补全评分": summary.get("repaired", {}).get("score", 0),
            "补全 TL;DR": summary.get("repaired", {}).get("tldr", 0),
            "补全翻译": summary.get("repaired", {}).get("translation", 0),
            "补全深度分析": summary.get("repaired", {}).get("analysis", 0),
            "修补报告": summary.get("report_files_patched", 0),
            "待重试": summary.get("pending_after", 0),
        },
        issues=issues,
        error_message=str(summary.get("error") or "") or None,
    )
    try:
        notifier = NotifierAgent()
        created = notifier.enqueue_workflow_result(store, run_id, result)
        delivery = notifier.deliver_pending_workflow_results(store)
        logger.info(
            "历史数据补全通知：新建 %s，发送 %s，待补发 %s",
            created,
            delivery["sent"],
            delivery["deferred"],
        )
    except Exception as exc:
        logger.warning("历史数据补全通知写入/发送失败: %s", exc)


def run_history_data_repair(
    *,
    store: Optional[DailyResearchStore] = None,
    notify: bool = True,
    paper_limit: Optional[int] = None,
) -> tuple[int, str, Dict[str, Any]]:
    """Run one bounded SQLite history repair pass.

    ``notify=False`` is used by full legacy import so its caller can emit one
    consolidated workflow notification instead of a notification for every
    internal step. ``paper_limit`` is an internal/test override; normal calls
    use ``history_maintenance.max_papers_per_run``.
    """
    store = store or DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
    effective_limit = resolve_history_maintenance_paper_limit(paper_limit)
    run_id = store.start_run(0, run_kind="history_data_repair")
    summary: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "candidates": 0,
        "repaired": {"score": 0, "tldr": 0, "translation": 0, "analysis": 0},
        "report_files_patched": 0,
        "report_patch_failures": 0,
        "stage_failures": 0,
        "issues": [],
        "pending_after": 0,
        "paper_limit": effective_limit,
        "deferred_by_limit": False,
    }
    agent: Optional[AnalysisAgent] = None
    all_keywords: Optional[Dict[str, float]] = None
    learned_terms: Optional[Dict[str, Dict[str, float]]] = None
    try:
        if settings.TOKEN_TRACKING_ENABLED:
            token_counter.reset()
        store.record_run_phase(run_id, "history_repair", detail="从 SQLite 检查已交付历史")
        candidates = store.history_repair_candidates(
            include_deep_analysis=bool(settings.DAILY_ENABLE_DEEP_ANALYSIS),
            limit=effective_limit,
        )
        summary["candidates"] = len(candidates)
        store.set_run_total(run_id, len(candidates))
        logger.info(
            "[HistoryRepair] SQLite 检查完成：本次处理 %s 篇需要补全或写回报告的论文（上限：%s）",
            len(candidates),
            effective_limit or "不限",
        )
        if not candidates:
            summary["finished_at"] = datetime.now().isoformat()
            store.complete_run(run_id, {})
            _save_summary(store, summary)
            if notify:
                _notify_result(store, run_id, summary, 0)
            logger.info("[HistoryRepair] SQLite 历史完整，无需补全")
            return 0, run_id, summary

        llm_health = make_llm_health_recorder(store)
        for index, candidate in enumerate(candidates, start=1):
            source = str(candidate["source"])
            paper_id = str(candidate["paper_id"])
            store.record_run_phase(
                run_id,
                "history_repair",
                detail=f"检查 {source}:{paper_id}",
                current=index - 1,
                total=len(candidates),
            )
            original_needs = set(candidate.get("needs") or [])
            changed: set[str] = set()
            stage_error = False
            try:
                paper = _paper_metadata(candidate.get("paper_json") or {}, source)
            except Exception as exc:
                summary["stage_failures"] += 1
                detail = f"{source}:{paper_id} 元数据损坏：{exc}"
                summary["issues"].append(detail)
                store.update_error(run_id, source, paper_id, detail, stage="score")
                logger.warning("[HistoryRepair] %s", detail)
                store.record_run_phase(
                    run_id,
                    "history_repair",
                    detail=f"{source}:{paper_id} 元数据不可用，已保留待重试",
                    current=index,
                    total=len(candidates),
                )
                continue

            record = store.get_paper_record(source, paper_id)
            needs = _current_repairs_needed(record, paper)
            try:
                if "score" in needs:
                    if all_keywords is None:
                        keyword_agent = KeywordAgent()
                        attach = getattr(keyword_agent, "set_health_recorder", None)
                        if callable(attach):
                            attach(llm_health)
                        all_keywords = keyword_agent.get_all_keywords()
                        if not all_keywords:
                            raise RuntimeError("未配置可用关键词，无法补全缺失评分")
                        try:
                            from modes.daily_research import _load_learned_terms

                            learned_terms = _load_learned_terms(store)
                        except Exception:
                            learned_terms = None
                    if agent is None:
                        agent = AnalysisAgent(health_recorder=llm_health)
                    score_response = agent.score_paper_with_keywords(
                        paper.title,
                        paper.authors,
                        paper.abstract,
                        all_keywords,
                        learned_terms=learned_terms,
                    )
                    fingerprints = build_stage_input_fingerprints(
                        paper, all_keywords, getattr(agent, "deep_template", {})
                    )
                    store.update_score(
                        run_id,
                        source,
                        {
                            "paper_metadata": paper,
                            "paper_id": paper_id,
                            "score_response": score_response,
                        },
                        score_input_fingerprint=fingerprints.get("score"),
                        score_audit_metadata=build_score_audit_metadata(
                            paper, all_keywords, fingerprints.get("score")
                        ),
                    )
                    summary["repaired"]["score"] += 1
                    changed.add("score")
                    record = store.get_paper_record(source, paper_id)
                    needs = _current_repairs_needed(record, paper)

                if "tldr" in needs:
                    if agent is None:
                        agent = AnalysisAgent(health_recorder=llm_health)
                    tldr = agent.generate_tldr(paper.title, paper.abstract)
                    store.update_score_tldr(run_id, source, paper_id, tldr)
                    summary["repaired"]["tldr"] += 1
                    changed.add("tldr")
                    record = store.get_paper_record(source, paper_id)
                    needs = _current_repairs_needed(record, paper)

                if "translation" in needs:
                    if agent is None:
                        agent = AnalysisAgent(health_recorder=llm_health)
                    translation = agent.translate_abstract(paper.abstract)
                    store.update_translation(run_id, source, paper_id, translation)
                    summary["repaired"]["translation"] += 1
                    changed.add("translation")
                    record = store.get_paper_record(source, paper_id)
                    needs = _current_repairs_needed(record, paper)

                if "analysis" in needs:
                    if agent is None:
                        agent = AnalysisAgent(health_recorder=llm_health)
                    analysis = agent.deep_analyze(
                        paper.title,
                        str(paper.get_best_pdf_url() or ""),
                        paper.abstract,
                    )
                    if not analysis:
                        raise RuntimeError("深度分析未返回可写入结果")
                    store.update_analysis(run_id, source, paper_id, analysis)
                    summary["repaired"]["analysis"] += 1
                    changed.add("analysis")
                    record = store.get_paper_record(source, paper_id)
                    needs = _current_repairs_needed(record, paper)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                # Determine the narrowest known stage so the next repair run
                # resumes that one field instead of invalidating other data.
                failed_stage = next(
                    (stage for stage in ("score", "tldr", "translation", "analysis") if stage in needs),
                    "score",
                )
                store.update_error(run_id, source, paper_id, str(exc), stage=failed_stage)
                summary["stage_failures"] += 1
                stage_error = True
                detail = f"{source}:{paper_id} {failed_stage} 补全失败：{exc}"
                summary["issues"].append(detail[:1000])
                logger.warning("[HistoryRepair] %s", detail)

            # Persisted data may be useful even when another field failed.
            # Patch every available repaired value now, then retain the still
            # missing stage for the next pass.
            record = store.get_paper_record(source, paper_id)
            patch_needed = bool(changed) or "report_patch" in original_needs
            if patch_needed and record is not None:
                score = _score_payload(record)
                analysis = _analysis_payload(record)
                store.set_history_report_repair_status(source, paper_id, "pending")
                patch_result = patch_historical_reports(
                    store.report_paths_for_paper(source, paper_id),
                    source=source,
                    paper_id=paper_id,
                    canonical_id=str(candidate.get("canonical_id") or paper.canonical_id or paper_id),
                    paper=candidate.get("paper_json") or {},
                    tldr=str(score.get("tldr") or ""),
                    abstract_cn=str(record["abstract_cn"] or ""),
                    analysis=analysis,
                )
                summary["report_files_patched"] += int(patch_result.get("patched", 0) or 0)
                if patch_result.get("success"):
                    store.set_history_report_repair_status(source, paper_id, "succeeded")
                else:
                    errors = "; ".join(str(item) for item in patch_result.get("errors") or [])
                    store.set_history_report_repair_status(source, paper_id, "failed", errors)
                    summary["report_patch_failures"] += 1
                    detail = (
                        f"{source}:{paper_id} 报告写回失败："
                        f"{errors or '未找到可更新的报告卡片'}"
                    )
                    summary["issues"].append(detail)
                    logger.warning("[HistoryRepair] %s", detail)

            if not stage_error:
                logger.info(
                    "[HistoryRepair] %s/%s %s:%s：补全 %s",
                    index,
                    len(candidates),
                    source,
                    paper_id,
                    "、".join(sorted(changed)) or "仅重试报告写回",
                )
            store.record_run_phase(
                run_id,
                "history_repair",
                detail=f"已处理 {source}:{paper_id}",
                current=index,
                total=len(candidates),
            )

        summary["pending_after"] = store.history_repair_summary(
            include_deep_analysis=bool(settings.DAILY_ENABLE_DEEP_ANALYSIS)
        )["pending"]
        summary["deferred_by_limit"] = bool(
            effective_limit
            and len(candidates) >= effective_limit
            and summary["pending_after"]
        )
        summary["finished_at"] = datetime.now().isoformat()
        exit_code = 0 if not (
            summary["stage_failures"] or summary["report_patch_failures"]
        ) else 1
        # ``daily_runs`` is also the status-panel source of truth.  Marking a
        # degraded repair pass completed made a failed patch/API call look
        # healthy after its heartbeat disappeared, even though its SQLite row
        # was intentionally retained for retry.  The detailed per-paper
        # issues stay in the summary and run log; the concise terminal error
        # makes the durable run state equally truthful.
        if exit_code == 0:
            store.complete_run(run_id, {})
        else:
            summary.setdefault(
                "error",
                "历史数据补全有步骤未完成；已保留缺失字段和报告写回项供下次重试",
            )
            store.fail_run(run_id, summary["error"])
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, exit_code)
        logger.info(
            "[HistoryRepair] 完成：检查 %s，评分 %s，TL;DR %s，翻译 %s，分析 %s，报告文件 %s，待重试 %s，退出码 %s",
            summary["candidates"],
            summary["repaired"]["score"],
            summary["repaired"]["tldr"],
            summary["repaired"]["translation"],
            summary["repaired"]["analysis"],
            summary["report_files_patched"],
            summary["pending_after"],
            exit_code,
        )
        return exit_code, run_id, summary
    except KeyboardInterrupt:
        summary["error"] = "用户中断历史数据补全；未完成字段会在下次运行时重试"
        summary["finished_at"] = datetime.now().isoformat()
        try:
            store.fail_run(run_id, summary["error"])
        except Exception:
            logger.warning("历史数据补全中断状态写入失败", exc_info=True)
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, 130)
        return 130, run_id, summary
    except Exception as exc:
        summary["error"] = str(exc)
        summary["finished_at"] = datetime.now().isoformat()
        logger.exception("历史数据补全异常终止")
        try:
            store.fail_run(run_id, str(exc))
        except Exception:
            logger.warning("历史数据补全失败状态写入失败", exc_info=True)
        _save_summary(store, summary)
        if notify:
            _notify_result(store, run_id, summary, 1)
        return 1, run_id, summary
    finally:
        try:
            usage = token_counter.get_summary().get("by_model") or {}
            if usage:
                store.record_token_usage(run_id, usage, mode="history_data_repair")
        except Exception:
            logger.debug("历史数据补全 Token 用量记录失败", exc_info=True)
