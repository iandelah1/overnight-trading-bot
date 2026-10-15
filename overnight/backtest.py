"""Close-to-open backtest with trading costs, taxes and a whole-share account sim."""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from overnight.strategy import Filters, hold_mask

TRADING_DAYS = 252


@dataclass(frozen=True)
class Costs:
    # Slippage per side vs. the official auction price, in basis points (0.01%).
    bps_per_side: float = 1.0


@dataclass(frozen=True)
class Taxes:
    short_term_rate: float = 0.22  # every overnight gain is short-term
    long_term_rate: float = 0.15   # buy-and-hold gains held > 1 year
    dividend_rate: float = 0.15    # qualified dividends for buy-and-hold


def session_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Per-session returns. Row t's `overnight` is the hold from close t-1 to open t.

    A dividend whose ex-date is t belongs to whoever held at close t-1, so it is
    credited to the overnight leg (the open drops by roughly that amount).
    """
    prev_close = df["Close"].shift(1)
    out = pd.DataFrame(index=df.index)
    out["overnight"] = (df["Open"] + df["Dividends"]) / prev_close - 1
    out["intraday"] = df["Close"] / df["Open"] - 1
    out["buy_hold"] = (df["Close"] + df["Dividends"]) / prev_close - 1
    out["ex_div"] = df["Dividends"] > 0
    return out.iloc[1:]


def strategy_returns(df: pd.DataFrame, filters: Filters, costs: Costs) -> pd.Series:
    """Nightly net return of the strategy, aligned to the morning it is realized."""
    rets = session_returns(df)
    # The decision is made at close t-1, so shift the mask onto the next row.
    held = hold_mask(df, filters).shift(1).loc[rets.index].astype(bool)
    round_trip = 2 * costs.bps_per_side / 1e4
    net = (rets["overnight"] - round_trip).where(held, 0.0)
    return net.rename("strategy")


def stats(returns: pd.Series, held: pd.Series | None = None) -> dict:
    equity = (1 + returns).cumprod()
    years = len(returns) / TRADING_DAYS
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else float("nan")
    vol = returns.std() * math.sqrt(TRADING_DAYS)
    drawdown = equity / equity.cummax() - 1
    out = {
        "Total return": equity.iloc[-1] - 1,
        "CAGR": cagr,
        "Volatility": vol,
        "Sharpe (rf=0)": cagr / vol if vol else float("nan"),
        "Max drawdown": drawdown.min(),
    }
    if held is not None:
        out["Nights held"] = held.mean()
        out["Win rate (held)"] = (returns[held] > 0).mean()
    return out


def after_tax_strategy(returns: pd.Series, taxes: Taxes) -> float:
    """Final value of $1 when net short-term gains are taxed every year.

    Tax is paid out of the account at year end; losses carry forward.
    """
    value, carry = 1.0, 0.0
    for _, year_rets in returns.groupby(returns.index.year):
        start = value
        value *= float((1 + year_rets).prod())
        gain = value - start + carry
        if gain > 0:
            value -= gain * taxes.short_term_rate
            carry = 0.0
        else:
            carry = gain
    return value


def after_tax_buy_hold(df: pd.DataFrame, taxes: Taxes) -> float:
    """Final value of $1 buy-and-hold: dividends taxed yearly, gains at the end."""
    rets = session_returns(df)
    price_ret = df["Close"].pct_change().iloc[1:]
    div_yield = rets["buy_hold"] - price_ret
    value, basis = 1.0, 1.0
    for p, d in zip(price_ret, div_yield):
        div_cash = value * d * (1 - taxes.dividend_rate)
        value = value * (1 + p) + div_cash
        basis += div_cash  # reinvested dividends add to cost basis
    return value - max(value - basis, 0) * taxes.long_term_rate


def per_year(df: pd.DataFrame, strat: pd.Series) -> pd.DataFrame:
    rets = session_returns(df)
    table = pd.DataFrame({
        "Strategy": strat,
        "Overnight (gross)": rets["overnight"],
        "Intraday": rets["intraday"],
        "Buy & hold": rets["buy_hold"],
    })
    return table.groupby(table.index.year).apply(lambda g: (1 + g).prod() - 1)


def simulate_account(df: pd.DataFrame, filters: Filters, costs: Costs,
                     capital: float) -> pd.DataFrame:
    """Dollar simulation with whole shares only, as Alpaca auction orders require.

    Each held night: buy floor(cash / price) shares at the close, sell them at
    the next open. Leftover cash sits idle.
    """
    slip = costs.bps_per_side / 1e4
    held = hold_mask(df, filters)
    cash = capital
    rows = []
    closes, opens, divs = df["Close"].to_numpy(), df["Open"].to_numpy(), df["Dividends"].to_numpy()
    for i in range(len(df) - 1):
        shares = 0
        if held.iloc[i]:
            buy_px = closes[i] * (1 + slip)
            shares = int(cash // buy_px)
            if shares:
                sell_px = opens[i + 1] * (1 - slip)
                cash += shares * (sell_px - buy_px + divs[i + 1])
        rows.append((df.index[i + 1], cash, shares))
    return pd.DataFrame(rows, columns=["Date", "Equity", "Shares"]).set_index("Date")


def compare(df: pd.DataFrame, filters: Filters, costs: Costs, taxes: Taxes) -> pd.DataFrame:
    """Side-by-side stats for strategy, gross overnight, intraday and buy-and-hold."""
    rets = session_returns(df)
    strat = strategy_returns(df, filters, costs)
    held = hold_mask(df, filters).shift(1).loc[rets.index].astype(bool)
    table = pd.DataFrame({
        "Strategy (net)": stats(strat, held),
        "Overnight gross": stats(rets["overnight"]),
        "Intraday": stats(rets["intraday"]),
        "Buy & hold": stats(rets["buy_hold"]),
    })
    table.loc["After-tax $1 ->", "Strategy (net)"] = after_tax_strategy(strat, taxes)
    table.loc["After-tax $1 ->", "Buy & hold"] = after_tax_buy_hold(df, taxes)
    return table.replace({np.nan: None})
