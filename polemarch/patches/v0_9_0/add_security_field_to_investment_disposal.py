"""Phase 9 — add `security` Custom Field on Investment Disposal.

Disposal becomes Security-keyed alongside Investment Holding. The legacy
`item` field stays for one release for backward compat — the matching
`make_item_optional_on_investment_disposal` patch flips its `reqd` flag
so new Disposals from Security Sale can leave it NULL.

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field", {"dt": "Investment Disposal", "fieldname": "security"}
    ):
        return

    create_custom_fields(
        {
            "Investment Disposal": [
                {
                    "fieldname": "security",
                    "label": "Security",
                    "fieldtype": "Link",
                    "options": "Security",
                    "insert_after": "naming_series",
                    "in_list_view": 1,
                    "in_standard_filter": 1,
                    "description": (
                        "Polemarch trading identity for the disposed instrument. "
                        "Backfilled from item.custom_security for legacy rows; "
                        "set directly by Security Sale for new ones."
                    ),
                }
            ]
        },
        update=True,
    )
