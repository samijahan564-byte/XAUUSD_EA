# XAUUSD Price Action Confluence EA for MetaTrader 5

This repository contains a complete MetaTrader 5 Expert Advisor (EA) for trading **XAUUSD / Gold on the 1-minute chart (M1)**.

The EA waits for several price action signals to agree with the current EMA trend before opening a trade. It is intended to be easy to study, customize, and backtest before any live use.

> Important: this EA is educational trading software, not financial advice. Always test on a demo account and with your broker's XAUUSD symbol before risking real money.

## Files included

| File | Purpose |
| --- | --- |
| `Experts/XAUUSD_PriceAction_Confluence_EA.mq5` | The MetaTrader 5 Expert Advisor source code. |
| `data/XAUUSD_M1_sample.csv` | Sample M1 XAUUSD bar data for importing into MT5/custom-symbol testing. |
| `scripts/validate_package.py` | Lightweight package validator for the EA source and sample data. |
| `scripts/ict_signal_analyzer.py` | Standalone ICT / Smart Money signal analyzer (Python). |
| `scripts/test_ict_signal_analyzer.py` | Tests for the ICT signal analyzer. |

## ICT / Smart Money Signal Analyzer

`scripts/ict_signal_analyzer.py` is a standalone Python script that analyzes XAUUSD M1 OHLC+Volume data using ICT (Inner Circle Trader) / Smart Money concepts and outputs a single JSON trade signal.

### Ten analysis layers

1. **Market Structure** — BOS (Break of Structure) and CHOCH (Change of Character) from swing highs/lows.
2. **Order Blocks** — Last opposing candle before an impulse move, validated by above-average volume.
3. **Liquidity & Stop Hunt** — Detects price sweeping previous highs/lows then reversing.
4. **Fair Value Gap (FVG)** — Three-candle imbalance zones with premium/discount filtering.
5. **Multi-Timeframe Confirmation** — 1M candles aggregated to 5M; 5M EMA trend must align with 1M entry.
6. **Candlestick Patterns** — Pin Bar, Engulfing, Hammer, Shooting Star detection.
7. **EMA & Momentum** — Short (9) and mid (21) EMA trend alignment, strong body, volume check.
8. **Session Filter** — Only signals during London (07–16 UTC) or New York (12–21 UTC) sessions.
9. **Risk Management** — SL behind last valid swing high/low with buffer; TP at minimum 1:2 R:R.
10. **Multi-Candle Confirmation** — Last 5 candles must align with direction, EMA, and momentum.

A trade signal is generated only when at least 4 layers confirm the same direction and no layer contradicts.

### Running the analyzer

```bash
# Default: reads data/XAUUSD_M1_sample.csv
python3 scripts/ict_signal_analyzer.py

# Custom CSV file
python3 scripts/ict_signal_analyzer.py path/to/candles.csv

# Read from stdin
cat candles.csv | python3 scripts/ict_signal_analyzer.py -

# Verbose diagnostics (logged to stderr, does not affect JSON output)
python3 scripts/ict_signal_analyzer.py --verbose
```

### Output format

```json
{"signal": "BUY", "entry": 2338.30, "SL": 2336.50, "TP": 2341.90}
```

If conditions are not met:

```json
{"signal": "NO TRADE", "entry": 0, "SL": 0, "TP": 0}
```

### Running the tests

```bash
cd scripts && python3 test_ict_signal_analyzer.py
```

## Strategy overview

Use this EA on an **XAUUSD M1 chart**. On every new 1-minute candle, the EA checks the last fully closed candle and scores five possible signal groups:

1. **Pin Bar** - a reversal candle with a long rejection wick.
2. **Engulfing** - a bullish or bearish two-candle engulfing pattern.
3. **Fake Breakout** - price breaks support/resistance but closes back inside the level.
4. **Pullback** - 2-3 candles retrace against the trend, then price confirms continuation.
5. **Support/Resistance** - price reacts at dynamic recent highs/lows or optional manual key levels.

A trade is allowed only when:

- The price is on the correct side of the EMA trend filter.
- The EMA slope agrees with the trend, if enabled.
- At least `InpMinimumSignals` signals align in the same direction. The default is `3`.
- The EA has fewer than `InpMaxOpenTrades` positions open. The default is `2`.

## How the EA decides direction

