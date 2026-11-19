"""Security gate: pip-audit on the locked requirements, bandit on the code.

Fails (exit 1) on any known vulnerability or any high-severity bandit finding.
Cross-platform so `make check` and `python scripts/check.py` behave the same.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd), flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.call(cmd, cwd=ROOT, env=env)  # nosec B603 - fixed argv


def main() -> int:
    py = sys.executable
    failures = 0
    if run([py, "-m", "pip_audit", "--strict", "--require-hashes", "-r", "requirements.txt"]) != 0:
        print("pip-audit: FAILED", flush=True)
        failures += 1
    # -lll / -iii: report only HIGH severity, HIGH confidence findings; any such finding fails.
    if run([py, "-m", "bandit", "-r", "linkreach", "-c", "pyproject.toml", "--severity-level", "high", "-q"]) != 0:
        print("bandit: FAILED (high-severity finding)", flush=True)
        failures += 1
    print("check: " + ("FAILED" if failures else "passed"), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
