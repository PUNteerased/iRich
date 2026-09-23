"""Rolling expectancy / win-rate drift guard (Phase 2.A)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class DriftState:
    halted: bool = False
    reason: str = ""
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    n: int = 0


class DriftGuard:
    """Halt new entries when rolling expectancy collapses."""

    def __init__(
        self,
        window: int = 20,
        min_trades: int = 10,
        min_win_rate: float = 0.30,
        min_expectancy_r: float = -0.25,
    ) -> None:
        self.window = window
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.min_expectancy_r = min_expectancy_r
        self._r: deque[float] = deque(maxlen=window)
        self.state = DriftState()

    def record(self, r_multiple: float) -> DriftState:
        self._r.append(float(r_multiple))
        n = len(self._r)
        self.state.n = n
        if n < self.min_trades:
            self.state.halted = False
            self.state.reason = ""
            return self.state
        wins = sum(1 for x in self._r if x > 0)
        self.state.win_rate = wins / n
        self.state.expectancy_r = sum(self._r) / n
        if self.state.win_rate < self.min_win_rate or self.state.expectancy_r < self.min_expectancy_r:
            self.state.halted = True
            self.state.reason = (
                f"drift_wr={self.state.win_rate:.3f}_expR={self.state.expectancy_r:.3f}"
            )
        else:
            self.state.halted = False
            self.state.reason = ""
        return self.state
