# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

This is a MetaTrader 5 Expert Advisor (MQL5) for XAUUSD M1 trading with a Python validation/replay script. The MQL5 source cannot be compiled or run on Linux—it requires the MetaTrader 5 desktop application (Windows). The only runnable component in a cloud VM is the Python validation script.

### Running the validation/test suite

```bash
python3 scripts/validate_package.py
```

This script uses **only Python standard library modules** (no pip dependencies). It performs:
1. EA source token validation (checks for 18 required MQL5 identifiers)
2. Brace-balance check on the `.mq5` source
3. Sample CSV data parsing and integrity checks (140 M1 bars)
4. Strategy replay that confirms at least one 3+ signal confluence setup exists in the sample data

Exit code 0 = all checks pass. Non-zero = validation failure with descriptive message.

### What cannot run in this environment

- **MQL5 compilation** requires MetaEditor (bundled with MetaTrader 5, Windows only).
- **Backtesting / live trading** requires a running MetaTrader 5 terminal connected to a broker demo account.

### Lint / static analysis

No linter is configured for MQL5. For the Python script, you can optionally run:
```bash
python3 -m py_compile scripts/validate_package.py
```

### File structure

| Path | Description |
|------|-------------|
| `Experts/XAUUSD_PriceAction_Confluence_EA.mq5` | Main EA source (688 lines of MQL5) |
| `data/XAUUSD_M1_sample.csv` | 140 M1 bars of XAUUSD sample data |
| `scripts/validate_package.py` | Python 3 package validator and strategy replay |
