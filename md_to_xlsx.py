#!/usr/bin/env python3
"""
md_to_xlsx.py — Parse Encyclopedia Magica compiled markdown → XLSX workbook.

Usage:
    python3 md_to_xlsx.py                          # full run
    python3 md_to_xlsx.py --sample                 # 5-item preview only
    python3 md_to_xlsx.py --input foo.md --output bar.xlsx
"""

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_INPUT  = "/home/user/Test-Game/tsr02141 Encyclopedia Magica Volume One_compiled.md"
DEFAULT_OUTPUT = "/home/user/Test-Game/encyclopedia_magica_vol1.xlsx"

FRONT_MATTER = {
    "Credits", "Table of Contents", "Design Notes",
    "Navigating the Encyclopedia", "ADVANCED DUNGEONS & DRAGONS Game",
    "DRAGONLANCE ADVENTURES", "Artifact Tables",
}

SLOT_KEYWORDS = {
    "ring":      "Ring",
    "amulet":    "Neck",
    "necklace":  "Neck",
    "pendant":   "Neck",
    "periapt":   "Neck",
    "talisman":  "Neck",
    "brooch":    "Neck",
    "medallion": "Neck",
    "cloak":     "Back",
    "robe":      "Body",
    "armor":     "Body",
    "shield":    "Off-hand",
    "bracers":   "Wrist",
    "bracelet":  "Wrist",
    "gauntlet":  "Hands",
    "gloves":    "Hands",
    "boots":     "Feet",
    "belt":      "Waist",
    "girdle":    "Waist",
    "helm":      "Head",
    "hat":       "Head",
    "headband":  "Head",
    "staff":     "Wielded",
    "wand":      "Wielded",
    "rod":       "Wielded",
    "sword":     "Wielded",
    "axe":       "Wielded",
    "bow":       "Wielded",
    "arrow":     "Wielded",
    "dagger":    "Wielded",
    "spear":     "Wielded",
    "mace":      "Wielded",
    "hammer":    "Wielded",
    "flail":     "Wielded",
    "bag":       "Carried",
    "sack":      "Carried",
    "pouch":     "Carried",
    "jar":       "Carried",
    "bottle":    "Carried",
    "flask":     "Carried",
    "dust":      "Carried",
    "scroll":    "Carried",
    "tome":      "Carried",
    "book":      "Carried",
    "cube":      "Carried",
    "figurine":  "Carried",
    "stone":     "Carried",
    "gem":       "Carried",
    "orb":       "Carried",
    "crystal":   "Carried",
    "candle":    "Carried",
    "horn":      "Carried",
    "pipes":     "Carried",
    "drum":      "Carried",
    "lyre":      "Carried",
    "harp":      "Carried",
    "cloak":     "Back",
}

CLASS_KEYWORDS = {
    "Fighter": ["fighter", "warrior", "paladin", "ranger", "barbarian"],
    "Mage":    ["mage", "wizard", "magic-user", "illusionist", "sorcerer"],
    "Cleric":  ["cleric", "priest", "druid", "shaman"],
    "Thief":   ["thief", "rogue", "bard", "assassin"],
    "Druid":   ["druid"],
    "Ranger":  ["ranger"],
    "Paladin": ["paladin"],
    "Bard":    ["bard"],
}

# ── Parsing helpers ────────────────────────────────────────────────────────

def parse_int(s: str) -> int:
    """Parse '1,500' → 1500, '—' → 0, '+3' → 3."""
    s = s.strip().replace(",", "").replace("+", "")
    if s in ("—", "-", "", "?", "N/A"):
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def parse_xp_gp_line(line: str) -> tuple[int, int] | None:
    """Extract (xp, gp) from '**XP Value:** 1,500 &emsp; **GP Value:** 7,500'"""
    xp_m = re.search(r"XP\s*Value[^0-9—\-]*([0-9,]+|—|-)", line)
    gp_m = re.search(r"GP\s*Value[^0-9—\-]*([0-9,]+|—|-)", line)
    if xp_m and gp_m:
        return parse_int(xp_m.group(1)), parse_int(gp_m.group(1))
    return None


