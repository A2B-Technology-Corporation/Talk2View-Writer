"""Block until headless LibreOffice's UNO socket accepts a TCP connection.

CI workflow uses this between ``soffice --headless --accept=...&`` and
``pytest -m integration`` because soffice takes a few seconds to:

  1. Spawn its child process.
  2. Initialise the UNO service manager.
  3. Bind + listen on the requested TCP port.

A naive ``sleep 5`` is either too slow (wastes runtime) or too fast
(integration tests start before the socket is ready and fail with a
ConnectionRefusedError that looks like a real bug). This script does
an exponential backoff so the wait is just long enough.

Usage:

    python scripts/wait_for_soffice.py [--host 127.0.0.1] [--port 2002] [--timeout 60]

Exits 0 on success, 1 on timeout. Logs progress to stderr.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time


def wait_for_tcp(host: str, port: int, timeout: float) -> bool:
    """Poll ``(host, port)`` until a TCP connect succeeds or ``timeout`` elapses.

    Returns:
        ``True`` if the socket accepted at least one connection within
        ``timeout`` seconds, ``False`` otherwise.
    """
    deadline = time.monotonic() + timeout
    delay = 0.25
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            with socket.create_connection((host, port), timeout=2.0):
                elapsed = timeout - (deadline - time.monotonic())
                print(
                    f"soffice ready on {host}:{port} after {elapsed:.1f}s "
                    f"({attempts} attempt{'s' if attempts != 1 else ''})",
                    file=sys.stderr,
                )
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(delay)
            delay = min(delay * 1.5, 2.0)
    print(
        f"timed out after {timeout}s waiting for soffice on {host}:{port} "
        f"({attempts} attempts)",
        file=sys.stderr,
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2002)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    ok = wait_for_tcp(args.host, args.port, args.timeout)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
