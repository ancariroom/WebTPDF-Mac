#!/usr/bin/env python3
"""Fetch PDFs from a URL (including paginated HTML listings and browser viewers) and merge into one PDF."""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfReader, PdfWriter

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

NEXT_TEXT_PATTERNS = (
    re.compile(r"^\s*(次へ|次のページ|次ページ|次の頁|next|next\s*page)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(>|›|»|→|▶)\s*$"),
    re.compile(r"(次へ|次のページ|next\s*page)", re.IGNORECASE),
)


def log(message: str) -> None:
    """Print a flushed progress message to stdout."""
    print(message, flush=True)


def is_http_url(url: str) -> bool:
    """Return True if the URL uses http or https."""
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_pdf_bytes(data: bytes) -> bool:
    """Return True if raw bytes begin with a PDF header."""
    return data.lstrip()[:5] == b"%PDF-"


def looks_like_pdf_url(url: str) -> bool:
    """Heuristic check for URLs that point to a PDF resource."""
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if path_lower.endswith(".pdf"):
        return True
    return ".pdf" in parsed.query.lower()


def create_session() -> requests.Session:
    """Create a configured requests session."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def download_if_pdf(session: requests.Session, url: str, timeout: int) -> bytes | None:
    """Download a URL and return bytes if the response is a valid PDF."""
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        log(f"[WARN] Failed to fetch {url}: {exc}")
        return None

    content_type = response.headers.get("Content-Type", "").lower()
    data = response.content
    if "application/pdf" in content_type or is_pdf_bytes(data):
        return data
    return None


def extract_pdf_links_from_html(html: str | bytes, base_url: str) -> list[str]:
    """Extract candidate PDF URLs from HTML elements in document order."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(raw_url: str | None) -> None:
        if not raw_url:
            return
        absolute = urljoin(base_url, raw_url.strip())
        if not is_http_url(absolute):
            return
        if absolute not in seen and looks_like_pdf_url(absolute):
            seen.add(absolute)
            candidates.append(absolute)

    for tag in soup.find_all("a", href=True):
        add_candidate(tag.get("href"))
    for tag in soup.find_all("iframe", src=True):
        add_candidate(tag.get("src"))
    for tag in soup.find_all("embed", src=True):
        add_candidate(tag.get("src"))
    for tag in soup.find_all("object", data=True):
        add_candidate(tag.get("data"))

    return candidates


def find_next_page_url(html: str | bytes, current_url: str) -> str | None:
    """Find the next pagination URL on the same host, if present."""
    soup = BeautifulSoup(html, "html.parser")
    current_host = urlparse(current_url).netloc

    def normalize_same_host(raw_href: str | None) -> str | None:
        if not raw_href:
            return None
        candidate = urljoin(current_url, raw_href.strip())
        parsed = urlparse(candidate)
        if not is_http_url(candidate) or parsed.netloc != current_host:
            return None
        if candidate == current_url:
            return None
        return candidate

    # 1. Explicit rel="next"
    for tag in soup.find_all(["a", "link"], rel=True, href=True):
        rels = [r.lower() for r in (tag.get("rel") or [])]
        if "next" in rels:
            if resolved := normalize_same_host(tag.get("href")):
                return resolved

    # 2. Anchor text / aria-label / title matching next-page patterns
    anchors = soup.find_all("a", href=True)
    for pattern in NEXT_TEXT_PATTERNS:
        for tag in anchors:
            label = " ".join(
                filter(
                    None,
                    [
                        tag.get_text(" ", strip=True),
                        tag.get("aria-label"),
                        tag.get("title"),
                    ],
                )
            )
            if label and pattern.search(label):
                if resolved := normalize_same_host(tag.get("href")):
                    return resolved

    # 3. Numbered pagination inside a pagination container
    for container in soup.select("nav, .pagination, .pager, [class*='pagin'], [class*='pager']"):
        current_el = container.select_one("[aria-current='page'], .active, .current, strong, span.current")
        if not current_el:
            continue
        current_text = current_el.get_text(strip=True)
        if not current_text.isdigit():
            continue
        next_num = str(int(current_text) + 1)
        for tag in container.find_all("a", href=True):
            if tag.get_text(strip=True) == next_num:
                if resolved := normalize_same_host(tag.get("href")):
                    return resolved

    return None


