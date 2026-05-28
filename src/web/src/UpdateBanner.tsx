/**
 * In-app "update available" banner.
 *
 * On mount, checks the public GitHub Releases API for the latest
 * (non-prerelease) release and, if it's newer than the bundled
 * `__APP_VERSION__`, shows a dismissible banner. The native LibreOffice
 * update feed (description.xml `<update-information>`) is the actual
 * update mechanism; this banner is the active nudge — and the only
 * signal already-installed 1.0.x users get, since their shipped
 * manifest predates the feed.
 *
 * An update check must never break the chat, so all failures are
 * swallowed (logged at debug) — this is a deliberate UI-boundary catch.
 */
import React, { useEffect, useState } from 'react';
import { isNewer } from './version';
import { logToHost, openExternal } from './bridge';

const REPO = 'A2B-Technology-Corporation/Talk2View-Writer';
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;
const DISMISS_KEY = 'talk2view_update_dismissed_version';

// E2E tests / manual dev can point the check at a stub by setting
// window.__T2V_RELEASES_URL_OVERRIDE (mirrors __T2V_BASE_URL_OVERRIDE).
function releasesApiUrl(): string {
  const override = (window as unknown as { __T2V_RELEASES_URL_OVERRIDE?: string })
    .__T2V_RELEASES_URL_OVERRIDE;
  return override ?? `https://api.github.com/repos/${REPO}/releases/latest`;
}

export function UpdateBanner(): React.ReactElement | null {
  const [latest, setLatest] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(releasesApiUrl(), {
          headers: { Accept: 'application/vnd.github+json' },
        });
        if (!resp.ok) return;
        const data = (await resp.json()) as { tag_name?: string };
        const tag = data.tag_name;
        if (!tag || cancelled) return;
        if (!isNewer(tag, __APP_VERSION__)) return;
        if (localStorage.getItem(DISMISS_KEY) === tag) return; // already dismissed
        setLatest(tag);
        logToHost('info', `[update] newer version available: ${tag} (running ${__APP_VERSION__})`);
      } catch (err) {
        logToHost('debug', `[update] check skipped: ${String(err)}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!latest) return null;

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, latest);
    setLatest(null);
  };

  return (
    <div
      role="status"
      data-testid="update-banner"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',
        background: '#eef4ff',
        borderBottom: '1px solid #c7d8f5',
        font: '13px/1.4 system-ui, sans-serif',
        color: '#1a3a6b',
      }}
    >
      <span style={{ flex: 1 }}>
        Talk2View <strong>{latest}</strong> is available — update via{' '}
        <strong>Tools → Extension Manager → Check for Updates</strong>.
      </span>
      <button
        type="button"
        onClick={() => {
          void openExternal(RELEASES_PAGE);
        }}
        style={{ cursor: 'pointer', font: 'inherit' }}
      >
        Releases
      </button>
      <button
        type="button"
        aria-label="Dismiss update notice"
        onClick={dismiss}
        style={{ cursor: 'pointer', font: 'inherit', border: 'none', background: 'transparent' }}
      >
        ✕
      </button>
    </div>
  );
}
