/**
 * Live-E2E: the penguin-story scenario.
 *
 * Drives the chat UI bundle in Chromium against the real engine
 * (engine.talk2view.com) with the real tool surface routed to a
 * real soffice + extension via the bridge_server. The scenario in
 * ``tests/e2e/scenarios/penguin_story.yaml`` defines user prompts
 * + loose expected outcomes per step. The spec walks each step,
 * captures per-step Playwright + Xvfb screenshots + transcript +
 * doc-state, and asserts loosely (only on obvious model misbehaviour).
 *
 * The artifacts (under tests/e2e/test-results + _diag/) are the
 * point: every step's chat UI, soffice desktop, tool-call log, doc
 * state, and assistant text are uploaded so Claude / a reviewer can
 * inspect what actually happened and tighten the scenario as
 * patterns emerge.
 *
 * Architecture C, scaffold step 6 (final).
 */
import { test, expect, liveSofficeAvailable, liveEngineLogin } from '../fixtures/live-test-fixtures';
import { installLivePywebviewShim } from '../fixtures/live-pywebview-shim';
import { readFile, mkdir, writeFile } from 'fs/promises';
import { resolve, join } from 'path';
import { load as yamlLoad } from 'js-yaml';

const SCENARIO_PATH = resolve(__dirname, '../scenarios/penguin_story.yaml');
const ARTIFACTS_DIR = resolve(__dirname, '../test-results/live-penguin-story');

type ParagraphStyleAt = { index: number; style_in: string[] };
type ParagraphTextContainsAt = { index: number; contains: string };

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
    };
    tool_calls?: {
      must_invoke?: string[];
      max_count?: number;
      no_duplicate_with?: string;
    };
  };
};

type Scenario = { name: string; description: string; steps: ScenarioStep[] };

test.skip(
  !liveSofficeAvailable(),
  'T2V_E2E_LIVE_SOFFICE_PORT not set — live penguin scenario requires real soffice',
);
test.skip(process.platform === 'win32', 'live E2E is AF_UNIX-only');
test.skip(
  !process.env.T2V_TEST_USER_EMAIL || !process.env.T2V_TEST_USER_PASSWORD,
  'T2V_TEST_USER_EMAIL/PASSWORD secrets missing — cannot authenticate against engine',
);