def merge_pdf_bytes_list(pdf_blobs: Iterable[bytes], output_path: Path) -> int:
    """Merge a sequence of PDF byte blobs into a single PDF file and return total page count."""
    writer = PdfWriter()
    total_pages = 0

    for idx, blob in enumerate(pdf_blobs, start=1):
        try:
            reader = PdfReader(io.BytesIO(blob))
        except Exception as exc:
            log(f"[WARN] Skipping unreadable PDF #{idx}: {exc}")
            continue

        for page in reader.pages:
            writer.add_page(page)
            total_pages += 1

    if total_pages == 0:
        raise RuntimeError("No valid PDF pages could be merged.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        writer.write(handle)

    return total_pages


def merge_images_to_pdf(image_paths: list[Path], output_path: Path) -> int:
    """Merge PNG screenshots into a single PDF file and return page count."""
    if not image_paths:
        raise RuntimeError("No captured page images to merge.")

    images: list[Image.Image] = []
    try:
        for img_path in image_paths:
            with Image.open(img_path) as opened:
                images.append(opened.convert("RGB"))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(
            output_path,
            "PDF",
            save_all=True,
            append_images=images[1:],
        )
        return len(images)
    finally:
        for img in images:
            img.close()


def crawl_html_for_pdfs(
    session: requests.Session,
    start_url: str,
    max_pages: int,
    timeout: int,
) -> list[str]:
    """Crawl paginated HTML pages starting at start_url and collect PDF URLs in order."""
    visited_pages: set[str] = set()
    collected_pdfs: list[str] = []
    seen_pdfs: set[str] = set()

    current_url: str | None = start_url
    page_num = 0

    while current_url and current_url not in visited_pages and page_num < max_pages:
        page_num += 1
        visited_pages.add(current_url)
        log(f"[Crawl] Page {page_num}: {current_url}")

        try:
            resp = session.get(current_url, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log(f"[WARN] Failed to load page {current_url}: {exc}")
            break

        content_type = resp.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type or is_pdf_bytes(resp.content):
            if resp.url not in seen_pdfs:
                seen_pdfs.add(resp.url)
                collected_pdfs.append(resp.url)
            break

        if "html" not in content_type and b"<html" not in resp.content[:500].lower():
            break

        pdf_links = extract_pdf_links_from_html(resp.content, resp.url)
        for link in pdf_links:
            if link not in seen_pdfs:
                seen_pdfs.add(link)
                collected_pdfs.append(link)

        log(f"  Found {len(pdf_links)} PDF link(s) on page {page_num} (total unique: {len(collected_pdfs)})")
        next_url = find_next_page_url(resp.content, resp.url)
        current_url = next_url

    return collected_pdfs


def resolve_cdp_url(candidates: tuple[str, ...] = (
    "http://127.0.0.1:9223",
    "http://[::1]:9223",
    "http://127.0.0.1:9222",
    "http://[::1]:9222",
)) -> str | None:
    """Return the first reachable Chromium CDP endpoint URL, or None."""
    for candidate in candidates:
        try:
            resp = requests.get(f"{candidate}/json/version", timeout=1.5)
            if resp.status_code == 200 and "webSocketDebuggerUrl" in resp.text:
                return candidate
        except requests.RequestException:
            continue
    return None


PRINT_CLEANUP_STYLE_ID = "__ebookconverter_print_cleanup__"


def inject_print_cleanup(page) -> dict:
    """Temporarily normalize page layout for clean multi-page or fixed-layout (.pf) PDF printing."""
    try:
        result = page.evaluate(
            """(styleId) => {
                if (document.getElementById(styleId)) return { fixedLayout: false };

                const pf = document.querySelector('.pf');
                if (pf) {
                    const st = window.getComputedStyle(pf);
                    const w = Math.ceil(parseFloat(st.width) || pf.offsetWidth);
                    const h = Math.ceil(parseFloat(st.height) || pf.offsetHeight);
                    if (w > 100 && h > 100) {
                        const style = document.createElement('style');
                        style.id = styleId;
                        style.textContent = `
                            @page { size: ${w}px ${h + 2}px !important; margin: 0 !important; }
                            @media print {
                                html, body {
                                    width: ${w}px !important; height: auto !important; min-height: 0 !important;
                                    margin: 0 !important; padding: 0 !important;
                                    background: #fff !important; overflow: visible !important;
                                }
                                header, nav, footer { display: none !important; }
                                #sbo-rt-content, #pdf-main, .pdf-page {
                                    margin: 0 !important; padding: 0 !important;
                                    transform: none !important; width: ${w}px !important;
                                    max-width: ${w}px !important; min-width: 0 !important;
                                    height: auto !important; min-height: 0 !important;
                                    position: static !important; left: 0 !important; top: 0 !important;
                                }
                                .pdf-page {
                                    height: ${h}px !important; overflow: hidden !important;
                                    break-after: page !important; page-break-after: always !important;
                                    break-inside: avoid !important; page-break-inside: avoid !important;
                                }
                                .pdf-page:last-of-type {
                                    break-after: auto !important; page-break-after: auto !important;
                                }
                                .pf {
                                    margin: 0 !important; border: none !important; box-shadow: none !important;
                                }
                            }
                        `;
                        document.head.appendChild(style);

                        const rt = document.querySelector('#sbo-rt-content') || document.querySelector('#pdf-main') || pf.parentElement;
                        let cur = rt;
                        while (cur && cur.parentElement && cur.parentElement !== document.body) {
                            for (const sib of cur.parentElement.children) {
                                if (sib !== cur && sib.tagName !== 'STYLE' && sib.tagName !== 'SCRIPT') {
                                    sib.setAttribute('data-ec-hide', sib.style.display || '');
                                    sib.style.setProperty('display', 'none', 'important');
                                }
                            }
                            cur.parentElement.setAttribute('data-ec-pad', cur.parentElement.style.padding || '');
                            cur.parentElement.style.setProperty('display', 'block', 'important');
                            cur.parentElement.style.setProperty('padding', '0', 'important');
                            cur.parentElement.style.setProperty('margin', '0', 'important');
                            cur.parentElement.style.setProperty('height', 'auto', 'important');
                            cur.parentElement.style.setProperty('min-height', '0', 'important');
                            cur = cur.parentElement;
                        }
                        return { fixedLayout: true, pfCount: document.querySelectorAll('.pf').length };
                    }
                }

                const style = document.createElement('style');
                style.id = styleId;
                style.textContent = `
                    @page {
                        margin: 0.5in 0.4in !important;
                    }
                    @media print {
                        html, body, main, article, section, [role="main"] {
                            overflow: visible !important;
                            height: auto !important;
                            max-height: none !important;
                            width: 100% !important;
                            max-width: none !important;
                            margin: 0 !important;
                            padding: 0 !important;
                        }
                        header, nav, footer, aside,
                        [role="complementary"], [role="navigation"], [role="banner"],
                        [class*="sidebar" i], [class*="drawer" i],
                        [class*="sidePanel" i], [class*="side-panel" i],
                        [class*="other-formats" i] {
                            display: none !important;
                        }
                        p, li, h1, h2, h3, h4, h5, h6, pre, blockquote, tr, figure, img {
                            break-inside: avoid;
                            page-break-inside: avoid;
                            orphans: 3;
                            widows: 3;
                        }
                    }
                `;
                document.head.appendChild(style);

                const rt = document.querySelector('#sbo-rt-content, .sbo-reader-content, [data-testid="book-content"], main article, main');
                if (rt && ((rt.innerText || '').trim().length > 0 || rt.querySelector('img, canvas, svg'))) {
                    let cur = rt;
                    while (cur && cur.parentElement && cur.parentElement !== document.body) {
                        for (const sib of cur.parentElement.children) {
                            if (sib !== cur && sib.tagName !== 'STYLE' && sib.tagName !== 'SCRIPT') {
                                sib.setAttribute('data-ec-hide', sib.style.display || '');
                                sib.style.setProperty('display', 'none', 'important');
                            }
                        }
                        cur.parentElement.setAttribute('data-ec-style', cur.parentElement.getAttribute('style') || '');
                        cur.parentElement.style.setProperty('display', 'block', 'important');
                        cur.parentElement.style.setProperty('width', '100%', 'important');
                        cur.parentElement.style.setProperty('max-width', 'none', 'important');
                        cur.parentElement.style.setProperty('min-width', '0', 'important');
                        cur.parentElement.style.setProperty('padding', '0', 'important');
                        cur.parentElement.style.setProperty('margin', '0', 'important');
                        cur = cur.parentElement;
                    }
                    rt.setAttribute('data-ec-style', rt.getAttribute('style') || '');
                    rt.style.setProperty('display', 'block', 'important');
                    rt.style.setProperty('width', '100%', 'important');
                    rt.style.setProperty('max-width', 'none', 'important');
                    rt.style.setProperty('min-width', '0', 'important');
                    rt.style.setProperty('margin', '0 auto', 'important');
                }

                for (const el of document.querySelectorAll('*')) {
                    const pos = window.getComputedStyle(el).position;
                    if (pos === 'fixed' || pos === 'sticky') {
                        el.setAttribute('data-ec-orig-pos', el.style.position || '');
                        el.style.setProperty('position', 'static', 'important');
                    }
                }
                return { fixedLayout: false };
            }""",
            PRINT_CLEANUP_STYLE_ID,
        )
        return result if isinstance(result, dict) else {"fixedLayout": False}
    except Exception:
        return {"fixedLayout": False}


def remove_print_cleanup(page) -> None:
    """Restore DOM styles modified by inject_print_cleanup."""
    try:
        page.evaluate(
            """(styleId) => {
                document.getElementById(styleId)?.remove();
                for (const el of document.querySelectorAll('[data-ec-hide]')) {
                    el.style.display = el.getAttribute('data-ec-hide');
                    el.removeAttribute('data-ec-hide');
                }
                for (const el of document.querySelectorAll('[data-ec-pad]')) {
                    el.style.padding = el.getAttribute('data-ec-pad');
                    el.style.removeProperty('display');
                    el.style.removeProperty('margin');
                    el.style.removeProperty('height');
                    el.style.removeProperty('min-height');
                    el.removeAttribute('data-ec-pad');
                }
                for (const el of document.querySelectorAll('[data-ec-style]')) {
                    const origStyle = el.getAttribute('data-ec-style');
                    if (origStyle) {
                        el.setAttribute('style', origStyle);
                    } else {
                        el.removeAttribute('style');
                    }
                    el.removeAttribute('data-ec-style');
                }
                for (const el of document.querySelectorAll('[data-ec-orig-pos]')) {
                    const orig = el.getAttribute('data-ec-orig-pos');
                    if (orig) {
                        el.style.position = orig;
                    } else {
                        el.style.removeProperty('position');
                    }
                    el.removeAttribute('data-ec-orig-pos');
                }
            }""",
            PRINT_CLEANUP_STYLE_ID,
        )
    except Exception:
        pass


def pdf_content_signature(pdf_bytes: bytes) -> str:
    """Return a deterministic content signature for a PDF ignoring metadata timestamps."""
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            parts: list[str] = []
            for pg in doc:
                img_hashes: list[str] = []
                for img in pg.get_images():
                    xref = img[0]
                    try:
                        img_bytes = doc.extract_image(xref)["image"]
                        img_hashes.append(hashlib.sha256(img_bytes).hexdigest()[:16])
                    except Exception:
                        img_hashes.append(str(xref))
                parts.append(
                    f"{pg.rect.width:.1f}x{pg.rect.height:.1f}|{','.join(img_hashes)}|{pg.get_text()}"
                )
            return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(pdf_bytes).hexdigest()


def strip_trailing_blank_pages(pdf_bytes: bytes) -> bytes:
    """Remove trailing completely blank pages produced by CSS page-break-after."""
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if len(doc) <= 1:
                return pdf_bytes
            last_non_blank = len(doc) - 1
            while last_non_blank > 0:
                pg = doc[last_non_blank]
                if pg.get_text().strip() or pg.get_images():
                    break
                last_non_blank -= 1
            if last_non_blank < len(doc) - 1:
                doc.delete_pages(from_page=last_non_blank + 1, to_page=len(doc) - 1)
                return doc.tobytes()
    except Exception:
        pass
    return pdf_bytes


def capture_fixed_layout_pf_pages(page, cdp_session=None) -> bytes | None:
    """Capture each .pf fixed-layout page via element screenshot so scaled/absolute layouts never clip."""
    pfs = page.locator(".pf")
    pf_count = pfs.count()
    if not pf_count:
        return None

    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('header, nav, [class*="iconMenu"], [class*="bottomIcons"]').forEach(el => {
                    el.setAttribute('data-ec-pf-hide', el.style.display || '');
                    el.style.setProperty('display', 'none', 'important');
                });
            }"""
        )
        images: list[Image.Image] = []
        try:
            for idx in range(pf_count):
                el = pfs.nth(idx)
                el.scroll_into_view_if_needed(timeout=5000)
                png_bytes = el.screenshot(timeout=15000)
                with Image.open(io.BytesIO(png_bytes)) as opened:
                    images.append(opened.convert("RGB"))

            if not images:
                return None

            buf = io.BytesIO()
            images[0].save(
                buf,
                "PDF",
                save_all=True,
                append_images=images[1:],
            )
            return buf.getvalue()
        finally:
            for img in images:
                img.close()
    finally:
        try:
            page.evaluate(
                """() => {
                    document.querySelectorAll('[data-ec-pf-hide]').forEach(el => {
                        el.style.display = el.getAttribute('data-ec-pf-hide');
                        el.removeAttribute('data-ec-pf-hide');
                    });
                }"""
            )
        except Exception:
            pass


def capture_page_as_pdf_bytes(page, cdp_session=None) -> bytes:
    """Capture the current Playwright page as PDF bytes via printToPDF or screenshot fallback."""
    fixed_bytes = capture_fixed_layout_pf_pages(page, cdp_session=cdp_session)
    if fixed_bytes is not None:
        return fixed_bytes

    cleanup_info = inject_print_cleanup(page)
    fixed_layout = bool(cleanup_info.get("fixedLayout"))
    cdp_params = (
        {
            "printBackground": True,
            "preferCSSPageSize": True,
            "marginTop": 0,
            "marginBottom": 0,
            "marginLeft": 0,
            "marginRight": 0,
        }
        if fixed_layout
        else {
            "printBackground": True,
            "preferCSSPageSize": False,
            "marginTop": 0.5,
            "marginBottom": 0.5,
            "marginLeft": 0.4,
            "marginRight": 0.4,
        }
    )

    try:
        if cdp_session is not None:
            try:
                import base64

                result = cdp_session.send("Page.printToPDF", cdp_params)
                data = base64.b64decode(result["data"])
                if is_pdf_bytes(data):
                    return strip_trailing_blank_pages(data) if fixed_layout else data
            except Exception:
                pass

        try:
            data = page.pdf(
                print_background=True,
                prefer_css_page_size=fixed_layout,
                margin=(
                    {"top": "0", "bottom": "0", "left": "0", "right": "0"}
                    if fixed_layout
                    else {"top": "0.5in", "bottom": "0.5in", "left": "0.4in", "right": "0.4in"}
                ),
            )
            return strip_trailing_blank_pages(data) if fixed_layout else data
        except Exception:
            png_bytes = page.screenshot(full_page=False)
            buf = io.BytesIO()
            with Image.open(io.BytesIO(png_bytes)) as img:
                img.convert("RGB").save(buf, "PDF")
            return buf.getvalue()
    finally:
        remove_print_cleanup(page)


def wait_for_page_ready(page, timeout_ms: int = 8000) -> None:
    """Wait until loading spinners, placeholder text, and pending images finish rendering."""
    try:
        page.wait_for_load_state("networkidle", timeout=min(2000, timeout_ms))
    except Exception:
        pass

    try:
        page.wait_for_function(
            """() => {
                if (document.readyState !== 'complete') return false;

                const bodyText = (document.body?.innerText || '').trim();
                const loadingPhrases = [
                    'hang tight',
                    "this'll take just a sec",
                    'loading...',
                    '読み込み中',
                ];
                const lowerText = bodyText.toLowerCase();
                if (loadingPhrases.some(phrase => lowerText.includes(phrase))) {
                    return false;
                }

                const spinnerSelectors = [
                    '[aria-busy="true"]',
                    '.loading',
                    '.spinner',
                    '.loader',
                    '[class*="spinner" i]',
                ];
                for (const sel of spinnerSelectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        const visible = (
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            parseFloat(style.opacity || '1') > 0 &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                        if (visible) return false;
                    }
                }

                const hasText = bodyText.length > 0;
                const hasCanvas = Array.from(document.querySelectorAll('canvas')).some(
                    c => c.width > 0 && c.height > 0
                );
                const imgs = Array.from(document.querySelectorAll('img'));
                const pendingImg = imgs.some(img => !img.complete);
                if (pendingImg) return false;

                return hasText || hasCanvas || imgs.length > 0;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        # Proceed if a non-standard viewer never satisfies every heuristic within timeout
        pass

    # Brief settle window for fonts and layout painting
    page.wait_for_timeout(500)


def check_access_denied(page, response_status: int | None) -> None:
    """Raise a clear error if the loaded page is an Access Denied / 403 block."""
    try:
        title = (page.title() or "").strip().lower()
        body_text = (page.inner_text("body") or "")[:1000].lower()
    except Exception:
        return

    if response_status in {401, 403} or "access denied" in title or "you don't have permission to access" in body_text:
        raise RuntimeError(
            "アクセスが拒否されました (Access Denied)。"
            "ログインや認証が必要なサイトの場合は、Chrome を `--remote-debugging-port=9222` で起動してログイン済みの状態で再実行してください。"
        )


def fetch_via_browser(
    start_url: str,
    output_path: Path,
    max_pages: int,
    timeout: int,
    direction: str = "right",
) -> int:
    """Use CDP (logged-in Chrome) or headless Chromium to paginate with ArrowRight/ArrowLeft and capture PDFs."""
    from playwright.sync_api import sync_playwright

    captured_pdfs: list[bytes] = []
    seen_pdf_urls: set[str] = set()

    with sync_playwright() as p:
        active_cdp_url = resolve_cdp_url()
        use_cdp = active_cdp_url is not None
        if use_cdp and active_cdp_url is not None:
            log(f"[Browser] Connecting to logged-in Chrome via CDP ({active_cdp_url})...")
            browser = p.chromium.connect_over_cdp(active_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = next((pg for pg in context.pages if pg.url == start_url), None)
            created_page = False
            if page is None:
                page = context.new_page()
                created_page = True
        else:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 1600},
            )
            page = context.new_page()
            created_page = True

        cdp_session = None
        try:
            cdp_session = context.new_cdp_session(page)
        except Exception:
            cdp_session = None

        def handle_response(response) -> None:
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                if "application/pdf" in ctype and response.url not in seen_pdf_urls:
                    body = response.body()
                    if is_pdf_bytes(body):
                        seen_pdf_urls.add(response.url)
                        captured_pdfs.append(body)
                        log(f"[Browser] Intercepted PDF response: {response.url} ({len(body)} bytes)")
            except Exception:
                pass

        page.on("response", handle_response)
        response_status: int | None = None
        if created_page or page.url != start_url:
            log(f"[Browser] Opening {start_url} ...")
            nav_resp = page.goto(start_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            response_status = nav_resp.status if nav_resp else None
        else:
            log(f"[Browser] Using already-open tab: {page.url}")
            try:
                page.bring_to_front()
            except Exception:
                pass

        log("[Browser] Waiting for page render...")
        wait_for_page_ready(page, timeout_ms=8000)
        check_access_denied(page, response_status)

        # 1. If a PDF was fetched via XHR/embed during load, merge and return
        if captured_pdfs:
            browser.close()
            return merge_pdf_bytes_list(captured_pdfs, output_path)

        # 2. Check DOM for JS-injected PDF links / iframes
        html = page.content()
        dom_pdf_links = extract_pdf_links_from_html(html, page.url)
        if dom_pdf_links:
            session = create_session()
            blobs: list[bytes] = []
            for link in dom_pdf_links:
                blob = download_if_pdf(session, link, timeout=timeout)
                if blob:
                    blobs.append(blob)
            if blobs:
                browser.close()
                return merge_pdf_bytes_list(blobs, output_path)

        # 3. Paginate with ArrowRight / ArrowLeft and capture each page via PDF command
        turn_key = "ArrowLeft" if direction == "left" else "ArrowRight"
        if direction == "left":
            arrow_button_selectors = [
                "button:has-text('←'):not([disabled])",
                "a:has-text('←')",
                "button:has-text('‹'):not([disabled])",
                "a:has-text('‹')",
                "[aria-label*='Next' i]:not([disabled])",
                "[title*='Next' i]:not([disabled])",
                "button:has-text('次へ'):not([disabled])",
                "a:has-text('次へ')",
                "[rel='next']",
            ]
        else:
            arrow_button_selectors = [
                "button:has-text('→'):not([disabled])",
                "a:has-text('→')",
                "button:has-text('›'):not([disabled])",
                "a:has-text('›')",
                "[aria-label*='Next' i]:not([disabled])",
                "[title*='Next' i]:not([disabled])",
                "button:has-text('次へ'):not([disabled])",
                "a:has-text('次へ')",
                "[rel='next']",
            ]

        collected_page_pdfs: list[bytes] = []
        prev_hash: str | None = None
        prev_pdf_hash: str | None = None

        for page_idx in range(1, max_pages + 1):
            wait_for_page_ready(page, timeout_ms=8000)
            png_bytes = page.screenshot(full_page=False, timeout=15000)
            current_hash = hashlib.sha256(png_bytes).hexdigest()

            if prev_hash == current_hash:
                log(f"[Browser] Page {page_idx} identical to previous; stopping pagination.")
                break

            prev_hash = current_hash
            pdf_count_before = len(captured_pdfs)
            page_pdf_bytes = capture_page_as_pdf_bytes(page, cdp_session=cdp_session)
            current_pdf_hash = pdf_content_signature(page_pdf_bytes)
            if prev_pdf_hash == current_pdf_hash:
                log(f"[Browser] Page {page_idx} PDF content identical to previous; stopping pagination.")
                break
            prev_pdf_hash = current_pdf_hash
            collected_page_pdfs.append(page_pdf_bytes)
            log(f"[Browser] Captured page {page_idx} via PDF command ({len(page_pdf_bytes)} bytes)")

            url_before_turn = page.url
            # Press ArrowRight or ArrowLeft to advance to the next page
            page.keyboard.press(turn_key)
            page.wait_for_timeout(500)
            wait_for_page_ready(page, timeout_ms=8000)

            # If a PDF response was triggered by the page turn, prefer collected PDFs if complete
            if len(captured_pdfs) > pdf_count_before:
                log(f"[Browser] Intercepted PDF response after page turn {page_idx}.")

            # Fallback: if ArrowRight key did not change the URL or view, try clicking a right-arrow / next button
            after_key_hash = hashlib.sha256(page.screenshot(full_page=False, timeout=15000)).hexdigest()
            if page.url == url_before_turn and after_key_hash == current_hash:
                for selector in arrow_button_selectors:
                    locator = page.locator(selector).first
                    try:
                        if locator.count() > 0 and locator.is_visible():
                            locator.click(timeout=3000)
                            page.wait_for_timeout(500)
                            wait_for_page_ready(page, timeout_ms=8000)
                            break
                    except Exception:
                        continue

        if not use_cdp:
            browser.close()
        if captured_pdfs and len(captured_pdfs) >= len(collected_page_pdfs):
            return merge_pdf_bytes_list(captured_pdfs, output_path)
        return merge_pdf_bytes_list(collected_page_pdfs, output_path)


def fetch_and_merge(
    url: str,
    output_path: Path,
    max_pages: int = 1000,
    timeout: int = 30,
    allow_browser: bool = True,
    direction: str = "right",
) -> int:
    """Fetch PDF(s) from url (with pagination) and write a single merged PDF to output_path."""
    if not is_http_url(url):
        raise ValueError(f"Invalid URL (must start with http:// or https://): {url}")

    session = create_session()

    # Step 1: Direct PDF check
    log(f"[Fetch] Checking URL: {url}")
    direct_blob = download_if_pdf(session, url, timeout=timeout)
    if direct_blob is not None:
        log("[Fetch] Direct PDF detected.")
        return merge_pdf_bytes_list([direct_blob], output_path)

    # Step 2: HTML pagination + PDF link crawl
    pdf_urls = crawl_html_for_pdfs(session, url, max_pages=max_pages, timeout=timeout)
    if pdf_urls:
        log(f"[Download] Downloading {len(pdf_urls)} PDF file(s)...")
        blobs: list[bytes] = []
        for idx, pdf_url in enumerate(pdf_urls, start=1):
            log(f"  [{idx}/{len(pdf_urls)}] {pdf_url}")
            blob = download_if_pdf(session, pdf_url, timeout=timeout)
            if blob is not None:
                blobs.append(blob)
        if blobs:
            return merge_pdf_bytes_list(blobs, output_path)

    # Step 3: Browser fallback for JS viewers / embedded PDFs
    if allow_browser:
        log("[Fetch] No static PDF links found. Trying headless browser...")
        return fetch_via_browser(url, output_path, max_pages=max_pages, timeout=timeout, direction=direction)

    raise RuntimeError(f"No PDF content found at URL: {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch PDF(s) from a URL with pagination and merge into one PDF.")
    parser.add_argument("--url", required=True, help="Source URL")
    parser.add_argument("--output", required=True, help="Output merged PDF path")
    parser.add_argument("--max-pages", type=int, default=1000, help="Maximum pages to paginate (default: 1000)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds (default: 30)")
    parser.add_argument("--direction", choices=["right", "left"], default="right", help="Page-turn key direction (default: right)")
    parser.add_argument("--no-browser", action="store_true", help="Disable Playwright browser fallback")
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    try:
        total_pages = fetch_and_merge(
            url=args.url.strip(),
            output_path=output_path,
            max_pages=max(1, args.max_pages),
            timeout=max(5, args.timeout),
            allow_browser=not args.no_browser,
            direction=args.direction,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1

    log(f"OUTPUT_PDF={output_path}")
    log(f"PAGES={total_pages}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
