# Publishing Talk2View for Writer on extensions.libreoffice.org

Copy-paste-ready content for the **LibreOffice Extensions** submission form
(<https://extensions.libreoffice.org> → *My extensions → New Extension*). Each
section below maps to a field on the "New Extension" page; the **Add a Release**
section maps to the per-release form you reach after saving the entry.

Keep this file in sync with `extension/description.xml` and
`extension/description/description_en.txt` whenever they change.

---

## New Extension form

### Title

```
Talk2View for Writer
```

### Short summary

```
Chat with your document. An AI assistant inside LibreOffice Writer that drafts, edits, formats, restructures, and reviews text from natural-language requests — by typing or by voice.
```

### Project logo

Upload `extension/icons/talk2view.png` (the same icon shipped in the `.oxt`).
A larger square PNG/SVG renders better on the listing page — if a hi-res master
exists, prefer it; otherwise `talk2view.png` is the canonical mark.

- Logo file in repo: `extension/icons/talk2view.png`
- Small variant (menu/toolbar): `extension/icons/talk2view-16.png`

### Matching Tags

The site's tag field is an autocomplete against an existing tag list — pick the
closest matches to these (add only tags that already exist on the site):

```
Writer, Tools, AI, Assistant, Productivity, Automation, Chat, Speech-to-text, Accessibility, Documentation
```

### Description

```
Talk2View for Writer adds an AI document assistant to LibreOffice Writer. Open
the chat panel beside your document (Talk2View -> Open Talk2View Chat) and ask
the assistant, in plain language, to read, write, edit, format, restructure, and
review your document. You can type your request or speak it — built-in
speech-to-text transcribes your voice into the chat.

The assistant works directly on the open document through a comprehensive set of
document tools and structured skills, so it does real edits you can see and undo
— it does not just give advice.

Key features:
- A docked companion chat panel, always available alongside your document.
- 21 document tools spanning reading, writing, formatting, search, page
  structure (headers, footers, page numbers, breaks, layout), tables, images,
  and comments.
- 13 high-level skills for document creation, formatting standards,
  restructuring, rewriting in place, template filling, content extraction,
  consistency checks, pre-send review, and comment triage.
- Voice input: dictate requests with built-in speech-to-text.
- Tracked changes by default: every AI edit is recorded as a tracked change you
  can review and accept or reject (configurable).
- Powered by the Talk2View cloud engine.

Requirements:
- LibreOffice 7.0 or later (tested on LibreOffice 26.2).
- A Talk2View account (partner key + user login).
- An internet connection (the assistant runs on the Talk2View cloud backend).

Privacy & responsible use: see the Privacy, Security & Responsible AI Policy and
Terms of Use linked from https://talk2view.com.

Disclaimer: Talk2View is not cleared or approved by the FDA (U.S.), TGA
(Australia), or notified bodies under the EU MDR as a medical device. It is
intended for educational and research purposes only and must not be used for
clinical diagnosis, treatment, or medical decision-making.

Copyright (c) 2026 A2B Technology Corporation Pty Ltd. Licensed under the
Mozilla Public License 2.0 (MPL-2.0).
```

### URL of the Extension's Homepage

```
https://talk2view.com
```

### URL to the repository's source

```
https://github.com/A2B-Technology-Corporation/Talk2View-Writer
```

### Screenshots (up to ten)

The repo has no committed marketing screenshots yet — capture these from a live
LibreOffice 26.2 session before submitting (PNG, ideally 1280px+ wide):

1. The Talk2View chat panel open beside a Writer document.
2. A natural-language edit in progress (prompt + the resulting document change).
3. Voice input: the microphone button / a transcribed request in the chat.
4. AI edits shown as tracked changes (Edit -> Track Changes -> Manage).
5. A structural task (e.g. headers/footers + page numbers, or a generated table).
6. The About dialog (Talk2View -> About) showing version, license, and links.

---

## Add a Release (after saving the entry)

Use these values on the per-release form. Re-run this section's "download URL"
swap for each future release, or use the always-latest URL.

| Field | Value |
|-------|-------|
| Release version | `1.0.4` |
| Extension file (.oxt) | `Talk2ViewWriter.oxt` from the GitHub release (link or upload — see URLs below) |
| Compatible with (LibreOffice) | `7.0` and newer (tested on `26.2`) |
| Compatible with (Operating Systems) | Linux, macOS, Windows |
| License | Mozilla Public License 2.0 (MPL-2.0) |

**Download URLs** (the `.oxt` is ~49 MB; it bundles the cross-platform Python
runtime wheels):

- This release: <https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/download/v1.0.4/Talk2ViewWriter.oxt>
- Always latest GA: <https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest/download/Talk2ViewWriter.oxt>
- Release page: <https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/tag/v1.0.4>

**Release notes (v1.0.4):**

```
Patch release.

- Page numbers now follow the page style (e.g. Arabic vs Roman numerals)
  instead of defaulting to letters.
- Microphone access (voice input) now works inside the chat panel across the
  WebKitGTK / WKWebView / WebView2 webview backends.
- Speech-to-text uploads now reach the engine correctly (multipart/form-data is
  proxied properly), so dictated requests transcribe end-to-end.
```

---

## Reference metadata (from `extension/description.xml`)

| Key | Value |
|-----|-------|
| Display name | Talk2View for Writer |
| Identifier | `com.talk2view.writer` |
| Current version | `1.0.4` |
| Publisher | A2B Technology Corporation Pty Ltd — <https://talk2view.com> |
| Minimum LibreOffice | 7.0 |
| License | Mozilla Public License 2.0 (MPL-2.0) — full text in `LICENSE` |
| Copyright | (c) 2026 A2B Technology Corporation Pty Ltd |
| Update feed | `https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest/download/update.xml` (LibreOffice "Check for Updates" reads this; served from the newest non-prerelease release) |
| Homepage | <https://talk2view.com> |
| Source | <https://github.com/A2B-Technology-Corporation/Talk2View-Writer> |
| Privacy policy | <https://talk2view.com/legal/privacy-policy> |
| Terms of use | <https://talk2view.com/legal/terms-of-use> |
| Contact / support | <https://talk2view.com/resources/contact> |

Because the shipped `description.xml` already declares an `<update-information>`
feed, LibreOffice clients will offer in-app updates from the GitHub "latest"
release automatically — independent of the version published on
extensions.libreoffice.org.

---

## Before you submit — items to double-check

- **macOS / Windows verification.** The `.oxt` ships runtime wheels for Linux,
  macOS, and Windows, and the OS list above reflects that. Linux is fully
  verified end-to-end; macOS and Windows are wired and unit-tested but await
  **manual verification on real hardware** — and on macOS the microphone also
  needs LibreOffice's own microphone usage entitlement + a one-time system
  consent (see `docs/adrs/0041`). Verify on macOS/Windows before advertising
  them, or list Linux only for now.
