#!/usr/bin/env python3
"""
XAUUSD ICT / Smart-Money Signal Engine
Implements: BOS/CHOCH, Order Blocks, Liquidity Sweeps, FVG,
            Multi-TF (1M/5M), Candlestick Patterns, EMA/Momentum,
            Session Filter, Risk Management, Multi-Candle Confirmation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "XAUUSD_M1_sample.csv"

EMA_SHORT  = 9
EMA_MID    = 21
SWING_WIN  = 5          # bars each side to qualify a swing high/low
VOL_LOOKBACK = 10       # bars for average volume comparison
MULTI_CANDLE_BARS = 5   # last N candles must agree
RR_MIN = 2.0            # minimum risk-to-reward ratio

# London: 07:00–12:00 UTC  |  New York: 12:00–17:00 UTC
LONDON_START, LONDON_END = 7, 12
NY_START,     NY_END     = 12, 17


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------
def load_m1(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        header=None,
        names=["date", "time", "open", "high", "low", "close", "volume", "_a", "_b"],
    )
    df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"],
                                    format="%Y.%m.%d %H:%M")
    df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Helper: EMA
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Rule 8 – Session filter
# ---------------------------------------------------------------------------
def in_session(dt: pd.Timestamp) -> bool:
    h = dt.hour
    return (LONDON_START <= h < LONDON_END) or (NY_START <= h < NY_END)


# ---------------------------------------------------------------------------
# Rule 1 – Swing highs / lows & Market Structure (BOS / CHOCH)
# ---------------------------------------------------------------------------
def find_swing_points(df: pd.DataFrame, win: int = SWING_WIN):
    """
    Returns two boolean Series: is_swing_high, is_swing_low.
    A swing high at i means df['high'][i] is the highest among [i-win .. i+win].
    """
    n = len(df)
    sh = pd.array([False] * n, dtype="boolean")
    sl = pd.array([False] * n, dtype="boolean")
    for i in range(win, n - win):
        window_h = df["high"].iloc[i - win: i + win + 1]
        window_l = df["low"].iloc[i - win: i + win + 1]
        if df["high"].iloc[i] == window_h.max():
            sh[i] = True
        if df["low"].iloc[i] == window_l.min():
            sl[i] = True
    df["is_sh"] = sh
    df["is_sl"] = sl
    return df


def classify_market_structure(df: pd.DataFrame) -> str:
    """
    Returns 'BULLISH', 'BEARISH', or 'NEUTRAL' based on the most recent
    BOS / CHOCH derived from confirmed swing highs and lows.
    Uses the last 3–5 candles and swing structure.
    """
    sh_idx = df.index[df["is_sh"]].tolist()
    sl_idx = df.index[df["is_sl"]].tolist()

    if len(sh_idx) < 2 or len(sl_idx) < 2:
        return "NEUTRAL"

    # Latest two swing highs and lows
    last_sh2, last_sh1 = sh_idx[-2], sh_idx[-1]
    last_sl2, last_sl1 = sl_idx[-2], sl_idx[-1]

    hh = df["high"].iloc[last_sh1] > df["high"].iloc[last_sh2]   # higher high
    hl = df["low"].iloc[last_sl1]  > df["low"].iloc[last_sl2]    # higher low
    ll = df["low"].iloc[last_sl1]  < df["low"].iloc[last_sl2]    # lower low
    lh = df["high"].iloc[last_sh1] < df["high"].iloc[last_sh2]   # lower high

    if hh and hl:
        return "BULLISH"
    if ll and lh:
        return "BEARISH"
    # Mixed – check last few candles for CHoCH signal
    last5 = df.tail(5)
    bull_count = (last5["close"] > last5["open"]).sum()
    bear_count = (last5["close"] < last5["open"]).sum()
    if bull_count >= 3:
        return "BULLISH"
    if bear_count >= 3:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Rule 2 – Order Blocks
# ---------------------------------------------------------------------------
def find_order_blocks(df: pd.DataFrame):
    """
    Bullish OB  = last BEARISH candle before a significant bullish impulse.
    Bearish OB  = last BULLISH candle before a significant bearish impulse.
    Returns (bullish_ob_row, bearish_ob_row) – the most recent valid OBs.
    Validity condition: volume of impulse candle > avg volume of prior window.
    """
    avg_vol = df["volume"].rolling(VOL_LOOKBACK).mean()

    bull_ob: Optional[pd.Series] = None
    bear_ob: Optional[pd.Series] = None

    for i in range(2, len(df) - 1):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        imp_vol = curr["volume"]
        avg = avg_vol.iloc[i]
        if pd.isna(avg):
            continue

        # Bullish impulse after bearish OB candle
        if (prev["close"] < prev["open"]                    # prev = bearish OB candle
                and curr["close"] > curr["open"]            # current = bullish impulse
                and curr["close"] > prev["high"]            # breaks above OB high
                and imp_vol > avg):                         # volume confirmation
            bull_ob = prev

        # Bearish impulse after bullish OB candle
        if (prev["close"] > prev["open"]                    # prev = bullish OB candle
                and curr["close"] < curr["open"]            # current = bearish impulse
                and curr["close"] < prev["low"]             # breaks below OB low
                and imp_vol > avg):                         # volume confirmation
            bear_ob = prev

    return bull_ob, bear_ob


# ---------------------------------------------------------------------------
# Rule 3 – Liquidity / Stop Hunt
# ---------------------------------------------------------------------------
def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20):
    """
    Detects whether the last candle swept a recent high or low
    (wick beyond the extreme then closed back inside).
    Returns 'HIGH_SWEPT', 'LOW_SWEPT', or None.
    """
    if len(df) < lookback + 1:
        return None

    window = df.iloc[-(lookback + 1): -1]
    last   = df.iloc[-1]

    recent_high = window["high"].max()
    recent_low  = window["low"].min()

    # Wick above recent high but closed back below (bearish sweep)
    if last["high"] > recent_high and last["close"] < recent_high:
        return "HIGH_SWEPT"

    # Wick below recent low but closed back above (bullish sweep)
    if last["low"] < recent_low and last["close"] > recent_low:
        return "LOW_SWEPT"

    return None


# ---------------------------------------------------------------------------
# Rule 4 – Fair Value Gap
# ---------------------------------------------------------------------------
def find_fvg(df: pd.DataFrame):
    """
    Bullish FVG: candle[i+2].low > candle[i].high  (gap between them)
    Bearish FVG: candle[i+2].high < candle[i].low
    Returns (latest_bull_fvg, latest_bear_fvg) each as dict {top, bottom, idx}.
    """
    bull_fvg = None
    bear_fvg = None

    for i in range(len(df) - 2):
        c1 = df.iloc[i]
        c3 = df.iloc[i + 2]

        if c3["low"] > c1["high"]:      # bullish gap
            bull_fvg = {"top": c3["low"], "bottom": c1["high"], "idx": i}

        if c3["high"] < c1["low"]:      # bearish gap
            bear_fvg = {"top": c1["low"], "bottom": c3["high"], "idx": i}

    return bull_fvg, bear_fvg


# ---------------------------------------------------------------------------
# Rule 5 – 5-Minute Aggregation
# ---------------------------------------------------------------------------
def resample_5m(df: pd.DataFrame) -> pd.DataFrame:
    df5 = (
        df.set_index("datetime")
        .resample("5min")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return df5


def htf_trend(df5: pd.DataFrame) -> str:
    """Returns 5M trend as BULLISH / BEARISH / NEUTRAL."""
    if len(df5) < 4:
        return "NEUTRAL"
    e9  = ema(df5["close"], 9)
    e21 = ema(df5["close"], 21)
    last_close = df5["close"].iloc[-1]
    if e9.iloc[-1] > e21.iloc[-1] and last_close > e9.iloc[-1]:
        return "BULLISH"
    if e9.iloc[-1] < e21.iloc[-1] and last_close < e9.iloc[-1]:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Rule 6 – Candlestick Patterns
# ---------------------------------------------------------------------------
def detect_pattern(df: pd.DataFrame) -> str:
    """Detects the last significant candlestick pattern."""
    if len(df) < 2:
        return "NONE"

    last = df.iloc[-1]
    prev = df.iloc[-2]

    body   = abs(last["close"] - last["open"])
    candle = last["high"] - last["low"]
    upper  = last["high"] - max(last["open"], last["close"])
    lower  = min(last["open"], last["close"]) - last["low"]

    if candle < 1e-6:
        return "NONE"

    # Bullish Engulfing
    if (last["close"] > last["open"]
            and prev["close"] < prev["open"]
            and last["open"] < prev["close"]
            and last["close"] > prev["open"]):
        return "BULL_ENGULF"

    # Bearish Engulfing
    if (last["close"] < last["open"]
            and prev["close"] > prev["open"]
            and last["open"] > prev["close"]
            and last["close"] < prev["open"]):
        return "BEAR_ENGULF"

    # Hammer (bullish reversal from bottom)
    if (lower > 2 * body
            and upper < 0.5 * body
            and body > 0
            and last["close"] > last["open"]):
        return "HAMMER"

    # Shooting Star (bearish reversal from top)
    if (upper > 2 * body
            and lower < 0.5 * body
            and body > 0
            and last["close"] < last["open"]):
        return "SHOOTING_STAR"

    # Pin Bar (long wick either side, tiny body)
    if body < 0.3 * candle:
        if upper > lower:
            return "BEARISH_PIN"
        return "BULLISH_PIN"

    return "NONE"


# ---------------------------------------------------------------------------
# Rule 7 – EMA & Momentum
# ---------------------------------------------------------------------------
def ema_momentum_check(df: pd.DataFrame):
    """
    Returns (direction, strength) where direction ∈ {BULLISH, BEARISH, NEUTRAL}
    and strength ∈ {STRONG, WEAK}.
    """
    if len(df) < EMA_MID + 2:
        return "NEUTRAL", "WEAK"

    e_short = ema(df["close"], EMA_SHORT)
    e_mid   = ema(df["close"], EMA_MID)

    last_close = df["close"].iloc[-1]
    es, em = e_short.iloc[-1], e_mid.iloc[-1]

    # EMA alignment
    if es > em and last_close > es:
        direction = "BULLISH"
    elif es < em and last_close < es:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Momentum: last candle body vs. average body
    bodies = (df["close"] - df["open"]).abs()
    avg_body = bodies.iloc[-VOL_LOOKBACK:].mean()
    last_body = bodies.iloc[-1]
    strength = "STRONG" if last_body > avg_body else "WEAK"

    # Volume: last candle volume vs. recent average
    avg_vol  = df["volume"].iloc[-VOL_LOOKBACK:].mean()
    last_vol = df["volume"].iloc[-1]
    if last_vol < avg_vol and strength == "STRONG":
        strength = "WEAK"

    return direction, strength


# ---------------------------------------------------------------------------
# Rule 10 – Multi-Candle Confirmation
# ---------------------------------------------------------------------------
def multi_candle_confirm(df: pd.DataFrame, direction: str) -> bool:
    """
    Returns True if the last MULTI_CANDLE_BARS candles agree with 'direction'.
    At least 3 out of 5 must be aligned (close > open for BULLISH, etc.).
    """
    if len(df) < MULTI_CANDLE_BARS:
        return False
    last_n = df.tail(MULTI_CANDLE_BARS)
    if direction == "BULLISH":
        count = (last_n["close"] > last_n["open"]).sum()
    elif direction == "BEARISH":
        count = (last_n["close"] < last_n["open"]).sum()
    else:
        return False
    return count >= 3


# ---------------------------------------------------------------------------
# Rule 9 – Risk Management
# ---------------------------------------------------------------------------
def compute_sl_tp(df: pd.DataFrame, direction: str):
    """
    SL: behind the last valid swing high (for SELL) or swing low (for BUY).
    TP: entry ± RR_MIN * risk.
    """
    entry = float(df["close"].iloc[-1])

    # Buffer = ATR proxy (average candle range of last 10 bars)
    atr = (df["high"] - df["low"]).tail(10).mean()
    buf = float(atr) * 0.5

    if direction == "BULLISH":
        # SL below the most recent swing low
        sl_idx = df.index[df["is_sl"]].tolist()
        if sl_idx:
            sl_price = float(df["low"].iloc[sl_idx[-1]]) - buf
        else:
            sl_price = entry - 2 * float(atr)
        risk = entry - sl_price
        if risk <= 0:
            return None, None
        tp_price = entry + RR_MIN * risk
    else:  # BEARISH
        sh_idx = df.index[df["is_sh"]].tolist()
        if sh_idx:
            sl_price = float(df["high"].iloc[sh_idx[-1]]) + buf
        else:
            sl_price = entry + 2 * float(atr)
        risk = sl_price - entry
        if risk <= 0:
            return None, None
        tp_price = entry - RR_MIN * risk

    return round(sl_price, 2), round(tp_price, 2)


# ---------------------------------------------------------------------------
# Main signal engine
# ---------------------------------------------------------------------------
def generate_signal(df: pd.DataFrame) -> dict:
    # --- Prepare indicators ---
    df = find_swing_points(df.copy())

    last_dt  = df["datetime"].iloc[-1]
    last_close = float(df["close"].iloc[-1])

    # Rule 8 – Session
    if not in_session(last_dt):
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 1 – Market structure on 1M
    structure_1m = classify_market_structure(df)

    # Rule 5 – 5M HTF trend
    df5 = resample_5m(df)
    structure_5m = htf_trend(df5)

    # Rule 7 – EMA / Momentum on 1M
    ema_dir, momentum = ema_momentum_check(df)

    # Require all three layers to agree; if not, skip
    directions = {structure_1m, structure_5m, ema_dir}
    if "NEUTRAL" in directions or len(directions) > 1:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    trade_dir = structure_1m  # BULLISH or BEARISH

    # Rule 10 – Multi-candle confirmation
    if not multi_candle_confirm(df, trade_dir):
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 7 – Momentum must be STRONG
    if momentum != "STRONG":
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 2 – Order Block presence (OB must exist in trade direction)
    bull_ob, bear_ob = find_order_blocks(df)
    if trade_dir == "BULLISH" and bull_ob is None:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}
    if trade_dir == "BEARISH" and bear_ob is None:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 4 – FVG confirmation
    bull_fvg, bear_fvg = find_fvg(df)
    if trade_dir == "BULLISH" and bull_fvg is None:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}
    if trade_dir == "BEARISH" and bear_fvg is None:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 3 – Liquidity sweep (optional boost; don't block if absent)
    sweep = detect_liquidity_sweep(df)
    sweep_aligned = (
        (trade_dir == "BULLISH" and sweep == "LOW_SWEPT")
        or (trade_dir == "BEARISH" and sweep == "HIGH_SWEPT")
        or sweep is None          # no sweep → neutral, still allowed
    )
    if not sweep_aligned:
        # Sweep in opposite direction is a contradicting signal → skip
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 6 – Candlestick pattern must not contradict
    pattern = detect_pattern(df)
    bearish_patterns = {"BEAR_ENGULF", "SHOOTING_STAR", "BEARISH_PIN"}
    bullish_patterns = {"BULL_ENGULF", "HAMMER", "BULLISH_PIN"}
    if trade_dir == "BULLISH" and pattern in bearish_patterns:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}
    if trade_dir == "BEARISH" and pattern in bullish_patterns:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # Rule 9 – Compute SL / TP with minimum RR
    sl, tp = compute_sl_tp(df, trade_dir)
    if sl is None or tp is None:
        return {"signal": "NO TRADE", "entry": last_close, "SL": None, "TP": None}

    # All rules passed – emit signal
    return {
        "signal": "BUY" if trade_dir == "BULLISH" else "SELL",
        "entry":  round(last_close, 2),
        "SL":     sl,
        "TP":     tp,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    df = load_m1(DATA_PATH)
    result = generate_signal(df)
    print(json.dumps(result, indent=2))
