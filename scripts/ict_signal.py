#!/usr/bin/env python3
"""Smart Money / ICT signal analyzer for XAUUSD.

Reads an array of OHLC+Volume candles (assumed 1-minute by default) and
prints exactly ONE JSON object describing the trading decision::

    {"signal": "BUY" | "SELL" | "NO TRADE", "entry": <price>, "SL": <price>, "TP": <price>}

Implements the ten ICT / Smart Money Concept rule layers requested by the
user: market structure (BOS/CHOCH), order blocks, liquidity sweeps,
fair-value gaps, multi-timeframe (M1+M5) confirmation, candlestick
patterns, EMA + momentum + volume filters, London/NY session filter,
risk management with R:R = 1:2, and multi-candle alignment. A signal is
only emitted when every layer agrees with the trend.

Stdout intentionally contains a single JSON line. All other diagnostic
output goes to stderr (only when ``--debug`` is provided).

Usage examples
--------------
Read MT5 9-column CSV (Date,Time,O,H,L,C,TickVol,Volume,Spread)::

    python3 scripts/ict_signal.py --csv data/XAUUSD_M1_sample.csv

Pipe JSON candles in on stdin (array of objects with keys time, open, high,
low, close, volume)::

    cat candles.json | python3 scripts/ict_signal.py --json -

Pass live candles inline (last is the just-closed candle)::

    python3 scripts/ict_signal.py --json '[{"time":"2026-05-01T09:00","open":...}]'
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Iterable, Sequence


@dataclass
class Candle:
    """One OHLC bar plus volume and (optionally) a timestamp."""

    time: datetime | None
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-9)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


def parse_mt5_csv(text: str) -> list[Candle]:
    """Parse the MT5 export format: Date,Time,O,H,L,C,TickVol,Volume,Spread."""

    candles: list[Candle] = []
    reader = csv.reader(text.splitlines())
    for row in reader:
        if not row or row[0].lower().startswith("date"):
            continue
        if len(row) < 6:
            raise ValueError(f"CSV row has too few columns: {row!r}")

        if len(row) >= 7 and ":" in row[1]:
            bar_time = datetime.strptime(
                row[0].strip() + " " + row[1].strip(), "%Y.%m.%d %H:%M"
            )
            o, h, l, c = map(float, row[2:6])
            tick_vol = float(row[6]) if len(row) >= 7 else 0.0
            real_vol = float(row[7]) if len(row) >= 8 else 0.0
            volume = real_vol if real_vol > 0 else tick_vol
        else:
            bar_time = _parse_time(row[0])
            o, h, l, c = map(float, row[1:5])
            volume = float(row[5]) if len(row) >= 6 else 0.0
        candles.append(Candle(bar_time, o, h, l, c, volume))
    return candles


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    formats = (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def parse_json_candles(payload: str) -> list[Candle]:
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("JSON candle payload must be an array")
    candles: list[Candle] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError(f"Candle must be an object, got {type(entry).__name__}")
        candles.append(
            Candle(
                _parse_time(entry.get("time")),
                float(entry["open"]),
                float(entry["high"]),
                float(entry["low"]),
                float(entry["close"]),
                float(entry.get("volume", 0.0) or 0.0),
            )
        )
    return candles


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------


def ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(alpha * float(v) + (1 - alpha) * out[-1])
    return out


def aggregate_m5(m1: Sequence[Candle]) -> list[Candle]:
    """Aggregate M1 candles into 5-minute candles using floor-of-5 bucketing.

    Falls back to fixed-size 5-bar groups when timestamps are missing.
    """

    if not m1:
        return []
    if any(c.time is None for c in m1):
        return _aggregate_fixed(m1, 5)

    out: list[Candle] = []
    bucket: list[Candle] = []
    current_key: tuple[int, int, int, int, int] | None = None
    for candle in m1:
        assert candle.time is not None
        t = candle.time
        bucket_minute = (t.minute // 5) * 5
        key = (t.year, t.month, t.day, t.hour, bucket_minute)
        if current_key is None or key != current_key:
            if bucket:
                out.append(_merge(bucket))
            bucket = [candle]
            current_key = key
        else:
            bucket.append(candle)
    if bucket:
        out.append(_merge(bucket))
    return out


def _aggregate_fixed(m1: Sequence[Candle], group: int) -> list[Candle]:
    out: list[Candle] = []
    for i in range(0, len(m1), group):
        chunk = m1[i : i + group]
        if not chunk:
            continue
        out.append(_merge(chunk))
    return out


def _merge(chunk: Sequence[Candle]) -> Candle:
    return Candle(
        time=chunk[0].time,
        open=chunk[0].open,
        high=max(c.high for c in chunk),
        low=min(c.low for c in chunk),
        close=chunk[-1].close,
        volume=sum(c.volume for c in chunk),
    )


def fractal_swings(
    candles: Sequence[Candle], k: int = 2
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (highs, lows) as ``(index, price)`` pairs.

    A swing high is a candle whose high is the strict max of its
    ``2k+1`` neighborhood; symmetrical for swing lows.
    """

    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(k, len(candles) - k):
        window = candles[i - k : i + k + 1]
        h = candles[i].high
        l = candles[i].low
        if h == max(c.high for c in window) and h > max(
            c.high for j, c in enumerate(window) if j != k
        ):
            highs.append((i, h))
        if l == min(c.low for c in window) and l < min(
            c.low for j, c in enumerate(window) if j != k
        ):
            lows.append((i, l))
    return highs, lows


