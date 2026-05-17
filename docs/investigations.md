# Investigations

Things we noticed in Talk2View-Word, Talk2View-Platform, or
LibreOffice itself that look **wrong, surprising, or worth
revisiting later** — but that aren't in scope to fix while building
Talk2View-Writer. Recording here so the question isn't lost.

Each entry has:

- **What:** the observation.
- **Where:** file / line(s).
- **Why it matters:** the risk or smell.
- **Next step:** a concrete action to take when prioritised.

Add new entries with the next sequential number; never reorder.

---

## #1 — SpeedWriter and Talk2View-Writer will co-exist in Extension Manager

**What:** Two LibreOffice extensions from the same vendor with
overlapping product surface (both add menu items, both target Writer)
will appear side-by-side in `Tools → Extension Manager`.

**Where:** Conceptual — affects `SpeedWriter-LibreOffice/` and
`Talk2View-Writer/` once both are installed.

**Why it matters:** Brand confusion. Users may install one when they
wanted the other, or both. Crash interaction is unlikely (different
UNO service names) but undefined.

**Next step:** Decide product positioning. Options: (a) keep
SpeedWriter focused on voice-only, brand Talk2View-Writer as
"document assistant"; (b) merge them eventually under one extension
with voice as a feature; (c) document them explicitly as
complementary in user-facing docs.

---

## #2 — Talk2View Python SDK is not published to PyPI

**What:** The `talk2view` package only exists inside the
`Talk2View-Platform` monorepo at `packages/sdk-python/`. There is no
versioned PyPI release.

**Where:** `Talk2View-Platform/packages/sdk-python/pyproject.toml`.

**Why it matters:** Every consumer (Talk2View-Writer, future
desktop integrations) has to use an editable path dep — see
ADR-0005. CI for third-party consumers can't `pip install talk2view`.
Vendoring or git-URL workarounds are brittle.

**Next step:** Publish `talk2view` to PyPI under the A2B Technology
organisation. Then update `Talk2View-Writer/pyproject.toml` to a
versioned dependency and delete `[tool.uv.sources]`. Coordinate with
the platform team on a release cadence.

---

## #3 — Bundling C-extension wheels in a `.oxt` is per-platform

**What:** `httpx` (via `httpcore` / `h11`) and `pydantic_core` ship
compiled C extensions. The `.oxt` artifact our `make build` produces
will contain Linux-only binaries if built on Linux, etc.

**Where:** `Talk2View-Writer/Makefile` — `build` target's pythonpath
copy loop.

**Why it matters:** A `.oxt` built on Linux will fail to import on
Windows or macOS — most likely silently, with the panel just not
appearing. SpeedWriter sidesteps this with `rpyc` (pure-Python) +
documenting `pip install` for native deps.

**Next step:**

1. Short-term: per-platform CI matrix producing one `.oxt` per OS.
2. Medium-term: investigate swapping `httpx` for a pure-Python HTTP
   client in the SDK (e.g. `urllib3` + a hand-rolled SSE parser). Or
   ship without `pydantic_core` by using `dataclasses`.
3. Long-term: switch the SDK to vendoring its own platform-agnostic
   HTTP stack.

---

## #4 — Sidebar deck IconURL borrows a LibreOffice internal icon

**What:** `extension/Sidebar.xcu` sets the deck icon to
`private:graphicrepository/sw/res/sidebar-mode.png` — a graphic
resource from LibreOffice's own Writer module.

**Where:** `Talk2View-Writer/extension/Sidebar.xcu` `IconURL` prop.

**Why it matters:** (1) Not Talk2View-branded. (2) The internal
resource path can change between LibreOffice releases and silently
break the icon. (3) Other vendors doing the same will produce decks
with identical tab icons — confusing.

**Next step:** Add a `Talk2View_24.png` to
`extension/icons/`, reference it via
`%origin%/icons/Talk2View_24.png` (the supported way to reference
extension-shipped resources). Coordinate with design on the icon
artwork.

---

## #5 — Stability of `solar_mutex` from PyUNO is undocumented

**What:** ADR-0009 picks a worker-thread + queue + UI-thread-drain
pattern for SDK iteration. An alternative is to acquire LibreOffice's
global UI lock (`solar_mutex`) on the worker thread before touching
UNO. SpeedWriter does not do this and there is no documented
PyUNO-side helper.

**Where:** `Talk2View-Writer/docs/adrs/0009-worker-thread-sse-iteration.md`.

