"""v3.2 HTML history importer with an optional complete-repair path.

The normal path parses old daily-report cards once and records their exact
versions in the v4 SQLite delivery ledger.  This is enough to keep future
daily runs from repeating papers that were already delivered.  The optional
complete path additionally imports v3.2 JSON delivery metadata. Its old
``keywords.db`` is never migrated wholesale: it is only a read-only fallback
for raw terms belonging to HTML-confirmed papers whose report card does not
contain extracted keywords. Normalization, aliases, and trend statistics stay
owned by the current SQLite database.

Repeated cards are resolved by their *report artifact timestamp*, not by the
old JSON delivery timestamp.  Therefore a newer archived report always wins
even when both cards share one historical delivery record.  Rows managed by a
v4 run retain priority and are never downgraded by imported archive content.
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# app_state key holding the latest import summary for the WebUI panel.
LEGACY_IMPORT_STATE_KEY = "legacy_import_summary"

_HISTORY_FILE_RE = re.compile(r"^(?P<source>.+)_history\.json$", re.IGNORECASE)

# 报告文件名中的时间戳：ARXIV_Report_2026-03-03_16-10-08[[_392421].html]
_REPORT_TS_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})(?:_(?P<micro>\d+))?"
)
_ARXIV_VERSION_RE = re.compile(r"^(?P<canonical>.+?)(?:v(?P<version>[0-9]+))$")

_CARD_SPLIT_RE = re.compile(r'<div class="card (?:pass|fail)">')
_TITLE_ANCHOR_RE = re.compile(
    r'<div class="card-title"><a href="(?P<url>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>",
    re.DOTALL,
)
_BARE_TITLE_RE = re.compile(r'<div class="card-title">(?P<title>.*?)</div>', re.DOTALL)
_TITLE_INDEX_RE = re.compile(r"^\s*\d+\.\s*")
_BADGE_RE = re.compile(r'<span class="badge (?:pass|fail)">')
_SCORE_V1_RE = re.compile(
    r'Score:</span> <span class="score">(?P<total>-?[\d.]+)</span> / (?P<passing>[\d.]+)'
)
_SCORE_V2_RE = re.compile(
    r'Core relevance:</span> <span class="score">(?P<relevance>[\d.]+)</span> '
    r"/ (?P<threshold>[\d.]+)"
    r"(?:\s*\| Ranking: (?P<ranking>[\d.]+))?"
)
_FIELD_RE = re.compile(
    r'<div class="field"><span class="field-label">(?P<label>[^<]+):</span> '
    r"(?P<value>.*?)</div>",
    re.DOTALL,
)
_SS_TLDR_RE = re.compile(
    r'<div class="tldr"><strong>Semantic Scholar TL;DR:</strong> (?P<text>.*?)</div>',
    re.DOTALL,
)
_TLDR_RE = re.compile(
    r'<div class="tldr"><strong>TL;DR:</strong> (?P<text>.*?)</div>', re.DOTALL
)
_DETAILS_RE = re.compile(
    r"<details[^>]*><summary>(?P<summary>[^<]*)</summary>\s*"
    r'<div class="analysis-content">(?P<content>.*?)</div>\s*</details>',
    re.DOTALL,
)
_SCORE_TABLE_ROW_RE = re.compile(r"<tr[^>]*>(?P<row>.*?)</tr>", re.DOTALL)
_SCORE_CELL_RE = re.compile(r"<td[^>]*>(?P<cell>.*?)</td>", re.DOTALL)
_REASONING_RE = re.compile(r"<strong>评分理由:</strong> (?P<text>.*?)</p>", re.DOTALL)
_AUTHOR_BONUS_RE = re.compile(r"<td[^>]*>(?P<bonus>\+[\d.]+)</td>")
_EXPERTS_RE = re.compile(r"专家: (?P<experts>.*?)</td>", re.DOTALL)
_ANALYSIS_SECTION_RE = re.compile(r"^深度分析")
_ANALYSIS_PARAGRAPH_RE = re.compile(r"<p><strong>(?P<label>[^<]+):</strong>\s*(?P<value>.*?)</p>", re.DOTALL)
_ANALYSIS_LIST_RE = re.compile(r"\s*<ul>(?P<items>.*?)</ul>", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<li>(?P<item>.*?)</li>", re.DOTALL)

# A progress callback is deliberately best-effort.  Importing an archive must
# not fail just because a WebUI heartbeat update cannot be persisted for a
# moment (for example while a mounted SQLite volume is briefly busy).
LegacyProgressCallback = Callable[..., None]


def _emit_progress(
    callback: Optional[LegacyProgressCallback],
    phase: str,
    detail: str = "",
    current: Optional[int] = None,
    total: Optional[int] = None,
) -> None:
    """Report one user-visible import checkpoint without affecting import data."""
    if callback is None:
        return
    try:
        callback(
            phase=phase,
            detail=detail,
            current=current,
            total=total,
        )
    except Exception as exc:  # pragma: no cover - observer failures are non-fatal
        logger.debug("[LegacyImport] 进度回调失败: %s", exc)

# 深度分析卡片里的展示标签 → 字段 id。旧报告使用英文标签，新模板使用
# name/label；模板映射在导入时动态补充，这里只兜底历史固定值。
_FALLBACK_ANALYSIS_LABELS = {
    "chinese title": "chinese_title",
    "中文标题": "chinese_title",
    "summary": "summary",
    "摘要": "summary",
    "内容摘要": "summary",
    "innovations": "innovations",
    "创新点": "innovations",
    "methodology": "methodology",
    "方法": "methodology",
    "key results": "key_results",
    "关键结果": "key_results",
    "tech stack": "tech_stack",
    "技术栈": "tech_stack",
    "strengths": "strengths",
    "优点": "strengths",
    "limitations": "limitations",
    "局限": "limitations",
    "局限性": "limitations",
    "relevance to keywords": "relevance_to_keywords",
    "与研究方向的相关性": "relevance_to_keywords",
    "future work": "future_work",
    "未来工作": "future_work",
    "label": "label",
}


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment or "")
    return html_module.unescape(text).strip()


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_text(fragment: str) -> str:
    return _collapse_ws(_strip_tags(fragment))


def _report_timestamp(path: Path) -> Optional[datetime]:
    match = _REPORT_TS_RE.search(path.stem)
    if not match:
        return None
    micro = match.group("micro") or ""
    if micro:
        micro = (micro + "000000")[:6]
    time_text = match.group("time").replace("-", ":")
    stamp = f"{match.group('date')}T{time_text}"
    try:
        return datetime.fromisoformat(stamp + (f".{micro}" if micro else ""))
    except ValueError:
        return None


def _source_from_path(path: Path, html_root: Path) -> Optional[str]:
    """Report source comes from its per-source directory (or filename prefix)."""
    relative = path.parent.relative_to(html_root)
    if relative.parts and relative.parts[0] != ".":
        return str(relative.parts[0]).strip().lower()
    match = re.match(r"(?P<source>.+?)_Report_", path.stem, re.IGNORECASE)
    return match.group("source").strip().lower() if match else None


def _split_arxiv_version(paper_id: str) -> Tuple[str, Optional[int]]:
    """Keep report-card parsing usable in the slim WebUI image.

    The worker's ``sources.base_source`` exports the same compatibility split,
    but importing that package from the WebUI would pull in network clients
    which deliberately are not shipped there.
    """
    value = str(paper_id or "").strip()
    match = _ARXIV_VERSION_RE.match(value)
    if not match:
        return value, None
    return match.group("canonical"), int(match.group("version"))


def _arxiv_pdf_url(paper_id: str) -> Optional[str]:
    canonical, _ = _split_arxiv_version(paper_id)
    return f"https://arxiv.org/pdf/{canonical}.pdf"


def _identity_from_url(url: str) -> Optional[Dict[str, Any]]:
    """Best-effort identity from a card link (arXiv abs page or DOI)."""
    arxiv_match = re.search(r"arxiv\.org/abs/([^/?#]+)", url or "", re.IGNORECASE)
    if arxiv_match:
        paper_id = arxiv_match.group(1)
        canonical, version = _split_arxiv_version(paper_id)
        return {
            "kind": "arxiv",
            "paper_id": paper_id,
            "canonical_id": canonical,
            "version": version if version is not None else 0,
        }
    doi_match = re.search(r"doi\.org/(?P<doi>.+)$", url or "", re.IGNORECASE)
    if doi_match:
        doi = doi_match.group("doi").strip().rstrip("/.")
        return {
            "kind": "doi",
            "paper_id": url.strip(),
            "canonical_id": doi.lower(),
            "version": 0,
        }
    return None


def normalize_doi_key(value: str) -> str:
    """Normalize a DOI URL/bare DOI for history ↔ card matching."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    return text.strip().rstrip("/.").lower()


