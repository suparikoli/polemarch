"""v0_24_0 — backfill Investment Holding.classifications child rows from
the legacy single-classification model.

For each Investment Holding with classification IN ('Stock in Trade',
'Investment'), insert one child row in the new `classifications` table
with qty=qty_remaining (so it captures only the still-open portion).
'Unallocated' holdings get no child row — they're still in the
classification window.

Idempotent: skips holdings that already have child rows. Re-runs are
no-ops.
"""

import frappe
from frappe.utils import flt


def execute():
    if not frappe.db.has_column("Investment Holding", "classification"):
        return
    if not frappe.db.exists("DocType", "Investment Holding Classification"):
        return

    holdings = frappe.db.sql(
        """
        SELECT name, classification, qty_remaining, qty_acquired, qty_disposed,
               creation, owner
        FROM `tabInvestment Holding`
        WHERE classification IN ('Stock in Trade', 'Investment')
          AND qty_remaining > 0
        """,
        as_dict=True,
    )

    backfilled = 0
    skipped = 0
    for h in holdings:
        # Skip if any child rows already exist (manual or prior run).
        existing = frappe.db.count(
            "Investment Holding Classification",
            {"parent": h["name"]},
        )
        if existing:
            skipped += 1
            continue

        qty = flt(h["qty_remaining"])
        if qty <= 0:
            continue

        # Insert directly via child-table semantics — bypasses the parent's
        # full validate cycle, which is what we want for a backfill.
        frappe.get_doc({
            "doctype": "Investment Holding Classification",
            "parent": h["name"],
            "parenttype": "Investment Holding",
            "parentfield": "classifications",
            "idx": 1,
            "classification": h["classification"],
            "qty": qty,
            "classified_on": h["creation"],
            "classified_by": h["owner"] or "Administrator",
            "auto_classified": 0,
            "notes": (
                "Backfilled from legacy single-classification model "
                "(v0_24_0). Represents the still-open portion at the "
                "time of migration."
            ),
        }).insert(ignore_permissions=True)
        backfilled += 1

    frappe.db.commit()
    if backfilled or skipped:
        print(
            f"Investment Holding Classification backfill: "
            f"{backfilled} backfilled, {skipped} skipped (already had rows)"
        )
