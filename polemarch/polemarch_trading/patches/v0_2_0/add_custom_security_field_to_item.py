"""Add `custom_security` Link field to Item, pointing at Security.

Idempotent: skipped if the Custom Field already exists.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "custom_security"}):
        return

    create_custom_fields(
        {
            "Item": [
                {
                    "fieldname": "custom_security",
                    "label": "Polemarch Security",
                    "fieldtype": "Link",
                    "options": "Security",
                    "insert_after": "item_group",
                    "read_only": 0,
                    "no_copy": 1,
                    "description": (
                        "1:1 link to the Polemarch Security master. Backfilled from existing "
                        "brand=Polemarch items by patch backfill_security_from_polemarch_items."
                    ),
                }
            ]
        },
        update=True,
    )
