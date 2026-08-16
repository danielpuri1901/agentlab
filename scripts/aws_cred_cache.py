"""Caching credential_process helper for the `agentlab` AWS profile.

Problem this solves: `aws login` sessions rotate short-lived tokens.
A static export can die mid-run (unpredictable remaining life), while
pointing credential_process straight at `aws configure export-credentials`
stampedes the token endpoint when Inspect opens ~30 parallel clients
(observed: 429 Rate exceeded on CreateOAuth2Token).

This helper serializes and caches: all concurrent callers share one cached
token from ~/.aws/agentlab-creds-cache.json while it has more than
MIN_REMAINING of life; a single locked caller refreshes it otherwise.

Wire-up (~/.aws/config):
    [profile agentlab]
    region = eu-west-1
    credential_process = python3 <repo>/scripts/aws_cred_cache.py
"""

import fcntl
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

CACHE_PATH = Path.home() / ".aws" / "agentlab-creds-cache.json"
MIN_REMAINING = timedelta(minutes=5)


def cache_is_fresh(raw: str, now: datetime) -> bool:
    """True if `raw` holds credentials valid for more than MIN_REMAINING."""
    try:
        data = json.loads(raw)
        expiry = datetime.fromisoformat(data["Expiration"])
    except (ValueError, KeyError):
        return False
    return expiry - now > MIN_REMAINING


def fetch_fresh() -> str:
    """Export current session credentials via the AWS CLI, with retry on 429."""
    last = ""
    for attempt in range(3):
        proc = subprocess.run(  # noqa: PLW1510 - returncode handled explicitly
            [
                "aws", "configure", "export-credentials",
                "--profile", "default", "--format", "process",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return proc.stdout
        last = proc.stderr
        time.sleep(2**attempt)
    raise SystemExit(f"aws export-credentials failed after retries: {last[:300]}")


def main() -> None:
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.touch(mode=0o600, exist_ok=True)
    with open(CACHE_PATH, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        raw = f.read()
        if not cache_is_fresh(raw, datetime.now(UTC)):
            raw = fetch_fresh()
            f.seek(0)
            f.truncate()
            f.write(raw)
            f.flush()
        sys.stdout.write(raw)


if __name__ == "__main__":
    main()
