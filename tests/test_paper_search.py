"""论文检索：元数据匹配、过滤条件与分页。"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _seed_paper(
    store: DailyResearchStore,
    *,
    paper_id: str,
    title: str,
    authors: list[str],
    completed_at: str,
    total_score: float | None = None,
    qualified: bool | None = None,
    tldr: str = "",
    extracted: list[str] | None = None,
) -> None:
    paper_json = json.dumps(
        {
            "paper_id": paper_id,
            "source": "arxiv",
            "title": title,
            "authors": authors,
            "url": f"https://example.org/{paper_id}",
            "categories": ["cs.LG"],
        },
        ensure_ascii=False,
    )
    score_json = None
    if total_score is not None:
        score_json = json.dumps(
            {
                "total_score": total_score,
                "is_qualified": qualified,
                "strategy_id": "legacy_weighted_keyword_v1",
                "tldr": tldr,
                "extracted_keywords": extracted or [],
            },
            ensure_ascii=False,
        )
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_papers(
                source, paper_id, canonical_id, first_seen_at, last_seen_at,
                paper_json, score_json, completed_at
            ) VALUES (?, ?, '', ?, ?, ?, ?, ?)
            """,
            ("arxiv", paper_id, completed_at, completed_at, paper_json, score_json, completed_at),
        )


class SearchPapersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = DailyResearchStore(
            Path(self._tmp.name) / "daily_research.db"
        )
        _seed_paper(
            self.store,
            paper_id="a1",
            title="Diffusion Models for Speech Synthesis",
            authors=["Alice Smith", "Bob Jones"],
            completed_at="2026-08-01T08:00:00",
            total_score=12.5,
            qualified=True,
            tldr="本文提出一种语音扩散模型。",
            extracted=["diffusion", "speech synthesis"],
        )
        _seed_paper(
            self.store,
            paper_id="a2",
            title="Transformer Quantization Survey",
            authors=["Carol White"],
            completed_at="2026-08-10T08:00:00",
            total_score=3.0,
            qualified=False,
            tldr="量化方法综述。",
            extracted=["quantization", "transformer"],
        )
        _seed_paper(
            self.store,
            paper_id="a3",
            title="Unscored Draft Paper",
            authors=["Dan Brown"],
            completed_at="2026-08-15T08:00:00",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_query_lists_all_completed(self):
        result = self.store.search_papers()
        self.assertEqual(result["total"], 3)
        # newest first
        self.assertEqual(result["items"][0]["paper_id"], "a3")

    def test_title_query_matches_case_insensitively(self):
        result = self.store.search_papers(query="diffusion")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a1")

    def test_query_matches_tldr_and_extracted_keywords(self):
        by_tldr = self.store.search_papers(query="量化")
        self.assertEqual(by_tldr["total"], 1)
        self.assertEqual(by_tldr["items"][0]["paper_id"], "a2")

        by_keyword = self.store.search_papers(query="speech synthesis")
        self.assertEqual(by_keyword["total"], 1)
        self.assertEqual(by_keyword["items"][0]["paper_id"], "a1")

    def test_query_matches_authors(self):
        result = self.store.search_papers(query="Alice Smith")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a1")

    def test_like_wildcards_match_literally(self):
        result = self.store.search_papers(query="100%")
        self.assertEqual(result["total"], 0)

    def test_min_score_filter_excludes_unscored(self):
        result = self.store.search_papers(min_score=5.0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a1")

    def test_date_range_filter(self):
        result = self.store.search_papers(
            completed_from="2026-08-05", completed_to="2026-08-12"
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a2")

    def test_liked_only_filter(self):
        self.store.set_paper_preference(
            "arxiv",
            "a2",
            preference="like",
            title="Transformer Quantization Survey",
        )
        result = self.store.search_papers(liked_only=True)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["paper_id"], "a2")
        self.assertEqual(result["items"][0]["preference"], "like")

    def test_pagination_limit_and_offset(self):
        page1 = self.store.search_papers(limit=2)
        self.assertEqual(page1["total"], 3)
        self.assertEqual([i["paper_id"] for i in page1["items"]], ["a3", "a2"])

        page2 = self.store.search_papers(limit=2, offset=2)
        self.assertEqual([i["paper_id"] for i in page2["items"]], ["a1"])

    def test_item_metadata_hydration(self):
        result = self.store.search_papers(query="Diffusion")
        item = result["items"][0]
        self.assertEqual(item["source"], "arxiv")
        self.assertEqual(item["title"], "Diffusion Models for Speech Synthesis")
        self.assertEqual(item["authors"], ["Alice Smith", "Bob Jones"])
        self.assertEqual(item["url"], "https://example.org/a1")
        self.assertEqual(item["total_score"], 12.5)
        self.assertTrue(item["is_qualified"])
        self.assertEqual(item["extracted_keywords"], ["diffusion", "speech synthesis"])

    def test_cross_source_records_merge_one_entity_and_keep_variants(self):
        run_id = self.store.start_run(2, run_kind="legacy_import")
        records = [
            {
                "source": "arxiv",
                "paper_id": "2601.12345v1",
                "canonical_id": "2601.12345",
                "version": 1,
                "paper_json": {
                    "paper_id": "2601.12345v1",
                    "source": "arxiv",
                    "title": "Shared Quantum Result",
                    "authors": ["Alice"],
                    "abstract": "arXiv abstract",
                    "url": "https://arxiv.org/abs/2601.12345v1",
                    "doi": "10.1000/shared-result",
                    "arxiv_id": "2601.12345v1",
                    "categories": ["quant-ph"],
                },
                "score": {
                    "total_score": 11.0,
                    "is_qualified": True,
                    "tldr": "arXiv TLDR",
                    "extracted_keywords": ["Quantum", "Optics"],
                },
                "abstract_cn": "arXiv 翻译",
                "analysis": {"summary": "arXiv analysis"},
            },
            {
                "source": "prl",
                "paper_id": "https://doi.org/10.1000/shared-result",
                "canonical_id": "10.1000/shared-result",
                "version": 0,
                "paper_json": {
                    "paper_id": "https://doi.org/10.1000/shared-result",
                    "source": "prl",
                    "title": "Shared quantum result",
                    "authors": ["Alice", "Bob"],
                    "abstract": "Journal abstract",
                    "url": "https://doi.org/10.1000/shared-result",
                    "doi": "10.1000/shared-result",
                    "arxiv_id": "2601.12345v1",
                    "arxiv_url": "https://arxiv.org/abs/2601.12345v1",
                    "categories": ["journal"],
                },
                "score": {
                    "total_score": 12.0,
                    "is_qualified": True,
                    "tldr": "Journal TLDR",
                    "extracted_keywords": ["optics", "Journal"],
                },
                "abstract_cn": "期刊翻译",
                "analysis": {"summary": "Journal analysis"},
            },
        ]
        for record in records:
            payload = {
                "source": record["source"],
                "paper_id": record["paper_id"],
                "canonical_id": record["canonical_id"],
                "version": record["version"],
                "paper_json": record["paper_json"],
                "score_json": json.dumps(record["score"], ensure_ascii=False),
                "abstract_cn": record["abstract_cn"],
                "analysis_json": json.dumps(record["analysis"], ensure_ascii=False),
                "score_status": "succeeded",
                "tldr_status": "succeeded",
                "translation_status": "succeeded",
                "analysis_status": "succeeded",
                "completed_at": "2026-08-20T08:00:00",
                "report_path": f"/reports/{record['source']}.html",
                "report_at": "2026-08-20T08:00:00",
                "delivered_at": "2026-08-20T08:00:00",
                "delivery_run_id": run_id,
            }
            self.store.import_legacy_paper(payload, delivered=True)

        result = self.store.search_papers(query="shared")
        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertEqual(item["sources"], ["arxiv", "prl"])
        self.assertEqual(item["merged_keywords"], ["Quantum", "Optics", "Journal"])
        self.assertEqual(len(item["variants"]), 2)
        self.assertEqual(
            {variant["tldr"] for variant in item["variants"]},
            {"arXiv TLDR", "Journal TLDR"},
        )
        self.assertEqual(self.store.search_papers(source="prl")["total"], 1)
        prl_item = self.store.search_papers(source="prl")["items"][0]
        self.assertEqual(prl_item["source"], "prl")
        self.assertEqual(prl_item["total_score"], 12.0)
        high_score_item = self.store.search_papers(min_score=11.5)["items"][0]
        self.assertEqual(high_score_item["source"], "prl")
        self.assertEqual(high_score_item["total_score"], 12.0)
        entity = self.store.get_paper_entity("prl", records[1]["paper_id"])
        self.assertIsNotNone(entity)
        self.assertEqual(entity["entity_id"], item["entity_id"])

    def test_versionless_mirror_merges_when_arxiv_version_arrives_later(self):
        run_id = self.store.start_run(2, run_kind="legacy_import")
        base = {
            "score_json": json.dumps(
                {
                    "total_score": 8.0,
                    "is_qualified": True,
                    "tldr": "summary",
                    "extracted_keywords": ["mirror"],
                }
            ),
            "score_status": "succeeded",
            "tldr_status": "succeeded",
            "translation_status": "not_required",
            "analysis_status": "not_required",
            "completed_at": "2026-08-21T08:00:00",
            "report_at": "2026-08-21T08:00:00",
            "delivered_at": "2026-08-21T08:00:00",
            "delivery_run_id": run_id,
        }
        mirror_id = "hf:2601.54321"
        self.store.import_legacy_paper(
            {
                **base,
                "source": "huggingface_papers",
                "paper_id": mirror_id,
                "canonical_id": mirror_id,
                "version": 0,
                "paper_json": {
                    "paper_id": mirror_id,
                    "source": "huggingface_papers",
                    "title": "Mirror first",
                    "authors": [],
                    "abstract": "",
                    "url": "https://arxiv.org/abs/2601.54321",
                    "arxiv_id": "2601.54321",
                },
            },
            delivered=True,
        )
        self.store.import_legacy_paper(
            {
                **base,
                "source": "arxiv",
                "paper_id": "2601.54321v1",
                "canonical_id": "2601.54321",
                "version": 1,
                "paper_json": {
                    "paper_id": "2601.54321v1",
                    "source": "arxiv",
                    "title": "Canonical later",
                    "authors": [],
                    "abstract": "",
                    "url": "https://arxiv.org/abs/2601.54321v1",
                    "arxiv_id": "2601.54321v1",
                },
            },
            delivered=True,
        )

        result = self.store.search_papers(query="Canonical later")
        self.assertEqual(result["total"], 1)
        self.assertEqual(
            result["items"][0]["sources"], ["arxiv", "huggingface_papers"]
        )


if __name__ == "__main__":
    unittest.main()
