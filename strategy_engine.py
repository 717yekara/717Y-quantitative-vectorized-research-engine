"""
strategy_engine.py
-------------------
"Strategy Engine" box.

Each strategy is a small class that turns price data (+ Feature Engine
output) into entries/exits boolean DataFrames -- the vectorbt-native
signal format used throughout the rest of the pipeline. Registered in
STRATEGY_REGISTRY so the Signal Generator / backtester can select one
by name from config.StrategyConfig.name.

Single MA-crossover example with a small library of strategies plus 
support for parameter grids (the `range_split` / multi-window style 
sweep).

IMPORTANT -- hide_params=True: vectorbt's IndicatorFactory tags every
`.run()` output with a column level per parameter, EVEN for a single
scalar value (this is documented vectorbt behavior, not a bug on our
end -- see https://github.com/polakowo/vectorbt/issues/641). Left
alone, `vbt.MA.run(prices, 5, short_name="fast").ma` has columns like
(5, "AAPL") instead of just "AAPL", so entries/exits stop matching the
plain symbol-columned `prices` frame everywhere downstream --
portfolio_engine.size_positions() silently produces all-zero sizes
when it can't align weights against prices, which is exactly what
turns into "0 orders, 0 return" with no error raised.
`hide_params=True` suppresses that level for the single-strategy
classes below, since there's only ever one scalar value here -- there's
nothing useful the level would tell you. ma_crossover_grid(), at the
bottom of this file, deliberately keeps the parameter levels: that
function exists specifically to compare multiple window values against
each other, so the level is the whole point there.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd
import vectorbt as vbt

import config


class Strategy(ABC):
    name: str = "base"

    def __init__(self, cfg: config.StrategyConfig = config.DEFAULT_STRATEGY):
        self.cfg = cfg

    @abstractmethod
    def generate_signals(
        self,
        prices: pd.DataFrame,
        features: dict[str, pd.DataFrame],
        high: pd.DataFrame | None = None,
        low: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return (entries, exits) boolean DataFrames shaped like `prices`."""
        raise NotImplementedError


class MACrossoverStrategy(Strategy):
    """Fast MA crosses above slow MA -> long. Crosses below -> flat."""
    name = "ma_crossover"

    def generate_signals(
            self,
            prices: pd.DataFrame,
            features: dict[str, pd.DataFrame],
            high: pd.DataFrame | None = None,
            low: pd.DataFrame | None = None,
        ) -> tuple[pd.DataFrame, pd.DataFrame]:

        fast_ma = features["fast_ma"]
        slow_ma = features["slow_ma"]

        entries = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
        exits = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))

        return entries, exits


class RSIMeanReversionStrategy(Strategy):
    """Buy oversold (RSI < lower), sell/flat overbought (RSI > upper)."""
    name = "rsi_mean_reversion"

    def generate_signals(
            self,
            prices: pd.DataFrame,
            features: dict[str, pd.DataFrame],
            high: pd.DataFrame | None = None,
            low: pd.DataFrame | None = None,
        ) -> tuple[pd.DataFrame, pd.DataFrame]:

        rsi = features["rsi"]

        entries = rsi < self.cfg.rsi_lower
        exits = rsi > self.cfg.rsi_upper

        return entries, exits


class BollingerBreakoutStrategy(Strategy):
    """Buy a close above the upper band (breakout), exit back below middle band."""
    name = "bollinger_breakout"

    def generate_signals(
            self,
            prices: pd.DataFrame,
            features: dict[str, pd.DataFrame],
            high: pd.DataFrame | None = None,
            low: pd.DataFrame | None = None,
        ) -> tuple[pd.DataFrame, pd.DataFrame]:

        upper = features["bb_upper"]
        middle = features["bb_middle"]

        entries = prices > upper
        exits = prices < middle

        return entries, exits

class SuperTrendStrategy(Strategy):
    """
    SuperTrend trend-following strategy.
    Long while SuperTrend is bullish.
    Exit when SuperTrend flips bearish.
    """
    name = "supertrend"

    def generate_signals(
        self,
        prices: pd.DataFrame,
        features: dict[str, pd.DataFrame],
        high: pd.DataFrame | None = None,
        low: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:

        trend = features["supertrend_trend"]

        entries = (trend == 1) & (trend.shift(1) != 1)
        exits = (trend == -1) & (trend.shift(1) != -1)

        return entries, exits

def build_feature_set(
    prices: pd.DataFrame,
    cfg: config.StrategyConfig = config.DEFAULT_STRATEGY,
) -> dict[str, pd.DataFrame]:

    bb = vbt.BBANDS.run(
        prices,
        cfg.bb_window,
        alpha=cfg.bb_alpha,
        hide_params=[
            "window",
            "alpha",
        ],
    )

    return {
        "fast_ma": vbt.MA.run(prices, cfg.fast_window, hide_params=["window"]).ma,
        "slow_ma": vbt.MA.run(prices, cfg.slow_window, hide_params=["window"]).ma,
        "rsi": vbt.RSI.run(prices, cfg.rsi_window, hide_params=["window"]).rsi,
        "bb_upper": bb.upper,
        "bb_middle": bb.middle,
    }


STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    s.name: s for s in (MACrossoverStrategy, RSIMeanReversionStrategy, BollingerBreakoutStrategy)
}


def get_strategy(name: str, cfg: config.StrategyConfig = config.DEFAULT_STRATEGY) -> Strategy:
    try:
        return STRATEGY_REGISTRY[name](cfg)
    except KeyError:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY)}"
        )


# --------------------------------------------------------------------------
# Parameter-grid version of MA crossover (range_split / multi-window sweep), 
# used by the backtester's optimisation pass.
# --------------------------------------------------------------------------
def ma_crossover(price, fast_window, slow_window):

    fast_ma = vbt.MA.run(price, fast_window, hide_params=["window"]).ma
    slow_ma = vbt.MA.run(price, slow_window, hide_params=["window"]).ma

    entries = ((fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1)))
    exits = ((fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1)))

    return entries, exits

def ma_crossover_grid(price, fast_windows, slow_windows):

    entries = {}
    exits = {}

    for fast in fast_windows:
        for slow in slow_windows:

            if slow <= fast:
                continue

            fast_ma = vbt.MA.run(price, fast, hide_params=["window"])
            slow_ma = vbt.MA.run(price, slow, hide_params=["window"])

            key = (fast, slow)

            entries[key] = fast_ma.ma_crossed_above(slow_ma)
            exits[key] = fast_ma.ma_crossed_below(slow_ma)

    entries = pd.concat(entries, axis=1)
    exits = pd.concat(exits, axis=1)

    return entries, exits
