# ADR-0024: Synthetic-UNO + mock-engine test rig alongside the real-soffice integration suite

**Status:** Accepted
**Date:** 2026-05-19
**Phase:** F
**Supersedes:** —
**Superseded by:** —

## Context

Before this PR the test suite had three layers:

- **`tests/unit/`** — pure-Python unit tests over helpers (~180 tests).
  Stub UNO via `tests/conftest.py`. Fast, but only cover the helpers,
  not the 20 tool function bodies.
- **`tests/integration/`** — drives a real headless soffice on
  `127.0.0.1:2002`. Two tests exist (smoke + sidebar-dock-crash),
  per-tool integration coverage is unwritten. CI runs them in a
  cross-platform matrix.
- **`tests/integration/test_live_chat.py`** — gated on
  `T2V_TEST_USER_EMAIL` + `T2V_TEST_USER_PASSWORD`. Hits the real
  engine. Skips when secrets aren't available (fork PRs).

This left two large coverage gaps:

1. **The 20 tool bodies never ran on any pre-merge gate.** Helpers
   are tested, but `insert_content`, `format_paragraph`, etc. weren't
   exercised against any document model. Integration tests covered
   them only on push to a runner that could install + start soffice.
2. **The SDK round-trip (login → chat → tool interrupt → resume) had no
   deterministic local coverage.** Live tests need an account + a
   reachable engine; investigation #25 also documents a sandbox where
   soffice itself can't be started.

Both gaps hurt local iteration (every "did this change break a tool?"
question requires installing + restarting soffice) and they block
contributors who don't have engine credentials.

## Decision

Add two new pytest marker layers alongside the existing three:

- **`tests/synthetic/`** (marker: `synthetic`) — runs each tool's
  real function body (`@tool @ui_thread_tool` decorators applied)
  against an in-process synthetic UNO Writer document
  (`tests/synthetic/synthetic_uno.py`). The fake doc models the
  `XText` / `XTextCursor` / `XTextTable` / `XAnnotation` / `XUndoManager`
  surface the tools actually use. A `patched_extension` fixture
  installs a stub `Talk2ViewWriterExtension` whose `ctx` resolves to
  the synthetic document and whose `ui_thread.run_sync` is a
  synchronous inline executor.
- **`tests/mock_chat/`** (marker: `mock_chat`) — drives the real
  `Talk2ViewSDKClient` + the real `talk2view` SDK with canned httpx
  responses. `httpx.request` / `httpx.post` / `httpx.stream` are
  monkey-patched per-test by a route registry + a streaming script
  registry, so a test scripts the agent's SSE chunks directly. The
  tool-interrupt / resume cycle uses two scripts in sequence — the
  SDK plays one, POSTs to `/resume`, plays the second.

Both layers ship without any external dependency:
`tests/synthetic/` needs nothing beyond `pytest` + the talk2view SDK
already pinned in `pyproject.toml`; `tests/mock_chat/` adds only
`httpx` (already a runtime dep). `make test-unit` runs both via
`pytest -m "unit or synthetic or mock_chat"` (see CI workflow).

The existing **integration suite stays the source of truth for UNO
behaviour**: synthetic tests can't catch a regression in
LibreOffice's actual API surface, only in the contract between our
tools and the UNO calls they make.

## Alternatives considered

- **Only fix the soffice-in-sandbox issue** (investigation #25) and
  keep the original integration suite as the sole tool-level gate.
  Rejected — even on a working soffice host, the integration suite
  needs ~1 min cold start vs `synthetic` running 60 tests in 0.2 s.
  The fast feedback loop is worth duplicating coverage.
- **Replace integration with synthetic.** Rejected — the synthetic
  model can't catch regressions in real UNO (e.g. LibreOffice
  property name changes). It complements, not replaces.
- **MagicMock-only fixtures for tool tests.** Rejected — every tool
  test would have to wire ~30 MagicMock methods. The synthetic model
  amortises that across all 20 tools.
- **`pytest-httpx` / `respx` for the SDK mock.** Rejected — they require
  `httpx.Client` injection but the SDK uses module-level
  `httpx.request` / `httpx.stream` calls. Monkeypatching directly is
  smaller and more explicit.

## Consequences

**Pros**

- Per-tool integration tests run on every push (currently 61 synthetic
  + 4 mock-chat tests vs 0 before). Coverage gap closed without
  touching CI hardware requirements.
- `make test-unit` (the local default) now exercises full tool bodies
  + SDK round-trip + tool-interrupt + resume, in ~0.7 s total.
- Contributors without engine credentials get the full chat-flow
  contract proven locally. Live test still runs on the upstream CI
  with secrets configured.
- The synthetic model is itself testable / debuggable (it's plain
  Python), so when a tool's UNO usage changes, the synthetic model
  is updated in the same PR.

**Cons**

- One more layer to maintain. Adding a new tool now means: add tool
  + integration test + synthetic test + (when needed) extend
  `synthetic_uno.py` with any UNO method the new tool calls.
- Risk of a "passes synthetic but fails real soffice" divergence. We
  mitigate by keeping the synthetic surface minimal and asserting
  on observable document state (not specific UNO calls).
- Synthetic test failures don't catch UNO version drift (e.g. a
  property renamed in LibreOffice 25.x). That's still the integration
  suite's job.

**Follow-up**

- Per-tool integration tests in `tests/integration/test_tools_*.py`
  remain unwritten (the README scaffold spec is in place, but the
  files are TODO). The synthetic suite now serves as a forcing
  function: every tool that has a synthetic test must also gain
  an integration test eventually, so the contract holds against
  real UNO too.
- Investigation #25 — when soffice's sandbox hang gets a workaround,
  re-enable the full integration suite in any local environment.
- Add a CI matrix entry that runs `pytest -m "synthetic or mock_chat"`
  on every Python version we support (currently 3.11 in the lint job).

## References

- Code:
  - `tests/synthetic/synthetic_uno.py` — synthetic Writer document model
  - `tests/synthetic/conftest.py` — `patched_extension` fixture
  - `tests/synthetic/test_*.py` — per-tool synthetic tests (61 cases)
  - `tests/mock_chat/conftest.py` — httpx-level mocks
  - `tests/mock_chat/test_chat_flow.py` — SDK round-trip tests
  - `pyproject.toml` — new markers `synthetic` and `mock_chat`
- Plan: `/.claude/plans/i-want-to-make-rustling-eich.md`
- Related ADRs: ADR-0008 (tool registration), ADR-0009 (worker thread
  SSE), ADR-0018 (UI-thread marshalling), ADR-0021 (JSON returns).
- Investigations: #25 (soffice URP hang in sandbox), #26 (silent
  alignment validation gap caught by synthetic tests).
