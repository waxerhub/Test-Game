"""
merger.py — Sends Tesseract OCR output to Claude for cleanup and formatting.

Uses prompt caching on the system prompt (constant across all pages) to
significantly reduce API cost on multi-page runs.
"""

from __future__ import annotations
import time
import anthropic

MODEL      = "claude-sonnet-4-6"
MAX_TOKENS = 4096

SYSTEM_PROMPT = """\
You are an expert editor processing scanned pages from the AD&D 2nd Edition \
*Encyclopedia Magica Volume 1* — a comprehensive catalogue of magic items \
covering items A through D.

You will receive the raw Tesseract OCR output for one page, plus any structured \
table data detected from the PDF's vector layer. Your job is to produce clean, \
accurate, agent-readable markdown.

## Output rules

1. **Fix OCR errors** — common mistakes: l/1/I confusion, 0/O, rn/m, \
   split words (e.g. "A nklet" → "Anklet"), merged words ("ofthe" → "of the"). \
   Also fix: cl→d, fi→fi ligatures, missing apostrophes (Tensers → Tenser's), \
   stray hyphens from line-wrap, and garbled punctuation.

2. **Preserve AD&D 2e terminology exactly** — THAC0, d4/d6/d8/d10/d12/d20/d100, \
   Hit Dice, Hit Points, Saving Throw, Armor Class, XP, GP/SP/CP/EP/PP, #AT, MV, \
   HD, hp, SA, SD, ML, AL, AC. Never paraphrase these; copy them verbatim once \
   corrected. Spell names should match canonical AD&D 2e spelling \
   (e.g. "magic missile", "dispel magic", "detect magic", "hold person", \
   "fireball", "lightning bolt", "cone of cold", "polymorph other", \
   "globe of invulnerability", "wall of force").

3. **Item entry format** — each magic item should follow this markdown structure:
   ```
   ## Full Item Name

   **XP Value:** X &emsp; **GP Value:** Y
   *Source: Book Title, page N* (or magazine reference)

   Description text…
   ```

4. **Full item names** — if a page is part of a section (e.g. the "Anklet" \
   section) and lists items as "of Levitation", "of Sinking" etc., prepend the \
   section noun to form the full name: "Anklet of Levitation", "Anklet of Sinking". \
   Similarly: "Arrow of Slaying", "Bag of Holding", "Boots of Elvenkind", \
   "Bracers of Defense", "Cloak of Protection", "Decanter of Endless Water", \
   "Dust of Appearance", etc. Always use the full name as the ## heading.

5. **Tables** — format as GitHub-flavored markdown tables. Use the structured \
   table data when provided (it has the most reliable cell boundaries). \
   Preserve all column headers and numeric values exactly. \
   Example table format:
   ```
   | d100 Roll | Result        |
   |-----------|---------------|
   | 01–05     | No effect     |
   | 06–10     | +1 bonus      |
   ```

6. **Two-column layout** — the source PDF uses a two-column layout. Tesseract \
   may interleave left and right column text. Reconstruct the correct \
   left-column-first, then right-column reading order. Clues: item entries \
   start with a name line followed by XP/GP values; a new item header mid-line \
   signals a column break.

7. **Headings** — use ## for item names, ### for sub-sections within an entry \
   (e.g. "### Powers", "### Curse"). Use # only for major chapter headings \
   (Introduction, Table of Contents, How to Use This Book, etc.). \
   Do not add headings that are not present in the source.

8. **Numbers are critical** — double-check all numeric values (bonuses, dice \
   expressions, GP/XP costs, ranges, durations, charges). Common OCR traps: \
   "l" for "1", "O" for "0", "S" for "5", "B" for "8". \
   Flag genuine uncertainty with [?] immediately after the value.

9. **Source references** — lines citing a source (e.g. "DRAGON #92, p. 14", \
   "Forgotten Realms Campaign Set", "Player's Handbook, p. 130") should be \
   formatted as italics on their own line: *Source: DRAGON #92, p. 14*

10. **Output only the clean markdown** — no preamble, no commentary, no \
    explanation. Start directly with the first heading or paragraph of content.

## AD&D 2e magic item reference

**Standard abbreviations used in entries:**
- XP = Experience Points (awarded to finder)
- GP = Gold Piece value (sale/identification value)
- AC = Armor Class (lower is better in 2e; AC 10 = unarmored)
- THAC0 = To Hit Armor Class 0 (attack roll target number)
- MV = Movement rate (in tens of feet per round)
- HD = Hit Dice; hp = hit points
- SA = Special Attack; SD = Special Defense
- ML = Morale rating; AL = Alignment
- ST = Saving Throw; d% or d100 = percentile dice roll
- #AT = Number of attacks per round
- (1/day), (1/week), (1/month) = usage frequency of a power
- [C] = Cursed item; charges = number of uses before depleted

**Saving throw categories (2e):**
Paralyzation/Poison/Death Magic | Rod/Staff/Wand | \
Petrification/Polymorph | Breath Weapon | Spell

**Typical item structure in Encyclopedia Magica:**
Each entry gives the item's full name as a heading, followed by XP Value and \
GP Value on one line, then a source citation (book, module, or magazine), \
then one or more paragraphs describing appearance, history, powers, command \
words, charges, and any curse or drawback. Some entries include a random \
effects table (roll d6, d10, d20, or d100). Sub-items within a section \
(e.g. multiple types of Arrow, multiple Bags) each get their own ## heading \
with the full name (e.g. "## Arrow of Slaying (Human)", "## Bag of Holding \
(Type I)").
"""


def _format_tables(tables: list) -> str:
    if not tables:
        return ""
    chunks = []
    for table in tables:
        if not table:
            continue
        cleaned = [[str(c or "").strip() for c in row] for row in table]
        if not cleaned:
            continue
        col_n = max(len(r) for r in cleaned)
        rows  = [r + [""] * (col_n - len(r)) for r in cleaned]
        lines = ["| " + " | ".join(rows[0]) + " |",
                 "| " + " | ".join(["---"] * col_n) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def merge_page(page_num: int, text: str, tables: list) -> tuple[str, dict]:
    """
    Send one page to Claude and return (cleaned_markdown, token_usage_dict).
    The system prompt is marked for caching — after the first page, it is read
    from cache at ~10% of the normal input token cost.
    """
    client = anthropic.Anthropic()

    table_section = ""
    if tables:
        fmt = _format_tables(tables)
        if fmt:
            table_section = (
                "\n\n**Structured table data (from PDF vector layer — "
                "use for cell boundaries):**\n" + fmt
            )

    user_msg = (
        f"## Page {page_num}\n\n"
        f"**Tesseract OCR output:**\n```\n{text or '(no text extracted)'}\n```"
        f"{table_section}\n\n"
        "Produce the clean markdown for this page now."
    )

    for attempt in range(6):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_msg}],
            )
            break
        except anthropic.RateLimitError as e:
            wait = 15 * (2 ** attempt)   # 15, 30, 60, 120, 240, 480s
            print(f"\n  Rate limit page {page_num}, retrying in {wait}s…", flush=True)
            time.sleep(wait)
    else:
        raise RuntimeError(f"Rate limit persisted after 6 retries on page {page_num}")

    u = resp.usage
    return resp.content[0].text, {
        "input_tokens":                u.input_tokens,
        "output_tokens":               u.output_tokens,
        "cache_read_input_tokens":     getattr(u, "cache_read_input_tokens",     0),
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
    }
