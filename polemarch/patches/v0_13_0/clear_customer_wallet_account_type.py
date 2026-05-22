"""Phase 13 — clear account_type=Payable on Customer Wallet Liability.

ERPNext's Journal Entry validation enforces that any row whose account
has account_type=Payable MUST set party_type=Supplier. Same lock-in
for Receivable → Customer. Polemarch's Customer Wallet Liability account
is conceptually "we owe customers their wallet balance" — it belongs to
a Customer, not a Supplier, so the Payable account_type fights the JE
validation.

Clear it to "" (plain Current Liability). The wallet engine doesn't
need ERPNext's party-ledger plumbing — Wallet Transaction is the
authoritative ledger; the GL is just a mirror.

Idempotent.
"""

import frappe


def execute():
    rows = frappe.db.sql(
        """
        SELECT name
          FROM `tabAccount`
         WHERE account_name = 'Customer Wallet Liability'
           AND account_type IN ('Payable', 'Receivable')
        """,
        as_dict=True,
    )
    for row in rows:
        frappe.db.set_value(
            "Account", row.name, "account_type", "", update_modified=False
        )
    frappe.db.commit()
