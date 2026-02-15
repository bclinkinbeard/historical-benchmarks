#!/usr/bin/env python3
"""
Chrome Release Harness

For each Chrome stable release in the past two years, downloads Chrome for
Testing and runs a user-provided shell script with the Chrome binary available.

Usage:
    ./run-chrome-releases.py <script> [options]

The user's script receives these environment variables:
    CHROME_VERSION       Full version string (e.g. "132.0.6834.83")
    CHROME_MILESTONE     Milestone number (e.g. "132")
    CHROME_DIR           Path to the extracted Chrome for Testing directory
    CHROME_BIN           Path to the Chrome binary
    CHROME_RELEASE_DATE  ISO date of the first stable release (e.g. "2025-01-14")
    CHROMEDRIVER_BIN     Path to chromedriver binary (if available)
    CHROMEDRIVER_DIR     Path to extracted chromedriver directory (if available)
"""

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

CFT_MILESTONES_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "latest-versions-per-milestone-with-downloads.json"
)

CHROMIUMDASH_RELEASES_URL = (
    "https://chromiumdash.appspot.com/fetch_releases"
    "?channel=Stable&platform={platform}&num=200"
)

# Maps Python platform info to Chrome for Testing platform identifiers.
CFT_PLATFORMS = {
    ("Linux", "x86_64"): "linux64",
    ("Darwin", "x86_64"): "mac-x64",
    ("Darwin", "arm64"): "mac-arm64",
    ("Windows", "AMD64"): "win64",
}

# Maps Chrome for Testing platform to the ChromiumDash platform name.
DASH_PLATFORMS = {
    "linux64": "Linux",
    "mac-x64": "Mac",
    "mac-arm64": "Mac",
    "win64": "Windows",
}

# Lock for printing from parallel workers.
_print_lock = threading.Lock()


def log(msg="", end="\n"):
    """Thread-safe print."""
    with _print_lock:
        print(msg, end=end, flush=True)


def detect_platform():
    """Detect the current platform for Chrome for Testing downloads."""
    key = (platform.system(), platform.machine())
    cft = CFT_PLATFORMS.get(key)
    if not cft:
        sys.exit(
            f"Unsupported platform: {key[0]} {key[1]}. "
            f"Supported: {', '.join(f'{s} {m}' for s, m in CFT_PLATFORMS)}"
        )
    return cft


