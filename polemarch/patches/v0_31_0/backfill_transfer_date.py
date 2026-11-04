"""v0_31_0 — backfill Security Purchase / Security Sale `transfer_date`.

Every document entered so far was posted on its demat transfer date
(checked against the share master ledger on 2026-11-04: 48 purchases and
59 sales, no exceptions), so the backfill copies `posting_date`. Writes
only the new column: no GL, no `modified` bump, no cancel/amend.

Idempotent — rows that already carry a transfer date are left alone.
"""

import frappe


def execute():
    for doctype in ("Security Purchase", "Security Sale"):
        frappe.db.sql(
            f"""UPDATE `tab{doctype}` SET transfer_date = posting_date
            WHERE transfer_date IS NULL"""
        )
