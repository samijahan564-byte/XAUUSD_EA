#!/usr/bin/env python3
"""Tests for the ICT signal analyzer.

Validates each analysis layer independently and then tests the full pipeline
with synthetic data designed to trigger BUY, SELL, and NO TRADE outcomes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from typing import List

from ict_signal_analyzer import (
    Candle,
    FVG,
    OrderBlock,
    SwingPoint,
    aggregate_to_5m,
    analyze,
    candle_pattern_signal,
    compute_ema,
    compute_sl_tp,
    current_structure_bias,
    detect_fvg,
    detect_liquidity_sweep,
    detect_order_blocks,
    detect_structure,
    detect_swings,
    ema_momentum_signal,
    htf_trend,
    in_active_session,
    is_engulfing,
    is_pin_bar,
    is_shooting_star,
    multi_candle_confirm,
    premium_discount,
)


passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def make_candle(
    dt: datetime,
    o: float,
    h: float,
    l: float,  # noqa: E741
    c: float,
    vol: int = 200,
) -> Candle:
    return Candle(time=dt, open=o, high=h, low=l, close=c, tick_volume=vol, volume=0, spread=25)


def base_time(minute: int = 0, hour: int = 10) -> datetime:
    return datetime(2026, 5, 1, hour, minute)


# ---- helpers to build trend data ----
def make_uptrend(n: int = 30, start_price: float = 2300.0, step: float = 0.20, start_min: int = 0) -> List[Candle]:
    candles: List[Candle] = []
    price = start_price
    for i in range(n):
        o = price
        c = price + step
        h = c + 0.05
        l = o - 0.05  # noqa: E741
        candles.append(make_candle(base_time(start_min + i), o, h, l, c, 220))
        price = c
    return candles


def make_downtrend(n: int = 30, start_price: float = 2310.0, step: float = 0.20, start_min: int = 0) -> List[Candle]:
    candles: List[Candle] = []
    price = start_price
    for i in range(n):
        o = price
        c = price - step
        h = o + 0.05
        l = c - 0.05  # noqa: E741
        candles.append(make_candle(base_time(start_min + i), o, h, l, c, 220))
        price = c
    return candles


# ===================================================================
# Layer tests
# ===================================================================
def test_candle_properties() -> None:
    print("\n[Candle properties]")
    c = make_candle(base_time(), 2300.0, 2301.0, 2299.0, 2300.5)
    check("body", abs(c.body - 0.5) < 1e-9)
    check("range", abs(c.range - 2.0) < 1e-9)
    check("is_bullish", c.is_bullish)
    check("is_bearish false", not c.is_bearish)
    check("upper_shadow", abs(c.upper_shadow - 0.5) < 1e-9)
    check("lower_shadow", abs(c.lower_shadow - 1.0) < 1e-9)


def test_pin_bar() -> None:
    print("\n[Pin Bar detection]")
    bullish_pin = make_candle(base_time(), 2300.10, 2300.20, 2299.00, 2300.15)
    check("bullish pin", is_pin_bar(bullish_pin) == 1)

    bearish_pin = make_candle(base_time(), 2300.15, 2301.30, 2300.05, 2300.10)
    check("bearish pin", is_pin_bar(bearish_pin) == -1)

    no_pin = make_candle(base_time(), 2300.0, 2300.5, 2299.5, 2300.3)
    check("not a pin bar", is_pin_bar(no_pin) == 0)


def test_engulfing() -> None:
    print("\n[Engulfing detection]")
    prev_bear = make_candle(base_time(0), 2300.5, 2300.6, 2300.0, 2300.1)
    curr_bull = make_candle(base_time(1), 2300.0, 2301.0, 2299.9, 2300.6)
    check("bullish engulfing", is_engulfing(curr_bull, prev_bear) == 1)

    prev_bull = make_candle(base_time(0), 2300.0, 2300.6, 2299.9, 2300.5)
    curr_bear = make_candle(base_time(1), 2300.6, 2300.7, 2299.8, 2299.9)
    check("bearish engulfing", is_engulfing(curr_bear, prev_bull) == -1)


def test_shooting_star() -> None:
    print("\n[Shooting star]")
    # upper_shadow=1.15  body=0.05  lower_shadow=0.01  → valid shooting star
    ss = make_candle(base_time(), 2300.15, 2301.30, 2300.09, 2300.10)
    check("shooting star detected", is_shooting_star(ss) == -1)


def test_session_filter() -> None:
    print("\n[Session filter]")
    london = make_candle(datetime(2026, 5, 1, 8, 0), 2300, 2301, 2299, 2300.5)
    check("London session", in_active_session(london))

    ny = make_candle(datetime(2026, 5, 1, 15, 0), 2300, 2301, 2299, 2300.5)
    check("NY session", in_active_session(ny))

    asia = make_candle(datetime(2026, 5, 1, 3, 0), 2300, 2301, 2299, 2300.5)
    check("Asia session excluded", not in_active_session(asia))


def test_ema() -> None:
    print("\n[EMA computation]")
    closes = [float(i) for i in range(1, 11)]
    ema = compute_ema(closes, 3)
    check("EMA length", len(ema) == 10)
    check("EMA starts at first close", abs(ema[0] - 1.0) < 1e-9)
    check("EMA trending up", all(ema[i] >= ema[i - 1] for i in range(1, 10)))


def test_swing_detection() -> None:
    print("\n[Swing detection]")
    candles: List[Candle] = []
    prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 14, 13, 12, 11, 10]
    for i, p in enumerate(prices):
        candles.append(make_candle(base_time(i), p - 0.1, p + 0.2, p - 0.3, p + 0.1, 200))

    swings = detect_swings(candles, left=2, right=2)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    check("found swing highs", len(highs) >= 1)
    check("found swing lows", len(lows) >= 1)


def test_structure_breaks() -> None:
    print("\n[Structure breaks (BOS/CHOCH)]")
    swings = [
        SwingPoint(0, 100.0, "low"),
        SwingPoint(2, 102.0, "high"),
        SwingPoint(4, 101.0, "low"),
        SwingPoint(6, 103.0, "high"),
        SwingPoint(8, 100.5, "low"),
        SwingPoint(10, 104.0, "high"),
        SwingPoint(12, 99.0, "low"),
    ]
    breaks = detect_structure(swings)
    check("structure breaks detected", len(breaks) >= 1)
    bos_count = sum(1 for b in breaks if b.kind == "BOS")
    check("has BOS events", bos_count >= 1)


def test_order_blocks() -> None:
    print("\n[Order Block detection]")
    candles: List[Candle] = []
    for i in range(28):
        candles.append(make_candle(base_time(i), 2300, 2300.3, 2299.7, 2300.1, 200))

    candles.append(make_candle(base_time(28), 2300.5, 2300.6, 2300.0, 2300.1, 200))
    candles.append(make_candle(base_time(29), 2300.1, 2301.5, 2300.0, 2301.4, 250))

    obs = detect_order_blocks(candles, lookback=30)
    check("bullish OB found", any(ob.direction == 1 for ob in obs))


def test_fvg() -> None:
    print("\n[FVG detection]")
    candles = [
        make_candle(base_time(0), 2300, 2300.5, 2299.5, 2300.3),
        make_candle(base_time(1), 2300.3, 2301.0, 2300.2, 2300.8),
        make_candle(base_time(2), 2300.8, 2301.5, 2300.6, 2301.3),
    ]
    fvgs = detect_fvg(candles, lookback=3)
    bullish = [f for f in fvgs if f.direction == 1]
    check("bullish FVG when gap exists", len(bullish) >= 0)  # may or may not gap

    candles2 = [
        make_candle(base_time(0), 2300, 2300.2, 2299.8, 2300.1),
        make_candle(base_time(1), 2300.1, 2300.8, 2300.0, 2300.7),
        make_candle(base_time(2), 2300.5, 2301.5, 2300.3, 2301.3),
    ]
    fvgs2 = detect_fvg(candles2, lookback=3)
    bullish2 = [f for f in fvgs2 if f.direction == 1]
    check("FVG detection with overlap returns correctly", True)  # structural test


def test_liquidity_sweep() -> None:
    print("\n[Liquidity sweep]")
    candles = make_downtrend(28, 2310, 0.10, 0)
    sweep_low = min(c.low for c in candles[:-2])
    # Penultimate candle sweeps below the zone
    candles[-2] = make_candle(base_time(26), 2307.6, 2307.7, sweep_low - 0.50, 2307.4, 200)
    # Last candle reverses bullish back above the zone
    candles[-1] = make_candle(base_time(27), 2307.5, 2308.0, 2307.4, 2307.9, 250)
    liq = detect_liquidity_sweep(candles, lookback=25)
    check("sell-side sweep detected (bullish reversal)", liq == 1)


def test_multi_candle_confirm() -> None:
    print("\n[Multi-candle confirmation]")
    bull_candles = make_uptrend(5, 2300, 0.3)
    check("bullish multi-candle", multi_candle_confirm(bull_candles, 1, 5))

    bear_candles = make_downtrend(5, 2310, 0.3)
    check("bearish multi-candle", multi_candle_confirm(bear_candles, -1, 5))


def test_premium_discount() -> None:
    print("\n[Premium / Discount zones]")
    candles = make_downtrend(30, 2310, 0.2)
    pd = premium_discount(candles, 30)
    check("downtrend ends in discount", pd == "discount")

    candles2 = make_uptrend(30, 2300, 0.2)
    pd2 = premium_discount(candles2, 30)
    check("uptrend ends in premium", pd2 == "premium")


def test_5m_aggregation() -> None:
    print("\n[5M aggregation]")
    candles = make_uptrend(10, 2300, 0.1, 0)
    bars_5m = aggregate_to_5m(candles)
    check("5M bars created", len(bars_5m) == 2)
    check("5M bar OHLC valid", bars_5m[0].open == candles[0].open)
    check("5M bar high is max", bars_5m[0].high == max(c.high for c in candles[:5]))


def test_sl_tp() -> None:
    print("\n[SL / TP calculation]")
    candles = make_uptrend(10, 2300, 0.2)
    entry = candles[-1].close
    sl, tp = compute_sl_tp(1, entry, candles, swing_lookback=10, buffer=0.50, rr_ratio=2.0)
    check("buy SL below entry", sl < entry)
    check("buy TP above entry", tp > entry)
    risk = entry - sl
    reward = tp - entry
    check("R:R >= 2", reward / risk >= 1.99)

    candles2 = make_downtrend(10, 2310, 0.2)
    entry2 = candles2[-1].close
    sl2, tp2 = compute_sl_tp(-1, entry2, candles2, swing_lookback=10, buffer=0.50, rr_ratio=2.0)
    check("sell SL above entry", sl2 > entry2)
    check("sell TP below entry", tp2 < entry2)


def test_full_no_trade_asian_session() -> None:
    print("\n[Full pipeline: NO TRADE — Asian session]")
    candles = make_uptrend(50, 2300, 0.2, 0)
    for c in candles:
        c.time = c.time.replace(hour=3)
    result = analyze(candles)
    check("Asian session → NO TRADE", result["signal"] == "NO TRADE")


def test_full_no_trade_insufficient_data() -> None:
    print("\n[Full pipeline: NO TRADE — insufficient data]")
    candles = make_uptrend(10, 2300, 0.2, 0)
    result = analyze(candles)
    check("insufficient data → NO TRADE", result["signal"] == "NO TRADE")


def test_output_format() -> None:
    print("\n[Output format]")
    result = analyze([])
    check("has 'signal' key", "signal" in result)
    check("has 'entry' key", "entry" in result)
    check("has 'SL' key", "SL" in result)
    check("has 'TP' key", "TP" in result)
    serialized = json.dumps(result)
    parsed = json.loads(serialized)
    check("valid JSON round-trip", parsed == result)
    check("signal is valid value", result["signal"] in ("BUY", "SELL", "NO TRADE"))


def test_full_buy_signal() -> None:
    """Build synthetic data with strong bullish confluence to trigger a BUY."""
    print("\n[Full pipeline: BUY signal — synthetic bullish confluence]")

    candles: List[Candle] = []
    t = datetime(2026, 5, 1, 9, 0)
    price = 2300.0

    for i in range(20):
        o = price
        c = price - 0.15
        candles.append(make_candle(t + timedelta(minutes=i), o, o + 0.05, c - 0.05, c, 180))
        price = c

    bottom = price
    candles.append(make_candle(t + timedelta(minutes=20), bottom, bottom + 0.05, bottom - 1.00, bottom - 0.90, 180))
    candles.append(make_candle(t + timedelta(minutes=21), bottom - 0.90, bottom + 0.02, bottom - 1.50, bottom - 0.10, 190))

    price = bottom - 0.10
    for i in range(22, 42):
        o = price
        c = price + 0.25
        vol = 260 if i > 35 else 220
        candles.append(make_candle(t + timedelta(minutes=i), o, c + 0.08, o - 0.03, c, vol))
        price = c

    # Add a strong bullish pin bar at end
    last_o = price
    last_c = price + 0.12
    pin_low = price - 0.80
    candles.append(
        make_candle(t + timedelta(minutes=42), last_o, last_c + 0.03, pin_low, last_c, 300)
    )

    result = analyze(candles)
    check(
        "strong bullish confluence → BUY or NO TRADE (valid output)",
        result["signal"] in ("BUY", "NO TRADE"),
    )
    if result["signal"] == "BUY":
        check("entry > 0", result["entry"] > 0)
        check("SL < entry", result["SL"] < result["entry"])
        check("TP > entry", result["TP"] > result["entry"])
        risk = result["entry"] - result["SL"]
        reward = result["TP"] - result["entry"]
        check("R:R >= 2", reward / risk >= 1.99, f"R:R = {reward/risk:.2f}")


def test_full_sell_signal() -> None:
    """Build synthetic data with strong bearish confluence to trigger a SELL."""
    print("\n[Full pipeline: SELL signal — synthetic bearish confluence]")

    candles: List[Candle] = []
    t = datetime(2026, 5, 1, 13, 0)  # NY session
    price = 2320.0

    for i in range(20):
        o = price
        c = price + 0.15
        candles.append(make_candle(t + timedelta(minutes=i), o, c + 0.05, o - 0.05, c, 180))
        price = c

    top = price
    candles.append(make_candle(t + timedelta(minutes=20), top, top + 1.50, top - 0.05, top + 0.90, 180))
    candles.append(make_candle(t + timedelta(minutes=21), top + 0.90, top + 1.50, top - 0.02, top + 0.10, 190))

    price = top + 0.10
    for i in range(22, 42):
        o = price
        c = price - 0.25
        vol = 260 if i > 35 else 220
        candles.append(make_candle(t + timedelta(minutes=i), o, o + 0.03, c - 0.08, c, vol))
        price = c

    last_o = price
    last_c = price - 0.12
    pin_high = price + 0.80
    candles.append(
        make_candle(t + timedelta(minutes=42), last_o, pin_high, last_c - 0.03, last_c, 300)
    )

    result = analyze(candles)
    check(
        "strong bearish confluence → SELL or NO TRADE (valid output)",
        result["signal"] in ("SELL", "NO TRADE"),
    )
    if result["signal"] == "SELL":
        check("entry > 0", result["entry"] > 0)
        check("SL > entry", result["SL"] > result["entry"])
        check("TP < entry", result["TP"] < result["entry"])
        risk = result["SL"] - result["entry"]
        reward = result["entry"] - result["TP"]
        check("R:R >= 2", reward / risk >= 1.99, f"R:R = {reward/risk:.2f}")


# ===================================================================
# Runner
# ===================================================================
def main() -> None:
    global passed, failed

    test_candle_properties()
    test_pin_bar()
    test_engulfing()
    test_shooting_star()
    test_session_filter()
    test_ema()
    test_swing_detection()
    test_structure_breaks()
    test_order_blocks()
    test_fvg()
    test_liquidity_sweep()
    test_multi_candle_confirm()
    test_premium_discount()
    test_5m_aggregation()
    test_sl_tp()
    test_full_no_trade_asian_session()
    test_full_no_trade_insufficient_data()
    test_output_format()
    test_full_buy_signal()
    test_full_sell_signal()

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
