"""Phase 10 — add `customer` Custom Field on Investment Holding.

Holdings become ownership-aware. NULL = Polemarch proprietary inventory;
populated = owned by that Customer. The FIFO engine reads this field so
a customer-Sell consumes the customer's own Holdings instead of dipping
into proprietary inventory, and a customer-Buy mints a new Holding owned
by the buyer (proprietary inventory drops by the corresponding Disposal).

All pre-Phase-10 Holdings stay NULL — those were Polemarch's own.

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "customer"}
    ):
        return

    create_custom_fields(
        {
            "Investment Holding": [
                {
                    "fieldname": "customer",
                    "label": "Customer Owner",
                    "fieldtype": "Link",
                    "options": "Customer",
                    "insert_after": "company",
                    "in_list_view": 1,
                    "in_standard_filter": 1,
                    "description": (
                        "Customer who owns this Holding. Blank = Polemarch "
                        "proprietary inventory. FIFO selection filters on this "
                        "field via the customer_filter parameter."
                    ),
                }
            ]
        },
        update=True,
    )
