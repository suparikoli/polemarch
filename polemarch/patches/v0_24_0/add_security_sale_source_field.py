"""v0_24_0 — add `source` Select field to Security Sale.

Drives the storefront's order-source chip ("Platform Purchase" vs
"Backend Order"). Populated by:

  - Medusa push subscriber → "Platform Purchase" (customer placed it
    via the polemarch storefront)
  - ERPNext form / API → defaults to "Backend Order" (operator created
    it inside ERPNext)
  - Anything else → "Direct" (legacy / one-off block sales)

Storefront reads `source` via the Frappe REST API on order list
rendering. Indexed (`in_list_view`, `in_standard_filter`) so admins
can filter sales by source in the desk.

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field", {"dt": "Security Sale", "fieldname": "source"}
    ):
        return

    create_custom_fields(
        {
            "Security Sale": [
                {
                    "fieldname": "source",
                    "label": "Source",
                    "fieldtype": "Select",
                    "options": "Direct\nPlatform Purchase\nBackend Order",
                    "default": "Backend Order",
                    "in_list_view": 1,
                    "in_standard_filter": 1,
                    "insert_after": "party",
                    "description": (
                        "Where this Sale originated. Platform Purchase = "
                        "customer placed it via the Polemarch storefront "
                        "(synced from Medusa). Backend Order = operator "
                        "created it inside ERPNext. Direct = anything else."
                    ),
                },
            ],
        },
        ignore_validate=True,
        update=True,
    )

    # Backfill existing rows — anything created before this patch was
    # operator-entered, so "Backend Order" is the right default.
    frappe.db.sql(
        "UPDATE `tabSecurity Sale` SET source = %s WHERE source IS NULL OR source = ''",
        ("Backend Order",),
    )
    frappe.db.commit()