### EMA trend filter

Default setting: `InpEMAPeriod = 20`.

- **Buy trend**: last closed candle closes above the EMA, and the EMA is rising when `InpRequireEMASlope = true`.
- **Sell trend**: last closed candle closes below the EMA, and the EMA is falling when `InpRequireEMASlope = true`.
- If neither condition is true, the EA does nothing.

### Signal scoring

Each matching signal adds `1` point. The trend itself is a filter and does not add a point.

- **Pin Bar**
  - Buy: long lower shadow, small body, small upper shadow, bullish close.
  - Sell: long upper shadow, small body, small lower shadow, bearish close.
  - Main inputs: `InpPinShadowToBodyRatio`, `InpPinMaxBodyPercent`, `InpPinOppositeShadowPercent`.

- **Engulfing**
  - Buy: last candle is bullish and its body engulfs the previous bearish body.
  - Sell: last candle is bearish and its body engulfs the previous bullish body.
  - Main input: `InpEngulfBodyRatio`.

- **Fake Breakout**
  - Buy: candle breaks below recent support, then closes back above support.
  - Sell: candle breaks above recent resistance, then closes back below resistance.
  - Main inputs: `InpFakeBreakoutLookback`, `InpFakeBreakoutBufferPoints`.

- **Pullback**
  - Buy: 2-3 bearish/minor retracement candles form inside an uptrend, then a bullish candle confirms continuation.
  - Sell: 2-3 bullish/minor retracement candles form inside a downtrend, then a bearish candle confirms continuation.
  - Main input: `InpPullbackCandles`.

- **Support/Resistance**
  - Dynamic levels use recent highs/lows from `InpDynamicSRLookback`.
  - Manual levels are optional comma-separated prices in `InpSupportLevels` and `InpResistanceLevels`.
  - Main inputs: `InpUseDynamicSR`, `InpSRProximityPoints`, `InpSupportLevels`, `InpResistanceLevels`.

## Risk management

The EA calculates stop loss and take profit before sending an order.

- **Stop Loss**: placed behind the latest swing low for buys or swing high for sells.
- **SL buffer**: `InpSLBufferPoints` adds extra distance behind the swing.
- **Take Profit**: default is `1.5x` the stop-loss distance using `InpTakeProfitToSLRatio`.
- **Max trades**: `InpMaxOpenTrades = 2` limits simultaneous EA positions for the same symbol and magic number.
- **Lot sizing**
  - `InpUseAccountRisk = true`: lot size is calculated from account balance and `InpRiskPercent`.
  - `InpUseAccountRisk = false`: EA uses `InpFixedLot`.

Example: if the account balance is 1,000 USD and `InpRiskPercent = 1.0`, the EA targets about 10 USD risk per trade, adjusted to the broker's lot step.

## Alerts and chart markers

When a valid signal appears:

- A BUY or SELL arrow is drawn on the chart if `InpShowChartArrows = true`.
- A text label shows the signal score and matching reasons.
- A popup alert appears if `InpEnablePopupAlerts = true`.
- A sound plays if `InpEnableSoundAlerts = true`.

## Installation: attach the EA to an MT5 chart

1. Open MetaTrader 5.
2. Click **File > Open Data Folder**.
3. Open the `MQL5/Experts` folder.
4. Copy `Experts/XAUUSD_PriceAction_Confluence_EA.mq5` from this repository into that folder.
5. In MT5, open **MetaEditor**.
6. In MetaEditor, open `XAUUSD_PriceAction_Confluence_EA.mq5`.
7. Click **Compile**.
8. Return to MT5 and open **Navigator > Expert Advisors**.
9. Drag `XAUUSD_PriceAction_Confluence_EA` onto an **XAUUSD M1** chart.
10. In the EA settings, check **Allow Algo Trading**.
11. Turn on the main MT5 **Algo Trading** button.

If your broker uses a different gold symbol such as `XAUUSDm`, `GOLD`, or `XAUUSD.`, set `InpTradeSymbol` to that exact symbol name.

## Beginner configuration guide

Start conservative on a demo account.

### Basic settings

