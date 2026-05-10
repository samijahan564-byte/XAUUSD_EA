#!/usr/bin/env python3
"""Synthetic scenario tests for ``scripts.ict_signal``.

Each test fabricates a small market and asserts that the analyzer
produces the expected JSON shape. Run with::

    python3 -m scripts.test_ict_signal

The tests intentionally cover three branches of the decision tree:
BUY confluence, SELL confluence, NO TRADE during a quiet/low-volume
range, and NO TRADE outside the London/NY session window.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ict_signal import (  # noqa: E402  (import after path adjust)
    Candle,
    analyze,
    main as cli_main,
)


def _bullish_run() -> list[Candle]:
    """Construct a strong M5/M1 uptrend with a final liquidity sweep + impulse."""

    candles: list[Candle] = []
    start = datetime(2026, 5, 1, 9, 0)
    price = 2300.0
    # A 90-minute uptrend at +0.18/minute on rising volume.
    for i in range(90):
        t = start + timedelta(minutes=i)
        o = price
        c = price + 0.18
        h = c + 0.05
        l = o - 0.04
        candles.append(Candle(t, o, h, l, c, 200 + i * 2))
        price = c
    # Quick pullback (4 minutes) + sweep below the local low.
    for i in range(4):
        t = start + timedelta(minutes=90 + i)
        o = price
        c = price - 0.10
        h = o + 0.04
        l = c - 0.04
        candles.append(Candle(t, o, h, l, c, 380 + i))
        price = c
    # Sweep candle: long lower wick that closes back above the recent low.
    pre_sweep_low = min(c.low for c in candles[-5:])
    sweep_open = price
    sweep_low = pre_sweep_low - 0.20
    sweep_close = pre_sweep_low + 0.30
    sweep_high = max(sweep_open, sweep_close) + 0.05
    candles.append(
        Candle(
            start + timedelta(minutes=94),
            sweep_open,
            sweep_high,
            sweep_low,
            sweep_close,
            520,
        )
    )
    price = sweep_close
    # Final strong bullish impulse closes the bar with high momentum + volume,
    # and gaps far enough above the prior pullback to leave a bullish FVG.
    impulse_open = price + 0.20
    impulse_close = price + 1.40
    impulse_high = impulse_close + 0.05
    impulse_low = impulse_open - 0.02
    candles.append(
        Candle(
            start + timedelta(minutes=95),
            impulse_open,
            impulse_high,
            impulse_low,
            impulse_close,
            900,
        )
    )
    return candles


def _bearish_run() -> list[Candle]:
    """Mirror image of the bullish run."""

    candles: list[Candle] = []
    start = datetime(2026, 5, 1, 13, 0)
    price = 2400.0
    for i in range(90):
        t = start + timedelta(minutes=i)
        o = price
        c = price - 0.18
        h = o + 0.04
        l = c - 0.05
        candles.append(Candle(t, o, h, l, c, 200 + i * 2))
        price = c
    for i in range(4):
        t = start + timedelta(minutes=90 + i)
        o = price
        c = price + 0.10
        h = c + 0.04
        l = o - 0.04
        candles.append(Candle(t, o, h, l, c, 380 + i))
        price = c
    pre_sweep_high = max(c.high for c in candles[-5:])
    sweep_open = price
    sweep_high = pre_sweep_high + 0.20
    sweep_close = pre_sweep_high - 0.30
    sweep_low = min(sweep_open, sweep_close) - 0.05
    candles.append(
        Candle(
            start + timedelta(minutes=94),
            sweep_open,
            sweep_high,
            sweep_low,
            sweep_close,
            520,
        )
    )
    price = sweep_close
    impulse_open = price - 0.20
    impulse_close = price - 1.40
    impulse_low = impulse_close - 0.05
    impulse_high = impulse_open + 0.02
    candles.append(
        Candle(
            start + timedelta(minutes=95),
            impulse_open,
            impulse_high,
            impulse_low,
            impulse_close,
            900,
        )
    )
    return candles


def _flat_range() -> list[Candle]:
    """Quiet, non-trending market with stable volume."""

    candles: list[Candle] = []
    start = datetime(2026, 5, 1, 10, 0)
    base = 2310.0
    for i in range(80):
        t = start + timedelta(minutes=i)
        o = base + (0.05 if i % 2 == 0 else -0.05)
        c = base - (0.05 if i % 2 == 0 else -0.05)
        h = max(o, c) + 0.02
        l = min(o, c) - 0.02
        candles.append(Candle(t, o, h, l, c, 200))
    return candles


def _outside_session() -> list[Candle]:
    """Bullish setup but at 03:00 UTC (outside London/NY)."""

    candles = _bullish_run()
    base = datetime(2026, 5, 1, 3, 0)
    return [
        Candle(base + timedelta(minutes=i), c.open, c.high, c.low, c.close, c.volume)
        for i, c in enumerate(candles)
    ]


def _expect(label: str, result: dict, *, signal: str, rr: float = 2.0) -> None:
    assert result["signal"] == signal, f"{label}: expected {signal}, got {result}"
    if signal == "NO TRADE":
        assert result["entry"] == 0 and result["SL"] == 0 and result["TP"] == 0, result
        print(f"[OK] {label}: NO TRADE")
        return
    entry = float(result["entry"])
    sl = float(result["SL"])
    tp = float(result["TP"])
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    assert risk > 0, f"{label}: zero risk in {result}"
    ratio = reward / risk
    assert abs(ratio - rr) < 0.05, f"{label}: R:R {ratio:.2f} != {rr}"
    if signal == "BUY":
        assert sl < entry < tp, f"{label}: ordering wrong {result}"
    else:
        assert tp < entry < sl, f"{label}: ordering wrong {result}"
    print(f"[OK] {label}: {signal} entry={entry} SL={sl} TP={tp} (R:R={ratio:.2f})")


def test_buy_signal() -> None:
    _expect("bullish confluence", analyze(_bullish_run()), signal="BUY")


def test_sell_signal() -> None:
    _expect("bearish confluence", analyze(_bearish_run()), signal="SELL")


def test_no_trade_range() -> None:
    _expect("flat range", analyze(_flat_range()), signal="NO TRADE")


def test_no_trade_session() -> None:
    _expect("outside session", analyze(_outside_session()), signal="NO TRADE")


def test_cli_csv_sample(capsys=None) -> None:
    """Run the CLI against the bundled MT5 sample CSV (a smooth uptrend)."""

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "ict_signal.py"),
        "--csv",
        str(ROOT / "data" / "XAUUSD_M1_sample.csv"),
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True, text=True)
    payload = json.loads(proc.stdout.strip())
    assert payload["signal"] in {"BUY", "SELL", "NO TRADE"}, payload
    assert proc.stdout.count("\n") == 1, "CLI must emit exactly one JSON line"
    print(f"[OK] CLI sample CSV: {payload}")


def test_cli_strict_json_only() -> None:
    """The CLI must print only the JSON, no commentary, on stdout."""

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "ict_signal.py"),
        "--csv",
        str(ROOT / "data" / "XAUUSD_M1_sample.csv"),
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True, text=True)
    text = proc.stdout.strip()
    json.loads(text)
    print("[OK] CLI emits only JSON on stdout")


def main() -> int:
    tests = [
        test_buy_signal,
        test_sell_signal,
        test_no_trade_range,
        test_no_trade_session,
        test_cli_csv_sample,
        test_cli_strict_json_only,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} ict_signal tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