test.describe('penguin story scenario (real engine + bundle + soffice)', () => {
  test.describe.configure({ retries: 0 }); // retries are misleading on LLM-driven assertions

  // Each scenario step can take ~30-60s of real LLM streaming. Three
  // steps + auth + bridge startup easily blows past Playwright's
  // default 30s test timeout.
  test.setTimeout(10 * 60 * 1000);

  test('walks the scripted scenario; loose assertions; full artifact dump', async ({
    browser,
    liveBridgeProxy,
    liveBundleServer,
  }, testInfo) => {
    // Force a single-shot run on Chromium only — WebKit project would
    // double-bill the engine for no extra signal.
    test.skip(testInfo.project.name !== 'chromium', 'one-shot Chromium');

    await mkdir(ARTIFACTS_DIR, { recursive: true });

    // 1. Load scenario.
    const scenarioText = await readFile(SCENARIO_PATH, 'utf-8');
    const scenario = yamlLoad(scenarioText) as Scenario;
    expect(scenario.steps.length).toBeGreaterThan(0);

    // 2. Authenticate against real engine; pre-seed tokens so the
    //    bundle skips its login UI.
    const tokens = await liveEngineLogin(
      process.env.T2V_TEST_USER_EMAIL!,
      process.env.T2V_TEST_USER_PASSWORD!,
    );

    // 3. New Chromium context with the live shim + auth pre-seeded.
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

    // 4. Navigate. Bundle should mount, see the pre-seeded tokens,
    //    skip login, and reach the composer.
    await page.goto(liveBundleServer.url() + '/');
    const composer = page.getByRole('textbox', { name: /message|chat/i }).first();
    await expect(composer).toBeVisible({ timeout: 15_000 });

    // 5. Walk each step. Collect everything as artifacts; assert
    //    loosely; never abort the run on a single soft-assertion
    //    failure — let every step generate its artifact so we can
    //    diff expected vs actual across the whole scenario.
    const softFailures: string[] = [];

    for (let i = 0; i < scenario.steps.length; i++) {
      const step = scenario.steps[i];
      const stepLabel = `step_${String(i + 1).padStart(2, '0')}`;

      await page.screenshot({
        path: join(ARTIFACTS_DIR, `${stepLabel}_pre.png`),
        fullPage: true,
      });

      const beforeToolCalls = await page.evaluate(
        () => window.__t2vToolCalls?.length ?? 0,
      );
      const beforeAssistantLogs = await page.evaluate(
        () =>
          (window.__t2vTestLogs ?? []).filter((l) =>
            l.message.startsWith('[chat:assistant] '),
          ).length,
      );

      // Composer is enabled here (previous step waited for re-enable
      // OR this is step 1 right after mount).
      await expect(composer).toBeEnabled({ timeout: 60_000 });
      await composer.fill(step.prompt);
      await composer.press('Enter');

      // Bundle disables the composer while the SDK is streaming a
      // response. Wait for that transition (proves the prompt was
      // accepted), then wait for the re-enable when streaming ends
      // (proves the assistant reply finished).
      await expect(composer).toBeDisabled({ timeout: 10_000 });
      await expect(composer).toBeEnabled({ timeout: 120_000 });

      // The bundle emits [chat:assistant] TWICE per response: an
      // empty placeholder when streaming starts (isStreaming=true,
      // content_length=0), and the final filled-in version when the
      // stream completes. Wait until we see a NEW non-empty one
      // (count > before AND latest message body is non-empty)
      // before capturing the transcript. Observed in artifact run
      // 26377562042 — without the non-empty check we'd capture the
      // placeholder and the transcript would show assistant_text="".
      await expect
        .poll(
          async () =>
            await page.evaluate((before) => {
              const logs = window.__t2vTestLogs ?? [];
              const assist = logs.filter((l) =>
                l.message.startsWith('[chat:assistant] '),
              );
              if (assist.length <= before) return false;
              const latest = assist[assist.length - 1];
              const body = latest.message.replace('[chat:assistant] ', '').trim();
              return body !== '';
            }, beforeAssistantLogs),
          { timeout: 60_000, intervals: [200, 500, 1000] },
        )
        .toBe(true);

      await page.screenshot({
        path: join(ARTIFACTS_DIR, `${stepLabel}_post.png`),
        fullPage: true,
      });

      // Collect transcript pieces.
      const toolCalls = await page.evaluate(
        () => window.__t2vToolCalls?.slice() ?? [],
      );
      const stepToolCalls = toolCalls.slice(beforeToolCalls);
      const allLogs = await page.evaluate(() => window.__t2vTestLogs?.slice() ?? []);
      const assistantLogs = allLogs.filter((l) =>
        l.message.startsWith('[chat:assistant] '),
      );
      const assistantText =
        assistantLogs.length > 0
          ? assistantLogs[assistantLogs.length - 1].message.replace(
              '[chat:assistant] ',
              '',
            )
          : '';

      // Fetch doc state via the bridge directly — bypasses the
      // bundle's tool-call dedup so we get the actual paragraph
      // count + text after the step.
      const docResp = await fetch(`${liveBridgeProxy.url()}/invoke_tool`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'get_document', args: {} }),
      });
      const docBody = (await docResp.json()) as { result?: string };
      const docState =
        docBody.result !== undefined
          ? (JSON.parse(docBody.result) as {
              paragraphs: Array<{ index: number; text: string; style: string }>;
              total_paragraphs: number;
            })
          : { paragraphs: [], total_paragraphs: 0 };

      const transcript = {
        step: stepLabel,
        prompt: step.prompt,
        assistant_text: assistantText,
        tool_calls: stepToolCalls,
        doc_state: docState,
      };
      await writeFile(
        join(ARTIFACTS_DIR, `${stepLabel}_transcript.json`),
        JSON.stringify(transcript, null, 2),
      );

      // Loose assertions — collect failures but don't abort.
      const exp = step.expect;
      const note = (msg: string) =>
        softFailures.push(`[${stepLabel}] ${msg}`);

      if (exp.assistant_contains) {
        const lc = assistantText.toLowerCase();
        const matched = exp.assistant_contains.some((n) =>
          lc.includes(n.toLowerCase()),
        );
        if (!matched) {
          note(
            `assistant reply matched none of [${exp.assistant_contains.join(', ')}]. Got: ${assistantText.slice(0, 240)}`,
          );
        }
      }
      if (exp.doc?.exact_paragraphs !== undefined) {
        if (docState.total_paragraphs !== exp.doc.exact_paragraphs) {
          note(
            `doc has ${docState.total_paragraphs} paragraphs, expected exactly ${exp.doc.exact_paragraphs}`,
          );
        }
      }
      if (exp.doc?.min_paragraphs !== undefined) {
        if (docState.total_paragraphs < exp.doc.min_paragraphs) {
          note(
            `doc has ${docState.total_paragraphs} paragraphs, expected >= ${exp.doc.min_paragraphs}`,
          );
        }
      }
      if (exp.doc?.max_paragraphs !== undefined) {
        if (docState.total_paragraphs > exp.doc.max_paragraphs) {
          note(
            `doc has ${docState.total_paragraphs} paragraphs, expected <= ${exp.doc.max_paragraphs}`,
          );
        }
      }
      if (exp.doc?.any_text_contains) {
        const flat = docState.paragraphs.map((p) => p.text).join('\n').toLowerCase();
        for (const needle of exp.doc.any_text_contains) {
          if (!flat.includes(needle.toLowerCase())) {
            note(`doc body did not contain '${needle}'`);
          }
        }
      }
      if (exp.doc?.paragraph_style_at) {
        for (const styleCheck of exp.doc.paragraph_style_at) {
          const para = docState.paragraphs[styleCheck.index];
          if (!para) {
            note(
              `paragraph_style_at[${styleCheck.index}]: doc has only ${docState.paragraphs.length} paragraphs`,
            );
            continue;
          }
          if (!styleCheck.style_in.includes(para.style)) {
            note(
              `paragraph[${styleCheck.index}] style='${para.style}', expected one of [${styleCheck.style_in.join(', ')}]`,
            );
          }
        }
      }
      if (exp.doc?.paragraph_text_contains_at) {
        for (const textCheck of exp.doc.paragraph_text_contains_at) {
          const para = docState.paragraphs[textCheck.index];
          if (!para) {
            note(
              `paragraph_text_contains_at[${textCheck.index}]: doc has only ${docState.paragraphs.length} paragraphs`,
            );
            continue;
          }
          if (!para.text.toLowerCase().includes(textCheck.contains.toLowerCase())) {
            note(
              `paragraph[${textCheck.index}] did not contain '${textCheck.contains}'. Got: ${para.text.slice(0, 120)}`,
            );
          }
        }
      }
      if (exp.tool_calls?.must_invoke) {
        const seen = new Set(stepToolCalls.map((c) => c.name));
        for (const must of exp.tool_calls.must_invoke) {
          if (!seen.has(must)) {
            note(
              `expected tool '${must}' to be invoked; actual tools this step: ${[...seen].join(', ')}`,
            );
          }
        }
      }
      if (exp.tool_calls?.max_count !== undefined) {
        if (stepToolCalls.length > exp.tool_calls.max_count) {
          note(
            `${stepToolCalls.length} tool calls this step exceeds max ${exp.tool_calls.max_count} (Platform #62 / #63 loop?)`,
          );
        }
      }
      if (exp.tool_calls?.no_duplicate_with) {
        const target = exp.tool_calls.no_duplicate_with;
        const targetCalls = stepToolCalls.filter((c) => c.name === target);
        const seenSerialised = new Set<string>();
        for (const c of targetCalls) {
          const sig = JSON.stringify(c.args);
          if (seenSerialised.has(sig)) {
            note(
              `${target} called twice with identical args: ${sig.slice(0, 120)}`,
            );
          }
          seenSerialised.add(sig);
        }
      }
    }

    // 6. Dump the expected-vs-actual digest. Always uploaded, even
    //    on pass, so Claude can read it post-run and decide whether
    //    to strengthen the scenario.
    const digest = {
      scenario: scenario.name,
      steps_executed: scenario.steps.length,
      soft_failure_count: softFailures.length,
      soft_failures: softFailures,
    };
    await writeFile(
      join(ARTIFACTS_DIR, 'expected_vs_actual.json'),
      JSON.stringify(digest, null, 2),
    );

    // 7. Hard assertions BEFORE closing the context. "Soft" failures
    //    are now hard: each scenario assertion was tightened to
    //    reflect observed-perfect behaviour from artifact-reviewed
    //    runs, so any drift is a real regression that should fail.
    //    Loosen only with a documented reason in the YAML.
    const totalToolCalls = await page
      .evaluate(() => window.__t2vToolCalls?.length ?? 0)
      .catch(() => 0);
    for (const sf of softFailures) {
      testInfo.annotations.push({ type: 'scenario-failure', description: sf });
    }

    await context.close();

    expect(scenario.steps.length).toBeGreaterThan(0);
    expect(totalToolCalls).toBeGreaterThanOrEqual(1);
    expect(
      softFailures,
      `${softFailures.length} scenario-failures; see expected_vs_actual.json + ` +
        `per-step transcripts + annotations for details:\n${softFailures.map((s) => '  - ' + s).join('\n')}`,
    ).toEqual([]);
  });
});
