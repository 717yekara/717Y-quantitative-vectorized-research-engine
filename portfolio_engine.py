"""
portfolio_engine.py
--------------------
The shared "PortfolioAllocator" + "PositionSizer" layer that sits
between Signals and everything downstream:

    Signals -> PortfolioAllocator -> Target Weights -> PositionSizer -> Share Sizes
                                                                              |
                                              +-------------------+----------+
                                              v                              v
                                        VectorBT backtest              IBKR live orders

This is the fix for the "two worlds" problem: previously
`vbt.Portfolio.from_signals(..., size_type="percent")` let vectorbt
decide position sizes internally during backtests (independent
per-column portfolios, each starting fresh with `init_cash`), while
`execution_engine.size_target_positions()` did its own, separate sizing
math for live trading. The two paths could silently diverge.

Now BOTH paths call the exact same functions below. A backtest is
"what would size_positions() have done with these signals, historically,
fed straight into vectorbt as fixed share counts" and live trading is
"what does size_positions() say to do right now, fed straight into
IBKR as an order". Same weights, same sizer, same caps.

Allocators
----------
Each allocator takes a "signals" input -- an in-position indicator,
typically the `entries` boolean frame/row from a Strategy (True/1 where
the strategy wants to be long) -- and returns TARGET WEIGHTS in [0, 1]
per symbol. A weight is the fraction of the *maximum allowed capital
slice* for that name, before position_size caps are re-applied (caps
are enforced once in `size_positions`, not duplicated in every
allocator).

    equal_weight(signals)                   -- implemented, default
    volatility_weight(signals, returns)     -- implemented
    risk_parity(signals, returns)           -- implemented (simplified/diagonal)
    kelly_weight(signals, returns)          -- implemented (simplified/diagonal)

All four accept either:
  - a DataFrame (index=datetime, columns=symbols) for backtesting the
    full history, or
  - a Series (index=symbols) for a single "as of right now" live call.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


# ==========================================================================
# Internal helpers
# ==========================================================================
# vectorbt convention (see strategy_engine.py's module docstring): every
# `.run()` call PREPENDS a new column level per parameter, in front of
# whatever levels the input already had. So a plain symbol frame's single
# level ("AAPL") becomes (fast_window, "AAPL") after one .run() call, or
# (fast_window, slow_window, split_idx, "AAPL") after two .run() calls on
# top of a range_split()'d frame -- but "symbol" (and any pre-existing
# levels like split_idx) always survive as the TRAILING levels, in the
# same relative order. The helpers below lean on that guarantee so
# equal_weight/size_positions keep working correctly whether they're
# fed a plain single-strategy backtest (no extra levels, after
# strategy_engine's hide_params=True) or a genuine parameter sweep
# (deliberately keeps the levels).
def _group_levels(columns: pd.Index) -> list | None:
    """All column levels except the trailing 'symbol' level, if any."""
    if isinstance(columns, pd.MultiIndex) and columns.nlevels > 1:
        return list(range(columns.nlevels - 1))
    return None


def _normalize(masked: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Rescale so active weights sum to 1 (per row, and per parameter
    group if the columns carry extra vectorbt parameter levels -- each
    group's symbols should compete for capital only with EACH OTHER,
    not with a different fast_window/slow_window combo's symbols)."""
    if isinstance(masked, pd.DataFrame):
        group_levels = _group_levels(masked.columns)
        if group_levels is not None:
            total = masked.T.groupby(level=group_levels).transform("sum").T
            return masked.div(total.replace(0, np.nan)).fillna(0.0)
        total = masked.sum(axis=1)
        return masked.div(total.replace(0, np.nan), axis=0).fillna(0.0)
    total = masked.sum()
    return masked / total if total > 0 else masked