def session_active(when: datetime | None) -> bool:
    """London (07:00-16:00 UTC) or New York (12:00-21:00 UTC) session.

    Missing timestamps are treated as active so manually-curated payloads
    without timestamps still produce signals.
    """

    if when is None:
        return True
    hour = when.hour + when.minute / 60.0
    london = 7.0 <= hour < 16.0
    ny = 12.0 <= hour < 21.0
    return london or ny


# ---------------------------------------------------------------------------
# Pattern + structure detection
# ---------------------------------------------------------------------------


def candle_pattern_direction(prev: Candle, last: Candle) -> int:
    """+1 bullish reversal, -1 bearish reversal, 0 neutral."""

    rng = last.range
    body = max(last.body, 1e-9)
    upper = last.upper_wick
    lower = last.lower_wick

    bullish_pin = (
        lower >= body * 2 and upper <= rng * 0.30 and last.close >= last.open
    )
    hammer = lower >= rng * 0.6 and body / rng <= 0.35 and upper <= rng * 0.2
    bullish_engulf = (
        prev.bearish
        and last.bullish
        and last.open <= prev.close
        and last.close >= prev.open
        and last.body > prev.body
    )
    if bullish_pin or hammer or bullish_engulf:
        return 1

    bearish_pin = (
        upper >= body * 2 and lower <= rng * 0.30 and last.close <= last.open
    )
    shooting_star = upper >= rng * 0.6 and body / rng <= 0.35 and lower <= rng * 0.2
    bearish_engulf = (
        prev.bullish
        and last.bearish
        and last.open >= prev.close
        and last.close <= prev.open
        and last.body > prev.body
    )
    if bearish_pin or shooting_star or bearish_engulf:
        return -1

    return 0


def fvg_direction(candles: Sequence[Candle], lookback: int = 8) -> int:
    """Detect a recent 3-candle Fair Value Gap.

    Bullish FVG: ``c[i-2].high < c[i].low`` (gap above), price has not
    fully filled it yet. Bearish FVG: ``c[i-2].low > c[i].high``.
    Returns +1 / -1 / 0.
    """

    n = len(candles)
    if n < 3:
        return 0
    last_close = candles[-1].close
    for i in range(n - 1, max(2, n - lookback) - 1, -1):
        gap_up_low = candles[i].low
        gap_up_high = candles[i - 2].high
        if gap_up_high < gap_up_low and last_close >= gap_up_low * 0.999:
            return 1
        gap_down_high = candles[i].high
        gap_down_low = candles[i - 2].low
        if gap_down_low > gap_down_high and last_close <= gap_down_high * 1.001:
            return -1
    return 0


def order_block_direction(candles: Sequence[Candle], lookback: int = 12) -> int:
    """Find the most recent valid order block.

    Bullish OB: a bearish candle followed by a bullish impulse (next
    candle closes above the OB high) on rising volume.
    Bearish OB: a bullish candle followed by a bearish impulse on
    rising volume.
    """

    n = len(candles)
    if n < 4:
        return 0
    avg_volume = sum(c.volume for c in candles[-min(20, n) :]) / max(1, min(20, n))
    start = max(1, n - lookback)
    for i in range(n - 2, start - 1, -1):
        ob = candles[i]
        impulse = candles[i + 1]
        if ob.bearish and impulse.bullish and impulse.close > ob.high:
            if impulse.volume >= avg_volume * 1.05 or impulse.body > ob.body:
                return 1
        if ob.bullish and impulse.bearish and impulse.close < ob.low:
            if impulse.volume >= avg_volume * 1.05 or impulse.body > ob.body:
                return -1
    return 0


