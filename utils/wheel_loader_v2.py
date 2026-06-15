#!/usr/bin/env python3

"""
Wheel Loader (v2 API)

Loads a newline-delimited list of service names into an existing AWS Ops Wheel
(v2) wheel as participants. Every participant is created with the same URL
(http://example.com/ by default).

Authentication:
    Set OPS_WHEEL_TOKEN to a valid Cognito ID token (a JWT). The script sends
    it as `Authorization: Bearer <token>`.

API base URL:
    Set OPS_WHEEL_API_BASE_URL to the v2 API base, including the `/app/api/v2`
    suffix. Examples:
        https://<api-id>.execute-api.<region>.amazonaws.com/<stage>/app/api/v2
        https://<cloudfront-host>/app/api/v2

Input file format:
    One service name per line. Blank lines and lines starting with `#` are
    ignored. Leading/trailing whitespace is stripped.

Usage:
    export OPS_WHEEL_TOKEN="eyJraWQ..."
    export OPS_WHEEL_API_BASE_URL="https://abc123.execute-api.us-east-1.amazonaws.com/dev/app/api/v2"
    python3 wheel_loader_v2.py \\
        --wheel-id 57709419-17c9-4b77-ac99-77fb0d7c7c51 \\
        --file services.txt
"""

import argparse
import os
import sys
from typing import List, Tuple

try:
    import requests
except ImportError:
    print("Missing dependency. Install with: pip3 install requests", file=sys.stderr)
    raise SystemExit(1)


DEFAULT_PARTICIPANT_URL = "http://example.com/"
DEFAULT_WEIGHT = 1.0
ENV_TOKEN = "OPS_WHEEL_TOKEN"
ENV_BASE_URL = "OPS_WHEEL_API_BASE_URL"


def read_service_names(path: str) -> List[str]:
    """Read non-empty, non-comment lines from the input file."""
    names: List[str] = []
    seen_lower = set()
    with open(path, "r", encoding="utf-8") as f:
        for raw_line_no, raw in enumerate(f, start=1):
            name = raw.strip()
            if not name or name.startswith("#"):
                continue
            key = name.lower()
            if key in seen_lower:
                print(f"  warning: line {raw_line_no}: duplicate name '{name}' skipped",
                      file=sys.stderr)
                continue
            seen_lower.add(key)
            names.append(name)
    return names


def post_participant(
    session: requests.Session,
    base_url: str,
    wheel_id: str,
    name: str,
    url: str,
    weight: float,
    timeout: float,
) -> Tuple[bool, str]:
    """Create one participant. Returns (success, message)."""
    endpoint = f"{base_url.rstrip('/')}/wheels/{wheel_id}/participants"
    payload = {
        "participant_name": name,
        "participant_url": url,
        "weight": weight,
    }
    try:
        resp = session.post(endpoint, json=payload, timeout=timeout)
    except requests.RequestException as e:
        return False, f"request failed: {e}"

    if resp.status_code in (200, 201):
        try:
            pid = resp.json().get("participant_id", "<no id>")
        except ValueError:
            pid = "<unparseable response>"
        return True, f"created (participant_id={pid})"

    # Try to surface the server's error message.
    try:
        err = resp.json().get("error") or resp.text
    except ValueError:
        err = resp.text
    return False, f"HTTP {resp.status_code}: {err}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load service names into an existing AWS Ops Wheel (v2) as participants.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-w", "--wheel-id", required=True,
        help="UUID of the existing wheel to load participants into.",
    )
    parser.add_argument(
        "-f", "--file", required=True,
        help="Path to the input file. One service name per line; blank lines and `#` comments ignored.",
    )
    parser.add_argument(
        "--url", default=DEFAULT_PARTICIPANT_URL,
        help=f"URL to assign to every participant (default: {DEFAULT_PARTICIPANT_URL}).",
    )
    parser.add_argument(
        "--weight", type=float, default=DEFAULT_WEIGHT,
        help=f"Weight to assign to every participant (default: {DEFAULT_WEIGHT}).",
    )
    parser.add_argument(
        "--base-url", default=os.environ.get(ENV_BASE_URL),
        help=f"v2 API base URL including /app/api/v2. Defaults to ${ENV_BASE_URL}.",
    )
    parser.add_argument(
        "--token", default=os.environ.get(ENV_TOKEN),
        help=f"Cognito ID token (JWT). Defaults to ${ENV_TOKEN}.",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Per-request timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse the file and print what would be sent without calling the API.",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="Keep going after a failed upload instead of stopping at the first error.",
    )
    args = parser.parse_args()

    if not args.base_url:
        print(f"error: --base-url not provided and {ENV_BASE_URL} is not set", file=sys.stderr)
        return 2
    if not args.dry_run and not args.token:
        print(f"error: --token not provided and {ENV_TOKEN} is not set", file=sys.stderr)
        return 2

    try:
        names = read_service_names(args.file)
    except OSError as e:
        print(f"error: could not read {args.file}: {e}", file=sys.stderr)
        return 2

    if not names:
        print("No service names found in input file. Nothing to do.")
        return 0

    print(f"Loaded {len(names)} service name(s) from {args.file}")
    print(f"Target wheel:    {args.wheel_id}")
    print(f"API base:        {args.base_url}")
    print(f"Participant URL: {args.url}")
    print(f"Weight:          {args.weight}")
    print()

    if args.dry_run:
        print("Dry run. The following participants would be created:")
        for n in names:
            print(f"  - {n}  ->  {args.url}")
        return 0

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    succeeded = 0
    failed = 0
    for i, name in enumerate(names, start=1):
        ok, msg = post_participant(
            session, args.base_url, args.wheel_id,
            name, args.url, args.weight, args.timeout,
        )
        prefix = f"[{i}/{len(names)}] {name}"
        if ok:
            print(f"{prefix}: {msg}")
            succeeded += 1
        else:
            print(f"{prefix}: FAILED - {msg}", file=sys.stderr)
            failed += 1
            if not args.continue_on_error:
                print(f"\nStopping after first failure. {succeeded} created, "
                      f"{len(names) - i} not attempted.", file=sys.stderr)
                return 1

    print()
    print(f"Done. {succeeded} created, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
