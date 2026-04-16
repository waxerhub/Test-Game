"""
extractors.py — OCR extraction for the PDF compiler.

Primary extractor: Tesseract (renders page as image, ignores broken text layer).
Secondary:        pdfplumber (table structure detection only).
"""

from __future__ import annotations
from pathlib import Path


def extract_page(pdf_path: str, page_idx: int, dpi: int = 300) -> dict:
    """
    Extract one page using Tesseract OCR + pdfplumber table detection.

    Returns:
        {
          "page":   int,          # 1-based page number
          "text":   str,          # Tesseract OCR text
          "tables": list[list],   # structured table data from pdfplumber
        }
    """
    text   = _tesseract(pdf_path, page_idx, dpi)
    tables = _plumber_tables(pdf_path, page_idx)
    return {"page": page_idx + 1, "text": text, "tables": tables}


# ── Tesseract ─────────────────────────────────────────────────────────────

def _tesseract(pdf_path: str, page_idx: int, dpi: int) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=page_idx + 1,
        last_page=page_idx + 1,
    )
    if not images:
        return ""

    # psm 1 = automatic page segmentation with OSD (handles two-column layout)
    config = r"--oem 3 --psm 1"
    return pytesseract.image_to_string(images[0], config=config, lang="eng").strip()


# ── pdfplumber table detection ────────────────────────────────────────────

def _plumber_tables(pdf_path: str, page_idx: int) -> list:
    """
    Extract structured table data from the PDF's vector layer.
    Even when the text layer is broken, table borders are often intact.
    """
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_idx]
            tables = page.extract_tables({
                "vertical_strategy":   "lines_strict",
                "horizontal_strategy": "lines_strict",
                "snap_tolerance": 4,
                "join_tolerance": 4,
            })
            if not tables:
                tables = page.extract_tables({
                    "vertical_strategy":   "text",
                    "horizontal_strategy": "text",
                    "snap_tolerance": 6,
                })
            return tables or []
    except Exception:
        return []
