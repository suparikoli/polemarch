"""Customer-role scoping for trading doctypes.

Wired into `hooks.py` as `permission_query_conditions` + `has_permission`.

The single primitive `_customer_filter_for_user(user)` returns either:
  - `None`         → unrestricted access (System Manager, Accounts Manager,
                     Polemarch Compliance Officer)
  - a SQL fragment → the set of Customer names the user is allowed to see,
                     resolved via `Customer.email_id = user`

Each per-doctype `_perm_query` wraps that fragment in a `<table>.<column> IN
(...)` clause. `_has_permission` mirrors the logic for single-doc reads.

Gated by feature flag `CUSTOMER_ROLE_SCOPING` (default OFF). When OFF, the
hooks return `""` / `True` for all users, leaving existing role permissions
in charge — useful during phased rollout so a misconfigured role doesn't
lock everyone out.
"""

from typing import Optional

import frappe


_UNRESTRICTED_ROLES = {
    "System Manager",
    "Accounts Manager",
    "Polemarch Compliance Officer",
    "Polemarch Settlement Officer",
    "Polemarch Trader",
    "Administrator",
}


# ── permission_query_conditions ──────────────────────────────────────────


def wallet_perm_query(user=None):
    return _perm_query("Wallet", "customer", user)


def wallet_transaction_perm_query(user=None):
    return _perm_query("Wallet Transaction", "customer", user)


def trade_order_perm_query(user=None):
    return _perm_query("Trade Order", "customer", user)


def settlement_perm_query(user=None):
    return _perm_query("Settlement Instruction", "customer", user)


def security_position_perm_query(user=None):
    return _perm_query("Security Position", "customer", user)


def investment_disposal_perm_query(user=None):
    return _perm_query("Investment Disposal", "customer", user)


def portfolio_transfer_perm_query(user=None):
    # Classification-based PT doesn't carry a customer field directly; show all
    # to operators / nothing to scoped customers.
    if not _flag_on():
        return ""
    customer_filter = _customer_filter_for_user(user)
    if customer_filter is None:
        return ""
    return "1=0"  # Customers don't see Portfolio Transfers (operator-only)


# ── has_permission ───────────────────────────────────────────────────────


def wallet_has_permission(doc, user=None, permission_type=None):
    return _has_permission(doc, user, "customer")


def wallet_transaction_has_permission(doc, user=None, permission_type=None):
    return _has_permission(doc, user, "customer")


def trade_order_has_permission(doc, user=None, permission_type=None):
    return _has_permission(doc, user, "customer")


def settlement_has_permission(doc, user=None, permission_type=None):
    return _has_permission(doc, user, "customer")


def security_position_has_permission(doc, user=None, permission_type=None):
    return _has_permission(doc, user, "customer")


def investment_disposal_has_permission(doc, user=None, permission_type=None):
    return _has_permission(doc, user, "customer")


def portfolio_transfer_has_permission(doc, user=None, permission_type=None):
    if not _flag_on():
        return True
    user = user or frappe.session.user
    if _has_unrestricted_role(user):
        return True
    # Customer-scoped users don't get to see Portfolio Transfers.
    return False


# ── helpers ──────────────────────────────────────────────────────────────


def _flag_on() -> bool:
    from polemarch.polemarch_trading.feature_flags import is_enabled
    return is_enabled("CUSTOMER_ROLE_SCOPING")


def _has_unrestricted_role(user: str) -> bool:
    return bool(_UNRESTRICTED_ROLES & set(frappe.get_roles(user)))


def _customer_filter_for_user(user: Optional[str]) -> Optional[str]:
    """Returns a SQL fragment resolving to the Customer names this user
    is allowed to access, or None if unrestricted.

    The fragment is intended to be embedded inside `IN (...)`.
    """
    user = user or frappe.session.user
    if _has_unrestricted_role(user):
        return None
    return (
        f"SELECT name FROM `tabCustomer` WHERE email_id = {frappe.db.escape(user)}"
    )


def _perm_query(doctype: str, customer_column: str, user) -> str:
    if not _flag_on():
        return ""
    customer_filter = _customer_filter_for_user(user)
    if customer_filter is None:
        return ""
    return f"(`tab{doctype}`.{customer_column} IN ({customer_filter}))"


def _resolve_customer_for_user(user: str) -> Optional[str]:
    return frappe.db.get_value("Customer", {"email_id": user}, "name")


def _has_permission(doc, user, customer_field: str) -> bool:
    if not _flag_on():
        return True
    user = user or frappe.session.user
    if _has_unrestricted_role(user):
        return True
    customer = _resolve_customer_for_user(user)
    if not customer:
        return False
    return doc.get(customer_field) == customer
