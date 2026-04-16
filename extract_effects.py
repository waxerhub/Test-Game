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
  "stat_str":          <int, net Strength BONUS or PENALTY granted TO the user by the item \
(e.g. "raises Strength by 3" → 3, "sets Strength to 18/00" is complex → note in notes). \
IMPORTANT: minimum required Strength to USE the item is NOT a bonus — put it in conditions instead \
(e.g. "requires Strength 13+" → stat_str=0, conditions="Requires minimum Strength 13+">, 0 if none>,
  "stat_dex":          <int, net Dexterity BONUS or PENALTY granted TO the user. \
Minimum Dex requirements go in conditions, not here. 0 if none>,
  "stat_con":          <int, net Constitution BONUS or PENALTY granted TO the user. \
Minimum Con requirements go in conditions, not here. 0 if none>,
  "stat_int":          <int, net Intelligence BONUS or PENALTY granted TO the user. \
Minimum Int requirements go in conditions, not here. 0 if none>,
  "stat_wis":          <int, net Wisdom BONUS or PENALTY granted TO the user. \
Minimum Wis requirements go in conditions, not here. 0 if none>,
  "stat_cha":          <int, net Charisma BONUS or PENALTY granted TO the user. \
Minimum Cha requirements go in conditions, not here. 0 if none>,
  "ac_bonus":          <int, a RELATIVE bonus added ON TOP of the wearer's existing armor — \
it STACKS with worn armor and applies regardless of what armor is worn. \
CORRECT: Ring of Protection +2 → 2; Cloak of Protection +1 → 1; \
"cloak +1 lowers AC 10 to AC 9" → ac_bonus=1 (the improvement is 1; AC 10 is the unarmored baseline, not the bonus). \
WRONG — do NOT set ac_bonus for OFFENSIVE effects on TARGETS/ENEMIES: \
"all creatures treated as AC 10 against this weapon" → ac_bonus=0, put in granted_abilities; \
"renders target AC 10" → ac_bonus=0, put in granted_abilities. \
For variable-tier items (e.g. cloak +1 to +5), use the LOWEST tier value (1). 0 if none>,
  "ac_set":            <int, REPLACES the wearer's BASE (unarmored) AC with a fixed value — \
the item functions AS ARMOR and therefore CANNOT be combined with worn armor \
(e.g. Bracers of Defense AC 0 → ac_set=0; Bracers of Defense AC 6 → ac_set=6; \
an amulet that grants AC as if wearing plate → ac_set=3). \
MUST be the integer -1 (never null, never "null", never 0, never "None") if this field does not apply. \
When ac_set is used, also add "Requires unarmored (no worn armor)" to conditions unless the text says otherwise>,
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
    ap.add_argument("--workers",   type=int, default=WORKERS)
    ap.add_argument("--checkpoint", type=int, default=50,
                    help="Save XLSX every N completed items (0 = disable)")
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
                    value = effects.get(col_name)
                    str_cols = ("granted_spells", "granted_abilities", "conditions", "notes")
                    # Coerce None / "null" / "None" to safe defaults
                    if value is None or value == "null" or value == "None":
                        value = -1 if col_name == "ac_set" else ("" if col_name in str_cols else 0)
                    # Ensure ac_set is always an int (never left as None/null)
                    if col_name == "ac_set" and not isinstance(value, int):
                        try:
                            value = int(value)
                        except (TypeError, ValueError):
                            value = -1
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

                if args.checkpoint and done[0] % args.checkpoint == 0:
                    wb.save(args.output)
                    print(f"\n  [checkpoint] saved at {done[0]}/{total}", flush=True)

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
