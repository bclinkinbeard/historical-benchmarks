#!/bin/sh
# print-chrome-info.sh
#
# Loads a ~5 MB JSON document in Chrome for Testing, parses it with
# JSON.parse(), and displays the user agent, parse duration, and current
# timestamp.  Takes a screenshot and dumps the DOM to stdout.
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
TMPJSON=$(mktemp --suffix=.json)
cleanup() { rm -f "$TMPHTML" "$TMPJSON"; }
trap cleanup EXIT

# Generate a ~5 MB JSON document (array of 10 000 objects).
python3 -c "
import json, hashlib
data = []
for i in range(10000):
    h = hashlib.sha256(str(i).encode()).hexdigest()
    data.append({
        'id': i,
        'hash': h,
        'nested': {
            'alpha': h[:16],
            'beta': h[16:32],
            'gamma': h[32:48],
            'delta': h[48:],
            'values': [i * j for j in range(20)]
        },
        'tags': ['tag-' + str(i % k) for k in range(1, 8)],
        'active': i % 3 != 0,
        'description': 'Record number {} with hash {}'.format(i, h)
    })
json.dump(data, open('$TMPJSON', 'w'))
"

# Build an HTML page that fetches and parses the JSON, then displays results.
cat > "$TMPHTML" <<HTMLEOF
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Chrome ${CHROME_MILESTONE:-} JSON Benchmark</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    h1 { font-size: 1.4rem; margin: 0.4rem 0; }
    .label { color: #555; font-size: 0.9rem; }
  </style>
</head>
<body>
  <h1 id="ua"></h1>
  <h1 id="timing"></h1>
  <h1 id="ts"></h1>
  <script>
    document.getElementById("ua").textContent = navigator.userAgent;

    // Fetch and parse the JSON document.
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "file://${TMPJSON}", false);   // synchronous
    xhr.send();
    var raw = xhr.responseText;

    var t0 = performance.now();
    var parsed = JSON.parse(raw);
    var t1 = performance.now();

    var elapsed = (t1 - t0).toFixed(2);
    document.getElementById("timing").textContent =
        "Parsed " + parsed.length + " records (" +
        (raw.length / 1024 / 1024).toFixed(2) + " MB) in " + elapsed + " ms";

    document.getElementById("ts").textContent =
        "Timestamp: " + new Date().toISOString();
  </script>
</body>
</html>
HTMLEOF

SCREENSHOT_PATH="${SCREENSHOT_DIR}/chrome-${CHROME_MILESTONE}.png"

# Take a screenshot of the page.
"$CHROME_BIN" \
    --headless \
    --no-sandbox \
    --disable-gpu \
    --allow-file-access-from-files \
    --window-size=900,250 \
    --screenshot="$SCREENSHOT_PATH" \
    "file://${TMPHTML}" 2>/dev/null

echo "Screenshot saved: $SCREENSHOT_PATH"

# Also dump the DOM to stdout.
"$CHROME_BIN" \
    --headless \
    --no-sandbox \
    --disable-gpu \
    --allow-file-access-from-files \
    --dump-dom \
    "file://${TMPHTML}" 2>/dev/null
