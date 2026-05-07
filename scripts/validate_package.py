#!/usr/bin/env python3
"""Validate the MT5 EA package structure and sample M1 data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "Experts" / "XAUUSD_PriceAction_Confluence_EA.mq5"
DATA_PATH = ROOT / "data" / "XAUUSD_M1_sample.csv"


REQUIRED_TOKENS = [
    "OnInit",
    "OnTick",
    "iMA",
    "CopyRates",
    "CopyBuffer",
    "IsPinBar",
    "IsEngulfing",
    "IsFakeBreakout",
    "IsPullback",
    "IsSupportResistanceSignal",
    "CalculateStopLoss",
    "CalculateLotSize",
    "CountOpenTrades",
    "OBJ_ARROW_BUY",
    "OBJ_ARROW_SELL",
    "InpEMAPeriod",
    "InpTakeProfitToSLRatio",
    "InpMinimumSignals",
]


def validate_source() -> None:
    source = SOURCE_PATH.read_text(encoding="ascii")
    missing = [token for token in REQUIRED_TOKENS if token not in source]
    if missing:
        raise SystemExit(f"Missing required EA tokens: {missing}")

    balance = 0
    for line_no, line in enumerate(source.splitlines(), 1):
        balance += line.count("{") - line.count("}")
        if balance < 0:
            raise SystemExit(f"Brace balance went negative at line {line_no}")

    if balance != 0:
        raise SystemExit(f"Brace balance ended at {balance}")

    print(f"EA source required-token check passed ({len(REQUIRED_TOKENS)} tokens).")
    print("EA source brace-balance check passed.")


def load_sample_rows() -> list[dict[str, float | datetime]]:
    rows: list[dict[str, float | datetime]] = []
    previous_time: datetime | None = None

    for line_no, line in enumerate(DATA_PATH.read_text(encoding="ascii").splitlines(), 1):
        parts = line.split(",")
        if len(parts) != 9:
            raise SystemExit(f"CSV line {line_no} has {len(parts)} columns, expected 9")

        bar_time = datetime.strptime(parts[0] + " " + parts[1], "%Y.%m.%d %H:%M")
        open_price, high, low, close = map(float, parts[2:6])

        if previous_time and bar_time <= previous_time:
            raise SystemExit(f"CSV time is not increasing at line {line_no}")
        if high < max(open_price, close) or low > min(open_price, close):
            raise SystemExit(f"CSV OHLC range invalid at line {line_no}")

        rows.append(
            {
                "time": bar_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
            }
        )
        previous_time = bar_time

    if len(rows) < 100:
        raise SystemExit(f"CSV has only {len(rows)} bars; expected at least 100")

    print(f"Sample CSV parsed successfully ({len(rows)} M1 bars, 9 columns each).")
    return rows


def replay_sample(rows: list[dict[str, float | datetime]]) -> None:
    point = 0.01
    alpha = 2 / (20 + 1)
    emas: list[float] = []
    ema = float(rows[0]["close"])

    for row in rows:
        ema = alpha * float(row["close"]) + (1 - alpha) * ema
        emas.append(ema)

    def lowest(start: int, end: int) -> float:
        return min(float(row["low"]) for row in rows[start:end])

    def highest(start: int, end: int) -> float:
        return max(float(row["high"]) for row in rows[start:end])

    def pin(row: dict[str, float | datetime], direction: int) -> bool:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        candle_range = high - low
        body = max(abs(close - open_price), point)
        upper = high - max(open_price, close)
        lower = min(open_price, close) - low
        if candle_range <= point or body / candle_range * 100 > 35:
            return False
        opposite = candle_range * 0.30
        return (
            direction == 1
            and lower >= body * 2
            and upper <= opposite
            and close > open_price
        ) or (
            direction == -1
            and upper >= body * 2
            and lower <= opposite
            and close < open_price
        )

    def engulf(
        current: dict[str, float | datetime],
        previous: dict[str, float | datetime],
        direction: int,
    ) -> bool:
        current_body = abs(float(current["close"]) - float(current["open"]))
        previous_body = abs(float(previous["close"]) - float(previous["open"]))
        if previous_body <= 0 or current_body < previous_body:
            return False
        if direction == 1:
            return (
                float(previous["close"]) < float(previous["open"])
                and float(current["close"]) > float(current["open"])
                and float(current["open"]) <= float(previous["close"])
                and float(current["close"]) >= float(previous["open"])
            )
        return (
            float(previous["close"]) > float(previous["open"])
            and float(current["close"]) < float(current["open"])
            and float(current["open"]) >= float(previous["close"])
            and float(current["close"]) <= float(previous["open"])
        )

    def pullback(index: int, direction: int) -> bool:
        current = rows[index]
        if direction == 1:
            if (
                float(current["close"]) <= float(current["open"])
                or float(current["close"]) <= float(rows[index - 1]["high"])
            ):
                return False
            return all(
                float(rows[j]["close"]) < float(rows[j]["open"])
                or float(rows[j]["close"]) < float(rows[j - 1]["close"])
                for j in (index - 1, index - 2)
            )

        if (
            float(current["close"]) >= float(current["open"])
            or float(current["close"]) >= float(rows[index - 1]["low"])
        ):
            return False
        return all(
            float(rows[j]["close"]) > float(rows[j]["open"])
            or float(rows[j]["close"]) > float(rows[j - 1]["close"])
            for j in (index - 1, index - 2)
        )

    hits: list[tuple[str, str, int, str]] = []
    for index in range(35, len(rows)):
        row = rows[index]
        direction = 0
        close = float(row["close"])

        if close > emas[index] and emas[index] >= emas[index - 1]:
            direction = 1
        elif close < emas[index] and emas[index] <= emas[index - 1]:
            direction = -1
        if direction == 0:
            continue

        reasons: list[str] = []
        if pin(row, direction):
            reasons.append("pin bar")
        if engulf(row, rows[index - 1], direction):
            reasons.append("engulfing")

        support = lowest(max(0, index - 25), index)
        resistance = highest(max(0, index - 25), index)
        if direction == 1 and float(row["low"]) < support - 0.30 and close > support:
            reasons.append("fake breakout")
        if direction == -1 and float(row["high"]) > resistance + 0.30 and close < resistance:
            reasons.append("fake breakout")

        if pullback(index, direction):
            reasons.append("pullback")

        dynamic_support = lowest(max(0, index - 30), index)
        dynamic_resistance = highest(max(0, index - 30), index)
        if (
            direction == 1
            and float(row["low"]) <= dynamic_support + 1.20
            and close > dynamic_support
            and close > float(row["open"])
        ):
            reasons.append("support/resistance")
        if (
            direction == -1
            and float(row["high"]) >= dynamic_resistance - 1.20
            and close < dynamic_resistance
            and close < float(row["open"])
        ):
            reasons.append("support/resistance")

        if len(reasons) >= 3:
            direction_text = "BUY" if direction == 1 else "SELL"
            hits.append(
                (
                    datetime.strftime(row["time"], "%Y-%m-%d %H:%M"),  # type: ignore[arg-type]
                    direction_text,
                    len(reasons),
                    ", ".join(reasons),
                )
            )

    if not hits:
        raise SystemExit("Sample replay found no default 3-signal setups")

    print(f"Sample replay found {len(hits)} default 3+ signal setup(s).")
    for hit in hits[:5]:
        print("Sample hit:", " | ".join(map(str, hit)))


def main() -> None:
    validate_source()
    replay_sample(load_sample_rows())


if __name__ == "__main__":
    main()
