import argparse
from PIL import Image
import os
from pathlib import Path

def numeric_sort_key(path_str):
    path = Path(path_str)
    stem = path.stem
    if stem.startswith("page_"):
        try:
            return int(stem.split("_", 1)[1])
        except ValueError:
            pass
    return stem

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="output", help="Directory of cropped images")
    parser.add_argument("--name", default="book.pdf", help="Output PDF filename")
    args = parser.parse_args()
    
    files = sorted(
        [os.path.join(args.input, f) for f in os.listdir(args.input) if f.endswith('.png')],
        key=numeric_sort_key,
    )
    
    if not files:
        print("\n[ERROR] No images found in input directory")
        return

    print(f"Starting PDF generation for {len(files)} pages...")
    
    image_list = []
    try:
        for i, fpath in enumerate(files):
            img = Image.open(fpath).convert('RGB')
            image_list.append(img)
            if (i + 1) % 50 == 0:
                print(f"  [+] Loaded {i + 1} pages...")
        
        if image_list:
            print(f"Saving to {args.name}...")
            image_list[0].save(
                args.name, 
                "PDF", 
                save_all=True, 
                append_images=image_list[1:]
            )
    finally:
        for img in image_list:
            img.close()
            
    print(f"\n[DONE] Saved to {args.name}")

if __name__ == "__main__":
    main()
