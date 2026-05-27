"""v0_24_0 — `medusa_originated` Check field on Wallet Deposit + Wallet Withdrawal.

Discriminates between:
  - Frappe-operator-created deposits/withdrawals (medusa_originated=0):
    operator opened the form in the desk and recorded a deposit
    manually. The pull cron in the Medusa plugin polls Frappe for
    these so the customer's Medusa wallet stays in sync.
  - API-created deposits (medusa_originated=1): the Medusa erpnext-
    plugin posted to polemarch.api.wallet_sync.record_deposit when a
    storefront payment captured. These are ALREADY reflected on the
    Medusa side; the pull cron skips them to avoid double-mirroring.

The pull cron in the Medusa plugin polls
`polemarch.api.wallet_sync.list_for_medusa(since)` which filters
WHERE medusa_originated = 0 AND modified > since.

Idempotent. Default = 0 (existing operator-created rows remain
operator-created; API-created rows from future calls set 1).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    fields_spec = {
        "fieldname": "medusa_originated",
        "label": "Medusa Originated",
        "fieldtype": "Check",
        "default": "0",
        "no_copy": 1,
        "read_only": 1,
        "in_standard_filter": 1,
        "description": (
            "Set to 1 when this doc was created by the Medusa erpnext-plugin "
            "via the wallet_sync API. The pull cron skips these to avoid "
            "double-mirroring; operator-created docs (default 0) are pulled "
            "into Medusa as cashfree_wallet transactions."
        ),
    }

    for doctype, insert_after in [
        ("Wallet Deposit", "reference_no"),
        ("Wallet Withdrawal", "reference_no"),
    ]:
        if not frappe.db.exists(
            "Custom Field", {"dt": doctype, "fieldname": "medusa_originated"}
        ):
            create_custom_fields(
                {doctype: [{**fields_spec, "insert_after": insert_after}]},
                ignore_validate=True,
                update=True,
            )

    # Backfill: any historical Wallet Deposit / Withdrawal whose
    # reference_no looks like a gateway reference (cashfree_*, razorpay_*,
    # wallet_*, evt_*) was clearly API-created — flag so the pull cron
    # skips it. Operator-typed cheque numbers / UPI refs are short and
    # don't match these prefixes.
    for doctype in ("Wallet Deposit", "Wallet Withdrawal"):
        if not frappe.db.has_column(doctype, "medusa_originated"):
            continue
        # Heuristic prefixes for known gateway / event references
        updated = frappe.db.sql(
            f"""
            UPDATE `tab{doctype}`
               SET medusa_originated = 1
             WHERE COALESCE(medusa_originated, 0) = 0
               AND (
                    reference_no LIKE 'cashfree_%%'
                 OR reference_no LIKE 'razorpay_%%'
                 OR reference_no LIKE 'wallet_%%'
                 OR reference_no LIKE 'evt_%%'
                 OR reference_no LIKE 'wt_%%'
               )
            """
        )
    frappe.db.commit()
