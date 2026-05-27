"""Shared helpers for Security Purchase / Security Sale controllers.

The two controllers are deliberately separate (different ledger
postings, different validate rules, different cancel-cascade ordering),
but they share a thin layer of generic plumbing: company-default
account lookups, name-based account resolution, Wallet Transaction
severance + reversal on cancel.

Anything that's identical or trivially parametrized between the two
controllers lives here. Anything that diverges (validate messages,
JE shape, FIFO planning) stays in the controller it belongs to.
"""

from typing import Iterable, Optional

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


def wallet_gl_account_for_customer(customer: str) -> Optional[str]:
    """Find the Customer Wallet Liability GL account for the customer's
    wallet (one wallet per customer, named `WAL-<customer>`)."""
    return frappe.db.get_value("Wallet", f"WAL-{customer}", "gl_liability_account")


def is_receivable_or_payable(account: str) -> bool:
    """True if the account is Receivable / Payable typed — these require
    a (party_type, party) tag on every JE line that touches them."""
    return frappe.db.get_value("Account", account, "account_type") in (
        "Receivable",
        "Payable",
    )


# ── wallet-transaction cancel cascade helpers ───────────────────────


def sever_wallet_transaction_link(source_doctype: str, source_name: str) -> list[str]:
    """Clear `reference_doctype` / `reference_name` on every Wallet
    Transaction pointing at `(source_doctype, source_name)`. Returns the
    list of WT names whose reverse-link was severed — the caller stashes
    these on `self.flags._pending_wt_reversal` so on_cancel can still
    reverse them after the forward pointer is gone.

    Why this is needed: Frappe's `check_links` runs BEFORE `on_cancel`
    and refuses to cancel a doc with active Dynamic Links pointing at it.
    Wallet Transaction (created on submit) has the source as its
    `reference_name`, which would block the source's cancel. We sever the
    link in `before_cancel`; the actual reversal still happens in
    `on_cancel`."""
    linked = frappe.get_all(
        "Wallet Transaction",
        filters={"reference_doctype": source_doctype, "reference_name": source_name},
        pluck="name",
    )
    for wt in linked:
        frappe.db.set_value(
            "Wallet Transaction",
            wt,
            {"reference_doctype": "", "reference_name": ""},
            update_modified=False,
        )
    return linked


def reverse_wallet_transactions(
    wt_names: Iterable[str], remarks: str
) -> int:
    """Idempotently reverse a set of Wallet Transactions. Skips rows that
    don't exist or are already cancelled. Returns the count actually
    reversed.

    Called from `on_cancel` after `before_cancel` has stashed the
    pending-reversal names on `self.flags._pending_wt_reversal`."""
    from polemarch.polemarch_trading import wallet as wallet_engine

    reversed_count = 0
    for original_wt in wt_names:
        if not frappe.db.exists("Wallet Transaction", original_wt):
            continue
        if frappe.db.get_value("Wallet Transaction", original_wt, "is_cancelled"):
            continue
        wallet_engine.reverse(original_wt, remarks=remarks)
        reversed_count += 1
    return reversed_count
