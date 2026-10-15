"""One bot tick: gather a snapshot, decide, act, log."""

import json
import logging
import subprocess
import time
from datetime import date

import pandas as pd

from overnight.broker import Broker
from overnight.config import DEFAULT_PROFILE, ROOT, Settings, alert_topic_arn
from overnight.decide import Snapshot, decide, entry_order
from overnight.journal import describe, journal_path, record_new_fills
from overnight.strategy import should_hold_tonight

STATE_DIR = ROOT / "state"
STOP_FILE = ROOT / "STOP"  # blocks new entries for every profile
log = logging.getLogger("bot")


def state_file(profile: str):
    return STATE_DIR / f"{profile}.json"


def load_state(profile: str) -> dict:
    path = state_file(profile)
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(profile: str, state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    state_file(profile).write_text(json.dumps(state, indent=2))


def notify(message: str, profile: str = "") -> None:
    """Log, show a macOS notification (skipped elsewhere), and email it via
    SNS when ALERT_TOPIC_ARN is set. A failed alert never stops the bot."""
    if profile:
        message = f"[{profile}] {message}"
    log.info(message)
    try:
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(message)} '
                        'with title "Overnight bot"'], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    topic = alert_topic_arn(profile or DEFAULT_PROFILE)
    if topic:
        try:
            import boto3  # only needed where email alerts are configured
            region = topic.split(":")[3]  # arn:aws:sns:<region>:<account>:<name>
            boto3.client("sns", region_name=region).publish(
                TopicArn=topic, Subject="Overnight bot", Message=message)
        except Exception:
            log.exception("could not send SNS alert")


def tick(settings: Settings, broker: Broker) -> None:
    state = load_state(settings.profile)
    now = broker.now()
    today = now.date()
    acct = broker.account()
    equity, spendable = acct.equity, acct.spendable

    # Alert once per change in account flags. Any flag blocks new entries.
    problems = ", ".join(acct.problems)
    if problems != state.get("account_problems", ""):
        notify(f"Account problem: {problems}. New entries halted." if problems
               else "Account problems cleared.", settings.profile)
        state["account_problems"] = problems
    # The bot never buys and sells the same day, so this should stay 0.
    if acct.daytrade_count > state.get("daytrade_count_seen", 0):
        notify(f"WARNING: Alpaca counts {acct.daytrade_count} day trade(s) in the last 5 days. "
               "Check logs/bot.log.", settings.profile)
    state["daytrade_count_seen"] = acct.daytrade_count

    # Journal fills since the last tick and announce them.
    for row in record_new_fills(broker.filled_orders(), settings.symbol, state, journal_path(settings.profile)):
        notify(describe(row), settings.profile)

    # High-water mark for the drawdown guard.
    peak = max(state.get("peak_equity", equity), equity)
    state["peak_equity"] = peak
    drawdown_hit = equity < peak * (1 - settings.max_drawdown_pct)
    if drawdown_hit and not state.get("drawdown_alerted"):
        notify(f"Drawdown limit hit: equity ${equity:,.2f} vs peak ${peak:,.2f}. New entries halted.", settings.profile)
        state["drawdown_alerted"] = True

    orders = broker.open_orders()
    last_entry = state.get("last_entry_date")
    snap = Snapshot(
        now=now,
        session=broker.session(today),
        position_qty=broker.position_qty(),
        open_buy=any(o.side.value == "buy" for o in orders),
        open_sell=any(o.side.value == "sell" for o in orders),
        last_entry_date=date.fromisoformat(last_entry) if last_entry else None,
        entries_halted=STOP_FILE.exists() or drawdown_hit or bool(acct.problems),
    )
    d = decide(snap, settings.entry_window_minutes, settings.entry_cutoff_minutes, settings.execution)
    log.info("[%s] %s | qty=%d equity=%.2f cash=%.2f | %s: %s", settings.profile, now.strftime("%Y-%m-%d %H:%M"),
             snap.position_qty, equity, spendable, d.action, d.reason)

    if d.action == "sell":
        broker.sell(snap.position_qty, d.tif)
        notify(f"SELL {snap.position_qty} {settings.symbol} ({d.tif}): {d.reason}", settings.profile)
    elif d.action == "evaluate_entry":
        enter(settings, broker, state, today, spendable)

    save_state(settings.profile, state)


def enter(settings: Settings, broker: Broker, state: dict, today: date, spendable: float) -> None:
    # Record the decision first so a crash can't cause a second buy today.
    state["last_entry_date"] = today.isoformat()
    save_state(settings.profile, state)

    price = broker.last_price()
    history = broker.daily_closes(before=today, days=(settings.filters.trend_sma_days or 0) + 5)
    history.loc[pd.Timestamp(today)] = price  # today's close ~ the latest trade
    if not should_hold_tonight(history, pd.Timestamp(today), settings.filters):
        notify(f"Skipping tonight: filters say no ({settings.filters}), last ${price:.2f}", settings.profile)
        return

    qty, limit = entry_order(price, spendable, settings.max_position_usd, settings.entry_limit_pct)
    if qty < 1:
        notify(f"Skipping tonight: ${spendable:,.2f} cash can't buy 1 share at ${limit:.2f}", settings.profile)
        return
    tif = "cls" if settings.execution == "auction" else "day"
    broker.buy(qty, limit, tif)
    kind = "limit-on-close" if tif == "cls" else "limit"
    notify(f"BUY {qty} {settings.symbol} {kind} ${limit:.2f} (last ${price:.2f})", settings.profile)


def flatten(settings: Settings, broker: Broker) -> None:
    """Emergency exit: cancel orders and sell everything as soon as possible."""
    cancelled = broker.cancel_open_orders()
    time.sleep(2)  # let cancellations release the shares
    qty = broker.position_qty()
    if qty:
        # A day market order fills now if the market is open, otherwise at the next open.
        broker.sell(qty, "day")
        notify(f"FLATTEN: cancelled {cancelled} orders, selling {qty} {settings.symbol} at market", settings.profile)
    else:
        notify(f"FLATTEN: cancelled {cancelled} orders, no position", settings.profile)
