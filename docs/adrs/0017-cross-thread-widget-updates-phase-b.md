# ADR-0017: Cross-thread widget updates from the chat worker (Phase B only)

**Status:** Accepted (interim, Phase B)
**Date:** 2026-05-17
**Phase:** B
**Supersedes (partially):** ADR-0009 (the "marshal back to UI thread"
clause is relaxed for Phase B only)

## Context

ADR-0009 mandates a worker-thread + UI-thread-drain pattern for SDK
iteration, with all UNO mutations marshalled to the UI thread. For
**document mutation** (tool execution, Phase C+) this is non-negotiable
— writing to the live `XText` from a background thread reliably
corrupts state and crashes LibreOffice.

But Phase B has **no tools yet**. The only UNO calls the chat worker
makes are:

- `history_field.getModel().setPropertyValue("Text", current + chunk)`
- `composer_field.getModel().setPropertyValue("Enabled", ...)`
- `send_button.getModel().setPropertyValue("Enabled", ...)`
- `status_label.getModel().setPropertyValue("Label", ...)`

These touch passive widget *model* properties — not the document
model, not the active VCL window's paint loop. Empirically this works
across LibreOffice 7.x; SpeedWriter's voice polling follows the same
pattern (`TranscriptionPoller` calls UNO from a `threading.Timer`).

The proper UI-thread marshalling primitive in PyUNO is poorly
documented — see Investigation #5 — and implementing it speculatively
in Phase B would block the milestone for an unverified gain.

## Decision

For **Phase B only**, the chat worker thread writes directly to
widget model properties. Methods involved are tightly scoped:

- `Talk2ViewPanel._append_history(text)`
- `Talk2ViewPanel._set_status(text)`
- `Talk2ViewPanel._set_busy(busy)`

All three are explicitly documented as "may be called from worker
thread" in their docstrings (and this ADR).

**Phase C will introduce a UI-thread marshalling queue** (a
`queue.Queue` + a periodic `XScheduler`/`XCallback` drain) for tool
execution. At that point the chat worker should migrate to use the
same queue, and the direct-write pattern becomes legacy.

## Alternatives considered

- **Block Phase B on the marshalling-queue design.** Worth doing
  *eventually* but the queue is materially more complex than the
  Phase B chat needs. Shipping the chat loop sooner gets us real
  feedback on the UX (history rendering, composer ergonomics) before
  the threading machinery lands.
- **Acquire `solar_mutex` on the worker before UNO calls.** PyUNO
  doesn't expose `solar_mutex_acquire()` cleanly; what's exposed via
  `uno` module is brittle across LibreOffice versions. Tracked in
  Investigation #5 as a Phase B spike.
- **Spawn the worker as a UNO `XJob`** so it inherits LibreOffice's
  thread model. UNO jobs are scoped to dispatch URLs, not background
  iteration — wrong tool for the job.

## Consequences

**Pros**
- Phase B ships in days, not weeks.
- Code stays simple; the threading model is "spawn thread, iterate,
  call setPropertyValue on widgets".
- If the marshalling-queue spike (Investigation #5) reveals
  `solar_mutex` works fine, we can replace the direct-write pattern
  cheaply.

**Cons**
- **Technically unsafe.** Cross-thread UNO calls can deadlock or
  crash. We're banking on the narrow surface (widget model property
  writes, no document mutation, no paint loop interaction) keeping
  the risk negligible.
- **No guarantees across LibreOffice versions.** Behaviour could
  regress in a future LibreOffice release; we'd see it as random
  panel-related crashes that are hard to reproduce.
- **Phase C migration cost.** When the marshalling queue lands, the
  three methods above need to migrate; until then they're a known
  technical debt.

**Follow-up**
- Phase B spike: write a small test confirming
  `solar_mutex_acquire()` works from PyUNO. Update Investigation #5
  with findings.
- Phase C: implement the UI-thread queue, migrate
  `_append_history`/`_set_status`/`_set_busy` to use it, then update
  this ADR's Status to **Superseded by ADR-NNNN**.

## References

- Code:
  `src/talk2view_writer/ui/sidebar_panel.py` — "Cross-thread widget
  writers" section
- Reference: `SpeedWriter-LibreOffice/src/speedwriter/voice/manager.py`
  (TranscriptionPoller direct-call pattern)
- Related ADRs: ADR-0009 (canonical threading rules)
- Investigations: `docs/investigations.md` #5
