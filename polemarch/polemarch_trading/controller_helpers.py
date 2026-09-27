"""Shared helpers for Security Purchase / Security Sale controllers.

The two controllers are deliberately separate (different ledger
postings, different validate rules, different cancel-cascade ordering),
but they share a thin layer of generic plumbing: company-default
account lookups and name-based account resolution. Wallet Transaction
severance + reversal on cancel live in `customer_wallet.wallet`.

Anything that's identical or trivially parametrized between the two
controllers lives here. Anything that diverges (validate messages,
JE shape, FIFO planning) stays in the controller it belongs to.
"""

from typing import Optional

import frappe


# ── account lookup helpers ───────────────────────────────────────────


def resolve_account_by_name(company: str, account_name: str) -> Optional[str]:
    """Look up an Account by its `account_name` field (not the composite
    `name`). Returns the canonical `Account.name` suitable for storing in
    a Link field, or None if not found.

    ERPNext composes Account.name as `<account_number> - <account_name> -
    <abbr>` when account_number is set, so we can't just key on the
    operator-friendly label."""
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_name": account_name, "disabled": 0},
        "name",
    )


def company_default_bank_account(company: str) -> Optional[str]:
    """Return Company.default_bank_account if set, else the first
    non-disabled Bank-typed account."""
    direct = frappe.db.get_value("Company", company, "default_bank_account")
    if direct:
        return direct
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Bank", "disabled": 0, "is_group": 0},
        "name",
        order_by="creation ASC",
    )


def company_default_cash_account(company: str) -> Optional[str]:
    """Return Company.default_cash_account if set, else the first
    non-disabled Cash-typed account."""
    direct = frappe.db.get_value("Company", company, "default_cash_account")
    if direct:
        return direct
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Cash", "disabled": 0, "is_group": 0},
        "name",
        order_by="creation ASC",
    )


def company_default_cost_center(company: str) -> Optional[str]:
    return frappe.db.get_value("Company", company, "cost_center")


def is_receivable_or_payable(account: str) -> bool:
    """True if the account is Receivable / Payable typed — these require
    a (party_type, party) tag on every JE line that touches them."""
    return frappe.db.get_value("Account", account, "account_type") in (
        "Receivable",
        "Payable",
    )
