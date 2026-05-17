---
name: document-creation
description: Build a complete Word document from scratch OR insert a coherent multi-element section into an existing one. Use when the user asks to "write", "create", "draft", "build", or "add a section/chapter" with mixed elements (headings, paragraphs, tables, lists). Plans structure, writes section by section, applies consistent styles and spacing in one batched call, and preserves heading hierarchy.
metadata:
  author: talk2view-word
  version: "3.0"
---

# Document Creation & Section Insertion

## When to use

- User asks to "write a report", "create a proposal", "draft a memo", or "build a document" from scratch
- User provides a topic or outline and expects a complete document
- Document is empty or near-empty and the user wants it populated
- User asks to "add a section", "add a chapter", or insert a coherent block with mixed elements (heading + body + table)
- You need to insert more than 2 consecutive elements that belong together

## The core problem

Creating a document is not the same as inserting text. A good document has logical structure (headings, sections), consistent styling, and clear flow. Without a plan, agents dump unstructured text.

## Key rules

### 1. Plan before you write

Write out the full structure before touching a tool:

```
Document: Quarterly Business Review
  Title: "Q1 2026 Business Review"
  Heading1: "Executive Summary" — 1-2 paragraphs
  Heading1: "Revenue Performance" — overview + table
  Heading1: "Key Initiatives"
    Heading2: "Product Launch"
    Heading2: "Market Expansion"
  Heading1: "Next Steps" — bulleted action items
```

### 2. Match the existing heading hierarchy

Before inserting into a non-empty document:

```
get_document()
```

If the document's top-level sections use `Heading1`, your new section uses `Heading1`. If you're adding a subsection under an existing `Heading1`, use `Heading2`. Never skip levels (no Heading1 → Heading3).

Use `Title` for the document title (not Heading1). Use the same body style (`Normal`, `NoSpacing`, …) that the surrounding document already uses.

### 3. Set up the page layout first (only for brand-new documents)

```
set_page_setup(orientation="portrait", top_margin=72, bottom_margin=72, left_margin=72, right_margin=72)
```

Word desktop only — skip on Word Online.

### 4. Batch in ONE call with blocks + fused paragraph formatting

`insert_content(blocks=[…])` accepts paragraph-level formatting (alignment, space_before, space_after, line_spacing, indent) that applies to every inserted paragraph — no follow-up `format_paragraph` call needed.

```
insert_content(
  blocks=[
    { text: "Q1 2026 Business Review", style: "Title" },
    { text: "Executive Summary",       style: "Heading1" },
    { text: "The first quarter exceeded expectations across all product lines, with total revenue reaching $4.2M." }
  ],
  location="end",
  space_before=12,
  space_after=6
)
```

If `location` is omitted, `insert_content` defaults to `"end"` (safe append). Do NOT mix `"start"` and `"end"` within a single sequence of calls — elements will interleave across calls.

### 5. Insert tables as a separate call in the flow

Tables cannot go inside `blocks`. Insert surrounding paragraphs, then the table, then follow-up text:

```
insert_content(blocks=[{ text: "Data Summary", style: "Heading2" }], location="end")
insert_table(rows=4, columns=3, location="end", data=[
  ["Metric", "Q1", "Q2"],
  ["Revenue", "$1.2M", "$1.4M"],
  ["Users",   "10K",   "12K"],
  ["NPS",     "72",    "78"]
])
insert_content(text="Table 1: Q1 performance against targets.", location="end")
```

### 6. Inserting BETWEEN existing sections

Use paragraph-relative locations to insert precisely, with or without fused formatting:

```
insert_content(
  blocks=[
    { text: "New Section", style: "Heading2" },
    { text: "Section body text." }
  ],
  location="after_paragraph",
  paragraph_index=5,
  space_before=12
)
```

### 7. Lists: fuse list creation with indent

Use `manage_list` with `left_indent` so you don't need a follow-up `format_paragraph`:

```
insert_content(
  blocks=[
    { text: "Next Steps", style: "Heading1" },
    { text: "Finalize budget allocation for Q3" },
    { text: "Schedule design review with stakeholders" },
    { text: "Submit compliance report by June 15" }
  ],
  location="end"
)
get_document()
// Find the three action-item indices (e.g. 7, 8, 9)
manage_list(
  action="add",
  paragraph_indices=[7, 8, 9],
  list_type="bullet",
  left_indent=36
)
```

### 8. Page breaks before major sections (when appropriate)

For top-level `Heading1` sections in longer documents:

```
insert_content(
  text="New Chapter Title",
  location="end",
  style="Heading1",
  page_break_before=true    // note: supported in desktop; silently skipped in Word Online
)
```

Or use an explicit `insert_break(type="page", location="end")` before the heading for broad compatibility.

## Step-by-step: Create a document

### Phase 1: Understand the request

1. Clarify document type if not obvious ("report, proposal, memo, letter?").
2. Identify key content. Ask if the request is vague. **Never fabricate facts/numbers.**
3. Plan the full outline.

### Phase 2: Set up

4. `get_document()` — confirm it's empty or get the user's permission to append/replace.
5. `set_page_setup(…)` if needed (desktop only).

### Phase 3: Write content

6. Title + first section in one batched call.
7. Tables + captions.
8. Lists fused with `manage_list(left_indent=…)`.
9. Continue section by section. Keep each `blocks` array to ~3–6 items for readability — this is a stylistic preference, not a limit.

### Phase 4: Headers, footers, page numbers

10. Use `insert_page_numbers(prefix_text=…, suffix_text=…)` to fuse brand text with page numbers in one call if you want them in the same footer (see `headers-footers-page-numbers` skill).

### Phase 5: Review

11. (Optional) Run `pre-send-review` if the document is being shipped, not just drafted.

## Document-type templates

| Type | Typical structure |
|------|-------------------|
| Report | Title → Executive Summary → Sections with data → Conclusion |
| Proposal | Title → Problem Statement → Proposed Solution → Timeline → Budget → Next Steps |
| Memo | To/From/Date/Subject → Body → Action Items |
| Letter | Date → Recipient → Salutation → Body → Closing → Signature |
| Meeting notes | Title with date → Attendees → Agenda → Discussion → Action Items |

## Section templates

### Report section
```
Heading2 → Overview paragraph → Detail paragraph → Table → Caption → Conclusion paragraph
```

### Meeting notes block
```
Heading2 "Meeting — [Date]" → "Attendees:" → Heading3 "Discussion" → summary → Heading3 "Action Items" → table [Owner, Action, Due Date]
```

### Proposal section
```
page break → Heading1 "Proposed Solution" → intro → Heading2 "Approach" → details → Heading2 "Timeline" → table → Heading2 "Budget" → table
```

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Dumping all content as one long paragraph | No structure, unreadable | Plan an outline; use headings and sections |
| `format_text` for headings | Fakes headings, breaks TOC | Set `style` in `insert_content` or use `format_paragraph` |
| `insert_content` then `format_paragraph` for spacing on the same paragraphs | Two calls when one works | Pass `space_before`/`space_after` directly to `insert_content` |
| `manage_list` then `format_paragraph` for list indent | Two calls when one works | Use `manage_list(left_indent=…)` |
| Mixing `location="start"` and `location="end"` in a sequence | Interleaved output | Pick one direction; usually `"end"` |
| Wrong heading level (Heading1 under Heading1) | Breaks hierarchy | Read `get_document()` first; match existing levels |
| Writing entire document then formatting | Hard to fix structure | Format during insertion via fused params |
| Not asking clarifying questions | Document doesn't match expectations | Ask about type, audience, and key content first |
| Fabricating data | Trust failure | If data is missing, ask for it |
