---
name: document-review
description: Review a Word document and leave comments anchored to specific text. Use when the user asks to "review", "give feedback on", "proofread", or "critique" a document. The agent reads the full document, identifies issues, and adds comments at the exact locations where problems exist — just like a human reviewer would in Word.
metadata:
  author: talk2view-word
  version: "1.1"
allowed-tools: get_document get_comments add_comment search_document
---

# Document Review

## When to use

- User asks to "review this document", "give me feedback", or "proofread this"
- User asks to "check for issues" or "what needs improvement?"
- User asks for a specific type of review (grammar, clarity, structure, tone)
- User shares a document and asks "what do you think?"

## Key rules

### 1. Always read the full document first

Before making any comments, understand the entire document:
```
get_document()
```

Read through all paragraphs. Understand the purpose, structure, audience, and tone. A comment that makes sense in isolation may be wrong in context.

### 2. Check for existing comments before adding new ones

```
get_comments()
```

If the document already has comments:
- Don't duplicate feedback that existing comments already cover
- Be aware of resolved vs unresolved comments — resolved issues have been addressed
- Your comments should add new value, not repeat existing feedback

### 3. Use unique, exact anchor text

The `add_comment` tool searches for text and anchors the comment to the **first match**. If your anchor text appears multiple times, the comment may land on the wrong instance.

**Do this — use enough text to be unique:**
```
add_comment(
  anchor="The project was delivered ahead of schedule and significantly under budget",
  comment="Consider adding specific numbers: how many days ahead? What percentage under budget?"
)
```

**Not this — too short, may match multiple locations:**
```
add_comment(
  anchor="the project",
  comment="Add specifics"
)
```

**Rule of thumb:** Use 5-15 words from the target sentence. Enough to be unique, short enough to be readable.

### 4. Write actionable comments

Every comment should tell the author WHAT to do and WHY. A vague comment wastes the author's time.

**Good comments:**
- "This paragraph repeats the point made in the Executive Summary. Consider removing it or adding new information."
- "The passive voice here ('it was decided') obscures who made the decision. Change to active: 'The board decided...'"
- "'Significant' is vague. Replace with a specific metric, e.g., '23% increase'."

**Bad comments:**
- "Fix this" — fix what?
- "Unclear" — what's unclear and how should it be fixed?
- "Needs work" — what kind of work?

### 5. Categorise your review

Organise comments by type. Address them in this priority order:

| Priority | Category | What to look for |
|----------|----------|-----------------|
| 1 | **Factual/logical** | Incorrect claims, contradictions, logical gaps, missing evidence |
| 2 | **Structural** | Missing sections, wrong heading levels, poor paragraph ordering, content under wrong heading |
| 3 | **Clarity** | Ambiguous sentences, jargon without explanation, passive voice that obscures meaning |
| 4 | **Grammar/spelling** | Typos, subject-verb disagreement, tense inconsistency, punctuation |
| 5 | **Style/polish** | Repetitive phrasing, wordy sentences, inconsistent capitalisation, tone mismatches |

### 6. Don't over-comment

A document with 40 comments is overwhelming. Aim for the most impactful feedback:
- **Short document (1-2 pages):** 3-8 comments
- **Medium document (3-10 pages):** 5-15 comments
- **Long document (10+ pages):** 10-20 comments, focused on the most important issues

If you find a recurring issue (e.g., passive voice everywhere), comment on it once and note that it applies throughout, rather than commenting on every instance.

## Step-by-step: Full document review

### Phase 1: Read and understand

1. **Read the document:**
   ```
   get_document()
   ```

2. **Check existing comments:**
   ```
   get_comments()
   ```

3. **Identify the document's purpose and audience.** Is this a proposal, report, email, memo? Who is reading it? This shapes what feedback is appropriate.

### Phase 2: Analyse and plan

4. **Make a mental pass through the document.** Note issues in each category:
   - Any factual or logical problems?
   - Is the structure sound? (heading hierarchy, section ordering)
   - Are there clarity issues?
   - Grammar/spelling errors?
   - Style/polish items?

5. **Prioritise.** Select the most impactful issues. Don't comment on every minor style preference if there are structural problems.

### Phase 3: Add comments

6. **Add comments one at a time,** starting with highest priority:

   ```
   add_comment(
     anchor="Revenue increased significantly in Q3",
     comment="'Significantly' is vague. Specify the actual figure or percentage, e.g., 'Revenue increased 34% in Q3.'"
   )
   ```

   ```
   add_comment(
     anchor="It was determined that the approach should be revised",
     comment="Passive voice obscures accountability. Who determined this? Rewrite as: 'The steering committee determined...'"
   )
   ```

   ```
   add_comment(
     anchor="Conclusion",
     comment="This conclusion introduces new information (the market expansion plan) that wasn't discussed in the body. Either add a section about market expansion above, or remove this from the conclusion."
   )
   ```

7. **For recurring issues, comment once and generalise:**
   ```
   add_comment(
     anchor="the first instance of passive voice",
     comment="Passive voice is used frequently throughout this document (I counted 8 instances). Consider rewriting these in active voice for more direct, engaging prose. This comment applies to all similar constructions."
   )
   ```

### Phase 4: Summarise

8. **Tell the user what you found.** After adding comments, provide a brief verbal summary:

   ```
   I've reviewed the document and added [N] comments:
   - 2 structural issues (missing conclusion context, heading level skip)
   - 3 clarity issues (vague metrics, passive voice, undefined acronym)
   - 1 grammar fix (subject-verb disagreement in paragraph 12)

   The most important issue is [X]. The document is otherwise well-structured.
   ```

## Review types

If the user requests a specific type of review, narrow your focus:

### Grammar/proofreading review
Focus only on: spelling, grammar, punctuation, tense consistency, subject-verb agreement. Do NOT comment on style, structure, or content.

### Clarity review
Focus on: ambiguous sentences, jargon, passive voice, undefined acronyms, sentences over 30 words. Do NOT comment on grammar or structure.

### Structure review
Focus on: heading hierarchy, section ordering, missing sections, content misplacement, introduction/conclusion quality. Do NOT comment on sentence-level issues.

### Executive review
Focus on: Is the key message clear in the first paragraph? Can a busy reader skim headings and get the gist? Are action items explicit? Is the document too long?

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Commenting without reading the full document | You miss context — a "problem" in paragraph 3 may be explained in paragraph 8 | Always `get_document()` first and read everything |
| Using short anchor text | Comment lands on the wrong text instance | Use 5-15 words, enough to be unique |
| Writing vague comments | Author doesn't know what to change | Always include WHAT to change and WHY |
| Commenting on every minor issue | Overwhelms the author; important feedback gets buried | Prioritise by category; limit total comment count |
| Not checking existing comments | Duplicate or contradicting feedback | Always `get_comments()` first |
| Skipping the summary | User has to read every comment to understand the review | Provide a verbal summary after adding comments |
