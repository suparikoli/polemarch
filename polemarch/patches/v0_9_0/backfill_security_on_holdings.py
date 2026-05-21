"""Phase 9 — backfill `Investment Holding.security` from `Item.custom_security`.

The Holding's identity key is becoming `security` instead of `item`. Every
existing Holding has `item` populated; this patch resolves item →
Item.custom_security and stamps it on the Holding's new `security` field.

Holdings whose `item` doesn't have a linked Security get logged but skipped
— the operator must back-link those manually before the next patch
(`drop_item_field_from_holdings`) makes Security mandatory. We deliberately
don't synthesise Securities here: that would silently spawn ISIN-less
records that audits later flag as invalid.

Idempotent. Re-running only touches rows whose `security` is still empty.
"""

import frappe


def execute():
    # Guard: the security Custom Field must be in place. Its install patch
    # (`add_security_field_to_investment_holding`) runs immediately before us
    # in the same v0_9_0 batch, so this should be true in practice.
    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "security"}
    ):
        return

    rows = frappe.db.sql(
        """
        SELECT name, item
          FROM `tabInvestment Holding`
         WHERE (security IS NULL OR security = '')
           AND item IS NOT NULL
           AND item != ''
        """,
        as_dict=True,
    )

    missing = []
    backfilled = 0

    for row in rows:
        security = frappe.db.get_value("Item", row.item, "custom_security")
        if not security:
            missing.append({"holding": row.name, "item": row.item})
            continue
        frappe.db.set_value(
            "Investment Holding",
            row.name,
            "security",
            security,
            update_modified=False,
        )
        backfilled += 1

    frappe.db.commit()

    if missing:
        frappe.log_error(
            message=frappe.as_json({"backfilled": backfilled, "missing": missing}),
            title="Polemarch v0_9_0: Holdings without linked Security",
        )
