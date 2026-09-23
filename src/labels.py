"""Triple-barrier labeling for sniper R:R 1:3."""

from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_WAIT = 0
LABEL_BUY = 1
LABEL_SELL = 2


def triple_barrier_labels(
    df: pd.DataFrame,
    atr_col: str = "atr_14",
    atr_sl: float = 1.0,
    atr_tp: float = 3.0,
    horizon: int = 45,
) -> pd.Series:
    """
    First-touch labeling on completed path t+1 .. t+horizon.
    BUY if TP(+atr_tp*ATR) touched before SL(-atr_sl*ATR).
    SELL if TP(-atr_tp*ATR) touched before SL(+atr_sl*ATR).
    """
    n = len(df)
    labels = np.full(n, LABEL_WAIT, dtype=int)
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    atrs = df[atr_col].to_numpy(dtype=float)

    for t in range(n - 1):
        atr = atrs[t]
        if not np.isfinite(atr) or atr <= 0:
            continue
        entry = closes[t]
        up_tp = entry + atr_tp * atr
        up_sl = entry - atr_sl * atr
        dn_tp = entry - atr_tp * atr
        dn_sl = entry + atr_sl * atr

        end = min(n, t + 1 + horizon)
        hit = LABEL_WAIT
        for j in range(t + 1, end):
            # check both sides; first touch wins
            hit_buy_tp = highs[j] >= up_tp
            hit_buy_sl = lows[j] <= up_sl
            hit_sell_tp = lows[j] <= dn_tp
            hit_sell_sl = highs[j] >= dn_sl

            buy_event = None
            sell_event = None
            if hit_buy_tp and hit_buy_sl:
                # ambiguous same bar -> WAIT for that side
                buy_event = None
            elif hit_buy_tp:
                buy_event = LABEL_BUY
            elif hit_buy_sl:
                buy_event = LABEL_WAIT  # SL first for long scenario

            if hit_sell_tp and hit_sell_sl:
                sell_event = None
            elif hit_sell_tp:
                sell_event = LABEL_SELL
            elif hit_sell_sl:
                sell_event = LABEL_WAIT

            # Prefer decisive TP events; if both TP same bar -> WAIT
            if buy_event == LABEL_BUY and sell_event == LABEL_SELL:
                hit = LABEL_WAIT
                break
            if buy_event == LABEL_BUY:
                hit = LABEL_BUY
                break
            if sell_event == LABEL_SELL:
                hit = LABEL_SELL
                break
            if hit_buy_sl and not hit_sell_tp:
                # long idea invalidated without short TP
                pass
            if hit_sell_sl and not hit_buy_tp:
                pass
        labels[t] = hit

    # last horizon bars cannot be labeled reliably
    labels[max(0, n - horizon) :] = LABEL_WAIT
    return pd.Series(labels, index=df.index, name="target")
