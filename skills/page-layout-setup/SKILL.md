---
name: page-layout-setup
description: Configure page layout for common document types — margins, orientation, paper size, headers, footers, and page numbers as a coordinated setup. Use when the user asks to "set up the page", "format as a letter", "make it landscape", "set margins", "create a professional layout", or mentions any document template format (report, memo, letter, resume, proposal). Coordinates set_page_setup, set_header_footer, and insert_page_numbers into a single coherent layout.
metadata:
  author: talk2view-word
  version: "1.1"
compatibility: set_page_setup requires Word desktop; other layout tools work in Word Online.
---

# Page Layout Setup

## When to use

- User asks to "set up the page" or "change the layout"
- User asks to "make this a letter format" or "set up a report template"
- User mentions margins, orientation, or paper size
- User asks for a "professional layout" or "standard formatting"
- User wants to set up headers, footers, and page numbers together
- Starting a new document and the user specifies a document type

## The core problem

Page layout involves three tools that must work together without conflicting:
- `set_page_setup` — margins, orientation, paper size (desktop only)
- `set_header_footer` — header and footer text
- `insert_page_numbers` — dynamic page numbers

Both `set_header_footer` and `insert_page_numbers` clear their target before writing. Combine brand text with page numbers in one call via `insert_page_numbers(prefix_text, suffix_text)` — see the `headers-footers-page-numbers` skill for the full conflict rules.

## Key rules

### 1. Plan the layout before calling any tool

Decide upfront what goes where:

```
Layout plan:
  Page: portrait, 1-inch margins, letter size
  Header: "Company Name — Document Title"
  Footer: page numbers (Page X of Y), centered
```

**Golden rule: text and page numbers go in DIFFERENT locations.**

### 2. Execute in order: page setup → header → footer/page numbers

```
1. set_page_setup (if needed)
2. set_header_footer for header text
3. insert_page_numbers for footer (or vice versa)
```

### 3. set_page_setup is desktop-only

`set_page_setup` uses WordApiDesktop 1.3 — it does NOT work in Word Online. If it fails, inform the user: "Page margins and orientation require the Word desktop app. Headers, footers, and page numbers still work."

### 4. Use header_footer_type for first-page differences

Many documents need a different first page (title page with no header, or a different header):

```
set_header_footer(type="header", text="Title Page Header", header_footer_type="firstPage")
set_header_footer(type="header", text="Standard Header", header_footer_type="primary")
```

## Standard layouts

### Professional report

```
set_page_setup(orientation="portrait", top_margin=72, bottom_margin=72, left_margin=72, right_margin=72)
set_header_footer(type="header", text=" ", header_footer_type="firstPage")
set_header_footer(type="header", text="Report Title — Company Name")
insert_page_numbers(location="footer", alignment="center", format="Page {PAGE} of {NUMPAGES}")
```

### Business letter

```
set_page_setup(orientation="portrait", top_margin=72, bottom_margin=72, left_margin=72, right_margin=72)
// Letters typically have no header/footer — skip unless requested
```

### Memo

```
set_page_setup(orientation="portrait", top_margin=72, bottom_margin=72, left_margin=72, right_margin=72)
set_header_footer(type="header", text="MEMORANDUM")
insert_page_numbers(location="footer", alignment="right", format="{PAGE}")
```

### Landscape data sheet

```
set_page_setup(orientation="landscape", top_margin=54, bottom_margin=54, left_margin=54, right_margin=54)
set_header_footer(type="header", text="Data Sheet — Confidential")
insert_page_numbers(location="footer", alignment="center", format="{PAGE} / {NUMPAGES}")
```

### Resume / CV

```
set_page_setup(orientation="portrait", top_margin=54, bottom_margin=54, left_margin=54, right_margin=54)
// Narrow margins to maximize space — no header/footer needed
```

### Proposal with title page

```
set_page_setup(orientation="portrait", top_margin=72, bottom_margin=72, left_margin=72, right_margin=72)
set_header_footer(type="header", text=" ", header_footer_type="firstPage")
set_header_footer(type="header", text="Proposal — Company Name")
set_header_footer(type="footer", text="Confidential", header_footer_type="firstPage")
insert_page_numbers(location="footer", alignment="center", format="Page {PAGE} of {NUMPAGES}")
```

## Step-by-step: Set up a page layout

1. **Ask the user** (if not clear) what type of document they want.

2. **Read the document for context:**
   ```
   get_document()
   ```
   Check section count and whether content already exists.

3. **Plan the layout.** Decide margins, orientation, header text, footer text, page number placement.

4. **Set page layout (desktop only):**
   ```
   set_page_setup(
     orientation="portrait",
     top_margin=72,
     bottom_margin=72,
     left_margin=72,
     right_margin=72
   )
   ```
   If this fails with "not available in Word Online", continue with headers/footers (those still work).

5. **Set header:**
   ```
   set_header_footer(type="header", text="Header Text Here")
   ```

6. **Set page numbers in footer:**
   ```
   insert_page_numbers(
     location="footer",
     alignment="center",
     format="Page {PAGE} of {NUMPAGES}"
   )
   ```

7. **For different first page (optional):**
   ```
   set_header_footer(type="header", text=" ", header_footer_type="firstPage")
   ```

8. **Confirm to the user** what was set up.

## Margin reference

Common margin sizes in points (72pt = 1 inch):

| Margin style | Points | Inches |
|-------------|--------|--------|
| Normal | 72 | 1.0" |
| Narrow | 36 | 0.5" |
| Moderate | 54 top/bottom, 54 left/right | 0.75" |
| Wide | 72 top/bottom, 144 left/right | 1.0" / 2.0" |

## Gotchas

- **set_page_setup is desktop-only.** On Word Online, this tool returns an error. Headers, footers, and page numbers still work — only margins and orientation are affected.
- **Don't put text and page numbers in the same location.** If header has text, page numbers must go in footer (or vice versa). Each tool CLEARS its target before writing.
- **insert_page_numbers is called ONCE.** Multiple calls overwrite — you don't get duplicate page numbers.
- **Header/footer types** (firstPage, evenPages) may not display differently on Word Online even though the content is set.
- **Multi-section documents** need headers/footers set per section. Check `get_document()` for section count and use `section_index` to target each one.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Putting header text and page numbers both in footer | insert_page_numbers overwrites the footer text | Put text in header, page numbers in footer (or vice versa) |
| Calling set_page_setup on Word Online | API not available — throws an error | Skip page setup or inform user they need desktop Word |
| Setting margins without specifying all four | Unset margins keep their current value (which may be inconsistent) | Set all four margins together for consistency |
| Calling insert_page_numbers multiple times | Each call overwrites the previous | Call exactly once |
| Not checking section count | Page setup applies to section 0 only by default | Use get_document() to check sections; set each section if needed |
