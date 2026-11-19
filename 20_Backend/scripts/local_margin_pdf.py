#!/usr/bin/env python3
"""Convert local EPUB files or image folders into cropped PDFs.

This covers the local-file workflow used for manga/image-heavy ebooks:
- extract ordered images from non-DRM EPUBs
- crop white/transparent outer margins
- optionally split two-page spreads right-to-left
- assemble the processed pages into a PDF
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from xml.etree import ElementTree

from PIL import Image


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_PAGE_SIZE = (836, 1200)


@dataclass(frozen=True)
class SourceImage:
    label: str
    suffix: str
    data: bytes


@dataclass(frozen=True)
class PdfPage:
    width: int
    height: int
    data: bytes


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def parse_page_size(raw: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[:xX]\s*(\d+)\s*", raw)
    if not match:
        raise argparse.ArgumentTypeError("page size must look like 836:1200")
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("page size values must be positive")
    return width, height


def normalize_posix_path(path: str) -> str:
    return str(PurePosixPath(path))


def resolve_epub_href(base_dir: PurePosixPath, href: str) -> str:
    href = href.split("#", 1)[0]
    return normalize_posix_path(base_dir / href)


def parse_xml(data: bytes, path: str) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        print(f"[WARN] Could not parse XML {path}: {exc}")
        return None


def first_attr(element: ElementTree.Element, names: Iterable[str]) -> str | None:
    for name in names:
        value = element.attrib.get(name)
        if value:
            return value
    for attr, value in element.attrib.items():
        if attr.rsplit("}", 1)[-1] in names and value:
            return value
    return None


def iter_epub_images(epub_path: Path) -> list[SourceImage]:
    with zipfile.ZipFile(epub_path) as archive:
        names = set(archive.namelist())
        container = parse_xml(archive.read("META-INF/container.xml"), "META-INF/container.xml")
        if container is None:
            raise SystemExit("EPUB container.xml is unreadable")

        rootfile = None
        for element in container.iter():
            if element.tag.rsplit("}", 1)[-1] == "rootfile":
                rootfile = element.attrib.get("full-path")
                break
        if not rootfile:
            raise SystemExit("EPUB rootfile not found")

        opf_root = parse_xml(archive.read(rootfile), rootfile)
        if opf_root is None:
            raise SystemExit("EPUB OPF is unreadable")

        opf_dir = PurePosixPath(rootfile).parent
        manifest: dict[str, tuple[str, str]] = {}
        spine_ids: list[str] = []
        for element in opf_root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "item":
                item_id = element.attrib.get("id")
                href = element.attrib.get("href")
                media_type = element.attrib.get("media-type", "")
                if item_id and href:
                    manifest[item_id] = (resolve_epub_href(opf_dir, href), media_type)
            elif tag == "itemref":
                idref = element.attrib.get("idref")
                if idref:
                    spine_ids.append(idref)

        ordered_paths: list[str] = []
        seen_paths: set[str] = set()

        def add_image_path(path: str) -> None:
            if path in names and PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS and path not in seen_paths:
                ordered_paths.append(path)
                seen_paths.add(path)

        for idref in spine_ids:
            item = manifest.get(idref)
            if not item:
                continue
            html_path, media_type = item
            if PurePosixPath(html_path).suffix.lower() in IMAGE_EXTENSIONS:
                add_image_path(html_path)
                continue
            if "html" not in media_type and PurePosixPath(html_path).suffix.lower() not in {".xhtml", ".html", ".htm"}:
                continue
            if html_path not in names:
                continue
            html_root = parse_xml(archive.read(html_path), html_path)
            if html_root is None:
                continue
            html_dir = PurePosixPath(html_path).parent
            for element in html_root.iter():
                tag = element.tag.rsplit("}", 1)[-1].lower()
                if tag in {"img", "image"}:
                    href = first_attr(element, ("src", "href", "xlink:href"))
                    if href:
                        add_image_path(resolve_epub_href(html_dir, href))

        for path in sorted(names, key=natural_key):
            if PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS and path not in seen_paths:
                add_image_path(path)

        return [
            SourceImage(label=path, suffix=PurePosixPath(path).suffix.lower(), data=archive.read(path))
            for path in ordered_paths
        ]


def iter_directory_images(input_dir: Path) -> list[SourceImage]:
    files = sorted(
        [p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS],
        key=lambda p: [natural_key(part) for part in p.relative_to(input_dir).parts],
    )
    return [SourceImage(label=str(path.relative_to(input_dir)), suffix=path.suffix.lower(), data=path.read_bytes()) for path in files]


def open_image(source: SourceImage) -> Image.Image:
    image = Image.open(io.BytesIO(source.data))
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        alpha = image.getchannel("A")
        background.paste(image.convert("RGB"), mask=alpha)
        return background
    return image.convert("RGB")


def nonwhite_ratio(image: Image.Image, threshold: int) -> float:
    histogram = image.convert("L").histogram()
    return sum(histogram[:threshold]) / (image.size[0] * image.size[1])


def content_bbox(image: Image.Image, threshold: int) -> tuple[int, int, int, int] | None:
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value < threshold else 0)
    return mask.getbbox()


def crop_outer_margin(image: Image.Image, threshold: int, margin_px: int) -> Image.Image:
    bbox = content_bbox(image, threshold)
    if not bbox:
        return image.copy()
    left, top, right, bottom = bbox
    return image.crop(
        (
            max(0, left - margin_px),
            max(0, top - margin_px),
            min(image.size[0], right + margin_px),
            min(image.size[1], bottom + margin_px),
        )
    )


def crop_half_to_page_ratio(
    half: Image.Image,
    *,
    threshold: int,
    page_size: tuple[int, int],
    margin_px: int,
    min_crop_height: int,
) -> Image.Image:
    page_width, page_height = page_size
    ratio = page_width / page_height
    half_width, half_height = half.size
    bbox = content_bbox(half, threshold)
    full_width = int(round(half_height * ratio))
    full_width -= full_width % 2

    if not bbox:
        left = max(0, (half_width - full_width) // 2)
        return half.crop((left, 0, left + full_width, half_height)).resize(page_size, Image.Resampling.LANCZOS)

    x0, y0, x1, y1 = bbox
    content_width = x1 - x0
    content_height = y1 - y0

    crop_height = max(min_crop_height, content_height + 2 * margin_px)
    crop_width = max(crop_height * ratio, content_width + 2 * margin_px)
    crop_height = max(crop_height, crop_width / ratio)
    crop_height = min(half_height, int(round(crop_height)))
    crop_width = min(half_width, int(round(crop_height * ratio)))

    if crop_width < content_width + 2 * margin_px:
        crop_width = min(half_width, content_width + 2 * margin_px)
        crop_height = min(half_height, int(round(crop_width / ratio)))

    crop_width -= crop_width % 2
    center_x = (x0 + x1) / 2
    center_y = (y0 + y1) / 2
    left = int(round(center_x - crop_width / 2))
    top = int(round(center_y - crop_height / 2))
    left = max(0, min(half_width - crop_width, left))
    top = max(0, min(half_height - crop_height, top))
    return half.crop((left, top, left + crop_width, top + crop_height)).resize(page_size, Image.Resampling.LANCZOS)


def image_to_jpeg_page(image: Image.Image, quality: int) -> PdfPage:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True, progressive=False)
    return PdfPage(width=image.size[0], height=image.size[1], data=buffer.getvalue())


def should_split_spread(image: Image.Image, mode: str, min_ratio: float) -> bool:
    if mode == "never":
        return False
    if mode == "always":
        return image.size[0] > image.size[1]
    return image.size[0] / max(1, image.size[1]) >= min_ratio


def select_cover(images: list[SourceImage]) -> tuple[SourceImage | None, list[SourceImage]]:
    dimensions: list[tuple[SourceImage, tuple[int, int]]] = []
    for source in images:
        with open_image(source) as image:
            dimensions.append((source, image.size))
    portrait = [(source, size) for source, size in dimensions if size[1] > size[0]]
    landscape = [(source, size) for source, size in dimensions if size[0] > size[1]]
    if len(portrait) == 1 and len(landscape) >= 2:
        cover = portrait[0][0]
        return cover, [source for source in images if source != cover]
    return None, images


def build_pages(args: argparse.Namespace, images: list[SourceImage]) -> tuple[list[PdfPage], list[str]]:
    pages: list[PdfPage] = []
    skipped: list[str] = []
    skip_hashes = set(args.skip_hash or [])

    cover, ordered_images = select_cover(images)
    if cover is not None:
        with open_image(cover) as cover_image:
            pages.append(image_to_jpeg_page(cover_image, args.jpeg_quality))
        print(f"[INFO] Cover: {cover.label}")

    for source in ordered_images:
        digest = hashlib.sha256(source.data).hexdigest()
        if digest in skip_hashes:
            skipped.append(f"{source.label}: matching skip hash")
            continue

        with open_image(source) as image:
            if should_split_spread(image, args.split_spreads, args.spread_min_ratio):
                width, height = image.size
                midpoint = width // 2
                halves = [("R", image.crop((midpoint, 0, width, height))), ("L", image.crop((0, 0, midpoint, height)))]
                for side, half in halves:
                    density = nonwhite_ratio(half, args.crop_threshold)
                    if density < args.skip_blank_threshold:
                        skipped.append(f"{source.label} {side}: low-density page ({density:.4f})")
                        continue
                    page_image = crop_half_to_page_ratio(
                        half,
                        threshold=args.crop_threshold,
                        page_size=args.page_size,
                        margin_px=args.margin_px,
                        min_crop_height=args.min_crop_height,
                    )
                    pages.append(image_to_jpeg_page(page_image, args.jpeg_quality))
            else:
                if args.skip_blank_pages and nonwhite_ratio(image, args.crop_threshold) < args.skip_blank_threshold:
                    skipped.append(f"{source.label}: low-density page")
                    continue
                page_image = crop_outer_margin(image, args.crop_threshold, args.margin_px) if args.crop_margins else image.copy()
                pages.append(image_to_jpeg_page(page_image, args.jpeg_quality))

    return pages, skipped


def pdf_escape(value: str) -> bytes:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("utf-8")


def write_pdf(pages: list[PdfPage], out_path: Path, title: str) -> None:
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    catalog_id = add(b"")
    pages_id = add(b"")
    page_ids: list[int] = []

    for page in pages:
        image_id = add(
            b"<< /Type /XObject /Subtype /Image "
            + f"/Width {page.width} /Height {page.height} ".encode("ascii")
            + b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode\n"
            + f"/Length {len(page.data)} >>\nstream\n".encode("ascii")
            + page.data
            + b"\nendstream"
        )
        page_width = page.width * 72.0 / 100.0
        page_height = page.height * 72.0 / 100.0
        content = f"q\n{page_width:.4f} 0 0 {page_height:.4f} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
        content_id = add(f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_width:.4f} {page_height:.4f}] ".encode(
                "ascii"
            )
            + f"/Resources << /XObject << /Im0 {image_id} 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
        )
        page_ids.append(page_id)

    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [" + kids + f"] /Count {len(page_ids)} >>".encode("ascii")
    info_id = add(b"<< /Title (" + pdf_escape(title) + b") /Producer (EbookConverter local-margin-pdf) >>")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(out_path.parent), prefix=out_path.name + ".tmp.") as file:
        temp_path = Path(file.name)
        file.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for idx, obj in enumerate(objects, start=1):
            offsets.append(file.tell())
            file.write(f"{idx} 0 obj\n".encode("ascii"))
            file.write(obj)
            file.write(b"\nendobj\n")
        xref = file.tell()
        file.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        file.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            file.write(f"{offset:010d} 00000 n \n".encode("ascii"))
        file.write(b"trailer\n")
        file.write(f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n".encode("ascii"))
        file.write(b"startxref\n")
        file.write(f"{xref}\n%%EOF\n".encode("ascii"))

    os.replace(temp_path, out_path)


def default_output_for(input_path: Path) -> Path:
    if input_path.is_dir():
        return Path.cwd() / f"{input_path.name}.pdf"
    return Path.cwd() / f"{input_path.stem}.pdf"


def load_input_images(input_path: Path) -> list[SourceImage]:
    if input_path.is_dir():
        return iter_directory_images(input_path)
    if input_path.suffix.lower() == ".epub":
        return iter_epub_images(input_path)
    if input_path.suffix.lower() in IMAGE_EXTENSIONS:
        return [SourceImage(label=input_path.name, suffix=input_path.suffix.lower(), data=input_path.read_bytes())]
    raise SystemExit(f"Unsupported input: {input_path}")


def run_qpdf_check(pdf_path: Path) -> None:
    qpdf = shutil.which("qpdf")
    if not qpdf:
        print("[WARN] qpdf not found; skipped PDF structural validation")
        return
    subprocess.run([qpdf, "--check", str(pdf_path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert local EPUB/image input to a cropped PDF.")
    parser.add_argument("--input", required=True, help="Input EPUB, image file, or image directory.")
    parser.add_argument("--output", help="Output PDF path. Defaults to <input-name>.pdf in the current directory.")
    parser.add_argument("--crop-margins", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--crop-threshold", type=int, default=245, help="Brightness threshold for white-margin detection.")
    parser.add_argument("--margin-px", type=int, default=22, help="Padding kept around detected content.")
    parser.add_argument("--split-spreads", choices=["never", "auto", "always"], default="never")
    parser.add_argument("--spread-min-ratio", type=float, default=1.35, help="Width/height ratio used by --split-spreads auto.")
    parser.add_argument("--page-size", type=parse_page_size, default=DEFAULT_PAGE_SIZE, help="Split page output size, e.g. 836:1200.")
    parser.add_argument("--min-crop-height", type=int, default=820, help="Minimum crop height before resizing split pages.")
    parser.add_argument("--skip-blank-threshold", type=float, default=0.025)
    parser.add_argument("--skip-blank-pages", action="store_true", help="Also skip low-density pages when not splitting spreads.")
    parser.add_argument("--skip-hash", action="append", help="SHA-256 hash of an image to skip. Can be passed multiple times.")
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--qpdf-check", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input does not exist: {input_path}")
    out_path = Path(args.output).expanduser().resolve() if args.output else default_output_for(input_path).resolve()

    images = load_input_images(input_path)
    if not images:
        raise SystemExit("No supported images found in input")

    pages, skipped = build_pages(args, images)
    if not pages:
        raise SystemExit("No PDF pages generated")

    write_pdf(pages, out_path, input_path.stem)
    if args.qpdf_check:
        run_qpdf_check(out_path)

    print(f"[DONE] PDF: {out_path}")
    print(f"[DONE] source_images={len(images)} pdf_pages={len(pages)} skipped={len(skipped)}")
    for item in skipped:
        print(f"[SKIP] {item}")


if __name__ == "__main__":
    main()
