"""
merger.py — Uses Claude to compare three OCR extractions and produce
clean, structured markdown output suitable for agent consumption.

Prompt caching is applied to the system prompt (constant across all pages)
to reduce API cost significantly when processing multi-page PDFs.
"""

from __future__ import annotations
import anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# System prompt is cached — it's the same for every page call.
SYSTEM_PROMPT = """\
You are an expert editor specialising in Advanced Dungeons & Dragons 2nd Edition (AD&D 2e) rulebooks.

You will receive three independent OCR extractions of the same PDF page, along with any structured table data detected automatically. Your job is to produce a single, clean, accurate markdown document from them.

## Output rules

1. **Cross-reference all three versions** to find the most accurate reading of each word. OCR errors to watch for: l/1/I confusion, 0/O confusion, rn/m confusion, split words, merged words, dropped characters.
2. **Preserve AD&D 2e terminology exactly**: THAC0, d4 d6 d8 d10 d12 d20 d100, Hit Dice, Hit Points, Saving Throw, Armor Class, Experience Points, XP, GP/SP/CP/EP/PP, etc.
3. **Format all tables as GitHub-flavored markdown tables** with a header separator row. Use the structured table data from pdfplumber when available — it is the most reliable for cell boundaries.
4. **Use markdown headings**: # for chapter titles, ## for major sections, ### for subsections. Infer hierarchy from font size clues in the pdfplumber text (ALL CAPS lines, lines ending with no period, etc.).
5. **Preserve numbered and bulleted lists** using markdown syntax.
6. **Reading order**: AD&D 2e books use a two-column layout. Reconstruct the correct left-column-then-right-column reading order.
7. **Do not invent content**. If all three versions are illegible for a passage, write `[illegible]`. If only one version has a reading that seems plausible, use it and mark it `[?]`.
8. **Numerical values are critical** — double-check all numbers (bonuses, penalties, dice, ranges, costs). Flag any disagreement between versions with `[?]`.
9. Output **only** the clean markdown. Do not add commentary, preamble, or postamble.
"""


def _format_pdfplumber_tables(tables: list) -> str:
    """Render pdfplumber's raw table data as markdown for the prompt."""
    if not tables:
        return ""
    chunks = []
    for table in tables:
        if not table:
            continue
        cleaned = [[str(cell or "").strip() for cell in row] for row in table]
        if not cleaned:
            continue
        col_count = max(len(row) for row in cleaned)
        # Pad rows to uniform width
        rows = [row + [""] * (col_count - len(row)) for row in cleaned]
        header = rows[0]
        sep = ["---"] * col_count
        md_rows = [header, sep] + rows[1:]
        chunks.append("\n".join("| " + " | ".join(r) + " |" for r in md_rows))
    return "\n\n".join(chunks)


def merge_page(
    page_num: int,           # 1-based for display
    pdfplumber_result: dict,
    tesseract_result: dict,
    easyocr_result: dict,
) -> str:
    """
    Send all three OCR versions to Claude and return clean markdown.
    The system prompt is marked for caching (Anthropic prompt caching).
    """
    client = anthropic.Anthropic()

    tables_md = _format_pdfplumber_tables(pdfplumber_result.get("tables", []))
    tables_section = (
        f"\n\n**Structured tables (pdfplumber — most reliable for cell layout):**\n{tables_md}"
        if tables_md
        else ""
    )

    user_content = f"""\
## Page {page_num}

### Version 1 — pdfplumber (existing PDF text layer){tables_section}
```
{pdfplumber_result.get("text") or "(nothing extracted)"}
```

### Version 2 — Tesseract OCR
```
{tesseract_result.get("text") or "(nothing extracted)"}
```

### Version 3 — EasyOCR
```
{easyocr_result.get("text") or "(nothing extracted)"}
```

Produce the clean merged markdown for this page now."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    usage = response.usage
    return response.content[0].text, {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
    }
