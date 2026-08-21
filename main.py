"""
main.py
-------
Runs the full pipeline end to end:

    IBKR Historical Data -> DB -> Feature Engine -> Strategy Engine
        -> Signal Generator -> VectorBT Backtester -> Performance
        Analytics -> IBKR Execution Engine

Usage
-----
    # First-time / nightly data refresh (needs IB Gateway running):
    python main.py refresh

    # Research: backtest the configured universe + strategy, print stats:
    python main.py backtest

    # Parameter sweep / robustness check across windows and time splits:
    python main.py sweep

    # Generate today's signals and (dry-run by default) send orders:
    python main.py trade [--live]

Everything defaults to paper/dry-run. You must pass --live AND have
IBKR_READONLY=false / QUANT_DRY_RUN=false set to actually transmit
orders -- see execution_engine.py's safety model.
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

import config
import database
import portfolio_engine
from ibkr_data import refresh_market_data
from strategy_engine import get_strategy
from signal_generator import SignalGenerator
from backtester import (
    run_backtest, 
    run_buy_and_hold, 
    run_buy_and_hold_per_symbol,
    run_parameter_sweep
    )
from performance_analytics import (
    summary_stats,
    portfolio_level_stats, 
    save_tearsheet,
    compare_to_benchmark,
    portfolio_equity,
    true_portfolio_stats,
    )
from execution_engine import IBKRExecutionEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


def _allocator_and_kwargs(name: str, prices):
    """
    Resolve --allocator into (callable, kwargs). volatility_weight,
    risk_parity and kelly_weight all need a `returns` series -- computed
    here from prices so both backtest and trade commands stay in sync.
    """
    allocator = portfolio_engine.get_allocator(name)
    if name == "equal_weight":
        return allocator, {}
    log_returns = np.log(prices / prices.shift(1))
    return allocator, {"returns": log_returns}


def cmd_refresh(args):
    results = refresh_market_data()
    logger.info("Refreshed: %s", results)


def cmd_backtest(args):
    database.init_db()
    prices = database.load_bars(config.UNIVERSE)
    if prices.empty:
        logger.error("No data in DB yet -- run `python main.py refresh` first.")
        return

    strategy = get_strategy(config.DEFAULT_STRATEGY.name)
    sig_gen = SignalGenerator(strategy)
    entries, exits = sig_gen.full_history_signals(prices)

    allocator, allocator_kwargs = _allocator_and_kwargs(args.allocator, prices)
    
    pf = run_backtest(
        prices, 
        entries, 
        exits, 
        allocator=allocator, 
        allocator_kwargs=allocator_kwargs)

    equity, daily_returns = portfolio_equity(pf, prices)

    pf_hold = run_buy_and_hold(prices)

    pf_hold_symbols = run_buy_and_hold_per_symbol(prices)

    hold_equity, hold_returns = portfolio_equity(
        pf_hold,
        prices
    )

    print("\n=== Strategy Performance (per symbol) ===")
    print(summary_stats(pf))
    print("\n=== Buy & Hold Performance (per symbol) ===")
    print(summary_stats(pf_hold_symbols))
    print("\n=== True Portfolio Stats ===")
    stats = true_portfolio_stats(equity, daily_returns, pf)
    print(f"Final equity:   ${stats['final_equity']:,.2f}")
    print(f"Total return:   {stats['total_return']:.2%}")
    print(f"Sharpe ratio:   {stats['sharpe_ratio']:.4f}")
    print(f"Max drawdown:   {stats['max_drawdown']:.2%}")
    print(f"Total orders:   {int(stats['total_orders'])}")
    print(f"Total trades:   {int(stats['total_trades'])}")
    print("\n=== Buy & Hold True Portfolio Stats ===")
    hold_stats = true_portfolio_stats(
        hold_equity,
        hold_returns,
        pf_hold
        )
    print(f"Final equity:   ${hold_stats['final_equity']:,.2f}")
    print(f"Total return:   {hold_stats['total_return']:.2%}")
    print(f"Sharpe ratio:   {hold_stats['sharpe_ratio']:.4f}")
    print(f"Max drawdown:   {hold_stats['max_drawdown']:.2%}")
    print(f"Total orders:   {int(hold_stats['total_orders'])}")
    print(f"Total trades:   {int(hold_stats['total_trades'])}")

    path = save_tearsheet(
        pf, 
        equity=equity,
        returns=daily_returns,
        name=strategy.name
    )
    print(f"\nTearsheet saved to {path}")


def cmd_sweep(args):
    database.init_db()
    prices = database.load_bars(config.UNIVERSE)
    if isinstance(prices.columns, pd.MultiIndex):
        prices.columns = prices.columns.get_level_values(0)
    if prices.empty:
        logger.error("No data in DB yet -- run `python main.py refresh` first.")
        return

    results = run_parameter_sweep(
        prices,
        fast_windows=[20, 50, 100],
        slow_windows=[100, 200, 300, 400]
        )

    results = results[
        np.isfinite(results["sharpe"])
        ]

    results = results[
        results["total_return"] != 0
        ]

    results = results.replace(
        [np.inf, -np.inf],
        np.nan
    )

    results = results.dropna()

    results["score"] = (
        results["sharpe"]
        + results["total_return"]
        + results["max_drawdown"]
    )


    print("=== Parameter sweep results ===")

    print(
        results.sort_values(
            ["score", "sharpe"],
            ascending=[False, False]
            ).head(10) 
        )


def cmd_trade(args):
    database.init_db()
    prices = database.load_bars(config.UNIVERSE)
    if prices.empty:
        logger.error("No data in DB yet -- run `python main.py refresh` first.")
        return

    strategy = get_strategy(config.DEFAULT_STRATEGY.name)
    sig_gen = SignalGenerator(strategy)
    latest_signals = sig_gen.latest_signals(prices)
    latest_prices = prices.iloc[-1]

    allocator, allocator_kwargs = _allocator_and_kwargs(args.allocator, prices)

    dry_run = not args.live
    with IBKRExecutionEngine(dry_run=dry_run) as engine:
        current_shares = engine.get_current_positions()

        # quick backtest just to evaluate the kill switch before trading --
        # uses the SAME allocator the trade below will use (not the
        # default), so this is a true apples-to-apples check
        entries, exits = sig_gen.full_history_signals(prices)

        pf = run_backtest(
            prices, 
            entries, 
            exits, 
            allocator=allocator, 
            allocator_kwargs=allocator_kwargs
        )

        if engine.check_kill_switch(pf):
            return

        live_allocator_kwargs = dict(allocator_kwargs)
        if "returns" in live_allocator_kwargs:
            live_allocator_kwargs["returns"] = live_allocator_kwargs["returns"].iloc[-1]  # latest row -> Series

        # live equity should come from IBKR's account summary in a real
        # deployment; using configured initial_capital as a placeholder
        # equity reference until that account-sync call is wired in
        target_shares = engine.target_shares_from_signals(
            latest_signals, latest_prices, equity=config.RISK.initial_capital,
            allocator=allocator, allocator_kwargs=live_allocator_kwargs,
        )

        orders = engine.build_orders(target_shares, current_shares)
        if orders.empty:
            logger.info("No rebalancing needed today.")
            return

        results = engine.submit_orders(orders)
        for r in results:
            logger.info(r)


def main():
    parser = argparse.ArgumentParser(description="IBKR quant pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    allocator_choices = list(portfolio_engine.ALLOCATOR_REGISTRY)

    sub.add_parser("refresh", help="Pull latest historical data from IBKR into the DB")

    backtest_p = sub.add_parser("backtest", help="Run the configured strategy across the universe")
    backtest_p.add_argument("--allocator", choices=allocator_choices, default="equal_weight",
                                help="PortfolioAllocator to use (default: equal_weight)")

    sub.add_parser("sweep", help="Parameter grid / time-split robustness sweep")

    trade_p = sub.add_parser("trade", help="Generate signals and (dry-run by default) trade")
    trade_p.add_argument("--live", action="store_true",
                            help="Actually transmit orders to IBKR (default: dry run)")
    trade_p.add_argument("--allocator", choices=allocator_choices, default="equal_weight",
                            help="PortfolioAllocator to use (default: equal_weight) -- "
                                "must match what you backtested with")

    args = parser.parse_args()
    {
        "refresh": cmd_refresh,
        "backtest": cmd_backtest,
        "sweep": cmd_sweep,
        "trade": cmd_trade,
    }[args.command](args)


if __name__ == "__main__":
    main()