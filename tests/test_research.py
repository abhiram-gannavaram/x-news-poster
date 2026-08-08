from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agents.research import fetch_article_text


class _RedirectHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self) -> None:
        type(self).requests.append(self.path)
        if self.path == "/article":
            self.send_response(302)
            self.send_header("Location", "/canonical")
            self.end_headers()
            return
        if self.path == "/unsafe":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/canonical":
            body = b"<html><body>Redirected article body</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class FetchArticleTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self) -> None:
        _RedirectHandler.requests.clear()

    def test_follows_a_validated_redirect_manually(self) -> None:
        with patch("agents.research.is_safe_public_url", return_value=True):
            text = fetch_article_text(f"{self.base_url}/article")

        self.assertEqual(text, "Redirected article body")
        self.assertEqual(_RedirectHandler.requests, ["/article", "/canonical"])

    def test_blocks_an_unsafe_redirect_target(self) -> None:
        def is_safe(url: str) -> bool:
            return url.startswith(self.base_url)

        with patch("agents.research.is_safe_public_url", side_effect=is_safe):
            text = fetch_article_text(f"{self.base_url}/unsafe")

        self.assertEqual(text, "")
        self.assertEqual(_RedirectHandler.requests, ["/unsafe"])


if __name__ == "__main__":
    unittest.main()
