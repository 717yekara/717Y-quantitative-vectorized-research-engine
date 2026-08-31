# 717Y Quantitative Vectorized Research Engine

> A public technical preview of an institutional-style vectorized systematic trading architecture.

**Public technical preview — deliberately limited.**

This repository is a technical snapshot of a larger quantitative research and
trading architecture. It is intended to demonstrate engineering, quantitative
research, portfolio construction, risk management, validation, and execution
design.

Proprietary research, datasets, credentials, production configurations,
monitoring infrastructure, and other components of the broader system are
intentionally not included.

This repository should not be interpreted as a claim of production readiness,
investment performance, or a complete representation of the underlying research
environment.
---

Overview

The 717Y Quantitative Research Engine is a Python-based systematic trading and quantitative research framework designed around a separation of responsibilities between:

Research → Validation → Portfolio Construction → Risk → Execution

The objective is not simply to backtest trading signals.

The system is designed to answer a more complete question:

Can a systematic trading idea be researched, validated, sized, risk-controlled, and ultimately translated into executable orders through one coherent architecture?

The public repository provides a technical snapshot of that architecture.

```
                    717Y QUANTITATIVE VECTORIZED RESEARCH ENGINE
                         ┌─────────────────────┐
                         │    IBKR Market Data │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Market Database   │
                         │   SQLite / Postgres │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Feature Engine    │
                         │ MA / RSI / BB / Vol │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │  Strategy Engine    │
                         │ Signal generation   │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Signal Generator  │
                         │ Position state      │
                         └──────────┬──────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────┐
                     │ Portfolio Allocator / Sizer  │
                     │                              │
                     │ Equal Weight                 │
                     │ Volatility Weight            │
                     │ Risk Parity                  │
                     │ Kelly Weight                 │
                     └──────────────┬───────────────┘
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                         ▼                     ▼
                ┌────────────────┐    ┌─────────────────┐
                │ VectorBT        │    │ IBKR Execution  │
                │ Backtesting     │    │ Engine          │
                └───────┬────────-┘    └────────┬────────┘
                        │                       │
                        └──────────┬───────────-┘
                                   ▼
                         ┌─────────────────────┐
                         │ Performance / Risk  │
                         │ Analytics & Reports │
                         └─────────────────────┘
```
---

Architectural principle: research and execution share the same portfolio logic

One of the central design decisions is that portfolio allocation and position sizing are not left to the backtesting framework or independently reimplemented in the execution layer.

The architecture is:
```text
Prices
   ↓
Strategy
   ↓
Signals
   ↓
PortfolioAllocator
   ↓
Target Weights
   ↓
PositionSizer
   ↓
Whole-share positions
   ↓
   ┌────────────────────────────────┐
   ↓                                ↓
   VectorBT                        IBKR
   Backtest                      Execution
```

This is important because an apparently small difference between backtest sizing and live sizing can create a significant difference between research results and actual implementation.

The backtester therefore uses the same portfolio construction and sizing functions that the execution engine uses.

VectorBT is responsible for simulating execution, rather than independently deciding how capital should be allocated.

---

Research and validation

The research layer includes more than a single historical backtest.

The public notebooks demonstrate components of a broader validation workflow including:

* parameter-grid evaluation
* time-based parameter sweeps
* walk-forward optimization
* in-sample / out-of-sample testing
* winning-parameter tracking
* parameter stability analysis
* Sharpe decay analysis
* pooled cross-symbol validation
* drawdown analysis
* return-distribution analysis
* VaR / CVaR analysis
* exposure sanity checks
* concurrent-position analysis
* allocator comparison

Walk-forward validation

The walk-forward framework separates historical data into sequential windows:

```
                    Fold 0
        ┌──────────────────────┐
        │       IN SAMPLE      │ OOS
        │                      │
        └──────────────────────┴─────┐
                                     │
                         Fold 1.     │
              ┌──────────────────────┴─────┐
              │       IN SAMPLE            │ OOS
              │                            │
              └────────────────────────────┴─────┐
                                      Fold 2.    │
                           ┌─────────────────────┴─────────────┐
                           │       IN SAMPLE                   │ OOS
                           │                                   │
                           └───────────────────────────────────┘
```

Parameters are selected using the in-sample period and then evaluated on the subsequent out-of-sample period.

The purpose is to examine whether observed strategy performance survives when the data used to select parameters is no longer available to the optimization process.

---

Parameter stability

The research layer also records the winning parameters selected in each walk-forward fold.

Rather than asking only:

“What parameter produced the highest Sharpe?”

the framework can also examine:

“How stable is the selected parameter through time?”

For example, the system tracks the modal winning fast_window and slow_window, their frequency across folds, and their observed ranges.

This provides a second dimension of robustness analysis beyond headline backtest performance.

---

Portfolio construction

The portfolio layer supports multiple allocation methodologies:

Allocator	Description
equal_weight	Allocates capital evenly among active signals
volatility_weight	Inverse-volatility allocation
risk_parity	Simplified diagonal risk-parity implementation
kelly_weight	Half-Kelly allocation based on rolling return statistics

The allocator produces target weights.

The position sizer then converts those weights into executable whole-share quantities while enforcing portfolio-level risk constraints.

---

Risk controls

