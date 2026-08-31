# Zipline Factor Engine

> A public technical preview of an institutional-style event-driven quantitative research architecture.

**Public technical preview — deliberately limited.**

This repository is a technical snapshot of a larger quantitative research and trading architecture. It is intended to demonstrate engineering, quantitative research, factor research, portfolio construction, risk management, event-driven backtesting, validation, analytics, and execution design.

Proprietary research, datasets, credentials, production configurations, monitoring infrastructure, and other components of the broader system are intentionally not included.

## This repository should not be interpreted as a claim of production readiness, investment performance, or a complete representation of the underlying research environment.

## Overview

The 717Y Quantitative Event-Driven Research Engine is a Python-based quantitative research framework designed around a separation of responsibilities between:

**Research → Validation → Portfolio Construction → Risk → Event-Driven Execution**

The objective is not simply to backtest factor signals.

The system is designed to answer a more complete question:

**Can a quantitative trading idea be researched, validated, sized, risk-controlled, simulated through an event-driven execution model, and ultimately translated into executable orders through one coherent architecture?**

The public repository provides a technical snapshot of that architecture.

```
                 717Y QUANTITATIVE EVENT-DRIVEN
                       RESEARCH ENGINE
                              │
                              ▼
                    ┌─────────────────────┐
                    │   IBKR Market Data  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Zipline Bundle   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Data Portal      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Pipeline Engine   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Factor Engine    │
                    │ Momentum / Mean Rev │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Strategy Engine   │
                    │ Cross-sectional     │
                    │ signal selection    │
                    └──────────┬──────────┘
                               │
                               ▼
                 ┌────────────────────────────┐
                 │ Portfolio Allocator / Risk │
                 │                            │
                 │ Equal Weight               │
                 │ Volatility Weight          │
                 └──────────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────┐
                    │  Event-Driven       │
                    │  Scheduler / Orders │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Zipline Event Loop  │
                    │ Commission/Slippage │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Performance / Risk  │
                    │ Analytics & Reports │
                    └─────────────────────┘
```

---

## Architectural principle: event-driven research and execution simulation

The central architectural principle is that portfolio decisions are evaluated through an explicit event-driven lifecycle rather than applying an entire history of signals simultaneously.

The architecture is:

**Market Data → Pipeline → Factors → Strategy → Portfolio Allocation → Risk Controls → Scheduled Rebalance → Orders → Event Loop → Portfolio → Analytics**

Zipline is responsible for the event-driven simulation framework.

The research engine is responsible for defining the factors, strategies, portfolio construction, risk constraints, and analytics that operate within that framework.

This separation makes it possible to study how a quantitative decision moves through an event-driven system from data to portfolio state and simulated execution.

---

## Research and validation

The research layer includes more than a single historical backtest.

The public notebooks and research modules demonstrate components of a broader validation workflow including:

- factor-level validation
- Information Coefficient analysis
- quantile-return analysis
- strategy comparison
- allocator comparison
- parameter-grid evaluation
- walk-forward validation
- in-sample / out-of-sample testing
- Sharpe decay analysis
- portfolio exposure analysis
- drawdown analysis
- performance attribution

The research layer is deliberately separated from the event-driven engine so that research questions can be evaluated without turning the core engine into experiment-specific code.

---

## Portfolio construction

The portfolio layer supports multiple allocation methodologies:

| Allocator | Description |
|---|---|
| `equal_weight` | Allocates capital evenly among active signals |
| `volatility_weight` | Inverse-volatility allocation |
| `risk_parity` | Simplified diagonal risk-parity implementation |
| `kelly_weight` | Fractional Kelly allocation based on rolling returns |
| `factor_neutral` | Factor-aware neutral allocation |
| `dollar_neutral` | Long/short dollar-neutral allocation |

The allocator produces target portfolio weights.

Portfolio-level constraints are then applied before orders are generated.

---

## Risk controls

Risk constraints are centralized within the portfolio construction and execution architecture.

Current controls include:

- maximum position size
- maximum gross exposure
- target net exposure
- whole-share position sizing
- exposure monitoring
- drawdown-based kill-switch architecture

