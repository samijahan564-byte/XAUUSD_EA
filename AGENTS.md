# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

This is a MetaTrader 5 Expert Advisor (EA) for XAUUSD gold trading. The core `.mq5` source cannot be compiled or run in a Linux cloud environment — it requires MetaTrader 5 on Windows.

### What can run in the cloud environment

The only executable component is the Python validation script:

```
python3 scripts/validate_package.py
```

This script uses **only the Python standard library** (no pip dependencies). It performs:

1. **Source token check** — verifies the EA source contains all required function/symbol tokens.
2. **Brace balance check** — ensures curly braces are balanced in the `.mq5` source.
3. **CSV data validation** — parses `data/XAUUSD_M1_sample.csv` and checks OHLC integrity.
4. **Signal replay** — replays sample data through simplified price-action logic and confirms at least one 3+ signal setup is found.

### Lint / test / build

- **Lint**: No linter is configured. You can optionally run `python3 -m py_compile scripts/validate_package.py` to check syntax.
- **Test**: `python3 scripts/validate_package.py` — exit code 0 means all checks pass.
- **Build**: Not applicable in this environment (MQL5 compilation requires MetaEditor on Windows).

### Notes

- Python 3.10+ is required (the script uses `list[dict]` and `X | Y` type union syntax).
- There are no dependency files (`requirements.txt`, `pyproject.toml`, etc.) because the script uses only stdlib.
- There is no update script needed — no dependencies to install.
