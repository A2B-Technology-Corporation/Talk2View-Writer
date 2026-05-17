# Integration tests

These tests require a running LibreOffice instance reachable over UNO
sockets. They are **skipped by default** under `make test-unit`; run
them explicitly with `make test-integration` once LibreOffice is
configured.

## Setup

1. Start LibreOffice in headless mode with a UNO socket:

   ```bash
   soffice --headless --accept="socket,host=127.0.0.1,port=2002;urp;" &
   ```

   (The default port `2002` is what `conftest.py`'s `headless_writer`
   fixture connects to.)

2. Run the integration suite:

   ```bash
   make test-integration
   ```

## What each integration test covers

The tests in this directory exercise the full UNO round-trip for each
of the 20 tools. They are organised mirroring `src/talk2view_writer/tools/`:

- `test_reading.py` — `get_document`, `get_selection`, `select_text`
- `test_writing.py` — `insert_content`, `insert_table`, `insert_image`,
  `undo_redo`, `delete_content`, `edit_table`
- `test_formatting.py` — `format_text`, `format_paragraph`, `manage_list`
- `test_search.py` — `search_document`
- `test_structure.py` — `insert_break`, `set_header_footer`,
  `insert_page_numbers`, `set_page_setup`
- `test_commenting.py` — `get_comments`, `add_comment`, `manage_comment`

Each tool's tests should at minimum:

1. Open a fresh blank document fixture.
2. Call the tool (via `all_tools()` lookup so the SDK decorator stays
   in scope).
3. Assert on the document's resulting state via UNO accessors.
4. Compare the JSON return shape against the equivalent Word fixture
   (see `tests/fixtures/word_responses/`) to enforce ADR-0021's
   byte-parity contract.

## Why these are not yet implemented

Phase D shipped tool implementations + unit-test validation paths.
The integration scaffolding (this README, fixture conventions,
`headless_writer` conftest) is in place so the tests can be written
incrementally without re-debating layout questions.

See ADR-0019 (tool aggregation), ADR-0020 (singleton context), and
ADR-0021 (JSON returns) for the contracts these tests will enforce.
