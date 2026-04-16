"""
merger.py — Sends Tesseract OCR output to Claude for cleanup and formatting.

Uses prompt caching on the system prompt (constant across all pages) to
significantly reduce API cost on multi-page runs.
"""

from __future__ import annotations
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
   split words (e.g. "A nklet" → "Anklet"), merged words ("ofthe" → "of the").

2. **Preserve AD&D 2e terminology exactly** — THAC0, d4/d6/d8/d10/d12/d20/d100, \
   Hit Dice, Hit Points, Saving Throw, Armor Class, XP, GP/SP/CP/EP/PP, #AT, MV, \
   HD, hp, SA, SD, ML, AL, AC.

3. **Item entry format** — each magic item should follow this markdown structure:
   ```
   ## Full Item Name

   **XP Value:** X &emsp; **GP Value:** Y
   *Source reference*

   Description text…
   ```

4. **Full item names** — if a page is part of a section (e.g. the "Anklet" \
   section) and lists items as "of Levitation", "of Sinking" etc., prepend the \
   section noun: "Anklet of Levitation", "Anklet of Sinking".

5. **Tables** — format as GitHub-flavored markdown tables. Use the structured \
   table data when provided; it has the most reliable cell boundaries.

6. **Two-column layout** — reconstruct correct left-then-right reading order.

7. **Headings** — use ## for item names, ### for sub-sections within an entry. \
   Use # only for major chapter headings (Introduction, Table of Contents, etc.).

8. **Numbers are critical** — double-check all numeric values (bonuses, dice, \
   costs, ranges). Flag genuine uncertainty with [?].

9. **Output only the clean markdown** — no preamble, no commentary.
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

    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )

    u = resp.usage
    return resp.content[0].text, {
        "input_tokens":                u.input_tokens,
        "output_tokens":               u.output_tokens,
        "cache_read_input_tokens":     getattr(u, "cache_read_input_tokens",     0),
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
    }
