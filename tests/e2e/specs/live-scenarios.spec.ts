/**
 * Live-E2E: every YAML scenario in tests/e2e/scenarios/ runs as a
 * separate Playwright test against the real engine + real soffice
 * + real Python tool surface. Each scenario gets a fresh Writer
 * document via the bridge orchestrator's loadComponentFromURL.
 *
 * Adding a scenario = one YAML file. No spec-file boilerplate.
 *
 * Artifacts per scenario: per-step Playwright pre/post screenshots,
 * per-step transcript JSON (prompt, assistant text, tool calls for
 * just that step, doc state via direct bridge call), and an
 * expected_vs_actual.json digest. Architecture C — see ADR-0036.
 */
import { test, expect, liveSofficeAvailable, liveEngineLogin } from '../fixtures/live-test-fixtures';
import { installLivePywebviewShim } from '../fixtures/live-pywebview-shim';
import { readFile, readdir, mkdir, writeFile } from 'fs/promises';
import { resolve, join, basename } from 'path';
import { load as yamlLoad } from 'js-yaml';

const SCENARIOS_DIR = resolve(__dirname, '../scenarios');
const ARTIFACTS_ROOT = resolve(__dirname, '../test-results/live-scenarios');

type ParagraphStyleAt = { index: number; style_in: string[] };
type ParagraphTextContainsAt = { index: number; contains: string };
type TableConstraint = {
  index: number;
  rows_min?: number;
  rows_max?: number;
  rows_exact?: number;
  cols?: number;
  first_row_contains?: string[];
};
type ScenarioStep = {
  prompt: string;
  expect: {
    assistant_contains?: string[];
    doc?: {
      exact_paragraphs?: number;
      min_paragraphs?: number;
      max_paragraphs?: number;
      any_text_contains?: string[];
      paragraph_style_at?: ParagraphStyleAt[];
      paragraph_text_contains_at?: ParagraphTextContainsAt[];
      exact_tables?: number;
      tables_must_contain?: TableConstraint[];
    };
    tool_calls?: {
      must_invoke?: string[];
      max_count?: number;
      no_duplicate_with?: string;
    };
  };
};
type Scenario = { name: string; description: string; steps: ScenarioStep[] };

type DocState = {
  paragraphs: Array<{ index: number; text: string; style: string }>;
  total_paragraphs: number;
  tables: Array<{
    index: number;
    rows: number;
    columns: number;
    first_row: string[];
  }>;
};

