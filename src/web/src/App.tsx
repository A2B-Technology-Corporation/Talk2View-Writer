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
import { Talk2View, ChatWidget, useChat, useTalk2View } from '@talk2view/sdk/ui';
import { writerTools } from './tools';
import { logToHost } from './bridge';

// Writer-specific partner key (provisioned 2026-05-17). Mirrors
// PARTNER_KEY in src/talk2view_writer/config.py — both sides need
// to agree on the key the engine sees, but only the JS side
// actually uses it now that auth has moved to the browser.
const PARTNER_KEY = 'pk_live_474f6f895dfec144a70b841db0d7a3fe1cd1fc7317540bc7';
const BASE_URL = 'https://engine.talk2view.com';

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

  // Log auth state.
  useEffect(() => {
    logToHost('info', '[auth] state', {
      isAuthenticated: t2v.isAuthenticated,
      email: t2v.user?.email ?? null,
    });
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
        <ChatWidget />
      </div>
    </Talk2View>
  );
}
