#!/usr/bin/env bash
# Install a built .oxt using whichever `unopkg` binary the host OS provides.
#
# ``unopkg`` lives in a different filesystem location on each platform
# and isn't always on PATH:
#
#   Linux (apt/rpm install)  /usr/bin/unopkg
#   macOS (brew --cask)      /Applications/LibreOffice.app/Contents/MacOS/unopkg
#   Windows (choco/.msi)     C:\Program Files\LibreOffice\program\unopkg.com
#
# Used by the GitHub Actions integration matrix so the same `bash
# scripts/install_oxt.sh dist/Talk2ViewWriter.oxt` invocation works on
# every runner.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <path-to-.oxt>" >&2
    exit 2
fi

OXT="$1"
if [[ ! -f "$OXT" ]]; then
    echo "ERROR: $OXT does not exist or is not a file" >&2
    exit 1
fi

is_macos=0
case "${OSTYPE:-$(uname -s)}" in
    darwin*|Darwin*)
        is_macos=1
        UNOPKG="/Applications/LibreOffice.app/Contents/MacOS/unopkg"
        ;;
    msys*|cygwin*|MINGW*)
        # Common Windows install paths. Prefer 64-bit then 32-bit.
        if [[ -f "/c/Program Files/LibreOffice/program/unopkg.com" ]]; then
            UNOPKG="/c/Program Files/LibreOffice/program/unopkg.com"
        elif [[ -f "/c/Program Files (x86)/LibreOffice/program/unopkg.com" ]]; then
            UNOPKG="/c/Program Files (x86)/LibreOffice/program/unopkg.com"
        else
            echo "ERROR: cannot find unopkg.com under C:\\Program Files\\LibreOffice\\" >&2
            exit 1
        fi
        ;;
    *)
        # Linux / BSD / other Unix-likes
        UNOPKG="$(command -v unopkg || true)"
        if [[ -z "$UNOPKG" ]]; then
            echo "ERROR: 'unopkg' not on PATH. Is LibreOffice installed?" >&2
            exit 1
        fi
        ;;
esac

echo "Installing $OXT via $UNOPKG"

# unopkg refuses to do a per-user install when invoked by uid 0:
#   "ERROR: Cannot run unopkg as root without --shared or --bundled option."
# CI containers (Debian, etc.) run as root, so detect that and use
# ``--shared`` which installs the extension system-wide. Local dev
# stays per-user.
UNOPKG_FLAGS=(--force --suppress-license --verbose)
if [[ "$(id -u 2>/dev/null || echo 0)" == "0" ]]; then
    echo "Running as root; using --shared to install system-wide"
    UNOPKG_FLAGS+=(--shared)
fi

# Capture unopkg's own verbose log next to the .oxt. unopkg's default
# failure is an opaque "ERROR: Exception occurred: Error while adding
# ..." with no detail (it swallows the real cause). The verbose log
# records what actually went wrong — a path over Windows MAX_PATH, a
# profile lock from a live soffice, etc. We print it before exiting so
# the CI log is self-contained instead of needing an artifact download
# (investigations #64). Disable -e around the call so the on-failure
# dump runs, then re-raise unopkg's exit code (fail-fast preserved).
LOGFILE="${OXT%.oxt}.unopkg.log"
set +e
"$UNOPKG" add "${UNOPKG_FLAGS[@]}" --log-file "$LOGFILE" "$OXT"
rc=$?
set -e
if [[ "$rc" -ne 0 ]]; then
    echo "ERROR: unopkg add failed (exit $rc)." >&2
    if [[ -f "$LOGFILE" ]]; then
        echo "--- unopkg log ($LOGFILE) ---" >&2
        cat "$LOGFILE" >&2
        echo "--- end unopkg log ---" >&2
    else
        echo "(no unopkg log written at $LOGFILE)" >&2
    fi
    # macOS: LibreOffice 26.x signs its `uno` helper with a parent
    # launch constraint that macOS 26 enforces — unopkg's spawned
    # registration helper is SIGKILLed (Code Signature Invalid /
    # Launch Constraint Violation) before it can serve the URP pipe,
    # so enabling fails with NoConnectException. Nothing this script
    # can fix; the in-process GUI path works (investigations #68).
    if [[ "$is_macos" == 1 ]] \
        && [[ -f "$LOGFILE" ]] \
        && grep -q "NoConnectException" "$LOGFILE"; then
        echo "" >&2
        echo "HINT: on macOS this NoConnectException usually means the" >&2
        echo "      LibreOffice 'uno' helper was killed by a code-signing" >&2
        echo "      launch constraint (LibreOffice 26.x + macOS 26; see" >&2
        echo "      docs/investigations.md #68). Install via the GUI" >&2
        echo "      instead: open the .oxt with LibreOffice (Tools >" >&2
        echo "      Extension Manager > Add), e.g.:" >&2
        echo "        open '$OXT'" >&2
    fi
    exit "$rc"
fi
echo "Installed."
