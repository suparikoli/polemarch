"""Phase 12 — backfill new (party_type, party, payment_method, payment_account)
columns from the legacy (supplier|customer, paid_from_account|paid_to_account)
pair on existing Security Purchase / Sale rows.

Mapping:

  Security Purchase
    party_type      = 'Supplier'
    party           = supplier
    payment_method  = 'Default Payable' (or 'Bank' / 'Cash' if the old
                       paid_from_account looks like a Bank / Cash account)
    payment_account = paid_from_account

  Security Sale
    party_type      = 'Customer'
    party           = customer
    payment_method  = 'Default Receivable' (or 'Bank' / 'Cash' inferred from
                       paid_to_account.account_type)
    payment_account = paid_to_account

Idempotent. Only touches rows where the new columns are empty.
"""

import frappe


def execute():
    if frappe.db.has_column("Security Purchase", "party_type"):
        _backfill_security_purchase()
    if frappe.db.has_column("Security Sale", "party_type"):
        _backfill_security_sale()


def _backfill_security_purchase():
    rows = frappe.db.sql(
        """
        SELECT name, supplier, paid_from_account
          FROM `tabSecurity Purchase`
         WHERE (party_type IS NULL OR party_type = '')
        """,
        as_dict=True,
    )
    for row in rows:
        payment_method = _infer_method_payable(row.paid_from_account)
        frappe.db.set_value(
            "Security Purchase",
            row.name,
            {
                "party_type": "Supplier",
                "party": row.supplier or "",
                "payment_method": payment_method,
                "payment_account": row.paid_from_account or "",
            },
            update_modified=False,
        )
    frappe.db.commit()


def _backfill_security_sale():
    rows = frappe.db.sql(
        """
        SELECT name, customer, paid_to_account
          FROM `tabSecurity Sale`
         WHERE (party_type IS NULL OR party_type = '')
        """,
        as_dict=True,
    )
    for row in rows:
        payment_method = _infer_method_receivable(row.paid_to_account)
        frappe.db.set_value(
            "Security Sale",
            row.name,
            {
                "party_type": "Customer",
                "party": row.customer or "",
                "payment_method": payment_method,
                "payment_account": row.paid_to_account or "",
            },
            update_modified=False,
        )
    frappe.db.commit()


def _infer_method_payable(account: str) -> str:
    if not account:
        return "Default Payable"
    account_type = frappe.db.get_value("Account", account, "account_type") or ""
    if account_type == "Bank":
        return "Bank"
    if account_type == "Cash":
        return "Cash"
    return "Default Payable"


def _infer_method_receivable(account: str) -> str:
    if not account:
        return "Default Receivable"
    account_type = frappe.db.get_value("Account", account, "account_type") or ""
    if account_type == "Bank":
        return "Bank"
    if account_type == "Cash":
        return "Cash"
    return "Default Receivable"
