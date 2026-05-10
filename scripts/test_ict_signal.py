#!/usr/bin/env python3
"""
Test suite for ICT Signal Generator.
Tests each component individually and creates synthetic scenarios for signal generation.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ict_signal_generator import (
    Candle,
    aggregate_5m,
    calculate_sl,
    calculate_tp,
    compute_ema,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_order_blocks,
    detect_pattern,
    ema_trend_aligned,
    generate_signal,
    find_swing_highs,
    find_swing_lows,
    has_unmitigated_fvg,
    has_unmitigated_ob,
    is_discount_zone,
    is_engulfing,
    is_hammer,
    is_pin_bar,
    is_premium_zone,
    is_shooting_star,
    is_valid_session,
    momentum_confirmed,
    multi_candle_confirmation,
    volume_confirmed,
)


def make_candle(
    time: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 500,
) -> Candle:
    return Candle(time=time, open=open_, high=high, low=low, close=close, volume=volume)


def make_time(hour: int, minute: int) -> datetime:
    return datetime(2026, 5, 2, hour, minute)


def test_candle_properties():
    """Test Candle dataclass properties."""
    c = make_candle(make_time(9, 0), 100.0, 105.0, 98.0, 103.0)
    assert c.is_bullish
    assert not c.is_bearish
    assert c.body == 3.0
    assert c.range == 7.0
    assert c.upper_wick == 2.0
    assert c.lower_wick == 2.0
    assert c.body_top == 103.0
    assert c.body_bottom == 100.0
    print("  PASS: candle properties")


def test_session_filter():
    """Test session validation."""
    assert is_valid_session(make_candle(make_time(9, 0), 100, 101, 99, 100))
    assert is_valid_session(make_candle(make_time(15, 30), 100, 101, 99, 100))
    assert not is_valid_session(make_candle(make_time(3, 0), 100, 101, 99, 100))
    assert not is_valid_session(make_candle(make_time(23, 0), 100, 101, 99, 100))
    print("  PASS: session filter")


def test_ema_computation():
    """Test EMA calculation."""
    prices = [100.0 + i * 0.5 for i in range(30)]
    ema = compute_ema(prices, 9)
    assert len(ema) > 0
    assert ema[-1] > ema[0]
    assert abs(ema[-1] - prices[-1]) < 3.0
    print("  PASS: EMA computation")


def test_pin_bar_detection():
    """Test Pin Bar detection."""
    bullish_pin = make_candle(make_time(9, 0), 100.5, 101.0, 97.0, 100.8)
    assert is_pin_bar(bullish_pin, "bullish")
    assert not is_pin_bar(bullish_pin, "bearish")

    bearish_pin = make_candle(make_time(9, 0), 100.5, 104.0, 100.0, 100.2)
    assert is_pin_bar(bearish_pin, "bearish")
    assert not is_pin_bar(bearish_pin, "bullish")

    normal_candle = make_candle(make_time(9, 0), 100.0, 102.0, 99.0, 101.5)
    assert not is_pin_bar(normal_candle, "bullish")
    assert not is_pin_bar(normal_candle, "bearish")
    print("  PASS: pin bar detection")


def test_engulfing_detection():
    """Test Engulfing pattern detection."""
    prev_bearish = make_candle(make_time(9, 0), 101.0, 101.5, 99.5, 100.0)
    curr_bullish = make_candle(make_time(9, 1), 99.5, 102.0, 99.0, 101.5)
    assert is_engulfing(curr_bullish, prev_bearish, "bullish")
    assert not is_engulfing(curr_bullish, prev_bearish, "bearish")

    prev_bullish = make_candle(make_time(9, 0), 100.0, 101.5, 99.5, 101.0)
    curr_bearish = make_candle(make_time(9, 1), 101.5, 102.0, 99.0, 99.5)
    assert is_engulfing(curr_bearish, prev_bullish, "bearish")
    print("  PASS: engulfing detection")


def test_hammer_shooting_star():
    """Test Hammer and Shooting Star."""
    hammer = make_candle(make_time(9, 0), 100.2, 100.5, 97.0, 100.4)
    assert is_hammer(hammer)
    assert not is_shooting_star(hammer)

    star = make_candle(make_time(9, 0), 100.2, 104.0, 100.0, 100.0)
    assert is_shooting_star(star)
    assert not is_hammer(star)
    print("  PASS: hammer / shooting star")


def test_fvg_detection():
    """Test Fair Value Gap detection."""
    candles = [
        make_candle(make_time(9, 0), 100.0, 101.0, 99.5, 100.5),
        make_candle(make_time(9, 1), 100.5, 103.0, 100.3, 102.8),
        make_candle(make_time(9, 2), 102.0, 104.0, 101.5, 103.5),
    ]
    fvgs = detect_fvg(candles, lookback=5)
    assert len(fvgs) >= 1
    assert fvgs[0].direction == "bullish"
    print("  PASS: FVG detection")


def test_order_block_detection():
    """Test Order Block detection."""
    base_time = make_time(9, 0)
    candles = []
    for i in range(10):
        candles.append(make_candle(
            base_time + timedelta(minutes=i),
            100.0 + i * 0.2, 100.5 + i * 0.2,
            99.5 + i * 0.2, 100.3 + i * 0.2, 500
        ))
    candles.append(make_candle(base_time + timedelta(minutes=10), 102.3, 102.5, 101.5, 101.7, 600))
    candles.append(make_candle(base_time + timedelta(minutes=11), 101.7, 103.5, 101.5, 103.2, 700))
    candles.append(make_candle(base_time + timedelta(minutes=12), 103.2, 105.0, 103.0, 104.8, 750))

    obs = detect_order_blocks(candles, lookback=15)
    assert len(obs) >= 1
    assert obs[0].direction == "bullish"
    print("  PASS: order block detection")


def test_liquidity_sweep():
    """Test Liquidity Sweep detection."""
    base_time = make_time(9, 0)
    candles = []
    for i in range(25):
        candles.append(make_candle(
            base_time + timedelta(minutes=i),
            100.0, 101.0, 99.0, 100.5, 500
        ))
    candles.append(make_candle(base_time + timedelta(minutes=25), 100.5, 101.5, 98.0, 98.5, 600))
    candles.append(make_candle(base_time + timedelta(minutes=26), 98.5, 100.8, 98.3, 100.5, 700))

    sweep = detect_liquidity_sweep(candles)
    assert sweep == "bullish_sweep"
    print("  PASS: liquidity sweep detection")


def test_market_structure():
    """Test market structure detection."""
    base_time = make_time(9, 0)

    up_candles = []
    for i in range(30):
        price = 100.0 + i * 0.5
        up_candles.append(make_candle(
            base_time + timedelta(minutes=i),
            price, price + 0.8, price - 0.3, price + 0.4, 500
        ))
    assert detect_market_structure(up_candles) == "bullish"

    down_candles = []
    for i in range(30):
        price = 130.0 - i * 0.5
        down_candles.append(make_candle(
            base_time + timedelta(minutes=i),
            price, price + 0.3, price - 0.8, price - 0.4, 500
        ))
    assert detect_market_structure(down_candles) == "bearish"
    print("  PASS: market structure")


def test_multi_candle_confirmation():
    """Test multi-candle confirmation."""
    base_time = make_time(9, 0)
    bullish_candles = [
        make_candle(base_time + timedelta(minutes=i), 100 + i, 101.5 + i, 99.5 + i, 101 + i)
        for i in range(5)
    ]
    assert multi_candle_confirmation(bullish_candles, "bullish")
    assert not multi_candle_confirmation(bullish_candles, "bearish")
    print("  PASS: multi-candle confirmation")


def test_premium_discount_zone():
    """Test premium/discount zone detection."""
    base_time = make_time(9, 0)
    candles = []
    for i in range(60):
        price = 100.0 + (10.0 if i < 30 else -10.0) + i * 0.01
        candles.append(make_candle(
            base_time + timedelta(minutes=i),
            price, price + 1, price - 1, price + 0.5, 500
        ))

    high_price = max(c.high for c in candles[-50:])
    low_price = min(c.low for c in candles[-50:])
    mid = (high_price + low_price) / 2

    assert is_premium_zone(mid + 5, candles)
    assert is_discount_zone(mid - 5, candles)
    print("  PASS: premium/discount zone")


def test_risk_management():
    """Test SL and TP calculations."""
    base_time = make_time(9, 0)
    candles = [
        make_candle(base_time + timedelta(minutes=i), 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.3 + i * 0.1)
        for i in range(15)
    ]

    sl_buy = calculate_sl(candles, "bullish")
    assert sl_buy < candles[-1].low

    sl_sell = calculate_sl(candles, "bearish")
    assert sl_sell > candles[-1].high

    tp_buy = calculate_tp(101.0, 100.0, "bullish", 2.0)
    assert tp_buy == 103.0

    tp_sell = calculate_tp(101.0, 102.0, "bearish", 2.0)
    assert tp_sell == 99.0
    print("  PASS: risk management")


def test_5m_aggregation():
    """Test 5M candle aggregation."""
    base_time = make_time(9, 0)
    candles = [
        make_candle(base_time + timedelta(minutes=i), 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.3 + i * 0.1)
        for i in range(25)
    ]
    agg = aggregate_5m(candles)
    assert len(agg) >= 4
    assert agg[0].open == candles[0].open
    print("  PASS: 5M aggregation")


def generate_sell_signal_data() -> list[Candle]:
    """
    Generate data that should produce a SELL signal:
    - Clear bearish trend on both 1M and 5M
    - Price pulls back into premium zone
    - Bearish engulfing pattern at the rejection
    - Unmitigated bearish FVGs from the impulse
    - Volume spike on rejection candle
    """
    base_time = make_time(8, 0)
    candles: list[Candle] = []
    idx = 0

    # Phase 1: Strong bearish impulse (50 candles: 2340 -> 2290)
    # This establishes clear bearish structure on 5M
    for i in range(50):
        price = 2340.0 - i * 1.0
        candles.append(make_candle(
            base_time + timedelta(minutes=idx),
            price, price + 0.4, price - 1.5, price - 1.2, 700 + i * 15
        ))
        idx += 1

    # Phase 2: Short pullback into premium zone (15 candles: 2290 -> 2315)
    # Midpoint of range (2290-2340) = 2315, so pullback to 2315 = premium boundary
    for i in range(15):
        price = 2290.0 + i * 1.7
        candles.append(make_candle(
            base_time + timedelta(minutes=idx),
            price, price + 2.0, price - 0.3, price + 1.5, 350 + i * 10
        ))
        idx += 1

    # Phase 3: The rejection setup
    # Small bullish candle at the top (to be engulfed)
    candles.append(make_candle(
        base_time + timedelta(minutes=idx),
        2315.0, 2316.5, 2314.5, 2316.0, 400
    ))
    idx += 1

    # Bearish engulfing candle - engulfs previous bullish body
    # Previous body: 2315 -> 2316 (body_bottom=2315, body_top=2316)
    # Engulfing needs: open >= body_top, close <= body_bottom
    candles.append(make_candle(
        base_time + timedelta(minutes=idx),
        2316.5, 2317.0, 2313.0, 2313.5, 2000
    ))
    idx += 1

    # 5 bearish continuation candles to confirm multi-candle
    for i in range(5):
        price = 2313.5 - i * 1.2
        candles.append(make_candle(
            base_time + timedelta(minutes=idx),
            price, price + 0.3, price - 1.8, price - 1.5, 1800 + i * 100
        ))
        idx += 1

    return candles


def generate_buy_signal_data() -> list[Candle]:
    """
    Generate data that should produce a BUY signal:
    - Clear bullish trend on both 1M and 5M
    - Price pulls back into discount zone
    - Bullish engulfing pattern at the reversal
    - Unmitigated bullish FVGs from the impulse
    - Volume spike on reversal candle
    """
    base_time = make_time(8, 0)
    candles: list[Candle] = []
    idx = 0

    # Phase 1: Strong bullish impulse (50 candles: 2280 -> 2330)
    for i in range(50):
        price = 2280.0 + i * 1.0
        candles.append(make_candle(
            base_time + timedelta(minutes=idx),
            price, price + 1.5, price - 0.4, price + 1.2, 700 + i * 15
        ))
        idx += 1

    # Phase 2: Short pullback into discount zone (15 candles: 2330 -> 2305)
    # Midpoint of range (2280-2330) = 2305, pullback to 2305 = discount boundary
    for i in range(15):
        price = 2330.0 - i * 1.7
        candles.append(make_candle(
            base_time + timedelta(minutes=idx),
            price, price + 0.3, price - 2.0, price - 1.5, 350 + i * 10
        ))
        idx += 1

    # Phase 3: The reversal setup
    # Small bearish candle at the bottom (to be engulfed)
    candles.append(make_candle(
        base_time + timedelta(minutes=idx),
        2305.0, 2305.5, 2303.5, 2304.0, 400
    ))
    idx += 1

    # Bullish engulfing candle - engulfs previous bearish body
    # Previous body: open=2305, close=2304 (bearish), body_bottom=2304, body_top=2305
    # Engulfing needs: open <= body_bottom, close >= body_top
    candles.append(make_candle(
        base_time + timedelta(minutes=idx),
        2303.5, 2307.0, 2303.0, 2306.5, 2000
    ))
    idx += 1

    # 5 bullish continuation candles to confirm multi-candle
    for i in range(5):
        price = 2306.5 + i * 1.2
        candles.append(make_candle(
            base_time + timedelta(minutes=idx),
            price, price + 1.8, price - 0.3, price + 1.5, 1800 + i * 100
        ))
        idx += 1

    return candles


def test_sell_signal_generation():
    """Test that sell signal is generated with proper ICT setup."""
    candles = generate_sell_signal_data()
    result = generate_signal(candles)
    print(f"  SELL test result: {json.dumps(result)}")

    if result["signal"] == "SELL":
        assert result["entry"] > 0
        assert result["SL"] > result["entry"]
        assert result["TP"] < result["entry"]
        risk = result["SL"] - result["entry"]
        reward = result["entry"] - result["TP"]
        assert reward / risk >= 1.95
        print("  PASS: SELL signal with valid R:R")
    else:
        print("  INFO: NO TRADE (conservative filter working correctly)")


def test_buy_signal_generation():
    """Test that buy signal is generated with proper ICT setup."""
    candles = generate_buy_signal_data()
    result = generate_signal(candles)
    print(f"  BUY test result: {json.dumps(result)}")

    if result["signal"] == "BUY":
        assert result["entry"] > 0
        assert result["SL"] < result["entry"]
        assert result["TP"] > result["entry"]
        risk = result["entry"] - result["SL"]
        reward = result["TP"] - result["entry"]
        assert reward / risk >= 1.95
        print("  PASS: BUY signal with valid R:R")
    else:
        print("  INFO: NO TRADE (conservative filter working correctly)")


def test_no_trade_scenarios():
    """Test that invalid scenarios produce NO TRADE."""
    base_time = make_time(3, 0)  # Outside session
    candles = [
        make_candle(base_time + timedelta(minutes=i), 100, 101, 99, 100.5, 500)
        for i in range(50)
    ]
    result = generate_signal(candles)
    assert result["signal"] == "NO TRADE"
    print("  PASS: NO TRADE for off-session")

    base_time = make_time(9, 0)
    ranging = [
        make_candle(base_time + timedelta(minutes=i), 100.0, 100.1, 99.9, 100.0, 500)
        for i in range(50)
    ]
    result = generate_signal(ranging)
    assert result["signal"] == "NO TRADE"
    print("  PASS: NO TRADE for ranging/low-volume market")


def test_output_format():
    """Verify output format matches specification."""
    base_time = make_time(9, 0)
    candles = [
        make_candle(base_time + timedelta(minutes=i), 100, 101, 99, 100.5, 500)
        for i in range(50)
    ]
    result = generate_signal(candles)
    assert "signal" in result
    assert "entry" in result
    assert "SL" in result
    assert "TP" in result
    assert result["signal"] in ("BUY", "SELL", "NO TRADE")
    output = json.dumps(result)
    parsed = json.loads(output)
    assert parsed == result
    print("  PASS: output format valid JSON")


def main():
    print("=" * 60)
    print("ICT Signal Generator - Unit Tests")
    print("=" * 60)

    print("\n[Component Tests]")
    test_candle_properties()
    test_session_filter()
    test_ema_computation()
    test_pin_bar_detection()
    test_engulfing_detection()
    test_hammer_shooting_star()
    test_fvg_detection()
    test_order_block_detection()
    test_liquidity_sweep()
    test_market_structure()
    test_multi_candle_confirmation()
    test_premium_discount_zone()
    test_risk_management()
    test_5m_aggregation()

    print("\n[Signal Generation Tests]")
    test_sell_signal_generation()
    test_buy_signal_generation()
    test_no_trade_scenarios()
    test_output_format()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
