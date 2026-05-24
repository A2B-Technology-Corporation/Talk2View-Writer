"""Live authentication smoke against engine.talk2view.com.

Marked ``@pytest.mark.live`` so it runs only under the CI matrix's
``Live chat E2E`` step with ``T2V_TEST_USER_EMAIL`` /
``T2V_TEST_USER_PASSWORD`` populated from repo secrets. Skips
cleanly when secrets are absent (PRs from forks, local dev).

This is the canary for the live-test infrastructure: the credential
secrets are wired, the SDK can reach the engine, the engine accepts
the partner key + login. The forthcoming live-E2E Playwright suite
(Architecture C, ADR-0036) builds on top of these same secrets.

Currently uses Word's partner key by way of ADR-0034 (Writer's key
is broken upstream — Platform #61). Once Platform #61 is resolved,
switch back to Writer's own key.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.live
def test_live_authenticates_against_real_engine() -> None:
    """Real login round-trip — proves secrets + SDK + engine all wired.

    Asserts:
      - SDK can be instantiated with the partner key.
      - Email + password authenticate.
      - The returned user matches the email we logged in as.

    Skips when ``T2V_TEST_USER_EMAIL`` / ``T2V_TEST_USER_PASSWORD``
    aren't in env — e.g. PR from a fork that can't read repo secrets.
    """
    email = os.environ.get("T2V_TEST_USER_EMAIL")
    password = os.environ.get("T2V_TEST_USER_PASSWORD")
    if not email or not password:
        pytest.skip(
            "T2V_TEST_USER_EMAIL / T2V_TEST_USER_PASSWORD not in env "
            "(repo secrets missing or PR from fork). The live test "
            "suite cannot run without real credentials."
        )

    from talk2view import Talk2View

    # Writer routes through Word's partner key while Platform #61 is
    # unresolved (ADR-0034). The Writer-specific system prompt is
    # dashboard-configured on the Word partner profile (see
    # Investigation #34 + the d54c666 revert).
    word_partner_key = (
        "pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7"
    )
    t2v = Talk2View(partner_key=word_partner_key)
    user = t2v.auth.login(email, password)

    assert user is not None, "auth.login returned None — engine accepted no user"
    assert user.email == email, (
        f"engine returned user with email {user.email!r}; expected {email!r}"
    )
    # Auth state should now be set on the SDK instance.
    assert t2v.auth.is_authenticated(), (
        "auth.is_authenticated() is False after a successful login"
    )

    # Cleanup so the next test gets a fresh state (the SDK caches the
    # token in MemoryStorage by default, but logout still hits the
    # /v1/auth/logout endpoint).
    t2v.auth.logout()
    assert not t2v.auth.is_authenticated(), (
        "auth.is_authenticated() is True after logout"
    )
