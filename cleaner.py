#!/usr/bin/env python3
"""
cleaner.py — Rule-based cleaner for Encyclopedia Magica Volume 1 raw OCR JSON.

Strategy:
  - XP Value / GP Value lines are the most reliable structural anchors.
  - Look back from each XP/GP anchor to find the item name.
  - If the name starts with 'of/from/…' it is a sub-item; prepend the current
    section category to make a full name (e.g. "Talisman of the Chimera").
  - Skip or flag lines that are clearly garbled.

Usage:
    python3 cleaner.py
    python3 cleaner.py --input foo_raw.json --output foo_clean.md
"""

import json
import re
import argparse
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

PREPOSITIONS = (
    'of ', 'from ', 'for ', 'against ', 'with ', 'upon ', 'the ',
    'at ', 'in ', 'by ', 'into ', 'over ', 'under ', 'within ',
)

SOURCE_KEYWORDS = (
    'DRAGON', 'DUNGEON', 'POLYHEDRON', 'The Book of', 'Legends & Lore',
    'Forgotten Realms', 'Spelljammer', 'Ravenloft', 'Dark Sun', 'Al-Qadim',
    'Realmspace', 'Greyhawk', 'Birthright', 'Planescape', 'Mystara',
    "Player's Handbook", 'Dungeon Master', 'Monster Manual', 'Fiend Folio',
    'Unearthed Arcana', 'Oriental Adventures', 'Manual of the Planes',
    'Tome of Magic', 'Complete',
)

# ── Text-fixing helpers ────────────────────────────────────────────────────

def is_reversed(line: str) -> bool:
    """Detect char-by-char reversed lines (each character separated by space)."""
    tokens = line.split()
    if len(tokens) < 4:
        return False
    return sum(1 for t in tokens if len(t) == 1) / len(tokens) > 0.65


def fix_reversed(line: str) -> str:
    """Un-reverse a character-by-character reversed line."""
    tokens = list(reversed(line.split()))
    text = ''.join(tokens)
    # Re-insert spaces around common prepositions fused into the string
    for p in ['of', 'from', 'the', 'for', 'with', 'by', 'at', 'in', 'on',
              'an', 'into', 'over', 'upon', 'within', 'against', 'under']:
        text = re.sub(rf'([a-zA-Z])({p})([a-zA-Z])', r'\1 \2 \3', text,
                      flags=re.IGNORECASE)
    # Split on lowercase→uppercase transitions (camelCase → word boundary)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return re.sub(r' {2,}', ' ', text).strip()


def clean_line(raw: str) -> str:
    """Strip positional whitespace, fix reversal, remove OCR noise."""
    line = raw.strip()
    if not line:
        return ''
    if is_reversed(line):
        line = fix_reversed(line)
    # Remove lone stray characters (OCR artefacts like "e h" left alone)
    line = re.sub(r'\b([a-zA-Z])\b', '', line)
    line = re.sub(r' {2,}', ' ', line).strip()
    return line


def garbled_ratio(line: str) -> float:
    """Fraction of characters that look like OCR garble (non-alpha, non-space, non-punct)."""
    if not line:
        return 0.0
    noise = sum(1 for c in line if not (c.isalpha() or c.isspace()
                                        or c in "'-,.:;!?()/+&%$"))
    return noise / len(line)


def is_mostly_garbled(line: str) -> bool:
    """True if the line is too garbled to be useful."""
    if len(line) < 4:
        return True
    # High ratio of single-char space-separated tokens in short segments
    tokens = line.split()
    if len(tokens) >= 3:
        single = sum(1 for t in tokens if len(t) == 1)
        if single / len(tokens) > 0.55:
            return True
    return False


# ── Structure helpers ──────────────────────────────────────────────────────

def is_xp_gp(line: str) -> bool:
    return bool(re.search(r'XP\s*Value', line) and re.search(r'GP\s*Value', line))


def is_source(line: str) -> bool:
    return any(kw in line for kw in SOURCE_KEYWORDS)


def is_subitem_name(line: str) -> bool:
    """'of the Chimera', 'of Protection' etc. — partial item name fragment."""
    lower = line.lower()
    return (any(lower.startswith(p) for p in PREPOSITIONS)
            and 1 < len(line.split()) <= 8
            and not re.search(r'[.;!?]', line))


def is_clean_item_name(line: str) -> bool:
    """
    Heuristic for a valid item name:
      - 2–6 words
      - Starts with a capital letter
      - No sentence-ending or mid-sentence punctuation
      - No colons (those indicate stat/description lines)
      - Most words are properly capitalised or known connectors
      - Average word length > 2 (filters out single-char noise)
    """
    line = line.strip()
    if not line:
        return False
    words = line.split()
    if not (2 <= len(words) <= 6):
        return False
    if not words[0][0].isupper():
        return False
    if re.search(r'[.;!?:]', line):
        return False
    if is_mostly_garbled(line):
        return False
    # Reject if average word length is too short (noise/garble indicator)
    if sum(len(w) for w in words) / len(words) < 2.5:
        return False
    # Reject lines that look like they start mid-sentence
    if line[0].islower():
        return False
    # At least half the words should start with a capital or be known connectors
    connectors = {'of', 'the', 'a', 'an', 'and', 'or', 'from', 'for', 'with',
                  'by', 'in', 'at', 'to', 'on', 'into', 'over', 'under',
                  'against', 'upon', 'within'}
    valid = sum(1 for w in words if w[0].isupper() or w.lower() in connectors)
    return valid >= len(words) * 0.65


