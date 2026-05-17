---
name: pre-send-review
description: Final go/no-go checklist before a document is sent, submitted, or published. Use when the user says "is this ready?", "final check", "review before sending", or asks you to verify a document is complete. Distinct from consistency-check — this skill focuses on completeness and deliverability, not style consistency.
metadata:
  author: talk2view-word
  version: "2.0"
allowed-tools: get_document get_comments search_document
---

# Pre-Send Review

## When to use

- User asks "is this document ready to send?"
- User asks for a "final check", "last review", or "pre-send review"
- User is about to email, submit, or publish a document
- You just finished building or editing a document and want to verify before telling the user it's done

## How this differs from consistency-check

| consistency-check | pre-send-review |
|-------------------|-----------------|
| Finds style violations and structural issues | Verifies nothing is missing or broken |
| "Your headings skip from H1 to H3" | "You have no page numbers" |
| Run during editing | Run when editing is finished |
| Reports issues to fix | Reports a pass/fail checklist |

## The checklist

Work through every check in order. Each check produces a **PASS** or **FAIL** with details.

### Check 1: No placeholder text remains

Search for placeholder patterns. Two wildcard calls cover most cases, then a few literal scans for bare tokens.

```
// {{…}} placeholders
search_document(query="\\{\\{*\\}\\}", match_wildcards=true)

// [INSERT …], [TODO …], bracketed placeholders
search_document(query="\\[*\\]", match_wildcards=true)

// Underscore fill-in lines
search_document(query="___")
```

Only run literal TODO/TBD/FIXME/XXX searches if the bracketed search missed them (Word wildcards don't support `|` alternation).

- **PASS** if all return count 0
- **FAIL** if any return count > 0 — list each placeholder found

### Check 2: Document has content

```
get_document()
```

- **PASS** if `total_paragraphs` > 0 and the text is not empty
- **FAIL** if the document body is empty or contains only whitespace

### Check 3: Document has a title or heading

From the `get_document()` response, check if any paragraph has style `Title` or `Heading1`.

- **PASS** if at least one Title or Heading1 exists
- **FAIL** if the document has no top-level heading — "Document has no title or top-level heading"

### Check 4: No excessive empty paragraphs

From the paragraph list, count paragraphs with empty text.

- **PASS** if 0-1 empty paragraphs (one trailing empty paragraph is normal)
- **FAIL** if 2+ empty paragraphs — list their indices. "Empty paragraphs at indices [5, 6, 12] — use paragraph spacing instead of blank lines"

### Check 5: Headers and footers are set (if expected)

This check depends on document type. For formal documents (reports, proposals, letters), headers and/or footers are expected.

There's no tool to read header/footer content, so this check is advisory:
- Ask the user: "Does this document need headers/footers? I can set them with `set_header_footer` if needed."
- If the user previously set headers/footers during this session, mark as **PASS**.
- If no header/footer work was done on a formal document, mark as **REVIEW** — "No headers or footers were set. Should this document have them?"

### Check 6: Page numbers are present (if multi-page)

For documents with substantial content (more than ~10 paragraphs or the user mentioned multiple pages):

- If `insert_page_numbers` was called during this session, **PASS**
- If not, mark as **REVIEW** — "No page numbers were added. Should this document have page numbers?"

### Check 7: Heading hierarchy is valid

From the paragraph list, check heading levels are sequential (no H1 → H3 skips).

- **PASS** if hierarchy is valid
- **FAIL** if any heading level is skipped — list the violations

### Check 8: Document properties are set (if relevant)

From the `get_document()` response, check the `properties` object:

- If `title` is empty and the document has a Title-style paragraph, mark as **REVIEW** — "Document properties 'title' is empty. Should it match the document title?"
- If `author` is empty, mark as **REVIEW** — "Document properties 'author' is not set."
- Otherwise **PASS**

## Step-by-step: Run the review

1. **Read the document:**
   ```
   get_document()
   ```

2. **Run placeholder checks (Check 1):**
   ```
   search_document(query="\\{\\{*\\}\\}", match_wildcards=true)
   search_document(query="\\[*\\]",       match_wildcards=true)
   search_document(query="___")
   ```

3. **Analyze the paragraph list** for Checks 2-4 and 7.

4. **Assess Checks 5-6** based on session context and document size.

5. **Check properties** for Check 8.

6. **Present the report** to the user:

```
Pre-Send Review:

  PASS  No placeholder text found
  PASS  Document has content (24 paragraphs)
  PASS  Document has a title ("Quarterly Report")
  PASS  No excessive empty paragraphs
  REVIEW  No headers/footers set — should this document have them?
  REVIEW  No page numbers — should this document have them?
  PASS  Heading hierarchy is valid
  REVIEW  Document 'author' property is empty

Result: 4 passed, 0 failed, 3 items to review
```

7. **Offer to fix** any FAIL or REVIEW items:
   - "I can add page numbers — where would you like them? (header or footer, what format?)"
   - "I can set the header text — what should it say?"

## Decision framework

| Result | What to tell the user |
|--------|----------------------|
| All PASS | "Document looks ready to send." |
| All PASS + some REVIEW | "Document looks good. A few optional items to consider: [list REVIEW items]" |
| Any FAIL | "Found [N] issues that should be fixed before sending: [list FAIL items]. Want me to fix them?" |

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Skipping the placeholder check | A `{{client_name}}` in the document reaches the recipient | Always search for common placeholder patterns |
| Reporting too many REVIEW items | Overwhelms the user with optional suggestions | Limit REVIEW items to genuinely relevant ones based on document type |
| Automatically fixing issues without asking | User might disagree with the fix | Report issues, then ask before fixing |
| Running this instead of consistency-check | They serve different purposes; this checks completeness, not style | Run both if the user wants a thorough review |
| Only checking `{{` pattern | Misses `[INSERT`, `TODO`, `___` patterns | Search for all common placeholder formats |
