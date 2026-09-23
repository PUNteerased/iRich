"""Local LLM worker process stub — isolated from the execution loop (Phase 2.B).

Run separately:
    python -m src.llm_worker
The trading loop must never import heavy LLM stacks; it only reads the
result file this worker writes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "llm_bias.json"


def write_neutral() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"bias": 0.0, "detail": "llm_worker_idle", "ts": time.time()}, indent=2),
        encoding="utf-8",
    )


def read_bias() -> float:
    if not OUT.exists():
        return 0.0
    try:
        return float(json.loads(OUT.read_text(encoding="utf-8")).get("bias", 0.0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0.0


def main() -> None:
    """Placeholder loop — replace with real local LLM inference later."""
    write_neutral()
    while True:
        write_neutral()
        time.sleep(60)


if __name__ == "__main__":
    main()
