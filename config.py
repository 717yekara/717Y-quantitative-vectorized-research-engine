"""
config.py
---------
Single source of truth for the whole pipeline:

    IBKR Historical Data
          v
    SQLite/PostgreSQL market database
          v
    Feature Engine
          v
    Strategy Engine
          v
    Signal Generator
          v
    VectorBT Backtester
          v
    Performance Analytics
          v
    IBKR Execution Engine

Everything else imports from here so you only ever change settings
in one place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import random

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
REPORT_DIR = ROOT_DIR / "reports"
for d in (DATA_DIR, LOG_DIR, REPORT_DIR):
    d.mkdir(exist_ok=True, parents=True)

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# Swap this one line to move from SQLite (local, zero-ops) to Postgres
# (multi-user / cloud / bigger universes) with no other code changes.
#
#   SQLite (default):
#       sqlite:///data/market.db
#   Postgres:
#       postgresql+psycopg2://user:password@host:5432/market_data
DATABASE_URL = os.environ.get(
    "QUANT_DB_URL", f"sqlite:///{DATA_DIR / 'market.db'}"
)

# --------------------------------------------------------------------------
# IBKR connection
# --------------------------------------------------------------------------
@dataclass
class IBKRConfig:
    host: str = os.environ.get("IBKR_HOST", "127.0.0.1")
    # 7497 = TWS paper, 7496 = TWS live, 4002 = IB Gateway paper, 4001 = IB Gateway live
    port: int = int(os.environ.get("IBKR_PORT", 4002))
    client_id: int = int(os.environ.get("IBKR_CLIENT_ID", random.randint(1, 1000)))
    account: str | None = os.environ.get("IBKR_ACCOUNT")  # e.g. "DU1234567"
    readonly: bool = os.environ.get("IBKR_READONLY", "true").lower() == "true"
    timeout: int = 30


IBKR = IBKRConfig()

# --------------------------------------------------------------------------
# Universe & data window
# --------------------------------------------------------------------------
UNIVERSE = ["AAPL", "AMZN", "MSFT", "NVDA", "GOOG"]

HISTORICAL_DURATION = "8 Y"         # how far back to pull on first load
BAR_SIZE = "1 day"                  # IBKR bar size string
WHAT_TO_SHOW = "ADJUSTED_LAST"      # dividend/split adjusted close
USE_RTH = True                      # regular trading hours only 

# --------------------------------------------------------------------------
# Strategy parameters (defaults; overridden per-run by Strategy Engine)
# --------------------------------------------------------------------------
@dataclass
class StrategyConfig:
    name: str = "ma_crossover"
    fast_window: int = 50
    slow_window: int = 200
    rsi_window: int = 14
    rsi_lower: float = 20.0
    rsi_upper: float = 90.0
    bb_window: int = 50
    bb_alpha: float = 3.0
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    use_vectorbt_pro_supertrend: bool = False


DEFAULT_STRATEGY = StrategyConfig()

# --------------------------------------------------------------------------
# Portfolio / risk / execution
# --------------------------------------------------------------------------
@dataclass
class RiskConfig:
    initial_capital: float = 100_000.0
    fees_bps: float = 1.0          # commission, in basis points of notional
    slippage_bps: float = 2.0
    max_position_pct: float = 0.25   # max weight in a single name
    max_gross_exposure: float = 1.0  # 1.0 = fully invested, no leverage
    kill_switch_drawdown: float = 0.20  # halt trading if DD exceeds this


RISK = RiskConfig()

# Dry-run is the safety default: the execution engine will log intended
# orders but never actually transmit them to IBKR unless this is False
# AND the caller explicitly passes dry_run=False as well (belt & suspenders).
DRY_RUN_DEFAULT = os.environ.get("QUANT_DRY_RUN", "true").lower() == "true"
