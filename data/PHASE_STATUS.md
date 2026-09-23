# Phase 0 implementation status (2026-09-22)

## Completed in code

| Gate / item | Status | Evidence |
|-------------|--------|----------|
| P0-12 G21 M5 ATR floor + per-symbol mult/TF | Done | `src/mtf.py`, `config.yaml`, retrain + `data/replay_results.json` |
| P0-9 G10 spread calibration | Bootstrap file present | `data/spread_calibration.json` — **re-run live `calibrate_spread.py` ×3 sessions before Phase 1 sign-off** |
| P0-3 confidence WF | Script + results path | `scripts/walk_forward_confidence.py` → `data/confidence_walk_forward.json`; `learning.online_confidence: false` until recommend_enable |
| P0.MM scaffold | Done | `src/money_management.py`, margin gate `REJECT_NO_MONEY`, VirtualAccount in replay, tests |
| P0-4/P0-8 demo evidence | Tooling ready | `scripts/verify_demo_evidence.py` — needs a short `python src/main.py` Demo run to populate logs |

## Phase 1

- Targets locked: `data/phase1_gate_targets.json`
- Checklist: `data/phase1_checklist.md`

## Phase 2 scaffolding landed

- Drift guard, Friday gap, opposing-structure TP / structure trail (`src/zones.py`)
- Headline feed (`src/news/headlines.py`), DualBrain, LLM worker stub
- SQLite journal, Streamlit dashboard, Telegram helper, watchdog

## Still human/calendar

1. Live multi-session `calibrate_spread.py` (London/NY, Asian, news)
2. Short Demo run for P0-8 log evidence
3. Phase 1 forward-test collection window
4. Train real ANN weights for dual-brain (optional pkl)
