#!/usr/bin/env python3
"""
ICT / Smart Money XAUUSD Signal Generator.

Analyzes OHLCV candle data using ICT concepts:
  - Market Structure (BOS / CHOCH)
  - Order Blocks
  - Liquidity Sweeps
  - Fair Value Gaps (FVG)
  - Multi-Timeframe Confirmation (5M derived from 1M)
  - Candlestick Patterns (Pin Bar, Engulfing, Hammer, Shooting Star)
  - EMA & Momentum filters
  - Session Filter (London / NY)
  - Risk Management (SL behind swing, TP at 1:2 R:R minimum)
  - Multi-Candle Confirmation

Reads M1 OHLCV data from CSV and outputs a single JSON signal.

Usage:
    python scripts/ict_signal_generator.py [path_to_csv]

Default CSV: data/XAUUSD_M1_sample.csv
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_candles(csv_path: Path) -> List[Candle]:
    candles: List[Candle] = []
    for line in csv_path.read_text(encoding="ascii").splitlines():
        parts = line.strip().split(",")
        if len(parts) < 7:
            continue
        bar_time = datetime.strptime(parts[0] + " " + parts[1], "%Y.%m.%d %H:%M")
        candles.append(Candle(
            time=bar_time,
            open=float(parts[2]),
            high=float(parts[3]),
            low=float(parts[4]),
            close=float(parts[5]),
            volume=int(parts[6]),
        ))
    return candles


def aggregate_5m(candles_1m: List[Candle]) -> List[Candle]:
    """Aggregate 1M candles into 5M candles for multi-timeframe analysis."""
    result: List[Candle] = []
    bucket: List[Candle] = []
    for c in candles_1m:
        minute = c.time.minute
        if bucket and minute % 5 == 0:
            agg = Candle(
                time=bucket[0].time,
                open=bucket[0].open,
                high=max(b.high for b in bucket),
                low=min(b.low for b in bucket),
                close=bucket[-1].close,
                volume=sum(b.volume for b in bucket),
            )
            result.append(agg)
            bucket = [c]
        else:
            bucket.append(c)
    if bucket:
        agg = Candle(
            time=bucket[0].time,
            open=bucket[0].open,
            high=max(b.high for b in bucket),
            low=min(b.low for b in bucket),
            close=bucket[-1].close,
            volume=sum(b.volume for b in bucket),
        )
        result.append(agg)
    return result


# ---------------------------------------------------------------------------
# Rule 1: Market Structure (BOS / CHOCH)
# ---------------------------------------------------------------------------

def find_swing_highs(candles: List[Candle], lookback: int = 5) -> List[tuple]:
    """Return list of (index, price) for swing highs."""
    swings = []
    for i in range(lookback, len(candles) - lookback):
        high = candles[i].high
        if all(candles[j].high <= high for j in range(i - lookback, i)) and \
           all(candles[j].high <= high for j in range(i + 1, i + lookback + 1)):
            swings.append((i, high))
    return swings


def find_swing_lows(candles: List[Candle], lookback: int = 5) -> List[tuple]:
    """Return list of (index, price) for swing lows."""
    swings = []
    for i in range(lookback, len(candles) - lookback):
        low = candles[i].low
        if all(candles[j].low >= low for j in range(i - lookback, i)) and \
           all(candles[j].low >= low for j in range(i + 1, i + lookback + 1)):
            swings.append((i, low))
    return swings


def detect_market_structure(candles: List[Candle], lookback: int = 3) -> str:
    """
    Detect BOS/CHOCH and return trend direction.
    Returns 'bullish', 'bearish', or 'neutral'.
    Uses swing structure when available, falls back to candle direction analysis.
    """
    swing_highs = find_swing_highs(candles, lookback)
    swing_lows = find_swing_lows(candles, lookback)

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_high = swing_highs[-1][1]
        prev_high = swing_highs[-2][1]
        last_low = swing_lows[-1][1]
        prev_low = swing_lows[-2][1]

        higher_highs = last_high > prev_high
        higher_lows = last_low > prev_low
        lower_highs = last_high < prev_high
        lower_lows = last_low < prev_low

        current_price = candles[-1].close

        if current_price > last_high and higher_highs and higher_lows:
            return "bullish"
        if current_price < last_low and lower_highs and lower_lows:
            return "bearish"

        if higher_highs and higher_lows:
            return "bullish"
        if lower_highs and lower_lows:
            return "bearish"

        # CHOCH detection
        if current_price > last_high and lower_lows:
            return "bullish"
        if current_price < last_low and higher_highs:
            return "bearish"

    # Fallback: use last 5 candles direction and price progression
    last_candles = candles[-5:]
    bullish_count = sum(1 for c in last_candles if c.is_bullish)
    bearish_count = sum(1 for c in last_candles if c.is_bearish)

    closes_rising = all(
        last_candles[i].close >= last_candles[i - 1].close
        for i in range(1, len(last_candles))
    )
    closes_falling = all(
        last_candles[i].close <= last_candles[i - 1].close
        for i in range(1, len(last_candles))
    )

    if bullish_count >= 4 or closes_rising:
        return "bullish"
    if bearish_count >= 4 or closes_falling:
        return "bearish"

    # Check broader price direction over last 10 candles
    if len(candles) >= 10:
        start_price = candles[-10].close
        end_price = candles[-1].close
        move = end_price - start_price
        avg_range = sum(c.range for c in candles[-10:]) / 10
        if move > avg_range * 2:
            return "bullish"
        if move < -avg_range * 2:
            return "bearish"

    return "neutral"


# ---------------------------------------------------------------------------
# Rule 2: Order Blocks
# ---------------------------------------------------------------------------

@dataclass
class OrderBlock:
    index: int
    high: float
    low: float
    direction: str  # 'bullish' or 'bearish'
    volume: int


def detect_order_blocks(candles: List[Candle], lookback: int = 20) -> List[OrderBlock]:
    """
    Detect valid Order Blocks: last opposing candle before an impulse move,
    confirmed by increased volume.
    """
    obs: List[OrderBlock] = []
    avg_vol = sum(c.volume for c in candles[-lookback:]) / max(lookback, 1)
    start = max(0, len(candles) - lookback)

    for i in range(start + 1, len(candles) - 1):
        prev = candles[i - 1]
        current = candles[i]
        next_c = candles[i + 1]

        if prev.is_bearish and current.is_bullish and next_c.is_bullish:
            impulse_body = current.body + next_c.body
            if impulse_body > avg_vol * 0.001 and current.volume > avg_vol * 0.8:
                obs.append(OrderBlock(
                    index=i - 1,
                    high=prev.high,
                    low=prev.low,
                    direction="bullish",
                    volume=prev.volume,
                ))

        if prev.is_bullish and current.is_bearish and next_c.is_bearish:
            impulse_body = current.body + next_c.body
            if impulse_body > avg_vol * 0.001 and current.volume > avg_vol * 0.8:
                obs.append(OrderBlock(
                    index=i - 1,
                    high=prev.high,
                    low=prev.low,
                    direction="bearish",
                    volume=prev.volume,
                ))

    return obs


def price_in_order_block(price: float, obs: List[OrderBlock], direction: str, tolerance: float = 2.0) -> Optional[OrderBlock]:
    """Check if price is within or near a valid order block zone."""
    for ob in reversed(obs):
        if ob.direction == direction:
            if ob.low - tolerance <= price <= ob.high + tolerance:
                return ob
    return None


def has_unmitigated_ob(price: float, obs: List[OrderBlock], direction: str) -> bool:
    """Check if there are unmitigated order blocks supporting the trade direction."""
    for ob in obs:
        if ob.direction == direction:
            if direction == "bullish" and ob.high < price:
                return True
            if direction == "bearish" and ob.low > price:
                return True
    return False


# ---------------------------------------------------------------------------
# Rule 3: Liquidity & Stop Hunt
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(candles: List[Candle], lookback: int = 20) -> Optional[str]:
    """
    Detect if price has swept liquidity above highs or below lows
    and reversed. Returns 'bullish_sweep', 'bearish_sweep', or None.
    """
    if len(candles) < lookback + 2:
        return None

    recent = candles[-(lookback + 2):-2]
    last = candles[-1]
    prev = candles[-2]

    recent_high = max(c.high for c in recent)
    recent_low = min(c.low for c in recent)

    if prev.high > recent_high and last.close < prev.high and last.is_bearish:
        return "bearish_sweep"

    if prev.low < recent_low and last.close > prev.low and last.is_bullish:
        return "bullish_sweep"

    return None


# ---------------------------------------------------------------------------
# Rule 4: Fair Value Gap (FVG)
# ---------------------------------------------------------------------------

@dataclass
class FVG:
    index: int
    high: float
    low: float
    direction: str  # 'bullish' or 'bearish'


def detect_fvg(candles: List[Candle], lookback: int = 20) -> List[FVG]:
    """Detect Fair Value Gaps in recent candles."""
    fvgs: List[FVG] = []
    start = max(0, len(candles) - lookback)

    for i in range(start + 2, len(candles)):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        if c2.is_bullish and c3.low > c1.high:
            fvgs.append(FVG(index=i - 1, high=c3.low, low=c1.high, direction="bullish"))

        if c2.is_bearish and c3.high < c1.low:
            fvgs.append(FVG(index=i - 1, high=c1.low, low=c3.high, direction="bearish"))

    return fvgs


def price_in_fvg(price: float, fvgs: List[FVG], direction: str, tolerance: float = 2.0) -> Optional[FVG]:
    """Check if current price is within or near a FVG zone."""
    for fvg in reversed(fvgs):
        if fvg.direction == direction:
            if fvg.low - tolerance <= price <= fvg.high + tolerance:
                return fvg
    return None


def has_unmitigated_fvg(price: float, fvgs: List[FVG], direction: str) -> bool:
    """Check for unmitigated FVGs supporting the trade direction."""
    for fvg in fvgs:
        if fvg.direction == direction:
            if direction == "bullish" and fvg.high < price:
                return True
            if direction == "bearish" and fvg.low > price:
                return True
    return False


def is_premium_zone(price: float, candles: List[Candle], lookback: int = 50) -> bool:
    """Price is in premium zone (above 50% of range)."""
    recent = candles[-lookback:]
    highest = max(c.high for c in recent)
    lowest = min(c.low for c in recent)
    mid = (highest + lowest) / 2
    return price > mid


def is_discount_zone(price: float, candles: List[Candle], lookback: int = 50) -> bool:
    """Price is in discount zone (below 50% of range)."""
    recent = candles[-lookback:]
    highest = max(c.high for c in recent)
    lowest = min(c.low for c in recent)
    mid = (highest + lowest) / 2
    return price < mid


# ---------------------------------------------------------------------------
# Rule 5: Multi-Timeframe Confirmation
# ---------------------------------------------------------------------------

def get_5m_trend(candles_5m: List[Candle]) -> str:
    """Determine trend on 5M timeframe using EMA crossover and candle direction."""
    if len(candles_5m) < 5:
        return "neutral"

    last5 = candles_5m[-5:]
    bullish_count = sum(1 for c in last5 if c.is_bullish)

    ema_short = compute_ema([c.close for c in candles_5m], 5)
    ema_long = compute_ema([c.close for c in candles_5m], 13)

    if not ema_short or not ema_long:
        # Fallback to candle direction when not enough data for EMA
        if bullish_count >= 4:
            return "bullish"
        if bullish_count <= 1:
            return "bearish"
        return "neutral"

    ema_bearish = ema_short[-1] < ema_long[-1]
    ema_bullish = ema_short[-1] > ema_long[-1]

    # Strong alignment: EMA + candle direction agree
    if ema_bullish and bullish_count >= 3:
        return "bullish"
    if ema_bearish and bullish_count <= 2:
        return "bearish"

    # EMA crossover alone is sufficient if the trend is clear
    if ema_bullish and candles_5m[-1].close > ema_short[-1]:
        return "bullish"
    if ema_bearish and candles_5m[-1].close < ema_short[-1]:
        return "bearish"

    # Last resort: price direction of last 3 candles
    last3 = candles_5m[-3:]
    if all(last3[i].close < last3[i - 1].close for i in range(1, 3)):
        return "bearish"
    if all(last3[i].close > last3[i - 1].close for i in range(1, 3)):
        return "bullish"

    return "neutral"


# ---------------------------------------------------------------------------
# Rule 6: Candlestick Patterns
# ---------------------------------------------------------------------------

def is_pin_bar(candle: Candle, direction: str) -> bool:
    """Detect Pin Bar / Hammer / Shooting Star."""
    if candle.range < 0.01:
        return False
    body_ratio = candle.body / candle.range

    if direction == "bullish":
        return (candle.lower_wick >= candle.body * 2 and
                candle.upper_wick <= candle.range * 0.3 and
                body_ratio < 0.35 and
                candle.is_bullish)

    return (candle.upper_wick >= candle.body * 2 and
            candle.lower_wick <= candle.range * 0.3 and
            body_ratio < 0.35 and
            candle.is_bearish)


def is_engulfing(current: Candle, previous: Candle, direction: str) -> bool:
    """Detect Bullish/Bearish Engulfing."""
    if previous.body < 0.01:
        return False
    if current.body < previous.body:
        return False

    if direction == "bullish":
        return (previous.is_bearish and current.is_bullish and
                current.body_bottom <= previous.body_bottom and
                current.body_top >= previous.body_top)

    return (previous.is_bullish and current.is_bearish and
            current.body_top >= previous.body_top and
            current.body_bottom <= previous.body_bottom)


def is_hammer(candle: Candle) -> bool:
    """Hammer: small body at top, long lower wick."""
    if candle.range < 0.01:
        return False
    return (candle.lower_wick >= candle.body * 2.5 and
            candle.upper_wick <= candle.range * 0.15)


def is_shooting_star(candle: Candle) -> bool:
    """Shooting Star: small body at bottom, long upper wick."""
    if candle.range < 0.01:
        return False
    return (candle.upper_wick >= candle.body * 2.5 and
            candle.lower_wick <= candle.range * 0.15)


def detect_pattern(candles: List[Candle], direction: str) -> bool:
    """Check if the last candle shows a valid pattern aligned with direction."""
    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else None

    if direction == "bullish":
        if is_pin_bar(last, "bullish"):
            return True
        if is_hammer(last) and last.is_bullish:
            return True
        if prev and is_engulfing(last, prev, "bullish"):
            return True

    if direction == "bearish":
        if is_pin_bar(last, "bearish"):
            return True
        if is_shooting_star(last) and last.is_bearish:
            return True
        if prev and is_engulfing(last, prev, "bearish"):
            return True

    return False


# ---------------------------------------------------------------------------
# Rule 7: EMA & Momentum
# ---------------------------------------------------------------------------

def compute_ema(prices: List[float], period: int) -> List[float]:
    """Compute EMA series."""
    if len(prices) < period:
        return []
    alpha = 2 / (period + 1)
    ema_values = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema_values.append(alpha * price + (1 - alpha) * ema_values[-1])
    return ema_values


def ema_trend_aligned(candles: List[Candle], direction: str) -> bool:
    """
    Check if EMA trend aligns with direction.
    For ICT entries after pullbacks, we check:
    - EMA21 slope confirms overall trend direction
    - Price is crossing or has crossed in the expected direction relative to EMA9
    """
    closes = [c.close for c in candles]
    ema_short = compute_ema(closes, 9)
    ema_mid = compute_ema(closes, 21)

    if not ema_short or not ema_mid or len(ema_mid) < 3:
        return False

    short_val = ema_short[-1]
    mid_val = ema_mid[-1]
    mid_prev = ema_mid[-3]
    price = closes[-1]

    # Classic alignment: price > EMA9 > EMA21 (bullish) or price < EMA9 < EMA21 (bearish)
    if direction == "bullish" and price > short_val > mid_val:
        return True
    if direction == "bearish" and price < short_val < mid_val:
        return True

    # ICT pullback entry: EMA21 slope confirms trend + price crossing back
    ema_mid_slope_bullish = mid_val > mid_prev
    ema_mid_slope_bearish = mid_val < mid_prev

    if direction == "bullish":
        # EMA21 rising (overall uptrend) and price crossing above EMA9
        if ema_mid_slope_bullish and price > short_val:
            return True
        # Price above EMA21 with recent bullish momentum
        if price > mid_val and closes[-1] > closes[-2]:
            return True

    if direction == "bearish":
        # EMA21 falling (overall downtrend) and price crossing below EMA9
        if ema_mid_slope_bearish and price < short_val:
            return True
        # Price below EMA21 with recent bearish momentum
        if price < mid_val and closes[-1] < closes[-2]:
            return True

    return False


def momentum_confirmed(candles: List[Candle], direction: str) -> bool:
    """Last candle shows strong movement in the expected direction."""
    last = candles[-1]
    avg_body = sum(c.body for c in candles[-10:]) / 10 if len(candles) >= 10 else last.body

    if direction == "bullish":
        return last.is_bullish and last.body >= avg_body * 0.8
    return last.is_bearish and last.body >= avg_body * 0.8


def volume_confirmed(candles: List[Candle], lookback: int = 20) -> bool:
    """Check if last candle volume is above recent average."""
    if len(candles) < lookback + 1:
        lookback = len(candles) - 1
    if lookback < 1:
        return False
    avg_vol = sum(c.volume for c in candles[-(lookback + 1):-1]) / lookback
    return candles[-1].volume >= avg_vol * 0.9


# ---------------------------------------------------------------------------
# Rule 8: Session Filter
# ---------------------------------------------------------------------------

def is_valid_session(candle: Candle) -> bool:
    """
    Only trade during London (07:00-16:00 UTC) or NY (12:00-21:00 UTC).
    Combined active window: 07:00-21:00 UTC.
    """
    hour = candle.time.hour
    return 7 <= hour <= 21


def is_low_volume_market(candles: List[Candle], lookback: int = 20) -> bool:
    """Detect if market is ranging / low-volume."""
    if len(candles) < lookback:
        return True
    recent = candles[-lookback:]
    avg_range = sum(c.range for c in recent) / lookback
    total_range = max(c.high for c in recent) - min(c.low for c in recent)
    return total_range < avg_range * 3


# ---------------------------------------------------------------------------
# Rule 9: Risk Management
# ---------------------------------------------------------------------------

def calculate_sl(candles: List[Candle], direction: str, lookback: int = 10) -> float:
    """Place SL behind last valid swing High/Low."""
    buffer = 0.50
    recent = candles[-lookback:]

    if direction == "bullish":
        swing_low = min(c.low for c in recent)
        return round(swing_low - buffer, 2)

    swing_high = max(c.high for c in recent)
    return round(swing_high + buffer, 2)


def calculate_tp(entry: float, sl: float, direction: str, rr_ratio: float = 2.0) -> float:
    """Set TP based on minimum 1:2 Risk/Reward."""
    risk = abs(entry - sl)
    if direction == "bullish":
        return round(entry + risk * rr_ratio, 2)
    return round(entry - risk * rr_ratio, 2)


# ---------------------------------------------------------------------------
# Rule 10: Multi-Candle Confirmation
# ---------------------------------------------------------------------------

def multi_candle_confirmation(candles: List[Candle], direction: str) -> bool:
    """
    Signal valid only if last 3-5 candles align with trend, EMA, momentum.
    """
    last5 = candles[-5:]
    if direction == "bullish":
        bullish_count = sum(1 for c in last5 if c.is_bullish)
        closes_rising = all(
            last5[i].close >= last5[i - 1].close for i in range(1, len(last5))
        )
        return bullish_count >= 3 or closes_rising

    bearish_count = sum(1 for c in last5 if c.is_bearish)
    closes_falling = all(
        last5[i].close <= last5[i - 1].close for i in range(1, len(last5))
    )
    return bearish_count >= 3 or closes_falling


# ---------------------------------------------------------------------------
# Main Signal Engine
# ---------------------------------------------------------------------------

def generate_signal(candles_1m: List[Candle]) -> dict:
    """
    Run all ICT rules and generate a single BUY/SELL/NO TRADE signal.
    """
    no_trade = {"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}

    if len(candles_1m) < 30:
        return no_trade

    # Rule 8: Session Filter
    last_candle = candles_1m[-1]
    if not is_valid_session(last_candle):
        return no_trade

    if is_low_volume_market(candles_1m):
        return no_trade

    # Rule 5: Multi-Timeframe - derive 5M trend
    candles_5m = aggregate_5m(candles_1m)
    trend_5m = get_5m_trend(candles_5m)

    if trend_5m == "neutral":
        return no_trade

    # Rule 1: Market Structure on 1M
    structure = detect_market_structure(candles_1m)

    if structure == "neutral":
        return no_trade

    # Multi-TF alignment: 5M and 1M must agree
    if trend_5m != structure:
        return no_trade

    direction = structure  # 'bullish' or 'bearish'

    # Rule 7: EMA & Momentum
    if not ema_trend_aligned(candles_1m, direction):
        return no_trade

    if not momentum_confirmed(candles_1m, direction):
        return no_trade

    if not volume_confirmed(candles_1m):
        return no_trade

    # Rule 10: Multi-Candle Confirmation
    if not multi_candle_confirmation(candles_1m, direction):
        return no_trade

    # Rule 2: Order Blocks
    obs = detect_order_blocks(candles_1m)

    # Rule 4: Fair Value Gaps
    fvgs = detect_fvg(candles_1m)

    # Rule 3: Liquidity Sweep
    sweep = detect_liquidity_sweep(candles_1m)

    # Rule 6: Candlestick Pattern
    pattern_match = detect_pattern(candles_1m, direction)

    # Confluence scoring - require multiple confirmations
    confluence_score = 0

    if price_in_order_block(last_candle.close, obs, direction):
        confluence_score += 1
    elif has_unmitigated_ob(last_candle.close, obs, direction):
        confluence_score += 1

    if price_in_fvg(last_candle.close, fvgs, direction):
        confluence_score += 1
    elif has_unmitigated_fvg(last_candle.close, fvgs, direction):
        confluence_score += 1

    if sweep:
        if (direction == "bullish" and sweep == "bullish_sweep") or \
           (direction == "bearish" and sweep == "bearish_sweep"):
            confluence_score += 1

    if pattern_match:
        confluence_score += 1

    # Premium/Discount zone alignment
    if direction == "bullish" and is_discount_zone(last_candle.close, candles_1m):
        confluence_score += 1
    elif direction == "bearish" and is_premium_zone(last_candle.close, candles_1m):
        confluence_score += 1

    # Strong momentum with volume spike adds confluence
    if len(candles_1m) >= 10:
        avg_vol = sum(c.volume for c in candles_1m[-11:-1]) / 10
        if last_candle.volume > avg_vol * 1.5:
            avg_body = sum(c.body for c in candles_1m[-11:-1]) / 10
            if last_candle.body > avg_body * 1.5:
                confluence_score += 1

    # Require at least 2 confluence factors beyond structure + EMA + momentum
    if confluence_score < 2:
        return no_trade

    # Generate signal
    entry = round(last_candle.close, 2)
    sl = calculate_sl(candles_1m, direction)
    tp = calculate_tp(entry, sl, direction)

    # Validate R:R is at least 1:2
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0 or reward / risk < 1.95:
        return no_trade

    signal = "BUY" if direction == "bullish" else "SELL"
    return {"signal": signal, "entry": entry, "SL": sl, "TP": tp}


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def generate_signal_debug(candles_1m: List[Candle]) -> dict:
    """Debug version that prints which rules pass/fail."""
    no_trade = {"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}
    debug_info: dict = {}

    if len(candles_1m) < 30:
        debug_info["reject"] = "insufficient data"
        return no_trade, debug_info

    last_candle = candles_1m[-1]
    debug_info["session_valid"] = is_valid_session(last_candle)
    if not is_valid_session(last_candle):
        debug_info["reject"] = "session filter"
        return no_trade, debug_info

    low_vol = is_low_volume_market(candles_1m)
    debug_info["low_volume_market"] = low_vol
    if low_vol:
        debug_info["reject"] = "low volume / ranging market"
        return no_trade, debug_info

    candles_5m = aggregate_5m(candles_1m)
    trend_5m = get_5m_trend(candles_5m)
    debug_info["trend_5m"] = trend_5m
    if trend_5m == "neutral":
        debug_info["reject"] = "5M trend neutral"
        return no_trade, debug_info

    structure = detect_market_structure(candles_1m)
    debug_info["market_structure_1m"] = structure
    if structure == "neutral":
        debug_info["reject"] = "1M structure neutral"
        return no_trade, debug_info

    if trend_5m != structure:
        debug_info["reject"] = f"MTF mismatch: 5M={trend_5m}, 1M={structure}"
        return no_trade, debug_info

    direction = structure
    debug_info["direction"] = direction

    ema_ok = ema_trend_aligned(candles_1m, direction)
    debug_info["ema_aligned"] = ema_ok
    if not ema_ok:
        debug_info["reject"] = "EMA not aligned"
        return no_trade, debug_info

    mom_ok = momentum_confirmed(candles_1m, direction)
    debug_info["momentum"] = mom_ok
    if not mom_ok:
        debug_info["reject"] = "momentum weak"
        return no_trade, debug_info

    vol_ok = volume_confirmed(candles_1m)
    debug_info["volume"] = vol_ok
    if not vol_ok:
        debug_info["reject"] = "volume low"
        return no_trade, debug_info

    multi_ok = multi_candle_confirmation(candles_1m, direction)
    debug_info["multi_candle"] = multi_ok
    if not multi_ok:
        debug_info["reject"] = "multi-candle not confirmed"
        return no_trade, debug_info

    obs = detect_order_blocks(candles_1m)
    fvgs = detect_fvg(candles_1m)
    sweep = detect_liquidity_sweep(candles_1m)
    pattern_match = detect_pattern(candles_1m, direction)

    confluence_score = 0
    confluence_reasons = []

    if price_in_order_block(last_candle.close, obs, direction):
        confluence_score += 1
        confluence_reasons.append("order_block")
    elif has_unmitigated_ob(last_candle.close, obs, direction):
        confluence_score += 1
        confluence_reasons.append("unmitigated_ob")

    if price_in_fvg(last_candle.close, fvgs, direction):
        confluence_score += 1
        confluence_reasons.append("fvg")
    elif has_unmitigated_fvg(last_candle.close, fvgs, direction):
        confluence_score += 1
        confluence_reasons.append("unmitigated_fvg")

    if sweep:
        if (direction == "bullish" and sweep == "bullish_sweep") or \
           (direction == "bearish" and sweep == "bearish_sweep"):
            confluence_score += 1
            confluence_reasons.append("liquidity_sweep")

    if pattern_match:
        confluence_score += 1
        confluence_reasons.append("candle_pattern")

    if direction == "bullish" and is_discount_zone(last_candle.close, candles_1m):
        confluence_score += 1
        confluence_reasons.append("discount_zone")
    elif direction == "bearish" and is_premium_zone(last_candle.close, candles_1m):
        confluence_score += 1
        confluence_reasons.append("premium_zone")

    if len(candles_1m) >= 10:
        avg_vol = sum(c.volume for c in candles_1m[-11:-1]) / 10
        if last_candle.volume > avg_vol * 1.5:
            avg_body = sum(c.body for c in candles_1m[-11:-1]) / 10
            if last_candle.body > avg_body * 1.5:
                confluence_score += 1
                confluence_reasons.append("volume_momentum_spike")

    debug_info["confluence_score"] = confluence_score
    debug_info["confluence_reasons"] = confluence_reasons
    debug_info["order_blocks_found"] = len(obs)
    debug_info["fvgs_found"] = len(fvgs)
    debug_info["sweep"] = sweep
    debug_info["pattern"] = pattern_match

    if confluence_score < 2:
        debug_info["reject"] = f"confluence too low ({confluence_score}/2)"
        return no_trade, debug_info

    entry = round(last_candle.close, 2)
    sl = calculate_sl(candles_1m, direction)
    tp = calculate_tp(entry, sl, direction)

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    debug_info["risk"] = round(risk, 2)
    debug_info["reward"] = round(reward, 2)

    if risk <= 0 or reward / risk < 1.95:
        debug_info["reject"] = "R:R below 2.0"
        return no_trade, debug_info

    signal = "BUY" if direction == "bullish" else "SELL"
    debug_info["reject"] = None
    return {"signal": signal, "entry": entry, "SL": sl, "TP": tp}, debug_info


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    default_csv = root / "data" / "XAUUSD_M1_sample.csv"

    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_csv
    debug_mode = "--debug" in sys.argv

    if not csv_path.exists():
        print(json.dumps({"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}))
        sys.exit(0)

    candles = load_candles(csv_path)

    if not candles:
        print(json.dumps({"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}))
        sys.exit(0)

    if debug_mode:
        result, debug_info = generate_signal_debug(candles)
        import sys as _sys
        print(json.dumps(debug_info, indent=2), file=_sys.stderr)
    else:
        result = generate_signal(candles)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
