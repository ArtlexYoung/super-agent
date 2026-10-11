---
name: "quantitative"
description: "Research financial data and strategies with causal timing, chronological validation, costs and risk checks"
metadata:
  super-agent-categories: "[\"research/quantitative\",\"time-series\",\"backtesting\"]"
  super-agent-type: "method"
  super-agent-includes: "[\"skill:super-agent/research/exploration\",\"skill:super-agent/research/experiments\"]"
  super-agent-version: "0.2.125"
---
# Quantitative research

Use for data analysis, factor research, portfolio experiments and offline backtests.
Fix the universe, period, frequency, timestamp/time zone, data availability rules,
benchmark, costs, objective, risk limits and budget. Record provider, retrieval time,
license, versions and exact data hashes. Check gaps, duplicates, adjustments,
delistings, corporate actions and point-in-time universe membership. Synthetic
data demonstrates mechanics; it does not establish investment performance.

## Causal evaluation

Features may use only information available at the decision time. Specify publication
lags and execution delay; a signal computed at a close cannot trade at that same
close unless an explicit executable timing model justifies it. Test that changing
future observations does not change earlier positions. Fit transformations and
parameters on training data only. Split chronologically; apply purging/embargo
when labels overlap. Use walk-forward validation when appropriate and reserve an
untouched final holdout. Log all trials and account for multiple selection attempts.

Model transaction fees, spread, slippage, turnover, liquidity/capacity, position
limits and financing/borrow costs relevant to the proposed strategy. Include cash
and a suitable passive benchmark. Report net returns, drawdown, exposure, turnover,
sample size and period; state annualization conventions and assumptions. Reject
non-finite accounting, impossible fills and future leakage. Investigate parameter,
cost, regime and data sensitivity, including adverse periods and negative results.

Read `skill:super-agent/research/experiments` for candidate comparisons. Freeze
metrics and risk limits before evaluation; final holdout results cannot become
the next tuning dataset while retaining the claim of independent validation.

## Delivery boundary

Deliver provenance, hypothesis, causal timing, code/configuration hashes, split
definitions, executed checks, cost assumptions, baseline comparison and limitations.
Missing market data or execution means no claimed backtest. This method supplies
research only: it does not provide brokerage access, place orders, manage accounts
or initiate live trading. Passing an offline backtest does not establish future
profitability. Use host-owned acceptance for task delivery.
