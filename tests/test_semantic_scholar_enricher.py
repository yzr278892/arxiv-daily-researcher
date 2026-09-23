import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.base_source import PaperMetadata, normalize_arxiv_identifier  # noqa: E402
from sources.search_agent import SearchAgent  # noqa: E402
from sources.semantic_scholar_enricher import SemanticScholarEnricher  # noqa: E402


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _journal_paper() -> PaperMetadata:
    return PaperMetadata(
        paper_id="10.1000/example",
        title="Example",
        authors=["Author"],
        abstract="Abstract",
        published_date=datetime.now(timezone.utc),
        url="https://doi.org/10.1000/example",
        source="prl",
        doi="10.1000/example",
    )


class SemanticScholarBoundaryTests(unittest.TestCase):
    def test_request_pacing_honors_key_policy_and_serializes_slots(self):
        authenticated = SemanticScholarEnricher(api_key="test-key")
        anonymous = SemanticScholarEnricher()
        self.addCleanup(authenticated.close)
        self.addCleanup(anonymous.close)

        self.assertEqual(authenticated.request_interval_seconds, 1.0)
        self.assertEqual(anonymous.request_interval_seconds, 0.1)

        # First request reserves [100, 101); a second request at 100.2 must
        # wait until the reserved slot.  ``monotonic`` is deliberately mocked
        # so this test never actually sleeps.
        with (
            patch(
                "sources.semantic_scholar_enricher.time.monotonic",
                side_effect=[100.0, 100.2, 101.0],
            ),
            patch("sources.semantic_scholar_enricher.time.sleep") as sleep,
        ):
            authenticated._wait_for_request_slot()
            authenticated._wait_for_request_slot()

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.8)

    def test_arxiv_identifier_validation_accepts_real_ids_only(self):
        self.assertEqual(normalize_arxiv_identifier(" 2501.12345v2 "), "2501.12345v2")
        self.assertEqual(normalize_arxiv_identifier("hep-th/9901001v3"), "hep-th/9901001v3")
        for invalid in (
            None,
            123,
            "arXiv:2501.12345",
            "2501.12345/../../internal",
            "2501.12345v0",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(normalize_arxiv_identifier(invalid))

    def test_invalid_external_id_keeps_valid_tldr_but_never_makes_a_url(self):
        enricher = SemanticScholarEnricher()
        self.addCleanup(enricher.close)
        enricher._api_get = lambda *_args, **_kwargs: _Response(
            {
                "tldr": {"text": "  A valid summary.  "},
                "externalIds": {"ArXiv": "2501.12345/../../internal"},
            }
        )

        info = enricher.get_paper_info("10.1000/example")

        self.assertEqual(info, {"tldr": "A valid summary."})
        self.assertIsNone(enricher.get_arxiv_id("\ninvalid"))

    def test_paper_metadata_only_constructs_https_pdf_for_valid_arxiv_id(self):
        paper = _journal_paper()
        paper.arxiv_id = "2501.12345v2"
        self.assertEqual(
            paper.get_arxiv_pdf_url(), "https://arxiv.org/pdf/2501.12345v2.pdf"
        )
        paper.arxiv_id = "2501.12345/../../internal"
        self.assertFalse(paper.has_pdf_access())
        self.assertIsNone(paper.get_arxiv_pdf_url())

    def test_search_agent_defensively_rejects_invalid_custom_enrichment(self):
        paper = _journal_paper()
        agent = SearchAgent.__new__(SearchAgent)
        agent.semantic_scholar_enricher = SimpleNamespace(
            enrich_many=lambda dois: {
                doi: {
                    "tldr": "Provider TLDR",
                    "arxiv_id": "2501.12345/../../internal",
                }
                for doi in dois
            }
        )

        enriched = agent._enrich_with_semantic_scholar([paper])

        self.assertEqual(enriched[0].semantic_scholar_tldr, "Provider TLDR")
        self.assertIsNone(enriched[0].arxiv_id)
        self.assertIsNone(enriched[0].pdf_url)



class BatchEnrichmentTests(unittest.TestCase):
    """一次批量请求解析整页期刊论文，而不是每篇各占一个配额。"""

    def test_batch_lookup_resolves_every_doi_with_one_request(self):
        enricher = SemanticScholarEnricher()
        calls = []

        def fake_post(url, params, json_body, timeout=10):
            calls.append((url, params, json_body))
            return _Response(
                [{"tldr": {"text": f"tldr-{index}"}} for index in range(len(json_body["ids"]))]
            )

        with patch.object(enricher, "_api_post", side_effect=fake_post):
            result = enricher.enrich_many(["10.1/a", "10.1/b", "10.1/c"])

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("/paper/batch"))
        self.assertEqual(
            calls[0][2]["ids"], ["DOI:10.1/a", "DOI:10.1/b", "DOI:10.1/c"]
        )
        self.assertEqual(result["10.1/b"]["tldr"], "tldr-1")

    def test_large_batches_are_split_into_provider_sized_chunks(self):
        enricher = SemanticScholarEnricher()
        chunk_sizes = []

        def fake_post(url, params, json_body, timeout=10):
            chunk_sizes.append(len(json_body["ids"]))
            return _Response([None] * len(json_body["ids"]))

        dois = [f"10.1/{index}" for index in range(1200)]
        with patch.object(enricher, "_api_post", side_effect=fake_post):
            result = enricher.enrich_many(dois)

        self.assertEqual(chunk_sizes, [500, 500, 200])
        self.assertEqual(len(result), 1200)
        self.assertTrue(all(value is None for value in result.values()))

    def test_a_failed_chunk_does_not_discard_the_other_chunks(self):
        enricher = SemanticScholarEnricher()
        attempts = []

        def fake_post(url, params, json_body, timeout=10):
            attempts.append(len(json_body["ids"]))
            if len(attempts) == 1:
                raise RuntimeError("provider unavailable")
            return _Response([{"tldr": {"text": "second"}} for _ in json_body["ids"]])

        dois = [f"10.1/{index}" for index in range(600)]
        with patch.object(enricher, "_api_post", side_effect=fake_post):
            result = enricher.enrich_many(dois)

        self.assertEqual(attempts, [500, 100])
        self.assertIsNone(result["10.1/0"])
        self.assertEqual(result["10.1/599"]["tldr"], "second")

if __name__ == "__main__":
    unittest.main()