**Why it matters:** If `solar_mutex` is reliable from PyUNO, the
threading model could be substantially simpler (no marshalling
queue). If it isn't, we want to know that and have it documented.

**Next step:** Phase B spike: write a tiny test that grabs
`solar_mutex` via `com.sun.star.lang.XMultiServiceFactory` from a
background thread, modifies a doc, and verifies it doesn't deadlock.
Report findings as a follow-up ADR.

---

## #6 — Partner key is embedded in the distributed bundle

**What:** Talk2View-Word's `pk_live_…` lives inline in
`src/taskpane/App.tsx`, shipped verbatim in the production build.
Talk2View-Writer inherits the pattern in `src/talk2view_writer/config.py`.

**Where:** `Talk2View-Word/src/taskpane/App.tsx:6`,
`Talk2View-Writer/src/talk2view_writer/config.py`.

**Why it matters:** The partner key identifies the partner, not the
user — so it isn't a credential in the strict sense — but treating
it as recoverable from any distributed bundle is a weak security
posture. If it's ever revoked, every install of both Word and Writer
breaks until users update.

**Next step:**

1. Issue distinct partner keys per host-app so a Word incident
   doesn't take down Writer.
2. Consider a settings-dialog override (Phase F) so enterprise
   customers can use their own keys.
3. Document on the platform side what guarantees the partner key
   provides — it appears to be more of an analytics tag than a
   credential. If that's so, document it as such.

---

## #7 — No OS-keychain backend in the SDK's token storage

**What:** `talk2view.storage` ships only `MemoryStorage` (and the
abstract `TokenStorage`). Production integrations (Talk2View-Word
uses browser `localStorage`, Slicer uses Qt settings) all roll their
own.

**Where:** `Talk2View-Platform/packages/sdk-python/src/talk2view/storage.py`.

**Why it matters:** Every desktop integration reinvents secure token
storage. We will do the same in Phase B (see ADR-0012). A shared
`KeyringTokenStorage` in the SDK would eliminate duplicated work and
centralise the security boundary.

**Next step:** Upstream a `KeyringTokenStorage` (using the `keyring`
PyPI package) into `Talk2View-Platform/packages/sdk-python` once
Phase B has validated the approach here.

---

## #8 — No defined re-sync cadence between Word and Writer skills

**What:** ADR-0013 copies skills + system prompt verbatim from
Talk2View-Word. There is no process to pull subsequent Word skill
updates into Writer.

**Where:** `Talk2View-Word/skills/`, `Talk2View-Writer/src/talk2view_writer/skills/`
(once populated in Phase E).