def liquidity_sweep_direction(candles: Sequence[Candle], lookback: int = 25) -> int:
    """Detect a stop-hunt / liquidity sweep on the most recent candle.

    Bullish sweep: the last candle's low pierced the recent swing low
    but the candle closed back above that low.
    Bearish sweep: symmetrical above the swing high.
    """

    n = len(candles)
    if n < 5:
        return 0
    last = candles[-1]
    window = candles[max(0, n - lookback - 1) : n - 1]
    if not window:
        return 0
    swing_high = max(c.high for c in window)
    swing_low = min(c.low for c in window)
    if last.low < swing_low and last.close > swing_low:
        return 1
    if last.high > swing_high and last.close < swing_high:
        return -1
    return 0


def structure_direction(candles: Sequence[Candle]) -> int:
    """Determine BOS/CHOCH bias from the most recent confirmed swings."""

    if len(candles) < 7:
        return 0
    highs, lows = fractal_swings(candles, k=2)
    if len(highs) < 2 or len(lows) < 2:
        return 0
    last_close = candles[-1].close
    last_high_idx, last_high = highs[-1]
    last_low_idx, last_low = lows[-1]
    prev_high = highs[-2][1]
    prev_low = lows[-2][1]

    bos_up = last_close > last_high and last_high > prev_high and last_low > prev_low
    bos_down = last_close < last_low and last_low < prev_low and last_high < prev_high
    if bos_up:
        return 1
    if bos_down:
        return -1

    choch_up = last_close > last_high and last_low > prev_low
    choch_down = last_close < last_low and last_high < prev_high
    if choch_up:
        return 1
    if choch_down:
        return -1
    return 0


def trend_direction(candles: Sequence[Candle]) -> int:
    """Combined EMA + structure trend bias on a given timeframe."""

    if len(candles) < 15:
        return 0
    closes = [c.close for c in candles]
    fast = ema(closes, 9)
    mid = ema(closes, 21)
    slope_lookback = min(5, len(mid) - 1)
    slope = mid[-1] - mid[-slope_lookback - 1]
    bias = 0
    if closes[-1] > mid[-1] and fast[-1] > mid[-1] and slope > 0:
        bias = 1
    elif closes[-1] < mid[-1] and fast[-1] < mid[-1] and slope < 0:
        bias = -1
    structure = structure_direction(candles)
    if bias != 0 and structure != 0 and bias != structure:
        return 0
    return bias if bias != 0 else structure


def momentum_ok(candles: Sequence[Candle], direction: int) -> bool:
    """Last candle has a strong directional close vs its range."""

    if direction == 0 or not candles:
        return False
    last = candles[-1]
    rng = last.range
    if rng <= 0:
        return False
    body_ratio = last.body / rng
    if direction == 1:
        return last.bullish and body_ratio >= 0.45
    return last.bearish and body_ratio >= 0.45


def volume_increasing(candles: Sequence[Candle], window: int = 20) -> bool:
    if len(candles) < 5:
        return False
    recent = candles[-min(window, len(candles)) :]
    avg = sum(c.volume for c in recent[:-1]) / max(1, len(recent) - 1)
    if avg <= 0:
        # Without real volume data treat as inconclusive (do not block, do not boost).
        return True
    return candles[-1].volume >= avg * 1.10


def multi_candle_alignment(candles: Sequence[Candle], direction: int, n: int = 3) -> bool:
    """Last ``n`` candles must broadly agree with the requested direction."""

    if direction == 0 or len(candles) < n:
        return False
    closes = [c.close for c in candles[-(n + 1) :]]
    if len(closes) < n + 1:
        return False
    moves = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    if direction == 1:
        return sum(1 for m in moves if m >= 0) >= max(2, n - 1) and closes[-1] > closes[0]
    return sum(1 for m in moves if m <= 0) >= max(2, n - 1) and closes[-1] < closes[0]


# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------


def last_swing_low(candles: Sequence[Candle], lookback: int = 20) -> float:
    window = candles[-min(lookback, len(candles)) :]
    return min(c.low for c in window)


def last_swing_high(candles: Sequence[Candle], lookback: int = 20) -> float:
    window = candles[-min(lookback, len(candles)) :]
    return max(c.high for c in window)


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        prev_close = candles[i - 1].close
        trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
    if not trs:
        return 0.0
    window = trs[-period:]
    return sum(window) / len(window)


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------


def _round(value: float, digits: int = 2) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, digits)


