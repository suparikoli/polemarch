"""Wallet REST endpoints (Phase 5 — customer-facing).

All endpoints scope to the caller's Customer when invoked under the Customer
role; back-office roles (System Manager, Accounts Manager, Polemarch
Settlement Officer) can act on any wallet by passing `customer=...` explicitly.

Mutating endpoints accept `idempotency_key` for safe replay. The kernel
wallet engine ALSO enforces idempotency on the underlying Wallet Transaction
row — these two layers are belt-and-braces.

Rate limits are conservative defaults; tune via `site_config.json` if needed.
"""

from typing import Optional

import frappe
from frappe import _

from polemarch.trading.api import _idempotency
from polemarch.trading.api._ratelimit import rate_limit


_CUSTOMER_OR_OPS = [
    "Customer",
    "System Manager",
    "Accounts Manager",
    "Polemarch Settlement Officer",
]


# ── Reads ────────────────────────────────────────────────────────────────


@frappe.whitelist()
@rate_limit(per_min=60, scope="user")
def get_balance(customer: Optional[str] = None):
    """Returns the wallet snapshot for the resolved customer."""
    customer = _resolve_customer(customer)
    wallet = f"WAL-{customer}"
    if not frappe.db.exists("Wallet", wallet):
        frappe.throw(
            _("No wallet found for customer {0}.").format(customer),
            title=_("Wallet Missing"),
        )

    from polemarch.trading import wallet as wallet_engine
    row = wallet_engine.get_balance(wallet)
    row["last_txn_at"] = frappe.db.get_value(
        "Wallet Transaction",
        {"wallet": wallet, "docstatus": 1},
        "posting_datetime",
        order_by="posting_datetime DESC",
    )
    return row


