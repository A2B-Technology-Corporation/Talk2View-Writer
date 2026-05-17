# ADR-0008: Tools registered as `@tool`-decorated Python functions

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A (planning), Phase C (implementation)

## Context

The Talk2View cloud agent needs a description of every tool the client
can execute. Talk2View-Word builds these descriptions as TypeScript
objects in `src/taskpane/tools/builder.ts` — explicit JSON Schemas
+ a handler function per tool.

The Python SDK takes a different approach:

```python
@tool
def insert_content(target: str, content: str) -> str:
    """Insert content at the named target.

    Args:
        target: Section heading or paragraph to insert at.
        content: Markdown content to insert.
    """
    ...

t2v.tools.register_from_functions([insert_content])
```

The `@tool` decorator introspects the function's **signature** and
**Google-style docstring** to build the schema the cloud agent sees.
No hand-written JSON Schema.

## Decision

Phase D implements all 26 tools as plain Python functions decorated
with `@tool`, grouped into modules under
`src/talk2view_writer/tools/` (`reading.py`, `writing.py`, …). The
extension singleton calls `t2v.tools.register_from_functions([...all
26...])` once at session creation.

## Alternatives considered

- **Mirror Talk2View-Word's explicit JSON Schema pattern.** Would
  give us tighter control over the schema but loses the docstring →
  schema benefit. Also makes maintenance harder — every schema field
  is duplicated between the function signature and the JSON.
- **`langchain_core.tools.tool`** — would pull in a heavy dependency
  for one decorator when the Talk2View SDK already provides one
  tailored to the platform's tool-call protocol.

## Consequences

**Pros**
- One source of truth per tool: the function. Type hints feed
  argument types, docstring feeds the description and per-arg help.
- Refactoring a tool's signature automatically updates the schema
  the agent sees — no risk of drift.
- Test discoverability — each tool is just a callable.

**Cons**
- The cloud agent sees a schema generated from our docstrings, so
  docstring quality is now load-bearing for agent behaviour.
  Inconsistent docs → confused agent. Reviewer must care about
  docstrings.
- `@tool` mutates the function in place (adds metadata). Combined
  with UNO threading (ADR-0009) we need to be careful that tool
  callables are stateless or guarded — see ADR-0009.
- If a tool needs to expose a *different* schema than its Python
  signature suggests (e.g. `Annotated[str, Literal["bold","italic"]]`),
  we may need to either drop down to manual schemas for that tool or
  push enhancements upstream into the SDK's introspection logic.

**Follow-up**
- Phase D ports each tool with care to docstring style — see
  `src/talk2view_writer/tools/__init__.py` module docstring.
- Cross-check the SDK's `tools.py::_function_to_schema` (or whatever
  the equivalent introspection is named) against Talk2View-Word's
  schemas to make sure semantics survive — the agent should see
  equivalent tool descriptions on either client.

## References

- SDK: `Talk2View-Platform/packages/sdk-python/src/talk2view/tools.py`
- Word equivalent: `Talk2View-Word/src/taskpane/tools/builder.ts`
- Related ADRs: ADR-0002 (cloud SDK), ADR-0009 (threading)