def _apply_risk_caps(weights: pd.DataFrame | pd.Series,
                      risk: config.RiskConfig) -> pd.DataFrame | pd.Series:
    """
    Enforce per-name and gross-exposure caps. This is the ONE place
    caps are applied, whether the caller is the backtester or the
    execution engine. Gross exposure is capped per parameter group (see
    _normalize) so one grid combo can't be scaled down by another
    combo's exposure.
    """
    capped = weights.clip(upper=risk.max_position_pct)
    if isinstance(capped, pd.DataFrame):
        group_levels = _group_levels(capped.columns)
        if group_levels is not None:
            gross = capped.T.groupby(level=group_levels).transform("sum").T
        else:
            gross = capped.sum(axis=1)
        if isinstance(gross, pd.DataFrame):
            scale = (risk.max_gross_exposure / gross).clip(upper=1.0)
            scale = scale.replace([np.inf, -np.inf], 1.0).fillna(1.0)
            return capped.mul(scale)
        scale = (risk.max_gross_exposure / gross).clip(upper=1.0)
        scale = scale.replace([np.inf, -np.inf], 1.0).fillna(1.0)
        return capped.mul(scale, axis=0)
    gross = capped.sum()
    if gross > risk.max_gross_exposure and gross > 0:
        capped = capped * (risk.max_gross_exposure / gross)
    return capped


def _broadcast_prices(prices: pd.DataFrame, weights: pd.DataFrame) -> pd.DataFrame:
    """
    Align `prices` to `weights`'s columns even when `weights` carries
    extra leading parameter levels `prices` doesn't have (e.g. prices
    has plain "AAPL" columns but weights -- derived from a parameter
    sweep's entries/exits -- has (fast_window, slow_window, "AAPL")).

    Per vectorbt's convention, weights' TRAILING levels are exactly
    prices' own columns, just repeated once per extra leading
    parameter combo. We use that to tile prices across the extra levels
    instead of requiring an exact column match.
    """
    if list(weights.columns) == list(prices.columns):
        return prices.reindex(index=weights.index)

    n = prices.columns.nlevels
    if isinstance(weights.columns, pd.MultiIndex) and weights.columns.nlevels > n:
        trailing = weights.columns.droplevel(list(range(weights.columns.nlevels - n)))
    else:
        trailing = weights.columns

    lookup = {c: prices[c] for c in prices.columns}
    data = {}
    for full_col, trail_key in zip(weights.columns, trailing):
        key = trail_key
        if key not in lookup and not isinstance(key, tuple):
            key = (key,)  # single-level trailing key vs. tuple-keyed prices
        if key not in lookup and isinstance(key, tuple) and len(key) == 1:
            key = key[0]  # tuple trailing key vs. single-level prices
        data[full_col] = lookup[key]

    broadcast = pd.DataFrame(data, index=prices.index)
    broadcast.columns = weights.columns
    return broadcast.reindex(index=weights.index)