**Why it matters:** As the Word team iterates on skills, Writer will
silently fall behind. Bug fixes (e.g. "this skill caused a hallucination
in 1% of runs") won't propagate.

**Next step:** Pick a cadence (monthly? per-Word-release?) and an
owner. Could be automated: a CI job in Talk2View-Writer that diffs
against `../Talk2View-Word/skills/` and opens a PR with the deltas.

---

## #9 — Python SDK has several silent `except Exception:` paths

**What:** The SDK has at least five places where an exception is
caught and either swallowed or downgraded:

- `client.py:248` — bare `except Exception:`
- `client.py:293` — bare `except Exception:`
- `auth.py:79` — `except Exception:  # noqa: S110 — logout failures
  are non-critical`
- `__init__.py:163` — `except Exception:  # noqa: S110 — fire-and-
  forget session cleanup`
- `tools.py:460` — `except Exception as exc:` (action unclear without
  reading further)

**Where:** `Talk2View-Platform/packages/sdk-python/src/talk2view/`.

**Why it matters:** Conflicts with the strict "fail fast, no silent
returns" rule documented in
`SpeedWriter-LibreOffice/CLAUDE.md`. From an integrator's perspective,
errors during logout / session cleanup / tool dispatch may matter for
debugging — silent swallows obscure them. At minimum the catches
should log.

**Next step:** File platform-side issue to audit each occurrence:
either narrow the exception type or add a `logger.exception()` so
the error is at least visible in logs.

---

## #10 — Talk2View-Word's `package.json` has no `test` script entry

**What:** `vitest` is configured (devDep + `vitest.config.ts`
present), but `package.json` has no `"test"` entry in its `scripts`
block. Running `npm test` does nothing useful.

**Where:** `Talk2View-Word/package.json`.

**Why it matters:** Discoverability — anyone clones the repo, runs
`npm test`, sees no tests, assumes there are none. Tests probably
exist somewhere (vitest config implies so) but aren't easily
invoked.

**Next step:** Add `"test": "vitest run"` and `"test:watch":
"vitest"` to `Talk2View-Word/package.json` scripts.

---

## #11 — Chat history `UnoControlEdit` has no built-in length cap

**What:** The Phase B chat panel renders history into a
`UnoControlEdit` and appends by concatenating the new chunk to the
current `Text` property. There is no automatic trim when the history
grows long, and the `setPropertyValue("Text", …)` call rewrites the
entire buffer each time — O(n) per append.

**Where:**
`Talk2View-Writer/src/talk2view_writer/ui/sidebar_panel.py::Talk2ViewPanel._append_history`.

**Why it matters:** Long sessions (multi-hour interactive editing)
will degrade noticeably as the buffer grows. Also no upper bound on
memory.

**Next step:**

1. Phase F: add a "Clear chat" button that resets the history widget.
2. Phase F: cap the buffer at e.g. 200 KB and auto-trim the oldest
   content (with a "[earlier history truncated]" marker).
3. Consider using `UnoControlEditModel.MaximumTextLength` as a hard
   safety net.

---

## #12 — `solar_mutex` from PyUNO — Phase B spike outcome TBD

**What:** ADR-0017 ships cross-thread widget writes from the chat
worker as a calculated risk, predicated on the assumption that
`solar_mutex_acquire()` from PyUNO is either unavailable or too
brittle to bet on. We have not actually verified this — the spike is
deferred to Phase B / C.

**Where:**
`Talk2View-Writer/docs/adrs/0017-cross-thread-widget-updates-phase-b.md`,
linked from Investigation #5.

**Why it matters:** If the spike confirms `solar_mutex` works
reliably from PyUNO, we can replace the direct-write pattern with a
much smaller change than building a full marshalling queue.

**Next step:** Write a small standalone test extension that grabs
`solar_mutex` from a background thread and mutates a document.
Report results as an addendum to Investigation #5 and update ADR-0017.

---

## #13 — Word style names don't all map cleanly to LibreOffice

**What:** Word's built-in paragraph style set differs from
LibreOffice's. Most are close (`Heading 1` ↔ `Heading1`, with a
space), but several Word styles have no LibreOffice equivalent:

- `IntenseQuote` — we map to `Quotations`, same as `Quote`
- `NoSpacing` — we map to `Default Paragraph Style`, same as `Normal`

Round-tripping `IntenseQuote` → `Quotations` → `Quote` is lossy.

**Where:** `Talk2View-Writer/src/talk2view_writer/uno_helpers/styles.py`.

**Why it matters:** When the agent sends a Word style name through
`insert_content` or `format_paragraph`, the resulting paragraph in
Writer may not look exactly like the Word equivalent. Round-tripping
through `get_document` will silently rename the style.

**Next step:**

1. Phase D Group 3 (Formatting): document which style names suffer
   degradation in the system-prompt deltas section (Phase E).
2. Long-term: ship a small set of LibreOffice paragraph styles named
   `IntenseQuote`, `NoSpacing`, etc. that match Word's visual look,
   so `word_to_libreoffice_style` becomes lossless. Could ship as
   a `Talk2ViewTemplate.ott` inside the `.oxt`.

---

## #14 — LibreOffice text sections ≠ Word document sections

**What:** Word's `document.sections` are page-layout boundaries that
control headers / footers, page numbering, and margins per range of
pages. LibreOffice has no direct equivalent — section concepts are
split across **page styles** (per-page layout) and **text sections**
(re-flowed content blocks). The Word agent treats `section_count` as
"how many page-layout sections does the doc have" but our
`get_document` returns `doc.getTextSections().getCount()` which is
a different quantity entirely.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/reading.py::get_document`,
`response["sections"]` field.

**Why it matters:** The agent may interpret a value of 0 or 1 as
"no section breaks" and make incorrect decisions about
`insert_break` with `type="section_next_page"`.

**Next step:**

1. Phase D Group 5 (Structure): `insert_break` needs to either
   simulate Word sections via page styles, or be honest about the
   semantic gap.
2. `get_document` should consider returning a richer
   `page_styles` field (count of distinct page styles used) instead
   of / alongside `sections`. Coordinate with Phase E system-prompt
   deltas so the agent knows the field's actual semantics.

---

## #15 — UNO `XFont` API has no direct equivalent on a paragraph

**What:** Word's `paragraph.getRange().font` gives a single Font
object whose properties describe the entire paragraph's font (with
the implicit assumption that the paragraph has uniform formatting,
or returning the property at the first character if mixed).
LibreOffice's UNO has no `paragraph.font` shortcut — to read font
properties we create a text cursor across the paragraph and read
`Char*` properties from it. The result for mixed-formatting
paragraphs is the property of the *first* character only, not a
union or majority.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/reading.py::_read_font_properties`.

