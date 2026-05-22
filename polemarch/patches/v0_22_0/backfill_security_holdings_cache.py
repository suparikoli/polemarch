"""v0_22_0 — backfill Security.qty_sit / qty_investment / qty_total /
cost_total for every existing Security row.

The 4 cache columns land on the Security table when the doctype is
migrated (they're part of security.json), so by the time this patch
runs the columns exist but are NULL/0. We just recompute the truth
from `tabInvestment Holding` and persist it.

Going forward, the cache stays fresh via the Investment Holding
doc_event hooks in hooks.py.
"""

import frappe


def execute():
    from polemarch.polemarch_trading import holdings_cache
    refreshed = holdings_cache.refresh_all()
    frappe.db.commit()
    if refreshed:
        print(f"Polemarch holdings cache: refreshed {refreshed} Security rows")
