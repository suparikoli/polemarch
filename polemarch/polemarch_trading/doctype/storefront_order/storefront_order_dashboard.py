"""Connections panel of Storefront Order: its sales and wallet rows."""

from frappe import _


def get_data():
    return {
        "fieldname": "storefront_order",
        "transactions": [{"label": _("Linked"), "items": ["Security Sale", "Wallet Transaction"]}],
    }