**Why it matters:** If a paragraph contains a mix of bold and
plain text, our `include_font_details=true` response will say the
paragraph is bold (because the first character is) — but Word's
Font object might say `bold = null` (mixed). Subtle but visible to
the agent.

**Next step:** When porting `format_text` (Phase D Group 3) decide
whether to detect mixed formatting and report `null` for divergent
properties, like Word does. May require walking each character of
the paragraph, which is expensive — measure first.

---

## #16 — `edit_table` ignores the `values` array for add_rows / add_columns

**What:** Word's `edit_table` allows populating newly-added rows or
columns in the same call by passing a `values` 2D array. The UNO
`XTableRows.insertByIndex(insertPos, count)` and
`XTableColumns.insertByIndex(insertPos, count)` APIs accept only a
position + count — there is no built-in "insert and fill" path.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/writing.py::edit_table`
(`add_rows` / `add_columns` actions).

**Why it matters:** The agent may pass `values` expecting the cells
to be populated. Today we silently ignore them and the user has to
follow up with `edit_cell` calls — slower and more error-prone.

**Next step:** After `insertByIndex`, walk the newly-added cells and
call `setString()` on each one to mirror Word's behaviour. Add a unit
test that asserts the cells receive the right values. Phase F.

---

## #17 — Image inserts go through a temp file (UNO has no in-memory path)

**What:** Word's `Word.body.insertInlinePictureFromBase64` decodes
the base64 directly. UNO's
`com.sun.star.graphic.GraphicProvider.queryGraphic` only loads from
a URL — there is a `com.sun.star.graphic.MediaProperties` route with
an `InputStream` property but no clean way to feed bytes from PyUNO
without writing to disk.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/writing.py::insert_image`.

**Why it matters:** Temp-file roundtrip adds ~ms of latency per
insert plus a brief on-disk footprint of the raw image. The temp
file is cleaned up in a `finally` block, but a crash between write
and `os.remove` leaves the bytes around. Not catastrophic, but worth
fixing if we can find a clean in-memory path.

**Next step:** Investigate `com.sun.star.io.SequenceInputStream` as
an `InputStream` parameter to `GraphicProvider.queryGraphic`. If it
works from PyUNO, switch and delete the temp-file dance.

---

## #18 — `undo_redo` operates on `XUndoManager`, not a Word-equivalent stack

**What:** Word's `(context.document as any).undo(count)` lives on
the document and is intrinsic to the document's own undo stack.
LibreOffice exposes `XDocumentUndoManager` via
`doc.getUndoManager()`, and `undo()` / `redo()` take no arguments —
each call is exactly one step. Our port loops to honour ``count``.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/writing.py::undo_redo`.

**Why it matters:** If a step is part of a multi-action undo block
(LibreOffice supports nested `enterUndoContext` / `leaveUndoContext`),
Word's `undo(N)` might behave differently than our N-step loop.
Edge case but worth a unit test once integration tests exist.

**Next step:** Phase F: integration test that records 3 atomic edits,
calls `undo_redo("undo", 3)`, and confirms the document is in its
original state.

## #19 — `search_document` flags `match_prefix` / `match_suffix` / `ignore_punct` / `ignore_space` have no UNO equivalent

**What:** Word's `search()` accepts four flags that have no direct
Writer counterparts:

- `matchPrefix` / `matchSuffix` — UNO's `SearchDescriptor` has no
  prefix/suffix mode. We approximate via regex `\b` anchors when the
  caller opts in (turning a plain search into a regex search).
- `ignorePunct` / `ignoreSpace` — accepted but no-op on Writer.

Additionally, `matchWildcards` maps to `SearchRegularExpression`. Word
"wildcards" are a different (smaller) DSL than regex — most patterns
overlap, but `<` / `>` for word boundaries and `?` for any-single-char
differ between the two dialects.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/search.py::search_document`.

