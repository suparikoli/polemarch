"""Phase 9 — backfill `Investment Disposal.security` from `Item.custom_security`.

Mirrors `backfill_security_on_holdings` for Disposals. Every existing
Disposal has `item` populated; resolve it through Item.custom_security and
stamp the new `security` field.

Idempotent.
"""

import frappe


def execute():
    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Disposal", "fieldname": "security"}
    ):
        return

    rows = frappe.db.sql(
        """
        SELECT name, item
          FROM `tabInvestment Disposal`
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
            missing.append({"disposal": row.name, "item": row.item})
            continue
        frappe.db.set_value(
            "Investment Disposal",
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
            title="Polemarch v0_9_0: Disposals without linked Security",
        )
