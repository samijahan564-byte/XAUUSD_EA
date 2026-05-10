#!/usr/bin/env python3
"""
XAUUSD Smart Money / ICT Signal Analyzer

Analyzes OHLC+Volume candle data using Inner Circle Trader (ICT) concepts and
outputs a single JSON trade signal.  Implements ten analysis layers:

 1. Market Structure  (BOS / CHOCH)
 2. Order Blocks
 3. Liquidity & Stop Hunt
 4. Fair Value Gaps (FVG)
 5. Multi-Timeframe Confirmation (5M trend -> 1M entry)
 6. Candlestick Patterns
 7. EMA & Momentum
 8. Session Filter
 9. Risk Management
10. Multi-Candle Confirmation

Usage:
    python scripts/ict_signal_analyzer.py                        # reads data/XAUUSD_M1_sample.csv
    python scripts/ict_signal_analyzer.py path/to/candles.csv    # reads a custom file
    cat candles.csv | python scripts/ict_signal_analyzer.py -    # reads from stdin
    python scripts/ict_signal_analyzer.py --verbose              # diagnostic output on stderr

CSV format (no header row):
    Date,Time,Open,High,Low,Close,TickVolume,Volume,Spread
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Candle data
# ---------------------------------------------------------------------------
@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    volume: int
    spread: int

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0


# ---------------------------------------------------------------------------
# Verbose / diagnostic logging (stderr, never contaminates JSON stdout)
# ---------------------------------------------------------------------------
VERBOSE = False


def _log(fmt: str, *args: object) -> None:
    if VERBOSE:
        print("[ICT]", fmt % args, file=sys.stderr)


# ---------------------------------------------------------------------------
# Swing point detection
# ---------------------------------------------------------------------------
@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" or "low"


def detect_swings(candles: List[Candle], left: int = 3, right: int = 3) -> List[SwingPoint]:
    swings: List[SwingPoint] = []
    n = len(candles)
    for i in range(left, n - right):
        is_high = all(candles[i].high >= candles[i - j].high for j in range(1, left + 1)) and all(
            candles[i].high >= candles[i + j].high for j in range(1, right + 1)
        )
        if is_high:
            swings.append(SwingPoint(i, candles[i].high, "high"))

        is_low = all(candles[i].low <= candles[i - j].low for j in range(1, left + 1)) and all(
            candles[i].low <= candles[i + j].low for j in range(1, right + 1)
        )
        if is_low:
            swings.append(SwingPoint(i, candles[i].low, "low"))

    swings.sort(key=lambda s: s.index)
    return swings


# ---------------------------------------------------------------------------
# 1. Market Structure — BOS / CHOCH
# ---------------------------------------------------------------------------
@dataclass
class StructureBreak:
    index: int
    kind: str  # "BOS" or "CHOCH"
    direction: int  # 1 = bullish, -1 = bearish


def detect_structure(swings: List[SwingPoint]) -> List[StructureBreak]:
    breaks: List[StructureBreak] = []
    if len(swings) < 4:
        return breaks

    prev_trend = 0
    last_high: Optional[SwingPoint] = None
    last_low: Optional[SwingPoint] = None

    for sp in swings:
        if sp.kind == "high":
            if last_high is not None and sp.price > last_high.price:
                new_trend = 1
                kind = "CHOCH" if prev_trend == -1 else "BOS"
                breaks.append(StructureBreak(sp.index, kind, 1))
                if new_trend != prev_trend:
                    prev_trend = new_trend
            last_high = sp
        elif sp.kind == "low":
            if last_low is not None and sp.price < last_low.price:
                new_trend = -1
                kind = "CHOCH" if prev_trend == 1 else "BOS"
                breaks.append(StructureBreak(sp.index, kind, -1))
                if new_trend != prev_trend:
                    prev_trend = new_trend
            last_low = sp

    return breaks


def current_structure_bias(breaks: List[StructureBreak], recent: int = 3) -> int:
    if not breaks:
        return 0
    tail = breaks[-recent:]
    score = sum(b.direction for b in tail)
    if score > 0:
        return 1
    if score < 0:
        return -1
    return 0


# ---------------------------------------------------------------------------
# 2. Order Blocks
# ---------------------------------------------------------------------------
@dataclass
class OrderBlock:
    index: int
    high: float
    low: float
    direction: int  # 1 = bullish OB, -1 = bearish OB
    volume_valid: bool


def detect_order_blocks(candles: List[Candle], lookback: int = 30) -> List[OrderBlock]:
    obs: List[OrderBlock] = []
    n = len(candles)
    start = max(0, n - lookback)
    avg_vol = sum(c.tick_volume for c in candles[start:n]) / max(1, n - start)

    for i in range(start + 1, n - 1):
        impulse = candles[i + 1] if i + 1 < n else None
        if impulse is None:
            continue

        if candles[i].is_bearish and impulse.is_bullish and impulse.body > candles[i].body * 1.5:
            vol_ok = impulse.tick_volume > avg_vol * 1.1
            obs.append(OrderBlock(i, candles[i].high, candles[i].low, 1, vol_ok))

        if candles[i].is_bullish and impulse.is_bearish and impulse.body > candles[i].body * 1.5:
            vol_ok = impulse.tick_volume > avg_vol * 1.1
            obs.append(OrderBlock(i, candles[i].high, candles[i].low, -1, vol_ok))

    return [ob for ob in obs if ob.volume_valid]


def price_in_ob(price: float, ob: OrderBlock) -> bool:
    return ob.low <= price <= ob.high


# ---------------------------------------------------------------------------
# 3. Liquidity & Stop Hunt
# ---------------------------------------------------------------------------
def detect_liquidity_sweep(candles: List[Candle], lookback: int = 25) -> int:
    """Return 1 if sell-side liquidity was swept (price broke below lows then
    reversed bullish), -1 if buy-side liquidity was swept (price broke above
    highs then reversed bearish), else 0."""
    if len(candles) < lookback + 2:
        return 0

    zone = candles[-(lookback + 2) : -2]
    last = candles[-1]
    prev = candles[-2]

    high_zone = max(c.high for c in zone)
    low_zone = min(c.low for c in zone)

    if prev.high > high_zone and last.close < high_zone and last.is_bearish:
        return -1

    if prev.low < low_zone and last.close > low_zone and last.is_bullish:
        return 1

    return 0


# ---------------------------------------------------------------------------
# 4. Fair Value Gap (FVG)
# ---------------------------------------------------------------------------
@dataclass
class FVG:
    index: int
    top: float
    bottom: float
    direction: int  # 1 = bullish FVG, -1 = bearish FVG


def detect_fvg(candles: List[Candle], lookback: int = 20) -> List[FVG]:
    gaps: List[FVG] = []
    n = len(candles)
    start = max(2, n - lookback)

    for i in range(start, n):
        c0, c1, c2 = candles[i - 2], candles[i - 1], candles[i]

        if c2.low > c0.high:
            gaps.append(FVG(i - 1, c2.low, c0.high, 1))

        if c2.high < c0.low:
            gaps.append(FVG(i - 1, c0.low, c2.high, -1))

    return gaps


def price_in_fvg(price: float, fvg: FVG) -> bool:
    return fvg.bottom <= price <= fvg.top


def premium_discount(candles: List[Candle], lookback: int = 30) -> str:
    recent = candles[-lookback:]
    high = max(c.high for c in recent)
    low = min(c.low for c in recent)
    mid = (high + low) / 2.0
    last_close = candles[-1].close
    return "discount" if last_close < mid else "premium"


# ---------------------------------------------------------------------------
# 5. Multi-Timeframe: aggregate 1M -> 5M
# ---------------------------------------------------------------------------
def aggregate_to_5m(candles: List[Candle]) -> List[Candle]:
    grouped: dict[str, List[Candle]] = {}
    for c in candles:
        minute = c.time.minute
        bucket = minute - (minute % 5)
        key = c.time.strftime("%Y%m%d%H") + f"{bucket:02d}"
        grouped.setdefault(key, []).append(c)

    bars: List[Candle] = []
    for key in sorted(grouped.keys()):
        grp = grouped[key]
        bars.append(
            Candle(
                time=grp[0].time,
                open=grp[0].open,
                high=max(g.high for g in grp),
                low=min(g.low for g in grp),
                close=grp[-1].close,
                tick_volume=sum(g.tick_volume for g in grp),
                volume=sum(g.volume for g in grp),
                spread=grp[-1].spread,
            )
        )
    return bars


def htf_trend(candles_5m: List[Candle], ema_period: int = 9) -> int:
    if len(candles_5m) < ema_period + 1:
        return 0
    ema = compute_ema([c.close for c in candles_5m], ema_period)
    if ema[-1] > ema[-2] and candles_5m[-1].close > ema[-1]:
        return 1
    if ema[-1] < ema[-2] and candles_5m[-1].close < ema[-1]:
        return -1
    return 0


# ---------------------------------------------------------------------------
# 6. Candlestick patterns
# ---------------------------------------------------------------------------
def is_pin_bar(c: Candle) -> int:
    if c.range < 0.01:
        return 0
    body_pct = c.body / c.range
    if body_pct > 0.35:
        return 0
    if c.lower_shadow >= c.body * 2.0 and c.upper_shadow <= c.range * 0.30 and c.is_bullish:
        return 1
    if c.upper_shadow >= c.body * 2.0 and c.lower_shadow <= c.range * 0.30 and c.is_bearish:
        return -1
    return 0


def is_hammer(c: Candle) -> int:
    if c.range < 0.01:
        return 0
    if c.lower_shadow >= c.body * 2.0 and c.upper_shadow <= c.body * 0.5:
        return 1
    return 0


def is_shooting_star(c: Candle) -> int:
    if c.range < 0.01:
        return 0
    if c.upper_shadow >= c.body * 2.0 and c.lower_shadow <= c.body * 0.5:
        return -1
    return 0


def is_engulfing(current: Candle, previous: Candle) -> int:
    if previous.body <= 0:
        return 0
    if current.body < previous.body:
        return 0
    if (
        previous.is_bearish
        and current.is_bullish
        and current.open <= previous.close
        and current.close >= previous.open
    ):
        return 1
    if (
        previous.is_bullish
        and current.is_bearish
        and current.open >= previous.close
        and current.close <= previous.open
    ):
        return -1
    return 0


def candle_pattern_signal(candles: List[Candle]) -> int:
    if len(candles) < 2:
        return 0
    last = candles[-1]
    prev = candles[-2]

    signals: List[int] = []
    pin = is_pin_bar(last)
    if pin:
        signals.append(pin)
    eng = is_engulfing(last, prev)
    if eng:
        signals.append(eng)
    ham = is_hammer(last)
    if ham:
        signals.append(ham)
    ss = is_shooting_star(last)
    if ss:
        signals.append(ss)

    if not signals:
        return 0
    total = sum(signals)
    if total > 0:
        return 1
    if total < 0:
        return -1
    return 0


# ---------------------------------------------------------------------------
# 7. EMA & Momentum
# ---------------------------------------------------------------------------
def compute_ema(closes: List[float], period: int) -> List[float]:
    alpha = 2.0 / (period + 1)
    ema_values = [closes[0]]
    for i in range(1, len(closes)):
        ema_values.append(alpha * closes[i] + (1 - alpha) * ema_values[-1])
    return ema_values


def ema_momentum_signal(candles: List[Candle], short_period: int = 9, mid_period: int = 21) -> int:
    closes = [c.close for c in candles]
    if len(closes) < mid_period + 2:
        return 0

    short_ema = compute_ema(closes, short_period)
    mid_ema = compute_ema(closes, mid_period)

    short_rising = short_ema[-1] > short_ema[-2]
    mid_rising = mid_ema[-1] > mid_ema[-2]
    short_falling = short_ema[-1] < short_ema[-2]
    mid_falling = mid_ema[-1] < mid_ema[-2]

    last = candles[-1]
    strong_body = last.body > (last.range * 0.50) if last.range > 0 else False

    recent_vol = [c.tick_volume for c in candles[-20:]]
    avg_vol = sum(recent_vol) / len(recent_vol) if recent_vol else 1
    vol_ok = last.tick_volume > avg_vol * 0.9

    if (
        short_rising
        and mid_rising
        and last.close > short_ema[-1]
        and last.close > mid_ema[-1]
        and strong_body
        and vol_ok
        and last.is_bullish
    ):
        return 1

    if (
        short_falling
        and mid_falling
        and last.close < short_ema[-1]
        and last.close < mid_ema[-1]
        and strong_body
        and vol_ok
        and last.is_bearish
    ):
        return -1

    return 0


# ---------------------------------------------------------------------------
# 8. Session filter
# ---------------------------------------------------------------------------
LONDON_START_H = 7
LONDON_END_H = 16
NY_START_H = 12
NY_END_H = 21


def in_active_session(candle: Candle) -> bool:
    h = candle.time.hour
    return (LONDON_START_H <= h < LONDON_END_H) or (NY_START_H <= h < NY_END_H)


# ---------------------------------------------------------------------------
# 9. Risk management
# ---------------------------------------------------------------------------
def compute_sl_tp(
    direction: int,
    entry: float,
    candles: List[Candle],
    swing_lookback: int = 10,
    buffer: float = 0.50,
    rr_ratio: float = 2.0,
) -> tuple[float, float]:
    recent = candles[-swing_lookback:]
    if direction == 1:
        swing_low = min(c.low for c in recent)
        sl = round(swing_low - buffer, 2)
        risk = entry - sl
        tp = round(entry + risk * rr_ratio, 2)
    else:
        swing_high = max(c.high for c in recent)
        sl = round(swing_high + buffer, 2)
        risk = sl - entry
        tp = round(entry - risk * rr_ratio, 2)
    return sl, tp


# ---------------------------------------------------------------------------
# 10. Multi-candle confirmation
# ---------------------------------------------------------------------------
def multi_candle_confirm(candles: List[Candle], direction: int, window: int = 5) -> bool:
    if len(candles) < window:
        return False
    tail = candles[-window:]

    closes = [c.close for c in tail]
    ema_short = compute_ema(closes, min(3, len(closes)))

    if direction == 1:
        trend_ok = closes[-1] > closes[0]
        ema_ok = ema_short[-1] > ema_short[0]
        bullish_count = sum(1 for c in tail if c.is_bullish)
        return trend_ok and ema_ok and bullish_count >= (window // 2 + 1)

    if direction == -1:
        trend_ok = closes[-1] < closes[0]
        ema_ok = ema_short[-1] < ema_short[0]
        bearish_count = sum(1 for c in tail if c.is_bearish)
        return trend_ok and ema_ok and bearish_count >= (window // 2 + 1)

    return False


# ---------------------------------------------------------------------------
# Signal aggregation
# ---------------------------------------------------------------------------
NO_TRADE = {"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}


def analyze(candles_1m: List[Candle]) -> dict:
    if len(candles_1m) < 40:
        _log("Rejected: insufficient data (%d candles)", len(candles_1m))
        return NO_TRADE

    last = candles_1m[-1]

    # ---- 8. Session filter ----
    if not in_active_session(last):
        _log("Rejected: outside London/NY session (hour=%d)", last.time.hour)
        return NO_TRADE

    # ---- 5. Multi-Timeframe: 5M trend ----
    candles_5m = aggregate_to_5m(candles_1m)
    htf_bias = htf_trend(candles_5m)

    # ---- 1. Market Structure ----
    swings = detect_swings(candles_1m, left=3, right=3)
    breaks = detect_structure(swings)
    struct_bias = current_structure_bias(breaks)

    # ---- 7. EMA & Momentum on 1M ----
    ema_mom = ema_momentum_signal(candles_1m)

    # ---- 6. Candlestick patterns ----
    pattern = candle_pattern_signal(candles_1m)

    # ---- 2. Order Blocks ----
    obs = detect_order_blocks(candles_1m, lookback=30)

    # ---- 3. Liquidity sweep ----
    liq = detect_liquidity_sweep(candles_1m, lookback=25)

    # ---- 4. FVG ----
    fvgs = detect_fvg(candles_1m, lookback=20)
    pd_zone = premium_discount(candles_1m)

    _log("Layer results — HTF:%+d  Structure:%+d  EMA/Mom:%+d  Pattern:%+d  Liquidity:%+d",
         htf_bias, struct_bias, ema_mom, pattern, liq)
    _log("  OBs:%d  FVGs:%d  Premium/Discount:%s", len(obs), len(fvgs), pd_zone)

    # ---- Determine candidate direction ----
    votes = [htf_bias, struct_bias, ema_mom, pattern, liq]
    score = sum(votes)
    if score > 0:
        direction = 1
    elif score < 0:
        direction = -1
    else:
        _log("Rejected: directional vote score is zero")
        return NO_TRADE

    _log("Candidate direction: %s (vote score %+d)", "BUY" if direction == 1 else "SELL", score)

    # ---- 5b. MTF confirmation: 1M direction must match 5M ----
    if htf_bias != 0 and htf_bias != direction:
        _log("Rejected: HTF bias contradicts direction")
        return NO_TRADE

    # ---- 1b. Structure must not contradict ----
    if struct_bias != 0 and struct_bias != direction:
        _log("Rejected: market structure contradicts direction")
        return NO_TRADE

    # ---- 7b. EMA/momentum must not contradict ----
    if ema_mom != 0 and ema_mom != direction:
        _log("Rejected: EMA/momentum contradicts direction")
        return NO_TRADE

    # ---- 6b. Pattern must align with BOS + FVG + Order Block ----
    if pattern != 0 and pattern != direction:
        _log("Rejected: candle pattern contradicts direction")
        return NO_TRADE

    # ---- 2b. Check OB alignment ----
    ob_aligned = any(ob.direction == direction and price_in_ob(last.close, ob) for ob in obs)

    # ---- 4b. FVG alignment ----
    fvg_aligned = any(f.direction == direction and price_in_fvg(last.close, f) for f in fvgs)

    # Premium/discount sanity: buy in discount, sell in premium
    pd_ok = (direction == 1 and pd_zone == "discount") or (direction == -1 and pd_zone == "premium")

    # ---- 10. Multi-candle confirmation ----
    mc_ok = multi_candle_confirm(candles_1m, direction, window=5)

    # ---- Confluence scoring ----
    layers_passed = 0
    layer_details: List[str] = []
    for label, ok in [
        ("structure", struct_bias == direction),
        ("ema_momentum", ema_mom == direction),
        ("pattern", pattern == direction),
        ("htf", htf_bias == direction),
        ("order_block", ob_aligned),
        ("fvg", fvg_aligned),
        ("liquidity", liq == direction),
        ("premium_discount", pd_ok),
        ("multi_candle", mc_ok),
    ]:
        if ok:
            layers_passed += 1
            layer_details.append(label)

    _log("Confluence: %d/9 layers passed [%s]", layers_passed, ", ".join(layer_details))

    if layers_passed < 4:
        _log("Rejected: only %d layers passed (need >= 4)", layers_passed)
        return NO_TRADE

    # ---- 10b. Final multi-candle gate ----
    if not mc_ok:
        _log("Rejected: multi-candle confirmation failed")
        return NO_TRADE

    # ---- 9. Risk management ----
    entry = round(last.close, 2)
    sl, tp = compute_sl_tp(direction, entry, candles_1m)

    risk = abs(entry - sl)
    if risk < 0.10:
        _log("Rejected: risk distance too small (%.2f)", risk)
        return NO_TRADE

    signal_text = "BUY" if direction == 1 else "SELL"
    _log("SIGNAL: %s  entry=%.2f  SL=%.2f  TP=%.2f", signal_text, entry, sl, tp)
    return {"signal": signal_text, "entry": entry, "SL": sl, "TP": tp}


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def load_candles(source: str) -> List[Candle]:
    if source == "-":
        lines = sys.stdin.read().strip().splitlines()
    else:
        lines = Path(source).read_text(encoding="ascii").strip().splitlines()

    candles: List[Candle] = []
    for line in lines:
        parts = line.split(",")
        if len(parts) != 9:
            continue
        try:
            bar_time = datetime.strptime(parts[0] + " " + parts[1], "%Y.%m.%d %H:%M")
        except ValueError:
            continue
        candles.append(
            Candle(
                time=bar_time,
                open=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                close=float(parts[5]),
                tick_volume=int(parts[6]),
                volume=int(parts[7]),
                spread=int(parts[8]),
            )
        )
    return candles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global VERBOSE
    args = [a for a in sys.argv[1:] if a != "--verbose"]
    if "--verbose" in sys.argv:
        VERBOSE = True

    source = args[0] if args else str(Path(__file__).resolve().parents[1] / "data" / "XAUUSD_M1_sample.csv")
    candles = load_candles(source)
    result = analyze(candles)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
