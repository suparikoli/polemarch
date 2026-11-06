"""Connections panel of Security Sale: the wallet rows it settled."""

from frappe import _


def get_data():
    return {
        "fieldname": "security_sale",
        "transactions": [{"label": _("Wallet"), "items": ["Wallet Transaction"]}],
    }
