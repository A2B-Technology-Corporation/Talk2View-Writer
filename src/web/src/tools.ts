/**
 * writerTools — 5 MVP tools (ADR-0030 step 3) routed through the
 * pywebview bridge.
 *
 * Schemas mirror the corresponding Word tools (Talk2View-Word/src/
 * taskpane/tools/) so the engine sees an equivalent interface. The
 * ``execute`` callback just proxies to ``invokeTool(name, args)``;
 * the actual UNO work happens in the matching @ui_thread_tool
 * Python function in src/talk2view_writer/tools/.
 *
 * Each tool returns the Python tool's return value verbatim. The
 * Python tools already return JSON-encoded strings (ADR-0021), so
 * the SDK gets a stringifiable payload either way.
 */
import type { ClientTool } from '@talk2view/sdk';
import { invokeTool } from './bridge';

function buildWriterTool(def: {
  name: string;
  description: string;
  parameters: ClientTool['parameters'];
}): ClientTool {
  return {
    name: def.name,
    description: def.description,
    parameters: def.parameters,
    execute: async (args: Record<string, unknown>) => {
      const result = await invokeTool(def.name, args);
      // Python side returns either a string (JSON-encoded already)
      // or an object. The SDK accepts both; if it's an object we
      // stringify so the engine's tool-result framing stays
      // consistent with Word.
      return typeof result === 'string' ? result : JSON.stringify(result);
    },
  };
}

