"""Trade journal: one CSV row per fill, with P&L when a sell closes a buy.

Every tick asks the broker for recent fills and appends the ones not yet
recorded. Recorded order ids are kept in the bot state, so running a tick
twice never writes a fill twice.
"""

import csv
from pathlib import Path

from overnight.broker import Fill
from overnight.config import ROOT

JOURNAL_DIR = ROOT / "journal"
COLUMNS = ["filled_at_et", "symbol", "side", "qty", "price", "value",
           "pnl", "pnl_pct", "order_id"]
KEEP_IDS = 50  # remembered order ids; far more than a week of fills


def journal_path(profile: str) -> Path:
    name = "trades.csv" if profile == "default" else f"trades-{profile}.csv"
    return JOURNAL_DIR / name


def record_new_fills(fills: list[Fill], symbol: str, state: dict, path: Path) -> list[dict]:
    """Append fills not seen before; return the new rows (oldest first)."""
    seen = state.setdefault("journaled_ids", [])
    rows = []
    for f in fills:
        if f.order_id in seen:
            continue
        row = {"filled_at_et": f.filled_at.strftime("%Y-%m-%d %H:%M:%S"), "symbol": symbol,
               "side": f.side, "qty": f.qty, "price": round(f.price, 4),
               "value": round(f.qty * f.price, 2), "pnl": "", "pnl_pct": "", "order_id": f.order_id}
        if f.side == "buy":
            state["open_buy"] = {"qty": f.qty, "price": f.price}
        elif state.get("open_buy"):
            buy = state.pop("open_buy")
            qty = min(buy["qty"], f.qty)
            row["pnl"] = round(qty * (f.price - buy["price"]), 2)
            row["pnl_pct"] = round(100 * (f.price / buy["price"] - 1), 3)
        rows.append(row)
        seen.append(f.order_id)
    state["journaled_ids"] = seen[-KEEP_IDS:]

    if rows:
        path.parent.mkdir(exist_ok=True)
        new_file = not path.exists()
        with path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerows(rows)
    return rows


def describe(row: dict) -> str:
    """One-line notification text for a journal row."""
    verb = "BOUGHT" if row["side"] == "buy" else "SOLD"
    text = f"{verb} {row['qty']:g} {row['symbol']} @ ${row['price']:.2f} (${row['value']:,.2f})"
    if row["pnl"] != "":
        sign = "+" if row["pnl"] >= 0 else "-"
        text += f": {sign}${abs(row['pnl']):.2f} ({row['pnl_pct']:+.2f}%)"
    return text
