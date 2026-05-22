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
      'Apply character-level formatting (bold, italic, underline, color, ' +
      'highlight, font size) to text matching ``query``. Use ``queries`` ' +
      'to apply different formatting to different snippets in one call. ' +
      "Use ``case_sensitive`` and ``match_index`` to disambiguate.",
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Text to find and format. Use the full phrase.',
        },
        queries: {
          type: 'array',
          description:
            'Batch: array of {query, ...format} for multiple format ' +
            'changes in one call.',
        },
        bold: { type: 'boolean', description: 'Bold on/off.' },
        italic: { type: 'boolean', description: 'Italic on/off.' },
        underline: {
          type: 'string',
          description: "Underline style: 'single', 'double', 'none'.",
        },
        color: {
          type: 'string',
          description:
            "Hex RGB foreground colour, no '#' (e.g. 'FF0000' for red).",
        },
        highlight: {
          type: 'string',
          description:
            "Highlight colour name: 'yellow', 'green', 'blue', 'pink', " +
            "'none'.",
        },
        size: {
          type: 'number',
          description: 'Font size in points.',
        },
        case_sensitive: {
          type: 'boolean',
          description: 'Default false.',
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
    name: 'search_document',
    description:
      'Find or find-and-replace text in the document body. Use ' +
      "action='find' to count matches + see the first match's context, " +
      "action='replace' to substitute. Doesn't change formatting; for " +
      'formatting changes use format_text.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Text to find.',
        },
        action: {
          type: 'string',
          enum: ['find', 'replace'],
          description: "Default 'find'.",
        },
        replacement: {
          type: 'string',
          description: "Required when action='replace'.",
        },
        case_sensitive: {
          type: 'boolean',
          description: 'Default false.',
        },
        whole_word: {
          type: 'boolean',
          description: 'Match whole words only. Default false.',
        },
      },
    },
  }),
];
