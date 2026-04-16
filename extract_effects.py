#!/usr/bin/env python3
"""
extract_effects.py — Use Claude to extract structured effect data from each
item description in the compiled XLSX, then write an updated workbook.

Usage:
    python3 extract_effects.py                    # full run
    python3 extract_effects.py --sample 5         # test on first N items
    python3 extract_effects.py --resume           # skip rows already filled
"""

from __future__ import annotations
import argparse
import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

XLSX_PATH = "/home/user/Test-Game/encyclopedia_magica_vol1.xlsx"
OUT_PATH  = "/home/user/Test-Game/encyclopedia_magica_vol1.xlsx"
MODEL     = "claude-sonnet-4-6"
WORKERS   = 3

SYSTEM_PROMPT = """\
You are a precise AD&D 2nd Edition rules parser. You will receive the name and \
full description of a magic item and must return a JSON object with structured \
effect data. Be exact — only extract values that are explicitly stated. \
Never invent or infer values not present in the text.

Return ONLY valid JSON, no prose, no markdown code fences.

JSON schema (all fields required):
{
  "stat_str":          <int, net Strength bonus/penalty, 0 if none>,
  "stat_dex":          <int, net Dexterity bonus/penalty, 0 if none>,
  "stat_con":          <int, net Constitution bonus/penalty, 0 if none>,
  "stat_int":          <int, net Intelligence bonus/penalty, 0 if none>,
  "stat_wis":          <int, net Wisdom bonus/penalty, 0 if none>,
  "stat_cha":          <int, net Charisma bonus/penalty, 0 if none>,
  "ac_bonus":          <int, RELATIVE AC improvement added to existing AC (positive = better, \
e.g. a +2 AC item makes AC 10 → AC 8, so this is 2), 0 if none>,
  "ac_set":            <int, ABSOLUTE AC value the item sets base AC to, overriding armor \
(e.g. Bracers of Defense AC 0 → use 0, Bracers AC 6 → use 6), -1 if not applicable. \
IMPORTANT: only use this when the item explicitly sets a base AC value, not when it adds a bonus>,
  "thac0_bonus":       <int, THAC0 improvement (positive = lower THAC0), 0 if none>,
  "save_bonus":        <int, saving throw bonus/penalty, 0 if none>,
  "hp_bonus":          <int, hit point bonus, 0 if none>,
  "movement_bonus":    <int, movement rate change, 0 if none>,
  "granted_spells":    <string, semicolon-separated list of "Spell Name (frequency)" — \
e.g. "Cure Light Wounds (1/day); Fireball (2/week)"; empty string if none>,
  "granted_abilities": <string, semicolon-separated non-spell abilities — \
e.g. "Infravision 60ft; Telepathy with owner"; empty string if none>,
  "conditions":        <string, important restrictions or situational limits — \
e.g. "Save bonus applies only vs. poison and disease; exhaustion check after each spell use"; \
empty string if none>,
  "charges":           <int, number of charges (-1 = unlimited/passive/continuous, \
0 = N/A or unknown, positive integer = specific charge count)>,
  "notes":             <string, anything mechanically significant that doesn't fit \
above columns — multi-tier tables, unusual interactions, alignment effects, etc.; \
empty string if none>,
  "needs_review":      <bool, true ONLY if text is garbled/incomplete/contradictory \
and you cannot confidently extract effects>
}

Rules:
- For conditional save bonuses (e.g. "+3 vs poison only"), put the bonus in \
  save_bonus and explain the condition in conditions.
- For continuous spell-like auras (e.g. "continuously radiates protection from evil"), \
  put "Protection from Evil 10-foot radius (continuous)" in granted_spells.
- For reaction roll bonuses, put them in granted_abilities \
  (e.g. "Reaction roll bonus +3").
- For alignment shifts, healing rate doublers, aging protection, etc., \
  use granted_abilities or notes.
- charges: use -1 for passive/always-on items, specific number if stated, \
  0 if item has no charges and is not passive (e.g. one-time use artifacts).
"""


def extract_effects_for_item(client: anthropic.Anthropic, name: str,
                              description: str, source: str) -> dict:
    """Call Claude to extract structured effects. Returns parsed JSON dict."""
    user_msg = (
        f"Item name: {name}\n"
        f"Source: {source}\n\n"
        f"Description:\n{description}\n\n"
        "Return the JSON effect object now."
    )

    for attempt in range(6):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=800,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            # Strip accidental markdown fences
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            return json.loads(raw)
        except anthropic.RateLimitError:
            wait = 15 * (2 ** attempt)
            print(f"\n  Rate limit [{name[:30]}], retry in {wait}s…", flush=True)
            time.sleep(wait)
        except json.JSONDecodeError as e:
            print(f"\n  JSON parse error [{name[:30]}]: {e}", flush=True)
            return _empty_effects(needs_review=True)

    return _empty_effects(needs_review=True)


