/**
 * Talk2View-Writer chat — React entry point.
 *
 * Mounted into the pywebview window's <div id="root">. ``installHostLogging``
 * runs BEFORE React mounts so any error during SDK init or first
 * render is captured in LO's rotating log via the bridge.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { installHostLogging, logToHost } from './bridge';

installHostLogging();
logToHost('info', '[boot] index.tsx loaded', {
  url: window.location.href,
  userAgent: navigator.userAgent,
  ts: new Date().toISOString(),
});

const container = document.getElementById('root');
if (!container) {
  logToHost('error', '[boot] #root element missing in HTML');
  throw new Error('Root element not found in HTML');
}
try {
  createRoot(container).render(<App />);
  logToHost('info', '[boot] React mounted');
} catch (err) {
  const e = err as Error;
  logToHost('error', `[boot] React mount threw: ${e.message}`, {
    name: e.name,
    stack: e.stack,
  });
  throw err;
}
