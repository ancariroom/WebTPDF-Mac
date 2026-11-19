#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
from statistics import median
from PIL import Image

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

def iter_image_files(input_dir):
    return sorted(
        p for p in Path(input_dir).iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )

def get_pro_range(starts, ends, total_len):
    """
    Finds all blocks that look like a page (1400-1670px height) 
    and picks the innermost one (closest to center).
    """
    if not starts or not ends: return None
    
    valid_blocks = []
    for s in starts:
        for e in ends:
            h = e - s
            if 1400 <= h <= 1670:
                valid_blocks.append((s, e))
    
    if not valid_blocks: return None
    
    # Priority: Inner-most (Max start, Min end)
    valid_blocks.sort(key=lambda b: (b[0], -b[1]), reverse=True)
    return valid_blocks[0]

def detect_page_bbox(img):
    l_img = img.convert("L")
    w, h = l_img.size
    pixels = l_img.load()

    # 1. Horizontal Search
    y_range = range(h // 4, h * 3 // 4, 10)
    h_profile = [sum(pixels[x, y] for y in y_range) // len(y_range) for x in range(w)]
    h_starts = []
    h_ends = []
    
    # Collect all starts/ends for X
    state = 0
    for i, v in enumerate(h_profile):
        if state == 0:
            if 240 <= v <= 245: state = 1
        elif state == 1:
            if v < 227: state = 2
        elif state == 2:
            if v >= 250:
                h_starts.append(i)
                state = 3
        elif state == 3:
            if v < 227:
                h_ends.append(i)
                state = 0
    
    # 2. Horizontal range (Width guard: 800-1200?)
    # For now, pick largest horizontal block
    h_res = None
    if h_starts and h_ends:
        cands = []
        for s in h_starts:
            for e in h_ends:
                if e > s and (e-s) > 800: cands.append((s, e))
        if cands:
            cands.sort(key=lambda b: (b[0], -b[1]), reverse=True)
            h_res = cands[0]
            
    if not h_res: h_res = (0, w)
    left, right = h_res

    # 3. Vertical Search (The critical one for double shadows)
    x_range = range(left + (right-left)//4, left + (right-left)*3//4, 10)
    v_profile = [sum(pixels[x, y] for x in x_range) // len(x_range) for y in range(h)]
    v_starts = []
    v_ends = []
    
    # Collect all shadow entries (starts) and exits (ends)
    # Entry: Background -> Shadow -> Page
    # Exit: Page -> Shadow -> Background
    cur_state = 0 
    for i, v in enumerate(v_profile):
        if cur_state == 0:
            if 240 <= v <= 245: cur_state = 1
        elif cur_state == 1:
            if v < 227: cur_state = 2
        elif cur_state == 2:
            if v >= 250:
                v_starts.append(i)
                cur_state = 0
    
    cur_state = 0
    for i, v in enumerate(v_profile):
        if cur_state == 0:
            if v >= 250: cur_state = 1
        elif cur_state == 1:
            if v < 227: cur_state = 2
        elif cur_state == 2:
            if 240 <= v <= 245:
                v_ends.append(i)
                cur_state = 0

    v_range = get_pro_range(v_starts, v_ends, h)
    if not v_range: return None
    
    top, bottom = v_range
    return left, top, right, bottom

def get_consensus_bbox(files, sample_limit):
    if not files: return None
    sample_files = files[::max(1, len(files)//sample_limit)][:sample_limit]
    boxes = []
    print(f"[INFO] Sampling {len(sample_files)} pages for consensus...")
    for path in sample_files:
        with Image.open(path) as img:
            res = detect_page_bbox(img)
            if res: boxes.append(res)
    
    if not boxes: return None
    return (
        int(median(box[0] for box in boxes)),
        int(median(box[1] for box in boxes)),
        int(median(box[2] for box in boxes)),
        int(median(box[3] for box in boxes))
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="input")
    parser.add_argument("--output", default="output")
    parser.add_argument("--threshold", type=int, default=240, help="Brightness threshold for shadow detection")
    parser.add_argument("--top-crop", type=int, default=98, help="Fixed pixels to crop from top (useful for toolbars)")
    parser.add_argument("--min-height", type=int, default=1400, help="Min height of detected page block")
    parser.add_argument("--max-height", type=int, default=1670, help="Max height of detected page block")
    parser.add_argument("--test-only", action="store_true")
    args = parser.parse_args()

    files = iter_image_files(args.input)
    if not files: return
    os.makedirs(args.output, exist_ok=True)

    # Viewer Standard v1.2: Fixed alignment based on consensus.
    # Individual page detection can jitter by 1-2px, which is visible in PDF.
    # Using the median (consensus) across 32 sample pages ensures perfect alignment.
    consensus_bbox = get_consensus_bbox(files, 32)
    if not consensus_bbox:
        print("[ERR] Could not determine consensus. Using defaults.")
        consensus_bbox = (890, 16, 1933, 1654)
    print(f"[INFO] consensus bbox: {consensus_bbox}")

    l, t, r, b = consensus_bbox
    # Margin settings: 3px for general padding, 5px for bottom to clear inner shadow artifacts
    l_off, r_off, b_off = 3, 3, 5

    target_files = files
    if args.test_only:
        target_files = [p for p in files if p.name.endswith(".png")][:10]

    for path in target_files:
        with Image.open(path) as img:
            # Fixed BBox: ct=98 crops the top toolbar (Browse/Annotate/Shape)
            # Individual edges use consensus to keep everything perfectly aligned.
            cl = max(0, l + l_off)
            ct = args.top_crop
            cr = min(img.size[0], r - r_off)
            cb = min(img.size[1], b - b_off)
            
            img.crop((cl, ct, cr, cb)).save(Path(args.output) / path.name)
            print(f"[OK] {path.name}")

if __name__ == "__main__":
    main()
