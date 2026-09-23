"""Simple watchdog: restart src/main.py if the process exits (Phase 2.C).

    python scripts/watchdog_main.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src" / "main.py"


def main() -> None:
    while True:
        print("Starting bot...")
        proc = subprocess.Popen([sys.executable, str(MAIN)], cwd=str(ROOT))
        code = proc.wait()
        print(f"Bot exited with {code}; restarting in 5s")
        time.sleep(5)


if __name__ == "__main__":
    main()