# ==========================================================================
# PortfolioAllocator: signals -> target weights
# ==========================================================================
def equal_weight(signals: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """
    Equal-weight across whichever symbols are actively signaled at each
    point in time. If 3 of 5 symbols are signaled long on a given day,
    each gets weight 1/3 that day (capital is reallocated across
    whichever names are active, not a fixed 1/5 regardless of signal).
    """
    active = signals.astype(bool).astype(float)
    return _normalize(active)


def volatility_weight(signals: pd.DataFrame | pd.Series,
                       returns: pd.DataFrame | pd.Series,
                       lookback: int = 20) -> pd.DataFrame | pd.Series:
    """
    Inverse-volatility weighting: active names with lower realized
    volatility get a larger slice of capital. `returns` should be a
    log-return series/frame aligned with `signals` (e.g. FeatureEngine's
    "log_returns" output for backtesting, or a rolling live equivalent).
    """
    vol = returns.rolling(lookback).std()
    inv_vol = 1.0 / vol.replace(0.0, np.nan)
    active = signals.astype(bool)
    masked = inv_vol.where(active, 0.0).fillna(0.0)
    return _normalize(masked)


def risk_parity(signals: pd.DataFrame | pd.Series,
                 returns: pd.DataFrame | pd.Series,
                 lookback: int = 60) -> pd.DataFrame | pd.Series:
    """
    Simplified / diagonal risk-parity: each active name is weighted so
    it contributes roughly equal risk, IGNORING cross-asset correlation.
    A textbook risk-parity solve needs the full covariance matrix and a
    numerical optimizer (e.g. minimizing variance of risk contributions
    subject to weights summing to 1); this diagonal version is the
    common practical approximation and mathematically collapses to
    inverse-volatility weighting. Swap in a covariance-aware optimizer
    here later without touching anything downstream.
    """
    return volatility_weight(signals, returns, lookback=lookback)


def kelly_weight(signals: pd.DataFrame | pd.Series,
                  returns: pd.DataFrame | pd.Series,
                  lookback: int = 100,
                  kelly_fraction: float = 0.5) -> pd.DataFrame | pd.Series:
    """
    Simplified per-symbol Kelly sizing using the continuous-return
    approximation f* = mean(returns) / variance(returns), scaled down by
    `kelly_fraction` (0.5 = half-Kelly by default, since full Kelly is
    aggressive and very sensitive to estimation error on noisy trailing
    windows). This is a rough starting point based on rolling return
    stats, not a substitute for a proper Kelly fit to realized trade
    win/loss statistics (e.g. from pf.trades.win_rate() / avg win/loss).
    Unlike the other allocators this one is intentionally NOT
    renormalized to sum to 1 -- Kelly is allowed to leave capital
    uninvested (or, before caps, over-invested) based on the edge
    estimate; `_apply_risk_caps` in `size_positions` still bounds it.
    """
    mu = returns.rolling(lookback).mean()
    var = returns.rolling(lookback).var()
    raw = (mu / var.replace(0.0, np.nan)).clip(lower=0.0) * kelly_fraction
    active = signals.astype(bool)
    return raw.where(active, 0.0).fillna(0.0)


ALLOCATOR_REGISTRY = {
    "equal_weight": equal_weight,
    "volatility_weight": volatility_weight,
    "risk_parity": risk_parity,
    "kelly_weight": kelly_weight,
}


# ==========================================================================
# PositionSizer: target weights -> share sizes
# ==========================================================================
def size_positions(
    weights: pd.DataFrame | pd.Series,
    prices: pd.DataFrame | pd.Series,
    capital: float,
    risk: config.RiskConfig = config.RISK,
) -> pd.DataFrame | pd.Series:
    """
    Turn target weights into whole-share sizes. This is the single
    function called by both the backtester (with full-history
    DataFrames) and the execution engine (with single-point-in-time
    Series) -- identical caps, identical rounding, identical output
    shape as the input.

    weights : target weight per symbol (0..1), pre-cap
    prices  : price per symbol, same shape/index/columns as `weights`
    capital : total account equity to allocate against
              (backtest: risk.initial_capital; live: current account equity)

    Returns whole share counts, same shape as `weights`.
    """

    if isinstance(weights, pd.DataFrame):
        if isinstance(prices, pd.Series):
            raise ValueError(
                "size_positions received full weights DataFrame with single price Series. "
                "Pass weights.loc[date] instead."
            )
        else:
            prices = _broadcast_prices(prices, weights)

    else:
        weights, prices = weights.align(prices, join="left")


    capped = _apply_risk_caps(weights, risk)
    dollar_alloc = capped * capital
    raw_shares = (dollar_alloc / prices.replace(0.0, np.nan)).fillna(0.0)

    if isinstance(raw_shares, pd.DataFrame):
        return np.floor(raw_shares).astype(int)
    return raw_shares.apply(lambda x: int(np.floor(x)))


def get_allocator(name: str):
    try:
        return ALLOCATOR_REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown allocator '{name}'. Available: {list(ALLOCATOR_REGISTRY)}")