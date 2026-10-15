import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.safe_url import safe_configured_http_url, safe_http_url  # noqa: E402


class SafeUrlTests(unittest.TestCase):
    def test_only_absolute_http_urls_are_renderable_links(self):
        self.assertEqual(
            safe_http_url(" https://arxiv.org/abs/2501.12345v1 "),
            "https://arxiv.org/abs/2501.12345v1",
        )
        self.assertEqual(
            safe_http_url("http://arxiv.org/abs/2501.12345v1"),
            "https://arxiv.org/abs/2501.12345v1",
        )
        self.assertEqual(
            safe_http_url("http://EXPORT.ARXIV.ORG/pdf/2501.12345v1.pdf"),
            "https://EXPORT.ARXIV.ORG/pdf/2501.12345v1.pdf",
        )
        self.assertEqual(safe_http_url("HTTP://example.test/paper"), "HTTP://example.test/paper")
        self.assertEqual(
            safe_http_url("http://notarxiv.org/abs/2501.12345v1"),
            "http://notarxiv.org/abs/2501.12345v1",
        )
        for unsafe in (
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "/relative/path",
            "https:\\not-a-host",
            "https://example.test/\nsecond-line",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertEqual(safe_http_url(unsafe), "")

    def test_configured_service_urls_allow_local_hosts_but_not_embedded_secrets(self):
        self.assertEqual(
            safe_configured_http_url("http://127.0.0.1:8080/webhook?token=kept"),
            "http://127.0.0.1:8080/webhook?token=kept",
        )
        self.assertEqual(
            safe_configured_http_url("https://dav.example.test/root/", allow_query=False),
            "https://dav.example.test/root/",
        )
        for unsafe in (
            "https://user:password@example.test/hook",
            "https://example.test:bad-port/hook",
            "https://example.test/hook#fragment",
            "https://example.test/hook?token=1",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertEqual(
                    safe_configured_http_url(unsafe, allow_query=False), ""
                )


if __name__ == "__main__":
    unittest.main()