- `InpTradeSymbol`: your broker's gold symbol. Default is `XAUUSD`.
- `InpSignalTimeframe`: keep as `PERIOD_M1`.
- `InpMagicNumber`: unique ID for this EA's trades. Change it only if running multiple copies.
- `InpEnableAutoTrading`: set `false` if you want arrows/alerts without real orders.
- `InpMaxOpenTrades`: default `2`.

### Trend and signal settings

- `InpEMAPeriod`: default `20`. Higher values filter more trades.
- `InpRequireEMASlope`: default `true`. This avoids flat-market trades.
- `InpMinimumSignals`: default `3`. Raising it means fewer but stricter trades.

### Support and resistance settings

- `InpUseDynamicSR = true`: EA automatically uses recent highs and lows.
- `InpSupportLevels`: optional manual support levels, for example `2300.00,2292.50`.
- `InpResistanceLevels`: optional manual resistance levels, for example `2325.00,2338.20`.
- `InpSRProximityPoints`: how close price must be to a level to count as a reaction.

### Lot size settings

- For percentage risk: keep `InpUseAccountRisk = true` and set `InpRiskPercent`.
- For a fixed lot: set `InpUseAccountRisk = false` and set `InpFixedLot`.
- On a small demo account, start with `InpFixedLot = 0.01` or `InpRiskPercent = 0.25`.

### Alerts

- `InpShowChartArrows = true`: shows BUY/SELL markers.
- `InpEnablePopupAlerts = true`: shows MT5 popup alerts.
- `InpEnableSoundAlerts = true`: plays `InpSoundFile`.

## Backtesting with the included sample data

The sample file is `data/XAUUSD_M1_sample.csv`. It uses this MT5 bar format:

`Date,Time,Open,High,Low,Close,TickVolume,Volume,Spread`

To test with broker history:

1. Open MT5.
2. Press **Ctrl+R** to open **Strategy Tester**.
3. Select `XAUUSD_PriceAction_Confluence_EA`.
4. Select your broker's XAUUSD symbol.
5. Select **M1** timeframe.
6. Choose a date range with enough M1 data.
7. Use **Every tick based on real ticks** if your broker provides it, or **1 minute OHLC** for faster logic checks.
8. Click **Start**.

To test the included CSV as a custom symbol:

1. Open **View > Symbols**.
2. Create or select a custom symbol for XAUUSD testing.
3. Import bars from `data/XAUUSD_M1_sample.csv`.
4. Open Strategy Tester using that custom symbol on M1.
5. Run a short backtest and watch the chart for arrows, labels, entries, stop loss, and take profit.

For serious optimization, use real broker history instead of the small sample file.

## Optimization suggestions

In Strategy Tester, open the **Inputs** tab and enable optimization for these parameters:

- `InpEMAPeriod`: try values such as `10`, `20`, `50`.
- `InpMinimumSignals`: try `3`, `4`, `5`.
- `InpTakeProfitToSLRatio`: try `1.2`, `1.5`, `2.0`.
- `InpSLBufferPoints`: adjust for your broker's XAUUSD digits and spread.
- `InpPinShadowToBodyRatio`: higher values require stronger pin bars.
- `InpSRProximityPoints`: adjust for how tightly price must touch support/resistance.

Avoid optimizing only for profit. Also check drawdown, number of trades, average win/loss, and whether results remain stable across different months.

## How to monitor live/demo use

1. Keep the EA on an XAUUSD M1 chart.
2. Watch the top-right chart status. A smiling/active EA icon and enabled Algo Trading are required for live orders.
3. Check the **Experts** and **Journal** tabs for messages such as signal reasons, skipped trades, or broker order errors.
4. Check the chart arrows:
   - Green BUY arrow = valid buy setup.
   - Red SELL arrow = valid sell setup.
5. Check the **Trade** tab for open positions, SL, TP, lot size, and profit/loss.
6. If the EA signals but does not trade, check:
   - `InpEnableAutoTrading`.
   - MT5 Algo Trading button.
   - Broker minimum lot and stop-level rules.
   - `InpMaxOpenTrades`.
   - Whether the symbol name in `InpTradeSymbol` matches your broker.

## Practical safety checklist

- Test on demo first.
- Confirm your broker's XAUUSD point size, spread, tick value, and minimum lot.
- Use conservative risk.
- Avoid running during major news until you understand the EA's behavior.
- Keep enough free margin for gold volatility.
- Re-test settings whenever changing broker, account type, or symbol suffix.
