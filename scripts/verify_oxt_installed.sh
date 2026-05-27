#!/usr/bin/env bash
# Verify the Talk2View-Writer extension is registered with LibreOffice,
# cross-platform, by parsing `unopkg list`. This is the "did it actually
# install?" check a user would implicitly rely on — and unlike the
# UNO-socket integration test (which needs PyUNO wired into the venv and so
# only runs the registration canary on Linux), `unopkg list` works the same
# on every platform.
#
# Mirrors scripts/install_oxt.sh's unopkg discovery + --shared (root) logic.
# Run AFTER install and BEFORE starting soffice (unopkg operations while
# soffice is alive can corrupt the deployment registry).

set -euo pipefail

EXT_ID="com.talk2view.writer"

case "${OSTYPE:-$(uname -s)}" in
    darwin*|Darwin*)
        UNOPKG="/Applications/LibreOffice.app/Contents/MacOS/unopkg"
        ;;
    msys*|cygwin*|MINGW*)
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
        UNOPKG="$(command -v unopkg || true)"
        if [[ -z "$UNOPKG" ]]; then
            echo "ERROR: 'unopkg' not on PATH. Is LibreOffice installed?" >&2
            exit 1
        fi
        ;;
esac

# Match the install scope: a root install used --shared, so list --shared.
LIST_FLAGS=()
if [[ "$(id -u 2>/dev/null || echo 0)" == "0" ]]; then
    LIST_FLAGS+=(--shared)
fi

echo "Listing installed extensions via $UNOPKG ${LIST_FLAGS[*]:-}"
listing="$("$UNOPKG" list "${LIST_FLAGS[@]}" 2>&1)"
echo "$listing"

if echo "$listing" | grep -q "$EXT_ID"; then
    echo "OK: $EXT_ID is registered with LibreOffice."
else
    echo "ERROR: $EXT_ID NOT found in 'unopkg list' output — the .oxt did not register." >&2
    exit 1
fi
