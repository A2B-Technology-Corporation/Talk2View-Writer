/**
 * Talk2View-Writer chat application.
 *
 * Mirrors Talk2View-Word/src/taskpane/App.tsx — wraps the SDK's
 * <Talk2View> provider around the chat UI with the partner key,
 * engine URL, and our writerTools. The SDK handles auth + chat;
 * each tool's execute callback proxies to LO's Python via the
 * pywebview bridge.
 *
 * <LogBridge/> subscribes to chat messages, agent status, errors,
 * and auth state and forwards each one to LO's rotating log.
 * Combined with the console.* + window.error hooks in bridge.ts,
 * the chat session is fully captured in talk2view.log.
 */
import React, { useEffect, useRef } from 'react';
// ChatPanel is the full-window chat UI; ChatWidget is the floating
// launcher-bubble variant — wrong shape for our pywebview window
// (the window IS the chat). E2E smoke caught the difference in
// 2026-05-22.
import { Talk2View, ChatPanel, useChat, useTalk2View } from '@talk2view/sdk/ui';
import { useT2V, useT2VTools } from '@talk2view/sdk/react';
import { writerTools } from './tools';
import { logToHost } from './bridge';
import { UpdateBanner } from './UpdateBanner';
import { rememberEmail, installEmailAutofill } from './remember_email';
// Repo-root SYSTEM_PROMPT.md — webpack's asset/source loader inlines
// the file's contents as a string at build time. Single source of
// truth: the Python side reads the same file via setup_logging.
// Writer's own partner key — Platform #61 has been resolved upstream
// (confirmed by Andy 2026-05-25). Word's key was the workaround
// during the unprovisioned window per ADR-0034; this is the revert.
const PARTNER_KEY = 'pk_live_474f6f895dfec144a70b841db0d7a3fe1cd1fc7317540bc7';
// E2E tests set window.__T2V_BASE_URL_OVERRIDE so the SDK fetches from
// the per-test mock engine instead of the production engine. The
// override is only honored when the value looks like a localhost URL
// — a safeguard so a compromised page can't redirect chat traffic.
const PRODUCTION_BASE_URL = 'https://engine.talk2view.com';
const _override = (window as unknown as { __T2V_BASE_URL_OVERRIDE?: string })
  .__T2V_BASE_URL_OVERRIDE;
const BASE_URL =
  _override && /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?(\/|$)/.test(_override)
    ? _override
    : PRODUCTION_BASE_URL;