def parse_table_tiers(lines: list[str]) -> tuple[int, int, str] | None:
    """
    Parse a markdown bonus table like:
        | Bonus | XP Value | GP Value |
        |-------|----------|----------|
        | +3    | 1,500    | 8,000    |
    Returns (min_xp, min_gp, table_text) or None.
    """
    # Find header row with XP and GP
    header_idx = None
    for i, l in enumerate(lines):
        if re.search(r"XP\s*Value", l, re.I) and re.search(r"GP\s*Value", l, re.I):
            header_idx = i
            break
    if header_idx is None:
        return None

    table_lines = []
    xp_vals, gp_vals = [], []
    xp_col, gp_col = None, None
    # Header + separator + data rows
    for l in lines[header_idx:]:
        if not l.strip().startswith("|"):
            break
        table_lines.append(l.strip())
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if re.match(r"[-:]+", cells[0]):  # separator row
            continue
        if re.search(r"XP\s*Value", cells[0], re.I):  # header row
            headers = [c.lower() for c in cells]
            xp_col = next((i for i, h in enumerate(headers) if "xp" in h), None)
            gp_col = next((i for i, h in enumerate(headers) if "gp" in h), None)
            continue
        if xp_col is not None and gp_col is not None:
            if len(cells) > max(xp_col, gp_col):
                xp_vals.append(parse_int(cells[xp_col]))
                gp_vals.append(parse_int(cells[gp_col]))

    if not xp_vals:
        return None

    table_text = "\n".join(table_lines)
    xp_nonzero = [v for v in xp_vals if v > 0]
    gp_nonzero = [v for v in gp_vals if v > 0]
    return (min(xp_nonzero) if xp_nonzero else 0,
            min(gp_nonzero) if gp_nonzero else 0,
            table_text)


def infer_slot(name: str, desc: str) -> str:
    combined = (name + " " + desc[:200]).lower()
    for kw, slot in SLOT_KEYWORDS.items():
        if kw in combined:
            return slot
    return "N/A"


def infer_classes(desc: str) -> str:
    desc_lower = desc.lower()
    found = []
    for cls, kws in CLASS_KEYWORDS.items():
        if any(kw in desc_lower for kw in kws):
            if cls not in found:
                found.append(cls)
    return ",".join(found) if found else "All"


def extract_stat_bonuses(desc: str) -> dict:
    """Extract numeric stat/effect bonuses from description text."""
    result = {
        "stat_str": 0, "stat_dex": 0, "stat_con": 0,
        "stat_int": 0, "stat_wis": 0, "stat_cha": 0,
        "ac_bonus": 0, "thac0_bonus": 0, "save_bonus": 0,
        "hp_bonus": 0, "movement_bonus": 0,
    }
    patterns = [
        (r"[Ss]trength\s+(?:by\s+)?([+-]?\d+)", "stat_str"),
        (r"[Ss]tr\s+(?:by\s+)?([+-]?\d+)",       "stat_str"),
        (r"[Dd]exterity\s+(?:by\s+)?([+-]?\d+)", "stat_dex"),
        (r"[Dd]ex\s+(?:by\s+)?([+-]?\d+)",       "stat_dex"),
        (r"[Cc]onstitution\s+(?:by\s+)?([+-]?\d+)", "stat_con"),
        (r"[Cc]on\s+(?:by\s+)?([+-]?\d+)",       "stat_con"),
        (r"[Ii]ntelligence\s+(?:by\s+)?([+-]?\d+)", "stat_int"),
        (r"[Ii]nt\s+(?:by\s+)?([+-]?\d+)",       "stat_int"),
        (r"[Ww]isdom\s+(?:by\s+)?([+-]?\d+)",    "stat_wis"),
        (r"[Ww]is\s+(?:by\s+)?([+-]?\d+)",       "stat_wis"),
        (r"[Cc]harisma\s+(?:by\s+)?([+-]?\d+)",  "stat_cha"),
        (r"[Cc]ha\s+(?:by\s+)?([+-]?\d+)",       "stat_cha"),
        (r"[Aa]rmor\s+[Cc]lass\s+(?:of\s+)?([+-]?\d+|AC\s*\d+)", "ac_bonus"),
        (r"\bAC\s*([+-]?\d+)",                   "ac_bonus"),
        (r"THAC0\s+(?:by\s+)?([+-]?\d+)",        "thac0_bonus"),
        (r"saving\s+throw[s]?\s+(?:by\s+)?([+-]?\d+)", "save_bonus"),
        (r"save[s]?\s+(?:by\s+)?([+-]?\d+)",     "save_bonus"),
        (r"([+-]?\d+)\s+hit\s+point[s]?",        "hp_bonus"),
        (r"movement\s+(?:rate\s+)?(?:by\s+|increases?\s+by\s+)?([+-]?\d+)", "movement_bonus"),
        (r"MV\s+([+-]?\d+)",                     "movement_bonus"),
    ]
    for pat, key in patterns:
        m = re.search(pat, desc)
        if m:
            try:
                result[key] = int(m.group(1).replace("+", ""))
            except ValueError:
                pass
    return result


