"""v0_24_0 — `medusa_product_id` Custom Field on Security.

Set by `polemarch.api.medusa_webhook._handle_product_synced` after the
Medusa side creates a Product mirroring this Security. Enables
cross-system lookup without the Medusa plugin having to maintain its
own mapping table.

Securities are Frappe-managed (operator creates them via the desk).
The Medusa side derives its Product catalog from Frappe via the pull
cron. This field is the back-pointer Medusa stamps once the Medusa
Product exists.

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field", {"dt": "Security", "fieldname": "medusa_product_id"}
    ):
        return

    create_custom_fields(
        {
            "Security": [
                {
                    "fieldname": "medusa_product_id",
                    "label": "Medusa Product ID",
                    "fieldtype": "Data",
                    "unique": 1,
                    "read_only": 1,
                    "no_copy": 1,
                    "in_standard_filter": 1,
                    "insert_after": "calcula_url"
                    if frappe.db.exists(
                        "Custom Field", {"dt": "Security", "fieldname": "calcula_url"}
                    )
                    else "isin",
                    "description": (
                        "Back-reference to the Medusa Product that mirrors "
                        "this Security. Stamped by the Medusa webhook "
                        "receiver after the storefront-side Product is "
                        "created. Empty for Securities not yet listed on "
                        "the storefront."
                    ),
                },
            ],
        },
        ignore_validate=True,
        update=True,
    )
