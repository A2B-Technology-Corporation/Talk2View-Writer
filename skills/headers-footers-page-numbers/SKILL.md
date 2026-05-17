---
name: headers-footers-page-numbers
description: Set document headers, footers, and page numbers using section-aware tools. Use when the user mentions headers, footers, page numbers, first-page headers, even/odd page headers, multi-section layouts, or letterheads. These elements live OUTSIDE the document body and need dedicated tools — not body editors. Supports per-section batching and combining brand text with page numbers in one call.
metadata:
  author: talk2view-word
  version: "3.0"
---

# Headers, Footers & Page Numbers

## When to use

- User asks to add, change, or remove a header or footer
- User asks to add page numbers (with or without surrounding brand/copyright text)
- User wants different headers/footers in different sections
- User mentions "letterhead", "running title", "page X of Y", or "document footer"

## Critical concept: Headers/footers are NOT part of the document body

Headers and footers exist in a separate layer tied to document **sections**. You CANNOT read or edit them via `get_document`, `insert_content`, or `format_paragraph` — those touch the body only.

You MUST use:
- `set_header_footer` — set header/footer text (plain text in one or many sections)
- `insert_page_numbers` — dynamic page numbers, optionally fused with surrounding text via `prefix_text`/`suffix_text`
- `get_document` — only to check the `sections` count returned in the response

## Key rules

### 1. Always check section count first

```
get_document()
// Look at the "sections" field in the response
```

Most documents have **1 section**. Documents with title pages, landscape sections, or per-region headers have multiple sections.

### 2. Both tools accept `section_index` OR `section_indices`

For a single section, use `section_index` (defaults to 0). For multi-section documents where the same content goes to multiple sections, use `section_indices=[…]` to apply in ONE call.

### 3. Both tools REPLACE (clear then write) the target

Each call overwrites the section's header/footer for the chosen `header_footer_type`. Two `set_header_footer` calls for the same `(section, type, header_footer_type)` triple — only the second survives.

### 4. To combine brand text with page numbers IN THE SAME footer, use one call

Don't call `set_header_footer` and then `insert_page_numbers` on the same location — the second call clears the first. Use `prefix_text`/`suffix_text`:

```
insert_page_numbers(
  location="footer",
  prefix_text="Acme Corp   ",
  format="Page {PAGE} of {NUMPAGES}",
  suffix_text="   © 2026"
)
// Footer renders: "Acme Corp   Page 3 of 12   © 2026"
```

Only split across header+footer when you genuinely want different content in each.

## Step-by-step: Add header + page numbers (single section)

```
get_document()
// → sections: 1

set_header_footer(type="header", text="Quarterly Business Review — Q1 2026")

insert_page_numbers(
  location="footer",
  alignment="center",
  format="Page {PAGE} of {NUMPAGES}"
)
```

Two calls. The header lives in the header layer; the footer holds the page numbers.

## Step-by-step: Brand text and page numbers together in ONE call

```
insert_page_numbers(
  location="footer",
  prefix_text="Confidential — Acme Corp   ",
  format="Page {PAGE} of {NUMPAGES}",
  alignment="center"
)
```

One call, no header/footer collision.

## Step-by-step: Different first-page header (no section breaks needed)

```
// Different header for the first page only
set_header_footer(type="header", text="Cover Page", header_footer_type="firstPage")

// Header for all other pages
set_header_footer(type="header", text="Project Proposal — Acme Corp", header_footer_type="primary")
```

The three `header_footer_type` values: `"primary"` (default; all pages or odd pages if even/odd mode is on), `"firstPage"`, `"evenPages"`. Different first-page or even/odd display may require Word desktop.

## Step-by-step: Multi-section document (title page + body)

```
// 1. Insert a section break after the title page content
insert_break(type="section_next_page", location="end")

// 2. Check section count
get_document()
// → sections: 2

// 3. Title page (section 0) — empty header, no page number
set_header_footer(type="header", text=" ", section_index=0)

// 4. Body sections — same header for sections 1 and any others, ONE call
set_header_footer(
  type="header",
  text="Project Proposal — Acme Corp",
  section_indices=[1]
)

// 5. Page numbers in body sections — also batched
insert_page_numbers(
  location="footer",
  format="{PAGE}",
  section_indices=[1]
)
```

If you have many sections that should share a header/footer, list them all in `section_indices` — one call instead of N.

## Page number format options

| Format | Example output |
|--------|---------------|
| `{PAGE}` | 3 |
| `Page {PAGE}` | Page 3 |
| `Page {PAGE} of {NUMPAGES}` | Page 3 of 12 |
| `- {PAGE} -` | - 3 - |
| `{PAGE} / {NUMPAGES}` | 3 / 12 |

`prefix_text` and `suffix_text` can wrap any of these.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Using `insert_content` to add header text | Body tools cannot reach headers/footers | Use `set_header_footer` |
| `set_header_footer(type="footer", …)` then `insert_page_numbers(location="footer")` | The page-number call clears the footer | Use one `insert_page_numbers` call with `prefix_text`/`suffix_text` |
| Calling `insert_page_numbers` once per section in a multi-section doc | Wastes calls | Pass `section_indices=[0,1,2,…]` |
| Not checking section count before specifying `section_index` | Index out of range | Always call `get_document` first |
| Trying to format header/footer text with `format_text` | format_text operates on the body | Headers/footers accept plain text only via `set_header_footer` and `insert_page_numbers` |