def extract_spells(desc: str) -> str:
    """Extract granted spells like 'cast fireball (1/day)'."""
    spells = []
    for m in re.finditer(
        r"(?:cast|use|casts?|invoke[s]?)\s+([\w '\-]+?)\s*\((\d+/(?:day|week|month|round|turn|year))\)",
        desc, re.I
    ):
        spells.append(f"{m.group(1).strip()} {m.group(2)}")
    return "; ".join(spells)


def extract_charges(desc: str) -> int:
    m = re.search(r"(\d+)\s+charges?", desc, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"unlimited|permanent|continuous|always|constantly", desc, re.I):
        return -1
    return 0


def is_garbled(text: str) -> bool:
    if not text or len(text) < 20:
        return True
    # High ratio of short space-separated tokens
    tokens = text.split()
    if len(tokens) < 3:
        return True
    single = sum(1 for t in tokens if len(t) == 1)
    if len(tokens) > 0 and single / len(tokens) > 0.4:
        return True
    return False


# ── Item extraction ────────────────────────────────────────────────────────

def extract_items(md_text: str) -> list[dict]:
    lines = md_text.splitlines()
    items = []
    skipped_headings = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Only process ## headings
        if not line.startswith("## "):
            i += 1
            continue

        name = line[3:].strip().rstrip("*").strip()

        # Skip front matter
        if name in FRONT_MATTER or name.startswith("Table "):
            skipped_headings.append(name)
            i += 1
            continue

        # Look ahead up to 15 lines for XP/GP or a tier table
        lookahead = lines[i+1 : i+16]
        has_xp_gp = any(
            parse_xp_gp_line(l) or
            (re.search(r"XP\s*Value", l, re.I) and re.search(r"GP\s*Value", l, re.I))
            for l in lookahead
        )

        if not has_xp_gp:
            skipped_headings.append(name)
            i += 1
            continue

        # Gather all lines until next ## heading (or end)
        block_lines = []
        j = i + 1
        while j < len(lines) and not lines[j].startswith("## "):
            block_lines.append(lines[j])
            j += 1

        block = "\n".join(block_lines)
        item = parse_item(name, block_lines, block)
        items.append(item)
        i = j  # jump to next heading

    return items, skipped_headings


def parse_item(name: str, block_lines: list[str], block: str) -> dict:
    item = {
        "name": name,
        "xp_value": 0,
        "value_gp": 0,
        "source": "",
        "classes_usable": "All",
        "slot": "",
        "description": "",
        # effect columns
        "stat_str": 0, "stat_dex": 0, "stat_con": 0,
        "stat_int": 0, "stat_wis": 0, "stat_cha": 0,
        "ac_bonus": 0, "thac0_bonus": 0, "save_bonus": 0,
        "hp_bonus": 0, "movement_bonus": 0,
        "granted_spells": "",
        "granted_abilities": "",
        "conditions": "",
        "charges": 0,
        "notes": "",
        "needs_review": False,
    }

    needs_review = False
    is_multi_tier = False
    desc_lines = []

    # --- XP/GP: try inline line first, then tier table ---
    xp_gp_found = False
    for idx, l in enumerate(block_lines):
        vals = parse_xp_gp_line(l)
        if vals:
            item["xp_value"], item["value_gp"] = vals
            xp_gp_found = True
            break
        # Multi-tier table?
        if re.search(r"XP\s*Value", l, re.I) and re.search(r"GP\s*Value", l, re.I) and l.strip().startswith("|"):
            tier = parse_table_tiers(block_lines[max(0, idx-1):idx+20])
            if tier:
                item["xp_value"], item["value_gp"], tier_text = tier
                item["notes"] = f"TIER TABLE:\n{tier_text}"
                is_multi_tier = True
                xp_gp_found = True
            break

    if not xp_gp_found:
        needs_review = True

    # --- Source ---
    for l in block_lines:
        m = re.match(r"\*(?:Source:?\s*)?(.*?)\*$", l.strip())
        if m and len(m.group(1)) < 120:
            item["source"] = m.group(1).strip()
            break

    # --- Description: all non-metadata lines ---
    skip_patterns = [
        r"^\*\*XP\s*Value",
        r"^\*\*GP\s*Value",
        r"^\*Source",
        r"^\*[A-Z][^*]{0,100}\*$",   # standalone italic source line
        r"^<!--",
        r"^---",
        r"^\|",                        # table rows
        r"^#",
    ]
    for l in block_lines:
        stripped = l.strip()
        if not stripped:
            continue
        if any(re.match(p, stripped) for p in skip_patterns):
            continue
        desc_lines.append(stripped)

    desc = " ".join(desc_lines).strip()
    # Clean up HTML entities
    desc = desc.replace("&emsp;", " ").replace("&nbsp;", " ")
    item["description"] = desc

    # --- Garble check ---
    if is_garbled(desc) and not is_multi_tier:
        needs_review = True
        item["notes"] = (item["notes"] + "\nRAW BLOCK:\n" + "\n".join(block_lines[:20])).strip()

    # --- Slot inference ---
    item["slot"] = infer_slot(name, desc)

    # --- Class restrictions ---
    item["classes_usable"] = infer_classes(desc)

    # --- Effect extraction ---
    stats = extract_stat_bonuses(desc)
    item.update(stats)
    item["granted_spells"] = extract_spells(desc)
    item["charges"] = extract_charges(desc)

    item["needs_review"] = needs_review
    return item


