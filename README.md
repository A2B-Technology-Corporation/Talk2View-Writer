# Talk2View-Writer

[![CI](https://github.com/A2B-Technology-Corporation/Talk2View-Writer/actions/workflows/ci.yml/badge.svg)](https://github.com/A2B-Technology-Corporation/Talk2View-Writer/actions/workflows/ci.yml)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-blue.svg)](LICENSE)

AI-powered document assistant for **LibreOffice Writer**. Sibling project to
[Talk2View-Word](../Talk2View-Word/) (Microsoft Word). Adds a Talk2View chat
panel to the LibreOffice Writer sidebar and executes Writer-native versions of
the Talk2View-Word tool catalog through the UNO API.

## Install (end users)

Download the latest `Talk2ViewWriter.oxt` from the
[Releases page](https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest),
then either:

- **From the LibreOffice GUI**: `Tools → Extension Manager → Add...` and
  pick the `.oxt`. Accept the MPL 2.0 license when prompted.
- **From the command line**:

  ```bash
  unopkg add --force Talk2ViewWriter.oxt
  ```

Restart LibreOffice Writer; the **Talk2View** deck appears in the right
sidebar.

The `.oxt` is a single universal package — same file works on Linux
(x86_64 + aarch64), macOS (Intel + Apple Silicon), and Windows x86_64,
across Python 3.10–3.13. The cross-platform `pydantic_core` wheel
matrix is bundled in the package; the right one is selected
automatically at first launch (see [ADR-0023](docs/adrs/0023-vendor-pydantic-core-wheels.md)).

### Supported LibreOffice builds

Talk2View-Writer follows the canonical Python sidebar-panel pattern
documented in LibreOffice's own SDK example
(`odk/examples/python/toolpanel/toolpanel.py`). It works on any build
that ships a stock PyUNO bridge:

- **LibreOffice from The Document Foundation** —
  [`.deb` / `.dmg` / `.msi` downloads](https://www.libreoffice.org/download/download/)
  (any 7.x, 24.x, 25.x, 26.x). All three OSes.
- **Flatpak** — `flathub:org.libreoffice.LibreOffice` (Linux).
- **Snap** — the LibreOffice Snap (Linux).
- **AppImage** from documentfoundation.org (Linux).
- **macOS Homebrew Cask** — `brew install --cask libreoffice`.
- **Debian/Ubuntu apt packages** — verified working on `bookworm`
  stable + backports, `noble` 24.2, and the
  `libreoffice-still`/`libreoffice-fresh` TDF PPAs (25.x, 26.x).

If the deck opens to an empty rectangle you may be on a downstream
build whose PyUNO bridge rejects the canonical pattern — see
[ADR-0027](docs/adrs/0027-canonical-toolpanel-pattern.md). Installing
a TDF-shipped build (Flathub / Snap / `.deb` from
documentfoundation.org) is the fix.

## Status

Phase F — packaging + comprehensive test rig complete. See
[`plan`](../../.claude/plans/i-want-to-make-rustling-eich.md) for the
full roadmap. The current branch covers:

- Sidebar deck renders reliably (ADR-0003, ADR-0007); chat panel
  handles every SDK event type (`text`, `status`, `todos`, `tool_call`,
  `error`, `done`) and ships with slash commands `/help`, `/clear`,
  `/logout`, `/settings`, `/tools`.
- All 20 tools registered with the SDK; tool-call interrupts
  auto-execute on the worker thread and marshal UNO calls back to the
  UI thread (ADRs 0008, 0009, 0018, 0020).
- Cross-platform universal `.oxt` (Linux, macOS x86_64 + arm64,
  Windows) with a bundled `pydantic_core` wheel matrix (ADR-0023).
- Five test layers — 180 unit + 61 synthetic-UNO tool + 4 mock-engine
  SDK round-trip + 3 real-soffice integration + 1 live-engine chat —
  proving the panel ↔ SDK ↔ tool ↔ document loop without the engine or
  soffice when those are unavailable. See ADR-0024 for the split.

## Documentation

- [`docs/adrs/`](docs/adrs/README.md) — Architecture Decision Records (ADRs)
  for every substantive choice (sibling-project design, cloud SDK, sidebar
  deck, threading model, etc.). Start here if you want to know *why* the code
  looks the way it does.
- [`docs/investigations.md`](docs/investigations.md) — running log of
  surprising / wrong / deferred things noticed in Talk2View-Word,
  Talk2View-Platform, or LibreOffice itself. Things we *won't* fix while
  building Talk2View-Writer but don't want to lose.
- [`CLAUDE.md`](CLAUDE.md) — code standards inherited from
  `SpeedWriter-LibreOffice/CLAUDE.md` plus Talk2View-Writer-specific guidance
  (UNO threading rules, tool surface, etc.).

## Architecture

```
LibreOffice Writer
  └─ Sidebar deck "Talk2View"
       └─ ChatPanel (UNO widgets)
            │
            ▼  user message
       sdk_client → talk2view (Python SDK) ──HTTPS──▶ engine.talk2view.com
            ▲                                         │
            │     interrupt: tool_call ───────────────┘
            ▼
       WriterTools (Python functions calling UNO XText/XTable/XStyle …)
```

- **Backend:** cloud (`engine.talk2view.com`) via partner key + user JWT.
- **SDK:** [`talk2view`](../Talk2View-Platform/packages/sdk-python/) — handles
  SSE streaming and the tool-execution `interrupt → resume` loop.
- **Tools:** 26 Python functions registered via `@tool`, mirroring the
  TypeScript tools in `Talk2View-Word/src/taskpane/tools/`.

## Development

```bash
make dev            # install with dev deps via uv
make lint test      # ruff + pytest
make build          # stage extension into build/Talk2ViewWriter/
make package        # produce dist/Talk2ViewWriter.oxt
make install-oxt    # install into user's LibreOffice profile
```

After `make install-oxt`, restart LibreOffice Writer and open the sidebar
(View → Sidebar). The "Talk2View" deck should appear.

## Project Layout

```
Talk2View-Writer/
├── extension/                       # OXT packaging files
│   ├── talk2view_writer.py          # UNO entry (XJobExecutor + XSidebarPanelFactory)
│   ├── description.xml
│   ├── Addons.xcu                   # menu bar entries
│   ├── Sidebar.xcu                  # sidebar deck registration
│   └── META-INF/manifest.xml
├── src/talk2view_writer/
│   ├── extension.py                 # singleton lifecycle + auth state
│   ├── sdk_client.py                # talk2view SDK wrapper
│   ├── ui/                          # sidebar panel, login dialog, components
│   ├── tools/                       # 26 UNO tools across 6 modules
│   ├── uno_helpers/                 # cursor, tables, styles, comments
│   └── skills/                      # SKILL.md docs (copied from Talk2View-Word)
└── tests/{unit,integration}/
```

## License

Mozilla Public License 2.0 — the same license LibreOffice itself
ships under. Full text in [LICENSE](LICENSE). Copyright A2B Technology
Corporation Pty Ltd and contributors.

The MPL is a weak / file-scoped copyleft: derivative works of MPL-
covered files must remain MPL, but the license can be combined freely
with code under most other licenses (including proprietary) as long as
modifications to MPL-covered files are made available under MPL.
