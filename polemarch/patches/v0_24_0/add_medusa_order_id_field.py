"""v0_24_0 — `medusa_order_id` Custom Field on Security Sale.

Stamped by `polemarch.api.medusa_webhook._handle_order_placed` when
a storefront order syncs in. Used for idempotency: if a Sale with
this Medusa order id already exists, the webhook handler returns
the existing Sale instead of minting a duplicate.

Unique constraint enforced at the DB level so concurrent webhook
deliveries can't race past the existence check.

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field", {"dt": "Security Sale", "fieldname": "medusa_order_id"}
    ):
        return

    create_custom_fields(
        {
            "Security Sale": [
                {
                    "fieldname": "medusa_order_id",
                    "label": "Medusa Order ID",
                    "fieldtype": "Data",
                    "unique": 1,
                    "read_only": 1,
                    "no_copy": 1,
                    "in_standard_filter": 1,
                    "insert_after": "source",
                    "description": (
                        "Back-link to the Medusa order that triggered this "
                        "Sale (when source='Platform Purchase'). Empty for "
                        "Backend Order / Direct sales. Used for webhook "
                        "idempotency by the medusa_webhook receiver."
                    ),
                },
            ],
        },
        ignore_validate=True,
        update=True,
    )
