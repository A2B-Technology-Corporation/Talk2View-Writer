/**
 * Talk2View-Writer chat application.
 *
 * Mirrors Talk2View-Word/src/taskpane/App.tsx — wraps the SDK's
 * <Talk2View> provider around the chat UI with the partner key,
 * engine URL, and our writerTools. The SDK handles auth + chat;
 * each tool's execute callback proxies to LO's Python via the
 * pywebview bridge.
 */
import React from 'react';
import { Talk2View, ChatWidget } from '@talk2view/sdk/ui';
import { writerTools } from './tools';

// Writer-specific partner key (provisioned 2026-05-17). Mirrors
// PARTNER_KEY in src/talk2view_writer/config.py — both sides need
// to agree on the key the engine sees, but only the JS side
// actually uses it now that auth has moved to the browser.
const PARTNER_KEY = 'pk_live_474f6f895dfec144a70b841db0d7a3fe1cd1fc7317540bc7';
const BASE_URL = 'https://engine.talk2view.com';

export function App() {
  return (
    <Talk2View
      partnerKey={PARTNER_KEY}
      baseUrl={BASE_URL}
      tools={writerTools}
    >
      <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
        <ChatWidget />
      </div>
    </Talk2View>
  );
}
