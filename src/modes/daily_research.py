"""
每日研究模式主流程

从多个数据源抓取论文，评分、深度分析并生成报告。

工作流程:
1. 加载配置
2. 准备关键词（主要关键词 + Reference 提取的次要关键词）
3. 从多个数据源抓取论文
4. 对所有论文进行加权评分
5. 对 ArXiv 及格论文进行深度分析（其他来源跳过）
6. 按数据源分别生成报告
7. 关键词趋势处理
8. 发送通知
"""

import hashlib
from dataclasses import asdict
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from tqdm import tqdm

from config import settings
from utils.logger import setup_logger
from utils.token_counter import token_counter
from agents import KeywordAgent, AnalysisAgent
from scoring_policy import LEARNED_PREFERENCE_V1, LEGACY_WEIGHTED_KEYWORD_V1
from sources import (
    ArxivFetchError,
    HuggingFacePapersFetchError,
    OpenAlexFetchError,
    PaperMetadata,
    SearchAgent,
    SourceScanReceiptError,
)
from report.daily import Reporter
from notifications import NotifierAgent, RunResult
from utils.daily_research_store import (
    DailyResearchStore,
    V1_PASS_SIGNAL_STRENGTH,
)
from utils.llm_health import make_llm_health_recorder
from utils.daily_research_errors import PaperStageError, paper_stage_error
from utils.daily_research_fingerprints import (
    build_score_audit_metadata,
    build_stage_input_fingerprints,
)
from scoring_policy import (
    optional_score_value,
    qualification_threshold_for,
    qualification_score_for,
    ranking_score_for,
    response_strategy_id,
)
from utils.webdav_sync import (
    after_report_sync_maintenance_entry,
    deliver_pending_after_report_syncs,
)

logger = setup_logger("DailyResearch")


def _keyword_configuration_error(
    all_keywords: Optional[Dict[str, float]],
    primary_keywords: object,
    reference_extraction_enabled: bool,
) -> Optional[str]:
    """Return a user-actionable keyword setup error, if one is known.

    ``all_keywords`` is ``None`` for the cheap preflight check.  Reference
    extraction is allowed to be the only keyword source, so a missing primary
    list is only an immediate configuration error when extraction is disabled.
    After extraction has run, an empty result gets a distinct message that
    points to the actual missing input instead of incorrectly demanding a
    manually configured primary keyword.
    """
    normalized_primary = [
        str(keyword).strip()
        for keyword in (primary_keywords or [])
        if str(keyword).strip()
    ]
    if normalized_primary:
        return None
    if not reference_extraction_enabled:
        return (
            "未配置可用关键词：主要关键词为空，且参考文献关键词提取未启用。"
            "请添加主要关键词，或启用提取并提供参考 PDF。"
        )
    if all_keywords is not None and not all_keywords:
        return (
            "未获得可用关键词：主要关键词为空，且参考文献关键词提取未产出关键词。"
            "请添加可读取的参考 PDF，或配置主要关键词后重试。"
        )
    return None


def _validate_report_paths(
    report_paths: Dict[str, Path], scored_papers_by_source: Dict[str, List[Dict[str, Any]]]
) -> None:
    """Ensure every enabled report artifact exists before committing history."""
    if not settings.ENABLE_MARKDOWN_REPORT and not settings.ENABLE_HTML_REPORT:
        raise RuntimeError(
            "日报配置无有效输出格式：至少启用 Markdown 或 HTML 报告，"
            "否则不会写入论文交付历史"
        )

    expected_keys = set()
    for source, papers in scored_papers_by_source.items():
        if not papers:
            continue
        if settings.ENABLE_MARKDOWN_REPORT:
            expected_keys.add(source)
        if settings.ENABLE_HTML_REPORT:
            expected_keys.add(f"{source}_html")

    missing = expected_keys.difference(report_paths)
    invalid = []
    for key in expected_keys.intersection(report_paths):
        path = Path(report_paths[key])
        try:
            valid = path.is_file() and path.stat().st_size > 0
        except OSError:
            valid = False
        if not valid:
            invalid.append(f"{key}={path}")

    if missing or invalid:
        details = []
        if missing:
            details.append(f"缺少输出: {', '.join(sorted(missing))}")
        if invalid:
            details.append(f"空文件或不可访问: {', '.join(sorted(invalid))}")
        raise RuntimeError("报告完整性校验失败，未写入论文历史: " + "; ".join(details))


def _select_top_papers(
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]], limit: int
) -> List[Dict[str, Any]]:
    """Select notification recommendations exclusively from qualified papers."""
    qualified = []
    for source, scored_papers in scored_papers_by_source.items():
        for paper in scored_papers:
            score_response = paper["score_response"]
            if not score_response.is_qualified:
                continue
            qualified.append(
                {
                    "title": paper["title"],
                    "score": ranking_score_for(score_response),
                    # Keep notification payloads display-only, but make V2's
                    # distinction explicit and safe for older hydrated rows.
                    "relevance_score": qualification_score_for(score_response),
                    "qualification_threshold": qualification_threshold_for(score_response),
                    "has_separate_relevance_score": optional_score_value(
                        score_response, "relevance_score"
                    ) is not None,
                    "strategy_id": response_strategy_id(score_response),
                    "source": source,
                    "tldr": score_response.tldr,
                    "url": paper["url"],
                }
            )
    qualified.sort(key=lambda item: item["score"], reverse=True)
    return qualified[: max(0, limit)]


def _exclude_sqlite_delivered_papers(
    store: DailyResearchStore, papers_by_source: Dict[str, List[PaperMetadata]]
) -> Dict[str, List[PaperMetadata]]:
    """Filter exact versions already committed to the SQLite delivery ledger."""
    filtered = {}
    for source, papers in papers_by_source.items():
        delivered = [
            paper for paper in papers if store.is_paper_delivered(source, paper.paper_id)
        ]
        if delivered:
            logger.info(
                "[%s] SQLite 交付记录跳过 %s 篇已完成论文（兼容历史文件恢复）",
                source,
                len(delivered),
            )
        filtered[source] = [paper for paper in papers if paper not in delivered]
    return filtered


def _arxiv_mirror_canonical_id(paper: PaperMetadata) -> str:
    """Return a normalised arXiv canonical ID exposed by a mirror record.

    Mirror sources deliberately retain their own source/paper identity in the
    delivery ledger.  This helper is only for deciding whether an arXiv record
    should take precedence in the current report, or whether an already
    delivered arXiv version has made a late mirror notification redundant.
    """
    from sources.base_source import split_arxiv_version

    raw_id = getattr(paper, "arxiv_id", None)
    if not isinstance(raw_id, str) or not raw_id.strip():
        return ""
    canonical_id, _version = split_arxiv_version(raw_id.strip())
    return canonical_id.strip()


def _exclude_cross_source_arxiv_mirrors(
    store: DailyResearchStore | None,
    papers_by_source: Dict[str, List[PaperMetadata]],
) -> Dict[str, List[PaperMetadata]]:
    """Keep source records even when an arXiv identity says they are the same work.

    The function name remains for compatibility with older callers, but v4.1
    deliberately no longer drops a Hugging Face or journal record merely
    because arXiv also has it.  Each source can produce its own report and
    retry state; ``DailyResearchStore`` links exact DOI/arXiv identities into
    one logical-paper entity for archive search and keyword de-duplication.
    ``store`` is retained in the signature to avoid breaking extensions.
    """
    del store
    return {source: list(papers) for source, papers in papers_by_source.items()}


def _load_learned_terms(store):
    """Load the learned keyword/author library for the learned scoring mode.

    Raw aggregated weights are passed through unclamped; the scoring side
    applies the configured cap and dampening so the tunables stay effective
    without rewriting the library.
    """
    if store is None:
        return None
    try:
        if settings.normalized_score_strategy() != LEARNED_PREFERENCE_V1:
            return None
        rows = store.get_learned_preference_terms()
    except Exception:
        logger.debug("学习库加载失败", exc_info=True)
        return None
    terms = {"keyword": {}, "author": {}}
    for row in rows:
        bucket = terms.get(row.get("term_type"))
        term = row.get("term")
        if bucket is None or not isinstance(term, str) or not term.strip():
            continue
        bucket[term.strip()] = float(row.get("weight") or 0.0)
    return terms if any(terms.values()) else None


def _record_v1_learning_signals(store, source, paper, score_response):
    """Feed legacy-family passes back into the learned library.

    Upserts keyed by (paper, term, kind) make re-recording idempotent, so
    recovery replays never double-count a paper.
    """
    if store is None or score_response is None:
        return
    strategy_id = getattr(score_response, "strategy_id", "") or ""
    if strategy_id not in (LEGACY_WEIGHTED_KEYWORD_V1, LEARNED_PREFERENCE_V1):
        return
    if not getattr(score_response, "is_qualified", False):
        return
    try:
        store.record_learning_signals(
            source,
            paper.paper_id,
            list(getattr(score_response, "extracted_keywords", None) or []),
            "keyword",
            "v1_pass",
            V1_PASS_SIGNAL_STRENGTH,
        )
        store.record_learning_signals(
            source,
            paper.paper_id,
            list(getattr(paper, "authors", None) or []),
            "author",
            "v1_pass",
            V1_PASS_SIGNAL_STRENGTH,
        )
    except Exception:
        logger.debug("v1 学习信号记录失败", exc_info=True)


def _auto_favorite_qualified_paper(store, source, paper, score_response) -> None:
    """Put qualified papers in 收藏 without overriding an explicit reader mark."""
    if (
        store is None
        or not getattr(settings, "AUTO_FAVORITE_QUALIFIED_PAPERS", True)
        or not getattr(score_response, "is_qualified", False)
    ):
        return
    try:
        added = store.add_auto_favorite_if_unmarked(
            source,
            paper.paper_id,
            title=paper.title,
            canonical_id=getattr(paper, "canonical_id", None),
            version=getattr(paper, "version", None),
            authors=list(getattr(paper, "authors", None) or []),
            categories=list(getattr(paper, "categories", None) or []),
        )
    except Exception:
        # 收藏 is a convenience layer. A failure here must not invalidate a
        # successfully scored paper or prevent it from being retried/delivered.
        logger.warning("自动收藏及格论文失败: %s:%s", source, paper.paper_id, exc_info=True)
        return
    if added:
        logger.info("自动收藏及格论文: %s:%s", source, paper.paper_id)


