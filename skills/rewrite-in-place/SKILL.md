---
name: rewrite-in-place
description: Rewrite or edit existing document text while preserving surrounding formatting, styles, and structure. Use when the user asks to rephrase, reword, shorten, expand, improve, or change the tone of existing content. Without this skill, agents tend to destroy formatting when replacing text.
metadata:
  author: talk2view-word
  version: "2.0"
---

# Rewrite In Place

## When to use

- User asks to "rewrite", "rephrase", "reword", "improve", "shorten", or "expand" existing text
- User asks to change tone (formal, casual, technical, simple)
- User asks to fix grammar or spelling in a section
- User highlights text and asks to change it

## The core problem

When an agent replaces text, it often destroys the surrounding formatting. Replacing a Heading2 paragraph's text without preserving style turns it into Normal. Replacing text mid-paragraph can strip bold/italic.

**The rule: read before you write, and use the tool that preserves the paragraph's style.**

## Key rules

### 1. Know what you're rewriting

`get_document()` is assumed (see SYSTEM_PROMPT "Read before write"). From the response, note each target paragraph's **style**, **index**, and the surrounding **voice** so the replacement fits.

### 2. Choose the right replacement strategy

There are three strategies. Almost all rewrites fit Strategy A or B.

**Strategy A: Targeted replacement (no selection needed).** The new `target_query` on `insert_content` finds the exact text and replaces it — one call, no prerequisite `get_selection`.

```
insert_content(
  text="this improved phrase",
  target_query="this old phrase"
)
```

Use this for inline edits, phrasings, and short sentence swaps. If the match is a full paragraph AND you pass a `style`, the old paragraph is replaced by a new styled paragraph cleanly.

**Strategy B: Find-and-replace across the document.** When a specific term must change everywhere (e.g., renaming a product):

```
search_document(query="old term", replace_with="new term")
```

Paragraph style is automatically preserved. For rewrite-AND-restyle in one call, pass `replace_format`:

```
search_document(
  query="TODO: revise intro",
  replace_with="DONE: intro revised",
  replace_format={ bold: true, color: "008000" }
)
```

**Strategy C: Whole-paragraph rewrite.** Use `search_document` with the full paragraph text as the query — same as B but the query happens to be the entire paragraph. Style is preserved.

For **deleting** text entirely (not replacing), use `delete_content(paragraph_index=…)` or `search_document(query, replace_with="")`.

### 3. Use `get_selection` only when the user explicitly highlighted text

`get_selection` is for UX ("act on what I picked"), not for routing. If the user hasn't selected anything, don't call `get_selection` just to discover what to rewrite — call `get_document` and use `target_query`.

When the user DID select text, the one-call form is:

```
// User highlighted "this old phrase"
get_selection()            // verify selection exists
insert_content(text="this improved phrase", location="replace_selection")
```

### 4. Match the document's voice

Before rewriting, scan the surrounding text for:
- **Formality** — "Let's look at…" vs "The following section examines…"
- **Sentence length** — short and punchy, or long and detailed
- **Person** — first ("we"), second ("you"), third ("the team")
- **Technical level** — jargon-heavy or plain language

Match it.

### 5. Don't over-edit

If the user asks to "improve" a paragraph, don't rewrite the document. Change only what was requested.

## Step-by-step: Rewrite a sentence anywhere in the document

```
get_document()
// locate the sentence

insert_content(
  text="The project finished ahead of schedule and 15% under budget.",
  target_query="The project was completed on time and under budget."
)
```

One call. No index arithmetic.

## Step-by-step: Rename a term globally AND restyle it

```
search_document(
  query="Project Phoenix",
  replace_with="Project Orion",
  replace_format={ bold: true }
)
```

One call replaces every mention and applies formatting to the replacement.

## Step-by-step: Change tone of a whole section

1. `get_document()` — find the section's paragraph range.
2. Rewrite paragraph-by-paragraph with `insert_content(target_query=…, text=…)`, using the full old text as the target.
3. Do NOT touch heading paragraphs unless explicitly asked.

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Calling `get_selection` before every rewrite | Selection is only relevant if the user highlighted something | Use `insert_content(target_query=…)` instead |
| Calling `select_text` then `insert_content(replace_selection)` | Two calls when one suffices | Use `target_query` on `insert_content` |
| Deleting a paragraph and inserting a new one | Loses original style, spacing, position | Use `insert_content(target_query=…)` or `search_document` |
| Rewriting more than was asked | User asked to fix one sentence; you rewrote the section | Change only what was requested |
| Replacing text that appears multiple times | `search_document` replaces ALL matches | Use a longer, unique query or pass `target_query` + `match_index` to `insert_content` |
| Ignoring document voice | New text sounds out of place | Analyze surrounding paragraphs and match style |
