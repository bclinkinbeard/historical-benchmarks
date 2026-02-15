#!/bin/sh
# print-chrome-info.sh
#
# Launches Chrome for Testing in headless mode, queries its DevTools endpoint
# for the V8 version, then renders an HTML page where JavaScript displays the
# user agent string and V8 version as page headers.
#
# Intended to be used with run-chrome-releases.py:
#   ./run-chrome-releases.py ./print-chrome-info.sh

set -e

if [ -z "$CHROME_BIN" ]; then
    echo "ERROR: CHROME_BIN is not set. Run this via run-chrome-releases.py." >&2
    exit 1
fi

# Pick a port derived from PID + a random component to avoid collisions.
CDP_PORT=$((9300 + ($$ % 700) + $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % 300))

STDERR_LOG=$(mktemp)
TMPHTML=$(mktemp --suffix=.html)

cleanup() {
    kill "$CHROME_PID" 2>/dev/null || true
    wait "$CHROME_PID" 2>/dev/null || true
    rm -f "$STDERR_LOG" "$TMPHTML"
}
trap cleanup EXIT

# Start Chrome headless with remote debugging, capture stderr.
"$CHROME_BIN" \
    --headless \
    --no-sandbox \
    --disable-gpu \
    --remote-debugging-port="$CDP_PORT" \
    about:blank >/dev/null 2>"$STDERR_LOG" &
CHROME_PID=$!

# Wait for the "DevTools listening" line on stderr — this is the reliable
# signal that the CDP port is ready, rather than polling with curl.
TRIES=0
while ! grep -q "DevTools listening" "$STDERR_LOG" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -ge 60 ]; then
        echo "ERROR: Chrome did not start within 30 seconds." >&2
        cat "$STDERR_LOG" >&2
        exit 1
    fi
    # Check that Chrome is still running.
    if ! kill -0 "$CHROME_PID" 2>/dev/null; then
        echo "ERROR: Chrome exited before DevTools was ready." >&2
        cat "$STDERR_LOG" >&2
        exit 1
    fi
    sleep 0.5
done

# Fetch V8 version from the DevTools protocol.
V8_VERSION=$(curl -s "http://localhost:${CDP_PORT}/json/version" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['V8-Version'])")

# Kill the background Chrome — we'll relaunch for dump-dom.
kill "$CHROME_PID" 2>/dev/null || true
wait "$CHROME_PID" 2>/dev/null || true

# Build an HTML page whose JavaScript displays both values as headers.
# The user agent comes from navigator.userAgent (live from the Chrome instance).
# The V8 version is injected as a JS variable (obtained via CDP above).
cat > "$TMPHTML" <<EOF
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Chrome ${CHROME_MILESTONE:-}</title>
</head>
<body>
  <h1 id="ua"></h1>
  <h1 id="v8"></h1>
  <script>
    var v8Version = "${V8_VERSION}";
    document.getElementById("ua").textContent = navigator.userAgent;
    document.getElementById("v8").textContent = "V8 " + v8Version;
  </script>
</body>
</html>
EOF

# Render the page in Chrome and dump the DOM to stdout.
"$CHROME_BIN" \
    --headless \
    --no-sandbox \
    --disable-gpu \
    --dump-dom \
    "file://${TMPHTML}" 2>/dev/null
