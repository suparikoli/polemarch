"""Tie Wallet Transactions to what they paid for.

The wallet app's ledger is generic; polemarch adds Custom Fields to Wallet
Transaction (see install.py): `particulars`, `security`, `qty`, `rate`,
`security_sale`, `storefront_order`, `external_reference`,
`cashfree_reference` and `linked_transaction`. They are descriptive only and written with set_value
because a Wallet Transaction is submitted and append-only: no balance or
GL effect.
"""

import frappe
from frappe.utils import flt


def tag(wallet_transaction, **values):
    if wallet_transaction:
        frappe.db.set_value("Wallet Transaction", wallet_transaction, values, update_modified=False)


def describe_trade(verb, security, qty, rate):
    name = frappe.db.get_value("Security", security, "security_name") or security
    return f"{verb} {flt(qty):,.0f} {name} @ ₹{flt(rate):,.2f}"
