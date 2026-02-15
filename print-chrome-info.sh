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
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 1.5rem;
    }
    .card {
      background: rgba(30, 41, 59, 0.8);
      border: 1px solid rgba(148, 163, 184, 0.15);
      border-radius: 12px;
      padding: 1.75rem 2rem;
      width: 100%;
      max-width: 800px;
      backdrop-filter: blur(8px);
    }
    .badge {
      display: inline-block;
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      color: #fff;
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 0.25rem 0.65rem;
      border-radius: 999px;
      margin-bottom: 1rem;
    }
    .ua {
      font-size: 0.95rem;
      font-weight: 500;
      line-height: 1.5;
      color: #f1f5f9;
      word-break: break-word;
    }
    .divider {
      border: none;
      border-top: 1px solid rgba(148, 163, 184, 0.12);
      margin: 1rem 0;
    }
    .metrics {
      display: flex;
      gap: 2rem;
      flex-wrap: wrap;
    }
    .metric {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
    }
    .metric-label {
      font-size: 0.65rem;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: #94a3b8;
    }
    .metric-value {
      font-size: 1.1rem;
      font-weight: 600;
      color: #f8fafc;
    }
    .metric-value.highlight {
      color: #38bdf8;
    }
  </style>
</head>
<body>
  <div class="card">
    <span class="badge">Chrome ${CHROME_MILESTONE:-}</span>
    <p class="ua" id="ua"></p>
    <hr class="divider">
    <div class="metrics">
      <div class="metric">
        <span class="metric-label">JSON Parse</span>
        <span class="metric-value highlight" id="timing"></span>
      </div>
      <div class="metric">
        <span class="metric-label">Payload</span>
        <span class="metric-value" id="payload"></span>
      </div>
      <div class="metric">
        <span class="metric-label">Records</span>
        <span class="metric-value" id="records"></span>
      </div>
      <div class="metric">
        <span class="metric-label">Timestamp</span>
        <span class="metric-value" id="ts"></span>
      </div>
    </div>
  </div>
  <script>
    document.getElementById("ua").textContent = navigator.userAgent;

    var xhr = new XMLHttpRequest();
    xhr.open("GET", "file://${TMPJSON}", false);
    xhr.send();
    var raw = xhr.responseText;

    var t0 = performance.now();
    var parsed = JSON.parse(raw);
    var t1 = performance.now();

    var elapsed = (t1 - t0).toFixed(2);
    document.getElementById("timing").textContent = elapsed + " ms";
    document.getElementById("payload").textContent =
        (raw.length / 1024 / 1024).toFixed(2) + " MB";
    document.getElementById("records").textContent =
        parsed.length.toLocaleString();
    document.getElementById("ts").textContent =
        new Date().toISOString().replace("T", " ").replace(/\.\d+Z/, " UTC");
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
    --window-size=860,320 \
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
