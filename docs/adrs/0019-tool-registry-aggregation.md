# ADR-0019: Tool registry aggregation via per-module ``TOOLS`` list

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** C

## Context

We will end up with 26 ``@tool``-decorated functions across six
modules (``reading``, ``writing``, ``formatting``, ``search``,
``structure``, ``commenting``). The extension singleton needs to
register all of them with the SDK exactly once per process via
``sdk.register_tools([...])``.

How should the registry be assembled?

1. **Per-module ``TOOLS = [...]`` list, aggregated by
   ``tools/__init__.py``.** Each module's tools are an explicit,
   easy-to-grep list.
2. **Auto-discovery via ``pkgutil.walk_packages`` + ``__tool__``
   attribute introspection.** Magical; requires importing every
   module just to find tools.
3. **Decorator-side mutation of a global registry.** The ``@tool``
   decorator pushes the function into a module-level set. Concise but
   import-order-sensitive and a side-effect in disguise.

## Decision

Use **option 1**. Each tool module declares an explicit
``TOOLS = [tool_a, tool_b]`` list at the bottom of the file.
``src/talk2view_writer/tools/__init__.py::all_tools()`` aggregates
them:

```python
def all_tools() -> List[Callable]:
    return [
        *_READING_TOOLS,
        *_WRITING_TOOLS,
        # ... other groups added as they ship
    ]
```

``Talk2ViewWriterExtension.sdk`` (the lazy SDK accessor in
``extension.py``) calls ``all_tools()`` exactly once per process
inside the same lock that creates the SDK client, then sets
``_tools_registered = True`` so re-entries skip.

## Alternatives considered

- **Auto-discovery.** Reads cleaner ("just drop a file") but adds
  import-time magic that complicates testing (e.g. monkey-patching a
  single tool module without dragging in all the others). Bug surface
  for cyclic imports.
- **Global decorator side-effect.** The ``@tool`` decorator in the
  SDK is third-party — we can't safely extend it with a hidden
  registry side-effect. We'd need our own wrapper, defeating part of
  the simplicity argument.

## Consequences

**Pros**
- Adding a new tool is a two-line change: define the function, add
  it to ``TOOLS``. Removing one is the inverse.
- Easy to grep for: every tool name appears in exactly one ``TOOLS``
  list.
- Modules can be imported independently in tests — no transitive
  import of unrelated tools.
- ``all_tools()`` returns a fresh list every call, so tests can
  monkey-patch a single module without poisoning the rest.

**Cons**
- Manual: if you ``@tool``-decorate a function and forget to append
  it to ``TOOLS``, it silently doesn't get registered. Mitigated
  by Phase D commit hygiene + a test
  (``test_all_tools_includes_expected_phase_d_set``) that asserts
  the expected name set per phase.
- The ``__init__.py`` grows one import + one splat per group as
  modules are added. Marginal.

**Follow-up**
- Phase D: extend ``all_tools()`` as each group lands. Update the
  expected-set assertion in ``tests/unit/test_tools.py`` per phase.
- Consider a lint rule (or a unit test) that asserts every
  ``@tool``-decorated function in ``src/talk2view_writer/tools/`` is
  reachable from ``all_tools()``. Would catch the
  "forgot to add to TOOLS" foot-gun automatically.

## References

- Code: `src/talk2view_writer/tools/__init__.py`
- Code: each `src/talk2view_writer/tools/<group>.py` ends with a
  ``TOOLS = [...]`` list.
- Code: `src/talk2view_writer/extension.py::Talk2ViewWriterExtension.sdk`
  (registration on first access).
- Tests: `tests/unit/test_tools.py` (4 tests)
- Related ADRs: ADR-0008 (tool decorator), ADR-0018 (UI marshalling)
