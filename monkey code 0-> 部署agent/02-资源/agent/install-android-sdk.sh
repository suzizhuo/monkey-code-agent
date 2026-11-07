#!/bin/sh
# Install the Android command-line tools into ANDROID_HOME.
# Safe to re-run; skips work that is already complete.
set -eu

SDK_ROOT="${ANDROID_HOME:-/opt/android-sdk}"
CMDLINE_VERSION="${CMDLINE_VERSION:-11076708}"
ARCHIVE="commandlinetools-linux-${CMDLINE_VERSION}_latest.zip"
URL="https://dl.google.com/android/repository/${ARCHIVE}"
PACKAGES="${ANDROID_PACKAGES:-platform-tools platforms;android-34 build-tools;34.0.0}"

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "error: required command not found: $1" >&2
        exit 1
    fi
}

need java
need curl
need unzip

if [ ! -x "${SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager" ]; then
    echo "installing Android command-line tools into ${SDK_ROOT}"
    mkdir -p "${SDK_ROOT}/cmdline-tools"
    tmp="$(mktemp -d)"
    curl -fsSL -o "${tmp}/${ARCHIVE}" "${URL}"
    unzip -q "${tmp}/${ARCHIVE}" -d "${tmp}/unpacked"
    rm -rf "${SDK_ROOT}/cmdline-tools/latest"
    mv "${tmp}/unpacked/cmdline-tools" "${SDK_ROOT}/cmdline-tools/latest"
    rm -rf "${tmp}"
fi

SDKMANAGER="${SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager"

set +eu
yes | "${SDKMANAGER}" --licenses >/dev/null 2>&1
set -eu

echo "installing packages: ${PACKAGES}"
# shellcheck disable=SC2086
"${SDKMANAGER}" ${PACKAGES}

echo "android sdk ready at ${SDK_ROOT}"
"${SDK_ROOT}/platform-tools/adb" version 2>/dev/null | head -1 || true