Risk constraints are centralized in the portfolio sizing layer.

Current controls include:

* maximum position size
* maximum gross exposure
* kill-switch drawdown threshold
* whole-share position sizing
* exposure monitoring
* concurrent-position monitoring

The objective is to prevent risk rules from being implemented differently in research and execution.

For example:
```text
Target Weight
      ↓
Position Sizer
      ↓
Position Cap
      ↓
Gross Exposure Cap
      ↓
Whole Shares
      ↓
Executable Position
```
---

Execution architecture

The execution layer is designed around Interactive Brokers.

The execution engine:

1. receives strategy signals
2. obtains current portfolio information
3. uses the shared portfolio allocator
4. applies the shared position-sizing logic
5. constructs target orders
6. supports dry-run operation
7. can submit orders to IBKR

The system is intentionally dry-run by default.

Paper trading should be used before any live deployment.

---

Auditability

Signals and intended orders are designed to be persisted to the database.

This creates an audit trail between:
```text
Market Data
     ↓
Features
     ↓
Strategy Decision
     ↓
Signal
     ↓
Portfolio Allocation
     ↓
Position Size
     ↓
Order
```

This separation makes it possible to inspect not only what happened, but also why the system intended to do it.

---

Repository structure
```text
Technical Strategies/
│
├── backtester.py
├── config.py
├── database.py
├── execution_engine.py
├── feature_engine.py
├── ibkr_data.py
├── main.py
├── performance_analytics.py
├── portfolio_engine.py
├── signal_generator.py
├── strategy_engine.py
│
├── research.ipynb
├── portfolio_analysis.ipynb
├── walk_forward.ipynb
├── walk_forward_optimization.ipynb
│
├── requirements.txt
├── README.md
│
└── data/
    └── market.db          # local / ignored
```

The notebooks are intentionally included because they show the research process, rather than presenting the project as a black-box library.

---

Key modules

Module	Responsibility
config.py	Central configuration for database, IBKR, strategy and risk parameters
database.py	SQLAlchemy models and market-data / audit persistence
ibkr_data.py	Historical market-data retrieval through IBKR
feature_engine.py	Technical features and derived market data
strategy_engine.py	Strategy implementations and parameter grids
signal_generator.py	Converts strategy output into position-state signals
portfolio_engine.py	Portfolio allocation and position sizing shared by backtest and execution
backtester.py	VectorBT-based historical simulation
performance_analytics.py	Performance, risk and drawdown analytics
execution_engine.py	IBKR order construction and execution workflow
main.py	Command-line orchestration

---

Example workflow

Refresh market data
```bash
python main.py refresh
```
Run a backtest
```bash
python main.py backtest
```
Run a parameter sweep
```bash
python main.py sweep
```
Generate trading instructions
```bash
python main.py trade
```
Select an allocator
```bash
python main.py backtest --allocator volatility_weight
python main.py trade --allocator volatility_weight
```
---

Safety model

The execution layer is designed to fail safely during development.

* Dry-run mode is enabled by default.
* IBKR paper trading should be used before live deployment.
* A drawdown kill switch can block new orders.
* Position-level exposure is capped.
* Gross portfolio exposure is capped.
* Signals and orders are recorded for auditability.

This repository is a research and engineering framework, not a production-ready deployment package.

Production deployment would additionally require infrastructure for monitoring, alerting, reconciliation, operational controls, failure recovery, secrets management, and independent validation.

---

Technology

* Python 3.12
* VectorBT
* pandas
* NumPy
* SciPy
* SQLAlchemy
* SQLite / PostgreSQL
* IBKR / ib_async
* Matplotlib
* Plotly

Tested environment:

Python       3.12.12
VectorBT     0.28.4
ib_async     2.1.0
pandas       2.3.3
NumPy        2.3.5
SciPy        1.16.3
SQLAlchemy   2.0.51

---

Public repository scope

This repository should be viewed as a technical preview rather than a complete representation of the underlying research environment.

It intentionally excludes:

* proprietary market datasets
* credentials and account information
* production infrastructure
* private research
* proprietary strategy research
* live trading configurations
* operational monitoring infrastructure

The purpose of publishing the repository is to demonstrate the engineering principles and quantitative research methodology behind the system.

---

Roadmap

Potential future development includes:

* covariance-aware risk parity
* richer portfolio optimization
* transaction-cost modeling
* market-impact modeling
* more sophisticated execution algorithms
* experiment tracking
* automated research reports
* portfolio-level attribution
* factor exposure analysis
* regime detection
* more extensive statistical validation
* production monitoring and reconciliation
* expanded asset-class support

---

## Disclaimer

This project is provided **strictly for research and educational purposes**.

Nothing in this repository constitutes investment advice, a recommendation to buy or sell securities, or a guarantee of trading performance.

Trading financial instruments involves substantial risk, including the potential loss of capital.

The strategies, models, backtests, simulations, analytics, and other outputs are hypothetical and may rely on assumptions that do not reflect real-world trading conditions.

Historical or simulated performance is not indicative of future results.

The presence of an IBKR integration or dry-run capability does not imply that the system is suitable for live or production trading.

Any use with real capital is solely the responsibility of the user and requires appropriate independent validation, risk controls, compliance review, and operational safeguards.

