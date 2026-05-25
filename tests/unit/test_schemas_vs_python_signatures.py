"""Contract test: every TypeScript schema is a subset of Python kwargs.

The schema in ``src/web/src/tools.ts`` must mirror the Python tool's
signature: every property declared there must be a keyword argument
the matching ``talk2view_writer.tools.*`` function accepts.

When the engine receives a request, the SDK passes the schema's
property names as keyword arguments to the Python tool. If the schema
declares a property the Python signature doesn't accept, the call
raises ``TypeError`` at runtime — and the engine surfaces it as a
generic "An error occurred" to the user (Investigation #35).

This test prevents that drift by walking every ``buildWriterTool({...})``
block in ``tools.ts``, extracting the schema property names, and
asserting each one is a parameter of the matching Python function in
``talk2view_writer.tools``.

The parser is intentionally pattern-specific to the conventions used
in ``tools.ts``. If those conventions change (e.g. someone introduces
a different builder shape), the test will fail loudly rather than
silently miss a tool.
"""


from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

# Repository root → src/web/src/tools.ts
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_TS = _REPO_ROOT / "src" / "web" / "src" / "tools.ts"

# Matches a single buildWriterTool block. Captures the ``name: '...'``
# value and the entire body so we can pull ``properties`` out of it.
# We avoid trying to balance braces in a regex — instead we cut from
# ``buildWriterTool({`` to the next ``})`` followed by either a
# comma or end-of-array.
_BUILD_TOOL_RE = re.compile(
    r"buildWriterTool\(\{(.*?)\}\),\s*(?=\n)",
    re.DOTALL,
)

_NAME_RE = re.compile(r"name:\s*'([a-z_]+)'")

# Inside a ``properties: { ... }`` block, each property starts at the
# beginning of a (whitespace-indented) line with the property name
# followed by a colon and an open brace. This rejects nested objects
# whose own keys (e.g. ``description:``, ``type:``) would otherwise
# look like properties.
_PROPERTY_RE = re.compile(r"^\s{8}([a-z_]+):\s*\{", re.MULTILINE)


def _extract_properties_block(body: str) -> str | None:
    """Return the substring inside ``properties: {...}``, or None."""
    start_match = re.search(r"properties:\s*\{", body)
    if not start_match:
        return None
    # Brace-balance from the opening ``{`` of properties.
    i = start_match.end() - 1  # the opening brace
    depth = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return body[start_match.end():i]
        i += 1
    return None


def _parse_tools_ts() -> dict[str, set[str]]:
    """Parse ``tools.ts`` → {tool_name: {property_name, ...}}."""
    text = _TOOLS_TS.read_text()
    out: dict[str, set[str]] = {}
    for block in _BUILD_TOOL_RE.finditer(text):
        body = block.group(1)
        name_m = _NAME_RE.search(body)
        if not name_m:
            raise AssertionError(
                f"Found a buildWriterTool block without a name: in tools.ts:\n{body[:200]}"
            )
        name = name_m.group(1)
        props_block = _extract_properties_block(body)
        if props_block is None:
            # Tool has no properties (e.g. get_selection, get_comments).
            out[name] = set()
            continue
        out[name] = {m.group(1) for m in _PROPERTY_RE.finditer(props_block)}
    return out


def _python_kwargs(tool_name: str) -> set[str]:
    """Return the kwarg names of the Python tool with this name."""
    from talk2view_writer.tools import all_tools

    for tool in all_tools():
        if tool.__name__ == tool_name:
            sig = inspect.signature(tool)
            return {
                p.name
                for p in sig.parameters.values()
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
    raise AssertionError(
        f"tools.ts declares schema for {tool_name!r} but no matching "
        f"Python tool is registered via @tool in talk2view_writer.tools"
    )


@pytest.mark.unit
class TestSchemaVsPythonContract:
    """Every TS schema property must be a Python kwarg."""

    def test_tools_ts_parses_to_21_tools(self) -> None:
        """Sanity: the parser finds all 21 registered tools."""
        parsed = _parse_tools_ts()
        assert len(parsed) == 21, (
            f"Expected 21 buildWriterTool blocks, parser found {len(parsed)}: "
            f"{sorted(parsed)}"
        )

    @pytest.mark.parametrize(
        "tool_name",
        [
            # Reading
            "get_document",
            "get_selection",
            "select_text",
            # Writing
            "insert_content",
            "insert_table",
            "edit_table",
            "insert_image",
            "undo_redo",
            "delete_content",
            # Formatting
            "format_text",
            "format_paragraph",
            "manage_list",
            # Search
            "search_document",
            # Structure
            "insert_break",
            "set_header_footer",
            "insert_page_numbers",
            "set_page_setup",
            # Commenting
            "get_comments",
            "add_comment",
            "manage_comment",
            # Preferences
            "manage_preferences",
        ],
    )
    def test_schema_properties_are_a_subset_of_python_kwargs(
        self, tool_name: str
    ) -> None:
        """Every property declared in tools.ts must be a Python kwarg.

        Drift between the two sides was the root cause of
        Investigation #35 — schema names that the Python function
        doesn't accept TypeError at engine-invocation time.
        """
        parsed = _parse_tools_ts()
        if tool_name not in parsed:
            pytest.fail(
                f"{tool_name!r} is missing from tools.ts (or the "
                f"buildWriterTool parser didn't find it)"
            )
        schema_props = parsed[tool_name]
        python_kwargs = _python_kwargs(tool_name)
        extra_in_schema = schema_props - python_kwargs
        assert not extra_in_schema, (
            f"tools.ts declares schema properties for {tool_name!r} "
            f"that the Python function does not accept: "
            f"{sorted(extra_in_schema)}. "
            f"Python signature: {sorted(python_kwargs)}. "
            f"Either fix tools.ts (rename / remove the property) "
            f"or add the kwarg to the Python function."
        )
