---
name: Bug report
about: Something in Talk2View-Writer isn't working as expected
title: ''
labels: bug
assignees: ''
---

## What happened

<!-- One or two sentences. What did you do, what did you expect, what
actually happened? -->

## Reproduction steps

1.
2.
3.

## Environment

- **LibreOffice version**: <!-- `Help → About LibreOffice`, paste the
  full version string + build number -->
- **OS**: <!-- e.g. Ubuntu 24.04, macOS 14.5, Windows 11 -->
- **CPU arch**: <!-- x86_64 / arm64 -->
- **Talk2View-Writer version**: <!-- from `Tools → Extension Manager`
  or the .oxt filename -->

## Logs

LibreOffice's Talk2View-Writer extension logs to stderr. To capture
them, launch LibreOffice from a terminal:

```bash
# Linux
soffice --writer 2>&1 | tee /tmp/talk2view.log

# macOS
/Applications/LibreOffice.app/Contents/MacOS/soffice --writer 2>&1 | tee /tmp/talk2view.log

# Windows (PowerShell)
& 'C:\Program Files\LibreOffice\program\soffice.exe' --writer 2>&1 | Tee-Object -FilePath $env:TEMP\talk2view.log
```

Reproduce the issue, then attach the resulting log file (or paste
the relevant `talk2view_writer.*` lines).

If the extension failed to load at all, check the wheel-loader
output:

```bash
# Linux/macOS — find the install path and dump bundled wheel tags
ls ~/.config/libreoffice/4/user/uno_packages/cache/uno_packages/*/Talk2ViewWriter.oxt/pythonpath/_vendored_wheels/
```

## What you've already tried

<!-- Optional but very helpful — saves us asking. -->
