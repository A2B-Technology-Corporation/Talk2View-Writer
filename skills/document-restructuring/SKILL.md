---
name: document-restructuring
description: Reorganize, reorder, or restructure sections within a Word document. Use when the user asks to "move a section", "reorder", "reorganize", "restructure", "merge sections", "split a section", or rearrange content. This is a destructive multi-step operation that requires careful index management — the skill prevents data loss from index shifts during moves.
metadata:
  author: talk2view-word
  version: "1.0"
---

# Document Restructuring

## When to use

- User asks to "move section X before section Y"
- User asks to "reorder the sections" or "reorganize this document"
- User asks to "merge these two sections" or "combine these paragraphs"
- User asks to "split this section into two"
- User asks to rearrange content within the document
- User says "this part should come earlier/later"

## The core problem

Moving content in Word is a multi-step delete-then-insert operation. The danger: deleting paragraphs shifts all subsequent indices, so your insertion target may be wrong by the time you insert. Without a careful plan, you lose content or put it in the wrong place.

**The rule: always read → plan → execute in the right order → verify.**

## Key rules

### 1. Read the full document and map the sections

Before any restructuring, build a complete section map:

```
get_document()
```

Identify sections by their headings:

```
Section map:
  Section A: "Introduction"       — paragraphs 0-3
  Section B: "Background"         — paragraphs 4-8
  Section C: "Methodology"        — paragraphs 9-14
  Section D: "Results"            — paragraphs 15-22
  Section E: "Conclusion"         — paragraphs 23-26
```

### 2. Plan the move before executing

Write out the operation:
```
Move: Section C (paragraphs 9-14) → after Section A (after paragraph 3)
```

### 3. Copy-first, then delete

For moving sections, the safe order is:
1. **Read** the content of the paragraphs to move (save the text and styles)
2. **Insert** the content at the new location
3. **Re-read** the document (indices have shifted)
4. **Delete** the original content using updated indices

**Never delete first.** If the insert fails, you've lost the content.

### 4. Re-read after every structural change

After any insert or delete, paragraph indices shift. Always call `get_document()` again before the next operation.

### 5. One move at a time

If the user wants multiple sections reordered, do them one at a time. Complete each move (insert → verify → delete → verify) before starting the next.

## Step-by-step: Move a section

### Example: Move "Methodology" (paragraphs 9-14) to after "Introduction" (after paragraph 3)

**Phase 1: Read and plan**

1. **Read the document:**
   ```
   get_document()
   ```

2. **Identify the section boundaries:**
   - Source: paragraphs 9-14 (Methodology section, starting with Heading1 "Methodology")
   - Target: insert after paragraph 3 (end of Introduction)

3. **Record the content.** Note the text and style of each paragraph in the source section:
   ```
   paragraph 9:  "Methodology" (Heading1)
   paragraph 10: "We used a mixed-methods approach..." (Normal)
   paragraph 11: "Data collection occurred over..." (Normal)
   paragraph 12: "Quantitative Analysis" (Heading2)
   paragraph 13: "Statistical methods included..." (Normal)
   paragraph 14: "Qualitative Analysis" (Heading2)
   ```

**Phase 2: Insert at the new location**

4. **Insert the content at the target location:**
   ```
   insert_content(
     blocks=[
       { text: "Methodology", style: "Heading1" },
       { text: "We used a mixed-methods approach..." },
       { text: "Data collection occurred over..." },
       { text: "Quantitative Analysis", style: "Heading2" },
       { text: "Statistical methods included..." },
       { text: "Qualitative Analysis", style: "Heading2" }
     ],
     location="after_paragraph",
     paragraph_index=3
   )
   ```

**Phase 3: Re-read and delete the original**

5. **Re-read the document** to get updated indices:
   ```
   get_document()
   ```

6. **Find the original section** (its indices have shifted because we inserted 6 paragraphs above it). It was at 9-14, now it's at 15-20 (shifted by 6).

7. **Verify** the original content is still there by checking the text at the new indices.

8. **Delete the original:**
   ```
   delete_content(start_index=15, end_index=20)
   ```

**Phase 4: Verify**

9. **Read the document one final time:**
   ```
   get_document()
   ```

10. **Confirm:**
    - The moved section is in the right place
    - The heading hierarchy is still valid
    - No content was lost
    - No duplicate content exists

## Step-by-step: Merge two sections

### Example: Merge "Background" and "Context" into one section

1. **Read the document:**
   ```
   get_document()
   ```

2. **Identify the sections:**
   ```
   "Background" — paragraphs 4-7 (Heading1 + 3 body)
   "Context"    — paragraphs 8-11 (Heading1 + 3 body)
   ```

3. **Delete the second heading** (keep its body text):
   ```
   delete_content(paragraph_index=8)
   ```

4. **Verify:**
   ```
   get_document()
   ```
   The body paragraphs of "Context" now flow under "Background".

5. **Optionally rename** the merged section:
   ```
   search_document(query="Background", replace_with="Background and Context")
   ```

## Step-by-step: Split a section into two

### Example: Split a long "Analysis" section at paragraph 12

1. **Read the document:**
   ```
   get_document()
   ```

2. **Identify the split point.** Paragraph 12 starts the content that should become its own section.

3. **Insert a new heading before the split point.** If you know a unique anchor sentence at the split point, use `target_query` to skip the index lookup:
   ```
   insert_content(
     text="Detailed Analysis",
     style="Heading1",
     target_query="The methodology shifted after the pilot"
   )
   ```
   Otherwise insert by paragraph index:
   ```
   insert_content(
     text="Detailed Analysis",
     style="Heading1",
     location="before_paragraph",
     paragraph_index=12
   )
   ```

4. **Verify:**
   ```
   get_document()
   ```
   Confirm the new heading is in place and content flows correctly.

## Gotchas

- **Index shift is the #1 source of bugs.** After inserting 6 paragraphs at index 3, everything after index 3 shifts by 6. Always re-read after insert/delete.
- **Tables don't move with paragraphs.** If a section includes a table, you need to recreate it at the new location with `insert_table`, then delete the original. Tables are separate from the paragraph list.
- **Lists break when moved.** Moving list items with delete_content + insert_content loses list formatting. After inserting the text, call `manage_list` to reapply list formatting.
- **format_paragraph styles are preserved during insert** only if you set the style in the blocks array. If you omit the style, the inserted paragraph uses the default style.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Deleting before inserting | If insert fails, content is lost forever | Always insert first, verify, then delete the original |
| Not re-reading after a move | Indices are stale; next operation targets wrong paragraph | Call get_document() after every insert or delete |
| Moving multiple sections at once | Index shifts compound and become impossible to track | One move at a time, verify between each |
| Forgetting to move tables | Tables are separate from paragraph indices | Recreate tables with insert_table at the new location |
| Not verifying heading hierarchy after restructuring | Moving sections can break H1 → H2 → H3 sequence | Check hierarchy after every move |
| Copying text without styles | Moved section loses its formatting | Include style in each block of the insert_content call |
