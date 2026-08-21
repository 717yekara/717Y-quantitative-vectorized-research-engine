"""
backtester.py
-------------
"VectorBT Backtester" box -- the second half of the SHARED pipeline:

    Prices -> Strategy -> Signals -> PortfolioAllocator -> Target Weights
        -> PositionSizer -> Share Sizes -> VectorBT

FIX (this file was previously out of sync with the live engine): the
allocator used to be fed the raw `entries` frame -- True only on the
exact bar a crossover fires. That silently over-allocates: if symbol A
entered 3 days ago and symbol B enters today, equal_weight(entries)
hands B 100% of capital today even though A is still holding its own
100% slice from 3 days ago (~200% gross exposure). The live engine
never had this bug -- SignalGenerator.latest_signals() has always sized
off the sustained "currently held" state, not raw entries.

This now calls signal_generator.signals_to_position_state(entries, exits)
to get that same sustained state before handing it to the allocator, so
backtest and live are structurally forced to agree -- same signal input,
same allocator, same sizer. `entries`/`exits` themselves are still
passed to vectorbt unchanged, since those are what should actually
trigger trades; only the SIZE at each triggered trade changes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt

import config
import portfolio_engine
from signal_generator import signals_to_position_state


def run_backtest(
    prices: pd.DataFrame,
    entries: pd.DataFrame,
    exits: pd.DataFrame,
    risk: config.RiskConfig = config.RISK,
    allocator=portfolio_engine.equal_weight,
    allocator_kwargs: dict | None = None,
    freq: str = "1D",
) -> vbt.Portfolio:
    """
    Signals -> sustained position state -> shared PortfolioAllocator/
    PositionSizer -> a fully-costed vectorbt Portfolio that just
    executes the share counts it's handed.

    allocator: one of portfolio_engine's allocator functions (or a
    matching custom callable). Defaults to equal_weight. Allocators that
    need `returns` (volatility_weight, risk_parity, kelly_weight) take
    them via allocator_kwargs, e.g.:

        run_backtest(prices, entries, exits,
                      allocator=portfolio_engine.volatility_weight,
                      allocator_kwargs={"returns": log_returns})
    """
    allocator_kwargs = allocator_kwargs or {}

    position_state = signals_to_position_state(entries, exits)

    weights_df = allocator(position_state, **allocator_kwargs)

    if not isinstance(weights_df, pd.DataFrame):
        raise TypeError(
            "Allocator must return a time-indexed DataFrame when given position_state. "
            f"Got: {type(weights_df)}"
        )

    # Set up event-driven order loop
    idx = list(prices.index)
    symbols = list(prices.columns)
    current_positions = pd.DataFrame(0, index=prices.index, columns=symbols, dtype=int)
    orders = pd.DataFrame(0, index=prices.index, columns=symbols, dtype=int)
    # For carry-forward logic
    prev_position = pd.Series(0, index=symbols, dtype=int)
    for i, ts in enumerate(idx):
        event_mask = entries.loc[ts] | exits.loc[ts]
        # Start from previous target/current position
        target = prev_position.copy()
        if event_mask.any():
            weights_row = weights_df.loc[ts]

            target_row = portfolio_engine.size_positions(
                weights_row,
                prices.loc[ts],
                risk.initial_capital,
                risk=risk,
            )
            # Only update symbols in event_mask; others remain at previous target
            target[event_mask] = target_row[event_mask]
        order = target - prev_position
        orders.loc[ts] = order
        current_positions.loc[ts] = prev_position + order
        prev_position = current_positions.loc[ts]

    # Ensure flat column indexes before handing data to VectorBT
    prices = prices.copy()
    if isinstance(prices.columns, pd.MultiIndex):
        prices.columns = prices.columns.get_level_values(-1)

    orders = orders.copy()
    if isinstance(orders.columns, pd.MultiIndex):
        orders.columns = orders.columns.get_level_values(-1)

    pf = vbt.Portfolio.from_orders(
        close=prices,
        size=orders,
        size_type="amount",
        init_cash=risk.initial_capital,
        group_by=False,
        cash_sharing=True,
        fees=risk.fees_bps / 10000,
        slippage=risk.slippage_bps / 10000,
        freq=freq,
    )

    return pf


def run_buy_and_hold(
    prices: pd.DataFrame,
    risk: config.RiskConfig = config.RISK,
    freq: str = "1D"
) -> vbt.Portfolio:

    n_assets = prices.shape[1]

    capital_per_asset = risk.initial_capital / n_assets

    # Buy quantity on first day
    size = pd.DataFrame(
        0,
        index=prices.index,
        columns=prices.columns
    )

    size.iloc[0] = (
        capital_per_asset / prices.iloc[0]
        ).astype(int)

    return vbt.Portfolio.from_orders(
        close=prices,
        size=size,
        size_type="amount",
        init_cash=risk.initial_capital,
        cash_sharing=True,
        group_by=np.ones(prices.shape[1], dtype=int),
        fees=risk.fees_bps / 10000,
        slippage=risk.slippage_bps / 10000,
        freq=freq,
    )

def run_buy_and_hold_per_symbol(
    prices: pd.DataFrame,
    risk: config.RiskConfig = config.RISK,
    freq: str = "1D",
) -> vbt.Portfolio:

    n_assets = prices.shape[1]
    capital_per_asset = risk.initial_capital / n_assets

    size = pd.DataFrame(
        0,
        index=prices.index,
        columns=prices.columns,
    )

    size.iloc[0] = (
        capital_per_asset / prices.iloc[0]
    ).astype(int)

    return vbt.Portfolio.from_orders(
        close=prices,
        size=size,
        size_type="amount",
        init_cash=capital_per_asset,   # $20k per stock
        cash_sharing=False,
        group_by=False,
        fees=risk.fees_bps / 10000,
        slippage=risk.slippage_bps / 10000,
        freq=freq,
    )

def run_parameter_sweep(
    prices: pd.DataFrame,
    fast_windows: list[int],
    slow_windows: list[int],
    n_splits: int = 4,
    risk: config.RiskConfig = config.RISK,
    allocator=portfolio_engine.equal_weight,
    allocator_kwargs: dict | None = None,
) -> vbt.Portfolio:
    """
    Walk-forward-style robustness check: split history into `n_splits`
    windows and backtest the whole fast/slow parameter grid on each --
    direct port of `range_split` + multi-window example, routed through 
    the same shared allocator/sizer as everything else (run_backtest 
    handles the entries -> position_state fix internally, so the grid/
    split case gets it for free).
    """
    import strategy_engine

    results = []
    splits = np.array_split(prices, n_splits)
    for split_idx, split_prices in enumerate(splits):
        for fast in fast_windows:
            for slow in slow_windows:
                if slow <= fast:
                    continue
                entries, exits = strategy_engine.ma_crossover(
                    split_prices,
                    fast,
                    slow
                )
                pf = run_backtest(
                    split_prices,
                    entries,
                    exits,
                    risk=risk,
                    allocator=allocator,
                    allocator_kwargs=allocator_kwargs,
                )
                
                results.extend(
                    {
                        "symbol": symbol[-1] if isinstance(symbol, tuple) else symbol,
                        "fast_window": fast,
                        "slow_window": slow,
                        "split_idx": split_idx,
                        "total_return": float(pf.total_return().loc[symbol]),
                        "sharpe": float(pf.sharpe_ratio().loc[symbol]),
                        "max_drawdown": float(pf.max_drawdown().loc[symbol])
                    }
                    for symbol in pf.total_return().index
                )

    return pd.DataFrame(results) 
