"""Backtest the buy-at-close / sell-at-open strategy.

Examples:
    python run_backtest.py                      # SPY, all nights, 1 bp per side
    python run_backtest.py --start 2015 --trend 200 --skip-weekends
    python run_backtest.py --ticker SPYM --start 2017 --capital 500
"""

import argparse

import pandas as pd

from overnight.backtest import Costs, Taxes, compare, per_year, simulate_account, strategy_returns
from overnight.data import load_daily
from overnight.strategy import Filters


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", default="SPY")
    p.add_argument("--start", default=None, help="first year or date, e.g. 2010")
    p.add_argument("--end", default=None)
    p.add_argument("--bps", type=float, default=1.0, help="slippage per side in basis points")
    p.add_argument("--trend", type=int, default=None, help="only hold above this SMA (days)")
    p.add_argument("--skip-weekends", action="store_true")
    p.add_argument("--down-day", choices=["intraday", "daily"], default="",
                   help="only hold after the price fell (NY Fed 'overnight drift' idea)")
    p.add_argument("--down-min", type=float, default=0.0, help="minimum drop, e.g. 0.01 = 1%%")
    p.add_argument("--down-overrides-trend", action="store_true",
                   help="a down day allows holding even when the trend filter says no")
    p.add_argument("--tax", type=float, default=0.22, help="short-term tax rate")
    p.add_argument("--capital", type=float, default=None, help="run a whole-share dollar sim")
    p.add_argument("--yearly", action="store_true", help="print per-year returns")
    p.add_argument("--refresh", action="store_true", help="re-download data")
    a = p.parse_args()

    df = load_daily(a.ticker, refresh=a.refresh).loc[a.start:a.end]
    filters = Filters(trend_sma_days=a.trend, skip_weekends=a.skip_weekends, down_day=a.down_day,
                      down_day_min_pct=a.down_min, down_day_overrides_trend=a.down_overrides_trend)
    costs = Costs(bps_per_side=a.bps)
    taxes = Taxes(short_term_rate=a.tax)

    print(f"\n{a.ticker}  {df.index[0].date()} -> {df.index[-1].date()}  "
          f"cost={a.bps} bp/side  {filters}\n")
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
    print(compare(df, filters, costs, taxes).to_string())

    if a.yearly:
        print("\nPer-year returns:")
        print(per_year(df, strategy_returns(df, filters, costs)).to_string())

    if a.capital:
        sim = simulate_account(df, filters, costs, a.capital)
        print(f"\nWhole-share account sim: ${a.capital:,.0f} -> ${sim['Equity'].iloc[-1]:,.2f}  "
              f"(avg shares held on traded nights: {sim.loc[sim.Shares > 0, 'Shares'].mean():.1f})")


if __name__ == "__main__":
    main()
