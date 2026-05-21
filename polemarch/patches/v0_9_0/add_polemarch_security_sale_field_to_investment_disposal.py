"""Phase 9 — add `polemarch_security_sale` back-link on Investment Disposal.

Security Sale.on_submit creates an Investment Disposal and stamps this
field with self.name so audits + dashboards can navigate Sale ↔ Disposal
both ways (mirrors the existing `polemarch_trade_order` link from
Phase 8).

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field",
        {"dt": "Investment Disposal", "fieldname": "polemarch_security_sale"},
    ):
        return

    create_custom_fields(
        {
            "Investment Disposal": [
                {
                    "fieldname": "polemarch_security_sale",
                    "label": "Security Sale",
                    "fieldtype": "Link",
                    "options": "Security Sale",
                    "read_only": 1,
                    "no_copy": 1,
                    "in_standard_filter": 1,
                    "insert_after": "polemarch_trade_order",
                    "description": (
                        "Back-link to the Security Sale whose submit created "
                        "this Disposal. Set by polemarch_trading.doctype."
                        "security_sale on submit."
                    ),
                }
            ]
        },
        update=True,
    )
