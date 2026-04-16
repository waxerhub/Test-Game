#!/usr/bin/env python3
"""
batch_submit.py — Submit all items to the Anthropic Batch API for effect extraction.

Reads encyclopedia_magica_vol1.xlsx, builds one batch request per item,
submits the batch, and saves the batch ID to batch_id.txt.

Usage:
    python3 batch_submit.py
    python3 batch_submit.py --input encyclopedia_magica_vol1.xlsx
"""

from __future__ import annotations
import argparse
import os

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
import openpyxl

from extract_effects import SYSTEM_PROMPT, EFFECT_COLS, MODEL, XLSX_PATH, col_index

BATCH_ID_FILE = "/home/user/Test-Game/batch_id.txt"


def build_user_msg(name: str, description: str, source: str) -> str:
    return (
        f"Item name: {name}\n"
        f"Source: {source}\n\n"
        f"Description:\n{description}\n\n"
        "Return the JSON effect object now."
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=XLSX_PATH)
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY not set")

    print(f"Loading {args.input} …")
    wb = openpyxl.load_workbook(args.input)
    ws = wb.active

    col_map = {h: col_index(ws, h) for h in ["name", "description", "source"]}

    requests = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        row_num = row[0].row  # Excel row number (e.g. 2 for first data row)
        name = str(row[col_map["name"] - 1].value or "").strip()
        desc = str(row[col_map["description"] - 1].value or "").strip()
        if not name or not desc:
            continue
        source = str(row[col_map["source"] - 1].value or "").strip()

        requests.append(Request(
            custom_id=f"row-{row_num}",
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=800,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_user_msg(name, desc, source)}],
            ),
        ))

    print(f"Submitting {len(requests)} requests to Batch API …")
    client = anthropic.Anthropic(api_key=api_key)
    batch = client.messages.batches.create(requests=requests)

    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch.id)

    print(f"Batch submitted!")
    print(f"  ID:     {batch.id}")
    print(f"  Status: {batch.processing_status}")
    print(f"  Saved to {BATCH_ID_FILE}")
    print(f"\nRun `python3 batch_collect.py` to poll for results.")


if __name__ == "__main__":
    main()
