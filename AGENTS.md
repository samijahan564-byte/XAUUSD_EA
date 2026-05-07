# AGENTS.md

## Cursor Cloud specific instructions

This repository contains a MetaTrader 5 Expert Advisor (MQL5) for XAUUSD trading. The MQL5 source cannot be compiled or run on Linux — it requires the MetaTrader 5 desktop platform on Windows.

### What can run on the Cloud Agent VM

- **Validation script**: `python3 scripts/validate_package.py` — verifies the EA source (token presence, brace balance) and replays sample M1 data to check signal logic. Uses only Python standard library; no `pip install` needed.
- Python 3.9+ is required (pre-installed on the VM).

### What cannot run on the Cloud Agent VM

- The EA itself (`Experts/XAUUSD_PriceAction_Confluence_EA.mq5`) requires MetaTrader 5 / MetaEditor on Windows to compile and backtest.
- There are no web servers, databases, Docker services, or build systems in this repo.

### Key commands

| Task | Command |
|---|---|
| Validate EA source + sample data | `python3 scripts/validate_package.py` |

### Repository structure

- `Experts/` — MQL5 EA source code
- `data/` — Sample XAUUSD M1 bar data (CSV)
- `scripts/` — Python validation utilities