function LogBridge() {
  const chat = useChat();
  const t2v = useTalk2View();

  // Log every newly-appended message in full.
  const seenIndex = useRef(0);
  const lastStreamingText = useRef('');
  useEffect(() => {
    const messages = chat.messages;
    while (seenIndex.current < messages.length) {
      const m = messages[seenIndex.current];
      // Log every message on first appearance (immediate, may be a
      // mid-stream partial). Keeping this immediate is good forensics —
      // if a turn hangs mid-stream we still see what arrived. The E2E
      // spec does NOT read these logs for its assistant-text assertion
      // (that would be racy under streaming /resume — Investigation
      // #42); it reads window.__t2vLastAssistantFinal, set below only
      // when the turn settles.
      // Do NOT log message content (or m.plan) at INFO: assistant and
      // tool-call text echoes document content that may be PHI in a
      // medical-document workflow, and the persistent log is attached to
      // bug reports. Mirror the Python bridge's INFO/DEBUG split
      // (bridge_server.py): metadata at INFO, full content only under the
      // T2V_WRITER_DEBUG opt-in.
      logToHost('info', `[chat:${m.role}]`, {
        id: m.id,
        role: m.role,
        timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : String(m.timestamp),
        isStreaming: m.isStreaming ?? false,
        content_length: m.content?.length ?? 0,
        steps_count: m.steps?.length ?? 0,
      });
      logToHost('debug', `[chat:${m.role}] ${(m.content ?? '').slice(0, 1000)}`, {
        id: m.id,
        plan: m.plan ?? null,
      });
      seenIndex.current += 1;
      lastStreamingText.current = '';
    }
    // Capture mid-stream growth on the last assistant message.
    const last = messages[messages.length - 1];
    if (last && last.isStreaming) {
      const text = last.content ?? '';
      if (text !== lastStreamingText.current) {
        if (text.startsWith(lastStreamingText.current)) {
          const delta = text.slice(lastStreamingText.current.length);
          if (delta.length > 0) {
            logToHost(
              'debug',
              `[chat:${last.role}:delta] +${delta.length}ch`,
            );
          }
        }
        lastStreamingText.current = text;
      }
    }
  }, [chat.messages]);

  // Expose the SETTLED assistant reply for the live-scenarios E2E spec.
  // The spec can't reliably read the streamed [chat:assistant] logs:
  // under streaming /resume the final segment lands just after the
  // composer re-enables, so a log-timing capture grabs the PREVIOUS
  // step's reply (off-by-one — observed in CI run 26465834730). When
  // chat.isLoading flips false the turn is fully settled, so the last
  // assistant message carries this turn's complete confirmation. We
  // publish it on window for the spec to read deterministically. This
  // is test-only plumbing; it has no effect on the chat UI.
  useEffect(() => {
    if (chat.isLoading) return;
    const assistantMsgs = chat.messages.filter((m) => m.role === 'assistant');
    const last = assistantMsgs[assistantMsgs.length - 1];
    (window as unknown as { __t2vLastAssistantFinal?: string }).__t2vLastAssistantFinal =
      last?.content ?? '';
  }, [chat.messages, chat.isLoading]);

  // Log SDK errors verbatim.
  useEffect(() => {
    if (chat.error) {
      // The error string can echo document content (PHI). Log its length
      // at error level for triage; the verbatim text only under DEBUG.
      logToHost('error', `[chat:error] (${String(chat.error).length} chars)`);
      logToHost('debug', `[chat:error] ${chat.error}`);
    }
  }, [chat.error]);

  // Log agent status transitions ("thinking", "running format_text"...).
  useEffect(() => {
    if (chat.agentStatus) {
      logToHost(
        'info',
        `[chat:status] ${chat.agentStatus.status}: ${chat.agentStatus.message}`,
      );
    }
  }, [chat.agentStatus]);

  // Log loading transitions (request inflight to engine).
  useEffect(() => {
    logToHost('debug', `[chat:loading] ${chat.isLoading}`);
  }, [chat.isLoading]);

  // Log auth state + persist last-used email for autofill.
  useEffect(() => {
    logToHost('info', '[auth] state', {
      isAuthenticated: t2v.isAuthenticated,
      // Presence, not the address — email is PII and lands in the
      // bug-report log unredacted.
      has_email: !!t2v.user?.email,
    });
    if (t2v.isAuthenticated && t2v.user?.email) {
      rememberEmail(t2v.user.email);
    } else if (!t2v.isAuthenticated) {
      // User just logged out (or this is the initial load with no
      // session). The SDK will re-render the LoginForm; the email
      // input is fresh in the DOM each time, so re-arm the autofill
      // observer to catch it.
      installEmailAutofill();
    }
  }, [t2v.isAuthenticated, t2v.user?.email]);

  // Log tool-call approval requests (the engine wants user OK).
  useEffect(() => {
    if (chat.pendingApproval) {
      // The approval payload carries tool args (document content / PHI).
      // Log only that an approval is pending at INFO; the full payload
      // under DEBUG.
      logToHost('info', '[chat:approval] pending');
      logToHost(
        'debug',
        `[chat:approval] ${JSON.stringify(chat.pendingApproval)}`,
        chat.pendingApproval as unknown as Record<string, unknown>,
      );
    }
  }, [chat.pendingApproval]);

  return null;
}

// Registers writerTools once the user is authenticated. The prebuilt
// `<Talk2View tools={...}>` prop registers in a useEffect keyed on
// [t2v, tools] (NOT auth), .catch-swallows the pre-login 401, and never
// retries after login — so a fresh first-login user gets a session with
// zero tools (Writer #6 / Platform #67). Registering via the headless
// useT2VTools hook gated on isAuthenticated is the pattern that actually
// retries after auth. t2v.tools.register() wires both the schema and the
// inline `execute` handler, so no separate .handle() call is needed.
function ToolRegistrar() {
  const { isAuthenticated } = useT2V();
  const { registerTools, isRegistered } = useT2VTools();
  useEffect(() => {
    if (!isAuthenticated || isRegistered) return;
    registerTools(writerTools).catch((err) => {
      logToHost('error', '[tools] registration failed', { err: String(err) });
    });
  }, [isAuthenticated, isRegistered, registerTools]);
  return null;
}

export function App() {
  useEffect(() => {
    logToHost('info', '[app] <App> mounted', {
      partner_key_suffix: PARTNER_KEY.slice(-8),
      base_url: BASE_URL,
      tools: writerTools.map((t) => t.name),
    });
  }, []);

  return (
    <Talk2View
      partnerKey={PARTNER_KEY}
      baseUrl={BASE_URL}
      debug={true}
    >
      <ToolRegistrar />
      <LogBridge />
      <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
        <UpdateBanner />
        {/* allowAnonymous defaults to true in the SDK (>=0.7.0), which would
            show the composer to a logged-out user and silently start a
            budget-capped anonymous demo session (with no tools, since
            ToolRegistrar gates on isAuthenticated). Talk2View-Writer is a
            login-gated host — force the login form for logged-out users. */}
        <ChatPanel allowAnonymous={false} />
      </div>
    </Talk2View>
  );
}
