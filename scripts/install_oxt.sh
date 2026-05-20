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

case "${OSTYPE:-$(uname -s)}" in
    darwin*|Darwin*)
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
UNOPKG_FLAGS=(--force --suppress-license)
if [[ "$(id -u 2>/dev/null || echo 0)" == "0" ]]; then
    echo "Running as root; using --shared to install system-wide"
    UNOPKG_FLAGS+=(--shared)
fi
"$UNOPKG" add "${UNOPKG_FLAGS[@]}" "$OXT"
echo "Installed."
