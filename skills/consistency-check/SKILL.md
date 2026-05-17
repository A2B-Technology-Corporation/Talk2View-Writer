---
name: consistency-check
description: Audit a Word document for formatting inconsistencies, style violations, and structural issues. Use when the user asks to "review", "check", "audit", or "clean up" a document, or before finalising a document for delivery. Catches heading hierarchy violations, mixed formatting, orphaned content, and other quality issues.
metadata:
  author: talk2view-word
  version: "2.0"
allowed-tools: get_document get_comments search_document
---

# Consistency Check

## When to use

- User asks to "review", "check", or "audit" the document
- User asks to "clean up" or "polish" a document
- Before the user sends or publishes a document
- After extensive editing to catch accumulated inconsistencies
- User asks "is this document ready?" or "does this look right?"

## What to check

This skill defines a structured audit. Work through each check in order, collect all issues, then report them as a single list.

### Check 1: Heading hierarchy

**Rule:** Heading levels must be sequential. No skipping (H1 → H3) or orphan headings.

**Procedure:**
```
get_document()
```

Scan the paragraph list for style fields containing `Heading`. Track the levels:

```
Good:                    Bad:
Heading1                 Heading1
  Heading2                   Heading3  ← skipped H2
    Heading3               Heading2
  Heading2                   Heading4  ← skipped H3
```

**Report format:** `"Heading hierarchy: paragraph [index] is Heading3 but follows Heading1 (skipped Heading2)"`

### Check 2: Style consistency

**Rule:** Same-level content should use the same style. If most body text uses `Normal`, a paragraph using no style (or a different base style) is an inconsistency.

**Procedure:** From the `get_document()` results, tally styles:
- Count how many paragraphs use each style
- Flag outliers (e.g., 1 paragraph is "Normal" among 20 "NoSpacing" paragraphs)

**Report format:** `"Style outlier: paragraph [index] uses 'NoSpacing' while all other body text uses 'Normal'"`

### Check 3: Empty paragraphs

**Rule:** Empty paragraphs (blank lines used for spacing) are almost always wrong. Spacing should be controlled through `space_before` and `space_after` on `format_paragraph`, not by inserting blank paragraphs.

**Procedure:** Scan the paragraph list for paragraphs where `text` is empty or whitespace-only.

**Exception:** A single empty paragraph at the very end of the document is normal.

**Report format:** `"Empty paragraph at index [index] — use paragraph spacing instead of blank lines"`

### Check 4: Heading followed immediately by heading

**Rule:** A heading should be followed by body content, not another heading at the same or higher level. Two consecutive headings with no content between them suggest missing content or incorrect structure.

**Procedure:** Scan for adjacent paragraphs where both have heading styles.

**Exception:** A Heading1 followed by a Heading2 is acceptable (section → subsection).

**Report format:** `"Missing content: Heading2 at index [index] is immediately followed by another Heading2 at index [index+1] with no body text between them"`

### Check 5: Inconsistent capitalisation in headings

**Rule:** Headings should use consistent capitalisation — either Title Case or Sentence case, not a mix.

**Procedure:** Check all heading paragraphs:
- Title Case: "Quarterly Business Review"
- Sentence case: "Quarterly business review"
- ALL CAPS: "QUARTERLY BUSINESS REVIEW"
- Mixed: problem

Determine the dominant pattern and flag deviations.

**Report format:** `"Capitalisation: heading at index [index] uses sentence case while other headings use title case"`

### Check 6: Placeholder remnants

**Rule:** No template placeholders should remain in a finished document.

**Procedure (wildcard-based, two calls cover most patterns):**

```
// {{…}} style placeholders — one call covers every variant
search_document(query="\\{\\{*\\}\\}", match_wildcards=true)

// [INSERT …], [TODO …], [TBD …] style — bracketed placeholders
search_document(query="\\[*\\]", match_wildcards=true)
```

Word's wildcard syntax doesn't support `|` alternation, so unbracketed tokens (`TODO`, `FIXME`, `TBD`, `XXX`) each need their own literal search. Only run them if the bracketed searches missed anything:

```
search_document(query="TODO")
search_document(query="TBD")
search_document(query="FIXME")
```

**Report format:** `"Placeholder found: '{{client_name}}' appears [count] times — was the template fully filled?"`

## Step-by-step: Run a full audit

1. **Read the full document:**
   ```
   get_document()
   ```

2. **Run checks 1-5** against the paragraph list. Collect all issues into a list.

3. **Run check 6** using `search_document` calls. Add any findings to the list.

4. **Present the report** to the user as a structured list:
   ```
   Document Audit — [N] issues found:

   Heading Hierarchy:
   - Paragraph 7: Heading3 follows Heading1 (skipped Heading2)

   Empty Paragraphs:
   - Paragraph 12: empty paragraph (use spacing instead)
   - Paragraph 13: empty paragraph (use spacing instead)

   Placeholders:
   - "{{date}}" found 2 times — template may not be fully filled

   Style Consistency:
   - No issues

   Heading Capitalisation:
   - No issues
   ```

5. **Ask the user** if they want you to fix any of the issues.

## Fixing issues

If the user asks to fix the issues, apply fixes in **reverse index order** (highest index first) to avoid index shifting:

```
// Delete empty paragraphs — highest index first
delete_content(start_index=12, end_index=13)

// Fix heading hierarchy
format_paragraph(paragraph_index=7, style="Heading2")

// Fix placeholders
search_document(query="{{date}}", replace_with="March 15, 2026")
```

Note: After deleting paragraphs, indices shift. Call `get_document()` again before making further index-based changes.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Fixing issues without reporting first | User doesn't know what changed or why | Report first, then ask permission to fix |
| Fixing in forward index order | Insertions/deletions shift later indices | Fix in reverse order (highest index first) |
| Reporting style issues on headings | Headings use heading styles by design; they're not "inconsistent" with body text | Only compare like with like (body vs body, headings vs headings) |
| Missing placeholder search | Some placeholders use unusual patterns | Search for multiple patterns: `{{`, `[INSERT`, `TODO`, `TBD`, `XXX` |
| Over-reporting | 50 minor issues overwhelms the user | Prioritise: heading hierarchy and placeholder issues first, cosmetic issues last |
