# ADR-0005: Consume Python SDK via editable path dependency

**Status:** Accepted
**Date:** 2026-05-17
**Phase:** A

## Context

`pyproject.toml` lists `talk2view` (the Python SDK) as a dependency.
The SDK ships from `Talk2View-Platform/packages/sdk-python/`. There is
currently **no published PyPI release** — the SDK lives only inside
the Talk2View monorepo.

We have to wire this so that:

- developer machines can install the SDK from the sibling checkout,
- SDK changes flow into Talk2View-Writer without re-publishing,
- CI / build hosts can resolve the dep deterministically.

## Decision

Use `uv`'s `[tool.uv.sources]` to point the `talk2view` dependency at
the sibling path as **editable**:

```toml
[tool.uv.sources]
talk2view = { path = "../Talk2View-Platform/packages/sdk-python", editable = true }
```

The extension's runtime gets the SDK by bundling it into
`pythonpath/` at `make build` time (see ADR-0006); the editable path
dep is purely a developer-time concern.

## Alternatives considered

- **Vendor a copy of the SDK** into `vendor/talk2view/`. Avoids the
  path-dep, but every SDK change becomes a sync chore and we lose the
  benefit of upstream bug fixes.
- **Publish the SDK to PyPI and pin a version**. Right answer
  long-term but blocked on `Talk2View-Platform` shipping a public PyPI
  release; tracked as Investigation #2 in `docs/investigations.md`.
- **Git URL dependency** (`talk2view @ git+ssh://...`). Works but
  doesn't allow local edits to flow without a commit/push, which is
  exactly the workflow we want during co-development.

## Consequences

**Pros**
- Local SDK changes are picked up immediately by `make test`,
  `make build`, etc.
- No publishing step in the developer loop.

**Cons**
- The Talk2View-Writer checkout assumes Talk2View-Platform is checked
  out at `../Talk2View-Platform/`. Solo-cloning Talk2View-Writer
  breaks until the user clones Platform too.
- CI that doesn't check out the monorepo can't `uv sync`.
- The runtime bundling step (`make build`) re-resolves the SDK via
  `.venv/lib/site-packages/talk2view`, which is the editable
  install — it pulls the source tree, not a wheel. Need to verify
  that `__pycache__` excludes don't drop any data files.

**Follow-up**
- File issue on `Talk2View-Platform` to publish the Python SDK to
  PyPI (Investigation #2).
- Once published, swap to `talk2view >= X.Y` in `pyproject.toml` and
  delete `[tool.uv.sources]`.

## References

- Code: `pyproject.toml` — `[project.dependencies]` and
  `[tool.uv.sources]`
- SDK: `Talk2View-Platform/packages/sdk-python/`
- Related ADRs: ADR-0006 (runtime bundling)
