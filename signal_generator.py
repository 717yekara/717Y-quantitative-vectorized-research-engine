"""
signal_generator.py
--------------------
"Signal Generator" box -- produces SIGNALS, and nothing more. It does
NOT decide how much capital to put behind a signal; that's
PortfolioAllocator + PositionSizer's job (portfolio_engine.py), shared
by both the backtester and the execution engine:

    Prices -> Strategy -> Signals -> PortfolioAllocator -> Target Weights
        -> PositionSizer -> Share Sizes -> {VectorBT, IBKR}

Bridges the Strategy Engine (which speaks in vectorbt entries/exits
DataFrames over full history) and everything downstream that needs
"as of today, is the strategy signaling long in each symbol, yes/no?".

Also persists every generated signal to the database (SignalRecord)
so you have a full audit trail of what the system decided and when --
essential once real orders start flowing.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
import database
from strategy_engine import Strategy, build_feature_set, get_strategy

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Module-level (not a method) so backtester.py can call it directly on any
# entries/exits pair -- including the parameter-grid frames from
# strategy_engine.ma_crossover_grid() -- without needing a SignalGenerator
# or Strategy instance.
# --------------------------------------------------------------------------
def signals_to_position_state(entries: pd.DataFrame, exits: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse raw entries/exits CROSSOVER EVENTS (True only on the single
    bar where the cross happens) into a sustained "is this symbol
    currently held" state (1 from the entry bar through the bar before
    the next exit, 0 otherwise).

    This -- NOT the raw entries frame -- is what a PortfolioAllocator
    should be fed. Allocating off raw entries means the weight on any
    given day only reflects how many symbols happen to cross on THAT
    exact day, ignoring every symbol already held from an earlier entry.
    Concretely: if symbol A entered 3 days ago and symbol B enters today,
    equal_weight(entries) hands B 100% of capital today, even though A
    is still holding its own 100% slice from 3 days ago -- ~200% gross
    exposure, silently. equal_weight(position_state) correctly gives
    each of A and B 50% today, because both are seen as simultaneously
    held. This is also exactly what SignalGenerator.latest_signals()
    produces for live trading (just the last row) -- routing the
    backtester through this same function is what keeps the two paths
    from drifting apart again.
    """
    signal_events = entries.astype(int) - exits.astype(int)
    position = (
        signal_events
        .replace(0, np.nan)
        .ffill()
        .fillna(0.0)
        .infer_objects(copy=False)
        .astype("int64")
    )
    return position.clip(lower=0, upper=1)


class SignalGenerator:
    def __init__(self, strategy: Strategy | None = None):
        self.strategy = strategy or get_strategy(config.DEFAULT_STRATEGY.name)

    # ------------------------------------------------------------------
    def full_history_signals(self, prices: pd.DataFrame):
        """entries/exits over the whole price history -- the raw
        crossover events. Used to trigger vectorbt trades and to derive
        position_state(); NOT what an allocator should size off of."""
        features = build_feature_set(
            prices,
            self.strategy.cfg
        )

        return self.strategy.generate_signals(
            prices,
            features
        )
        

    # ------------------------------------------------------------------
    def position_state(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Full-history sustained "currently held" state -- the correct
        input for a PortfolioAllocator. See signals_to_position_state()."""
        entries, exits = self.full_history_signals(prices)
        return signals_to_position_state(entries, exits)

    # ------------------------------------------------------------------
    def latest_signals(self, prices: pd.DataFrame) -> pd.Series:
        """
        The last row of position_state() -- "is the strategy signaling
        long this symbol right now?" (1 = long, 0 = flat). This is a
        SIGNAL, not a position size or weight -- feed it into
        portfolio_engine.equal_weight() (or another allocator) to get
        target weights, then size_positions() for actual share counts.
        Same sequence run_backtest() uses internally on the full history.
        """
        position = self.position_state(prices)

        latest = position.iloc[-1]
        latest_ts = position.index[-1]
        for symbol, sig in latest.items():
            database.record_signal(symbol, self.strategy.name, latest_ts, int(sig))
        return latest
