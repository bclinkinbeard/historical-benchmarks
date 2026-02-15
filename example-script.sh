#!/bin/sh
# Example script to demonstrate the harness.
# Prints the Chrome version info and verifies the binary exists.

echo "Chrome milestone: $CHROME_MILESTONE"
echo "Chrome version:   $CHROME_VERSION"
echo "Release date:     $CHROME_RELEASE_DATE"
echo "Chrome binary:    $CHROME_BIN"
echo "Chromedriver:     $CHROMEDRIVER_BIN"

if [ -x "$CHROME_BIN" ]; then
    "$CHROME_BIN" --version --no-sandbox 2>/dev/null || true
    echo "OK"
else
    echo "ERROR: Chrome binary not found or not executable"
    exit 1
fi