def _parse_history_key(source: str, key: str) -> Optional[Tuple[str, int]]:
    """历史键 → (canonical, version)。arXiv 兼容 id vN 与 canonical@vN 两种。"""
    text = str(key or "").strip()
    if not text:
        return None
    if source == "arxiv":
        if "@v" in text:
            canonical, _, version = text.rpartition("@v")
            if canonical:
                try:
                    return canonical, int(version)
                except ValueError:
                    return canonical, 0
        canonical, version = _split_arxiv_version(text)
        return canonical, version if version is not None else 0
    return normalize_doi_key(text), 0


def load_legacy_history_files(
    history_dir: Path,
    *,
    progress_callback: Optional[LegacyProgressCallback] = None,
) -> Dict[str, Dict[Tuple[str, int], str]]:
    """读取旧 JSON 历史：{source: {(canonical, version): delivered_at ISO}}.

    ``progress_callback`` 仅供完整旧历史导入显示当前文件；时间段扫描等
    复用读取逻辑的调用方无需提供它。
    """
    result: Dict[str, Dict[Tuple[str, int], str]] = {}
    directory = Path(history_dir)
    if not directory.is_dir():
        _emit_progress(progress_callback, "legacy_history", "未找到旧 JSON 历史目录", 0, 0)
        return result
    files = [
        item
        for item in sorted(directory.glob("*_history.json"))
        if not item.name.lower().startswith("history_old")
    ]
    _emit_progress(
        progress_callback,
        "legacy_history",
        f"发现 {len(files)} 个旧 JSON 历史文件",
        0,
        len(files),
    )
    for index, item in enumerate(files, start=1):
        if item.name.lower().startswith("history_old"):
            # v1.0 存档文件，不属于 v3.2 活跃历史。
            continue
        _emit_progress(
            progress_callback,
            "legacy_history",
            f"读取 {item.name}",
            index - 1,
            len(files),
        )
        match = _HISTORY_FILE_RE.match(item.name)
        source = match.group("source").strip().lower() if match else "unknown"
        try:
            raw = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[LegacyImport] 跳过无法解析的历史文件 %s: %s", item.name, exc)
            continue
        if not isinstance(raw, dict):
            continue
        entries: Dict[Tuple[str, int], str] = {}
        for key, delivered_at in raw.items():
            identity = _parse_history_key(source, str(key))
            if identity is None:
                continue
            if not isinstance(delivered_at, str) or not delivered_at.strip():
                continue
            entries[identity] = delivered_at.strip()
        result[source] = entries
        logger.info("[LegacyImport] 历史文件 %s: %s 条", item.name, len(entries))
        _emit_progress(
            progress_callback,
            "legacy_history",
            f"已读取 {item.name}（{len(entries)} 条）",
            index,
            len(files),
        )
    return result