def fetch_json(url):
    """Fetch and parse a JSON URL."""
    req = urllib.request.Request(url, headers={"User-Agent": "chrome-release-harness/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_cft_milestones():
    """Fetch available Chrome for Testing milestones with download URLs."""
    print("Fetching Chrome for Testing milestones...")
    return fetch_json(CFT_MILESTONES_URL)


def get_release_dates(dash_platform):
    """Fetch stable release dates from ChromiumDash.

    Returns a dict mapping milestone number to the ISO date string
    of the first stable release for that milestone.
    """
    print("Fetching Chrome stable release dates...")
    url = CHROMIUMDASH_RELEASES_URL.format(platform=dash_platform)
    data = fetch_json(url)

    # Find the earliest stable release timestamp for each milestone.
    milestones = {}
    for release in data:
        m = release["milestone"]
        ts = release["time"]
        if m not in milestones or ts < milestones[m]:
            milestones[m] = ts

    return {
        m: datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for m, ts in milestones.items()
    }


def build_release_list(cft_data, release_dates, cft_platform, years):
    """Build a sorted list of releases to process.

    Each entry is a dict with: milestone, version, release_date,
    chrome_url, chromedriver_url.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=years * 365)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    releases = []
    for mstr, info in cft_data["milestones"].items():
        milestone = int(mstr)
        release_date = release_dates.get(milestone)
        if release_date is None:
            continue
        if release_date < cutoff_str:
            continue

        # Find download URLs for the target platform.
        chrome_url = None
        chromedriver_url = None
        for dl in info.get("downloads", {}).get("chrome", []):
            if dl["platform"] == cft_platform:
                chrome_url = dl["url"]
        for dl in info.get("downloads", {}).get("chromedriver", []):
            if dl["platform"] == cft_platform:
                chromedriver_url = dl["url"]

        if chrome_url is None:
            print(f"  Skipping milestone {milestone}: no download for {cft_platform}")
            continue

        releases.append({
            "milestone": milestone,
            "version": info["version"],
            "release_date": release_date,
            "chrome_url": chrome_url,
            "chromedriver_url": chromedriver_url,
        })

    releases.sort(key=lambda r: r["milestone"])
    return releases


def download_and_extract(url, dest_dir, label=""):
    """Download a zip from url and extract it to dest_dir. Returns the path to
    the extracted top-level directory."""
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    try:
        log(f"  {label}  Downloading {url.split('/')[-1]}...")
        urllib.request.urlretrieve(url, tmp_path)
        with zipfile.ZipFile(tmp_path) as zf:
            zf.extractall(dest_dir)
            # Restore Unix file permissions from the zip metadata.
            # Python's extractall does not do this by default, which
            # leaves binaries like chrome_crashpad_handler non-executable.
            for info in zf.infolist():
                if info.external_attr:
                    perm = info.external_attr >> 16
                    if perm:
                        os.chmod(
                            os.path.join(dest_dir, info.filename),
                            perm,
                        )
    finally:
        os.close(fd)
        os.unlink(tmp_path)

    # Return the top-level extracted directory.
    entries = list(Path(dest_dir).iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return str(entries[0])
    return dest_dir


def find_chrome_binary(chrome_dir):
    """Locate the Chrome binary inside the extracted directory."""
    candidates = [
        os.path.join(chrome_dir, "chrome"),
        os.path.join(chrome_dir, "chrome.exe"),
        os.path.join(chrome_dir, "Google Chrome for Testing.app",
                     "Contents", "MacOS", "Google Chrome for Testing"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            os.chmod(c, 0o755)
            return c
    # Fallback: find any file named "chrome" recursively.
    for root, _dirs, files in os.walk(chrome_dir):
        for f in files:
            if f in ("chrome", "chrome.exe"):
                path = os.path.join(root, f)
                os.chmod(path, 0o755)
                return path
    return None


def find_chromedriver_binary(driver_dir):
    """Locate the chromedriver binary inside the extracted directory."""
    candidates = [
        os.path.join(driver_dir, "chromedriver"),
        os.path.join(driver_dir, "chromedriver.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            os.chmod(c, 0o755)
            return c
    for root, _dirs, files in os.walk(driver_dir):
        for f in files:
            if f in ("chromedriver", "chromedriver.exe"):
                path = os.path.join(root, f)
                os.chmod(path, 0o755)
                return path
    return None


def run_script(script, env, timeout=None, capture=False):
    """Run the user's script with the given environment.

    Returns (exit_code, duration_seconds, stdout_text).
    When capture=False stdout_text is empty.
    """
    start = datetime.now()
    stdout_arg = subprocess.PIPE if capture else None
    stderr_arg = subprocess.STDOUT if capture else None
    try:
        result = subprocess.run(
            [script],
            env=env,
            timeout=timeout,
            shell=False,
            stdout=stdout_arg,
            stderr=stderr_arg,
        )
        duration = (datetime.now() - start).total_seconds()
        output = result.stdout.decode(errors="replace") if capture and result.stdout else ""
        return result.returncode, duration, output
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start).total_seconds()
        return -1, duration, ""
    except PermissionError:
        # Try running through sh.
        result = subprocess.run(
            ["sh", script],
            env=env,
            timeout=timeout,
            stdout=stdout_arg,
            stderr=stderr_arg,
        )
        duration = (datetime.now() - start).total_seconds()
        output = result.stdout.decode(errors="replace") if capture and result.stdout else ""
        return result.returncode, duration, output


def process_release(release, script, cache_dir, timeout, parallel):
    """Download Chrome, run the script, and return a result dict.

    This function is safe to call from a thread.
    """
    milestone = release["milestone"]
    version = release["version"]
    tag = f"[Chrome {milestone}]"

    if cache_dir:
        chrome_extract_dir = os.path.join(cache_dir, f"chrome-{version}")
        driver_extract_dir = os.path.join(cache_dir, f"chromedriver-{version}")
        use_cache = True
    else:
        chrome_extract_dir = tempfile.mkdtemp(prefix=f"chrome-{milestone}-")
        driver_extract_dir = tempfile.mkdtemp(prefix=f"chromedriver-{milestone}-")
        use_cache = False

    try:
        # Download and extract Chrome.
        chrome_dir = None
        if use_cache and os.path.isdir(chrome_extract_dir):
            log(f"  {tag}  Using cached Chrome...")
            entries = list(Path(chrome_extract_dir).iterdir())
            chrome_dir = str(entries[0]) if len(entries) == 1 and entries[0].is_dir() else chrome_extract_dir
        else:
            os.makedirs(chrome_extract_dir, exist_ok=True)
            chrome_dir = download_and_extract(release["chrome_url"], chrome_extract_dir, label=tag)

        chrome_bin = find_chrome_binary(chrome_dir)
        if not chrome_bin:
            log(f"  {tag}  ERROR: Could not find Chrome binary in {chrome_dir}")
            return {
                "milestone": milestone, "version": version,
                "exit_code": -2, "error": "binary not found", "output": "",
            }

        # Download and extract chromedriver (optional).
        chromedriver_dir = None
        chromedriver_bin = ""
        if release.get("chromedriver_url"):
            if use_cache and os.path.isdir(driver_extract_dir):
                log(f"  {tag}  Using cached chromedriver...")
                entries = list(Path(driver_extract_dir).iterdir())
                chromedriver_dir = str(entries[0]) if len(entries) == 1 and entries[0].is_dir() else driver_extract_dir
            else:
                os.makedirs(driver_extract_dir, exist_ok=True)
                chromedriver_dir = download_and_extract(release["chromedriver_url"], driver_extract_dir, label=tag)
            chromedriver_bin = find_chromedriver_binary(chromedriver_dir) or ""

        # Build environment for the user's script.
        env = os.environ.copy()
        env["CHROME_VERSION"] = version
        env["CHROME_MILESTONE"] = str(milestone)
        env["CHROME_DIR"] = chrome_dir
        env["CHROME_BIN"] = chrome_bin
        env["CHROME_RELEASE_DATE"] = release["release_date"]
        env["CHROMEDRIVER_BIN"] = chromedriver_bin
        env["CHROMEDRIVER_DIR"] = chromedriver_dir or ""

        # Run the user's script.
        log(f"  {tag}  Running script...")
        exit_code, duration, output = run_script(
            script, env, timeout=timeout, capture=parallel,
        )

        status = "OK" if exit_code == 0 else ("TIMEOUT" if exit_code == -1 else f"FAILED ({exit_code})")
        log(f"  {tag}  {status} ({duration:.1f}s)")

        return {
            "milestone": milestone,
            "version": version,
            "exit_code": exit_code,
            "duration": duration,
            "output": output,
        }

    finally:
        if not use_cache:
            shutil.rmtree(chrome_extract_dir, ignore_errors=True)
            shutil.rmtree(driver_extract_dir, ignore_errors=True)


def run_sequential(releases, script, cache_dir, timeout, continue_on_error):
    """Run releases one at a time (original behaviour)."""
    results = []
    interrupted = False

    def handle_sigint(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\nInterrupted. Finishing current release...")

    prev_handler = signal.signal(signal.SIGINT, handle_sigint)

    for i, release in enumerate(releases):
        if interrupted:
            break

        milestone = release["milestone"]
        version = release["version"]
        print(f"[{i+1}/{len(releases)}] Chrome {milestone} ({version}, {release['release_date']})")

        result = process_release(release, script, cache_dir, timeout, parallel=False)
        results.append(result)

        if result["exit_code"] != 0 and not continue_on_error:
            print("    Stopping due to failure (use --continue-on-error to keep going).")
            break

    signal.signal(signal.SIGINT, prev_handler)
    return results


def run_parallel(releases, script, cache_dir, timeout, continue_on_error, jobs):
    """Run releases in parallel using a thread pool."""
    total = len(releases)
    log(f"Running {total} releases with {jobs} parallel workers\n")

    results_by_milestone = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        future_to_release = {
            pool.submit(process_release, r, script, cache_dir, timeout, parallel=True): r
            for r in releases
        }

        try:
            for future in as_completed(future_to_release):
                result = future.result()
                results_by_milestone[result["milestone"]] = result
                completed_count += 1
                log(f"  Completed {completed_count}/{total}: Chrome {result['milestone']}")
        except KeyboardInterrupt:
            log("\nInterrupted. Cancelling remaining tasks...")
            pool.shutdown(wait=False, cancel_futures=True)

    # Print captured output in milestone order.
    print("\n" + "-" * 60)
    print("Output")
    print("-" * 60)
    for release in releases:
        m = release["milestone"]
        if m not in results_by_milestone:
            continue
        result = results_by_milestone[m]
        output = result.get("output", "").strip()
        if output:
            print(f"\n--- Chrome {m} ({release['version']}) ---")
            print(output)

    # Return results sorted by milestone.
    return [results_by_milestone[r["milestone"]] for r in releases if r["milestone"] in results_by_milestone]


def main():
    parser = argparse.ArgumentParser(
        description="Run a script against each Chrome stable release from the past two years.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "script",
        help="Path to the shell script to run for each Chrome release.",
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=1,
        help="Number of parallel workers (default: 1 = sequential).",
    )
    parser.add_argument(
        "--platform",
        choices=sorted(set(CFT_PLATFORMS.values())),
        default=None,
        help="Chrome for Testing platform (default: auto-detect).",
    )
    parser.add_argument(
        "--years",
        type=float,
        default=2,
        help="How many years of releases to include (default: 2).",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directory to cache downloaded Chrome binaries. "
             "If not set, downloads are extracted to a temp directory and "
             "deleted after each run.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout in seconds for each script invocation.",
    )
    parser.add_argument(
        "--milestones",
        type=str,
        default=None,
        help="Comma-separated list of milestone numbers to run "
             "(e.g. '130,131,132'). Overrides --years filtering.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Continue running remaining releases if the script fails.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List releases that would be processed without downloading or running.",
    )

    args = parser.parse_args()

    # Resolve the script path.
    script = os.path.abspath(args.script)
    if not os.path.isfile(script):
        sys.exit(f"Script not found: {script}")

    # Detect platform.
    cft_platform = args.platform or detect_platform()
    dash_platform = DASH_PLATFORMS.get(cft_platform, "Linux")

    # Fetch data.
    cft_data = get_cft_milestones()
    release_dates = get_release_dates(dash_platform)

    # Build release list.
    releases = build_release_list(cft_data, release_dates, cft_platform, args.years)

    # Apply milestone filter if specified.
    if args.milestones:
        allowed = {int(m.strip()) for m in args.milestones.split(",")}
        releases = [r for r in releases if r["milestone"] in allowed]

    if not releases:
        sys.exit("No matching releases found.")

    print(f"\nFound {len(releases)} Chrome releases to process:\n")
    for r in releases:
        print(f"  Chrome {r['milestone']:>3}  {r['version']:<20}  ({r['release_date']})")
    print()

    if args.dry_run:
        return

    # Set up cache directory.
    cache_dir = None
    if args.cache_dir:
        cache_dir = os.path.abspath(args.cache_dir)
        os.makedirs(cache_dir, exist_ok=True)

    jobs = max(1, args.jobs)

    if jobs == 1:
        results = run_sequential(releases, script, cache_dir, args.timeout, args.continue_on_error)
    else:
        results = run_parallel(releases, script, cache_dir, args.timeout, args.continue_on_error, jobs)

    # Print summary.
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    passed = sum(1 for r in results if r["exit_code"] == 0)
    failed = sum(1 for r in results if r["exit_code"] != 0)
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print(f"  Total:  {len(results)}/{len(releases)}")
    print()
    for r in results:
        status = "PASS" if r["exit_code"] == 0 else "FAIL"
        dur = f"{r.get('duration', 0):.1f}s" if "duration" in r else "N/A"
        print(f"  [{status}] Chrome {r['milestone']:>3}  {r['version']:<20}  {dur}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
