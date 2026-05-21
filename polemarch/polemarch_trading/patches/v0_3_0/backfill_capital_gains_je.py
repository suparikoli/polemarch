"""OPT-IN: post cost-recognition JEs for historical Investment Disposals.

This is NOT wired into `patches.txt` by default. Historical disposals may
sit in closed accounting periods, and we don't want a migration to bulk-
post into them by surprise.

To run manually:

    bench --site <site> execute polemarch.polemarch_trading.patches.v0_3_0.backfill_capital_gains_je.execute

Or with a posting-date floor (only backfill disposals on/after a date):

    bench --site <site> execute polemarch.polemarch_trading.patches.v0_3_0.backfill_capital_gains_je.execute \
        --kwargs '{"from_date": "2026-04-01"}'

Skipped disposals (already-posted JEs, missing accounts, etc.) are logged
to `frappe.log_error` for review.
"""

import frappe
from frappe.utils import getdate


def execute(from_date: str | None = None, dry_run: bool = False):
    if not frappe.db.exists(
        "Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_doctype"}
    ):
        frappe.log_error(
            "backfill_capital_gains_je: JE custom fields not present — run "
            "polemarch.polemarch_trading.patches.v0_3_0.add_custom_fields_to_je first.",
            "Polemarch Capital Gains Backfill",
        )
        return

    filters = {"docstatus": 1}
    if from_date:
        filters["disposal_date"] = [">=", getdate(from_date)]

    disposals = frappe.get_all(
        "Investment Disposal",
        filters=filters,
        fields=["name"],
        order_by="disposal_date ASC",
    )

    from polemarch.polemarch_trading import accounting as accounting_engine

    posted = 0
    skipped = []
    for row in disposals:
        existing = frappe.db.get_value(
            "Journal Entry",
            {
                "custom_source_doctype": "Investment Disposal",
                "custom_source_name": row.name,
                "docstatus": ["!=", 2],
            },
            "name",
        )
        if existing:
            skipped.append({"disposal": row.name, "reason": f"JE {existing} already exists"})
            continue

        if dry_run:
            posted += 1
            continue

        try:
            disposal = frappe.get_doc("Investment Disposal", row.name)
            je_name = accounting_engine.post_cost_recognition_je_for_disposal(disposal)
            if je_name:
                posted += 1
            else:
                skipped.append({"disposal": row.name, "reason": "no JE emitted (zero cost?)"})
        except Exception as exc:
            skipped.append({"disposal": row.name, "reason": str(exc)[:200]})

    frappe.db.commit()

    frappe.log_error(
        f"backfill_capital_gains_je (dry_run={dry_run}, from_date={from_date}): "
        f"posted={posted}, skipped={len(skipped)}. First 20 skipped: {skipped[:20]}",
        "Polemarch Capital Gains Backfill",
    )

    return {"posted": posted, "skipped": len(skipped)}
