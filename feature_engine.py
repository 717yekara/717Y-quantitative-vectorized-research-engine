"""
feature_engine.py
-----------------
"Feature Engine" box.

Turns raw OHLCV (from the market database) into the indicators the
Strategy Engine and Signal Generator consume. Built on vectorbt's
indicator factory so the exact same rolling calculations used here are
also what powers the backtester -- no train/serve skew between
research and the live signal path.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt

import config

class FeatureEngine:
    def __init__(self, strategy_cfg: config.StrategyConfig = config.DEFAULT_STRATEGY):
        self.cfg = strategy_cfg

    # ------------------------------------------------------------------
    def compute(
            self,
            prices: pd.DataFrame,
            high: pd.DataFrame | None = None,
            low: pd.DataFrame | None = None,
    ) -> dict[str, pd.DataFrame]:
        """
        prices: wide DataFrame, columns = symbols, index = datetime, values = close.
        Returns a dict of feature frames, each shaped like `prices`.
        """
        cfg = self.cfg
        fast_ma = vbt.MA.run(prices, cfg.fast_window, short_name="fast").ma
        slow_ma = vbt.MA.run(prices, cfg.slow_window, short_name="slow").ma
        rsi = vbt.RSI.run(prices, cfg.rsi_window).rsi
        bb = vbt.BBANDS.run(prices, cfg.bb_window, alpha=cfg.bb_alpha)
        atr = None
        supertrend = None
        supertrend_trend = None

        if cfg.use_vectorbt_pro_supertrend:
            if high is None or low is None:
                raise ValueError(
                    "SuperTrend requires High and Low price data."
                )

            # -------------------------------------------------
            # VectorBT Pro implementation
            # NOTE:
            # The exact output names (atr, supertrend, trend)
            # depend on the VectorBT Pro version.
            # Verify when Pro is installed.
            # -------------------------------------------------

            st = vbt.SuperTrend.run(
                high=high,
                low=low,
                close=prices,
                period=cfg.supertrend_period,
                multiplier=cfg.supertrend_multiplier,
            )

            atr = st.atr
            supertrend = st.supertrend
            supertrend_trend = st.trend

        log_returns = np.log(prices / prices.shift(1))
        realized_vol = log_returns.rolling(20).std() * np.sqrt(252)

        features = {
            "close": prices,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "rsi": rsi,
            "bb_upper": bb.upper,
            "bb_middle": bb.middle,
            "bb_lower": bb.lower,
            "log_returns": log_returns,
            "realized_vol_ann": realized_vol,
        }

        if cfg.use_vectorbt_pro_supertrend:
            features.update({
                "atr": atr,
                "supertrend": supertrend,
                "supertrend_trend": supertrend_trend,
            })

        return features

    # ------------------------------------------------------------------
    def latest_snapshot(self, prices: pd.DataFrame) -> pd.DataFrame:
        """One row per symbol with the most recent value of every feature --
        what the live Signal Generator looks at each morning."""
        features = self.compute(prices)
        rows = {name: frame.iloc[-1] for name, frame in features.items()}
        return pd.DataFrame(rows)
