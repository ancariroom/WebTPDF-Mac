#!/usr/bin/env python3
"""Unit tests for url_pdf_fetcher.py using a local HTTP test server."""

from __future__ import annotations

import functools
import http.server
import io
import json
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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
                  <section class="_contentContainer_test _sidebarIsOpen_test">
                    <div data-testid="alternativeFormatCallout">Other formats available Audiobook</div>
                    <h1>Main Chapter Title</h1>
                    <p>READER_BODY_CONTENT_LINE</p>
                  </section>
                </main>
                <aside class="sidebar">SIDEBAR_EXPLANATION_SHOULD_HIDE</aside>
              </div>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.root / "blank_page.html").write_text(
            """
            <html><body><header></header><main id="sbo-rt-content"></main></body></html>
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

    def test_kindle_redownload_merges_saved_pages_without_gaps(self) -> None:
        import fitz

        reader = self.root / "kindle.html"
        html = """
            <img id="page" width="200" height="260"><p id="position"></p>
            <button id="back" hidden>Back to Library</button>
            <button data-asin="TEST" hidden>Open book</button>
            <button aria-label="Table of Contents" id="toc-toggle">☰</button>
            <section hidden id="toc">
              <h2>Table of Contents</h2>
              <button data-page="23" aria-current="location">Chapter A</button>
              <button data-page="27">Chapter B</button>
            </section>
            <script>
              let current = 1;
              let objectURL;
              let usedToc = false;
              const image = document.querySelector('#page');
              const position = document.querySelector('#position');
              const back = document.querySelector('#back');
              const book = document.querySelector('[data-asin]');
              function render() {
                const canvas = document.createElement('canvas');
                canvas.width = 200;
                canvas.height = 260;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = 'rgb(' + current * 8 + ',60,120)';
                ctx.fillRect(0, 0, 200, 260);
                canvas.toBlob((blob) => {
                  if (objectURL) URL.revokeObjectURL(objectURL);
                  objectURL = URL.createObjectURL(blob);
                  image.src = objectURL;
                  image.hidden = false;
                  position.textContent = 'Page ' + current + ' of 28';
                });
              }
              back.onclick = () => {
                back.hidden = true;
                position.textContent = '';
                book.hidden = false;
              };
              book.onclick = () => {
                book.hidden = true;
                current = 23;
                render();
              };
              const toc = document.querySelector('#toc');
              document.querySelector('#toc-toggle').onclick = () => {
                toc.hidden = !toc.hidden;
              };
              for (const chapter of toc.querySelectorAll('[data-page]')) {
                chapter.onclick = () => {
                  usedToc = true;
                  current = Number(chapter.dataset.page);
                  toc.hidden = true;
                  render();
                };
              }
              document.addEventListener('keydown', (event) => {
                if (!back.hidden || !book.hidden) return;
                const next = current + (event.key === 'ArrowRight' ? 1 : -1);
                if (next < 1 || next > 28) return;
                if (FAIL_CONDITION) {
                  image.hidden = false; // The old page is still behind the dialog.
                  position.textContent = 'Oops... Something Went Wrong';
                  back.hidden = false;
                  return;
                }
                current = next;
                render();
              });
              render();
            </script>
        """
        reader.write_text(html.replace("FAIL_CONDITION", "next === 25"), encoding="utf-8")
        url = f"http://127.0.0.1:{self.port}/kindle.html?asin=TEST"
        output = self.root / "resumed.pdf"
        checkpoint = fetcher.KindleCheckpoint(self.root / "checkpoint.sqlite3")
        with patch.object(fetcher, "resolve_cdp_url", return_value=None), patch.object(
            fetcher, "is_kindle_cloud_reader", return_value=True
        ):
            with self.assertRaisesRegex(RuntimeError, "取得済み: 24"):
                fetcher.fetch_via_browser(url, output, 28, 10, kindle_checkpoint=checkpoint)
            self.assertEqual(len(checkpoint.load()), 24)
            self.assertFalse(output.exists())

            report = self.root / "result.json"
            args = [
                "url_pdf_fetcher", "--url", url, "--output", str(output),
                "--max-pages", "24", "--kindle-checkpoint", str(checkpoint.path),
                "--result-json", str(report),
            ]
            with patch.object(sys, "argv", args):
                self.assertEqual(fetcher.main(), 1)
            status = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual((status["pageCount"], status["expectedPages"]), (24, 28))
            self.assertIn("上限", status["message"])
            self.assertFalse(output.exists())
            self.assertEqual(len(checkpoint.load()), 24)

            # The next chapter opens at 27 but page turns into 26 only work
            # after visiting it. Recovery must go back to the verified anchor.
            reader.write_text(
                html.replace("FAIL_CONDITION", "next === 26 && !usedToc"), encoding="utf-8"
            )
            args[args.index("--max-pages") + 1] = "28"
            with patch.object(sys, "argv", args), patch.object(
                fetcher, "jump_to_kindle_chapter", wraps=fetcher.jump_to_kindle_chapter
            ) as jump:
                self.assertEqual(fetcher.main(), 0)
                self.assertEqual(jump.call_args.args[1], 1)
            status = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual((status["pageCount"], status["expectedPages"]), (28, 28))
        self.assertEqual(Path(status["outputPath"]), output.resolve())
        self.assertFalse(checkpoint.path.exists())
        with fitz.open(output) as pdf:
            for number, page in enumerate(pdf, 1):
                pixels = page.get_pixmap()
                self.assertAlmostEqual(pixels.pixel(pixels.width // 2, pixels.height // 2)[0], number * 8, delta=3)

    def test_canceled_kindle_capture_reports_saved_progress(self) -> None:
        output = self.root / "canceled.pdf"
        checkpoint = fetcher.KindleCheckpoint(self.root / "checkpoint.sqlite3")
        checkpoint.append(1, fetcher.KindleCapture(make_sample_pdf_bytes(1), 1, None, 4, None, None))
        report = self.root / "result.json"
        args = [
            "url_pdf_fetcher", "--url", "https://read.amazon.co.jp/?asin=TEST",
            "--output", str(output), "--kindle-checkpoint", str(checkpoint.path),
            "--result-json", str(report),
        ]
        with patch.object(sys, "argv", args), patch.object(
            fetcher, "fetch_via_browser", side_effect=fetcher.CaptureCanceled
        ):
            self.assertEqual(fetcher.main(), 130)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((result["pageCount"], result["expectedPages"]), (1, 4))
        self.assertEqual(checkpoint.progress(), (1, 4))
        self.assertFalse(output.exists())

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
        self.assertNotIn("Other formats available", text)

    def test_blank_page_raises_error_instead_of_saving_white_pdf(self) -> None:
        out_pdf = self.root / "out_blank.pdf"
        with self.assertRaises(RuntimeError):
            fetcher.fetch_and_merge(
                url=f"http://127.0.0.1:{self.port}/blank_page.html",
                output_path=out_pdf,
                max_pages=2,
                allow_browser=True,
            )
        self.assertFalse(out_pdf.exists())


if __name__ == "__main__":
    unittest.main()
