#!/usr/bin/env python3
"""
AD&D 2e PDF Compiler
────────────────────
Extracts text from a PDF using three independent OCR methods, then uses
Claude to compare them and produce clean, agent-readable markdown.

Usage:
    python3 compiler.py <pdf> [options]

Examples:
    python3 compiler.py phb.pdf
    python3 compiler.py phb.pdf -o phb_clean.md --pages 1-50
    python3 compiler.py phb.pdf --pages 12 --no-easyocr   # single page, faster
    python3 compiler.py phb.pdf --raw-only                 # skip Claude merge
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    # Minimal fallback if tqdm isn't installed yet
    def tqdm(it, **kwargs):
        total = kwargs.get("total", "?")
        desc = kwargs.get("desc", "")
        for i, item in enumerate(it):
            print(f"\r{desc}: {i+1}/{total}", end="", flush=True)
            yield item
        print()


def parse_args():
    p = argparse.ArgumentParser(
        description="AD&D 2e PDF → clean markdown via multi-OCR comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("pdf", help="Path to the input PDF file")
    p.add_argument(
        "-o", "--output",
        help="Output markdown file (default: <pdf_stem>_compiled.md)",
    )
    p.add_argument(
        "--pages",
        help="Page range to process, e.g. '1-50' or '12' (default: all pages)",
    )
    p.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI for rendering PDF pages to images (default: 300)",
    )
    p.add_argument(
        "--no-easyocr",
        action="store_true",
        help="Skip EasyOCR (faster; uses pdfplumber + Tesseract only)",
    )
    p.add_argument(
        "--raw-only",
        action="store_true",
        help="Save raw OCR outputs as JSON without Claude merging",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip pages whose output is already written to the output file",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel workers for image rendering (default: 1)",
    )
    p.add_argument(
        "--text-only",
        action="store_true",
        help="Use only pdfplumber (no image rendering, no Tesseract/EasyOCR). Fast.",
    )
    return p.parse_args()


def check_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print(
            "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-..."
        )
        sys.exit(1)


def parse_page_range(pages_str: str, total: int) -> list[int]:
    """Convert a page range string like '1-50' or '12' to 0-based indices."""
    if not pages_str:
        return list(range(total))
    parts = pages_str.strip().split("-")
    if len(parts) == 1:
        n = int(parts[0])
        return [n - 1]
    start, end = int(parts[0]) - 1, int(parts[1]) - 1
    return list(range(max(0, start), min(total, end + 1)))


def get_page_count(pdf_path: str) -> int:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def process_page(
    pdf_path: str,
    page_idx: int,
    dpi: int,
    use_easyocr: bool,
    text_only: bool = False,
) -> dict:
    """Run all extractors on a single page and return their outputs."""
    from extractors import extract_pdfplumber, extract_tesseract, extract_easyocr

    plumber = extract_pdfplumber(pdf_path, page_idx)
    empty   = {"text": "", "tables": []}

    if text_only:
        return {"page": page_idx + 1, "pdfplumber": plumber, "tesseract": empty, "easyocr": empty}

    tess = extract_tesseract(pdf_path, page_idx, dpi=dpi)
    easy = extract_easyocr(pdf_path, page_idx, dpi=min(dpi, 200)) if use_easyocr else empty

    return {
        "page": page_idx + 1,
        "pdfplumber": plumber,
        "tesseract": tess,
        "easyocr": easy,
    }


def compile_pdf(args):
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else pdf_path.with_name(pdf_path.stem + "_compiled.md")
    raw_path    = pdf_path.with_name(pdf_path.stem + "_raw.json")

    if not args.raw_only:
        check_api_key()

    print(f"PDF:    {pdf_path}")
    print(f"Output: {output_path}")

    total_pages = get_page_count(str(pdf_path))
    page_indices = parse_page_range(args.pages, total_pages)
    print(f"Pages:  {page_indices[0]+1}–{page_indices[-1]+1} ({len(page_indices)} pages)")
    mode = "pdfplumber only (text layer)" if args.text_only else \
           "pdfplumber + Tesseract" + (" + EasyOCR" if not args.no_easyocr else " (EasyOCR disabled)")
    print(f"Mode:   {mode}")
    print()

    # ── Load already-processed pages if resuming ──────────────────────────────
    existing_raw: dict[int, dict] = {}
    if args.resume and raw_path.exists():
        with open(raw_path) as f:
            for entry in json.load(f):
                existing_raw[entry["page"]] = entry
        print(f"Resuming: {len(existing_raw)} pages already extracted.")

    # ── Phase 1: OCR extraction ───────────────────────────────────────────────
    raw_results: list[dict] = []
    pages_to_extract = [i for i in page_indices if (i + 1) not in existing_raw]

    if pages_to_extract:
        print("Phase 1: Extracting text with three OCR engines...")
        if not args.text_only and not args.no_easyocr and len(pages_to_extract) > 0:
            print("  (Loading EasyOCR model — this takes ~30s on first run)")
        print()

        for page_idx in tqdm(pages_to_extract, desc="Extracting", unit="page"):
            result = process_page(
                str(pdf_path),
                page_idx,
                dpi=args.dpi,
                use_easyocr=not args.no_easyocr,
                text_only=args.text_only,
            )
            raw_results.append(result)

        # Merge with any resumed results and sort
        all_raw = list(existing_raw.values()) + raw_results
        all_raw.sort(key=lambda r: r["page"])

        with open(raw_path, "w") as f:
            json.dump(all_raw, f, indent=2, ensure_ascii=False)
        print(f"\nRaw OCR saved to: {raw_path}")
    else:
        all_raw = sorted(existing_raw.values(), key=lambda r: r["page"])
        print("All pages already extracted (using cached raw OCR).\n")

    if args.raw_only:
        print("--raw-only flag set. Skipping merge. Done.")
        return

    # ── Phase 2: Claude merge ─────────────────────────────────────────────────
    from merger import merge_page

    print("\nPhase 2: Merging with Claude (comparing OCR versions)...")
    print("  Prompt caching active — after the first page, cache hits will reduce cost.\n")

    total_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    compiled_pages: list[tuple[int, str]] = []

    # Load any already-merged pages if the output file exists
    already_merged: set[int] = set()
    if args.resume and output_path.exists():
        with open(output_path) as f:
            content = f.read()
        # Pages are delimited by sentinel comments
        for line in content.splitlines():
            if line.startswith("<!-- page "):
                try:
                    already_merged.add(int(line.split()[2]))
                except (IndexError, ValueError):
                    pass
        if already_merged:
            print(f"  Resuming merge: {len(already_merged)} pages already merged.\n")

    with open(output_path, "a" if args.resume else "w", encoding="utf-8") as out:
        if not args.resume:
            # Write document header
            out.write(f"# {pdf_path.stem}\n\n")
            out.write("> Compiled by AD&D 2e PDF Compiler  \n")
            out.write(f"> Source: `{pdf_path.name}`  \n")
            out.write(f"> Pages: {page_indices[0]+1}–{page_indices[-1]+1}\n\n")
            out.write("---\n\n")

        for entry in tqdm(all_raw, desc="Merging", unit="page"):
            page_num = entry["page"]
            if page_num in already_merged:
                continue

            try:
                merged_text, usage = merge_page(
                    page_num=page_num,
                    pdfplumber_result=entry["pdfplumber"],
                    tesseract_result=entry["tesseract"],
                    easyocr_result=entry["easyocr"],
                )
            except Exception as exc:
                print(f"\n  WARNING: Page {page_num} failed ({exc}). Writing raw text instead.")
                merged_text = entry["pdfplumber"].get("text", "(extraction failed)")
                usage = {"input_tokens": 0, "output_tokens": 0,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

            total_tokens["input"]       += usage["input_tokens"]
            total_tokens["output"]      += usage["output_tokens"]
            total_tokens["cache_read"]  += usage["cache_read_input_tokens"]
            total_tokens["cache_write"] += usage["cache_creation_input_tokens"]

            # Write page with sentinel for resume support
            out.write(f"<!-- page {page_num} -->\n\n")
            out.write(merged_text.strip())
            out.write("\n\n---\n\n")
            out.flush()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"Output written to: {output_path}")
    print(f"Token usage:")
    print(f"  Input:         {total_tokens['input']:>8,}")
    print(f"  Output:        {total_tokens['output']:>8,}")
    print(f"  Cache reads:   {total_tokens['cache_read']:>8,}  (billed at ~10% rate)")
    print(f"  Cache writes:  {total_tokens['cache_write']:>8,}")
    print(f"{'─'*50}")


if __name__ == "__main__":
    args = parse_args()
    compile_pdf(args)
