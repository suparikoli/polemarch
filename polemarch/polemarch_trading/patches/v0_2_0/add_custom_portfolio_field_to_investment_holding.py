"""Add `custom_portfolio` Link field to the existing Investment Holding doctype.

Lets us classify each legacy Investment Holding into a Trading or Investment
portfolio without touching the doctype JSON (which is shipped in
polemarch_customizations and is mutated only via patches per project
convention).

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists("Custom Field", {"dt": "Investment Holding", "fieldname": "custom_portfolio"}):
        return

    create_custom_fields(
        {
            "Investment Holding": [
                {
                    "fieldname": "custom_portfolio",
                    "label": "Portfolio",
                    "fieldtype": "Link",
                    "options": "Portfolio",
                    "insert_after": "company",
                    "no_copy": 0,
                    "in_standard_filter": 1,
                    "description": (
                        "Polemarch Portfolio classification (Trading vs Investment). "
                        "Backfilled by patch backfill_portfolio_on_holdings for existing rows."
                    ),
                }
            ]
        },
        update=True,
    )
