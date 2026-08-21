"""
performance_analytics.py
-------------------------
"Performance Analytics" box.

Turns a vectorbt Portfolio into the numbers a PM actually asks for
(Sharpe, Sortino, Calmar, max drawdown, win rate, exposure) plus the
plots (per-symbol return bar chart, order stats), and saves a CSV 
tearsheet per run to REPORT_DIR for record-keeping.
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
import vectorbt as vbt

import config

logger = logging.getLogger(__name__)


def summary_stats(pf: vbt.Portfolio) -> pd.DataFrame:
    """One row per symbol with the metrics that matter for a go/no-go decision."""
    stats = pd.DataFrame({
        "total_return": pf.total_return(),
        "sharpe_ratio": pf.sharpe_ratio(),
        "sortino_ratio": pf.sortino_ratio(),
        "calmar_ratio": pf.calmar_ratio(),
        "max_drawdown": pf.max_drawdown(),
        "win_rate": pf.trades.win_rate(),
        "total_trades": pf.trades.count(),
        "avg_trade_return": pf.trades.returns.mean(),
    })

    # Flatten duplicated VectorBT wrapper index
    if isinstance(stats.index, pd.MultiIndex):
        stats.index = stats.index.get_level_values(-1)
        stats.index.name = "symbol"
        
    return stats


def portfolio_level_stats(pf: vbt.Portfolio) -> pd.Series:
    """Aggregated, group_by=True equivalents of the 
    pf.orders.stats(group_by=True) / pf.sharpe_ratio()
    calls."""
    return pd.Series({
        "total_return": pf.total_return().mean(),
        "sharpe_ratio": pf.sharpe_ratio().mean(),
        "max_drawdown": pf.max_drawdown().mean(),
        "total_orders": pf.orders.count().sum(),
    })

def portfolio_equity(pf, prices, initial_capital=None):
    if initial_capital is None:
        initial_capital = config.RISK.initial_capital

    # Market value of current positions
    position_value = pf.asset_value()
    if isinstance(position_value, pd.DataFrame):
        gross_exposure = position_value.sum(axis=1)
    else:
        gross_exposure = position_value

    # Reconstruct single portfolio cash balance from orders
    orders = pf.orders.records_readable.copy()

    orders["cash_flow"] = np.where(
        orders["Side"] == "Buy",
        -(orders["Size"] * orders["Price"] + orders["Fees"]),
        (orders["Size"] * orders["Price"] - orders["Fees"])
    )

    cash_flow = (
        orders
        .groupby("Timestamp")["cash_flow"]
        .sum()
        .reindex(prices.index)
        .fillna(0)
        .cumsum()
    )

    cash = initial_capital + cash_flow

    # True portfolio NAV
    equity = cash + gross_exposure
    returns = equity.pct_change().fillna(0)

    return equity, returns

def true_portfolio_stats(equity, returns, pf):
    """
    Statistics based on real $100k portfolio NAV.
    """
    return pd.Series({
        "final_equity": equity.iloc[-1],
        "total_return": equity.iloc[-1] / equity.iloc[0] - 1,
        "sharpe_ratio": (
            returns.mean() / returns.std() * np.sqrt(252)
            if returns.std() != 0 else np.nan
        ),
        "max_drawdown": (
            equity / equity.cummax() - 1
        ).min(),
        "total_orders": pf.orders.count().sum(),
        "total_trades": pf.trades.count().sum(),
    })

def compare_to_benchmark(pf: vbt.Portfolio, pf_hold: vbt.Portfolio) -> pd.DataFrame:
    return pd.DataFrame({
        "strategy_return": pf.total_return(),
        "buy_and_hold_return": pf_hold.total_return(),
        "excess_return": pf.total_return() - pf_hold.total_return(),
    })


def barplot_returns(pf: vbt.Portfolio):
    """Direct port of pf.total_return().groupby("symbol").mean().vbt.barplot()."""
    return pf.total_return().vbt.barplot()


def save_tearsheet(
    pf: vbt.Portfolio,
    name: str = "run",
    equity=None,
    returns=None
) -> str:
    """
    Persist a CSV tearsheet to REPORT_DIR; returns the file path.

    Optional equity and returns are the true portfolio-level NAV
    reconstructed from portfolio_equity().
    """

    stats = summary_stats(pf)

    if equity is not None:
        portfolio_stats = pd.DataFrame({
            "portfolio_final_equity": [equity.iloc[-1]],
            "portfolio_total_return": [
                (equity.iloc[-1] / equity.iloc[0]) - 1
            ],
            "portfolio_mean_daily_return": [
                returns.mean() if returns is not None else np.nan
            ],
            "portfolio_daily_volatility": [
                returns.std() if returns is not None else np.nan
            ],
        })

        # save portfolio stats below symbol stats
        stats = pd.concat(
            [
                stats,
                portfolio_stats.set_index(
                    pd.Index(["PORTFOLIO"])
                )
            ],
            axis=0
        )

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = config.REPORT_DIR / f"{name}_{ts}.csv"

    stats.to_csv(path)

    logger.info("Saved tearsheet to %s", path)

    return str(path)
