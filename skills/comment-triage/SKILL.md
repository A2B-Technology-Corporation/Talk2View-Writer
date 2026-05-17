---
name: comment-triage
description: Work through existing comments in a Word document — fix the flagged issues, leave a reply explaining what was done, and RESOLVE (not delete) each comment so the record is preserved. Use when the user asks to "address the comments", "fix the feedback", "go through my comments", "respond to the reviewer", or "close out the comments". Default workflow: fix → reply → resolve. Never delete unless the user explicitly asks.
metadata:
  author: talk2view-word
  version: "1.0"
allowed-tools: get_document get_comments manage_comment add_comment search_document insert_content format_text format_paragraph
---

# Comment Triage

## When to use

- User says "go through the comments", "address the feedback", "fix the review notes", "resolve the comments"
- User mentions "reviewer asked me to…" or references existing comments they want actioned
- You (the agent) previously left comments via `document-review` and are now being asked to fix them

## The core rule

**Fix → `resolve_with_reply`.** After the document change, make ONE call that both replies (describing what you did) and resolves in the same operation:

```
manage_comment(
  comment_id="<id>",
  action="resolve_with_reply",
  text="Rephrased opening sentence. New text: 'Revenue grew 12% in Q1.'"
)
```

This is the atomic closure. Don't split it into a separate `reply` + `resolve` — `resolve_with_reply` cannot forget either half.

Never use `action="delete"` just because you fixed the issue. Deleting erases the audit trail. Only delete when the user explicitly says "delete the comments" or "remove the comments."

## Key principles

### 1. Read all comments before acting

```
get_comments()
```

Note each comment's `id`, `anchor_text`, and `comment` content. Plan the full sequence of fixes before making any edits.

### 2. Fix the document issue first

Apply the text/formatting change that addresses the comment. Use whatever tool fits: `search_document`, `insert_content(target_query=…)`, `format_text`, `format_paragraph`.

### 3. Close with `resolve_with_reply`

Immediately after the document change, close the comment in one atomic call:

```
manage_comment(
  comment_id="<id from get_comments>",
  action="resolve_with_reply",
  text="Rephrased opening sentence to lead with the Q1 result. New text: 'Revenue grew 12% in Q1, driven by…'"
)
```

Reply style:
- **One sentence** describing the action taken
- Include the new text (or a preview of it) in quotes where helpful
- Do not apologize, editorialize, or summarize the original comment
- If the fix is partial or debatable, say so: "Softened the tone in paragraphs 4-6 but left paragraph 7 as written — flag if you want that one changed too."

The comment stays in the document with the original note, your reply, and a "resolved" status. Reviewers can unresolve if they disagree.

### 4. If you can't fix it, reply WITHOUT resolving

If the comment asks for something you can't do (ambiguous, missing info, out of scope):
- Use plain `action="reply"` (not `resolve_with_reply`) explaining what's blocking: `manage_comment(action="reply", text="Need clarification: which table should this reference? Reviewer didn't specify.")`
- **Leave the comment unresolved** so the user sees it's still open.
- Report the list of unresolved items to the user at the end.

### 5. IDs can shift — always fetch fresh

Comment IDs can change between sessions. Always call `get_comments()` at the start of a triage pass, not earlier in a long session.

## Step-by-step: Full triage pass

1. **Fetch all comments:**
   ```
   get_comments()
   ```
   Returns an array of `{id, anchor_text, comment, author, resolved, replies?}`. Skip any where `resolved: true`.

2. **Plan the fix sequence.** For each open comment, decide:
   - What change does it ask for?
   - Which tool applies it (`search_document` for replacement, `format_text` for emphasis, etc.)?
   - Is the fix obvious or does it need clarification?

3. **Work through comments one by one.** For each comment:

   a. Apply the fix (document-mutating tool call).

   b. Close the comment with one atomic call:
   ```
   manage_comment(
     comment_id="<id>",
     action="resolve_with_reply",
     text="<one sentence describing what you did>"
   )
   ```

4. **Report back to the user.** Summarize:
   ```
   Addressed 5 of 7 comments:
     ✓ Rephrased opening (id abc123)
     ✓ Fixed typo in revenue figure (id def456)
     ✓ Added citation to claim in §3 (id ghi789)
     ✓ Shortened paragraph 4 per suggestion (id jkl012)
     ✓ Matched heading case (id mno345)

   Left open (need your input):
     ? Comment at "we should consider" — reviewer asked for specifics but didn't say which alternative; replied asking for clarification
     ? Comment at "Table 2" — reviewer flagged inconsistency but I can't see which column they mean
   ```

## Reply wording — good vs bad

| Bad | Good |
|-----|------|
| "Done." | "Rephrased to: 'Revenue grew 12% in Q1.'" |
| "Fixed." | "Replaced 'utilize' with 'use' in paragraphs 2, 5, and 8." |
| "Sorry about that, I've changed it." | "Added the missing citation — Smith et al. 2024." |
| "Agreed, updating now." | "Moved the timeline table above the budget section as requested." |
| (empty reply, then resolve) | Always leave a reply before resolving |

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Deleting the comment after fixing | Destroys the audit trail; reviewer can't verify what was done | Use `action="resolve_with_reply"`, not `action="delete"` |
| Calling `resolve` without replying | Reviewer sees the comment closed with no explanation | Use `resolve_with_reply` — it's one call that does both |
| Calling `reply` then forgetting to `resolve` | Comment stays open looking unactioned | Use `resolve_with_reply` instead of separate calls |
| Fixing all comments then resolving in bulk at the end | Hard to recover if the session drops mid-pass | Close each comment immediately after its fix |
| Rewriting the comment via `action="edit"` | You don't own the original note | Leave the original; add your work in a reply |
| Using stale IDs from an earlier call | IDs can shift | Always re-fetch via `get_comments()` at the start of the triage pass |
