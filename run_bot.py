"""Overnight bot: buy at the closing auction, sell at the next opening auction.

Commands:
    python run_bot.py tick      # run one step (the scheduler calls this every 5 min)
    python run_bot.py status    # account, position, open orders, next action
    python run_bot.py report    # nightly P&L from Alpaca's order history
    python run_bot.py trades    # the bot's own trade journal (journal/trades.csv)
    python run_bot.py compare   # side-by-side results for every profile
    python run_bot.py flatten   # emergency: cancel orders and sell everything

--profile NAME uses profiles/NAME.env on top of .env; --profile all runs every
profile (default: just .env).

Paper trading is the default. Live trading needs ALPACA_PAPER=false AND the
--live flag on every command, so it can't happen by accident.
"""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

import pandas as pd
import requests

from overnight.bot import STOP_FILE, flatten, load_state, notify, tick
from overnight.broker import Broker
from overnight.journal import journal_path
from overnight.config import DEFAULT_PROFILE, ROOT, Settings, list_profiles, load_settings


def setup_logging() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    handlers = [RotatingFileHandler(ROOT / "logs" / "bot.log", maxBytes=2_000_000, backupCount=5),
                logging.StreamHandler(sys.stdout)]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def status(settings: Settings, broker: Broker) -> None:
    acct = broker.account()
    now = broker.now()
    print(f"== {settings.profile}: {'PAPER' if settings.paper else 'LIVE'}  {settings.symbol}  execution={settings.execution}  {settings.filters}")
    print(f"Time (ET): {now:%a %Y-%m-%d %H:%M}   session today: {broker.session(now.date())}")
    print(f"Equity:    ${acct.equity:,.2f}   spendable cash ${acct.spendable:,.2f}   "
          f"max position ${settings.max_position_usd:,.0f}")
    print(f"Account:   {', '.join(acct.problems) or 'OK'}   day trades (5 days): {acct.daytrade_count}")
    print(f"Position:  {broker.position_qty()} shares")
    for o in broker.open_orders():
        print(f"Open order: {o.side.value} {o.qty} {o.type.value} tif={o.time_in_force.value} "
              f"limit={o.limit_price} status={o.status.value}")
    print(f"State:     {load_state(settings.profile)}   STOP file: {'YES' if STOP_FILE.exists() else 'no'}\n")


def nightly_pnl(broker: Broker, days: int) -> pd.DataFrame:
    """Pair each filled buy with the next filled sell: one row per night held."""
    fills = [o for o in broker.recent_orders(days) if o.filled_at and float(o.filled_qty or 0) > 0]
    fills.sort(key=lambda o: o.filled_at)
    nights, buy = [], None
    for o in fills:
        price, qty = float(o.filled_avg_price), float(o.filled_qty)
        if o.side.value == "buy":
            buy = (pd.Timestamp(o.filled_at).tz_convert("America/New_York"), qty, price)
        elif buy is not None:
            q = min(buy[1], qty)
            nights.append({"bought": buy[0].strftime("%Y-%m-%d"), "qty": q, "buy": buy[2], "sell": price,
                           "pnl $": q * (price - buy[2]), "ret %": 100 * (price / buy[2] - 1)})
            buy = None
    return pd.DataFrame(nights, columns=["bought", "qty", "buy", "sell", "pnl $", "ret %"])


def trades(profile: str) -> None:
    path = journal_path(profile)
    if not path.exists():
        print(f"No trades journaled yet ({path.relative_to(ROOT)}).")
        return
    df = pd.read_csv(path)
    print(df.drop(columns=["order_id"]).to_string(index=False, na_rep=""))
    closed = df.dropna(subset=["pnl"])
    if len(closed):
        print(f"\n{len(closed)} round trips  total P&L ${closed['pnl'].sum():,.2f}  "
              f"win rate {(closed['pnl'] > 0).mean():.0%}  best {closed['pnl_pct'].max():+.2f}%  "
              f"worst {closed['pnl_pct'].min():+.2f}%")
    print(f"\nFile: {path}")


