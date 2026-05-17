# ADR-0020: Tool bodies fetch UNO context via the extension singleton

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** C

## Context

Tool functions need an ``XComponentContext`` to call
``ServiceManager.createInstanceWithContext(...)`` for ``Desktop``,
``DispatchHelper``, etc. The SDK calls each tool with only the
arguments the agent supplied — there is no implicit ``ctx`` parameter.

How does a tool obtain ``ctx``?

1. **Pass ``ctx`` as a hidden first parameter.** Use a wrapper that
   shifts arguments. Awkward — every tool's true signature would
   diverge from its schema-visible signature.
2. **Read ``ctx`` from the extension singleton.** ``get_extension_or_raise().ctx``
   inside the tool body. Cheap, but couples each tool to the
   singleton.
3. **Thread-local context.** Push ``ctx`` onto a ``threading.local``
   before each tool invocation, pop after. Adds a dispatcher
   contract just for one variable.
4. **``uno.getComponentContext()`` at the top of each tool.** Works
   from inside LibreOffice's Python interpreter but the binding
   between this call and the *correct* document/frame is fragile if
   multiple documents are open.

## Decision

Tool bodies call ``get_extension_or_raise()`` from
``talk2view_writer.extension`` and read ``ext.ctx``. Example:

```python
@tool
@ui_thread_tool
def insert_content(content: str) -> str:
    """..."""
    ext = get_extension_or_raise()
    doc = get_writer_document(ext.ctx)
    ...
```

``get_extension_or_raise()`` raises ``RuntimeError`` if the singleton
hasn't been instantiated yet — which can't happen during normal flow
(the sidebar panel that triggered the chat had to instantiate it)
and means a real bug if it does.

## Alternatives considered

- **Hidden ``ctx`` argument.** Would require either pre-binding via
  ``functools.partial`` (changes signature visible to ``inspect``)
  or a custom wrapper that knows to shift. Both add complexity for
  no test/maintenance benefit.
- **Thread-local context.** Slightly cleaner separation (tools
  don't import the extension module), but ``threading.local`` set
  by the dispatcher has to be unset on every code path including
  exceptions. Failure mode is silent (subsequent tools see stale
  ctx). Singleton access is simpler and the failure mode is loud.
- **``uno.getComponentContext()``.** Returns the *current* UNO
  context from the import chain, which is not necessarily the
  context the extension was instantiated with. In practice they
  match, but the implicit linkage is brittle.

## Consequences

**Pros**
- Tool signatures are exactly what the SDK + agent see — no hidden
  parameters.
- Every tool's first line is the same boilerplate; easy to grep.
- The dependency on the singleton is explicit, so test mocks can
  monkey-patch ``get_extension_or_raise`` to inject a fake context.

**Cons**
- Tools are not pure functions of their arguments. They have a
  hidden dependency on global state (the singleton). For *tools*
  specifically — which are inherently stateful host-app integrations
  — this is fine, but it does mean a tool can't be unit-tested in
  isolation without setting up the singleton.
- Tight coupling to the singleton: refactoring the extension's
  ownership model would ripple through 26 tool files.

**Follow-up**
- Phase D: keep the boilerplate consistent across all 26 tools.
  Consider a small helper ``ctx()`` that wraps
  ``get_extension_or_raise().ctx`` if the noise becomes excessive.
- Phase F: integration tests can mock ``get_extension_or_raise`` to
  inject a controlled ``ctx`` for headless ``soffice`` test runs.

## References

- Code: `src/talk2view_writer/tools/_base.py::get_writer_document`
- Code: `src/talk2view_writer/extension.py::get_extension_or_raise`
- Code: `src/talk2view_writer/tools/reading.py`,
  `tools/writing.py` (canonical usage)
- Related ADRs: ADR-0008 (tool decorator), ADR-0018 (UI marshalling),
  ADR-0019 (registry)
