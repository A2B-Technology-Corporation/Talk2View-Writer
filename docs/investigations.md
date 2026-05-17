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
