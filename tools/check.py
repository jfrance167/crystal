"""Run reusable local checks; writes no raw credentials or external requests.

Usage: python tools/check.py [--bandit]
Requires Python 3.11+. Bandit is optional and must already be installed.
Outputs: verification/tests.txt, verification/checks.json and optional bandit.json.
Exit status: zero only when every selected check passes.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bandit", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "verification"
    output.mkdir(exist_ok=True)
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                           cwd=root, capture_output=True, text=True, timeout=60, check=False)
    (output / "tests.txt").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
              "tests": {"passed": tests.returncode == 0, "exit_code": tests.returncode}}
    if args.bandit:
        scan = subprocess.run([sys.executable, "-m", "bandit", "-r", "crystal", "-f", "json", "-o", str(output / "bandit.json")],
                              cwd=root, capture_output=True, text=True, timeout=60, check=False)
        report["bandit"] = {"passed": scan.returncode == 0, "exit_code": scan.returncode}
    (output / "checks.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if all(report[name]["passed"] for name in ["tests", "bandit"] if name in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