def _empty_effects(needs_review=False) -> dict:
    return {
        "stat_str": 0, "stat_dex": 0, "stat_con": 0,
        "stat_int": 0, "stat_wis": 0, "stat_cha": 0,
        "ac_bonus": 0, "ac_set": -1, "thac0_bonus": 0, "save_bonus": 0,
        "hp_bonus": 0, "movement_bonus": 0,
        "granted_spells": "", "granted_abilities": "",
        "conditions": "", "charges": 0, "notes": "",
        "needs_review": needs_review,
    }


EFFECT_COLS = [
    "stat_str", "stat_dex", "stat_con", "stat_int", "stat_wis", "stat_cha",
    "ac_bonus", "ac_set", "thac0_bonus", "save_bonus", "hp_bonus", "movement_bonus",
    "granted_spells", "granted_abilities", "conditions",
    "charges", "notes", "needs_review",
]


def col_index(ws, header: str) -> int | None:
    """Return 1-based column index for a header name."""
    for cell in ws[1]:
        if cell.value == header:
            return cell.column
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   default=XLSX_PATH)
    ap.add_argument("--output",  default=OUT_PATH)
    ap.add_argument("--sample",  type=int, default=0,
                    help="Only process first N data rows (0 = all)")
    ap.add_argument("--resume",  action="store_true",
                    help="Skip rows where granted_spells is already filled")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: ANTHROPIC_API_KEY not set")

    print(f"Loading {args.input} …")
    wb = openpyxl.load_workbook(args.input)
    ws = wb.active

    # Build column index map
    col_map = {h: col_index(ws, h) for h in
               ["name", "description", "source", "needs_review"] + EFFECT_COLS}

    # Gather rows to process
    all_rows = list(ws.iter_rows(min_row=2, values_only=False))
    if args.sample:
        all_rows = all_rows[:args.sample]

    tasks = []
    for row in all_rows:
        name_cell  = row[col_map["name"] - 1]
        desc_cell  = row[col_map["description"] - 1]
        spells_cell = row[col_map["granted_spells"] - 1]

        name = str(name_cell.value or "").strip()
        desc = str(desc_cell.value or "").strip()

        if not name or not desc:
            continue
        if args.resume and spells_cell.value:
            continue

        src_cell = row[col_map["source"] - 1]
        source   = str(src_cell.value or "").strip()
        tasks.append((row, name, desc, source))

    print(f"Processing {len(tasks)} items with {args.workers} workers …\n")

    client   = anthropic.Anthropic(api_key=api_key)
    lock     = threading.Lock()
    done     = [0]
    total    = len(tasks)
    review_fill  = PatternFill("solid", fgColor="FFF2CC")
    normal_fill  = PatternFill(fill_type=None)

    def process(task):
        row, name, desc, source = task
        effects = extract_effects_for_item(client, name, desc, source)
        return row, effects

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, t): t for t in tasks}
        for fut in as_completed(futures):
            row, effects = fut.result()
            with lock:
                done[0] += 1
                # Write effect values back into the row
                for col_name in EFFECT_COLS:
                    col_idx = col_map.get(col_name)
                    if col_idx is None:
                        continue
                    cell  = row[col_idx - 1]
                    value = effects.get(col_name, "" if isinstance(effects.get(col_name), str) else 0)
                    cell.value = value
                    cell.alignment = Alignment(vertical="top", wrap_text=False)

                # Highlight review rows
                nr = effects.get("needs_review", False)
                fill = review_fill if nr else normal_fill
                for cell in row:
                    cell.fill = fill

                pct = done[0] * 100 // total
                print(f"\r  {done[0]}/{total} ({pct}%)  latest: {futures[fut][1][:40]}",
                      end="", flush=True)

    print(f"\n\nSaving {args.output} …")
    wb.save(args.output)

    # Summary
    n_review = sum(
        1 for row in ws.iter_rows(min_row=2, values_only=True)
        if row[col_map["needs_review"] - 1]
    )
    print(f"Done → {args.output}")
    print(f"Items with needs_review=True: {n_review}/{total}")


if __name__ == "__main__":
    main()
