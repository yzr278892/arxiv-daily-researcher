"""
OpenAlex 期刊数据源

通过 OpenAlex API 获取学术期刊的最新论文元数据。
相比 Crossref，OpenAlex 提供更完整的摘要和元数据。
"""

import logging
import re
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List, Dict, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from .base_source import BasePaperSource, PaperMetadata
from utils.source_registry import OPENALEX_JOURNAL_CATALOG

logger = logging.getLogger(__name__)


_OPENALEX_WORK_ID_RE = re.compile(
    r"^(?:https?://openalex\.org/)?(?P<work_id>W[1-9]\d*)$", re.IGNORECASE
)

# An OpenAlex location pointing at arXiv carries the identifier in its landing
# page URL rather than in a structured field.
_ARXIV_LOCATION_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<canonical>\d{4}\.\d{4,5})(?P<version>v\d+)?"
)


class OpenAlexFetchError(RuntimeError):
    """Raised when any configured journal cannot be fetched completely."""


# Backward-compatible export for integrations importing the old constant.
JOURNAL_ISSN_MAP = OPENALEX_JOURNAL_CATALOG


class OpenAlexSource(BasePaperSource):
    """
    OpenAlex 期刊数据源。

    特点：
    - 支持多种学术期刊（PRL、PRA、Nature 等）
    - 通过 OpenAlex API 获取元数据
    - 提供倒排索引格式的摘要（自动重建为文本）
    - 不支持 PDF 下载，仅进行评分分析
    """

    API_BASE_URL = "https://api.openalex.org"
    # An OpenAlex abstract is a compact inverted index.  A value above this
    # bound is neither useful to the daily scorer nor safe to allocate from
    # untrusted upstream JSON.  Unlike the old code, it is not truncated: a
    # truncated abstract can silently change relevance scoring.
    MAX_ABSTRACT_POSITION = 50_000

    def __init__(
        self,
        history_dir: Path,
        journals: List[str] = None,
        max_results: int = 100,
        api_key: str = None,
        journal_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
        load_legacy_history: bool = True,
    ):
        """
        初始化 OpenAlex 数据源。

        参数:
            history_dir: 历史记录存储目录
            journals: 要抓取的期刊代码列表，如 ["prl", "pra"]
            max_results: 兼容旧配置的参数。日报抓取不按数量截断，始终扫描时间窗口内的全部结果。
            api_key: OpenAlex API Key（可选；免费 Key 可提高每日额度）
        """
        super().__init__(
            "openalex", history_dir, load_legacy_history=load_legacy_history
        )
        self.journals = journals or []
        self.max_results = max_results
        self.api_key = api_key
        self.journal_catalog = journal_catalog or OPENALEX_JOURNAL_CATALOG

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ArxivDailyResearcher/2.0 (https://github.com/yzr278892/arxiv-daily-researcher; yzr278892@gmail.com)"
            }
        )
        if api_key:
            # The official API supports a bearer token as an alternative to
            # ``api_key`` in the query string.  Keep a secret out of request
            # URLs, which can otherwise be retained by proxy/debug logs.
            self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def __enter__(self):
        """支持上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时关闭Session"""
        self.close()

    def close(self):
        """关闭网络连接"""
        if self.session:
            self.session.close()
            logger.debug("OpenAlex Session已关闭")

    def _api_request(self, url: str, params: dict) -> dict:
        """发送 OpenAlex API 请求，带自动重试。"""
        from config import settings as _settings

        @retry(
            stop=stop_after_attempt(_settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=_settings.RETRY_MIN_WAIT, max=_settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_request():
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        return _do_request()

    @property
    def display_name(self) -> str:
        return "OpenAlex"

    def can_download_pdf(self) -> bool:
        return False  # OpenAlex 只提供元数据

    def get_journal_info(self, journal_code: str) -> Optional[Dict]:
        """获取期刊信息"""
        return self.journal_catalog.get(journal_code.lower())

    def fetch_papers(self, days: int, journals: List[str] = None, **kwargs) -> List[PaperMetadata]:
        """
        从 OpenAlex 抓取指定期刊最近 N 天的论文。

        参数:
            days: 搜索最近 N 天的论文
            journals: 期刊代码列表，如 ["prl", "pra"]

        返回:
            List[PaperMetadata]: 论文元数据列表
        """
        if journals is not None:
            self.journals = journals

        if not self.journals:
            logger.warning("[OpenAlex] 未指定期刊，跳过抓取")
            return []

        # Keep the source safe when it is used directly, rather than only via
        # SearchAgent (which performs the same configuration validation).  In
        # particular, mixed-case journal codes must not be silently skipped or
        # fetched twice.
        normalized_journals = []
        seen_journals = set()
        for configured_code in self.journals:
            if not isinstance(configured_code, str) or not configured_code.strip():
                raise OpenAlexFetchError(
                    f"OpenAlex 期刊代码必须是非空字符串: {configured_code!r}"
                )
            journal_code = configured_code.strip().lower()
            if journal_code not in self.journal_catalog:
                raise OpenAlexFetchError(f"OpenAlex 未知期刊代码: {configured_code}")
            if journal_code not in seen_journals:
                normalized_journals.append(journal_code)
                seen_journals.add(journal_code)
        self.journals = normalized_journals

        all_papers = []
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        logger.info(f"[OpenAlex] 开始抓取期刊论文")
        logger.info(f"  目标期刊: {self.journals}")
        logger.info(f"  时间范围: 最近 {days} 天（从 {from_date}）")

        for journal_code in self.journals:
            journal_info = self.get_journal_info(journal_code)
            # The journal list above is validated, but retain this guard for
            # future changes to the journal map.
            if not journal_info:  # pragma: no cover - defensive invariant
                raise OpenAlexFetchError(f"OpenAlex 未知期刊代码: {journal_code}")

            issn_list = journal_info["issn"]
            journal_name = journal_info["full_name"]
            display_name = journal_info["display_name"]

            logger.info(f"  正在抓取 {journal_name}...")

            try:
                papers = self._fetch_journal_papers(
                    issn_list=issn_list,
                    journal_code=journal_code,
                    journal_name=journal_name,
                    from_date=from_date,
                )
                all_papers.extend(papers)
                logger.info(f"    {display_name}: 发现 {len(papers)} 篇新论文")

            except Exception as exc:
                # A partial journal list is worse than a failed daily run: it
                # looks complete, then the missed items can age out of the
                # time window and never be reported.  Propagate the failure so
                # the pipeline retries the entire configured set next run.
                raise OpenAlexFetchError(
                    f"OpenAlex 期刊 {journal_code} ({display_name}) 抓取未完成: {exc}"
                ) from exc

        logger.info(f"[OpenAlex] 总计发现 {len(all_papers)} 篇新论文")
        return all_papers

    def fetch_papers_between(
        self,
        date_from: date,
        date_to: date,
        journals: Optional[List[str]] = None,
    ) -> List[PaperMetadata]:
        """Fetch configured journal works in an inclusive publication range.

        This path is used by past daily reports and omission scans. It bypasses
        legacy JSON history because SQLite performs exact source/version
        de-duplication after the complete range has been fetched.
        """
        if not isinstance(date_from, date) or not isinstance(date_to, date):
            raise ValueError("OpenAlex 历史范围必须是 date")
        if date_from > date_to:
            raise ValueError("OpenAlex 历史范围起始日期不能晚于结束日期")

        selected = list(journals) if journals is not None else list(self.journals)
        if not selected:
            return []
        normalized: List[str] = []
        for configured_code in selected:
            if not isinstance(configured_code, str) or not configured_code.strip():
                raise OpenAlexFetchError(
                    f"OpenAlex 期刊代码必须是非空字符串: {configured_code!r}"
                )
            code = configured_code.strip().lower()
            if code not in self.journal_catalog:
                raise OpenAlexFetchError(f"OpenAlex 未知期刊代码: {configured_code}")
            if code not in normalized:
                normalized.append(code)

        logger.info(
            "[OpenAlex] 历史期刊扫描: %s 至 %s（期刊: %s）",
            date_from,
            date_to,
            normalized,
        )
        all_papers: List[PaperMetadata] = []
        for journal_code in normalized:
            journal_info = self.get_journal_info(journal_code)
            try:
                papers = self._fetch_journal_papers(
                    issn_list=journal_info["issn"],
                    journal_code=journal_code,
                    journal_name=journal_info["full_name"],
                    from_date=date_from.isoformat(),
                    to_date=date_to.isoformat(),
                    filter_processed=False,
                )
            except Exception as exc:
                raise OpenAlexFetchError(
                    f"OpenAlex 期刊 {journal_code} ({journal_info['display_name']}) "
                    f"历史抓取未完成: {exc}"
                ) from exc
            all_papers.extend(papers)
        return all_papers

    def set_arxiv_lookup_source(self, source) -> None:
        """Reuse a caller-owned arXiv source for journal enrichment.

        ``SearchAgent`` already owns an arXiv source (client, proxy and rate
        budget) when arXiv is enabled; journal enrichment should spend that
        same budget instead of building a second client.
        """
        self._arxiv_lookup = source

    def _arxiv_lookup_source(self):
        """Return the shared arXiv source used to resolve journal records.

        Constructing a client per paper also meant one client per paper; a
        single, history-free source now serves the whole journal scan.
        """
        source = getattr(self, "_arxiv_lookup", None)
        if source is None:
            from .arxiv_source import ArxivSource

            source = ArxivSource(history_dir=self.history_dir, load_legacy_history=False)
            self._arxiv_lookup = source
        return source

    def _fetch_arxiv_records(self, arxiv_ids: List[str]) -> Dict[str, PaperMetadata]:
        """Fetch arXiv metadata for many ids with one batched API lookup."""

        wanted = [str(value).strip() for value in arxiv_ids if str(value).strip()]
        if not wanted:
            return {}
        return self._arxiv_lookup_source().fetch_papers_by_ids(wanted)

    @staticmethod
    def _journal_metadata_from_arxiv(
        record: PaperMetadata,
        *,
        journal_code: str,
        journal_name: str,
        doi: str,
        arxiv_id: str,
    ) -> PaperMetadata:
        """Rebuild one arXiv record under its journal identity.

        Keep the OpenAlex/DOI identity even when the metadata is enriched from
        arXiv.  ``_fetch_journal_papers`` checks ``is_processed`` with this DOI
        on the next scan; using the arXiv short id here used to write a
        different history key and made the same journal article appear new
        every day.  The arXiv identifier is still retained separately for
        metadata and PDF access.
        """
        return PaperMetadata(
            paper_id=doi,
            title=record.title,
            authors=list(record.authors or []),
            abstract=record.abstract,  # arXiv 提供完整摘要
            published_date=record.published_date,
            url=record.url,
            source=journal_code,  # 保留期刊代码
            pdf_url=record.pdf_url,
            doi=doi,  # 使用期刊的 DOI
            journal=journal_name,  # 标注期刊名称
            arxiv_id=arxiv_id,
            arxiv_url=record.url,
            categories=list(record.categories or []),
        )

    def _fetch_from_arxiv(
        self, arxiv_id: str, journal_code: str, journal_name: str, doi: str
    ) -> Optional[PaperMetadata]:
        """
        通过 arXiv ID 从 ArXiv 获取论文元数据。

        保留单篇兼容路径：批量预取未命中的论文仍按需单独获取。

        参数:
            arxiv_id: arXiv ID
            journal_code: 期刊代码
            journal_name: 期刊全名
            doi: DOI

        返回:
            Optional[PaperMetadata]: 论文元数据，失败时返回 None
        """
        try:
            record = self._fetch_arxiv_records([arxiv_id]).get(arxiv_id)
            if record is None:
                logger.warning(f"    ⚠️  arXiv API 未找到论文: {arxiv_id}")
                return None

            metadata = self._journal_metadata_from_arxiv(
                record,
                journal_code=journal_code,
                journal_name=journal_name,
                doi=doi,
                arxiv_id=arxiv_id,
            )

            logger.info(
                f"    ✅ [{metadata.title[:30]}...] 使用 arXiv 源获取完整元数据 (arXiv:{arxiv_id})"
            )
            return metadata

        except Exception as e:
            logger.warning(f"    ⚠️  从 arXiv 获取论文失败 ({arxiv_id}): {e}")
            return None

    def _has_legacy_arxiv_history(self, arxiv_id: str) -> bool:
        """Recognize pre-v3.3 journal history written with an arXiv ID.

        Before stable journal identities were fixed, an OpenAlex work with an
        arXiv location was marked as processed under ``<arxiv-id>vN`` even
        though future OpenAlex scans check its DOI.  Retaining this narrow
        compatibility lookup avoids a one-time duplicate after upgrading.
        New records are always stored under their DOI/OpenAlex ID instead.
        """
        canonical = str(arxiv_id or "").strip()
        if not canonical:
            return False
        with self._history_lock:
            if not self.history_filtering_enabled:
                return False
            if self._history_load_error:
                from .base_source import HistoryLoadError

                raise HistoryLoadError(
                    "[openalex] 兼容历史不可用，拒绝以空历史继续去重: "
                    f"{self._history_load_error}"
                )
            return canonical in self.history

    @staticmethod
    def _work_identity(item: Dict[str, Any]) -> str:
        """Return a stable DOI/OpenAlex fallback identity or reject the work.

        A work without a usable identity cannot be compared with delivery
        history.  Silently skipping it would let a completed scan watermark
        hide that paper forever, so it is a source-level integrity failure.
        """
        raw_doi = item.get("doi")
        if raw_doi is not None:
            if not isinstance(raw_doi, str) or not raw_doi.strip():
                raise OpenAlexFetchError("DOI 不是非空字符串")
            return raw_doi.strip()

        raw_openalex_id = item.get("id")
        if not isinstance(raw_openalex_id, str):
            raise OpenAlexFetchError("缺少 DOI 和有效 OpenAlex work ID")
        match = _OPENALEX_WORK_ID_RE.fullmatch(raw_openalex_id.strip())
        if match is None:
            raise OpenAlexFetchError("缺少 DOI 且 OpenAlex work ID 无效")
        # Preserve the stable ID in a source-distinct namespace.  The API
        # normally sends the URL form, but accepting its documented bare form
        # makes the parser deterministic for exported/replayed responses too.
        return f"openalex:{match.group('work_id').upper()}"

    @staticmethod
    def _clean_title(value: Any) -> str:
        """Normalize a required work title without inventing a placeholder."""
        if not isinstance(value, str):
            raise OpenAlexFetchError("标题不是字符串")
        title = re.sub(r"<[^>]+>", "", value)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            raise OpenAlexFetchError("缺少标题")
        return title

    @staticmethod
    def _authors_from_authorships(value: Any) -> List[str]:
        """Validate the container while allowing a legitimately empty author list."""
        if not isinstance(value, list):
            raise OpenAlexFetchError("authorships 不是列表")
        authors = []
        for authorship in value[:20]:  # 最多20个作者
            if not isinstance(authorship, dict):
                raise OpenAlexFetchError("authorships 包含非对象条目")
            author = authorship.get("author")
            if author is None:
                continue
            if not isinstance(author, dict):
                raise OpenAlexFetchError("authorships.author 不是对象")
            display_name = author.get("display_name")
            if display_name is None:
                continue
            if not isinstance(display_name, str):
                raise OpenAlexFetchError("作者名称不是字符串")
            name = display_name.strip()
            if name:
                authors.append(name)
        return authors

    @staticmethod
    def _entry_error(journal_code: str, page: int, item_index: int, exc: BaseException):
        """Attach enough non-secret context to a malformed upstream entry."""
        return OpenAlexFetchError(
            f"OpenAlex {journal_code} 第 {page} 页条目 {item_index} 元数据无效: {exc}"
        )

    def _arxiv_identity(
        self,
        item: Dict[str, Any],
        journal_code: str,
        page: int,
        item_index: int,
        *,
        strict: bool,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return ``(arxiv_id, arxiv_history_id, arxiv_url)`` for one work.

        ``strict`` keeps the scan's validation error for a malformed
        ``locations`` structure.  The prefetch pass is lenient: a record that
        the processed-history filter skips must not fail the scan on metadata
        the scan never reads.
        """
        arxiv_id: Optional[str] = None
        arxiv_history_id: Optional[str] = None
        arxiv_url: Optional[str] = None

        def _reject(message: str):
            if strict:
                raise self._entry_error(
                    journal_code, page, item_index, OpenAlexFetchError(message)
                )
            return None, None, None

        locations = item.get("locations", [])
        if not isinstance(locations, list):
            return _reject("locations 不是列表")
        for loc in locations:
            if not isinstance(loc, dict):
                return _reject("locations 包含非对象条目")
            source_info = loc.get("source", {})
            if source_info is not None and not isinstance(source_info, dict):
                return _reject("locations.source 不是对象")
            if not source_info:
                continue
            source_name = source_info.get("display_name", "")
            if not isinstance(source_name, str):
                return _reject("locations.source.display_name 不是字符串")
            # 检查是否是 arXiv 来源
            if "arxiv" not in source_name.lower():
                continue
            loc_url = loc.get("landing_page_url", "")
            if loc_url is not None and not isinstance(loc_url, str):
                return _reject("locations.landing_page_url 不是字符串")
            if not (loc_url and "arxiv.org" in loc_url):
                continue
            arxiv_url = loc_url
            # 使用正则表达式提取 arXiv ID，更健壮
            try:
                match = _ARXIV_LOCATION_RE.search(loc_url)
                if match:
                    arxiv_id = match.group("canonical")
                    arxiv_history_id = f"{arxiv_id}{match.group('version') or ''}"
            except Exception as exc:
                logger.debug(f"arXiv ID提取失败: {exc}")
            break
        return arxiv_id, arxiv_history_id, arxiv_url

    def _prefetch_page_arxiv(
        self,
        results: List[Any],
        journal_code: str,
        journal_name: str,
        page: int,
        *,
        filter_processed: bool,
    ) -> Dict[str, PaperMetadata]:
        """Resolve the arXiv versions of one OpenAlex page with one request.

        Journal pages routinely contain many records that also exist on arXiv.
        Resolving each of them through its own client and request dominated a
        journal scan, so the page is collected first and resolved with a
        batched ``id_list`` lookup.  Only records the scan will keep are
        requested; a record the API cannot resolve still falls back to the
        per-paper path at the point of use.
        """
        candidates: Dict[str, str] = {}
        for item_index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            try:
                doi = self._work_identity(item)
                if filter_processed and self.is_processed(doi):
                    continue
                arxiv_id, arxiv_history_id, _url = self._arxiv_identity(
                    item, journal_code, page, item_index, strict=False
                )
                if not arxiv_id:
                    continue
                if filter_processed and self._has_legacy_arxiv_history(
                    arxiv_history_id or arxiv_id
                ):
                    continue
            except Exception:
                continue
            candidates.setdefault(arxiv_id, doi)
        if not candidates:
            return {}
        try:
            records = self._fetch_arxiv_records(list(candidates))
        except Exception as exc:
            logger.warning(f"    ⚠️  arXiv 批量补全失败，回退为按需获取: {exc}")
            return {}
        prefetched: Dict[str, PaperMetadata] = {}
        for arxiv_id, doi in candidates.items():
            record = records.get(arxiv_id)
            if record is None:
                continue
            prefetched[arxiv_id] = self._journal_metadata_from_arxiv(
                record,
                journal_code=journal_code,
                journal_name=journal_name,
                doi=doi,
                arxiv_id=arxiv_id,
            )
        if prefetched:
            logger.info("    ⚡ 已批量获取 %d 篇论文的 arXiv 元数据", len(prefetched))
        return prefetched

    def _fetch_journal_papers(
        self,
        issn_list: List[str],
        journal_code: str,
        journal_name: str,
        from_date: str,
        to_date: Optional[str] = None,
        filter_processed: bool = True,
    ) -> List[PaperMetadata]:
        """
        抓取单个期刊的论文。

        参数:
            issn_list: 期刊 ISSN 列表
            journal_code: 期刊代码（用于 source 字段）
            journal_name: 期刊全名
            from_date: 起始日期 (YYYY-MM-DD)

        返回:
            List[PaperMetadata]: 论文列表
        """
        papers = []

        # 构建 ISSN 过滤器（支持多个ISSN）
        issn_filter = "|".join(issn_list)

        url = f"{self.API_BASE_URL}/works"

        base_params = {}

        # Daily fetches must exhaust the date window.  ``max_results`` remains
        # an API-compatible constructor field for older configurations, but it
        # is not a result budget: a quiet day can have hundreds of papers and
        # truncating at a user-facing display limit silently loses them.
        #
        # OpenAlex supports offset paging only up to a finite depth.  Cursor
        # paging is therefore required here: a busy journal or a wider retry
        # window must not silently stop at that API limit.
        page = 0
        per_page = 100  # OpenAlex documented per-page maximum
        api_total = 0
        cursor = "*"
        seen_cursors = set()

        while True:
            if cursor in seen_cursors:
                raise OpenAlexFetchError("OpenAlex 分页 cursor 重复，无法确认抓取完整性")
            seen_cursors.add(cursor)
            page += 1
            date_filter = f"from_publication_date:{from_date}"
            if to_date:
                date_filter += f",to_publication_date:{to_date}"
            params = {
                "filter": f"primary_location.source.issn:{issn_filter},{date_filter}",
                "per-page": per_page,
                "cursor": cursor,
                "sort": "publication_date:desc",
                "select": "id,doi,title,authorships,abstract_inverted_index,publication_date,primary_location,open_access,locations,best_oa_location,ids",
            }
            params.update(base_params)

            logger.debug(f"  正在获取第 {page} 页...")
            data = self._api_request(url, params)
            if not isinstance(data, dict):
                raise OpenAlexFetchError("OpenAlex API 响应不是 JSON 对象")

            results = data.get("results", [])
            if not isinstance(results, list):
                raise OpenAlexFetchError("OpenAlex API 响应 results 字段不是列表")
            if not results:
                logger.debug(f"  第 {page} 页无更多结果，停止分页")
                break
            api_total += len(results)

            # Resolve this page's arXiv versions in one batched lookup before
            # the per-record loop needs them; the loop still validates each
            # record and falls back to a single fetch on a miss.
            prefetched_arxiv = self._prefetch_page_arxiv(
                results,
                journal_code,
                journal_name,
                page,
                filter_processed=filter_processed,
            )

            for item_index, item in enumerate(results, start=1):
                try:
                    if not isinstance(item, dict):
                        raise OpenAlexFetchError("条目不是 JSON 对象")

                    doi = self._work_identity(item)
                    title = self._clean_title(item.get("title"))
                    published_date = self._parse_date(item.get("publication_date"))
                    authors = self._authors_from_authorships(item.get("authorships"))

                    # Missing/empty abstracts are a normal consequence of
                    # publisher licensing.  A present but malformed index is
                    # different: treating it as no abstract changes scoring
                    # while making an incomplete scan look successful.
                    inverted_index = item.get("abstract_inverted_index")
                    if inverted_index is None or inverted_index == {}:
                        abstract = ""
                        logger.warning(
                            "    ⚠️  [%s...] OpenAlex 未提供摘要数据 (可能因期刊版权限制)",
                            title[:30],
                        )
                    else:
                        abstract = self._rebuild_abstract(inverted_index)
                        logger.debug("    ✅ [%s...] 成功获取摘要", title[:30])
                except OpenAlexFetchError as exc:
                    raise self._entry_error(journal_code, page, item_index, exc) from exc

                # 去重检查必须在身份字段验证之后进行。无身份条目既无法
                # 去重，也不能安全地被当作“已处理”。
                if filter_processed and self.is_processed(doi):
                    continue

                # 提取 URL
                if doi.startswith("http"):
                    landing_page_url = doi
                elif doi.startswith("openalex:"):
                    landing_page_url = f"https://openalex.org/{doi.replace('openalex:', '')}"
                else:
                    landing_page_url = f"https://doi.org/{doi}"
                primary_location = item.get("primary_location", {})
                if primary_location is not None and not isinstance(primary_location, dict):
                    raise self._entry_error(
                        journal_code,
                        page,
                        item_index,
                        OpenAlexFetchError("primary_location 不是对象"),
                    )
                if primary_location and primary_location.get("landing_page_url"):
                    candidate_url = primary_location["landing_page_url"]
                    if not isinstance(candidate_url, str) or not candidate_url.strip():
                        raise self._entry_error(
                            journal_code,
                            page,
                            item_index,
                            OpenAlexFetchError("primary_location.landing_page_url 不是非空字符串"),
                        )
                    landing_page_url = candidate_url.strip()

                # 提取 PDF URL（如果开放获取）
                pdf_url = None
                open_access = item.get("open_access", {})
                if open_access is not None and not isinstance(open_access, dict):
                    raise self._entry_error(
                        journal_code,
                        page,
                        item_index,
                        OpenAlexFetchError("open_access 不是对象"),
                    )
                open_access = open_access or {}
                if open_access.get("is_oa") and open_access.get("oa_url"):
                    candidate_pdf_url = open_access["oa_url"]
                    if not isinstance(candidate_pdf_url, str) or not candidate_pdf_url.strip():
                        raise self._entry_error(
                            journal_code,
                            page,
                            item_index,
                            OpenAlexFetchError("open_access.oa_url 不是非空字符串"),
                        )
                    pdf_url = candidate_pdf_url.strip()
                    logger.debug(f"    ✅ [{title[:30]}...] 找到开放获取 PDF")

                # 从 locations 提取 arXiv 信息（使用正则表达式提高健壮性）
                arxiv_id, arxiv_history_id, arxiv_url = self._arxiv_identity(
                    item, journal_code, page, item_index, strict=True
                )

                # 🎯 优先策略：如果找到 arXiv 版本，使用 ArXiv 源获取完整元数据
                if arxiv_id:
                    if filter_processed and self._has_legacy_arxiv_history(
                        arxiv_history_id or arxiv_id
                    ):
                        logger.info(
                            "    ↪ [%s...] 已由旧版历史记录处理，跳过重复期刊论文",
                            title[:30],
                        )
                        continue
                    logger.info(
                        f"    🔄 [{title[:30]}...] 检测到 arXiv 版本: {arxiv_id}，转而使用 ArXiv 源获取完整元数据"
                    )
                    arxiv_metadata = prefetched_arxiv.get(arxiv_id)
                    if arxiv_metadata is None:
                        arxiv_metadata = self._fetch_from_arxiv(
                            arxiv_id, journal_code, journal_name, doi
                        )
                    if arxiv_metadata:
                        arxiv_metadata.source_date = published_date.date()
                        papers.append(arxiv_metadata)
                        continue  # 跳过 OpenAlex 的元数据提取，直接处理下一篇论文
                    logger.warning(f"    ⚠️  从 ArXiv 获取失败，回退到 OpenAlex 元数据")
                else:
                    logger.debug(
                        f"    ℹ️  [{title[:30]}...] 未找到 arXiv 版本，使用 OpenAlex 元数据"
                    )

                # 构建论文元数据
                metadata = PaperMetadata(
                    paper_id=doi,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    published_date=published_date,
                    url=landing_page_url,
                    source=journal_code,  # 使用期刊代码作为 source
                    pdf_url=pdf_url,
                    doi=doi if not doi.startswith("openalex:") else None,
                    journal=journal_name,
                    arxiv_id=arxiv_id,
                    arxiv_url=arxiv_url,
                )
                papers.append(metadata)

            # A short page is the normal terminal signal.  A full cursor page
            # must contain a usable continuation token.  Treating a missing
            # token as an ordinary end would turn an API/proxy truncation into
            # a plausible-looking, but incomplete, daily report.
            if len(results) < per_page:
                break

            meta = data.get("meta")
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                raise OpenAlexFetchError(
                    f"OpenAlex 第 {page} 页已满但未返回有效 next_cursor"
                )
            cursor = next_cursor.strip()

        logger.info(f"  共获取 {len(papers)} 篇论文（API 返回 {api_total} 条，分 {page} 页）")
        return papers

    def _rebuild_abstract(self, inverted_index: Dict[str, List[int]]) -> str:
        """
        将倒排索引格式的摘要重建为普通文本。

        OpenAlex 使用倒排索引存储摘要以规避版权问题。
        格式: {"word": [position1, position2, ...], ...}

        参数:
            inverted_index: 倒排索引字典

        返回:
            str: 重建的摘要文本
        """
        if not isinstance(inverted_index, dict):
            raise OpenAlexFetchError("摘要倒排索引不是对象")
        if not inverted_index:
            return ""

        words_by_position: Dict[int, str] = {}
        for word, positions in inverted_index.items():
            if not isinstance(word, str) or not word.strip():
                raise OpenAlexFetchError("摘要倒排索引包含无效词元")
            if not isinstance(positions, list) or not positions:
                raise OpenAlexFetchError("摘要倒排索引词元位置不是非空列表")
            for position in positions:
                if (
                    isinstance(position, bool)
                    or not isinstance(position, int)
                    or position < 0
                    or position > self.MAX_ABSTRACT_POSITION
                ):
                    raise OpenAlexFetchError("摘要倒排索引包含超范围或非整数位置")
                if position in words_by_position:
                    raise OpenAlexFetchError("摘要倒排索引包含重复位置")
                words_by_position[position] = word

        abstract = " ".join(words_by_position[position] for position in sorted(words_by_position))
        if not abstract.strip():  # Defensive: non-empty indices must reconstruct text.
            raise OpenAlexFetchError("摘要倒排索引未能重建有效摘要")
        return abstract.strip()

    def _parse_date(self, date_str: Any) -> datetime:
        """
        解析 OpenAlex 返回的日期。

        OpenAlex 日期格式: "YYYY-MM-DD"

        参数:
            date_str: 日期字符串

        返回:
            datetime: 解析后的日期对象
        """
        if not isinstance(date_str, str) or not date_str.strip():
            raise OpenAlexFetchError("缺少 publication_date")
        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError as exc:
            raise OpenAlexFetchError(f"publication_date 格式无效: {date_str!r}") from exc