def analyze(
    m1: Sequence[Candle],
    *,
    rr: float = 2.0,
    digits: int = 2,
    debug: bool = False,
) -> dict[str, object]:
    """Run all 10 ICT/SMC layers and return the final signal dict."""

    if len(m1) < 30:
        _log(debug, f"insufficient candles: {len(m1)}")
        return _no_trade()

    m5 = aggregate_m5(m1)
    if len(m5) < 6:
        _log(debug, "insufficient M5 candles after aggregation")
        return _no_trade()

    last = m1[-1]
    prev = m1[-2]

    if not session_active(last.time):
        _log(debug, f"session filter blocked at {last.time}")
        return _no_trade()

    trend5 = trend_direction(m5)
    if trend5 == 0:
        _log(debug, "no clean M5 trend")
        return _no_trade()

    trend1 = trend_direction(m1)
    if trend1 != 0 and trend1 != trend5:
        _log(debug, f"M1 trend ({trend1}) opposes M5 trend ({trend5})")
        return _no_trade()

    direction = trend5
    structure1 = structure_direction(m1)
    if structure1 != 0 and structure1 != direction:
        _log(debug, f"BOS/CHOCH ({structure1}) contradicts trend")
        return _no_trade()

    ob = order_block_direction(m1)
    if ob != 0 and ob != direction:
        _log(debug, f"order block ({ob}) contradicts trend")
        return _no_trade()

    fvg = fvg_direction(m1)
    if fvg != 0 and fvg != direction:
        _log(debug, f"FVG ({fvg}) contradicts trend")
        return _no_trade()

    sweep = liquidity_sweep_direction(m1)
    if sweep != 0 and sweep != direction:
        _log(debug, f"liquidity sweep ({sweep}) contradicts trend")
        return _no_trade()

    pattern = candle_pattern_direction(prev, last)
    if pattern != 0 and pattern != direction:
        _log(debug, f"candle pattern ({pattern}) contradicts trend")
        return _no_trade()

    if not momentum_ok(m1, direction):
        _log(debug, "momentum check failed")
        return _no_trade()

    if not volume_increasing(m1):
        _log(debug, "volume check failed")
        return _no_trade()

    if not multi_candle_alignment(m1, direction, n=3):
        _log(debug, "multi-candle alignment failed")
        return _no_trade()

    confluences = sum(
        1
        for v in (structure1, ob, fvg, sweep, pattern)
        if v == direction
    )
    if confluences < 2:
        _log(
            debug,
            f"only {confluences} confirming SMC layers (need >= 2)",
        )
        return _no_trade()

    avg_range = atr(m1, 14) or last.range
    buffer = max(0.10, avg_range * 0.25)

    entry = last.close
    if direction == 1:
        sl = min(last_swing_low(m1, 20), last.low) - buffer
        risk = entry - sl
        if risk <= 0:
            _log(debug, "non-positive BUY risk")
            return _no_trade()
        tp = entry + rr * risk
        signal = "BUY"
    else:
        sl = max(last_swing_high(m1, 20), last.high) + buffer
        risk = sl - entry
        if risk <= 0:
            _log(debug, "non-positive SELL risk")
            return _no_trade()
        tp = entry - rr * risk
        signal = "SELL"

    return {
        "signal": signal,
        "entry": _round(entry, digits),
        "SL": _round(sl, digits),
        "TP": _round(tp, digits),
    }


def _no_trade() -> dict[str, object]:
    return {"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}


def _log(debug: bool, message: str) -> None:
    if debug:
        print(f"[ict_signal] {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_input(args: argparse.Namespace) -> list[Candle]:
    if args.csv:
        path = Path(args.csv)
        text = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
        return parse_mt5_csv(text)
    if args.json:
        if args.json == "-":
            payload = sys.stdin.read()
        elif Path(args.json).exists():
            payload = Path(args.json).read_text(encoding="utf-8")
        else:
            payload = args.json
        return parse_json_candles(payload)
    raise SystemExit("Provide --csv or --json input")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", help="Path to MT5 9-column CSV (or '-' for stdin)")
    parser.add_argument(
        "--json",
        help="Inline JSON, path to JSON file, or '-' for stdin (array of OHLCV objects)",
    )
    parser.add_argument(
        "--rr", type=float, default=2.0, help="Reward:Risk multiplier (default 2.0)"
    )
    parser.add_argument(
        "--digits", type=int, default=2, help="Price rounding digits (default 2)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print rule-layer diagnostics to stderr",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        candles = _read_input(args)
        result = analyze(candles, rr=args.rr, digits=args.digits, debug=args.debug)
    except Exception as exc:  # noqa: BLE001 - per spec we always emit one JSON
        if args.debug:
            print(f"[ict_signal] error: {exc}", file=sys.stderr)
        result = _no_trade()

    print(json.dumps(result, separators=(", ", ": ")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