def _analysis_label_map() -> Dict[str, str]:
    """展示标签 → 深度分析字段 id（模板动态值优先，历史标签兜底）。"""
    mapping = dict(_FALLBACK_ANALYSIS_LABELS)
    try:
        from config import settings

        template = settings.load_report_template("deep_analysis_template.json")
        for module in template.get("modules", []):
            module_id = module.get("id")
            if not isinstance(module_id, str) or not module_id:
                continue
            for field in ("label", "name"):
                label = module.get(field)
                if isinstance(label, str) and label.strip():
                    mapping.setdefault(label.strip().lower(), module_id)
                    mapping.setdefault(label.strip(), module_id)
    except Exception:  # pragma: no cover - 模板缺失时仅用静态映射
        pass
    return mapping


def _parse_analysis_sections(content: str) -> Dict[str, Any]:
    """深度分析 <p><strong>标签:</strong> …</p>（含随后的 <ul> 列表）→ 字段字典。"""
    label_map = _analysis_label_map()
    analysis: Dict[str, Any] = {}

    def assign(label: str, value: Any) -> None:
        if value is None:
            return
        key = label_map.get(label.strip()) or label_map.get(label.strip().lower())
        analysis[key or label.strip()] = value

    pos = 0
    while True:
        match = _ANALYSIS_PARAGRAPH_RE.search(content, pos)
        if not match:
            break
        label = html_module.unescape(match.group("label")).strip()
        inline_value = _clean_text(match.group("value"))
        end = match.end()
        if inline_value:
            assign(label, inline_value)
        else:
            list_match = _ANALYSIS_LIST_RE.match(content, end)
            if list_match:
                items = [
                    _clean_text(item_match.group("item"))
                    for item_match in _LIST_ITEM_RE.finditer(list_match.group("items"))
                ]
                items = [item for item in items if item]
                assign(label, items or None)
                end = list_match.end()
        pos = end
    return analysis


def _parse_score_details(content: str) -> Dict[str, Any]:
    """评分详情表格 → keyword_scores / author_bonus / expert_authors_found."""
    keyword_scores: Dict[str, float] = {}
    negative_keyword_scores: Dict[str, float] = {}
    negative_keyword_weights: Dict[str, float] = {}
    author_bonus = 0.0
    experts: List[str] = []
    for row_match in _SCORE_TABLE_ROW_RE.finditer(content):
        cells = [
            _clean_text(cell.group("cell"))
            for cell in _SCORE_CELL_RE.finditer(row_match.group("row"))
        ]
        if len(cells) >= 4 and cells[0] not in ("关键词",):
            # 列顺序：关键词 | 权重 | 相关度 | 得分（作者行为变体）
            if cells[1] == "-" and cells[2].startswith("+"):
                try:
                    author_bonus = float(cells[2].lstrip("+"))
                except ValueError:
                    pass
                expert_text = cells[3]
                prefix = "专家:"
                if expert_text.startswith(prefix):
                    experts = [
                        item.strip()
                        for item in expert_text[len(prefix):].split(",")
                        if item.strip()
                    ]
                continue
            try:
                relevance = float(cells[2].split("/")[0])
            except (ValueError, IndexError):
                continue
            if cells[0].startswith("不关注："):
                keyword = cells[0].removeprefix("不关注：").strip()
                try:
                    penalty_weight = abs(float(cells[1]))
                except ValueError:
                    continue
                if keyword:
                    negative_keyword_scores[keyword] = relevance
                    negative_keyword_weights[keyword] = penalty_weight
                continue
            keyword_scores[cells[0]] = relevance
    return {
        "keyword_scores": keyword_scores,
        "negative_keyword_scores": negative_keyword_scores,
        "negative_keyword_weights": negative_keyword_weights,
        "author_bonus": author_bonus,
        "expert_authors_found": experts,
    }


def parse_legacy_report_cards(
    html_root: Path,
    *,
    progress_callback: Optional[LegacyProgressCallback] = None,
) -> List[Dict[str, Any]]:
    """解析所有旧 HTML 日报，按报告时间升序返回卡片记录（新报告在后）。"""
    root = Path(html_root)
    if not root.is_dir():
        _emit_progress(progress_callback, "legacy_reports", "未找到旧 HTML 报告目录", 0, 0)
        return []
    files: List[Tuple[datetime, Path, str]] = []
    for path in root.rglob("*.html"):
        source = _source_from_path(path, root)
        if not source:
            continue
        stamp = _report_timestamp(path)
        if stamp is None:
            logger.warning("[LegacyImport] 文件名无时间戳，跳过: %s", path.name)
            continue
        files.append((stamp, path, source))
    files.sort(key=lambda item: item[0])

    _emit_progress(
        progress_callback,
        "legacy_reports",
        f"发现 {len(files)} 份旧 HTML 报告",
        0,
        len(files),
    )

    cards: List[Dict[str, Any]] = []
    for index, (stamp, path, source) in enumerate(files, start=1):
        _emit_progress(
            progress_callback,
            "legacy_reports",
            f"解析 {path.name}",
            index - 1,
            len(files),
        )
        report_cards = _parse_report_file(path, source, stamp)
        cards.extend(report_cards)
        logger.info(
            "[LegacyImport] HTML 报告 %s/%s: %s，解析 %s 张卡片",
            index,
            len(files),
            path.name,
            len(report_cards),
        )
        _emit_progress(
            progress_callback,
            "legacy_reports",
            f"已解析 {path.name}（{len(report_cards)} 张卡片）",
            index,
            len(files),
        )
    return cards


def _parse_report_file(path: Path, source: str, stamp: datetime) -> List[Dict[str, Any]]:
    """Read one report and reconstruct its cards without changing the archive."""
    try:
        html_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("[LegacyImport] 读取报告失败 %s: %s", path, exc)
        return []
    cards: List[Dict[str, Any]] = []
    for chunk in _CARD_SPLIT_RE.split(html_text)[1:]:
        card = _parse_card(chunk, source, stamp, path)
        if card is not None:
            cards.append(card)
    return cards