**Why it matters:** A skill that constructs Word-wildcard patterns and
hands them to Writer will see different match counts (or syntax errors
from regex parser).

**Next step:** Phase F integration tests should run the same query
against Word + Writer and compare counts; flag any divergence in the
system prompt's "Writer deltas" section.

## #20 — Writer has no first-class document "section" concept

**What:** Word documents are partitioned into *sections* with their
own page setup, headers, footers, and column layout. Writer has
*page styles* (template-like definitions reused across pages) and
*text sections* (named regions with optional column layout / hide
behaviour). Neither is a 1:1 substitute.

The closest analogue is: each paragraph carries a `PageDescName`
property — switching `PageDescName` between paragraphs effectively
ends one "section" and begins another. We expose `section_index` in
`set_header_footer`, `insert_page_numbers`, and `set_page_setup` as
an index into the list of page styles actually used in the document.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/structure.py`
(`_get_page_style`, `_list_page_styles_in_use`,
`set_header_footer`, `set_page_setup`).

**Why it matters:** Word skills that say "in section 2, use landscape
orientation" will work only if the document already has a second page
style in use. Creating a new section equivalent requires inserting a
page break with a page-style swap — currently exposed as
`insert_break(break_type="section")` but with caveats.

**Next step:** System prompt's "Writer deltas" must explain section
semantics. Phase F integration test: insert section break, verify
landscape on page 2 only.

## #21 — Different-first / different-odd-even headers not yet wired

**What:** Word's `Section.getHeader(headerType)` accepts `Primary`,
`FirstPage`, and `EvenPages`. Our Writer port currently only writes
to the main header (`HeaderText`) of the resolved page style. Writer
supports the distinction via separate page styles (`First Page`,
`Left Page`, `Right Page`) and via the `HeaderIsShared` /
`FirstIsShared` properties on a page style.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/structure.py::set_header_footer`.

**Why it matters:** A skill that says "different first-page header"
will silently apply to all pages on Writer. Functional but not
faithful.

**Next step:** Add a `header_type` / `footer_type` arg accepting
`primary` / `first_page` / `even_pages`. For `first_page` flip
`FirstIsShared = False` and write to `HeaderTextFirst`. For
`even_pages` flip `HeaderIsShared = False` and write to
`HeaderTextLeft`. Tracked for Phase F polish.

## #22 — Page numbers are restarted per page-style, not per Word "section"

**What:** Word lets you restart numbering at any section boundary by
setting `Section.pageSetup.pageNumberStart`. Writer exposes
`FirstPageNumber` on the page descriptor (page style), which means
all pages using that style share the same first-page number.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/structure.py::insert_page_numbers`.

**Why it matters:** A skill that asks "restart numbering at page 5"
in a document with one page style will affect the first page, not
page 5. This is bound up with #20 (no Word sections) — restart
behaviour requires a page-style swap.

**Next step:** Document in the system prompt's "Writer deltas". When
integration tests land, add a fixture document with two page styles
and verify per-style numbering works.

## #23 — Annotation reply chains and `Resolved` are LibreOffice ≥ 7.4 only

**What:** Writer's annotation API gained the `ParentName` and
`Resolved` properties on `com.sun.star.text.TextField.Annotation`
in LibreOffice 7.4 (2022). Older builds expose the comment surface
but treat every annotation as a top-level item with no resolved
state.

Word ships `Comment.resolved` and `Comment.reply()` unconditionally
since the early-2020 Office version. There's no version negotiation
across the SDK boundary — both hosts advertise the same tool surface.

**Where:**
`Talk2View-Writer/src/talk2view_writer/tools/commenting.py`
(`_annotation_resolved`, `_annotation_parent_name`,
`_insert_reply`, `manage_comment`).

**Why it matters:** Users on LibreOffice 7.3 or older will get
"property not supported" errors from `manage_comment` actions
`resolve` / `unresolve` / `resolve_with_reply`. We surface a clear
recovery message ("Upgrade to LibreOffice 7.4+") but the skill model
can't downgrade gracefully without telemetry on the running build.

**Next step:** Expose the LibreOffice version (already available via
`Bootstrap.GetVersion()`) in the extension's first tool-call payload
so the system prompt can pick a strategy. Track LibreOffice version
distribution from real users before deciding whether to gate
resolve-related tool calls behind a version check.
