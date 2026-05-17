# Talk2View-Writer

AI-powered document assistant for **LibreOffice Writer**. Sibling project to
[Talk2View-Word](../Talk2View-Word/) (Microsoft Word). Adds a Talk2View chat
panel to the LibreOffice Writer sidebar and executes Writer-native versions of
the Talk2View-Word tool catalog through the UNO API.

## Status

Phase A scaffold — see [`plan`](../../.claude/plans/i-want-to-make-rustling-eich.md)
for the full roadmap.

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

Proprietary — A2B Technology Corporation Pty Ltd.
