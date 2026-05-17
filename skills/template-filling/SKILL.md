---
name: template-filling
description: Systematically find and fill placeholders in Word document templates. Use when the user provides a template with placeholders like {{name}}, [INSERT DATE], <COMPANY>, or similar markers, and wants them replaced with real values. Ensures no placeholder is missed across body, headers, and footers.
metadata:
  author: talk2view-word
  version: "1.0"
---

# Template Filling

## When to use

- User says "fill in this template" or "populate the template"
- Document contains placeholder patterns (`{{...}}`, `[INSERT ...]`, `<...>`, `___`, `XXXX`)
- User provides a list of values to insert into a document
- User asks to "personalize" or "customize" a standard document

## Key rules

### 1. Scan the ENTIRE document before replacing anything

Placeholders can appear in three places:
- **Document body** — most common
- **Headers/footers** — often overlooked
- **Tables** — cells may contain placeholders

Always do a full scan first. Never start replacing until you have a complete inventory.

### 2. Detect the placeholder pattern

Common patterns:
| Pattern | Example |
|---------|---------|
| Double curly braces | `{{client_name}}` |
| Square brackets | `[INSERT COMPANY NAME]` |
| Angle brackets | `<DATE>` |
| Underscores | `___________` |
| ALL CAPS markers | `COMPANY_NAME`, `XXXX` |

Read the document and identify which pattern is used. Don't assume — check.

### 3. Replace all occurrences, not just the first

`search_document` with `replace_with` replaces ALL matches. This is what you want — a placeholder like `{{client_name}}` may appear multiple times (in the body, in a table, in different paragraphs).

### 4. Verify zero remaining after each replacement

After replacing a placeholder, search for it again to confirm count is 0:
```
search_document(query="{{client_name}}")
// Should return: { "count": 0, "matches": [] }
```

### 5. Headers and footers require separate handling

`search_document` only searches the document body. Placeholders in headers/footers must be replaced with `set_header_footer`. For multi-section templates where the header/footer is identical across sections, apply in ONE call using `section_indices`:

```
set_header_footer(
  type="header",
  text="Acme Corporation — Website Redesign Proposal",
  section_indices=[0, 1, 2]
)
```

If the footer needs page numbers AND brand text, fuse via `prefix_text`/`suffix_text` on `insert_page_numbers` — don't call `set_header_footer` on the same footer afterwards (it would clear the numbers).

## Step-by-step: Fill a template

### Phase 1: Inventory

1. **Read the full document:**
   ```
   get_document()
   ```

2. **Identify the placeholder pattern.** Scan the text for `{{`, `[INSERT`, `<`, `___`, etc.

3. **Search for each placeholder** to get a count:
   ```
   search_document(query="{{client_name}}")
   search_document(query="{{date}}")
   search_document(query="{{project_title}}")
   ```

4. **Check headers and footers** by looking for placeholder text in the full document text. Since headers/footers are not in the body text returned by `get_document`, ask the user if there are placeholders in headers/footers, or proactively check by looking at the document context.

5. **Build a replacement map:**
   ```
   {{client_name}}    → "Acme Corporation"     (found 3 times)
   {{date}}           → "March 15, 2026"       (found 2 times)
   {{project_title}}  → "Website Redesign"     (found 1 time)
   ```

### Phase 2: Replace

6. **Replace each placeholder** one at a time:
   ```
   search_document(query="{{client_name}}", replace_with="Acme Corporation")
   search_document(query="{{date}}", replace_with="March 15, 2026")
   search_document(query="{{project_title}}", replace_with="Website Redesign")
   ```

7. **Handle headers/footers** if they contained placeholders:
   ```
   set_header_footer(type="header", text="Acme Corporation — Website Redesign Proposal")
   set_header_footer(type="footer", text="Confidential — March 15, 2026")
   ```

### Phase 3: Verify

8. **Search for any remaining placeholders.** Search for the pattern markers themselves:
   ```
   search_document(query="{{")
   search_document(query="}}")
   search_document(query="[INSERT")
   ```

9. **If count > 0**, you missed one. Read the document around that location and fill it.

10. **Report to the user:** List which placeholders were filled and how many replacements were made.

## Handling missing values

If the user provides values for some placeholders but not all:
1. Fill what you can
2. Report the remaining unfilled placeholders with their locations
3. Ask the user for the missing values

**Never guess placeholder values.** If the user didn't provide `{{phone_number}}`, don't invent one.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Replacing placeholders one at a time without inventory | You miss some and don't know until the user spots them | Full scan first, then replace |
| Forgetting to check headers/footers | Placeholder remains visible on every page | Check and handle separately with `set_header_footer` |
| Not verifying after replacement | A partial match or encoding issue could leave remnants | Search for the placeholder pattern markers (`{{`, `[INSERT`) after all replacements |
| Guessing missing values | User gets wrong data in their document | Report unfilled placeholders; ask for values |
| Replacing too broadly | `search_document(query="name")` replaces the word "name" everywhere, not just `{{name}}` | Use the full placeholder string including delimiters |
