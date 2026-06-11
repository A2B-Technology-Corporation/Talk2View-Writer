# Architecture Decision Records

Each ADR captures one decision: the context it was made in, the options
weighed, the choice taken, and the consequences. Format follows
[Michael Nygard's lightweight template](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md).

ADRs are numbered sequentially in the order they were decided, regardless of
which phase they belong to. The **Phase** field indicates when the decision
applied.

## Status legend

| Status     | Meaning                                                              |
|------------|----------------------------------------------------------------------|
| Accepted   | Active decision driving current code.                                |
| Proposed   | Drafted but not yet acted on.                                        |
| Deferred   | Will be revisited in a later phase; documented to avoid losing it.   |
| Superseded | Replaced by a later ADR — link from the body of the new one.         |
| Deprecated | Old decision; code being removed but not yet replaced.               |
| Reverted   | Tried, then rolled back; the change is no longer in the code.         |

> **Numbering gap:** there is no ADR-0032. The streaming-SSE-proxy decision
> that early notes (e.g. ADR-0031) referred to as a future "ADR-0032" was
> recorded as [ADR-0033](0033-streaming-sse-proxy-via-polled-queue.md); the
> number 0032 was skipped.

## Index

| #   | Title                                                  | Phase | Status   |
|-----|--------------------------------------------------------|-------|----------|
| [0001](0001-new-sibling-project.md) | New sibling project rather than fork Word or evolve SpeedWriter | A | Accepted |
| [0002](0002-cloud-sdk-backend.md)   | Cloud `talk2view` SDK rather than local RPyC Core              | A | Accepted |
| [0003](0003-sidebar-deck-ui.md)     | Sidebar deck as the primary UI surface                          | A | Superseded by 0029 |
| [0004](0004-dual-uno-component-entry.md) | Two UNO components in one entry file                        | A | Accepted |
| [0005](0005-sdk-editable-path-dependency.md) | Consume Python SDK via editable path dependency       | A | Accepted |
| [0006](0006-bundle-deps-into-pythonpath.md)  | Bundle Python deps into `pythonpath/` inside the `.oxt` | A | Accepted |
| [0007](0007-manual-widget-positioning.md)    | Manual widget positioning rather than vcl.builder XML | A | Accepted |
| [0008](0008-tool-registration-via-decorator.md) | Tools registered as `@tool`-decorated Python functions | A | Accepted |
| [0009](0009-worker-thread-sse-iteration.md)  | Worker thread + UI-thread queue for SDK iteration     | A | Accepted |
| [0010](0010-partner-key-shared-with-word.md) | Reuse Word partner key until a Writer key is issued    | A | Accepted |
| [0011](0011-sidebar-context-writer-only.md)  | Sidebar deck scoped to Writer + Writer Global docs    | A | Accepted |
| [0012](0012-token-storage-deferred.md)       | Token storage deferred to Phase B                     | A | Superseded by 0014 |
| [0013](0013-skill-and-prompt-copy-from-word.md) | Skills + system prompt copied verbatim from Word    | A | Accepted |
| [0014](0014-file-token-storage.md)           | File-backed token storage in user config directory    | B | Accepted |
| [0015](0015-programmatic-login-dialog.md)    | Login dialog built programmatically (not `.xdl`)      | B | Accepted |
| [0016](0016-multiline-edit-history.md)       | Chat history rendered as a multiline `UnoControlEdit` | B | Accepted |
| [0017](0017-cross-thread-widget-updates-phase-b.md) | Cross-thread widget updates from the chat worker (Phase B only) | B | Superseded by 0018 |
| [0018](0018-ui-thread-marshalling-queue.md)  | UI-thread marshalling via `AsyncCallback` + `XCallback` | C | Accepted |
| [0019](0019-tool-registry-aggregation.md)    | Tool registry aggregation via per-module `TOOLS` list | C | Accepted |
| [0020](0020-tool-context-via-singleton.md)   | Tool bodies fetch UNO context via the extension singleton | C | Accepted |
| [0021](0021-json-string-tool-returns.md)     | Tools return JSON-encoded strings, not Python dicts   | D | Accepted |
| [0022](0022-system-prompt-bundled-and-overridable.md) | Bundle SYSTEM_PROMPT.md in the .oxt, pass per-session | E | Accepted |
| [0023](0023-vendor-pydantic-core-wheels.md) | Bundle pre-built pydantic_core wheels for the cross-platform matrix | F | Accepted |
| [0024](0024-synthetic-uno-and-mock-chat-test-rig.md) | Synthetic-UNO + mock-engine test rig alongside the real-soffice integration suite | F | Accepted |
| [0025](0025-desktop-window-peer-for-panel-construction.md) | Use Toolkit.getDesktopWindow() as the XWindowPeer for sidebar panel construction | F | Superseded by 0026 |
| [0026](0026-python-xwindowpeer-adapter.md) | Python XWindowPeer adapter for strict-PyUNO sidebar panel construction | F | Superseded by 0027 |
| [0027](0027-canonical-toolpanel-pattern.md) | Follow LibreOffice's canonical Python toolpanel pattern verbatim | F | Superseded by 0029 |
| [0028](0028-queryinterface-xwindowpeer.md) | Obtain XWindowPeer via queryInterface before createContainerWindow | F | Superseded by 0029 |
| [0029](0029-floating-chat-window.md) | Floating non-modal chat window instead of a sidebar panel | F | Superseded by 0030 |
| [0030](0030-web-chat-via-pywebview-subprocess.md) | Web chat UI via pywebview subprocess + Unix-socket bridge | F | Accepted |
| [0031](0031-e2e-via-playwright-against-browser-bundle.md) | E2E via Playwright against the browser-bundle in Chromium, not pywebview | G | Accepted |
| [0033](0033-streaming-sse-proxy-via-polled-queue.md) | Streaming SSE proxy via a polled per-stream queue | G | Accepted |
| [0034](0034-reuse-word-partner-key-with-writer-system-prompt.md) | Reuse Word's partner key + override system prompt for Writer | G | Reverted |
| [0035](0035-track-changes-default-for-ai-edits.md) | Track changes by default for AI edits (per-call envelope, user preference) | G | Accepted |
| [0036](0036-live-e2e-via-playwright-real-soffice.md) | Live E2E via Playwright + bridge-routed shim + real soffice + real engine | G | Accepted |
| [0037](0037-comment-authorship-stamping.md) | Stamp author ("Talk2View on behalf of …") + date on AI-created comments | G | Accepted |
| [0038](0038-macos-bundle-pyobjc-use-lo-python.md) | Spawn LO-bundled Python on macOS + bundle pyobjc as universal2 wheels (supersedes the system-Python assumption in ADR-0030) | F | Accepted |
| [0039](0039-companion-window-docking.md) | Integrated companion window (WM identity + host-window docking) instead of the dead sidebar deck | G | Accepted |
| [0040](0040-guided-tour-demo-skill.md) | Guided demo as a partner skill (provisioned via dashboard), not a client-side prompt injection | G | Accepted |
| [0041](0041-grant-microphone-across-webview-backends.md) | Grant microphone (getUserMedia) across all three webview backends (WebKitGTK / WKWebView / WebView2) | G | Accepted |

## Writing a new ADR

1. Copy [`0000-template.md`](0000-template.md) to the next free number.
2. Fill in the sections — keep it focused on **one** decision.
3. Add a row to the index above.
4. Link the ADR from any code or other ADR that depends on it
   (e.g. `# See ADR-0007`).

If a later decision overrides this one, change the old ADR's status to
**Superseded** and link forward to the new ADR; do not edit history.
