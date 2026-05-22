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
import { writerTools } from './tools';
import { logToHost } from './bridge';
import { rememberEmail, installEmailAutofill } from './remember_email';
// Repo-root SYSTEM_PROMPT.md — webpack's asset/source loader inlines
// the file's contents as a string at build time. Single source of
// truth: the Python side reads the same file via setup_logging.
// Writer-specific partner key. System prompt + skills are configured
// in the engine dashboard for this key; we do NOT override the
// systemPrompt prop here (it would shadow the dashboard config).
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
      const preview = (m.content ?? '').slice(0, 1000);
      logToHost('info', `[chat:${m.role}] ${preview}`, {
        id: m.id,
        role: m.role,
        timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : String(m.timestamp),
        isStreaming: m.isStreaming ?? false,
        content_length: m.content?.length ?? 0,
        plan: m.plan ?? null,
        steps_count: m.steps?.length ?? 0,
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

  // Log SDK errors verbatim.
  useEffect(() => {
    if (chat.error) {
      logToHost('error', `[chat:error] ${chat.error}`);
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
      email: t2v.user?.email ?? null,
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
      logToHost(
        'info',
        `[chat:approval] ${JSON.stringify(chat.pendingApproval)}`,
        chat.pendingApproval as unknown as Record<string, unknown>,
      );
    }
  }, [chat.pendingApproval]);

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
      tools={writerTools}
      debug={true}
    >
      <LogBridge />
      <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
        <ChatPanel />
      </div>
    </Talk2View>
  );
}
