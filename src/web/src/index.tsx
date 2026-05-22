/**
 * Talk2View-Writer chat — React entry point.
 *
 * Mounted into the pywebview window's <div id="root">. The bridge
 * (window.pywebview.api) is injected by pywebview before this
 * script runs, but we still wait for it inside ``invokeTool`` so
 * tools that fire on first render don't see a missing API.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element not found in HTML');
}
createRoot(container).render(<App />);

window.addEventListener('error', (e) => {
  // eslint-disable-next-line no-console
  console.error('[taskpane] uncaught error', e.error ?? e.message);
});
window.addEventListener('unhandledrejection', (e) => {
  // eslint-disable-next-line no-console
  console.error('[taskpane] unhandled promise rejection', e.reason);
});