# ── XLSX output ────────────────────────────────────────────────────────────

COLUMNS = [
    "name", "xp_value", "value_gp", "source",
    "classes_usable", "slot", "description",
    "stat_str", "stat_dex", "stat_con", "stat_int", "stat_wis", "stat_cha",
    "ac_bonus", "ac_set", "thac0_bonus", "save_bonus", "hp_bonus", "movement_bonus",
    "granted_spells", "granted_abilities", "conditions",
    "charges", "notes", "needs_review",
]

COL_WIDTHS = {
    "name": 35, "xp_value": 10, "value_gp": 10, "source": 30,
    "classes_usable": 20, "slot": 12, "description": 80,
    "stat_str": 8, "stat_dex": 8, "stat_con": 8,
    "stat_int": 8, "stat_wis": 8, "stat_cha": 8,
    "ac_bonus": 10, "ac_set": 8, "thac0_bonus": 12, "save_bonus": 11,
    "hp_bonus": 10, "movement_bonus": 14,
    "granted_spells": 40, "granted_abilities": 40, "conditions": 40,
    "charges": 9, "notes": 50, "needs_review": 13,
}


def write_xlsx(items: list[dict], output_path: str):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Items"

    # Header row
    header_font  = Font(bold=True, color="FFFFFF")
    header_fill  = PatternFill("solid", fgColor="1F4E79")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col_idx, col in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col_idx, value=col)
        cell.font   = header_font
        cell.fill   = header_fill
        cell.alignment = header_align

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    # Review row highlight
    review_fill = PatternFill("solid", fgColor="FFF2CC")

    # Data rows
    for row_idx, item in enumerate(items, 2):
        for col_idx, col in enumerate(COLUMNS, 1):
            val = item.get(col, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(vertical="top", wrap_text=(col == "description"))
            if item.get("needs_review"):
                cell.fill = review_fill

    # Column widths
    for col_idx, col in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = COL_WIDTHS.get(col, 15)

    wb.save(output_path)


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--sample", action="store_true",
                    help="Parse first 5 real items and print preview only")
    args = ap.parse_args()

    print(f"Reading {args.input} …")
    with open(args.input, encoding="utf-8") as f:
        md_text = f.read()

    print("Parsing items …")
    items, skipped = extract_items(md_text)

    if args.sample:
        print(f"\n{'═'*60}")
        print(f"SAMPLE — first 5 items ({len(items)} total parsed)")
        print(f"{'═'*60}")
        for item in items[:5]:
            print(f"\n  Name:        {item['name']}")
            print(f"  XP:          {item['xp_value']}   GP: {item['value_gp']}")
            print(f"  Source:      {item['source']}")
            print(f"  Slot:        {item['slot']}")
            print(f"  Classes:     {item['classes_usable']}")
            print(f"  AC bonus:    {item['ac_bonus']}   Save: {item['save_bonus']}")
            print(f"  Spells:      {item['granted_spells'] or '—'}")
            print(f"  Charges:     {item['charges']}")
            print(f"  Needs review:{item['needs_review']}")
            print(f"  Notes:       {item['notes'][:80] or '—'}")
            print(f"  Desc[0:120]: {item['description'][:120]}")
        return

    # Summary
    n_review     = sum(1 for i in items if i["needs_review"])
    n_multitier  = sum(1 for i in items if "TIER TABLE" in i.get("notes", ""))
    print(f"\n{'─'*48}")
    print(f"Items parsed:       {len(items)}")
    print(f"Needs review:       {n_review}")
    print(f"Multi-tier items:   {n_multitier}")
    print(f"Skipped headings:   {len(skipped)}")
    if skipped:
        print("  Skipped (sample):", skipped[:10])
    print(f"{'─'*48}\n")

    print(f"Writing {args.output} …")
    write_xlsx(items, args.output)
    print(f"Done → {args.output}")


if __name__ == "__main__":
    main()