async function loadScenarios(): Promise<Array<{ name: string; path: string }>> {
  const files = await readdir(SCENARIOS_DIR);
  return files
    .filter((f) => f.endsWith('.yaml') || f.endsWith('.yml'))
    .map((f) => ({ name: basename(f).replace(/\.ya?ml$/, ''), path: join(SCENARIOS_DIR, f) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

test.skip(
  !liveSofficeAvailable(),
  'T2V_E2E_LIVE_SOFFICE_PORT not set — live scenarios require real soffice',
);
test.skip(process.platform === 'win32', 'live E2E is AF_UNIX-only');
test.skip(
  !process.env.T2V_TEST_USER_EMAIL || !process.env.T2V_TEST_USER_PASSWORD,
  'T2V_TEST_USER_EMAIL/PASSWORD secrets missing',
);

// Discovery has to happen synchronously for test.describe.parallel
// — Playwright builds the test tree at import time. Read the dir
// sync via fs.readdirSync.
import { readdirSync } from 'fs';
const SCENARIO_FILES = readdirSync(SCENARIOS_DIR)
  .filter((f) => f.endsWith('.yaml') || f.endsWith('.yml'))
  .map((f) => ({ name: basename(f).replace(/\.ya?ml$/, ''), path: join(SCENARIOS_DIR, f) }))
  .sort((a, b) => a.name.localeCompare(b.name));

// Circuit breaker. If a scenario completes with ZERO tool calls, the engine
// isn't tool-calling at all (Platform #73) — every remaining scenario would
// just burn its per-step timeouts (a broken engine turned this ~minutes suite
// into a 1.7h run). Trip the breaker on the first such scenario and skip the
// rest. Safe because every scenario expects >= 1 tool call, so 0 reliably
// means the engine is broken, not that the scenario legitimately needed none.
// Relies on --workers=1 (serial) so a scenario can see the prior one's result.
let engineNotToolCalling = false;

for (const sc of SCENARIO_FILES) {
  test.describe(`live scenario: ${sc.name}`, () => {
    // Each scenario can take minutes (multiple LLM round-trips). Set the
    // timeout via describe.configure, NOT test.setTimeout(): the latter,
    // called here in the describe BODY (collection phase), is silently
    // ignored, leaving the 30s global default from playwright.config.ts.
    // That 30s killed each test mid-step (the first soft-wait alone is
    // 120s) BEFORE the fail-fast logic below could run, so the breaker
    // never tripped and a dead engine still walked all 11 scenarios.
    test.describe.configure({ retries: 0, timeout: 15 * 60 * 1000 });

    test(`walks the scripted scenario; hard-fails on any violation`, async ({
      browser,
      liveBridgeProxy,
      liveBundleServer,
    }, testInfo) => {
      // One-shot Chromium — WebKit would double-bill the engine.
      test.skip(testInfo.project.name !== 'chromium', 'one-shot Chromium');
      // Fail-fast: a prior scenario proved the engine makes no tool calls
      // (Platform #73). Skip rather than burn this scenario's timeouts too.
      test.skip(
        engineNotToolCalling,
        'an earlier live scenario produced 0 tool calls — engine not tool-calling (Platform #73); skipping to avoid burning the full suite',
      );

      const artifactsDir = join(ARTIFACTS_ROOT, sc.name);
      await mkdir(artifactsDir, { recursive: true });

      const scenarioText = await readFile(sc.path, 'utf-8');
      const scenario = yamlLoad(scenarioText) as Scenario;
      expect(scenario.steps.length).toBeGreaterThan(0);

      const tokens = await liveEngineLogin(
        process.env.T2V_TEST_USER_EMAIL!,
        process.env.T2V_TEST_USER_PASSWORD!,
      );

      const context = await browser.newContext();
      const page = await context.newPage();
      await page.addInitScript(installLivePywebviewShim, {
        proxyUrl: liveBridgeProxy.url(),
      });
      await page.addInitScript((t) => {
        localStorage.setItem('talk2view_access_token', t.access_token);
        localStorage.setItem('talk2view_refresh_token', t.refresh_token);
        localStorage.setItem('talk2view_user', JSON.stringify(t.user));
      }, tokens);

      await page.goto(liveBundleServer.url() + '/');
      const composer = page.getByRole('textbox', { name: /message|chat/i }).first();
      await expect(composer).toBeVisible({ timeout: 15_000 });

      const softFailures: string[] = [];
      const note = (msg: string) => softFailures.push(msg);

      for (let i = 0; i < scenario.steps.length; i++) {
        const step = scenario.steps[i];
        const stepLabel = `step_${String(i + 1).padStart(2, '0')}`;

        await page.screenshot({
          path: join(artifactsDir, `${stepLabel}_pre.png`),
          fullPage: true,
        });

        const beforeToolCalls = await page.evaluate(
          () => window.__t2vToolCalls?.length ?? 0,
        );
        // Snapshot the prior turn's settled assistant text. After this
        // step we wait for window.__t2vLastAssistantFinal to CHANGE,
        // which signals this turn settled (isLoading flipped false in
        // the bundle). Reading this settled var — rather than parsing
        // the streamed [chat:assistant] logs — avoids the off-by-one
        // capture race under streaming /resume (Investigation #42).
        const beforeAssistantFinal = await page.evaluate(
          () =>
            (window as unknown as { __t2vLastAssistantFinal?: string })
              .__t2vLastAssistantFinal ?? '',
        );

        await expect(composer).toBeEnabled({ timeout: 60_000 });
        await composer.fill(step.prompt);
        await composer.press('Enter');

        // Tracks whether THIS turn hung (a soft-wait below timed out).
        // Combined with 0 cumulative tool calls it is the fail-fast signal
        // that the engine isn't tool-calling at all (Platform #73).
        let turnHung = false;

        await expect(composer).toBeDisabled({ timeout: 10_000 });
        // Soft-wait for composer to re-enable. If a tool call hangs
        // forever (e.g. LO C++ bug in manage_list — Investigation #37 —
        // or add_comment — Investigation #38), the composer stays
        // disabled and the model can't be reached. Note the hang and
        // continue so the post-step screenshot + transcript still get
        // captured — and so the next step doesn't blow up with a
        // disabled-locator error that yields less context.
        try {
          await expect(composer).toBeEnabled({ timeout: 120_000 });
        } catch {
          turnHung = true;
          note(
            `${stepLabel} composer never re-enabled within 120s — likely an underlying LO tool hang (see Investigations #37, #38)`,
          );
        }

        // Wait for this turn's settled assistant text to land — i.e.
        // window.__t2vLastAssistantFinal changes from the prior turn's
        // value to a fresh non-empty reply. Soft try/catch so a turn
        // that never produces text (e.g. a tool hang) records an empty
        // assistant_text as a soft-failure below rather than aborting.
        try {
          await expect
            .poll(
              async () =>
                await page.evaluate((before) => {
                  const cur =
                    (window as unknown as { __t2vLastAssistantFinal?: string })
                      .__t2vLastAssistantFinal ?? '';
                  return cur.trim() !== '' && cur !== before;
                }, beforeAssistantFinal),
              { timeout: 120_000, intervals: [200, 500, 1000] },
            )
            .toBe(true);
        } catch {
          turnHung = true;
          note(
            `${stepLabel} timed out (120s) waiting for the settled assistant reply; transcript will show whatever was captured`,
          );
        }

        await page.screenshot({
          path: join(artifactsDir, `${stepLabel}_post.png`),
          fullPage: true,
        });

        const toolCalls = await page.evaluate(
          () => window.__t2vToolCalls?.slice() ?? [],
        );
        const stepToolCalls = toolCalls.slice(beforeToolCalls);

        // Fail-fast (Platform #73). A turn that hung AND has produced no
        // tool call across the whole scenario so far means the engine isn't
        // tool-calling — walking the remaining steps would just burn ~240s
        // of soft-waits each (this is what turned the suite into a ~1.9h
        // run). Abort now and trip the cross-scenario breaker so the rest
        // skip. The post-loop assertions still fire, so this scenario fails
        // loudly — just fast. Conservative: only fires while cumulative
        // tool calls are still 0, so a slow-but-working engine that already
        // made a call is never aborted.
        if (turnHung && toolCalls.length === 0) {
          engineNotToolCalling = true;
          note(
            `${stepLabel} turn hung with 0 tool calls — engine not tool-calling (Platform #73); aborting scenario early`,
          );
          break;
        }
        const assistantText = await page.evaluate(
          () =>
            (
              window as unknown as { __t2vLastAssistantFinal?: string }
            ).__t2vLastAssistantFinal?.trim() ?? '',
        );

        const docResp = await fetch(`${liveBridgeProxy.url()}/invoke_tool`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ name: 'get_document', args: {} }),
        });
        const docBody = (await docResp.json()) as { result?: string };
        const docState: DocState =
          docBody.result !== undefined
            ? (JSON.parse(docBody.result) as DocState)
            : { paragraphs: [], total_paragraphs: 0, tables: [] };

        await writeFile(
          join(artifactsDir, `${stepLabel}_transcript.json`),
          JSON.stringify(
            {
              step: stepLabel,
              prompt: step.prompt,
              assistant_text: assistantText,
              tool_calls: stepToolCalls,
              doc_state: docState,
            },
            null,
            2,
          ),
        );

        const exp = step.expect;
        const prefix = `[${stepLabel}]`;

        if (exp.assistant_contains) {
          const lc = assistantText.toLowerCase();
          // Coerce to string — YAML scalars like `- 7` parse as
          // numbers, which would crash on .toLowerCase().
          const needles = exp.assistant_contains.map((n) => String(n));
          const matched = needles.some((n) => lc.includes(n.toLowerCase()));
          if (!matched) {
            note(
              `${prefix} assistant reply matched none of [${needles.join(', ')}]. Got: ${assistantText.slice(0, 240)}`,
            );
          }
        }
        if (exp.doc?.exact_paragraphs !== undefined) {
          if (docState.total_paragraphs !== exp.doc.exact_paragraphs) {
            note(
              `${prefix} doc has ${docState.total_paragraphs} paragraphs, expected exactly ${exp.doc.exact_paragraphs}`,
            );
          }
        }
        if (exp.doc?.min_paragraphs !== undefined) {
          if (docState.total_paragraphs < exp.doc.min_paragraphs) {
            note(
              `${prefix} doc has ${docState.total_paragraphs} paragraphs, expected >= ${exp.doc.min_paragraphs}`,
            );
          }
        }
        if (exp.doc?.max_paragraphs !== undefined) {
          if (docState.total_paragraphs > exp.doc.max_paragraphs) {
            note(
              `${prefix} doc has ${docState.total_paragraphs} paragraphs, expected <= ${exp.doc.max_paragraphs}`,
            );
          }
        }
        if (exp.doc?.any_text_contains) {
          const flat = docState.paragraphs
            .map((p) => p.text)
            .join('\n')
            .toLowerCase();
          for (const needle of exp.doc.any_text_contains) {
            if (!flat.includes(needle.toLowerCase())) {
              note(`${prefix} doc body did not contain '${needle}'`);
            }
          }
        }
        if (exp.doc?.paragraph_style_at) {
          for (const sc2 of exp.doc.paragraph_style_at) {
            const para = docState.paragraphs[sc2.index];
            if (!para) {
              note(
                `${prefix} paragraph_style_at[${sc2.index}]: only ${docState.paragraphs.length} paragraphs`,
              );
              continue;
            }
            if (!sc2.style_in.includes(para.style)) {
              note(
                `${prefix} paragraph[${sc2.index}] style='${para.style}', expected one of [${sc2.style_in.join(', ')}]`,
              );
            }
          }
        }
        if (exp.doc?.paragraph_text_contains_at) {
          for (const tc of exp.doc.paragraph_text_contains_at) {
            const para = docState.paragraphs[tc.index];
            if (!para) {
              note(
                `${prefix} paragraph_text_contains_at[${tc.index}]: only ${docState.paragraphs.length} paragraphs`,
              );
              continue;
            }
            if (!para.text.toLowerCase().includes(tc.contains.toLowerCase())) {
              note(
                `${prefix} paragraph[${tc.index}] did not contain '${tc.contains}'. Got: ${para.text.slice(0, 120)}`,
              );
            }
          }
        }
        if (exp.doc?.exact_tables !== undefined) {
          if (docState.tables.length !== exp.doc.exact_tables) {
            note(
              `${prefix} doc has ${docState.tables.length} tables, expected exactly ${exp.doc.exact_tables}`,
            );
          }
        }
        if (exp.doc?.tables_must_contain) {
          for (const tcs of exp.doc.tables_must_contain) {
            const tbl = docState.tables[tcs.index];
            if (!tbl) {
              note(
                `${prefix} tables_must_contain[${tcs.index}]: doc has only ${docState.tables.length} tables`,
              );
              continue;
            }
            if (tcs.rows_exact !== undefined && tbl.rows !== tcs.rows_exact) {
              note(
                `${prefix} table[${tcs.index}] has ${tbl.rows} rows, expected exactly ${tcs.rows_exact}`,
              );
            }
            if (tcs.rows_min !== undefined && tbl.rows < tcs.rows_min) {
              note(
                `${prefix} table[${tcs.index}] has ${tbl.rows} rows, expected >= ${tcs.rows_min}`,
              );
            }
            if (tcs.rows_max !== undefined && tbl.rows > tcs.rows_max) {
              note(
                `${prefix} table[${tcs.index}] has ${tbl.rows} rows, expected <= ${tcs.rows_max}`,
              );
            }
            if (tcs.cols !== undefined && tbl.columns !== tcs.cols) {
              note(
                `${prefix} table[${tcs.index}] has ${tbl.columns} cols, expected ${tcs.cols}`,
              );
            }
            if (tcs.first_row_contains) {
              const flatRow = tbl.first_row.map((c) => c.toLowerCase()).join('\n');
              for (const needle of tcs.first_row_contains) {
                if (!flatRow.includes(needle.toLowerCase())) {
                  note(
                    `${prefix} table[${tcs.index}].first_row did not contain '${needle}'. Got: [${tbl.first_row.join(', ')}]`,
                  );
                }
              }
            }
          }
        }
        if (exp.tool_calls?.must_invoke) {
          const seen = new Set(stepToolCalls.map((c) => c.name));
          for (const must of exp.tool_calls.must_invoke) {
            if (!seen.has(must)) {
              note(
                `${prefix} expected tool '${must}' invoked; got [${[...seen].join(', ')}]`,
              );
            }
          }
        }
        if (exp.tool_calls?.max_count !== undefined) {
          if (stepToolCalls.length > exp.tool_calls.max_count) {
            note(
              `${prefix} ${stepToolCalls.length} tool calls exceeds max ${exp.tool_calls.max_count} (Platform #62 / #63?)`,
            );
          }
        }
        if (exp.tool_calls?.no_duplicate_with) {
          const target = exp.tool_calls.no_duplicate_with;
          const targetCalls = stepToolCalls.filter((c) => c.name === target);
          const seenSig = new Set<string>();
          for (const c of targetCalls) {
            const sig = JSON.stringify(c.args);
            if (seenSig.has(sig)) {
              note(`${prefix} ${target} called twice with identical args: ${sig.slice(0, 120)}`);
            }
            seenSig.add(sig);
          }
        }
      }

      const totalToolCalls = await page
        .evaluate(() => window.__t2vToolCalls?.length ?? 0)
        .catch(() => 0);
      // Trip the circuit breaker (see top of file) so the remaining
      // scenarios skip instead of repeating this broken-engine walk.
      if (totalToolCalls === 0) engineNotToolCalling = true;
      for (const sf of softFailures) {
        testInfo.annotations.push({ type: 'scenario-failure', description: sf });
      }
      await writeFile(
        join(artifactsDir, 'expected_vs_actual.json'),
        JSON.stringify(
          {
            scenario: scenario.name,
            steps_executed: scenario.steps.length,
            soft_failure_count: softFailures.length,
            soft_failures: softFailures,
          },
          null,
          2,
        ),
      );
      await context.close();

      expect(totalToolCalls).toBeGreaterThanOrEqual(1);
      expect(
        softFailures,
        `${softFailures.length} scenario-failures in '${sc.name}'; see expected_vs_actual.json + per-step transcripts + annotations:\n${softFailures.map((s) => '  - ' + s).join('\n')}`,
      ).toEqual([]);
    });
  });
}
