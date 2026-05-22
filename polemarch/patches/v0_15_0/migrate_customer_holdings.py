"""Phase 15 — migrate customer-owned Investment Holdings → Customer Holding rows.

Phase 10 introduced `Investment Holding.customer` so customer ownership
could live in the proprietary inventory ledger. Phase 15 splits that
back out: customer holdings become a separate, simpler doctype that is
NOT part of the accounting ledger (no FIFO, no cost basis, no
classification, no disposal chain). They're CRM data — Polemarch tracks
"who currently holds what" so we can call them when we need supply.

For each Investment Holding where customer is populated:
  1. Sum qty_remaining for that (customer, security) pair across all
     Holdings (a customer might have multiple Holdings of the same
     security from different acquisitions).
  2. Upsert a Customer Holding row at that total.
  3. Delete the underlying Investment Holding row(s).

Investment Disposals that referenced these Holdings keep their child
Disposal Lot rows for the audit trail (the Holding link goes stale but
the qty/cost data is captured in the Lot row itself).

Idempotent. Drift-safe — if Customer Holding already has manual edits,
we update qty to the sum from Investment Holdings (the most authoritative
historical snapshot at migration time).
"""

import frappe
from frappe.utils import flt, now_datetime


def execute():
    if not frappe.db.exists("DocType", "Customer Holding"):
        # Doctype JSON hasn't synced yet — bail; the migrate after this
        # patch will re-run it.
        return
    if not frappe.db.has_column("Investment Holding", "customer"):
        # Already migrated (or pre-Phase-10 site that never had the column).
        return

    # Sum qty_remaining per (customer, security) across non-fully-disposed Holdings.
    rows = frappe.db.sql(
        """
        SELECT customer, security, COALESCE(SUM(qty_remaining), 0) AS qty
          FROM `tabInvestment Holding`
         WHERE customer IS NOT NULL
           AND customer != ''
           AND status IN ('Open', 'Partially Disposed')
         GROUP BY customer, security
        """,
        as_dict=True,
    )

    for row in rows:
        if not row.customer or not row.security:
            continue
        if flt(row.qty) <= 0:
            continue

        ch_name = f"{row.customer}-{row.security}"
        now = now_datetime()
        if frappe.db.exists("Customer Holding", ch_name):
            frappe.db.set_value(
                "Customer Holding", ch_name,
                {
                    "qty": flt(row.qty),
                    "source": "Imported",
                    "last_updated": now,
                    "last_updated_by": "Administrator",
                    "notes": f"[{now:%Y-%m-%d %H:%M}] v0_15_0 migration: backfilled from Investment Holding.",
                },
                update_modified=False,
            )
        else:
            ch = frappe.get_doc({
                "doctype": "Customer Holding",
                "customer": row.customer,
                "security": row.security,
                "qty": flt(row.qty),
                "source": "Imported",
                "notes": f"[{now:%Y-%m-%d %H:%M}] v0_15_0 migration: backfilled from Investment Holding.",
            })
            ch.flags.ignore_permissions = True
            ch.insert(ignore_permissions=True)

    # Now delete every Investment Holding row that had customer populated.
    # Disposal lots that referenced these stay (they have the qty/cost
    # captured directly on the lot row); the holding link goes stale but
    # the audit trail survives.
    frappe.db.sql(
        """
        DELETE FROM `tabInvestment Holding`
         WHERE customer IS NOT NULL AND customer != ''
        """,
    )

    frappe.db.commit()
