"""
extractors.py — Three independent OCR extraction methods.

Each extractor receives a PDF path and a page index (0-based) and returns:
  {
    "text": str,          # raw extracted text
    "tables": list[list]  # structured table data (pdfplumber only)
  }
"""

from __future__ import annotations
import numpy as np
from pathlib import Path
from typing import Optional


# ── pdfplumber ────────────────────────────────────────────────────────────────

def extract_pdfplumber(pdf_path: str, page_idx: int) -> dict:
    """
    Pull text and structured tables from the PDF's existing text layer.
    Best at preserving the original layout; great table detection.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_idx]

        # Detect tables and record their bounding boxes so we can
        # exclude those regions from the free-text extraction (avoids duplicates).
        tables_raw = page.extract_tables(
            table_settings={
                "vertical_strategy": "lines_strict",
                "horizontal_strategy": "lines_strict",
                "snap_tolerance": 4,
                "join_tolerance": 4,
                "edge_min_length": 10,
                "min_words_vertical": 2,
                "min_words_horizontal": 1,
            }
        )

        # Fallback: try with text strategy if line-based found nothing
        if not tables_raw:
            tables_raw = page.extract_tables(
                table_settings={
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                    "snap_tolerance": 6,
                }
            )

        text = page.extract_text(layout=True) or ""

        return {"text": text.strip(), "tables": tables_raw or []}


# ── Tesseract ─────────────────────────────────────────────────────────────────

def extract_tesseract(pdf_path: str, page_idx: int, dpi: int = 300) -> dict:
    """
    Render the page to an image and run Tesseract OCR.
    --psm 1  = automatic page segmentation with OSD (handles two-column layout).
    """
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=page_idx + 1,
        last_page=page_idx + 1,
    )
    if not images:
        return {"text": "", "tables": []}

    img = images[0]
    custom_config = r"--oem 3 --psm 1"
    text = pytesseract.image_to_string(img, config=custom_config, lang="eng")
    return {"text": text.strip(), "tables": []}


# ── EasyOCR ───────────────────────────────────────────────────────────────────

# Module-level reader so the model loads only once per process.
_easyocr_reader = None


def _get_reader():
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _easyocr_reader


def extract_easyocr(pdf_path: str, page_idx: int, dpi: int = 200) -> dict:
    """
    Render the page to an image and run EasyOCR.
    Results are sorted into reading order (top-to-bottom, with two-column
    awareness) before being joined into lines.
    """
    from pdf2image import convert_from_path

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=page_idx + 1,
        last_page=page_idx + 1,
    )
    if not images:
        return {"text": "", "tables": []}

    img = images[0]
    img_array = np.array(img)

    reader = _get_reader()
    # detail=1 returns (bbox, text, confidence)
    results = reader.readtext(img_array, detail=1, paragraph=False)

    if not results:
        return {"text": "", "tables": []}

    page_width = img.width

    def bbox_center(bbox):
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    # Annotate each result with (col, cy) for sorting
    annotated = []
    for bbox, text, conf in results:
        cx, cy = bbox_center(bbox)
        col = 0 if cx < page_width / 2 else 1
        annotated.append((col, cy, cx, text))

    # Sort: top-to-bottom within each column, columns left-to-right
    annotated.sort(key=lambda r: (r[0], r[1]))

    # Group into lines by proximity in Y within each column
    lines: list[str] = []
    current_col = None
    current_y = None
    current_line: list[str] = []
    Y_THRESHOLD = 15  # pixels — adjust if lines merge or split incorrectly

    for col, cy, cx, text in annotated:
        if current_col is None:
            current_col, current_y, current_line = col, cy, [text]
        elif col != current_col or abs(cy - current_y) > Y_THRESHOLD:
            if current_line:
                lines.append(" ".join(current_line))
            current_col, current_y, current_line = col, cy, [text]
        else:
            current_line.append(text)
            current_y = (current_y + cy) / 2  # rolling average

    if current_line:
        lines.append(" ".join(current_line))

    return {"text": "\n".join(lines), "tables": []}
