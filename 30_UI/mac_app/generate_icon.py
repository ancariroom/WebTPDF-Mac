#!/usr/bin/env python3
"""Generate a macOS AppIcon.iconset PNG set inspired by the PDF-download document badge."""

from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ICONSET_SPECS = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def load_bold_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a bold sans-serif font available on macOS."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=1 if path.endswith(".ttc") else 0)
            except Exception:
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
    return ImageFont.load_default()


def render_master_icon(size: int = 1024) -> Image.Image:
    """Render a 1024x1024 macOS-style squircle icon matching the PDF download motif."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer macOS rounded-square card
    pad = 72
    card_rect = [pad, pad, size - pad, size - pad]
    draw.rounded_rectangle(
        card_rect,
        radius=196,
        fill=(248, 249, 251, 255),
        outline=(214, 219, 226, 255),
        width=12,
    )

    ink = (24, 26, 31, 255)
    paper = (255, 255, 255, 255)
    accent_red = (212, 46, 46, 255)
    stroke = 34

    # Document outline with top-right folded corner
    doc_left, doc_top = 310, 175
    doc_right, doc_bottom = 815, 795
    fold = 155

    doc_poly = [
        (doc_left, doc_top),
        (doc_right - fold, doc_top),
        (doc_right, doc_top + fold),
        (doc_right, doc_bottom),
        (doc_left, doc_bottom),
    ]
    draw.polygon(doc_poly, fill=paper)
    draw.line(doc_poly + [doc_poly[0]], fill=ink, width=stroke, joint="curve")

    # Folded corner triangle
    fold_poly = [
        (doc_right - fold, doc_top),
        (doc_right - fold, doc_top + fold),
        (doc_right, doc_top + fold),
    ]
    draw.polygon(fold_poly, fill=(235, 238, 243, 255))
    draw.line(fold_poly + [fold_poly[0]], fill=ink, width=stroke, joint="curve")

    # Two horizontal content lines
    line_y1, line_y2 = 465, 550
    draw.rounded_rectangle([425, line_y1, 705, line_y1 + 34], radius=17, fill=ink)
    draw.rounded_rectangle([425, line_y2, 705, line_y2 + 34], radius=17, fill=ink)

    # Bold "PDF" badge on upper-left overlapping the document edge
    badge_rect = [155, 245, 515, 420]
    draw.rounded_rectangle(
        badge_rect,
        radius=36,
        fill=accent_red,
        outline=ink,
        width=stroke,
    )
    font = load_bold_font(122)
    bbox = draw.textbbox((0, 0), "PDF", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bx = (badge_rect[0] + badge_rect[2] - tw) / 2 - bbox[0]
    by = (badge_rect[1] + badge_rect[3] - th) / 2 - bbox[1]
    draw.text((bx, by), "PDF", font=font, fill=(255, 255, 255, 255))

    # Downward download arrow centered at bottom
    cx = 562
    shaft_w = 88
    shaft_top = 640
    shaft_bottom = 755
    head_half = 108
    tip_y = 895

    arrow_poly = [
        (cx - shaft_w // 2, shaft_top),
        (cx + shaft_w // 2, shaft_top),
        (cx + shaft_w // 2, shaft_bottom),
        (cx + head_half, shaft_bottom),
        (cx, tip_y),
        (cx - head_half, shaft_bottom),
        (cx - shaft_w // 2, shaft_bottom),
    ]
    draw.polygon(arrow_poly, fill=paper)
    draw.line(arrow_poly + [arrow_poly[0]], fill=ink, width=stroke, joint="curve")

    return img


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iconset-dir", required=True, help="Output .iconset directory")
    args = parser.parse_args()

    out_dir = Path(args.iconset_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    master = render_master_icon(1024)

    for filename, px in ICONSET_SPECS:
        resized = master.resize((px, px), Image.Resampling.LANCZOS)
        resized.save(out_dir / filename, "PNG")


if __name__ == "__main__":
    main()
