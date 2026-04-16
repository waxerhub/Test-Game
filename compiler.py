#!/usr/bin/env python3
"""
AD&D 2e PDF Compiler — Tesseract + Claude pipeline
────────────────────────────────────────────────────
Phase 1: Render each PDF page as an image and OCR it with Tesseract.
         Progress is saved after every page so interruptions are safe.
Phase 2: Send each page's OCR text to Claude for cleanup and formatting.
         Progress is also saved page-by-page; use --resume to continue.

Usage:
    python3 compiler.py <pdf>
    python3 compiler.py <pdf> --pages 1-50
    python3 compiler.py <pdf> --resume          # continue after interruption
    python3 compiler.py <pdf> --ocr-only        # Phase 1 only (no API needed)
    python3 compiler.py <pdf> --dpi 200         # faster, slightly lower quality
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw):
        total = kw.get("total", "?")
        desc  = kw.get("desc",  "")
        for i, item in enumerate(it):
            print(f"\r{desc} {i+1}/{total}", end="", flush=True)
            yield item
        print()


# ── Argument parsing ───────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="AD&D 2e PDF → clean markdown via Tesseract + Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("pdf",  help="Path to the input PDF")
    p.add_argument("-o", "--output", help="Output .md file (default: <pdf_stem>_compiled.md)")
    p.add_argument("--pages",    help="Page range, e.g. '1-50' or '12'")
    p.add_argument("--dpi",      type=int, default=300,
                   help="Render DPI (default 300; use 200 for speed)")
    p.add_argument("--resume",   action="store_true",
                   help="Resume a previous run (skip already-processed pages)")
    p.add_argument("--ocr-only", action="store_true",
                   help="Run Phase 1 only — extract and cache OCR, no Claude calls")
    p.add_argument("--workers",  type=int, default=5,
                   help="Parallel Claude workers for Phase 2 (default 5)")
    return p.parse_args()


# ── Helpers ────────────────────────────────────────────────────────────────

def require_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "Export it before running:\n  export ANTHROPIC_API_KEY=sk-..."
        )


def get_page_count(pdf_path: str) -> int:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def parse_page_range(spec: str, total: int) -> list[int]:
    if not spec:
        return list(range(total))
    parts = spec.strip().split("-")
    if len(parts) == 1:
        return [int(parts[0]) - 1]
    s, e = int(parts[0]) - 1, int(parts[1]) - 1
    return list(range(max(0, s), min(total, e + 1)))


def load_cache(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    with open(path) as f:
        return {e["page"]: e for e in json.load(f)}


def save_cache(path: Path, cache: dict[int, dict]):
    with open(path, "w") as f:
        json.dump(sorted(cache.values(), key=lambda e: e["page"]),
                  f, indent=2, ensure_ascii=False)


def already_merged_pages(output_path: Path) -> set[int]:
    """Read sentinel comments from a partial output file."""
    if not output_path.exists():
        return set()
    done: set[int] = set()
    with open(output_path) as f:
        for line in f:
            m = re.match(r"<!-- page (\d+) -->", line)
            if m:
                done.add(int(m.group(1)))
    return done


# ── Phase 1: OCR extraction ────────────────────────────────────────────────

def run_phase1(pdf_path: str, page_indices: list[int], dpi: int,
               cache: dict[int, dict], cache_path: Path) -> dict[int, dict]:
    from extractors import extract_page

    todo = [i for i in page_indices if (i + 1) not in cache]
    if not todo:
        print("Phase 1: all pages already extracted (cached).\n")
        return cache

    print(f"Phase 1: OCR with Tesseract ({len(todo)} pages to extract)…\n")
    for page_idx in tqdm(todo, desc="Extracting", unit="page"):
        result = extract_page(pdf_path, page_idx, dpi=dpi)
        cache[result["page"]] = result
        save_cache(cache_path, cache)   # save after every page

    print(f"\nOCR cache saved → {cache_path}\n")
    return cache


# ── Phase 2: Claude merge ──────────────────────────────────────────────────

def _merge_one(page_idx: int, cache: dict[int, dict]) -> tuple[int, str, dict]:
    """Worker: call Claude for one page. Returns (page_num, markdown, usage)."""
    from merger import merge_page
    page_num = page_idx + 1
    entry    = cache.get(page_num, {})
    text     = entry.get("text",   "")
    tables   = entry.get("tables", [])
    try:
        merged, usage = merge_page(page_num, text, tables)
    except Exception as exc:
        print(f"\n  WARNING page {page_num}: {exc}", flush=True)
        merged = text
        usage  = dict(input_tokens=0, output_tokens=0,
                      cache_read_input_tokens=0,
                      cache_creation_input_tokens=0)
    return page_num, merged, usage


def run_phase2(cache: dict[int, dict], page_indices: list[int],
               output_path: Path, pdf_stem: str, resume: bool,
               workers: int = 5):
    done = already_merged_pages(output_path) if resume else set()
    todo = [i for i in page_indices if (i + 1) not in done]

    if not todo:
        print("Phase 2: all pages already merged.\n")
        return

    print(f"Phase 2: Claude cleanup ({len(todo)} pages, {workers} parallel workers)…")
    print("  Prompt caching active — cost drops significantly after page 1.\n")

    totals     = dict(input=0, output=0, cache_read=0, cache_write=0)
    buffer     = {}          # page_num → (merged, usage) for out-of-order arrivals
    write_lock = threading.Lock()
    next_write = [sorted(todo)[0] + 1]   # next page_num we must write

    mode = "a" if (resume and output_path.exists()) else "w"
    out_file = open(output_path, mode, encoding="utf-8")
    if mode == "w":
        out_file.write(f"# {pdf_stem}\n\n")
        out_file.write(f"> Compiled from `{pdf_stem}.pdf` — {len(page_indices)} pages\n\n")
        out_file.write("---\n\n")

    sorted_page_nums = [i + 1 for i in sorted(todo)]

    def flush_buffer():
        """Write consecutive pages from buffer in order, flushing after each."""
        while next_write[0] in buffer:
            page_num = next_write[0]
            merged, usage = buffer.pop(page_num)
            totals["input"]       += usage["input_tokens"]
            totals["output"]      += usage["output_tokens"]
            totals["cache_read"]  += usage["cache_read_input_tokens"]
            totals["cache_write"] += usage["cache_creation_input_tokens"]
            out_file.write(f"<!-- page {page_num} -->\n\n")
            out_file.write(merged.strip() + "\n\n---\n\n")
            out_file.flush()
            # Advance to next expected page
            idx = sorted_page_nums.index(page_num)
            next_write[0] = sorted_page_nums[idx + 1] if idx + 1 < len(sorted_page_nums) else -1

    # Process pages in parallel, writing each to disk as soon as its turn arrives
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_merge_one, idx, cache): idx for idx in todo}
        for fut in tqdm(as_completed(futures), total=len(todo),
                        desc="Merging", unit="page"):
            page_num, merged, usage = fut.result()
            with write_lock:
                buffer[page_num] = (merged, usage)
                flush_buffer()

    out_file.close()

    print(f"\n{'─'*48}")
    print(f"Output → {output_path}")
    print(f"Tokens:  input {totals['input']:,}  output {totals['output']:,}")
    print(f"Cache:   reads {totals['cache_read']:,}  writes {totals['cache_write']:,}")
    print(f"{'─'*48}\n")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    pdf_path    = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"ERROR: {pdf_path} not found")

    pdf_stem    = pdf_path.stem
    output_path = Path(args.output) if args.output else \
                  pdf_path.with_name(pdf_stem + "_compiled.md")
    cache_path  = pdf_path.with_name(pdf_stem + "_ocr_cache.json")

    if not args.ocr_only:
        require_api_key()

    total_pages  = get_page_count(str(pdf_path))
    page_indices = parse_page_range(args.pages, total_pages)

    print(f"PDF:    {pdf_path.name}")
    print(f"Pages:  {page_indices[0]+1}–{page_indices[-1]+1}  ({len(page_indices)} pages)")
    print(f"Output: {output_path.name}")
    print(f"DPI:    {args.dpi}")
    print()

    # ── Phase 1 ──
    cache = load_cache(cache_path)
    if cache:
        print(f"  Cache: {len(cache)} pages already extracted.")
    cache = run_phase1(str(pdf_path), page_indices, args.dpi, cache, cache_path)

    if args.ocr_only:
        print("--ocr-only: stopping after Phase 1.")
        return

    # ── Phase 2 ──
    run_phase2(cache, page_indices, output_path, pdf_stem, args.resume,
               workers=args.workers)


if __name__ == "__main__":
    main()