export const writerTools: ClientTool[] = [
  buildWriterTool({
    name: 'get_document',
    description:
      'Read the document body: paragraphs (with index + style), tables, ' +
      'document properties, sections. Use this before insert_content / ' +
      'format_paragraph / delete_content to discover what is in the doc. ' +
      'Paginate with start_index/count for long documents.',
    parameters: {
      type: 'object',
      properties: {
        start_index: {
          type: 'number',
          description:
            'Zero-based paragraph index to start reading from. Default 0.',
        },
        count: {
          type: 'number',
          description:
            'Max number of paragraphs to return. Default 100; max 100.',
        },
        include_font_details: {
          type: 'boolean',
          description:
            'Include per-run font name, size, bold, italic, underline, ' +
            'color. Default false (cheaper). Set true only when needed.',
        },
      },
    },
  }),

  buildWriterTool({
    name: 'get_selection',
    description:
      'Return the currently selected (highlighted) text in the document. ' +
      'Empty string if nothing is selected. Call before insert_content ' +
      'with location="replace_selection" or "after_selection".',
    parameters: {
      type: 'object',
      properties: {},
    },
  }),

  buildWriterTool({
    name: 'insert_content',
    description:
      'Insert one or more styled paragraphs. Use ``blocks`` to insert ' +
      'multiple paragraphs in one call (preferred). Target by ``location`` ' +
      "(``start``/``end``/``replace_selection``/``after_selection``/" +
      '``before_paragraph``/``after_paragraph``) or by ``target_query`` ' +
      '(find text and replace it inline). NEVER fake a heading with ' +
      'format_text — set ``style`` here.',
    parameters: {
      type: 'object',
      properties: {
        text: {
          type: 'string',
          description:
            'Single paragraph to insert. Mutually exclusive with blocks.',
        },
        blocks: {
          type: 'array',
          description:
            'Insert multiple paragraphs in one call. Array of ' +
            '{text, style?} objects.',
        },
        style: {
          type: 'string',
          description:
            'LibreOffice paragraph style for single-paragraph insertion ' +
            "(ignored when using blocks). E.g. 'Heading 1', 'Heading 2', " +
            "'Default Paragraph Style', 'Quotations'.",
        },
        location: {
          type: 'string',
          enum: [
            'start',
            'end',
            'before_paragraph',
            'after_paragraph',
            'after_selection',
            'replace_selection',
          ],
          description:
            "Where to insert. Defaults to 'end' if omitted.",
        },
        target_query: {
          type: 'string',
          description:
            'Find this text and replace it with the inserted content. ' +
            'Mutually exclusive with location.',
        },
        paragraph_index: {
          type: 'number',
          description:
            "Required when location is 'before_paragraph' or " +
            "'after_paragraph'. Zero-based.",
        },
      },
    },
  }),

  buildWriterTool({
    name: 'format_text',
    description:
      'Apply character-level formatting (bold, italic, underline, ' +
      'strikethrough, super/subscript, color, highlight, font family, ' +
      'font size) to text matching ``query`` or every character in the ' +
      'paragraph at ``paragraph_index``. Use ``queries`` to apply ' +
      'different formatting to different snippets in one call (up to 20). ' +
      'Use ``match_index`` to disambiguate when ``query`` matches multiple ' +
      'places. NEVER fake a heading with bold + size — use format_paragraph ' +
      "to set the paragraph's style instead.",
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description:
            'Text to find and format (case-insensitive). Mutually ' +
            'exclusive with paragraph_index and queries.',
        },
        paragraph_index: {
          type: 'number',
          description:
            'Format every character in this paragraph. Mutually ' +
            'exclusive with query and queries.',
        },
        queries: {
          type: 'array',
          description:
            'Batch mode. Array of {query|paragraph_index, ...format} ' +
            'objects — up to 20 items per call.',
        },
        bold: { type: 'boolean', description: 'Bold on/off.' },
        italic: { type: 'boolean', description: 'Italic on/off.' },
        underline: {
          type: 'boolean',
          description:
            'Underline on/off (single-line). For other underline ' +
            'styles use underline_style.',
        },
        underline_style: {
          type: 'string',
          description:
            "One of 'none', 'single', 'double', 'dotted', 'dashed', 'wave'.",
        },
        strikethrough: {
          type: 'boolean',
          description: 'Strikethrough on/off.',
        },
        superscript: {
          type: 'boolean',
          description:
            'Superscript on/off. Mutually exclusive with subscript.',
        },
        subscript: {
          type: 'boolean',
          description:
            'Subscript on/off. Mutually exclusive with superscript.',
        },
        color: {
          type: 'string',
          description:
            "Hex RGB foreground colour, no '#' (e.g. 'FF0000' for red).",
        },
        highlight: {
          type: 'string',
          description:
            "Highlight colour name (case-sensitive). One of: 'Yellow', " +
            "'Green', 'Turquoise', 'Pink', 'Blue', 'Red', 'DarkBlue', " +
            "'Teal', 'Violet', 'DarkRed', 'DarkYellow', 'Gray25', " +
            "'Gray50', 'Black', 'White'. Use 'NoColor' to clear.",
        },
        size: {
          type: 'number',
          description: 'Font size in points. Must be > 0.',
        },
        font: {
          type: 'string',
          description:
            "Font family name as displayed (e.g. 'Arial', " +
            "'Times New Roman', 'Liberation Serif'). Whatever the font " +
            'menu in Writer lists is fair game.',
        },
        match_index: {
          type: 'number',
          description:
            'When multiple matches exist, pick the n-th (0-based). ' +
            'Default 0 (first match).',
        },
      },
    },
  }),

  buildWriterTool({
    name: 'format_paragraph',
    description:
      'Paragraph-level formatting: style (Heading1, Title, Quote, ...), ' +
      'alignment, spacing, indents, page-break and keep-together flags. ' +
      'Use ``paragraph_indices`` to format multiple paragraphs in one ' +
      'call. For character-level styling (bold, font, color) use ' +
      'format_text instead.',
    parameters: {
      type: 'object',
      properties: {
        paragraph_index: {
          type: 'number',
          description:
            'Single zero-based paragraph index. Mutually exclusive ' +
            'with paragraph_indices.',
        },
        paragraph_indices: {
          type: 'array',
          description:
            'List of zero-based integer indices (NOT strings) to format.',
        },
        style: {
          type: 'string',
          description:
            "Built-in style name: 'Normal', 'Heading1', 'Heading2', " +
            "'Heading3', 'Title', 'Subtitle', 'Quote', 'ListParagraph'.",
        },
        alignment: {
          type: 'string',
          description: "'left', 'center', 'right', 'justified'.",
        },
        space_before: {
          type: 'number',
          description: 'Space before paragraph in points. >= 0.',
        },
        space_after: {
          type: 'number',
          description: 'Space after paragraph in points. >= 0.',
        },
        line_spacing: {
          type: 'number',
          description: 'Line spacing in points. > 0.',
        },
        left_indent: {
          type: 'number',
          description: 'Left indent in points.',
        },
        right_indent: {
          type: 'number',
          description: 'Right indent in points.',
        },
        first_line_indent: {
          type: 'number',
          description:
            'First-line indent in points. Negative = hanging indent.',
        },
        keep_together: {
          type: 'boolean',
          description: 'Keep all lines of this paragraph on one page.',
        },
        keep_with_next: {
          type: 'boolean',
          description: 'Keep on same page as the next paragraph.',
        },
        page_break_before: {
          type: 'boolean',
          description: 'Force a page break before this paragraph.',
        },
      },
    },
  }),

  buildWriterTool({
    name: 'manage_preferences',
    description:
      "Read or change Talk2View-Writer preferences (the extension's own " +
      "settings — separate from the document's settings). " +
      "Use ``action='list'`` to see every preference and its value. " +
      "Use ``action='get'``, ``'set'``, or ``'reset'`` with ``key`` for " +
      "individual reads/writes. Known keys: " +
      "``ai_track_changes_enabled`` (default true) — when true, every " +
      "AI edit to the document is recorded as a tracked change the user " +
      'can review (Edit → Track Changes → Manage). When false, AI edits ' +
      "land directly without going through redlining. This is independent " +
      "of the document's global Track Changes toggle: with this off, the " +
      "user's own edits keep whatever global setting they had.",
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['list', 'get', 'set', 'reset'],
          description:
            "'list' returns everything. 'get'/'set'/'reset' need ``key``; " +
            "'set' also needs ``value``.",
        },
        key: {
          type: 'string',
          description:
            "Preference name. Currently: 'ai_track_changes_enabled'.",
        },
        value: {
          // Schema-typed as boolean because the only currently-declared
          // preference (``ai_track_changes_enabled``) is a bool. When
          // we add a non-bool preference, switch to a union schema.
          type: 'boolean',
          description:
            "New value for action='set'. For " +
            "``ai_track_changes_enabled`` use true or false.",
        },
      },
      required: ['action'],
    },
  }),

  buildWriterTool({
    name: 'search_document',
    description:
      'Find or find-and-replace text in the document body. Omit ' +
      '``replace_with`` to count matches and return context; pass ' +
      '``replace_with`` (use ``""`` to delete) to substitute all matches. ' +
      'To replace AND style the new text, pass ``replace_format``. ' +
      "Doesn't change formatting of unrelated text; for that use format_text.",
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Text to search for. <=255 characters.',
        },
        replace_with: {
          type: 'string',
          description:
            'If provided, replaces ALL matches with this text. Use ``""`` ' +
            'to delete all matches.',
        },
        replace_format: {
          type: 'object',
          description:
            'Inline formatting applied to the replaced text. Fields: ' +
            'bold, italic, underline, color (hex RGB), highlight ' +
            "(colour name), size (points). Only with replace_with.",
        },
        match_case: {
          type: 'boolean',
          description: 'Case-sensitive search. Default false.',
        },
        match_whole_word: {
          type: 'boolean',
          description: 'Whole-word match. Default false.',
        },
        match_wildcards: {
          type: 'boolean',
          description:
            'Treat ``query`` as a regex (Writer-equivalent to Word ' +
            'wildcards). Default false.',
        },
        match_prefix: {
          type: 'boolean',
          description: 'Match only at start of words. Default false.',
        },
        match_suffix: {
          type: 'boolean',
          description: 'Match only at end of words. Default false.',
        },
      },
    },
  }),

  // ---- Reading: select_text ----------------------------------------------
  buildWriterTool({
    name: 'select_text',
    description:
      'NICHE. Highlight a range visually for the user. For any actual ' +
      'operation, direct-targeting tools are better (insert_content ' +
      'target_query, format_text query/queries, search_document). Only ' +
      'use when the user explicitly asks for a visible selection.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description:
            'Text to find and select. Mutually exclusive with ' +
            'paragraph_index.',
        },
        match_index: {
          type: 'number',
          description:
            'When the query matches multiple places, pick the n-th ' +
            '(0-based). Defaults to 0.',
        },
        paragraph_index: {
          type: 'number',
          description:
            'Select the entire paragraph at this zero-based index. ' +
            'Mutually exclusive with query.',
        },
      },
    },
  }),

  // ---- Writing: insert_table --------------------------------------------
  buildWriterTool({
    name: 'insert_table',
    description:
      'Insert a new table at the start or end of the document. Pass ' +
      "``data`` (2D string array, first row = header) in the same call so " +
      'cells are populated up front. For modifying existing tables use ' +
      'edit_table.',
    parameters: {
      type: 'object',
      properties: {
        rows: {
          type: 'number',
          description: 'Total number of rows including header. >= 1.',
        },
        columns: {
          type: 'number',
          description: 'Number of columns. >= 1.',
        },
        location: {
          type: 'string',
          enum: ['start', 'end'],
          description:
            "Where to insert. 'start' = before all content; 'end' = after.",
        },
        data: {
          type: 'array',
          description:
            '2D string array of cell values (array of arrays of ' +
            'strings). First inner array is the header row. e.g. ' +
            '[["Item","Qty"],["Apple","5"]].',
        },
      },
      required: ['rows', 'columns', 'location'],
    },
  }),

  // ---- Writing: edit_table ----------------------------------------------
  buildWriterTool({
    name: 'edit_table',
    description:
      'Modify an existing table. Actions: edit_cell, add_rows, ' +
      'delete_rows, add_columns, delete_columns. Call get_document first ' +
      'to get table_index + current row/column counts.',
    parameters: {
      type: 'object',
      properties: {
        table_index: {
          type: 'number',
          description: 'Zero-based table index from get_document.',
        },
        action: {
          type: 'string',
          enum: [
            'edit_cell',
            'add_rows',
            'delete_rows',
            'add_columns',
            'delete_columns',
          ],
          description: 'Operation to perform.',
        },
        row: {
          type: 'number',
          description:
            'Zero-based row index. Required for edit_cell and delete_rows.',
        },
        column: {
          type: 'number',
          description:
            'Zero-based column index. Required for edit_cell and delete_columns.',
        },
        value: {
          type: 'string',
          description: 'New cell text for edit_cell.',
        },
        count: {
          type: 'number',
          description: 'Number of rows/columns to add or delete. Default 1.',
        },
        insert_location: {
          type: 'string',
          enum: ['start', 'end'],
          description: "Required for add_rows / add_columns. 'start' or 'end'.",
        },
        values: {
          type: 'array',
          description:
            'Optional 2D string array to populate added rows/columns ' +
            "in the same call as 'add_rows' / 'add_columns'. Pair " +
            'with count = values.length so each added row/column gets ' +
            'filled in one tool call — saves several edit_cell follow-ups. ' +
            'Example for add_rows: values=[["Banana","12","0.50"]] with ' +
            'count=1.',
        },
      },
      required: ['table_index', 'action'],
    },
  }),

  // ---- Writing: insert_image --------------------------------------------
  buildWriterTool({
    name: 'insert_image',
    description:
      'Insert a base64-encoded image (PNG/JPEG/GIF). base64_data must be ' +
      "raw — no 'data:image/...;base64,' prefix. Set both width and " +
      'height (in points, 72pt = 1in) for size control, or omit both for ' +
      'original dimensions.',
    parameters: {
      type: 'object',
      properties: {
        base64_data: {
          type: 'string',
          description: 'Raw base64-encoded image bytes (no data URI prefix).',
        },
        location: {
          type: 'string',
          enum: ['start', 'end', 'after_selection'],
          description: 'Where to insert the image.',
        },
        width: {
          type: 'number',
          description: 'Width in points. > 0. Pair with height.',
        },
        height: {
          type: 'number',
          description: 'Height in points. > 0. Pair with width.',
        },
      },
      required: ['base64_data', 'location'],
    },
  }),

  // ---- Writing: undo_redo -----------------------------------------------
  buildWriterTool({
    name: 'undo_redo',
    description:
      'Undo or redo the last N document-level operations from the ' +
      "document's undo stack. Use when the user asks to revert a recent " +
      'change. NOT a substitute for delete_content — undo reverses the ' +
      'last edit (yours or theirs); delete_content removes specific text.',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['undo', 'redo'],
          description: "'undo' reverses the last edit; 'redo' replays it.",
        },
        count: {
          type: 'number',
          description: 'How many steps to undo/redo. Defaults to 1.',
        },
      },
      required: ['action'],
    },
  }),

  // ---- Writing: delete_content ------------------------------------------
  buildWriterTool({
    name: 'delete_content',
    description:
      'Delete WHOLE paragraphs by index, range, or text query. For ' +
      'deleting inline text within paragraphs (keeping the paragraph), ' +
      'prefer search_document(query, replace_with=""). For removing list ' +
      "formatting without deleting text, use manage_list(action='remove').",
    parameters: {
      type: 'object',
      properties: {
        paragraph_index: {
          type: 'number',
          description: 'Zero-based index of a single paragraph to delete.',
        },
        start_index: {
          type: 'number',
          description:
            'Inclusive start of paragraph range. Requires end_index.',
        },
        end_index: {
          type: 'number',
          description: 'Inclusive end of paragraph range. >= start_index.',
        },
        query: {
          type: 'string',
          description:
            'Text to match and delete (preserving paragraph structure). ' +
            'Kept for back-compat — prefer search_document(query, ' +
            'replace_with="") for inline text.',
        },
        match_case: {
          type: 'boolean',
          description: 'Case-sensitive query. Defaults to false.',
        },
      },
    },
  }),

  // ---- Formatting: manage_list ------------------------------------------
  buildWriterTool({
    name: 'manage_list',
    description:
      'Apply or remove bullet / numbered list formatting on paragraphs. ' +
      "action='add' converts paragraphs to list items (style 'List Bullet' " +
      "for bullet, 'List Number' for number). action='remove' reverts to " +
      "'Default Paragraph Style'. Indent levels are supported via level.",
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['add', 'remove'],
          description: "'add' to bullet/number paragraphs; 'remove' to revert.",
        },
        paragraph_indices: {
          type: 'array',
          description:
            'Zero-based integer paragraph indices (NOT strings) to ' +
            'apply the action to. Required.',
        },
        list_type: {
          type: 'string',
          enum: ['bullet', 'number'],
          description: "Required for action='add'. 'bullet' or 'number'.",
        },
        level: {
          type: 'number',
          description: 'Indent level (0 = top-level). Default 0.',
        },
      },
      required: ['action', 'paragraph_indices'],
    },
  }),

  // ---- Structure: insert_break ------------------------------------------
  buildWriterTool({
    name: 'insert_break',
    description:
      'Insert a page break or section break. ' +
      "type='page' = simple new-page break. " +
      "type='section_next_page' = closest LO equivalent to a Word section " +
      "break (forces next paragraph onto a new page-style boundary). " +
      "type='section_continuous' degrades to a plain paragraph break in " +
      "LibreOffice. location is 'start' or 'end' of the document.",
    parameters: {
      type: 'object',
      properties: {
        type: {
          type: 'string',
          enum: ['page', 'section_next_page', 'section_continuous'],
          description: 'Break type.',
        },
        location: {
          type: 'string',
          enum: ['start', 'end'],
          description: 'Where to insert.',
        },
      },
      required: ['type', 'location'],
    },
  }),

  // ---- Structure: set_header_footer -------------------------------------
  buildWriterTool({
    name: 'set_header_footer',
    description:
      'Set the content of a page header or footer. REPLACES existing ' +
      'content. For page numbers prefer insert_page_numbers (it can ' +
      'include surrounding text via prefix_text/suffix_text).',
    parameters: {
      type: 'object',
      properties: {
        type: {
          type: 'string',
          enum: ['header', 'footer'],
          description: "'header' (top of page) or 'footer' (bottom).",
        },
        text: {
          type: 'string',
          description:
            "New content. Cannot be empty — pass ' ' to clear.",
        },
        section_index: {
          type: 'number',
          description:
            'Single section (0-based). Defaults to 0. Mutually exclusive ' +
            'with section_indices.',
        },
        section_indices: {
          type: 'array',
          description:
            'Apply to multiple sections in one call (integer indices, ' +
            'NOT strings). Mutually exclusive with section_index.',
        },
        header_footer_type: {
          type: 'string',
          enum: ['primary', 'firstPage', 'evenPages'],
          description:
            "Defaults to 'primary'. firstPage/evenPages need LO to be " +
            'configured for those variants.',
        },
      },
      required: ['type', 'text'],
    },
  }),

  // ---- Structure: insert_page_numbers -----------------------------------
  buildWriterTool({
    name: 'insert_page_numbers',
    description:
      'Insert automatic page-number fields into a header or footer. ' +
      'Each call CLEARS the target h/f first. To combine page numbers ' +
      'with brand text in the same h/f, use prefix_text / suffix_text ' +
      'rather than chaining with set_header_footer.',
    parameters: {
      type: 'object',
      properties: {
        location: {
          type: 'string',
          enum: ['header', 'footer'],
          description: "Defaults to 'footer'.",
        },
        alignment: {
          type: 'string',
          enum: ['left', 'center', 'right'],
          description: "Defaults to 'center'.",
        },
        format: {
          type: 'string',
          description:
            "Page-number template. One of: '{PAGE}', 'Page {PAGE}', " +
            "'Page {PAGE} of {NUMPAGES}', '{PAGE} of {NUMPAGES}'.",
        },
        prefix_text: {
          type: 'string',
          description: 'Text inserted before the page-number field.',
        },
        suffix_text: {
          type: 'string',
          description: 'Text inserted after the page-number field.',
        },
        section_index: {
          type: 'number',
          description: 'Single section (0-based). Mutually exclusive with section_indices.',
        },
        section_indices: {
          type: 'array',
          description:
            'Apply to multiple sections in one call (integer indices, NOT strings).',
        },
      },
    },
  }),

  // ---- Structure: set_page_setup ----------------------------------------
  buildWriterTool({
    name: 'set_page_setup',
    description:
      'Set page layout (orientation, margins, paper size) for a section. ' +
      'Only included properties change. section_index = 0 (default) for ' +
      'single-section documents.',
    parameters: {
      type: 'object',
      properties: {
        section_index: {
          type: 'number',
          description: 'Zero-based section index. Defaults to 0.',
        },
        orientation: {
          type: 'string',
          enum: ['portrait', 'landscape'],
          description: 'Page orientation.',
        },
        top_margin: {
          type: 'number',
          description: 'Top margin in points (72pt = 1 inch). >= 0.',
        },
        bottom_margin: {
          type: 'number',
          description: 'Bottom margin in points. >= 0.',
        },
        left_margin: {
          type: 'number',
          description: 'Left margin in points. >= 0.',
        },
        right_margin: {
          type: 'number',
          description: 'Right margin in points. >= 0.',
        },
        paper_size: {
          type: 'string',
          description:
            'Named paper size, e.g. A4, Letter, Legal, A3, A5, Tabloid.',
        },
      },
    },
  }),

  // ---- Commenting: get_comments -----------------------------------------
  buildWriterTool({
    name: 'get_comments',
    description:
      'List all comments / annotations in the document. Returns each ' +
      'comment with its id, anchor text, author, date, content, resolved ' +
      'state, and reply chain (if supported by the LO version). Use ' +
      'before add_comment / manage_comment to find specific comments.',
    parameters: {
      type: 'object',
      properties: {},
    },
  }),

  // ---- Commenting: add_comment ------------------------------------------
  buildWriterTool({
    name: 'add_comment',
    description:
      'Anchor a new review comment to a text snippet. anchor is the ' +
      'text to attach the comment to (exact match in the document body). ' +
      'comment is the comment content. The comment appears in the ' +
      "Comments sidebar — NOT inline in the document body.",
    parameters: {
      type: 'object',
      properties: {
        anchor: {
          type: 'string',
          description: 'The text in the document body to anchor the comment to.',
        },
        comment: {
          type: 'string',
          description: 'The comment content shown in the Comments sidebar.',
        },
        match_case: {
          type: 'boolean',
          description: 'Case-sensitive anchor match. Defaults to false.',
        },
      },
      required: ['anchor', 'comment'],
    },
  }),

  // ---- Commenting: manage_comment ---------------------------------------
  buildWriterTool({
    name: 'manage_comment',
    description:
      'Resolve / unresolve / reply-to / delete a specific comment by id. ' +
      'Get the id from get_comments. resolve / unresolve / ' +
      'resolve_with_reply require LibreOffice 7.4+ — older builds return ' +
      'a "not supported" error with a clear recovery message.',
    parameters: {
      type: 'object',
      properties: {
        comment_id: {
          type: 'string',
          description: 'Comment id from get_comments.',
        },
        action: {
          type: 'string',
          enum: [
            'resolve',
            'unresolve',
            'resolve_with_reply',
            'reply',
            'delete',
          ],
          description: 'Operation to perform on the comment.',
        },
        text: {
          type: 'string',
          description: "Reply / resolve_with_reply text. Required for those.",
        },
      },
      required: ['comment_id', 'action'],
    },
  }),
];