The purpose is to keep portfolio construction and risk constraints explicit rather than hiding them inside the backtesting framework.

---

## Event-driven execution architecture

The execution layer is built around Zipline's event-driven lifecycle.

The core sequence is:

```text
Market Event
      ↓
before_trading_start()
      ↓
Pipeline Output
      ↓
Scheduled Rebalance
      ↓
Factor Strategy
      ↓
Portfolio Allocator
      ↓
Target Weights
      ↓
Order Generation
      ↓
Commission + Slippage
      ↓
Portfolio Update
```

Unlike a vectorized backtest, decisions occur as events are processed by the simulation engine.

This makes the execution lifecycle itself part of the research architecture.

---

## IBKR execution architecture

Zipline's `run_algorithm` is a backtesting engine and does not itself provide the live brokerage connection used by this project.

The IBKR bridge is therefore intentionally separated from the event-driven research engine.

`execution/broker_ibkr.py` provides the interface between portfolio targets and IBKR orders while keeping broker-specific functionality outside the Zipline event loop.

The execution layer is intentionally dry-run by default.

Paper trading should be used before any live deployment.

The presence of an IBKR integration does not mean this repository is a production trading system.

---

## Auditability

The architecture is designed around an explicit chain of responsibility:

**Market Data → Factors → Strategy Decision → Portfolio Allocation → Risk Controls → Orders → Portfolio State → Performance**

This separation makes it possible to study not only what the system did, but also which architectural layer was responsible for each decision.

---

## Repository structure

```text
Factor Engine/
│
├── config.py
├── main.py
│
├── data/
│   ├── bundle.py
│   ├── ingest.py
│   ├── loaders.py
│   └── universe.py
│
├── factors/
│   ├── common.py
│   ├── liquidity.py
│   ├── mean_reversion.py
│   └── momentum.py
│
├── strategies/
│   ├── factor_pipeline.py
│   ├── mean_reversion.py
│   └── momentum.py
│
├── portfolio/
│   └── allocators.py
│
├── execution/
│   ├── scheduler.py
│   ├── costs.py
│   └── broker_ibkr.py
│
├── analytics/
│   ├── performance.py
│   ├── factor_analysis.py
│   ├── attribution.py
│   └── reports.py
│
├── research/
│   ├── factor_tests.py
│   ├── portfolio_tests.py
│   ├── parameter_sweeps.py
│   ├── walk_forward.py
│   └── robustness.py
│
├── tests/
│   └── test_allocators.py
│
├── notebooks/
│
├── requirements.txt
└── README.md
```

---

## Technology

- Python
- Zipline-reloaded
- pandas
- NumPy
- SciPy
- ib_async
- Interactive Brokers
- Matplotlib
- Plotly

The repository is built around Zipline's Pipeline API and event-driven simulation framework.

---

## Public repository scope

This repository should be viewed as a technical preview rather than a complete representation of the underlying research environment.

It intentionally excludes:

- proprietary market datasets
- credentials and account information
- production infrastructure
- private research
- proprietary strategy research
- live trading configurations
- operational monitoring infrastructure

The purpose of publishing the repository is to demonstrate the engineering principles and quantitative research methodology behind the system.

---

## Roadmap

Potential future development includes:

- covariance-aware risk parity
- richer portfolio optimization
- real sector and industry classification
- Barra-style multi-factor attribution
- direct database-backed Zipline bundles
- expanded factor research
- additional statistical validation
- broader asset-class support

---

## Disclaimer

This project is provided **strictly for research and educational purposes**.

Nothing in this repository constitutes investment advice, a recommendation to buy or sell securities, or a guarantee of trading performance.

Trading financial instruments involves substantial risk, including the potential loss of capital.

The strategies, models, backtests, simulations, analytics, and other outputs are hypothetical and may rely on assumptions that do not reflect real-world trading conditions.

Historical or simulated performance is not indicative of future results.

The presence of an IBKR integration or dry-run capability does not imply that the system is suitable for live or production trading.

Any use with real capital is solely the responsibility of the user and requires appropriate independent validation, risk controls, compliance review, and operational safeguards.
