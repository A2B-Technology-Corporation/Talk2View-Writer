---
name: content-extraction
description: Extract, summarize, or analyze content from a Word document without modifying it. Use when the user asks to "summarize", "list the key points", "extract action items", "what does this document say about X", "outline this document", or any read-only analysis of document content. Also use when the user asks to pull specific data from the document (tables, headings, metrics, names).
metadata:
  author: talk2view-word
  version: "1.1"
allowed-tools: get_document get_comments search_document
---

# Content Extraction

## When to use

- User asks to "summarize", "outline", or "give me the key points"
- User asks to "extract" or "list" specific elements (action items, decisions, names, dates, metrics)
- User asks "what does this document say about X?"
- User asks for a "table of contents" or "document outline"
- User asks to compare or contrast sections within the document
- User wants to understand a document before editing it

## The core problem

Extraction is read-only — you analyze the document and respond verbally. You do NOT modify the document. The temptation is to skim and guess, but the agent must read the full document systematically to avoid missing content in later sections.

## Key rules

### 1. Read the FULL document before answering

Always start with a complete read:
```
get_document()
```

If `total_paragraphs` exceeds the returned count, paginate:
```
get_document(start_index=100, count=100)
```

Do not answer based on partial reads. A summary that misses the last section is wrong.

### 2. Use the paragraph structure as your guide

The `get_document()` response includes paragraph styles. Use heading styles to understand document structure:

```
Heading1: "Introduction"        → top-level section
  Normal: body text...
Heading1: "Financial Results"   → next top-level section
  Heading2: "Revenue"           → subsection
    Normal: body text...
  Heading2: "Expenses"          → subsection
    Normal: body text...
```

This heading hierarchy IS the document outline.

### 3. Check tables for structured data

`get_document()` now returns table metadata:
```
tables: [
  { index: 0, rows: 5, columns: 3, first_row: ["Quarter", "Revenue", "Growth"] }
]
```

Tables often contain the most important data — don't skip them.

### 4. Check comments for context

If the document has review comments, they may contain important context:
```
get_comments()
```

Comments reveal what reviewers flagged, what's been discussed, and what's unresolved.

### 5. Never modify the document during extraction

Extraction is read-only. Do not call insert_content, format_text, format_paragraph, delete_content, or any write tool. If the user wants changes based on your analysis, confirm first.

## Step-by-step: Summarize a document

1. **Read the full document:**
   ```
   get_document()
   ```
   If paginated, read all pages.

2. **Build the outline** from heading styles:
   ```
   Heading1: "Executive Summary"
   Heading1: "Market Analysis"
     Heading2: "Competitive Landscape"
     Heading2: "Target Segments"
   Heading1: "Strategy"
   Heading1: "Financial Projections"
   Heading1: "Next Steps"
   ```

3. **Identify the key message** from the first section (usually Executive Summary or Introduction).

4. **Note data from tables** — revenue figures, timelines, comparisons.

5. **Present a structured summary:**
   ```
   This document is a [type] covering [topic]. Key points:

   - [Main finding/argument from section 1]
   - [Main finding from section 2]
   - [Key data point from table: e.g., "Revenue grew 23% to $4.2M"]
   - [Conclusion or recommended action]

   The document has [N] sections and [M] tables.
   ```

## Step-by-step: Extract specific elements

### Extract action items

1. **Read the document:**
   ```
   get_document()
   ```

2. **Scan for action-oriented language:** "will", "should", "must", "action:", "next step:", "TODO", "by [date]", "responsible:", "owner:".

3. **Check tables** — action items are often in tables with columns like "Action", "Owner", "Due Date".

4. **Present as a list:**
   ```
   Action items found:

   1. [Action] — Owner: [name], Due: [date] (paragraph N)
   2. [Action] — Owner: [name], Due: [date] (table 0, row 3)
   ```

### Extract document outline

1. **Read the document:**
   ```
   get_document()
   ```

2. **Filter paragraphs with heading styles** (Heading1, Heading2, Heading3, Title).

3. **Present with indentation:**
   ```
   Document outline:

   Title: "Annual Report 2025"
   1. Executive Summary
   2. Financial Performance
      2.1 Revenue
      2.2 Operating Costs
   3. Strategic Initiatives
      3.1 Product Development
      3.2 Market Expansion
   4. Outlook
   ```

### Extract data from tables

1. **Read the document to find tables:**
   ```
   get_document()
   // tables: [{ index: 0, rows: 5, columns: 3, first_row: ["Quarter", "Revenue", "Growth"] }]
   ```

2. **Report the table contents** using the first_row preview and row count:
   ```
   Found 1 table:
   - Table 0: 5 rows x 3 columns, headers: Quarter, Revenue, Growth
   ```

3. **For detailed data**, the full table values are available in the get_document response paragraphs and table metadata.

## Extraction types

| User request | What to extract | Where to look |
|-------------|----------------|---------------|
| "Summarize this" | Main argument, key findings, conclusion | Headings + first paragraph of each section |
| "List action items" | Tasks, owners, deadlines | Body text + tables with action/owner/date columns |
| "Give me the outline" | Document structure | Heading styles only |
| "What are the key metrics?" | Numbers, percentages, amounts | Tables + body text with numeric data |
| "Who is mentioned?" | Names, roles | Body text + tables |
| "What decisions were made?" | Decision statements | Body text near "decided", "agreed", "approved" |
| "What's unresolved?" | Open items, questions | Comments (get_comments) + text with "TBD", "TODO", "?" |

## Gotchas

- **Paginate large documents.** If `total_paragraphs > 100`, you only got the first 100. Call `get_document(start_index=100, count=100)` to get the rest.
- **Tables may contain the most important data.** Don't skip them in your summary.
- **Comments are separate from body text.** Call `get_comments()` if the user asks about feedback, issues, or unresolved items.
- **Don't confuse heading text with body text.** A heading paragraph like "Revenue" is a section label, not a content statement.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Summarizing from the first few paragraphs only | Misses key content in later sections | Read the full document; paginate if needed |
| Not checking tables | Tables often contain the key data | Always check table metadata in get_document response |
| Modifying the document during extraction | User asked for analysis, not changes | Only use read tools (get_document, get_comments, search_document) |
| Giving a vague summary | "The document discusses several topics" is useless | Include specific findings, numbers, and section references |
| Not mentioning unresolved comments | User may not know there's open feedback | Check get_comments and report unresolved items |
