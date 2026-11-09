"""Step 1 of the SAT style reference: rasterise a spread of pages from the scanned
past-paper PDF to PNG. Run with the interpreter that has pymupdf (py -V:3.15 here):
    py -V:3.15 tools/render_sat_pages.py
Step 2 (tools/ocr_sat_sample.py) reads these PNGs and asks Gemini vision about them."""
import sys
from pathlib import Path

import pymupdf

SRC = Path(r"C:\Users\PC\Downloads\SAT English questions.pdf")
OUT = Path(__file__).resolve().parent.parent / "research" / "sat_pages"
PAGES = 20


def main():
    doc = pymupdf.open(SRC)
    n = doc.page_count
    step = max(1, n // PAGES)
    picks = sorted({min(n - 1, i * step) for i in range(PAGES)})
    OUT.mkdir(parents=True, exist_ok=True)
    for i in picks:
        dst = OUT / f"page{i + 1:03d}.png"
        if not dst.exists():
            doc[i].get_pixmap(dpi=110).save(dst)
        print(f"page {i + 1} -> {dst.name} {dst.stat().st_size // 1024} KB")
    print(f"{n} pages in source, {len(picks)} rendered into {OUT}")


if __name__ == "__main__":
    sys.exit(main())