def extract_category(name: str) -> str:
    """
    Extract the category noun from an item name.
    'Anklet of Levitation' → 'Anklet'
    'Iron Anvil of the Armies' → 'Iron Anvil'  (words before 'of')
    'High Anvil of the Dwarves' → 'High Anvil'
    """
    lower = name.lower()
    for p in (' of ', ' from ', ' for ', ' against ', ' with ', ' upon '):
        idx = lower.find(p)
        if idx > 0:
            return name[:idx].strip()
    return name.strip()


def format_xp_gp(line: str) -> str:
    """Normalise spacing and bold the labels."""
    line = re.sub(r'\s+', ' ', line).strip()
    line = re.sub(r'(XP\s*Value\s*:?\s*)', r'**XP Value:** ', line)
    line = re.sub(r'(GP\s*Value\s*:?\s*)', r'**GP Value:** ', line)
    return line


# ── Core processing ────────────────────────────────────────────────────────

def get_page_lines(raw: str) -> list[str]:
    """Return cleaned, non-empty lines for one page."""
    out = []
    for raw_line in raw.splitlines():
        c = clean_line(raw_line)
        if c and not is_mostly_garbled(c):
            out.append(c)
    return out


def build_markdown(pages: list[dict]) -> str:
    """
    Main assembly pass.
    Walks all pages; uses XP/GP anchors to find item entries; resolves
    partial names to full names using category tracking.
    """
    parts: list[str] = []
    current_category = ''
    emitted_items: set[str] = set()

    for entry in pages:
        page_num = entry['page']
        raw = entry.get('pdfplumber', {}).get('text', '')
        if not raw.strip():
            continue

        lines = get_page_lines(raw)
        if not lines:
            continue

        parts.append(f'\n<!-- page {page_num} -->\n')
        pending_item_name = ''  # name found; waiting for XP/GP line

        i = 0
        while i < len(lines):
            line = lines[i]

            # ── XP / GP line: emit the buffered item name + values ─────────
            if is_xp_gp(line):
                # If we don't yet have a name, scan back up to 4 lines
                if not pending_item_name:
                    for back in range(max(0, i - 4), i):
                        cand = lines[back]
                        if is_subitem_name(cand):
                            pending_item_name = (current_category + ' ' + cand).strip()
                            break
                        if is_clean_item_name(cand):
                            pending_item_name = cand
                            break

                if pending_item_name and pending_item_name not in emitted_items:
                    emitted_items.add(pending_item_name)
                    parts.append(f'\n## {pending_item_name}\n')
                    # Update category whenever we have a fresh full name
                    current_category = extract_category(pending_item_name)

                parts.append(format_xp_gp(line))
                pending_item_name = ''
                i += 1
                continue

            # ── Source reference line ─────────────────────────────────────
            if is_source(line):
                parts.append(f'*{line}*')
                i += 1
                continue

            # ── Sub-item name (e.g. "of Levitation") ─────────────────────
            if is_subitem_name(line):
                full = (current_category + ' ' + line).strip()
                pending_item_name = full
                i += 1
                continue

            # ── Potential item / section name ─────────────────────────────
            if is_clean_item_name(line):
                # Could be a section header arriving without an XP/GP line
                # (intro page for a section). Update category tracking.
                category_candidate = extract_category(line)
                if category_candidate:
                    current_category = category_candidate
                pending_item_name = line
                i += 1
                continue

            # ── Regular description prose ─────────────────────────────────
            parts.append(line)
            i += 1

    return '\n'.join(parts)


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input',  default='tsr02141 Encyclopedia Magica Volume One_raw.json')
    ap.add_argument('--output', default='tsr02141 Encyclopedia Magica Volume One_compiled.md')
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)

    print(f'Reading {inp} …')
    with open(inp, encoding='utf-8') as f:
        data = json.load(f)

    print(f'Processing {len(data)} pages …')
    header = (
        '# Encyclopedia Magica Volume One\n\n'
        f'> Source: `{inp.name}`  \n'
        f'> Pages: {len(data)}\n\n---\n'
    )

    body = build_markdown(data)

    with open(out, 'w', encoding='utf-8') as f:
        f.write(header + body)

    # Stats
    headings = [l for l in body.splitlines() if l.startswith('## ')]
    print(f'Done → {out}  ({out.stat().st_size // 1024} KB, {len(headings)} item headings)')


if __name__ == '__main__':
    main()
