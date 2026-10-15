"""Pure decision logic for one bot tick. No network calls, so it is fully testable.

The bot runs `tick` every few minutes. Each tick looks at the time and the
account and does at most one thing:

  * holding shares bought on an earlier day -> make sure a sell is queued:
      - market open: plain market sell (the morning sell was missed)
      - "auction" mode, 7:00pm..9:28am ET: market-on-open (opening auction) sell
      - "regular" mode, any time the market is closed: market day order, which
        Alpaca queues and executes at the next open
  * flat, inside the entry window before today's close, not yet decided today
      -> evaluate the filters and maybe place the buy (limit-on-close in
         "auction" mode, a marketable limit day order in "regular" mode)

"regular" exists because Alpaca's paper simulator doesn't fill auction orders.

Shares bought today are never sold today, so the bot can't make a day trade.
"""

from dataclasses import dataclass
from datetime import date, datetime, time

OPG_REJECT_START = time(9, 28)   # Alpaca rejects opening-auction orders 9:28am..7pm ET
OPG_REJECT_END = time(19, 0)


@dataclass(frozen=True)
class Session:
    open: datetime   # timezone-aware, ET
    close: datetime


@dataclass(frozen=True)
class Snapshot:
    now: datetime                # timezone-aware, ET
    session: Session | None      # today's session, None on non-trading days
    position_qty: int
    open_buy: bool               # an unfilled buy order exists for the symbol
    open_sell: bool              # an unfilled sell order exists for the symbol
    last_entry_date: date | None  # last day the bot made its entry decision
    entries_halted: bool         # STOP file present or drawdown limit hit


@dataclass(frozen=True)
class Decision:
    action: str          # "sell", "evaluate_entry" or "wait"
    reason: str
    tif: str | None = None  # for sells: "opg" or "day"


def decide(s: Snapshot, entry_window_min: int, entry_cutoff_min: int,
           execution: str = "auction") -> Decision:
    today = s.now.date()
    market_open = s.session is not None and s.session.open <= s.now < s.session.close

    if s.position_qty > 0:
        if s.open_sell:
            return Decision("wait", "sell already queued")
        if s.last_entry_date == today and (s.session is None or s.now < s.session.close):
            # Only possible if a buy filled during today's session: never sell it today.
            return Decision("wait", "position opened today; holding to avoid a day trade")
        if market_open:
            return Decision("sell", "missed the morning sell; selling at market", "day")
        if execution == "regular":
            return Decision("sell", "queue market sell for the next open", "day")
        if OPG_REJECT_START <= s.now.time() < OPG_REJECT_END:
            return Decision("wait", "holding; opening-auction orders accepted after 7pm ET")
        return Decision("sell", "queue sell for the opening auction", "opg")

    if s.open_buy:
        return Decision("wait", "buy order pending")
    if not market_open:
        return Decision("wait", "market closed")
    if s.last_entry_date == today:
        return Decision("wait", "entry already decided today")
    minutes_to_close = (s.session.close - s.now).total_seconds() / 60
    if minutes_to_close > entry_window_min:
        return Decision("wait", f"{minutes_to_close:.0f} min to close; entry window not open")
    if minutes_to_close < entry_cutoff_min:
        return Decision("wait", "too close to the close to place the buy")
    if s.entries_halted:
        return Decision("wait", "entries halted")
    return Decision("evaluate_entry", "inside the entry window")


def entry_order(last_price: float, buying_power: float, max_position_usd: float,
                limit_pct: float) -> tuple[int, float]:
    """Whole-share quantity and limit price for the buy.

    Sized against the limit price so the fill can never exceed available cash.
    """
    limit = round(last_price * (1 + limit_pct), 2)
    budget = min(buying_power, max_position_usd)
    return int(budget // limit), limit
