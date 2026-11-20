#!/usr/bin/env python3
"""Unit tests for url_pdf_fetcher.py using a local HTTP test server."""

from __future__ import annotations

import functools
import http.server
import io
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import url_pdf_fetcher as fetcher  # noqa: E402


def make_sample_pdf_bytes(page_count: int) -> bytes:
    """Create a valid blank PDF with the requested number of pages."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with suppressed stderr logging."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class UrlPdfFetcherTests(unittest.TestCase):
    """Test direct PDF download, paginated HTML PDF crawling, and error handling."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Create sample PDFs (2 pages, 3 pages, 1 page)
        (self.root / "doc1.pdf").write_bytes(make_sample_pdf_bytes(2))
        (self.root / "doc2.pdf").write_bytes(make_sample_pdf_bytes(3))
        (self.root / "doc3.pdf").write_bytes(make_sample_pdf_bytes(1))

        # Create paginated HTML pages
        (self.root / "page1.html").write_text(
            """
            <html><body>
              <a href="doc1.pdf">Doc 1</a>
              <a href="/page2.html">次へ</a>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.root / "page2.html").write_text(
            """
            <html><body>
              <a href="doc1.pdf">Doc 1 Duplicate</a>
              <a href="doc2.pdf">Doc 2</a>
              <a rel="next" href="page3.html">Next</a>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.root / "page3.html").write_text(
            """
            <html><body>
              <iframe src="doc3.pdf"></iframe>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.root / "viewer.html").write_text(
            """
            <html><body>
              <div id="spinner">Hang tight. This'll take just a sec.</div>
              <h1 id="title" style="display:none">Slide 1</h1>
              <script>
                let page = 1;
                function showPage(n) {
                  document.getElementById('spinner').style.display = 'block';
                  document.getElementById('title').style.display = 'none';
                  setTimeout(() => {
                    document.getElementById('spinner').style.display = 'none';
                    document.getElementById('title').textContent = 'Slide ' + n;
                    document.getElementById('title').style.display = 'block';
                  }, 700);
                }
                showPage(1);
                document.addEventListener('keydown', (e) => {
                  if (e.key === 'ArrowRight' && page < 3) {
                    page += 1;
                    showPage(page);
                  }
                });
              </script>
            </body></html>
            """,
            encoding="utf-8",
        )

        (self.root / "viewer_left.html").write_text(
            """
            <html><body>
              <h1 id="title">RTL Page 1</h1>
              <script>
                let page = 1;
                document.addEventListener('keydown', (e) => {
                  if (e.key === 'ArrowLeft' && page < 2) {
                    page += 1;
                    document.getElementById('title').textContent = 'RTL Page ' + page;
                  }
                });
              </script>
            </body></html>
            """,
            encoding="utf-8",
        )
        paragraphs = "\n".join(
            f"<p>LineMarker_{i:03d}: Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt.</p>"
            for i in range(1, 65)
        )
        (self.root / "long_chapter.html").write_text(
            f"""
            <html><head><style>
              @page {{ margin: 0.05in; }}
              header {{ position: sticky; top: 0; height: 60px; background: #eee; }}
            </style></head><body>
              <header>Sticky Navbar</header>
              <main>{paragraphs}</main>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.root / "fixed_layout.html").write_text(
            """
            <html><head><style>
              .pf { width: 420px; height: 595px; position: relative; }
              #pdf-main { transform: scale(1.5); transform-origin: 0 0; }
            </style></head><body>
              <div id="sbo-rt-content">
                <div id="pdf-main">
                  <div class="pdf-page"><div class="pf">FixedPage_001</div></div>
                  <div class="pdf-page"><div class="pf">FixedPage_002</div></div>
                  <div class="pdf-page"><div class="pf">FixedPage_003</div></div>
                </div>
              </div>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.root / "sidebar_layout.html").write_text(
            """
            <html><body>
              <header>TOP_BANNER_SHOULD_HIDE</header>
              <div style="display:flex;">
                <main id="sbo-rt-content" style="width:300px;">
                  <h1>Main Chapter Title</h1>
                  <p>READER_BODY_CONTENT_LINE</p>
                </main>
                <aside class="sidebar">SIDEBAR_EXPLANATION_SHOULD_HIDE</aside>
              </div>
            </body></html>
            """,
            encoding="utf-8",
        )
        handler = functools.partial(QuietHandler, directory=str(self.root))
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_direct_pdf_download(self) -> None:
        out_pdf = self.root / "out_direct.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/doc2.pdf",
            output_path=out_pdf,
            allow_browser=False,
        )
        self.assertEqual(pages, 3)
        self.assertTrue(out_pdf.exists())
        self.assertEqual(len(PdfReader(str(out_pdf)).pages), 3)

    def test_paginated_html_crawl_and_merge(self) -> None:
        out_pdf = self.root / "out_merged.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/page1.html",
            output_path=out_pdf,
            allow_browser=False,
        )
        # doc1 (2) + doc2 (3) + doc3 (1) = 6 pages (doc1 duplicate skipped)
        self.assertEqual(pages, 6)
        self.assertTrue(out_pdf.exists())
        self.assertEqual(len(PdfReader(str(out_pdf)).pages), 6)

    def test_invalid_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            fetcher.fetch_and_merge(
                url="ftp://example.com/file.pdf",
                output_path=self.root / "bad.pdf",
                allow_browser=False,
            )

    def test_browser_arrow_right_and_pdf_command(self) -> None:
        out_pdf = self.root / "out_viewer.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/viewer.html",
            output_path=out_pdf,
            max_pages=10,
            allow_browser=True,
        )
        self.assertEqual(pages, 3)
        self.assertTrue(out_pdf.exists())
        reader = PdfReader(str(out_pdf))
        self.assertEqual(len(reader.pages), 3)
        joined_text = "\n".join(p.extract_text() or "" for p in reader.pages).replace("\x01", " ")
        self.assertIn("Slide 1", joined_text)
        self.assertIn("Slide 3", joined_text)
        self.assertNotIn("Hang tight", joined_text)

    def test_browser_arrow_left_direction(self) -> None:
        out_pdf = self.root / "out_viewer_left.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/viewer_left.html",
            output_path=out_pdf,
            max_pages=10,
            allow_browser=True,
            direction="left",
        )
        self.assertEqual(pages, 2)
        reader = PdfReader(str(out_pdf))
        joined_text = "\n".join(p.extract_text() or "" for p in reader.pages).replace("\x01", " ")
        self.assertIn("RTL Page 1", joined_text)
        self.assertIn("RTL Page 2", joined_text)

    def test_long_chapter_no_clipped_lines_across_page_breaks(self) -> None:
        out_pdf = self.root / "out_long_chapter.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/long_chapter.html",
            output_path=out_pdf,
            max_pages=5,
            allow_browser=True,
        )
        self.assertGreaterEqual(pages, 2)
        reader = PdfReader(str(out_pdf))
        joined_text = "\n".join(p.extract_text() or "" for p in reader.pages)
        compact_text = "".join(joined_text.split())
        for i in range(1, 65):
            self.assertIn(f"LineMarker_{i:03d}", compact_text)

    def test_fixed_layout_pf_pages_one_to_one(self) -> None:
        out_pdf = self.root / "out_fixed.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/fixed_layout.html",
            output_path=out_pdf,
            max_pages=5,
            allow_browser=True,
        )
        self.assertEqual(pages, 3)
        reader = PdfReader(str(out_pdf))
        self.assertEqual(len(reader.pages), 3)

    def test_pdf_content_signature_distinguishes_image_pdfs(self) -> None:
        from PIL import Image

        def make_image_pdf(color: tuple[int, int, int]) -> bytes:
            buf = io.BytesIO()
            img = Image.new("RGB", (200, 300), color=color)
            img.save(buf, "PDF")
            return buf.getvalue()

        sig_red = fetcher.pdf_content_signature(make_image_pdf((255, 0, 0)))
        sig_blue = fetcher.pdf_content_signature(make_image_pdf((0, 0, 255)))
        self.assertNotEqual(sig_red, sig_blue)

    def test_sidebar_and_header_hidden_in_reader_pdf(self) -> None:
        out_pdf = self.root / "out_sidebar.pdf"
        pages = fetcher.fetch_and_merge(
            url=f"http://127.0.0.1:{self.port}/sidebar_layout.html",
            output_path=out_pdf,
            max_pages=2,
            allow_browser=True,
        )
        self.assertEqual(pages, 1)
        reader = PdfReader(str(out_pdf))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        self.assertIn("READER_BODY_CONTENT_LINE", text)
        self.assertNotIn("SIDEBAR_EXPLANATION_SHOULD_HIDE", text)
        self.assertNotIn("TOP_BANNER_SHOULD_HIDE", text)


if __name__ == "__main__":
    unittest.main()
