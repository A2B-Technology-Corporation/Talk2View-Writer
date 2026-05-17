"""Writer tool implementations registered with the Talk2View SDK.

Each submodule exposes ``@tool``-decorated Python functions that the SDK
calls (via the ``interrupt → resume`` loop) when the cloud agent decides to
use a tool. Function signatures + Google-style docstrings drive the schema
the LLM sees, so type hints and arg descriptions matter.

The 26 tools mirror ``Talk2View-Word/src/taskpane/tools/*.ts`` — see the
mapping table in the project plan and ``CLAUDE.md`` for the
TypeScript-to-UNO equivalence.
"""
