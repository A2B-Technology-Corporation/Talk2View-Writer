/**
 * Remember-last-email: small helper that persists the last
 * successfully-signed-in email and autofills the SDK's login form
 * after the user signs out.
 *
 * The Talk2View SDK's ``LoginForm`` is bundled and not directly
 * hookable — we can't pass a defaultValue prop. Workaround:
 *   * Save the email to localStorage whenever the SDK's user
 *     transitions to non-null (LogBridge calls ``rememberEmail``).
 *   * Use a MutationObserver to spot the email <input> when it
 *     appears in the DOM and set its value programmatically.
 *
 * Bypassing React's controlled-input state with a raw value set is
 * fragile — React's onChange won't fire from a programmatic set, so
 * the SDK's internal state remains empty. We dispatch a native
 * ``input`` event after setting so React's synthetic-event handler
 * picks it up.
 */
import { logToHost } from './bridge';

const LAST_EMAIL_KEY = 't2v_writer_last_email';

export function rememberEmail(email: string | null | undefined): void {
  if (!email) return;
  try {
    localStorage.setItem(LAST_EMAIL_KEY, email);
    logToHost('info', `[remember_email] saved ${email} to localStorage`);
  } catch (e) {
    logToHost(
      'warning',
      `[remember_email] localStorage.setItem threw: ${(e as Error).message}`,
    );
  }
}

export function getLastEmail(): string | null {
  try {
    return localStorage.getItem(LAST_EMAIL_KEY);
  } catch {
    return null;
  }
}

/**
 * Watch the DOM for the SDK's login-form email input and prefill it
 * with the remembered email. Idempotent — safe to call any number
 * of times.
 */
export function installEmailAutofill(): void {
  let filled = false;
  const tryFill = (): boolean => {
    if (filled) return true;
    const last = getLastEmail();
    if (!last) return false;
    // The SDK's LoginForm renders <input type="email">. We use the
    // first such input we see; the chat composer has no email input
    // so this is unambiguous.
    const inputs = document.querySelectorAll<HTMLInputElement>(
      'input[type="email"]',
    );
    if (inputs.length === 0) return false;
    const input = inputs[0];
    if (input.value) return true; // user already typed
    input.value = last;
    // Trigger React's controlled-input update path so the SDK's
    // internal state catches up with the DOM value. Without this,
    // React will overwrite the value on the next render.
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )?.set;
    if (nativeSetter) {
      nativeSetter.call(input, last);
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    filled = true;
    logToHost(
      'info',
      `[remember_email] autofilled ${last} into login form`,
    );
    return true;
  };

  // First-render attempt.
  if (tryFill()) return;

  // Otherwise watch for the input to appear.
  const observer = new MutationObserver(() => {
    if (tryFill()) {
      observer.disconnect();
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  // Stop watching after 30 s — by then either the user has typed
  // their own email or they're not logging in this session.
  setTimeout(() => {
    if (!filled) {
      observer.disconnect();
      logToHost(
        'debug',
        '[remember_email] autofill watcher timed out (no email input appeared)',
      );
    }
  }, 30_000);
}
