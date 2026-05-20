"""Mark-to-market job + ad-hoc position refresh helpers.

Daily cron entry (in hooks.py at 06:00 IST):
    polemarch.trading.positions.mark_to_market_unrealized_pnl

For each Security Position row with `qty_held > 0`, snapshots the current
`Security.last_traded_price` into `last_marked_price`, recomputes
`market_value` and `unrealized_pnl`, and bumps `version`. Skipped if the
Security has no LTP yet — the row stays at its previous mark.

`refresh_one(name)` is exposed for back-office / API ad-hoc refresh on a
single Position row (Phase 5 API can wire this if needed).
"""

import frappe
from frappe.utils import flt, now_datetime


_BATCH_SIZE = 500


def mark_to_market_unrealized_pnl() -> dict:
    """Daily MTM refresh across all open Security Position rows."""
    if not frappe.db.table_exists("Security Position"):
        return {"refreshed": 0, "skipped": 0}

    refreshed = 0
    skipped = 0
    offset = 0

    while True:
        rows = frappe.db.sql(
            """
            SELECT sp.name,
                   sp.security,
                   sp.qty_held,
                   sp.total_cost,
                   COALESCE(sec.last_traded_price, 0) AS ltp
              FROM `tabSecurity Position` sp
              LEFT JOIN `tabSecurity` sec ON sec.name = sp.security
             WHERE sp.qty_held > 0
             ORDER BY sp.name
             LIMIT %s OFFSET %s
            """,
            (_BATCH_SIZE, offset),
            as_dict=True,
        )
        if not rows:
            break

        for row in rows:
            ltp = flt(row.ltp)
            if ltp <= 0:
                skipped += 1
                continue
            market_value = flt(row.qty_held) * ltp
            unrealized = market_value - flt(row.total_cost)
            frappe.db.sql(
                """
                UPDATE `tabSecurity Position`
                   SET last_marked_price = %s,
                       market_value      = %s,
                       unrealized_pnl    = %s,
                       last_updated_on   = %s,
                       version           = version + 1,
                       modified          = %s
                 WHERE name = %s
                """,
                (ltp, market_value, unrealized, now_datetime(), now_datetime(), row.name),
            )
            refreshed += 1

        offset += _BATCH_SIZE
        frappe.db.commit()  # checkpoint per batch — keeps txn small

    return {"refreshed": refreshed, "skipped": skipped}


def refresh_one(name: str) -> dict:
    """Ad-hoc MTM refresh for a single Security Position row."""
    row = frappe.db.sql(
        """
        SELECT sp.name, sp.security, sp.qty_held, sp.total_cost,
               COALESCE(sec.last_traded_price, 0) AS ltp
          FROM `tabSecurity Position` sp
          LEFT JOIN `tabSecurity` sec ON sec.name = sp.security
         WHERE sp.name = %s
        """,
        (name,),
        as_dict=True,
    )
    if not row:
        return {"refreshed": 0, "name": name, "reason": "not found"}
    r = row[0]
    if flt(r.ltp) <= 0 or flt(r.qty_held) <= 0:
        return {"refreshed": 0, "name": name, "reason": "no LTP or empty position"}

    market_value = flt(r.qty_held) * flt(r.ltp)
    unrealized = market_value - flt(r.total_cost)
    frappe.db.sql(
        """
        UPDATE `tabSecurity Position`
           SET last_marked_price = %s,
               market_value      = %s,
               unrealized_pnl    = %s,
               last_updated_on   = %s,
               version           = version + 1,
               modified          = %s
         WHERE name = %s
        """,
        (r.ltp, market_value, unrealized, now_datetime(), now_datetime(), r.name),
    )
    return {
        "refreshed": 1,
        "name": r.name,
        "last_marked_price": flt(r.ltp),
        "market_value": market_value,
        "unrealized_pnl": unrealized,
    }
