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
          description: 'List of zero-based indices to format.',
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
];