def report(broker: Broker, days: int) -> None:
    out = nightly_pnl(broker, days)
    if out.empty:
        print("No completed round trips yet.")
        return
    print(out.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print(f"\n{len(out)} nights  total P&L ${out['pnl $'].sum():,.2f}  "
          f"win rate {(out['pnl $'] > 0).mean():.0%}  avg {out['ret %'].mean():.3f}%/night")


def compare(profiles: list[str], live: bool, days: int) -> None:
    rows = []
    for name in profiles:
        s = load_settings(name)
        check_mode(s, live)
        b = Broker(s.api_key, s.secret_key, s.paper, s.symbol)
        n = nightly_pnl(b, days)
        pnl = n["pnl $"].sum()
        rows.append({
            "profile": name, "symbol": s.symbol, "nights": len(n),
            "P&L $": pnl, "on $": s.max_position_usd, "return %": 100 * pnl / s.max_position_usd,
            "win %": 100 * (n["pnl $"] > 0).mean() if len(n) else None,
            "best night %": n["ret %"].max() if len(n) else None,
            "worst night %": n["ret %"].min() if len(n) else None,
            "holding now": b.position_qty(),
        })
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))


def check_mode(settings: Settings, live: bool) -> None:
    if not settings.paper and not live:
        sys.exit(f"[{settings.profile}] ALPACA_PAPER=false but --live not given. "
                 "Refusing to touch the live account.")
    if settings.paper and live:
        sys.exit(f"[{settings.profile}] --live given but ALPACA_PAPER is not false.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["tick", "status", "report", "trades", "compare", "flatten"])
    p.add_argument("--profile", default=DEFAULT_PROFILE, help="profile name, or 'all'")
    p.add_argument("--live", action="store_true", help="required when ALPACA_PAPER=false")
    p.add_argument("--days", type=int, default=90, help="report/compare window")
    a = p.parse_args()
    setup_logging()

    use_all = a.profile == "all" or (a.command == "compare" and a.profile == DEFAULT_PROFILE)
    profiles = (list_profiles() or [DEFAULT_PROFILE]) if use_all else [a.profile]
    if not profiles:
        sys.exit("No profiles in profiles/*.env")
    if a.command == "compare":
        compare(profiles, a.live, a.days)
        return
    if a.command == "trades":
        for name in profiles:
            trades(name)
        return

    failed = False
    for name in profiles:
        settings = load_settings(name)
        check_mode(settings, a.live)
        broker = Broker(settings.api_key, settings.secret_key, settings.paper, settings.symbol)
        try:
            if a.command == "tick":
                tick(settings, broker)
            elif a.command == "status":
                status(settings, broker)
            elif a.command == "report":
                print(f"== {name} ({settings.symbol})")
                report(broker, a.days)
            elif a.command == "flatten":
                flatten(settings, broker)
            offline_streak(name, reset=True)
        except requests.exceptions.ConnectionError:
            # Usually the Mac just woke up and Wi-Fi isn't back yet. Only
            # alert if it keeps happening.
            streak = offline_streak(name)
            logging.warning("[%s] %s: no connection to Alpaca (%d in a row)", name, a.command, streak)
            if streak == OFFLINE_ALERT_AFTER:
                notify(f"Can't reach Alpaca for {streak} checks in a row. Is the Mac online?", name)
            failed = True
        except Exception as e:  # one profile failing must not stop the others
            logging.exception("[%s] %s failed", name, a.command)
            notify(f"Bot error: {e}", name)
            failed = True
    sys.exit(1 if failed else 0)


OFFLINE_ALERT_AFTER = 3


def offline_streak(profile: str, reset: bool = False) -> int:
    """Count consecutive connection failures in a small file per profile."""
    path = ROOT / "state" / f"{profile}.offline"
    if reset:
        path.unlink(missing_ok=True)
        return 0
    path.parent.mkdir(exist_ok=True)
    count = (int(path.read_text()) if path.exists() else 0) + 1
    path.write_text(str(count))
    return count


if __name__ == "__main__":
    main()