@frappe.whitelist()
@rate_limit(per_min=60, scope="user")
def list_transactions(
    customer: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    txn_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    customer = _resolve_customer(customer)
    wallet = f"WAL-{customer}"
    filters = {"wallet": wallet, "docstatus": 1}
    if txn_type:
        filters["txn_type"] = txn_type
    if from_date or to_date:
        filters["posting_datetime"] = [
            "between",
            [from_date or "1970-01-01 00:00:00", to_date or "2099-12-31 23:59:59"],
        ]

    rows = frappe.get_all(
        "Wallet Transaction",
        filters=filters,
        fields=[
            "name",
            "posting_datetime",
            "txn_type",
            "direction",
            "amount",
            "balance_total_after",
            "balance_available_after",
            "reference_doctype",
            "reference_name",
            "is_cancelled",
            "remarks",
        ],
        order_by="posting_datetime DESC, creation DESC",
        limit_page_length=min(int(limit), 200),
        limit_start=int(offset),
    )
    return {
        "rows": rows,
        "total": frappe.db.count("Wallet Transaction", filters=filters),
        "limit": int(limit),
        "offset": int(offset),
    }


# ── Writes ───────────────────────────────────────────────────────────────


@frappe.whitelist()
@rate_limit(per_min=100, scope="ip")
def deposit(
    customer: str,
    amount: float,
    source_ref: str,
    bank_account: Optional[str] = None,
    idempotency_key: Optional[str] = None,
):
    """Bank-side webhook entry point. Restricted to back-office; the customer
    never hits this directly — funds enter the wallet via reconciled Bank
    Transactions in the Phase 5+ hook (Phase 2 currently stubbed)."""
    frappe.only_for(
        ["System Manager", "Accounts Manager", "Polemarch Settlement Officer"],
        message=_("Not allowed to deposit into customer wallets."),
    )

    with _idempotency.scope(
        "polemarch.trading.api.wallet.deposit", idempotency_key, locals()
    ) as ctx:
        if ctx.replay:
            return ctx.replay

        from polemarch.trading import wallet as wallet_engine
        wt_name = wallet_engine.apply_delta(
            wallet=f"WAL-{customer}",
            txn_type="Deposit",
            direction="Credit",
            amount=float(amount),
            reference_doctype="Bank Transaction" if not source_ref.startswith("ext:") else None,
            reference_name=source_ref if not source_ref.startswith("ext:") else None,
            idempotency_key=f"api-deposit:{idempotency_key}" if idempotency_key else None,
            remarks=f"Deposit (source: {source_ref}, bank: {bank_account or '-'})",
        )
        response = {"wallet_transaction": wt_name, "balance": _balance_snapshot(customer)}
        ctx.store(response)
        return response


@frappe.whitelist()
@rate_limit(per_min=5, scope="customer")
def withdraw(
    customer: Optional[str] = None,
    amount: float = 0,
    target_bank: Optional[str] = None,
    idempotency_key: Optional[str] = None,
):
    """Customer-initiated payout request. Posts a Withdrawal Wallet Transaction
    immediately — back-office downstream picks it up to drive the NEFT/IMPS
    payout (settle via `Wallet Transaction` reference on the bank-side flow)."""
    frappe.only_for(_CUSTOMER_OR_OPS, message=_("Not allowed to withdraw."))
    customer = _resolve_customer(customer)
    amount = float(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be positive."), title=_("Invalid Amount"))

    with _idempotency.scope(
        "polemarch.trading.api.wallet.withdraw", idempotency_key, locals()
    ) as ctx:
        if ctx.replay:
            return ctx.replay

        from polemarch.trading import wallet as wallet_engine
        wt_name = wallet_engine.apply_delta(
            wallet=f"WAL-{customer}",
            txn_type="Withdrawal",
            direction="Debit",
            amount=amount,
            reference_doctype=None,
            reference_name=None,
            idempotency_key=f"api-withdraw:{idempotency_key}" if idempotency_key else None,
            remarks=f"Withdrawal requested to bank {target_bank or '-'}",
        )
        response = {
            "wallet_transaction": wt_name,
            "status": "Pending payout",
            "balance": _balance_snapshot(customer),
        }
        ctx.store(response)
        return response


@frappe.whitelist()
@rate_limit(per_min=30, scope="customer")
def reserve(
    customer: Optional[str] = None,
    amount: float = 0,
    trade_order: Optional[str] = None,
    idempotency_key: Optional[str] = None,
):
    """Internal-facing — the Trade Order submit path already calls this via
    the matching engine. Exposed for back-office reconciliation/repair flows."""
    frappe.only_for(
        ["System Manager", "Accounts Manager", "Polemarch Settlement Officer", "Polemarch Trader"],
        message=_("Not allowed to reserve wallet funds."),
    )
    customer = _resolve_customer(customer)
    amount = float(amount)

    with _idempotency.scope(
        "polemarch.trading.api.wallet.reserve", idempotency_key, locals()
    ) as ctx:
        if ctx.replay:
            return ctx.replay

        from polemarch.trading import wallet as wallet_engine
        wt_name = wallet_engine.apply_delta(
            wallet=f"WAL-{customer}",
            txn_type="Reservation",
            direction="Debit",
            amount=amount,
            reference_doctype="Trade Order" if trade_order else None,
            reference_name=trade_order,
            idempotency_key=f"api-reserve:{idempotency_key}" if idempotency_key else None,
            remarks=f"Reserved for Trade Order {trade_order or '-'}",
        )
        response = {
            "wallet_transaction": wt_name,
            "balance": _balance_snapshot(customer),
        }
        ctx.store(response)
        return response


@frappe.whitelist()
@rate_limit(per_min=30, scope="customer")
def release_reservation(
    wallet_transaction: str,
    idempotency_key: Optional[str] = None,
):
    """Reverse a Reservation row via the canonical wallet.reverse() path."""
    frappe.only_for(
        ["System Manager", "Accounts Manager", "Polemarch Settlement Officer"],
        message=_("Not allowed to release reservations."),
    )

    with _idempotency.scope(
        "polemarch.trading.api.wallet.release_reservation", idempotency_key, locals()
    ) as ctx:
        if ctx.replay:
            return ctx.replay

        from polemarch.trading import wallet as wallet_engine
        new_name = wallet_engine.reverse(wallet_transaction, remarks="Reservation released via API")
        original = frappe.db.get_value("Wallet Transaction", wallet_transaction, "wallet")
        customer = frappe.db.get_value("Wallet", original, "customer") if original else None
        response = {
            "reversed_by": new_name,
            "balance": _balance_snapshot(customer) if customer else None,
        }
        ctx.store(response)
        return response


# ── helpers ──────────────────────────────────────────────────────────────


def _resolve_customer(customer: Optional[str]) -> str:
    """For Customer-role calls, default to the session's mapped Customer.
    For back-office calls, require `customer` explicitly."""
    user_roles = set(frappe.get_roles(frappe.session.user))
    if customer:
        # Back-office can target any; Customer role can only target their own.
        if "Customer" in user_roles and not (
            user_roles & {"System Manager", "Accounts Manager", "Polemarch Settlement Officer", "Polemarch Trader"}
        ):
            mapped = frappe.db.get_value("Customer", {"email_id": frappe.session.user}, "name")
            if mapped != customer:
                frappe.throw(_("Customer mismatch."), title=_("Forbidden"))
        return customer

    mapped = frappe.db.get_value("Customer", {"email_id": frappe.session.user}, "name")
    if not mapped:
        frappe.throw(
            _("No Customer mapped to user {0}.").format(frappe.session.user),
            title=_("Customer Mapping Missing"),
        )
    return mapped


def _balance_snapshot(customer: str) -> dict:
    from polemarch.trading import wallet as wallet_engine
    return wallet_engine.get_balance(f"WAL-{customer}")