def _score_single_paper(
    paper,
    source,
    analysis_agent,
    all_keywords,
    translation_cache,
    cache_lock,
    score_response=None,
    abstract_cn=None,
    translate=True,
    learned_terms=None,
    learning_store=None,
):
    """
    对单篇论文进行评分和翻译（供并发调用）。

    线程安全：translation_cache 通过 cache_lock 保护。
    """
    if score_response is None:
        try:
            score_response = analysis_agent.score_paper_with_keywords(
                title=paper.title,
                authors=paper.authors,
                abstract=paper.abstract,
                keywords_dict=all_keywords,
                learned_terms=learned_terms,
            )
        except Exception as exc:
            raise paper_stage_error("score", exc) from exc

    if abstract_cn is None:
        abstract_cn = ""
    if translate and abstract_cn == "" and paper.abstract and paper.abstract.strip():
        abstract_hash = hashlib.md5(paper.abstract.encode("utf-8")).hexdigest()

        with cache_lock:
            cached = translation_cache.get(abstract_hash)

        if cached:
            abstract_cn = cached
            logger.debug(f"使用缓存的翻译: {paper.title[:30]}...")
        else:
            try:
                abstract_cn = analysis_agent.translate_abstract(paper.abstract)
            except Exception as exc:
                raise paper_stage_error("translation", exc) from exc
            if not abstract_cn or not abstract_cn.strip():
                raise PaperStageError("translation", "摘要翻译返回空结果")
            with cache_lock:
                translation_cache[abstract_hash] = abstract_cn
            logger.debug(f"翻译并缓存: {paper.title[:30]}...")

    scored = {
        "paper_metadata": paper,
        "paper_id": paper.paper_id,
        "title": paper.title,
        "authors": paper.get_authors_string(),
        "abstract": paper.abstract,
        "abstract_cn": abstract_cn,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "published": paper.published_date.strftime("%Y-%m-%d") if paper.published_date else "N/A",
        "score_response": score_response,
    }

    _record_v1_learning_signals(learning_store, source, paper, score_response)

    return scored


def _score_or_translate_stage_error(stage: str, exc: BaseException) -> PaperStageError:
    """Classify only the local stage that raised, never its message text."""
    return paper_stage_error(stage, exc)


def _deep_analyze_single_paper(paper_info, analysis_agent):
    """
    对单篇论文进行深度分析（供并发调用）。

    返回:
        dict 或 None: {'paper_id': ..., 'analysis': ...} 或 None（失败时）
    """
    paper_meta = paper_info.get("paper_metadata")
    pdf_url = paper_meta.get_best_pdf_url() if paper_meta else paper_info.get("pdf_url")

    max_attempts = max(1, int(getattr(settings, "RETRY_MAX_ATTEMPTS", 3)))
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            analysis = analysis_agent.deep_analyze(
                title=paper_info["title"],
                pdf_url=pdf_url,
                abstract=paper_info["abstract"],
                fallback_to_abstract=True,
            )
            if analysis:
                return {
                    "paper_id": paper_info["paper_id"],
                    "analysis": analysis,
                    "paper_meta": paper_meta,
                    "title": paper_info["title"],
                }
            last_error = RuntimeError("深度分析未返回结果")
        except Exception as exc:
            last_error = exc

        if attempt < max_attempts:
            wait_seconds = min(
                int(getattr(settings, "RETRY_MIN_WAIT", 2)) * (2 ** (attempt - 1)),
                int(getattr(settings, "RETRY_MAX_WAIT", 30)),
            )
            logger.warning(
                "深度分析失败，将重试 (%s/%s)，等待 %ss: %s",
                attempt,
                max_attempts,
                wait_seconds,
                last_error,
            )
            time.sleep(wait_seconds)

    raise PaperStageError(
        "analysis",
        f"深度分析在 {max_attempts} 次尝试后仍失败: {last_error}",
        cause=last_error,
    ) from last_error


def _score_or_hydrate_paper(
    run_id,
    source,
    paper,
    analysis_agent,
    all_keywords,
    translation_cache,
    cache_lock,
    store,
    learned_terms=None,
    previous_version_info=None,
):
    """Reuse persisted scoring when available, otherwise score and persist."""
    existing_record = None
    if store:
        existing_record = store.get_paper_record(source, paper.paper_id)
        # Restore durable optional enrichment before calculating cache keys.
        # In particular, an earlier retry may have retained an arXiv PDF URL
        # while a new source response was temporarily missing it.
        store.restore_optional_enrichment_from_record(paper, existing_record)
        fingerprints = build_stage_input_fingerprints(
            paper,
            all_keywords,
            getattr(analysis_agent, "deep_template", {}),
        )
        store.upsert_paper_seen(run_id, source, paper, fingerprints)
        record = store.get_paper_record(source, paper.paper_id)
        hydrated = store.hydrate_scored_paper(paper, record, require_translation=False)
        if hydrated:
            scored = hydrated
            score_is_new = False
            logger.debug(f"复用已持久化评分: {paper.title[:30]}...")
        else:
            try:
                score_response = analysis_agent.score_paper_with_keywords(
                    title=paper.title,
                    authors=paper.authors,
                    abstract=paper.abstract,
                    keywords_dict=all_keywords,
                    learned_terms=learned_terms,
                )
            except Exception as exc:
                raise _score_or_translate_stage_error("score", exc) from exc
            scored = _score_single_paper(
                paper,
                source,
                analysis_agent,
                all_keywords,
                translation_cache,
                cache_lock,
                score_response=score_response,
                abstract_cn="",
                translate=False,
                learned_terms=learned_terms,
                learning_store=store,
            )
            store.update_score(
                run_id,
                source,
                scored,
                score_input_fingerprint=fingerprints.get("score"),
                score_audit_metadata=build_score_audit_metadata(
                    paper,
                    all_keywords,
                    fingerprints.get("score"),
                ),
            )
            score_is_new = True

        _auto_favorite_qualified_paper(
            store, source, paper, scored["score_response"]
        )

        record = store.get_paper_record(source, paper.paper_id)
        translation_required = bool(paper.abstract and paper.abstract.strip())
        translation_done = record["translation_status"] in ("succeeded", "not_required")
        if translation_required and not translation_done:
            abstract_hash = hashlib.md5(paper.abstract.encode("utf-8")).hexdigest()
            with cache_lock:
                cached = translation_cache.get(abstract_hash)
            if cached:
                abstract_cn = cached
            else:
                try:
                    abstract_cn = analysis_agent.translate_abstract(paper.abstract)
                except Exception as exc:
                    raise _score_or_translate_stage_error("translation", exc) from exc
                if not abstract_cn or not abstract_cn.strip():
                    raise PaperStageError("translation", "摘要翻译返回空结果")
                with cache_lock:
                    translation_cache[abstract_hash] = abstract_cn
            store.update_translation(
                run_id,
                source,
                paper.paper_id,
                abstract_cn,
                translation_input_fingerprint=fingerprints.get("translation"),
            )
            scored["abstract_cn"] = abstract_cn
        elif not translation_required:
            store.mark_translation_not_required(run_id, source, paper.paper_id)
        elif record["abstract_cn"]:
            scored["abstract_cn"] = record["abstract_cn"]

        _add_paper_delivery_context(
            scored,
            paper,
            existing_record,
            store.get_previous_version_record(source, paper),
            previous_version_info,
        )
        scored["stage_fingerprints"] = fingerprints
        return scored

    scored = _score_single_paper(
        paper,
        source,
        analysis_agent,
        all_keywords,
        translation_cache,
        cache_lock,
        learned_terms=learned_terms,
        learning_store=store,
    )

    _add_paper_delivery_context(
        scored,
        paper,
        existing_record,
        store.get_previous_version_record(source, paper) if store else None,
        previous_version_info,
    )

    return scored


def _add_paper_delivery_context(
    scored, paper, existing_record=None, previous_record=None, previous_history=None
):
    """Attach retry/revision metadata consumed by report renderers."""
    if existing_record is not None and existing_record["completed_at"] is None:
        # Every fetched candidate is now registered before the per-run queue
        # limit is applied.  A pristine pending row is therefore not itself a
        # retry; only prior stage work/failure should receive that label.
        was_attempted = bool(existing_record["retry_count"]) or any(
            existing_record[field] != "pending"
            for field in ("score_status", "translation_status", "analysis_status")
        )
        if was_attempted:
            scored["is_retry"] = True

    previous_version = None
    previous_pushed_at = None
    if previous_record is not None:
        previous_version = previous_record["version"]
        previous_pushed_at = (
            previous_record["delivered_at"]
            if "delivered_at" in previous_record.keys()
            else None
        ) or previous_record["completed_at"]
    elif previous_history:
        previous_version = previous_history.get("version")
        previous_pushed_at = previous_history.get("processed_at")

    if previous_version is not None:
        scored["revision"] = {
            "version": paper.version,
            "previous_version": previous_version,
            "previous_pushed_at": previous_pushed_at,
        }


