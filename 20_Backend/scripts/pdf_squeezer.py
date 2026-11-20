#!/usr/bin/env python3
"""PDF Squeezer-style compressor for WebTPDF Mac using PyMuPDF."""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf


@dataclass(frozen=True)
class CompressionPreset:
    name: str
    dpi_threshold: int
    dpi_target: int
    quality: int


PRESETS: dict[str, CompressionPreset] = {
    "light": CompressionPreset(name="light", dpi_threshold=180, dpi_target=150, quality=85),
    "balanced": CompressionPreset(name="balanced", dpi_threshold=150, dpi_target=120, quality=75),
    "strong": CompressionPreset(name="strong", dpi_threshold=120, dpi_target=96, quality=60),
}


def format_bytes(size: int) -> str:
    """Format byte size into human-readable MB/KB."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def compress_pdf(
    input_path: Path,
    output_path: Path,
    preset: CompressionPreset,
    in_place: bool = False,
) -> tuple[int, int, bool]:
    """Compress a single PDF and return (before_bytes, after_bytes, replaced_or_written)."""
    before_bytes = input_path.stat().st_size
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        dir=str(output_path.parent),
        prefix=f".{input_path.stem}_",
        suffix=".pdf",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with fitz.open(str(input_path)) as doc:
            doc.rewrite_images(
                dpi_threshold=preset.dpi_threshold,
                dpi_target=preset.dpi_target,
                quality=preset.quality,
                lossy=True,
                lossless=True,
                bitonal=True,
                color=True,
                gray=True,
            )
            doc.save(
                str(tmp_path),
                garbage=4,
                deflate=True,
                deflate_images=True,
                deflate_fonts=True,
                clean=True,
                use_objstms=1,
            )

        after_bytes = tmp_path.stat().st_size
        if in_place and after_bytes >= before_bytes:
            tmp_path.unlink(missing_ok=True)
            return before_bytes, before_bytes, False

        tmp_path.replace(output_path)
        return before_bytes, after_bytes, True
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def collect_pdfs(inputs: list[Path], recursive: bool) -> list[Path]:
    """Expand file and directory inputs into a sorted list of unique PDF paths."""
    seen: set[Path] = set()
    result: list[Path] = []
    for item in inputs:
        resolved = item.expanduser().resolve()
        if resolved.is_dir():
            iterator = resolved.rglob("*.pdf") if recursive else resolved.glob("*.pdf")
            for p in sorted(iterator):
                if p.is_file() and not p.stem.endswith("_compressed") and p not in seen:
                    seen.add(p)
                    result.append(p)
        elif resolved.is_file() and resolved.suffix.lower() == ".pdf":
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compress PDF files using PyMuPDF.")
    parser.add_argument("--input", dest="inputs", action="append", required=True, help="PDF file or folder path (repeatable)")
    parser.add_argument("--output-dir", help="Optional output directory (ignored when --in-place is set)")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), default="balanced", help="Compression strength preset")
    parser.add_argument("--in-place", action="store_true", help="Overwrite original PDF when smaller")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories when input is a folder")
    args = parser.parse_args()

    preset = PRESETS[args.preset]
    pdfs = collect_pdfs([Path(p) for p in args.inputs], recursive=args.recursive)
    if not pdfs:
        print("Error: No PDF files found.", file=sys.stderr, flush=True)
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else None
    total_before = 0
    total_after = 0
    total_count = len(pdfs)

    print(f"[Squeezer] Starting ({total_count} file(s), preset={preset.name}, in_place={args.in_place})", flush=True)

    for idx, src in enumerate(pdfs, start=1):
        if args.in_place:
            dst = src
        elif output_dir is not None:
            dst = output_dir / src.name
        else:
            dst = src.with_name(f"{src.stem}_compressed.pdf")

        try:
            before, after, written = compress_pdf(src, dst, preset=preset, in_place=args.in_place)
            total_before += before
            total_after += after
            saved_pct = ((before - after) / before * 100.0) if before > 0 else 0.0
            status = "OK" if written else "SKIP (already optimal)"
            print(
                f"[{idx}/{total_count}] {status}: {src.name} | {format_bytes(before)} -> {format_bytes(after)} (-{saved_pct:.1f}%)",
                flush=True,
            )
        except Exception as exc:
            print(f"[{idx}/{total_count}] ERROR: {src.name} | {exc}", file=sys.stderr, flush=True)

    total_saved = total_before - total_after
    total_pct = (total_saved / total_before * 100.0) if total_before > 0 else 0.0
    print(
        f"[Squeezer] Completed: {format_bytes(total_before)} -> {format_bytes(total_after)} (Saved {format_bytes(total_saved)}, -{total_pct:.1f}%)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
