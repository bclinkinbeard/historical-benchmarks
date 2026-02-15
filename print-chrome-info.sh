#!/bin/sh
# print-chrome-info.sh
#
# Renders an HTML page in Chrome for Testing that displays the user agent
# string as a header. Takes a screenshot and also dumps the DOM to stdout.
#
# Intended to be used with run-chrome-releases.py:
#   ./run-chrome-releases.py ./print-chrome-info.sh
#
# Environment variables (set by the harness):
#   CHROME_BIN        - path to Chrome binary
#   CHROME_MILESTONE  - milestone number (e.g. "145")
#   CHROME_VERSION    - full version string
#
# Optional:
#   SCREENSHOT_DIR    - directory to save screenshots (default: ./screenshots)

set -e

if [ -z "$CHROME_BIN" ]; then
    echo "ERROR: CHROME_BIN is not set. Run this via run-chrome-releases.py." >&2
    exit 1
fi

SCREENSHOT_DIR="${SCREENSHOT_DIR:-./screenshots}"
mkdir -p "$SCREENSHOT_DIR"

TMPHTML=$(mktemp --suffix=.html)
cleanup() { rm -f "$TMPHTML"; }
trap cleanup EXIT

# Build an HTML page whose JavaScript displays the user agent as a header.
cat > "$TMPHTML" <<EOF
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Chrome ${CHROME_MILESTONE:-}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    h1 { font-size: 1.5rem; }
  </style>
</head>
<body>
  <h1 id="ua"></h1>
  <script>
    document.getElementById("ua").textContent = navigator.userAgent;
  </script>
</body>
</html>
EOF

SCREENSHOT_PATH="${SCREENSHOT_DIR}/chrome-${CHROME_MILESTONE}.png"

# Take a screenshot of the page.
"$CHROME_BIN" \
    --headless \
    --no-sandbox \
    --disable-gpu \
    --window-size=800,200 \
    --screenshot="$SCREENSHOT_PATH" \
    "file://${TMPHTML}" 2>/dev/null

echo "Screenshot saved: $SCREENSHOT_PATH"

# Also dump the DOM to stdout.
"$CHROME_BIN" \
    --headless \
    --no-sandbox \
    --disable-gpu \
    --dump-dom \
    "file://${TMPHTML}" 2>/dev/null