def _paper_metadata_from_backlog_payload(
    payload: Dict[str, Any], source: str
) -> Optional[PaperMetadata]:
    """从积压行持久化的 paper_json 重建 PaperMetadata（缺字段容错）。"""
    try:
        published = payload.get("published_date")
        return PaperMetadata(
            paper_id=str(payload["paper_id"]),
            title=str(payload.get("title") or payload["paper_id"]),
            authors=[str(item) for item in (payload.get("authors") or [])],
            abstract=str(payload.get("abstract") or ""),
            published_date=(
                datetime.fromisoformat(published) if published else datetime.now()
            ),
            url=str(payload.get("url") or ""),
            source=source,
            pdf_url=payload.get("pdf_url"),
            doi=payload.get("doi"),
            journal=payload.get("journal"),
            categories=[str(item) for item in (payload.get("categories") or [])],
            semantic_scholar_tldr=payload.get("semantic_scholar_tldr"),
            arxiv_id=payload.get("arxiv_id"),
            arxiv_url=payload.get("arxiv_url"),
            canonical_id=payload.get("canonical_id"),
            version=payload.get("version"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _load_supplement_candidates(
    store: DailyResearchStore,
    run_id: str,
    *,
    reasons: Optional[set[str] | list[str] | tuple[str, ...]] = None,
    published_from: Optional[date] = None,
    published_to: Optional[date] = None,
    paper_limit: Optional[int] = None,
) -> Tuple[
    Dict[str, List[PaperMetadata]], List[Tuple[str, str, int]], int
]:
    """装载本次补充运行要处理的积压论文。

    返回 (papers_by_source, 选中的 (source, canonical, version) 身份列表,
    元数据获取失败条数)。缺 paper_json 的 arXiv 行按 ID 补抓元数据；
    抓不到或其他来源无法补抓的行记为 failed 留待再次重试。
    """
    limit = (
        int(getattr(settings, "DAILY_MAX_PAPERS_PER_RUN", 0) or 0)
        if paper_limit is None
        else paper_limit
    )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("补充报告论文上限必须是非负整数（0 表示不限）")
    # ``0`` is the documented unlimited setting for every daily-style run.
    # Read the ordered backlog without applying the report cap here: a broken
    # legacy row must be recorded as retryable *and then skipped* so a later,
    # usable row can still fill this capped supplement report.
    rows = store.claim_supplement_backlog(
        0,
        reasons=reasons,
        published_from=published_from,
        published_to=published_to,
    )
    papers_by_source: Dict[str, List[PaperMetadata]] = {}
    selected: List[Tuple[str, str, int]] = []
    failed: List[Tuple[str, str, int]] = []
    arxiv_source = None
    # ArXiv's ID API is intentionally requested in small chunks.  Besides
    # matching its client-side batch behavior, this lets a failed old repair
    # yield to a later persisted scan result without fetching an unbounded
    # legacy backlog merely to satisfy one report's cap.
    fetch_chunk_size = 50
    for offset in range(0, len(rows), fetch_chunk_size):
        if limit and len(selected) >= limit:
            break
        batch = rows[offset : offset + fetch_chunk_size]
        need_fetch_ids = [
            str(row.get("paper_id") or "").strip()
            for row in batch
            if row["source"] == "arxiv" and not row.get("paper_json")
            and str(row.get("paper_id") or "").strip()
        ]
        fetched: Dict[str, PaperMetadata] = {}
        if need_fetch_ids:
            try:
                if arxiv_source is None:
                    from sources.arxiv_source import ArxivSource

                    arxiv_source = ArxivSource(
                        history_dir=settings.HISTORY_DIR,
                        proxy_dict=settings.get_proxy_dict("arxiv"),
                        load_legacy_history=False,
                    )
                fetched = arxiv_source.fetch_papers_by_ids(need_fetch_ids)
                try:
                    store.record_source_health_event(
                        "arxiv",
                        True,
                        run_id=run_id,
                        task_kind="supplement",
                        candidate_count=len(fetched),
                        origin_key=f"supplement-id-fetch:{run_id}:{offset}",
                    )
                except Exception:
                    logger.debug("补充元数据补抓健康事件写入失败", exc_info=True)
                logger.info(
                    "补充积压按 ID 补抓元数据: 请求 %s，成功 %s",
                    len(need_fetch_ids),
                    len(fetched),
                )
            except Exception as exc:
                try:
                    store.record_source_health_event(
                        "arxiv",
                        False,
                        run_id=run_id,
                        task_kind="supplement",
                        error_summary=exc,
                        origin_key=f"supplement-id-fetch:{run_id}:{offset}",
                    )
                except Exception:
                    logger.debug("补充元数据补抓健康事件写入失败", exc_info=True)
                logger.warning("补充积压元数据补抓失败（本次跳过这些论文）: %s", exc)

        for row in batch:
            if limit and len(selected) >= limit:
                break
            source = row["source"]
            canonical_id = row["canonical_id"]
            version = int(row.get("version") or 0)
            identity = (source, canonical_id, version)

            paper = None
            if row.get("paper_json"):
                paper = _paper_metadata_from_backlog_payload(row["paper_json"], source)
            if paper is None and source == "arxiv":
                paper = fetched.get(canonical_id)
            if paper is None:
                failed.append(identity)
                logger.warning(
                    "补充积压论文无法获取元数据，记为失败待重试: %s:%sv%s",
                    source, canonical_id, version,
                )
                continue

            if store.is_paper_delivered_strict(source, paper.paper_id):
                # 导入之后已被其他运行交付：直接销账，避免重复推送。
                store.resolve_supplement_backlog(
                    run_id, [identity], status="delivered", detail="已由其他运行交付"
                )
                continue

            papers_by_source.setdefault(source, []).append(paper)
            selected.append(identity)

    if failed:
        store.resolve_supplement_backlog(
            run_id, failed, status="failed", detail="无法获取论文元数据"
        )
    return papers_by_source, selected, len(failed)


def _make_source_health_recorder(
    store: DailyResearchStore, run_id: str, task_kind: str
):
    """Return a best-effort observer for optional source enrichment calls."""

    def record(
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
                task_kind=task_kind,
                candidate_count=candidate_count,
                error_summary=error,
            )
        except Exception:
            logger.debug("来源健康事件写入失败: %s", source, exc_info=True)

    return record


def _fetch_backfill_papers(
    store: DailyResearchStore,
    run_id: str,
    target_date: date,
) -> Dict[str, List[PaperMetadata]]:
    """Fetch every enabled source for one past calendar day.

    Backfill receipts are observational only: this path never calls
    ``prepare_scan`` and therefore cannot advance ordinary daily watermarks.
    """
    agent = SearchAgent(
        history_dir=settings.HISTORY_DIR,
        enabled_sources=settings.ENABLED_SOURCES,
        arxiv_domains=settings.TARGET_DOMAINS,
        journals=settings.TARGET_JOURNALS,
        enable_openalex=getattr(settings, "ENABLE_OPENALEX", True),
        openalex_api_key=settings.OPENALEX_API_KEY,
        enable_semantic_scholar=settings.ENABLE_SEMANTIC_SCHOLAR_TLDR,
        semantic_scholar_api_key=settings.SEMANTIC_SCHOLAR_API_KEY,
        extra_source_definitions=getattr(settings, "EXTRA_SOURCE_DEFINITIONS", []),
        use_legacy_history_filter=False,
        source_health_recorder=_make_source_health_recorder(
            store, run_id, "backfill"
        ),
    )
    try:
        callbacks = {
            source: (
                lambda receipt, source=source: store.record_scan_receipt(
                    run_id,
                    source,
                    {**receipt, "backfill_date": target_date.isoformat()},
                )
            )
            for source in agent.get_enabled_sources()
        }
        papers_by_source = agent.fetch_papers_between(
            target_date,
            target_date,
            scan_receipt_callbacks=callbacks,
        )
    finally:
        agent.close()
    logger.info(
        "过去日报补跑 %s：%s 个来源共发现 %s 篇",
        target_date.isoformat(),
        len(papers_by_source),
        sum(len(papers) for papers in papers_by_source.values()),
    )
    return papers_by_source


def _enabled_backfill_sources() -> List[str]:
    """Return report sources that the current provider switches can fetch."""
    enabled: List[str] = []
    for source in list(getattr(settings, "ENABLED_SOURCES", []) or []):
        normalized = str(source or "").strip().lower()
        if not normalized or normalized in enabled:
            continue
        if normalized not in {"arxiv", "huggingface_papers"} and not getattr(
            settings, "ENABLE_OPENALEX", True
        ):
            continue
        enabled.append(normalized)
    return enabled


def _delivered_papers_for_finalization(
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
    analyses_by_source: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build the exact paper set eligible for an atomic report delivery commit."""
    delivered = {}
    for source, scored_papers in scored_papers_by_source.items():
        analyzed_ids = {item["paper_id"] for item in analyses_by_source.get(source, [])}
        eligible = []
        for paper_info in scored_papers:
            paper_meta = paper_info.get("paper_metadata")
            requires_analysis = bool(
                settings.DAILY_ENABLE_DEEP_ANALYSIS
                and paper_info["score_response"].is_qualified
                and paper_meta
                and paper_meta.has_pdf_access()
            )
            if requires_analysis and paper_info["paper_id"] not in analyzed_ids:
                raise RuntimeError(
                    f"深度分析尚未完成，不能交付日报: {source}:{paper_info['paper_id']}"
                )
            eligible.append({**paper_info, "requires_analysis": requires_analysis})
        if eligible:
            delivered[source] = eligible
    return delivered


def _reportable_scored_papers(
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
    analyses_by_source: Dict[str, List[Dict[str, Any]]],
) -> tuple[Dict[str, List[Dict[str, Any]]], list[tuple[str, str]]]:
    """Keep incomplete deep-analysis papers out of a partial report.

    A paper whose score/translation failed never reaches ``scored_papers``.
    A qualified PDF paper can, however, have reached that list before its
    deep-analysis call failed. It must not be rendered or delivered in a
    partial report: otherwise it would look complete and lose its durable
    retry state. The surrounding pipeline records the stage failure in SQLite
    and includes every other complete paper normally.
    """
    reportable: Dict[str, List[Dict[str, Any]]] = {}
    withheld: list[tuple[str, str]] = []
    for source, scored_papers in scored_papers_by_source.items():
        analyzed_ids = {
            item["paper_id"] for item in analyses_by_source.get(source, [])
        }
        complete_papers: List[Dict[str, Any]] = []
        for paper_info in scored_papers:
            paper_meta = paper_info.get("paper_metadata")
            requires_analysis = bool(
                settings.DAILY_ENABLE_DEEP_ANALYSIS
                and paper_info["score_response"].is_qualified
                and paper_meta
                and paper_meta.has_pdf_access()
            )
            if requires_analysis and paper_info["paper_id"] not in analyzed_ids:
                withheld.append((source, paper_info["paper_id"]))
                continue
            complete_papers.append(paper_info)
        if complete_papers:
            reportable[source] = complete_papers
    return reportable, withheld


def _run_result_notification_entries(
    notifier: NotifierAgent, result: RunResult
) -> List[Dict[str, Any]]:
    """Return one durable notification request per currently configured channel."""
    if result.success and not notifier.settings.NOTIFY_ON_SUCCESS:
        return []
    if not result.success and not notifier.settings.NOTIFY_ON_FAILURE:
        return []
    payload = {"result": asdict(result)}
    return [
        {"event_type": "daily_run_result", "channel": channel, "payload": payload}
        for channel in notifier.configured_channels()
    ]


def _build_daily_run_result(
    total_papers_count: int,
    scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
    analyses_by_source: Dict[str, List[Dict[str, Any]]],
    report_paths: Dict[str, Path],
    *,
    deferred_paper_count: int = 0,
) -> RunResult:
    """Build the immutable report-delivery notification payload before committing it."""
    run_result = RunResult(
        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_papers_fetched=total_papers_count,
        deferred_paper_count=max(0, int(deferred_paper_count or 0)),
        top_papers=_select_top_papers(scored_papers_by_source, settings.NOTIFICATION_TOP_N),
    )
    for source, scored_papers in scored_papers_by_source.items():
        source_qualified = sum(1 for p in scored_papers if p["score_response"].is_qualified)
        source_analyzed = len(analyses_by_source.get(source, []))
        run_result.papers_by_source[source] = len(scored_papers)
        run_result.qualified_by_source[source] = source_qualified
        run_result.analyzed_by_source[source] = source_analyzed
        run_result.total_qualified += source_qualified
        run_result.total_analyzed += source_analyzed

    run_result.report_paths = {source: str(path) for source, path in report_paths.items()}
    if settings.TOKEN_TRACKING_ENABLED:
        run_result.token_usage = token_counter.get_summary()
    return run_result


def _notification_issue_messages(
    stage_errors: List[Tuple[str, str, str]],
    analysis_errors: List[Tuple[str, str, str]],
    withheld_analysis: List[Tuple[str, str]],
    *,
    deferred_paper_count: int = 0,
    supplement_fetch_failures: int = 0,
) -> List[str]:
    """Describe partial completion without embedding an unbounded execution log.

    A report may be valid even when a few papers failed scoring, translation,
    PDF analysis, or metadata recovery.  Those rows stay retryable in SQLite;
    the notification needs the actionable paper/stage summary rather than an
    opaque all-success message.
    """
    issues: List[str] = []
    for stage_label, entries in (("评分/翻译", stage_errors), ("深度分析", analysis_errors)):
        for source, paper_id, error in entries[:6]:
            issues.append(
                f"{stage_label}未完成：{source}:{paper_id}；{str(error)[:500]}"
            )
        if len(entries) > 6:
            issues.append(f"{stage_label}另有 {len(entries) - 6} 篇失败，已保留供重试")

    if withheld_analysis and not analysis_errors:
        issues.append(f"{len(withheld_analysis)} 篇合格论文缺少可交付的深度分析，已保留供重试")
    if supplement_fetch_failures:
        issues.append(
            f"{supplement_fetch_failures} 条旧历史补充数据暂时无法获取元数据，已保留供下次重试"
        )
    if deferred_paper_count > 0:
        issues.append(
            f"受本次最多处理论文数限制，仍有 {deferred_paper_count} 篇留待后续运行"
        )
    return issues


class DailyResearchPipeline:
    """
    每日研究模式流水线。

    从多个数据源抓取论文，评分筛选，深度分析，生成报告，发送通知。
    """

    def run(
        self,
        run_kind: str = "daily",
        target_date: Optional[date] = None,
        *,
        supplement_reasons: Optional[set[str] | list[str] | tuple[str, ...]] = None,
        supplement_week_start: Optional[date] = None,
        supplement_week_end: Optional[date] = None,
        report_timestamp: Optional[datetime] = None,
        paper_limit: Optional[int] = None,
    ):
        """
        执行每日研究完整流程。

        ``run_kind``:
        - "daily"（默认）：正常扫描-评分-报告流程；
        - "supplement"：跳过扫描，从补充积压（旧历史缺数据/遗漏论文）
          装载候选，走同一评分/分析/报告/原子交付流程，产出补充报告；
        - "backfill"：为过去的某一天（``target_date``）重跑当天的每日
          研究，报告时间戳用过去日期 + 当前时刻。

        ``supplement_reasons`` 和 ``supplement_week_start/end`` 让历史遗漏
        工作流只消费自己的 SQLite 积压，并以真实自然周拆分补充报告。
        它们不改变普通补充运行的兼容行为。``paper_limit`` 仅供历史维护
        调用覆盖补充报告的每日上限。
        """
        if run_kind == "backfill" and target_date is None:
            raise ValueError("backfill 运行必须提供 target_date")
        if run_kind != "supplement" and any(
            value is not None
            for value in (
                supplement_reasons,
                supplement_week_start,
                supplement_week_end,
                report_timestamp,
                paper_limit,
            )
        ):
            raise ValueError("补充报告筛选、时间戳和论文上限只能用于 supplement 运行")
        if supplement_week_start is not None and not isinstance(supplement_week_start, date):
            raise ValueError("补充报告周起始日期必须是 date")
        if supplement_week_end is not None and not isinstance(supplement_week_end, date):
            raise ValueError("补充报告周结束日期必须是 date")
        if (
            supplement_week_start is not None
            and supplement_week_end is not None
            and supplement_week_start > supplement_week_end
        ):
            raise ValueError("补充报告周起始日期不能晚于结束日期")
        if report_timestamp is not None and not isinstance(report_timestamp, datetime):
            raise ValueError("补充报告时间戳必须是 datetime")
        if paper_limit is not None and (
            isinstance(paper_limit, bool)
            or not isinstance(paper_limit, int)
            or paper_limit < 0
        ):
            raise ValueError("补充报告论文上限必须是非负整数（0 表示不限）")
        store = None
        run_id = None
        notifier = None
        report_delivery_committed = False
        try:
            print("\n" + "=" * 80)
            print("🚀 多数据源研究系统启动")
            print("=" * 80 + "\n")

            logger.info("=" * 80)
            logger.info("启动多数据源研究系统")
            logger.info("=" * 80)

            if settings.TOKEN_TRACKING_ENABLED:
                token_counter.reset()

            # SQLite is the authoritative daily-research ledger. It stores
            # exact versions, resumable stages, scan checkpoints and atomic
            # delivery state; falling back to JSON would discard those
            # guarantees on an upgraded installation.
            store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
            # Record only final outcomes of the real calls performed by this
            # run.  The callback is best-effort and shares this run's SQLite
            # ledger, so concurrent paper workers do not create side stores.
            llm_health_recorder = make_llm_health_recorder(store)
            # Keep the durable run ledger truthful: supplement/backfill runs
            # share the daily pipeline, but they are distinct operations in
            # diagnostics and must never be presented as an ordinary daily
            # scan.
            run_id = store.start_run(0, run_kind=run_kind)
            store.record_run_phase(run_id, "prepare")
            logger.info(f"每日研究 SQLite 状态库已启用: {settings.DAILY_RESEARCH_DB_PATH}")

            # WebDAV is non-critical post-report maintenance. Retry old
            # uploads before the long scan without letting a remote outage
            # affect this run's paper identity or delivery state.
            try:
                sync_summary = deliver_pending_after_report_syncs(store, logger)
                if sync_summary["claimed"]:
                    logger.info(
                        "已补发待处理 WebDAV 同步: 完成 %s，延后 %s",
                        sync_summary["completed"],
                        sync_summary["deferred"],
                    )
            except Exception as exc:
                logger.warning("待补发 WebDAV 同步检查失败，将继续生成日报: %s", exc)

            # Notification retries are independent from the current paper scan.
            # Do this before processing so an old failed webhook is not delayed
            # by a long LLM run, and never affects whether a paper is "new".
            if settings.ENABLE_NOTIFICATIONS and store:
                try:
                    notifier = NotifierAgent()
                    retry_summary = notifier.deliver_pending_run_results(store)
                    if retry_summary["claimed"]:
                        logger.info(
                            "已补发待处理通知: 发送 %s，延后 %s",
                            retry_summary["sent"],
                            retry_summary["deferred"],
                        )
                    workflow_retry = notifier.deliver_pending_workflow_results(store)
                    if workflow_retry["claimed"]:
                        logger.info(
                            "已补发待处理长任务通知: 发送 %s，延后 %s",
                            workflow_retry["sent"],
                            workflow_retry["deferred"],
                        )
                except Exception as exc:
                    # The report pipeline must remain available even if a
                    # notification provider or its templates are broken.
                    logger.warning("待补发通知检查失败，将继续生成日报: %s", exc)

            # ==================== 阶段1: 配置加载 ====================
            logger.info(">>> 阶段1: 加载配置...")

            logger.info(f"启用的数据源: {settings.ENABLED_SOURCES}")
            if "arxiv" in settings.ENABLED_SOURCES:
                logger.info(f"ArXiv目标领域: {settings.TARGET_DOMAINS}")
            if settings.TARGET_JOURNALS:
                logger.info(f"目标期刊: {settings.TARGET_JOURNALS}")
            logger.info(f"搜索窗口: 最近 {settings.DAILY_SCAN_WINDOW_DAYS} 天（固定；过去日期由补跑处理）")
            logger.info("日报抓取: 完整扫描时间窗口内的全部论文（由请求限速和重试保护服务）")
            logger.info(f"启用Reference提取: {settings.ENABLE_REFERENCE_EXTRACTION}")

            # ==================== 阶段2: 关键词准备 ====================
            logger.info(">>> 阶段2: 准备关键词...")

            primary_keywords = getattr(settings, "PRIMARY_KEYWORDS", [])
            reference_extraction_enabled = bool(
                getattr(settings, "ENABLE_REFERENCE_EXTRACTION", False)
            )
            keyword_error = _keyword_configuration_error(
                None, primary_keywords, reference_extraction_enabled
            )
            if keyword_error:
                logger.error("错误: %s", keyword_error)
                fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message=keyword_error,
                )
                if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                    try:
                        NotifierAgent().notify(fail_result)
                    except Exception:
                        pass
                if store and run_id:
                    store.fail_run(run_id, fail_result.error_message)
                return fail_result

            keyword_agent = KeywordAgent()
            attach_health_recorder = getattr(keyword_agent, "set_health_recorder", None)
            if callable(attach_health_recorder):
                attach_health_recorder(llm_health_recorder)
            all_keywords = keyword_agent.get_all_keywords()

            keyword_error = _keyword_configuration_error(
                all_keywords, primary_keywords, reference_extraction_enabled
            )
            if keyword_error:
                logger.error("错误: %s", keyword_error)
                fail_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=False,
                    error_message=keyword_error,
                )
                if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                    try:
                        NotifierAgent().notify(fail_result)
                    except Exception:
                        pass
                if store and run_id:
                    store.fail_run(run_id, fail_result.error_message)
                return fail_result

            logger.info("关键词准备完成:")
            if bool(getattr(settings, "PRIMARY_KEYWORD_WEIGHTS_EXPLICIT", False)):
                logger.info("  - 主要关键词: %d 个（使用独立权重）", len(settings.PRIMARY_KEYWORDS))
            else:
                logger.info(
                    f"  - 主要关键词: {len(settings.PRIMARY_KEYWORDS)} 个（权重 {settings.PRIMARY_KEYWORD_WEIGHT}）"
                )
            if settings.ENABLE_REFERENCE_EXTRACTION:
                ref_count = len(all_keywords) - len(settings.PRIMARY_KEYWORDS)
                logger.info(f"  - Reference关键词: {ref_count} 个（权重 0.3-0.8）")
            logger.info(f"  - 关键词总数: {len(all_keywords)} 个")
            logger.info(f"  - 总权重: {sum(all_keywords.values()):.2f}")

            learned_terms = _load_learned_terms(store)
            if learned_terms:
                logger.info(
                    "  - 学习库: 关键词 %d 个、作者 %d 个（学习模式启用）",
                    len(learned_terms["keyword"]),
                    len(learned_terms["author"]),
                )

            total_weight = sum(all_keywords.values())
            if settings.normalized_score_strategy() == "core_relevance_v2":
                logger.info(
                    "  - V2 核心相关性门槛: %.1f；核心关键词强匹配门槛: %.1f",
                    settings.CORE_RELEVANCE_THRESHOLD,
                    settings.CORE_KEYWORD_MIN_SCORE,
                )
            else:
                passing_score = settings.calculate_passing_score(total_weight)
                logger.info(f"  - 动态及格分: {passing_score:.1f}")
                logger.info(
                    f"  - 及格分公式: {settings.PASSING_SCORE_BASE} + {settings.PASSING_SCORE_WEIGHT_COEFFICIENT} × {total_weight:.1f}"
                )

            # ==================== 阶段3: 抓取所有最新论文 ====================
            logger.info(">>> 阶段3: 从多个数据源抓取论文...")
            store.record_run_phase(run_id, "scan")

            search_agent = None
            supplement_identities: List[Tuple[str, str, int]] = []
            supplement_fetch_failures = 0

            if run_kind == "supplement":
                # 补充运行不扫描：候选来自旧历史导入/时间段扫描写入的
                # 补充积压表；无 prepare_scan → 交付时不要求扫描收据，
                # 也不会推进任何来源的扫描水位线。
                (
                    papers_by_source,
                    supplement_identities,
                    supplement_fetch_failures,
                ) = _load_supplement_candidates(
                    store,
                    run_id,
                    reasons=supplement_reasons,
                    published_from=supplement_week_start,
                    published_to=supplement_week_end,
                    paper_limit=paper_limit,
                )
                registered_candidate_count = sum(
                    len(papers) for papers in papers_by_source.values()
                )
                total_papers_count = registered_candidate_count
                backlog_summary = store.supplement_backlog_summary(
                    reasons=supplement_reasons,
                    published_from=supplement_week_start,
                    published_to=supplement_week_end,
                )
                pending_paper_count = backlog_summary["pending"]
                deferred_paper_count = pending_paper_count - total_papers_count
                if supplement_fetch_failures:
                    logger.warning(
                        "补充积压 %s 条论文无法获取元数据，已记为失败（下次触发会重试）",
                        supplement_fetch_failures,
                    )
                scope = ""
                if supplement_week_start is not None or supplement_week_end is not None:
                    scope = "（自然周 %s 至 %s）" % (
                        supplement_week_start.isoformat() if supplement_week_start else "—",
                        supplement_week_end.isoformat() if supplement_week_end else "—",
                    )
                logger.info(
                    "补充运行%s：积压待处理 %s 篇，本次处理 %s 篇，留待后续 %s 篇（%s 个数据源）",
                    scope,
                    pending_paper_count,
                    total_papers_count,
                    deferred_paper_count,
                    len(papers_by_source),
                )
            elif run_kind == "backfill":
                # A capped report may have persisted more candidates than it
                # could deliver.  Continue those durable, date-scoped rows
                # first; this avoids a second network fetch and means one
                # temporary arXiv outage cannot strand the remainder of an
                # otherwise successful historical day.
                limit = int(getattr(settings, "DAILY_MAX_PAPERS_PER_RUN", 0) or 0)
                enabled_backfill_sources = _enabled_backfill_sources()
                papers_by_source, pending_paper_count = store.select_pending_papers(
                    enabled_backfill_sources,
                    limit,
                    queue_scope="backfill",
                    backfill_target_date=target_date,
                )
                registered_candidate_count = 0
                if pending_paper_count:
                    logger.info(
                        "过去日报（%s）续跑已登记队列：待处理 %s 篇",
                        target_date.isoformat(),
                        pending_paper_count,
                    )
                else:
                    # First batch for this date: scan once, register every
                    # candidate, then select the capped subset from SQLite.
                    fetched_by_source = _fetch_backfill_papers(store, run_id, target_date)
                    fetched_by_source = _exclude_sqlite_delivered_papers(
                        store, fetched_by_source
                    )
                    fetched_by_source = _exclude_cross_source_arxiv_mirrors(
                        store, fetched_by_source
                    )
                    registered_candidate_count = store.register_paper_candidates(
                        run_id,
                        fetched_by_source,
                        queue_scope="backfill",
                        backfill_target_date=target_date,
                    )
                    papers_by_source, pending_paper_count = store.select_pending_papers(
                        enabled_backfill_sources,
                        limit,
                        queue_scope="backfill",
                        backfill_target_date=target_date,
                    )
                total_papers_count = sum(
                    len(papers) for papers in papers_by_source.values()
                )
                deferred_paper_count = pending_paper_count - total_papers_count
                logger.info(
                    "过去日报（%s）：本轮新登记 %s 篇，当前待处理 %s 篇，本次处理 %s 篇，留待后续 %s 篇",
                    target_date.isoformat(),
                    registered_candidate_count,
                    pending_paper_count,
                    total_papers_count,
                    deferred_paper_count,
                )
            else:
                search_agent = SearchAgent(
                    history_dir=settings.HISTORY_DIR,
                    enabled_sources=settings.ENABLED_SOURCES,
                    arxiv_domains=settings.TARGET_DOMAINS,
                    journals=settings.TARGET_JOURNALS,
                    enable_openalex=getattr(settings, "ENABLE_OPENALEX", True),
                    openalex_api_key=settings.OPENALEX_API_KEY,
                    enable_semantic_scholar=settings.ENABLE_SEMANTIC_SCHOLAR_TLDR,
                    semantic_scholar_api_key=settings.SEMANTIC_SCHOLAR_API_KEY,
                    extra_source_definitions=getattr(settings, "EXTRA_SOURCE_DEFINITIONS", []),
                    # SQLite is the sole daily-history authority. Legacy JSON files
                    # are neither read as a filter nor updated after delivery.
                    use_legacy_history_filter=False,
                    source_health_recorder=_make_source_health_recorder(
                        store, run_id, run_kind
                    ),
                )

                # Semantic Scholar is optional enrichment, but a synchronous
                # lookup can take a while for journal-heavy scans.  Establish the
                # recovery checkpoint immediately before the source queries, not
                # before construction/configuration work, so a successful scan
                # window starts where the APIs were actually queried.
                effective_scan_days = settings.DAILY_SCAN_WINDOW_DAYS
                if store and run_id:
                    # A failed run must not let its unreported papers age out of
                    # the user-configured window.  The store records a per-source
                    # checkpoint only after a complete report/no-paper scan has
                    # committed, so this recovery window is expanded precisely
                    # when a prior scan did not reach a durable terminal state.
                    effective_scan_days = store.prepare_scan(
                        run_id,
                        settings.DAILY_SCAN_WINDOW_DAYS,
                        search_agent.get_enabled_sources(),
                    )
                    if effective_scan_days > settings.DAILY_SCAN_WINDOW_DAYS:
                        logger.warning(
                            "日报恢复扫描窗口已扩展: 配置 %s 天 -> %s 天；"
                            "已交付版本会由 SQLite 账本过滤，不会重复推送",
                            settings.DAILY_SCAN_WINDOW_DAYS,
                            effective_scan_days,
                        )

                try:
                    scan_receipt_callbacks = {}
                    if store and run_id:
                        # Receipt persistence is part of source completeness, not
                        # optional analytics. Every configured report source gets
                        # one callback: arXiv emits a rich domain receipt, while
                        # supplementary feeds/each OpenAlex journal emit a source
                        # summary. A callback failure aborts before reports or
                        # watermarks can make an incomplete scan look complete.
                        for receipt_source in search_agent.get_enabled_sources():
                            scan_receipt_callbacks[receipt_source] = (
                                lambda receipt, source=receipt_source: store.record_scan_receipt(
                                    run_id, source, receipt
                                )
                            )
                    papers_by_source: Dict[str, List[PaperMetadata]] = search_agent.fetch_all_papers(
                        days=effective_scan_days,
                        scan_receipt_callbacks=scan_receipt_callbacks,
                    )
                except SourceScanReceiptError as sre:
                    error_detail = str(sre)
                    logger.error("数据源扫描收据持久化失败，终止本次运行: %s", error_detail)
                    receipt_fail_result = RunResult(
                        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        success=False,
                        error_message=f"数据源扫描收据失败: {error_detail}",
                    )
                    if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                        try:
                            NotifierAgent().notify(receipt_fail_result)
                            NotifierAgent().notify_error(
                                "source_scan_receipt",
                                "数据源扫描收据持久化失败；本次日报已终止，"
                                "以避免将无完整扫描证据的结果标记为成功。",
                            )
                        except Exception as ne:
                            logger.warning("发送扫描收据错误通知失败: %s", ne)
                    if store and run_id:
                        store.fail_run(run_id, error_detail)
                    return receipt_fail_result
                except ArxivFetchError as afe:
                    # ArXiv 抓取彻底失败（多次重试后仍无法获取任何论文）
                    error_detail = str(afe)
                    logger.error(f"ArXiv 抓取失败，终止本次运行: {error_detail}")
                    fetch_fail_result = RunResult(
                        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        success=False,
                        error_message=f"ArXiv 抓取失败: {error_detail}",
                    )
                    if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                        try:
                            NotifierAgent().notify(fetch_fail_result)
                            NotifierAgent().notify_error(
                                "arxiv_fetch",
                                f"ArXiv 论文抓取失败\n\n错误详情：{error_detail}\n\n建议检查网络连接及 ArXiv 服务状态。",
                            )
                        except Exception as ne:
                            logger.warning(f"发送错误通知失败: {ne}")
                    if store and run_id:
                        store.fail_run(run_id, error_detail)
                    return fetch_fail_result
                except HuggingFacePapersFetchError as hfe:
                    # The optional source is still fail-closed once enabled: an
                    # incomplete curated feed must not be reported as an empty
                    # success, because its missed entries could fall outside the
                    # recovery window before the next run.
                    error_detail = str(hfe)
                    logger.error("Hugging Face Papers 抓取失败，终止本次运行: %s", error_detail)
                    fetch_fail_result = RunResult(
                        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        success=False,
                        error_message=f"Hugging Face Papers 抓取失败: {error_detail}",
                    )
                    if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                        try:
                            NotifierAgent().notify(fetch_fail_result)
                            NotifierAgent().notify_error(
                                "huggingface_papers_fetch",
                                "Hugging Face Papers 抓取失败\n\n"
                                f"错误详情：{error_detail}\n\n"
                                "已终止本次日报，以避免产生不完整的数据源结果。",
                            )
                        except Exception as ne:
                            logger.warning("发送错误通知失败: %s", ne)
                    if store and run_id:
                        store.fail_run(run_id, error_detail)
                    return fetch_fail_result
                except OpenAlexFetchError as oae:
                    # Enabled journals are part of the requested daily scope.  A
                    # malformed entry, failed page, or partial journal list must
                    # not fall through to the generic exception path: return a
                    # normal failed result so schedulers observe it, while leaving
                    # the source watermark unchanged for a full retry next run.
                    error_detail = str(oae)
                    logger.error("OpenAlex 期刊抓取失败，终止本次运行: %s", error_detail)
                    fetch_fail_result = RunResult(
                        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        success=False,
                        error_message=f"OpenAlex 期刊抓取失败: {error_detail}",
                    )
                    if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                        try:
                            NotifierAgent().notify(fetch_fail_result)
                            NotifierAgent().notify_error(
                                "openalex_fetch",
                                "OpenAlex 期刊论文抓取失败\n\n"
                                f"错误详情：{error_detail}\n\n"
                                "已终止本次日报，以避免产生不完整的期刊数据源结果。",
                            )
                        except Exception as ne:
                            logger.warning("发送错误通知失败: %s", ne)
                    if store and run_id:
                        store.fail_run(run_id, error_detail)
                    return fetch_fail_result

                # SQLite is the only authoritative delivery ledger. Filter exact
                # source/version deliveries before registering new queue entries.
                papers_by_source = _exclude_sqlite_delivered_papers(store, papers_by_source)

                papers_by_source = _exclude_cross_source_arxiv_mirrors(
                    store, papers_by_source
                )

                registered_candidate_count = store.register_paper_candidates(
                    run_id, papers_by_source
                )
                papers_by_source, pending_paper_count = store.select_pending_papers(
                    search_agent.get_enabled_sources(),
                    int(getattr(settings, "DAILY_MAX_PAPERS_PER_RUN", 0)),
                )
                total_papers_count = sum(len(papers) for papers in papers_by_source.values())
                deferred_paper_count = pending_paper_count - total_papers_count

            if total_papers_count == 0:
                if run_kind == "supplement":
                    logger.info("补充积压中没有可处理的论文。")
                    print("\n补充积压中没有可处理的论文，程序退出。")
                else:
                    logger.info("未找到新的或待恢复的论文。")
                    print("\n未找到新的或待恢复的论文，程序退出。")
                no_papers_result = RunResult(
                    run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    success=True,
                    deferred_paper_count=deferred_paper_count,
                    issues=_notification_issue_messages(
                        [],
                        [],
                        [],
                        deferred_paper_count=deferred_paper_count,
                        supplement_fetch_failures=supplement_fetch_failures,
                    ),
                )
                if store and run_id:
                    store.complete_run(run_id, {})
                    # The scan/checkpoint commit is complete before the
                    # optional status notification. A provider failure must
                    # never turn an already completed no-paper run back into
                    # a failed run or reopen its recovery window.
                    report_delivery_committed = True
                if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                    # No-paper runs do not have a report delivery to recover;
                    # retain the legacy best-effort behaviour for this status-only notice.
                    try:
                        (notifier or NotifierAgent()).notify(no_papers_result)
                    except Exception as exc:
                        logger.warning("无新论文通知发送失败，运行状态仍保持已完成: %s", exc)
                return no_papers_result

            if run_kind != "supplement":
                logger.info(
                    "完整扫描发现 %s 篇未交付候选；SQLite 当前待处理 %s 篇，"
                    "本次处理 %s 篇，留待后续 %s 篇（%s 个数据源）",
                    registered_candidate_count,
                    pending_paper_count,
                    total_papers_count,
                    deferred_paper_count,
                    len(papers_by_source),
                )

            if store and run_id:
                store.set_run_total(run_id, total_papers_count)

            # ==================== 阶段4: 对所有论文评分 ====================
            logger.info(">>> 阶段4: 对所有论文进行加权评分...")
            store.record_run_phase(run_id, "score")

            analysis_agent = AnalysisAgent()
            attach_health_recorder = getattr(analysis_agent, "set_health_recorder", None)
            if callable(attach_health_recorder):
                attach_health_recorder(llm_health_recorder)
            scored_papers_by_source: Dict[str, List[Dict[str, Any]]] = {}

            translation_cache = {}
            cache_lock = threading.Lock()
            stage_errors = []
            logger.debug("翻译缓存已启用")

            for source, papers in papers_by_source.items():
                if not papers:
                    continue

                logger.info(f"  评分数据源 [{source}]: {len(papers)} 篇论文")
                scored_papers = []

                if settings.ENABLE_CONCURRENCY and len(papers) > 1:
                    logger.info(f"    使用并发模式 (workers={settings.CONCURRENCY_WORKERS})")
                    with tqdm(
                        total=len(papers), desc=f"📊 [{source}] 评分", unit="篇", ncols=100
                    ) as pbar:
                        with ThreadPoolExecutor(
                            max_workers=settings.CONCURRENCY_WORKERS
                        ) as executor:
                            futures = {
                                executor.submit(
                                    _score_or_hydrate_paper,
                                    run_id,
                                    source,
                                    paper,
                                    analysis_agent,
                                    all_keywords,
                                    translation_cache,
                                    cache_lock,
                                    store,
                                    (
                                        None
                                        if store or search_agent is None
                                        else search_agent.get_previous_processed_version(
                                            paper.paper_id, source
                                        )
                                    ),
                                ): paper
                                for paper in papers
                            }
                            for future in as_completed(futures):
                                try:
                                    result = future.result()
                                    scored_papers.append(result)
                                except Exception as e:
                                    paper = futures[future]
                                    logger.warning(
                                        "论文评分/翻译本次失败，已保留供下次重试 (%s...): %s",
                                        paper.title[:30],
                                        e,
                                    )
                                    if store:
                                        stage = e.stage if isinstance(e, PaperStageError) else "score"
                                        store.update_error(
                                            run_id, source, paper.paper_id, str(e), stage=stage
                                        )
                                    stage_errors.append((source, paper.paper_id, str(e)))
                                pbar.update(1)
                else:
                    with tqdm(
                        total=len(papers), desc=f"📊 [{source}] 评分", unit="篇", ncols=100
                    ) as pbar:
                        for idx, paper in enumerate(papers, 1):
                            pbar.set_description(f"📊 [{source}] [{idx}/{len(papers)}]")
                            pbar.set_postfix_str(f"{paper.title[:35]}...")

                            try:
                                result = _score_or_hydrate_paper(
                                    run_id,
                                    source,
                                    paper,
                                    analysis_agent,
                                    all_keywords,
                                    translation_cache,
                                    cache_lock,
                                    store,
                                    learned_terms,
                                    (
                                        None
                                        if store or search_agent is None
                                        else search_agent.get_previous_processed_version(
                                            paper.paper_id, source
                                        )
                                    ),
                                )
                            except Exception as e:
                                logger.warning(
                                    "论文评分/翻译本次失败，已保留供下次重试 (%s...): %s",
                                    paper.title[:30],
                                    e,
                                )
                                if store:
                                    stage = e.stage if isinstance(e, PaperStageError) else "score"
                                    store.update_error(
                                        run_id, source, paper.paper_id, str(e), stage=stage
                                    )
                                stage_errors.append((source, paper.paper_id, str(e)))
                                pbar.update(1)
                                continue

                            scored_papers.append(result)
                            pbar.update(1)

                scored_papers_by_source[source] = scored_papers

                qualified_count = sum(1 for p in scored_papers if p["score_response"].is_qualified)
                logger.info(f"    [{source}] 评分完成: {qualified_count}/{len(papers)} 篇及格")

            if stage_errors:
                logger.warning(
                    "评分/翻译阶段有 %s 篇失败；已写入 SQLite 待重试，其余完成论文将继续生成报告",
                    len(stage_errors),
                )

            if translation_cache:
                cache_savings = total_papers_count - len(translation_cache)
                if cache_savings > 0:
                    logger.info(f"  翻译缓存节省了 {cache_savings} 次API调用")

            # ==================== 阶段5: 深度分析及格论文 ====================
            store.record_run_phase(run_id, "analyze")
            analyses_by_source: Dict[str, List[Dict[str, Any]]] = {}
            analysis_errors = []

            if not settings.DAILY_ENABLE_DEEP_ANALYSIS:
                logger.info(
                    ">>> 阶段5: 深度分析已通过配置关闭，仅保留评分与列表输出"
                )
            else:
                for source, scored_papers in scored_papers_by_source.items():
                    qualified_papers = [p for p in scored_papers if p["score_response"].is_qualified]

                    if not qualified_papers:
                        logger.info(f">>> 阶段5: [{source}] 没有及格论文，跳过深度分析")
                        continue

                    papers_with_pdf = []
                    for p in qualified_papers:
                        paper_meta = p.get("paper_metadata")
                        if paper_meta and paper_meta.has_pdf_access():
                            papers_with_pdf.append(p)

                    if not papers_with_pdf:
                        logger.info(
                            f">>> 阶段5: [{source}] {len(qualified_papers)} 篇及格论文均无PDF可用，跳过深度分析"
                        )
                        continue

                    logger.info(
                        f">>> 阶段5: [{source}] 深度分析 {len(papers_with_pdf)}/{len(qualified_papers)} 篇有PDF的及格论文..."
                    )

                    qualified_papers_with_analysis = []
                    papers_to_analyze = []

                    for paper_info in papers_with_pdf:
                        cached_analysis = None
                        if store:
                            record = store.get_paper_record(source, paper_info["paper_id"])
                            cached_analysis = store.hydrate_analysis(record)

                        if cached_analysis:
                            qualified_papers_with_analysis.append(
                                {
                                    "paper_id": paper_info["paper_id"],
                                    "analysis": cached_analysis,
                                }
                            )
                            logger.debug(f"复用已持久化深度分析: {paper_info['title'][:30]}...")
                        else:
                            papers_to_analyze.append(paper_info)

                    if papers_to_analyze and len(papers_to_analyze) != len(papers_with_pdf):
                        logger.info(
                            f"    [{source}] 复用 {len(papers_with_pdf) - len(papers_to_analyze)} 篇已完成深度分析"
                        )

                    if settings.ENABLE_CONCURRENCY and len(papers_to_analyze) > 1:
                        logger.info(f"    使用并发模式 (workers={settings.CONCURRENCY_WORKERS})")
                        with tqdm(
                            total=len(papers_to_analyze),
                            desc=f"🔬 [{source}] 深度分析",
                            unit="篇",
                            ncols=100,
                        ) as pbar:
                            with ThreadPoolExecutor(
                                max_workers=settings.CONCURRENCY_WORKERS
                            ) as executor:
                                futures = {
                                    executor.submit(
                                        _deep_analyze_single_paper, paper_info, analysis_agent
                                    ): paper_info
                                    for paper_info in papers_to_analyze
                                }
                                for future in as_completed(futures):
                                    paper_info = futures[future]
                                    try:
                                        result = future.result()
                                        if result:
                                            qualified_papers_with_analysis.append(
                                                {
                                                    "paper_id": result["paper_id"],
                                                    "analysis": result["analysis"],
                                                }
                                            )
                                            if store:
                                                store.update_analysis(
                                                    run_id,
                                                    source,
                                                    result["paper_id"],
                                                    result["analysis"],
                                                    analysis_input_fingerprint=(
                                                        paper_info.get("stage_fingerprints", {}).get("analysis")
                                                    ),
                                                )
                                            pm = result.get("paper_meta")
                                            if pm and pm.arxiv_id:
                                                pbar.write(
                                                    f"  ✓ 完成 (via arXiv {pm.arxiv_id}): {result['title'][:50]}..."
                                                )
                                            else:
                                                pbar.write(f"  ✓ 完成: {result['title'][:55]}...")
                                        else:
                                            if store:
                                                store.update_error(
                                                    run_id,
                                                    source,
                                                    paper_info["paper_id"],
                                                    "深度分析未返回结果",
                                                    stage="analysis",
                                                )
                                            analysis_errors.append(
                                                (source, paper_info["paper_id"], "深度分析未返回结果")
                                            )
                                            pbar.write(f"  ✗ 失败: {paper_info['title'][:55]}...")
                                    except Exception as e:
                                        logger.warning(
                                            "深度分析本次失败，已保留供下次重试 (%s...): %s",
                                            paper_info["title"][:30],
                                            e,
                                        )
                                        if store:
                                            store.update_error(
                                                run_id,
                                                source,
                                                paper_info["paper_id"],
                                                str(e),
                                                stage=(
                                                    e.stage if isinstance(e, PaperStageError) else "analysis"
                                                ),
                                            )
                                        analysis_errors.append(
                                            (source, paper_info["paper_id"], str(e))
                                        )
                                        pbar.write(f"  ✗ 异常: {paper_info['title'][:55]}...")
                                    pbar.update(1)
                    else:
                        with tqdm(
                            total=len(papers_to_analyze),
                            desc=f"🔬 [{source}] 深度分析",
                            unit="篇",
                            ncols=100,
                        ) as pbar:
                            for idx, paper_info in enumerate(papers_to_analyze, 1):
                                pbar.set_description(f"🔬 [{source}] [{idx}/{len(papers_to_analyze)}]")
                                pbar.set_postfix_str(f"{paper_info['title'][:35]}...")

                                try:
                                    result = _deep_analyze_single_paper(paper_info, analysis_agent)
                                    qualified_papers_with_analysis.append(
                                        {"paper_id": result["paper_id"], "analysis": result["analysis"]}
                                    )
                                    if store:
                                        store.update_analysis(
                                            run_id,
                                            source,
                                            result["paper_id"],
                                            result["analysis"],
                                            analysis_input_fingerprint=(
                                                paper_info.get("stage_fingerprints", {}).get("analysis")
                                            ),
                                        )
                                    pm = result.get("paper_meta")
                                    if pm and pm.arxiv_id:
                                        pbar.write(
                                            f"  ✓ 完成 (via arXiv {pm.arxiv_id}): {result['title'][:50]}..."
                                        )
                                    else:
                                        pbar.write(f"  ✓ 完成: {result['title'][:55]}...")
                                except Exception as e:
                                    logger.warning(
                                        "深度分析本次失败，已保留供下次重试 (%s...): %s",
                                        paper_info["title"][:30],
                                        e,
                                    )
                                    if store:
                                        store.update_error(
                                            run_id,
                                            source,
                                            paper_info["paper_id"],
                                            str(e),
                                            stage=(
                                                e.stage if isinstance(e, PaperStageError) else "analysis"
                                            ),
                                        )
                                    analysis_errors.append((source, paper_info["paper_id"], str(e)))
                                    pbar.write(f"  ✗ 失败: {paper_info['title'][:55]}...")

                                pbar.update(1)

                    analyses_by_source[source] = qualified_papers_with_analysis
                    logger.info(
                        f"    [{source}] 深度分析完成: {len(qualified_papers_with_analysis)}/{len(papers_with_pdf)} 篇成功"
                    )

            if analysis_errors:
                logger.warning(
                    "深度分析阶段有 %s 篇失败；这些论文将不进入本次报告，并会在下次运行重试",
                    len(analysis_errors),
                )

            reportable_papers_by_source, withheld_analysis = _reportable_scored_papers(
                scored_papers_by_source, analyses_by_source
            )
            if withheld_analysis:
                logger.warning(
                    "本次报告跳过 %s 篇尚未完成深度分析的论文，避免将不完整数据标为已交付",
                    len(withheld_analysis),
                )
            if not any(reportable_papers_by_source.values()):
                details = "; ".join(
                    f"{source}:{paper_id} - {error}"
                    for source, paper_id, error in (stage_errors + analysis_errors)
                )
                raise RuntimeError(
                    "本次没有可安全交付的论文，未生成报告；失败论文已保留供重试"
                    + (f": {details}" if details else "")
                )

            # ==================== 阶段6: 生成分数据源报告 ====================
            logger.info(">>> 阶段6: 生成分数据源研究报告...")
            store.record_run_phase(run_id, "report")

            reporter = Reporter()
            # 过去日报的时间戳 = 目标日期 + 当前时刻（用户指定的补跑语义）。
            # 历史遗漏补充报告会显式传入该自然周周日 + 实际运行时刻，因而
            # 报告查看页按历史周排序，同时同周多批可由微秒时间戳安全区分。
            effective_report_timestamp = report_timestamp or datetime.now()
            if run_kind == "backfill":
                effective_report_timestamp = datetime.combine(
                    target_date, datetime.now().time()
                )
            report_paths = reporter.generate_reports_by_source(
                scored_papers_by_source=reportable_papers_by_source,
                keywords_dict=all_keywords,
                analyses_by_source=analyses_by_source,
                token_usage=token_counter.get_summary() if settings.TOKEN_TRACKING_ENABLED else None,
                report_kind="supplement" if run_kind == "supplement" else "daily",
                report_timestamp=effective_report_timestamp,
            )
            _validate_report_paths(report_paths, reportable_papers_by_source)

            # Commit the critical daily-delivery state before optional keyword
            # trend post-processing.  A later interruption therefore cannot turn
            # a valid report into a second day's "new" paper batch.
            run_result = _build_daily_run_result(
                total_papers_count,
                reportable_papers_by_source,
                analyses_by_source,
                report_paths,
                deferred_paper_count=deferred_paper_count,
            )
            run_result.issues = _notification_issue_messages(
                stage_errors,
                analysis_errors,
                withheld_analysis,
                deferred_paper_count=run_result.deferred_paper_count,
                supplement_fetch_failures=supplement_fetch_failures,
            )
            if store and run_id:
                delivered_papers_by_source = _delivered_papers_for_finalization(
                    reportable_papers_by_source, analyses_by_source
                )
                if run_kind == "backfill":
                    # ``pending_paper_count`` was captured while this workflow
                    # gate was held.  Only the exact delivered set leaves the
                    # date-scoped queue, so this also retains stage failures
                    # for the next automatic batch.
                    delivered_count = sum(
                        len(papers)
                        for papers in delivered_papers_by_source.values()
                    )
                    run_result.deferred_paper_count = max(
                        0, pending_paper_count - delivered_count
                    )
                    run_result.issues = _notification_issue_messages(
                        stage_errors,
                        analysis_errors,
                        withheld_analysis,
                        deferred_paper_count=run_result.deferred_paper_count,
                        supplement_fetch_failures=supplement_fetch_failures,
                    )
                notification_entries = []
                if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                    try:
                        notifier = notifier or NotifierAgent()
                        notification_entries = _run_result_notification_entries(notifier, run_result)
                    except Exception as exc:
                        # A malformed/temporarily unavailable notifier must not
                        # reopen already analyzed papers.  A normal provider
                        # delivery failure still has its per-channel outbox row.
                        logger.error("无法建立通知 outbox 条目，日报仍将完成: %s", exc)
                maintenance_entry = after_report_sync_maintenance_entry(run_id)
                store.finalize_report_delivery(
                    run_id,
                    report_paths,
                    delivered_papers_by_source,
                    notification_entries,
                    [maintenance_entry] if maintenance_entry is not None else [],
                    report_at=effective_report_timestamp,
                )
                report_delivery_committed = True

                if run_kind == "supplement" and supplement_identities:
                    # Only the papers included in the committed report are
                    # delivered. A transient stage failure must stay visible
                    # and retryable instead of being silently consumed with
                    # the rest of this supplement batch.
                    delivered_identities = {
                        (
                            source,
                            str(
                                paper_info["paper_metadata"].canonical_id
                                or paper_info["paper_id"]
                            ),
                            int(paper_info["paper_metadata"].version or 0),
                        )
                        for source, papers in delivered_papers_by_source.items()
                        for paper_info in papers
                    }
                    delivered_backlog = [
                        identity
                        for identity in supplement_identities
                        if identity in delivered_identities
                    ]
                    retryable_backlog = [
                        identity
                        for identity in supplement_identities
                        if identity not in delivered_identities
                    ]
                    if delivered_backlog:
                        resolved = store.resolve_supplement_backlog(
                            run_id, delivered_backlog, status="delivered"
                        )
                        logger.info("补充运行交付完成，销账积压 %s 条", resolved)
                    if retryable_backlog:
                        retained = store.resolve_supplement_backlog(
                            run_id,
                            retryable_backlog,
                            status="failed",
                            detail="本次补充处理未完成，等待下一次重试",
                        )
                        logger.warning(
                            "补充运行保留 %s 条未完成积压，下一次读取旧历史会重试",
                            retained,
                        )

                # Report delivery has already committed.  A WebDAV failure now
                # only reschedules its own outbox row and can never make this
                # paper version appear as new on a later day.
                try:
                    sync_summary = deliver_pending_after_report_syncs(store, logger)
                    if sync_summary["claimed"]:
                        logger.info(
                            "报告后 WebDAV 同步: 完成 %s，待补发 %s",
                            sync_summary["completed"],
                            sync_summary["deferred"],
                        )
                except Exception as exc:
                    logger.error("报告后 WebDAV 同步调度异常，已保留待补发状态: %s", exc)

            # ==================== 关键词趋势处理（已拆出主流程） ====================
            # 关键词标准化/趋势报告改由 cron 在每天 0 点静默执行
            # （modes/keyword_maintenance.py），避免 LLM 批量调用拖住日报收尾。

            # ==================== 完成 ====================
            logger.info("=" * 80)
            logger.info("✅ 任务完成！")

            for source, scored_papers in reportable_papers_by_source.items():
                logger.info(
                    "  [%s] 抓取: %s | 及格: %s | 深度分析: %s",
                    source,
                    len(scored_papers),
                    run_result.qualified_by_source[source],
                    run_result.analyzed_by_source[source],
                )

            logger.info(
                f"  - 总计: 抓取 {total_papers_count} | 及格 {run_result.total_qualified} | 深度分析 {run_result.total_analyzed}"
            )
            logger.info(f"  - 报告位置: {settings.REPORTS_DIR}")
            logger.info("=" * 80)

            print("\n" + "=" * 80)
            print("🎉 所有任务已完成！")
            print("=" * 80)
            print("📊 统计信息:")

            for source, scored_papers in reportable_papers_by_source.items():
                source_qualified = run_result.qualified_by_source.get(source, 0)
                source_analyzed = run_result.analyzed_by_source.get(source, 0)
                pct = (source_qualified / len(scored_papers) * 100) if scored_papers else 0
                print(f"   [{source.upper()}]")
                print(f"     • 抓取: {len(scored_papers)} 篇")
                print(f"     • 及格: {source_qualified} 篇 ({pct:.1f}%)")
                if search_agent is None or search_agent.can_download_pdf(source):
                    print(f"     • 深度分析: {source_analyzed} 篇")

            print("\n📁 报告位置:")
            for source, path in report_paths.items():
                print(f"   • [{source}] {path}")
            print("=" * 80 + "\n")

            # ==================== 阶段8: 持久化并发送通知 ====================
            # 注意：run 在阶段6 交付提交时已完成（终态），这里不再写阶段
            # 心跳——record_run_phase 也会拒绝为非 running 的 run 写入。
            if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                logger.info(">>> 阶段8: 写入通知 outbox 并发送...")
                notifier = notifier or NotifierAgent()
                if store and run_id:
                    try:
                        delivery_summary = notifier.deliver_pending_run_results(store)
                        logger.info(
                            "通知派发完成: 发送 %s，待补发 %s",
                            delivery_summary["sent"],
                            delivery_summary["deferred"],
                        )
                        workflow_delivery = notifier.deliver_pending_workflow_results(store)
                        if workflow_delivery["claimed"]:
                            logger.info(
                                "长任务通知补发完成: 发送 %s，待补发 %s",
                                workflow_delivery["sent"],
                                workflow_delivery["deferred"],
                            )
                    except Exception as exc:
                        logger.error("通知 outbox 派发异常，已保留待补发记录: %s", exc)
                else:
                    # Explicitly preserve the non-persistence mode, where an
                    # outbox cannot survive restarts.
                    try:
                        notifier.notify(run_result)
                    except Exception as exc:
                        # Notification delivery is a follow-up concern. The
                        # report and compatibility history have already been
                        # committed, so a provider outage must not make the
                        # completed paper batch look retryable.
                        logger.warning("通知发送失败，日报状态仍保持已完成: %s", exc)

            return run_result

        except KeyboardInterrupt:
            logger.warning("\n用户中断程序执行")
            print("\n⚠️  程序已被用户中断")
            if store and run_id and not report_delivery_committed:
                store.fail_run(run_id, "用户中断程序执行")
            return RunResult(
                run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                success=False,
                interrupted=True,
                error_message="用户中断程序执行",
            )
        except Exception as e:
            logger.error(f"程序执行出错: {e}", exc_info=True)
            print(f"\n❌ 程序执行失败: {e}")
            print("详细错误信息已记录到日志文件")
            if store and run_id and not report_delivery_committed:
                store.fail_run(run_id, str(e))
            import traceback

            traceback.print_exc()

            if settings.ENABLE_NOTIFICATIONS and run_kind == "daily":
                try:
                    fail_result = RunResult(
                        run_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        success=False,
                        error_message=str(e),
                    )
                    NotifierAgent().notify(fail_result)
                except Exception:
                    pass

            raise
        finally:
            # 无论成功、失败还是中断，都把本次运行真实消耗的 token 落库；
            # 统计失败绝不影响主流程。
            if store and run_id:
                try:
                    usage = token_counter.get_summary()
                    by_model = usage.get("by_model") or {}
                    if by_model:
                        token_mode = {
                            "daily": "daily_research",
                            "supplement": "supplement_run",
                            "backfill": "backfill_run",
                        }.get(run_kind, str(run_kind or "daily_research"))
                        store.record_token_usage(run_id, by_model, mode=token_mode)
                except Exception:
                    logger.debug("Token 用量记录失败", exc_info=True)

            # 运行收尾后按配置做一次压缩数据库备份；SQLite 是队列与偏好
            # 数据的唯一真相源，备份失败不影响运行结果本身。
            try:
                from utils.backup import run_scheduled_backup

                backup_result = run_scheduled_backup(logger=logger)
                if backup_result and backup_result.get("created"):
                    if backup_result.get("uploaded"):
                        upload_note = "，已上传 WebDAV"
                    elif backup_result.get("skipped_reason") == "already_on_remote":
                        upload_note = "，WebDAV 已有同名副本，跳过上传"
                    elif backup_result.get("skipped_reason"):
                        upload_note = "，WebDAV 增量跳过（内容未变化）"
                    else:
                        upload_note = ""
                    logger.info(
                        "数据库备份完成: %s（%d 字节%s）",
                        backup_result.get("name"),
                        backup_result.get("size_bytes", 0),
                        upload_note,
                    )
            except Exception:
                logger.warning("运行后数据库备份失败", exc_info=True)
