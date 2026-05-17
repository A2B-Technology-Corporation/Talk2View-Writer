---
name: formatting-standards
description: Apply consistent document formatting using Word built-in styles. Use when creating, editing, or reformatting documents — especially when applying headings, adjusting spacing, making text bold/italic, or when the user asks for professional formatting. Supports batch paragraph formatting and inline text targeting by query or paragraph index. Prevents heading hierarchy violations, mixed formatting, and inconsistent spacing.
metadata:
  author: talk2view-word
  version: "3.0"
---

# Formatting Standards

## When to use

- User asks to "format", "clean up", or "make professional" a document
- You are creating a new document with multiple sections
- You are adding headings, subheadings, or restructuring content
- The document has inconsistent styles (mixed direct formatting and built-in styles)

## Key rules

### 1. Always use built-in styles, never direct formatting for structure

Built-in styles (`Heading1`, `Heading2`, `Title`, `Subtitle`, `Quote`, `ListParagraph`) are the correct way to structure a document. Direct formatting (manually setting font size/bold) creates maintenance problems and breaks Table of Contents generation.

**Do this:**
```
insert_content(text="Introduction", location="end", style="Heading1")
```

**Not this:**
```
insert_content(text="Introduction", location="end")
format_text(bold=true, size=16)  // BAD: creates fake heading
```

### 2. Never skip heading levels

Heading hierarchy must be sequential. Going from Heading1 to Heading3 (skipping Heading2) breaks document structure, accessibility, and TOC.

**Valid hierarchy:**
```
Heading1: "Annual Report"
  Heading2: "Executive Summary"
  Heading2: "Financial Overview"
    Heading3: "Q1 Results"
    Heading3: "Q2 Results"
```

**Invalid hierarchy:**
```
Heading1: "Annual Report"
    Heading3: "Executive Summary"  // WRONG: skipped Heading2
```

### 3. Use `format_text` only for inline emphasis — with direct targeting AND batching

`format_text` is for character-level formatting within a paragraph — bold a key term, italicise a title, color a warning. It is NOT for structural formatting.

**Single target** (one region):
```
format_text(query="key term", bold=true)
format_text(paragraph_index=3, italic=true)
format_text(query="WARNING: this action is irreversible", highlight="Yellow")
```

**Batch** — always prefer this when formatting 2+ regions. One call instead of N (up to 20 regions per call):

```
format_text(queries=[
  { query: "key term",                bold: true },
  { query: "WARNING: irreversible",   highlight: "Yellow" },
  { paragraph_index: 3,               italic: true },
  { query: "Revenue Q1 2026",         bold: true, color: "0066CC" }
])
```

If neither `query`, `paragraph_index`, nor `queries` is provided, `format_text` falls back to the current selection.

### 4. Match existing document style before adding content

Before inserting content, always read the document first to understand what styles and spacing are already in use.

**Procedure:**
1. Call `get_document()` to see all paragraphs with their styles
2. Note which heading levels are used and their pattern
3. Note spacing between sections
4. Insert new content matching the existing pattern

### 5. Standard spacing conventions

When no existing style is established, use these defaults:

| Element | Style | Space Before | Space After |
|---------|-------|-------------|-------------|
| Title | `Title` | 0pt | 12pt |
| Heading 1 | `Heading1` | 24pt | 6pt |
| Heading 2 | `Heading2` | 18pt | 4pt |
| Heading 3 | `Heading3` | 12pt | 4pt |
| Body text | `Normal` | 0pt | 8pt |
| Quote | `Quote` | 6pt | 6pt |

Apply with `format_paragraph`:
```
format_paragraph(paragraph_index=5, style="Heading1", space_before=24, space_after=6)
```

## Step-by-step: Format an existing document

1. **Read the document:**
   ```
   get_document()
   ```

2. **Audit the paragraph list.** Look at the `style` field for each paragraph. Identify:
   - Paragraphs using "Normal" that should be headings (short text, all caps, or bold)
   - Heading level violations (H1 → H3 skips)
   - Inconsistent spacing patterns

3. **Apply styles in batch.** Use `paragraph_indices` to format multiple paragraphs in one call:
   ```
   format_paragraph(paragraph_indices=[0], style="Title", alignment="center")
   format_paragraph(paragraph_indices=[2, 8, 14], style="Heading1", space_before=24, space_after=6)
   format_paragraph(paragraph_indices=[5, 11],    style="Heading2", space_before=18, space_after=4)
   ```

   Fuse style + spacing in the same call — do NOT call `format_paragraph` twice for the same paragraphs.

4. **Skip re-verification.** `format_paragraph` already returns `resulting_style` per paragraph in its response. Another `get_document()` just to confirm is a wasted call. Only re-read when subsequent operations depend on new indices or text.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Using `format_text(bold=true, size=16)` for headings | Creates "fake" headings that don't appear in TOC or navigation | Use `format_paragraph(style="Heading1")` |
| Skipping `get_document()` before formatting | You don't know what styles already exist; you'll create inconsistency | Always read first |
| Applying styles without checking index bounds | Paragraph indices change as you insert content | Re-read after insertions |
| Setting alignment on every paragraph | Most paragraphs should inherit from their style | Only set alignment when it differs from the style default |
| Mixing `Heading1` and direct bold+size for same-level headings | Inconsistent appearance and broken structure | Pick one approach (styles) and use it everywhere |
