"""Phase 9 — promote Security to Investment Holding's primary identity.

Add `security` (Link Security) as a Custom Field on Investment Holding.
Keep the existing `item` field around for one release so any legacy
queries don't 500 — the backfill (`backfill_security_on_holdings`) lands
right after this and populates `security` from `item.custom_security`,
and a later patch (`drop_item_field_from_holdings`) removes `item`.

Idempotent. Safe to re-run.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "security"}
    ):
        return

    create_custom_fields(
        {
            "Investment Holding": [
                {
                    "fieldname": "security",
                    "label": "Security",
                    "fieldtype": "Link",
                    "options": "Security",
                    "insert_after": "naming_series",
                    "in_list_view": 1,
                    "in_standard_filter": 1,
                    "description": (
                        "Polemarch trading identity for the held instrument. "
                        "Independent of any ERPNext Item — Security Purchase / "
                        "Security Sale doctypes write Holdings directly against "
                        "this field. Becomes required after the backfill patch."
                    ),
                }
            ]
        },
        update=True,
    )