def parse_legacy_report_file(
    path: Path,
    *,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Parse one saved daily report for display-time card actions.

    This lightweight helper is shared by the WebUI and the full import. It
    deliberately does not need the worker's source clients, so the thin WebUI
    image can restore 👍/👎 controls for historical reports after an upgrade.
    """
    report_path = Path(path)
    source_name = str(source or "").strip().lower()
    if not source_name:
        match = re.match(r"(?P<source>.+?)_Report_", report_path.stem, re.IGNORECASE)
        source_name = match.group("source").strip().lower() if match else ""
    if not source_name:
        return []
    stamp = _report_timestamp(report_path)
    if stamp is None:
        try:
            stamp = datetime.fromtimestamp(report_path.stat().st_mtime)
        except OSError as exc:
            logger.warning("[LegacyImport] 无法读取报告时间 %s: %s", report_path, exc)
            return []
    return _parse_report_file(report_path, source_name, stamp)


def _parse_card(chunk: str, source: str, stamp: datetime, path: Path) -> Optional[Dict[str, Any]]:
    title_match = _TITLE_ANCHOR_RE.search(chunk)
    url = ""
    if title_match:
        url = html_module.unescape(title_match.group("url")).strip()
        title = _clean_text(title_match.group("title"))
        title = _TITLE_INDEX_RE.sub("", title)
    else:
        bare = _BARE_TITLE_RE.search(chunk)
        if not bare:
            return None
        title = _TITLE_INDEX_RE.sub("", _clean_text(bare.group("title")))
    if not title:
        return None

    identity = _identity_from_url(url) if url else None
    if identity is None:
        # 没有 URL 的卡片无法对齐旧历史，保留标题但仍尝试字段解析。
        identity = {"kind": "unknown", "paper_id": title, "canonical_id": title.lower(), "version": 0}
    if identity["kind"] == "unknown" and source == "arxiv":
        # 标题兜底无法构造 arXiv 身份，交给调用方按缺失记录。
        identity = {"kind": "title", "paper_id": title, "canonical_id": "", "version": 0}

    is_qualified = '<span class="badge pass">' in chunk

    fields: Dict[str, str] = {}
    for field_match in _FIELD_RE.finditer(chunk):
        label = html_module.unescape(field_match.group("label")).strip()
        fields[label] = _clean_text(field_match.group("value"))

    published: Optional[datetime] = None
    published_text = fields.get("Published")
    if published_text:
        try:
            published = datetime.strptime(published_text[:10], "%Y-%m-%d")
        except ValueError:
            published = None
    version_from_field: Optional[int] = None
    version_text = fields.get("Version")
    if version_text:
        match = re.search(r"v(\d+)", version_text)
        if match:
            version_from_field = int(match.group(1))

    abstract = ""
    abstract_cn = ""
    score_details_raw = ""
    extracted_keywords: List[str] = []
    analysis: Dict[str, Any] = {}
    for details in _DETAILS_RE.finditer(chunk):
        summary = html_module.unescape(details.group("summary")).strip()
        content = details.group("content")
        if summary == "摘要翻译":
            abstract_cn = _clean_text(re.sub(r"</?p>", " ", content))
        elif summary == "Abstract":
            abstract = _clean_text(re.sub(r"</?p>", " ", content))
        elif summary == "评分详情":
            score_details_raw = content
        elif summary == "关键词":
            extracted_keywords = [
                item.strip()
                for item in _clean_text(content).split(",")
                if item.strip()
            ]
        elif _ANALYSIS_SECTION_RE.match(summary):
            analysis = _parse_analysis_sections(content)

    ss_tldr = ""
    ss_match = _SS_TLDR_RE.search(chunk)
    if ss_match:
        ss_tldr = _clean_text(ss_match.group("text"))
    tldr = ""
    tldr_match = _TLDR_RE.search(chunk)
    if tldr_match:
        tldr = _clean_text(tldr_match.group("text"))

    score_payload: Dict[str, Any] = {}
    v2_match = _SCORE_V2_RE.search(chunk)
    v1_match = _SCORE_V1_RE.search(chunk)
    if v2_match:
        threshold = float(v2_match.group("threshold"))
        ranking = float(v2_match.group("ranking") or v2_match.group("relevance"))
        score_payload = {
            "strategy_id": "core_relevance_v2",
            "total_score": ranking,
            "ranking_score": ranking,
            "relevance_score": float(v2_match.group("relevance")),
            "qualification_threshold": threshold,
            "passing_score": threshold,
            "is_qualified": is_qualified,
        }
    elif v1_match:
        score_payload = {
            "total_score": float(v1_match.group("total")),
            "passing_score": float(v1_match.group("passing")),
            "is_qualified": is_qualified,
        }
    if score_payload:
        details = _parse_score_details(score_details_raw)
        if details["negative_keyword_scores"] and not v2_match:
            score_payload["strategy_id"] = "weighted_keyword_with_penalties_v1"
            score_payload["negative_keyword_scores"] = details["negative_keyword_scores"]
            score_payload["negative_keyword_weights"] = details["negative_keyword_weights"]
            score_payload["negative_keyword_penalty"] = sum(
                score * details["negative_keyword_weights"][keyword]
                for keyword, score in details["negative_keyword_scores"].items()
            )
        reasoning_match = _REASONING_RE.search(chunk)
        score_payload.update(
            {
                "keyword_scores": details["keyword_scores"],
                "author_bonus": details["author_bonus"],
                "expert_authors_found": details["expert_authors_found"],
                "reasoning": _clean_text(reasoning_match.group("text")) if reasoning_match else "",
                "tldr": tldr,
                "extracted_keywords": extracted_keywords,
            }
        )

    return {
        "source": source,
        "paper_id": identity["paper_id"],
        "canonical_id": identity["canonical_id"],
        "version": version_from_field if version_from_field is not None else identity["version"],
        "identity_kind": identity["kind"],
        "report_path": str(path),
        "report_at": stamp,
        "title": title,
        "url": url,
        "authors": [item.strip() for item in fields.get("Authors", "").split(",") if item.strip()],
        "published": published,
        "published_text": published_text or stamp.strftime("%Y-%m-%d"),
        "abstract": abstract,
        "abstract_cn": abstract_cn,
        "ss_tldr": ss_tldr,
        "score_payload": score_payload,
        "analysis": analysis,
    }


def _paper_json_from_card(card: Dict[str, Any]) -> Dict[str, Any]:
    paper_id = card["paper_id"]
    is_arxiv_identity = card.get("identity_kind") == "arxiv"
    is_doi_identity = card.get("identity_kind") == "doi"
    pdf_url = _arxiv_pdf_url(paper_id) if is_arxiv_identity else None
    published = card["published"] or card["report_at"]
    return {
        "paper_id": paper_id,
        "title": card["title"],
        "authors": card["authors"],
        "abstract": card["abstract"],
        "published_date": published.isoformat(),
        "url": card["url"],
        "source": card["source"],
        "pdf_url": pdf_url,
        "doi": card["canonical_id"] if is_doi_identity else None,
        "journal": None,
        "categories": [],
        "semantic_scholar_tldr": card["ss_tldr"] or None,
        "arxiv_id": paper_id if is_arxiv_identity else None,
        "arxiv_url": card["url"] if is_arxiv_identity else None,
        "canonical_id": card["canonical_id"],
        "version": card["version"] or None,
    }


def _dedupe_keyword_terms(values: Any) -> List[str]:
    """Return stable, display-preserving keyword terms from one loose input."""
    if not isinstance(values, (list, tuple, set)):
        return []
    terms: List[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        term = _collapse_ws(value)
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def _card_extracted_keywords(card: Dict[str, Any]) -> List[str]:
    payload = card.get("score_payload")
    if not isinstance(payload, dict):
        return []
    return _dedupe_keyword_terms(payload.get("extracted_keywords"))


def _open_legacy_keywords_readonly(
    source_path: Path,
) -> Tuple[Optional[sqlite3.Connection], str, Optional[str]]:
    """Open the optional v3.2 keyword cache without changing source files."""
    path = Path(source_path)
    if not path.is_file():
        return None, "not_found", None
    try:
        resolved = path.resolve()
        conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA schema_version").fetchone()
        return conn, "readonly", None
    except (OSError, sqlite3.Error) as normal_error:
        # A clean read-only NAS snapshot can require immutable=1 for a
        # WAL-mode database. Never ignore a non-empty WAL: its contents may
        # hold newer records than the main database.
        try:
            wal_path = path.with_name(path.name + "-wal")
            has_live_wal = wal_path.is_file() and wal_path.stat().st_size > 0
        except OSError:
            has_live_wal = True
        if has_live_wal:
            return None, "unreadable", "旧关键词库存在未合并的 WAL 文件"
        try:
            resolved = path.resolve()
            conn = sqlite3.connect(
                f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA schema_version").fetchone()
            return conn, "immutable_snapshot", None
        except (OSError, sqlite3.Error) as immutable_error:
            return None, "unreadable", str(immutable_error or normal_error)


def _supplement_html_keywords_from_legacy_db(
    cards: List[Dict[str, Any]],
    legacy_db_path: Optional[Path],
    *,
    progress_logger: Any,
    progress_callback: Optional[LegacyProgressCallback],
) -> Dict[str, Any]:
    """Use v3.2 raw terms only as a fallback for HTML-confirmed papers.

    This deliberately does *not* import old normalized terms, aliases or
    derived counts. HTML is authoritative when it has a keyword section; the
    old cache only fills an absent section for the same reported paper.
    """
    html_terms_by_card = {
        id(card): _card_extracted_keywords(card) for card in cards
    }
    summary: Dict[str, Any] = {
        "state": "html_only",
        "html_papers": sum(bool(terms) for terms in html_terms_by_card.values()),
        "html_terms": sum(len(terms) for terms in html_terms_by_card.values()),
        "fallback_papers": 0,
        "fallback_terms": 0,
        "db_records_scanned": 0,
    }

    # A legacy cache can only complement an already scored report card. Cards
    # without a score retain their normal SQLite repair path instead of being
    # made to look scored merely because a keyword cache happened to exist.
    candidates = [
        card
        for card in cards
        if isinstance(card.get("score_payload"), dict)
        and card.get("score_payload")
        and not html_terms_by_card[id(card)]
    ]
    if not candidates:
        summary["state"] = "not_needed"
        progress_logger.info(
            "[LegacyKeywordFallback] HTML 已提供所有已分析论文的关键词；不读取旧 keywords.db"
        )
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            "HTML 已提供关键词，不读取旧关键词库",
            0,
            0,
        )
        return summary

    if legacy_db_path is None:
        summary["state"] = "not_configured"
        progress_logger.info(
            "[LegacyKeywordFallback] 有 %s 篇 HTML 卡片缺少关键词；未配置旧关键词库，保留为空",
            len(candidates),
        )
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            "未配置旧关键词库，仅保留 HTML 中已有关键词",
            0,
            len(candidates),
        )
        return summary

    conn, read_mode, error = _open_legacy_keywords_readonly(legacy_db_path)
    if conn is None:
        summary.update({"state": read_mode, "error": error})
        level = progress_logger.warning if read_mode == "unreadable" else progress_logger.info
        level(
            "[LegacyKeywordFallback] 旧 keywords.db 未用于补齐报告关键词：%s",
            error or read_mode,
        )
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            "旧关键词库不可用，仅保留 HTML 中已有关键词",
            0,
            len(candidates),
        )
        return summary

    exact_cards: Dict[Tuple[str, str], Dict[str, Any]] = {}
    canonical_cards: Dict[Tuple[str, str], Dict[str, Any]] = {}
    candidate_ids: Dict[str, set[str]] = {}
    for card in candidates:
        source = str(card.get("source") or "").strip().lower()
        paper_id = str(card.get("paper_id") or "").strip()
        canonical_id = str(card.get("canonical_id") or "").strip()
        if not source or not paper_id:
            continue
        exact_cards[(source, paper_id.casefold())] = card
        candidate_ids.setdefault(source, set()).add(paper_id.casefold())
        if canonical_id:
            # v3.2 stores canonical arXiv ids in some installations. When
            # several historical versions exist, the newest report artifact
            # is the only safe recipient for that versionless cache record.
            canonical_key = (source, canonical_id.casefold())
            previous = canonical_cards.get(canonical_key)
            if previous is None or card["report_at"] > previous["report_at"]:
                canonical_cards[canonical_key] = card
            candidate_ids[source].add(canonical_id.casefold())

    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(keywords)").fetchall()
        } if "keywords" in tables else set()
        required = {"keyword", "paper_id", "source"}
        if not required.issubset(columns):
            summary["state"] = "unsupported_schema"
            progress_logger.info(
                "[LegacyKeywordFallback] 旧 keywords.db 不含原始关键词表；不迁移别名或统计缓存"
            )
            _emit_progress(
                progress_callback,
                "legacy_keywords",
                "旧关键词库格式不支持，仅保留 HTML 中已有关键词",
                0,
                len(candidates),
            )
            return summary

        progress_logger.info(
            "[LegacyKeywordFallback] 仅查询 %s 篇缺关键词的 HTML 已分析论文；不迁移别名、规范词或统计缓存",
            len(candidates),
        )
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            f"为 {len(candidates)} 篇 HTML 已分析论文补查关键词",
            0,
            len(candidates),
        )
        assigned: Dict[int, List[str]] = {}
        for source, paper_ids in candidate_ids.items():
            ids = sorted(paper_ids)
            for offset in range(0, len(ids), 400):
                chunk = ids[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT source, paper_id, keyword FROM keywords "
                    "WHERE LOWER(source) = ? AND LOWER(paper_id) IN ("
                    + placeholders + ")",
                    [source, *chunk],
                )
                for row in rows:
                    summary["db_records_scanned"] += 1
                    row_source = str(row["source"] or "").strip().lower()
                    row_paper_id = str(row["paper_id"] or "").strip().casefold()
                    target = exact_cards.get((row_source, row_paper_id))
                    if target is None:
                        target = canonical_cards.get((row_source, row_paper_id))
                    if target is None:
                        continue
                    term = _dedupe_keyword_terms([row["keyword"]])
                    if term:
                        assigned.setdefault(id(target), []).extend(term)

        for card in candidates:
            terms = _dedupe_keyword_terms(assigned.get(id(card), []))
            if not terms:
                continue
            card["score_payload"]["extracted_keywords"] = terms
            summary["fallback_papers"] += 1
            summary["fallback_terms"] += len(terms)

        summary["state"] = "supplemented" if summary["fallback_terms"] else "no_matching_records"
        progress_logger.info(
            "[LegacyKeywordFallback] 完成：HTML 关键词 %s 篇/%s 个，旧库仅补齐 %s 篇/%s 个",
            summary["html_papers"],
            summary["html_terms"],
            summary["fallback_papers"],
            summary["fallback_terms"],
        )
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            "HTML 关键词导入完成；旧库仅补齐 "
            f"{summary['fallback_papers']} 篇/{summary['fallback_terms']} 个",
            len(candidates),
            len(candidates),
        )
        return summary
    except sqlite3.Error as exc:
        summary.update({"state": "unreadable", "error": str(exc)})
        progress_logger.warning("[LegacyKeywordFallback] 旧关键词库读取失败，继续导入 HTML：%s", exc)
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            "旧关键词库读取失败，仅保留 HTML 中已有关键词",
            0,
            len(candidates),
        )
        return summary
    finally:
        conn.close()


def import_legacy_history(
    store: Any,
    *,
    history_dir: Path,
    reports_html_dir: Path,
    delivery_run_id: str,
    legacy_keywords_db_path: Optional[Path] = None,
    progress_logger: Optional[Any] = None,
    progress_callback: Optional[LegacyProgressCallback] = None,
    full_repair: bool = False,
) -> Dict[str, Any]:
    """Write legacy HTML cards to SQLite and optionally queue full repair.

    The default path is deliberately lightweight: every v3.2 HTML card with
    a reliable arXiv or DOI identity is indexed and entered in its own
    source-level delivery ledger.  That prevents future scans of *any*
    enabled source from treating already reported work as new. Enabling
    ``full_repair`` additionally reads legacy JSON and uses old raw keywords
    only to fill an absent HTML keyword section for the same report card;
    later repair and omission tasks operate from SQLite, not by re-scanning
    HTML.
    """
    log = progress_logger or logger
    summary: Dict[str, Any] = {
        "finished_at": None,
        "full_repair_enabled": bool(full_repair),
        "history_files": {},
        "reports_scanned": 0,
        "cards_found": 0,
        "cards_selected": 0,
        "cards_without_identity": 0,
        "source_breakdown": {},
        "imported": 0,
        "skipped_existing_newer": 0,
        "skipped_v4_rows": 0,
        "delivered_ledger_rows": 0,
        "missing_cards": 0,
        "missing_tldr": 0,
        "missing_translation": 0,
        "missing_analysis": 0,
        "backlog_queued": 0,
        "report_keywords": {},
        "errors": [],
    }

    histories: Dict[str, Dict[Tuple[str, int], str]] = {}
    if full_repair:
        log.info(
            "[LegacyImport] 完整模式：读取 v3.2 JSON 与 HTML 报告；旧 keywords.db 仅按报告卡片补齐缺失关键词"
        )
        _emit_progress(progress_callback, "legacy_history", "开始读取旧 JSON 历史")
        histories = load_legacy_history_files(
            history_dir, progress_callback=progress_callback
        )
        summary["history_files"] = {
            source: len(entries) for source, entries in histories.items()
        }
    else:
        log.info("[LegacyImport] 轻量模式：登记 HTML 中已有的各来源论文")
        _emit_progress(
            progress_callback,
            "legacy_history",
            "轻量模式跳过旧 JSON 与旧关键词库，只读取各来源 HTML 报告",
            0,
            0,
        )

    cards = parse_legacy_report_cards(
        reports_html_dir, progress_callback=progress_callback
    )
    summary["cards_found"] = len(cards)
    selected_cards = [
        card
        for card in cards
        if card.get("canonical_id")
        and card.get("identity_kind") in {"arxiv", "doi"}
    ]
    summary["cards_without_identity"] = len(cards) - len(selected_cards)
    summary["cards_selected"] = len(selected_cards)
    summary["reports_scanned"] = len({card["report_path"] for card in selected_cards})
    source_breakdown: Dict[str, int] = {}
    for card in selected_cards:
        source = str(card.get("source") or "").strip().lower()
        if source:
            source_breakdown[source] = source_breakdown.get(source, 0) + 1
    summary["source_breakdown"] = dict(sorted(source_breakdown.items()))

    # 旧历史的交付时间优先于报告时间戳（更接近真实推送时刻）。每个来源
    # 独立保留其交付记录；跨来源归并由 SQLite 实体层按 DOI/arXiv 身份完成。
    histories_by_canonical: Dict[str, Dict[str, str]] = {}
    for history_source, entries in histories.items():
        by_canonical: Dict[str, str] = {}
        for (canonical, _version), delivered_at in entries.items():
            previous = by_canonical.get(canonical)
            if previous is None or delivered_at > previous:
                by_canonical[canonical] = delivered_at
        histories_by_canonical[history_source] = by_canonical

    card_index: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for card in selected_cards:
        if not card["canonical_id"]:
            continue
        key = (card["source"], card["canonical_id"], card["version"])
        existing = card_index.get(key)
        if existing is None or card["report_at"] > existing["report_at"]:
            card_index[key] = card

    # Only cardless JSON history becomes a supplemental-report backlog. HTML
    # cards always enter the delivery ledger, even when a field is missing:
    # repair updates the original report in place rather than re-delivering
    # the same paper in a new report.
    backlog_entries: List[Dict[str, Any]] = []

    def delivered_at_for(card: Dict[str, Any]) -> str:
        source_history = histories.get(card["source"], {})
        exact = source_history.get((card["canonical_id"], card["version"]))
        if exact:
            return exact
        fallback = histories_by_canonical.get(card["source"], {}).get(
            card["canonical_id"]
        )
        if fallback:
            return fallback
        return card["report_at"].isoformat()

    cards_to_write = sorted(
        card_index.items(), key=lambda item: item[1]["report_at"]
    )
    keyword_cards = [card for _, card in cards_to_write]
    if full_repair:
        keyword_db_path = (
            Path(legacy_keywords_db_path)
            if legacy_keywords_db_path is not None
            else Path(history_dir).parent / "keywords" / "keywords.db"
        )
        summary["report_keywords"] = _supplement_html_keywords_from_legacy_db(
            keyword_cards,
            keyword_db_path,
            progress_logger=log,
            progress_callback=progress_callback,
        )
    else:
        html_terms = [_card_extracted_keywords(card) for card in keyword_cards]
        summary["report_keywords"] = {
            "state": "html_only",
            "html_papers": sum(bool(terms) for terms in html_terms),
            "html_terms": sum(len(terms) for terms in html_terms),
            "fallback_papers": 0,
            "fallback_terms": 0,
            "db_records_scanned": 0,
        }
        _emit_progress(
            progress_callback,
            "legacy_keywords",
            "仅写入 HTML 卡片中的已分析论文关键词，不读取旧 keywords.db",
            len(keyword_cards),
            len(keyword_cards),
        )
    _emit_progress(
        progress_callback,
        "legacy_write",
        f"准备写入 {len(cards_to_write)} 张最新报告卡片",
        0,
        len(cards_to_write),
    )
    for index, (key, card) in enumerate(cards_to_write, start=1):
        _emit_progress(
            progress_callback,
            "legacy_write",
            f"写入 {card['source']}:{card['paper_id']}",
            index - 1,
            len(cards_to_write),
        )
        try:
            has_score = bool(card["score_payload"])
            has_tldr = bool(
                has_score
                and isinstance(card["score_payload"].get("tldr"), str)
                and card["score_payload"]["tldr"].strip()
            )
            needs_translation = bool(card["abstract"].strip())
            has_translation = bool(card["abstract_cn"].strip())
            # 历史报告只在可确定拥有 arXiv PDF 的情况下要求深度分析。
            # 这以论文能力而非来源名称判断，因此带 arXiv 身份的非 arXiv
            # 来源记录也会与主源使用相同的修复逻辑；纯 DOI 卡片不会被
            # 凭空判为缺分析。
            needs_analysis = (
                card["identity_kind"] == "arxiv"
                and card["score_payload"].get("is_qualified", False)
            )
            has_analysis = bool(card["analysis"])

            missing_fields: List[str] = []
            if not has_score:
                missing_fields.append("score")
            elif not has_tldr:
                missing_fields.append("tldr")
            if needs_translation and not has_translation:
                missing_fields.append("translation")
            if needs_analysis and not has_analysis:
                missing_fields.append("analysis")
            delivered_at = delivered_at_for(card)
            payload = {
                "source": card["source"],
                "paper_id": card["paper_id"],
                "canonical_id": card["canonical_id"],
                "version": card["version"],
                "paper_json": _paper_json_from_card(card),
                "score_json": json.dumps(card["score_payload"], ensure_ascii=False) if has_score else None,
                "abstract_cn": card["abstract_cn"] or None,
                "analysis_json": json.dumps(card["analysis"], ensure_ascii=False) if has_analysis else None,
                "score_status": "succeeded" if has_score else "pending",
                "tldr_status": "succeeded" if has_tldr else "pending",
                "translation_status": (
                    "succeeded" if has_translation else ("not_required" if not needs_translation else "pending")
                ),
                "analysis_status": (
                    "succeeded"
                    if has_analysis
                    else ("pending" if needs_analysis else "not_required")
                ),
                "completed_at": delivered_at,
                "report_path": card["report_path"],
                "report_at": card["report_at"].isoformat(),
                "delivered_at": delivered_at,
                "delivery_run_id": delivery_run_id,
            }
            outcome = store.import_legacy_paper(payload, delivered=True)
            summary[outcome if outcome in summary else "imported"] = (
                summary.get(outcome, 0) + 1
            )
            summary["delivered_ledger_rows"] += 1
            for missing in missing_fields:
                key_name = {
                    "tldr": "missing_tldr",
                    "translation": "missing_translation",
                    "analysis": "missing_analysis",
                }.get(missing)
                if key_name:
                    summary[key_name] += 1
            if index == len(cards_to_write) or index % 25 == 0:
                log.info(
                    "[LegacyImport] 卡片写入 %s/%s：已导入 %s，保留新数据 %s，补充积压候选 %s",
                    index,
                    len(cards_to_write),
                    summary["imported"],
                    summary["skipped_existing_newer"],
                    sum(
                        summary[key]
                        for key in ("missing_tldr", "missing_translation", "missing_analysis")
                    ),
                )
        except Exception as exc:  # 单卡片失败不终止整个导入
            log.warning("[LegacyImport] 导入卡片失败 %s: %s", key, exc)
            summary["errors"].append(f"{key}: {exc}")
        finally:
            _emit_progress(
                progress_callback,
                "legacy_write",
                f"已处理 {card['source']}:{card['paper_id']}",
                index,
                len(cards_to_write),
            )

    if full_repair:
        # JSON-only entries have no prior report to patch. Preserve them as
        # supplemental candidates so a full import can create a new report if
        # metadata becomes available, while lightweight import remains a
        # zero-LLM HTML ledger migration.
        arxiv_card_canonicals = {
            card["canonical_id"]
            for card in selected_cards
            if card["identity_kind"] == "arxiv"
        }
        doi_card_canonicals = {
            normalize_doi_key(card["paper_id"])
            for card in selected_cards
            if card["identity_kind"] == "doi"
        }
        card_canonicals_by_source: Dict[str, set[str]] = {}
        for card in selected_cards:
            card_canonicals_by_source.setdefault(card["source"], set()).add(
                card["canonical_id"]
            )

        for history_source, entries in histories.items():
            for (canonical, version), _delivered_at in entries.items():
                if history_source == "arxiv":
                    present = canonical in arxiv_card_canonicals
                    paper_id = f"{canonical}v{version}" if version else canonical
                else:
                    # DOI cards are often rendered under journal-specific
                    # sources even when the old JSON file was named
                    # ``openalex_history.json``. A DOI match in any report is
                    # therefore sufficient evidence that this work has a
                    # patchable card already.
                    is_doi = normalize_doi_key(canonical) == canonical and canonical.startswith("10.")
                    present = (
                        canonical in doi_card_canonicals
                        if is_doi
                        else canonical in card_canonicals_by_source.get(history_source, set())
                    )
                    paper_id = f"https://doi.org/{canonical}" if is_doi else canonical
                if present:
                    continue
                backlog_entries.append(
                    {
                        "source": history_source,
                        "canonical_id": canonical,
                        "version": version,
                        "paper_id": paper_id,
                        "reason": "missing_data",
                        "detail": "旧历史有交付记录但未找到报告卡片",
                    }
                )
                summary["missing_cards"] += 1

    _emit_progress(
        progress_callback,
        "legacy_backlog",
        (
            f"整理 {len(backlog_entries)} 条无报告的旧历史记录"
            if full_repair
            else "轻量导入不创建补充积压"
        ),
        0,
        len(backlog_entries),
    )
    if backlog_entries:
        try:
            summary["backlog_queued"] = store.record_supplement_backlog(backlog_entries)
            log.info(
                "[LegacyImport] 缺失/遗漏数据已写入补充积压：候选 %s 条，新增或更新 %s 条",
                len(backlog_entries),
                summary["backlog_queued"],
            )
        except Exception as exc:
            log.warning("[LegacyImport] 补充积压写入失败: %s", exc)
            summary["errors"].append(f"backlog: {exc}")
        finally:
            _emit_progress(
                progress_callback,
                "legacy_backlog",
                f"补充积压完成（新增或更新 {summary['backlog_queued']} 条）",
                len(backlog_entries),
                len(backlog_entries),
            )
    else:
        _emit_progress(progress_callback, "legacy_backlog", "没有无报告的旧历史记录", 0, 0)

    summary["finished_at"] = datetime.now().isoformat()
    log.info(
        "[LegacyImport] 导入完成（%s）: 报告 %s 份、卡片 %s/%s 张、账本 %s 条、积压 %s 条",
        "完整修复" if full_repair else "仅 HTML 入库",
        summary["reports_scanned"],
        summary["cards_selected"],
        summary["cards_found"],
        summary["delivered_ledger_rows"],
        summary["backlog_queued"],
    )
    return summary
