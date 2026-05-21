"""Wallet engine — atomic balance mutations under row-level lock.

Every balance change MUST go through `apply_delta(...)` so that:
  1. A `SELECT … FOR UPDATE` holds the Wallet row exclusively for the txn.
  2. The Wallet Transaction (ledger row) is inserted *inside* that lock,
     with `balance_*_after` snapshotted from the locked state.
  3. The Wallet's balance + version are bumped in a single UPDATE statement.

If any step fails the whole transaction rolls back.

Direct `wallet_doc.save()` of balance fields will trip the validate-time
invariant check, but the real safety net is structural — operational code
calls only this module.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, now_datetime


# Effect of (direction, txn_type) on (available, reserved, total) balances.
# Each tuple is the signed delta to apply.
#   (Δ available, Δ reserved, Δ total)
_DELTA_MAP = {
    # Credits
    ("Credit", "Deposit"):              (+1,  0, +1),
    ("Credit", "Reservation Release"):  (+1, -1,  0),
    ("Credit", "Sell Payout"):          (+1,  0, +1),
    ("Credit", "Fee Refund"):           (+1,  0, +1),
    ("Credit", "Adjustment"):           (+1,  0, +1),
    ("Credit", "Reversal"):             (+1,  0, +1),
    # Debits
    ("Debit",  "Withdrawal"):           (-1,  0, -1),
    ("Debit",  "Reservation"):          (-1, +1,  0),
    ("Debit",  "Buy Settlement"):       ( 0, -1, -1),
    ("Debit",  "Fee"):                  (-1,  0, -1),
    ("Debit",  "Adjustment"):           (-1,  0, -1),
    ("Debit",  "Reversal"):             (-1,  0, -1),
}


def apply_delta(
    wallet: str,
    txn_type: str,
    direction: str,
    amount: float,
    reference_doctype: Optional[str] = None,
    reference_name: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    remarks: Optional[str] = None,
    reverses: Optional[str] = None,
) -> str:
    """Apply a wallet delta atomically; insert + submit a Wallet Transaction.

    Returns the new Wallet Transaction name.
    """
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Wallet amount must be > 0."), title=_("Invalid Amount"))

    pair = (direction, txn_type)
    if pair not in _DELTA_MAP:
        frappe.throw(
            _("Direction {0} not valid for txn_type {1}.").format(direction, txn_type),
            title=_("Invalid Direction/Type"),
        )

    if idempotency_key:
        existing = frappe.db.get_value(
            "Wallet Transaction",
            {"idempotency_key": idempotency_key, "docstatus": ["!=", 2]},
            "name",
        )
        if existing:
            return existing

    # Lock the wallet row. Frappe wraps each request in a single MySQL
    # transaction by default, so this lock is held until commit/rollback.
    locked = frappe.db.sql(
        """
        SELECT name, balance_available, balance_reserved, balance_total,
               balance_pending, status, version
          FROM `tabWallet`
         WHERE name = %s
           FOR UPDATE
        """,
        (wallet,),
        as_dict=True,
    )
    if not locked:
        frappe.throw(_("Wallet {0} not found.").format(wallet), title=_("Wallet Missing"))
    state = locked[0]

    if state.status == "Closed":
        frappe.throw(_("Wallet {0} is Closed.").format(wallet), title=_("Wallet Closed"))
    if state.status == "Frozen" and txn_type not in ("Adjustment", "Reversal"):
        frappe.throw(_("Wallet {0} is Frozen.").format(wallet), title=_("Wallet Frozen"))

    d_avail, d_reserved, d_total = _DELTA_MAP[pair]
    new_available = flt(state.balance_available) + d_avail * amount
    new_reserved = flt(state.balance_reserved) + d_reserved * amount
    new_total = flt(state.balance_total) + d_total * amount

    if new_available < -0.01 or new_reserved < -0.01 or new_total < -0.01:
        frappe.throw(
            _(
                "Wallet {0}: applying {1} {2} of ₹{3} would drive balances negative "
                "(available={4}, reserved={5}, total={6})."
            ).format(wallet, direction, txn_type, amount, new_available, new_reserved, new_total),
            title=_("Insufficient Funds"),
        )

    wt = frappe.get_doc(
        {
            "doctype": "Wallet Transaction",
            "wallet": wallet,
            "posting_datetime": now_datetime(),
            "txn_type": txn_type,
            "direction": direction,
            "amount": amount,
            "balance_available_after": new_available,
            "balance_reserved_after": new_reserved,
            "balance_total_after": new_total,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "idempotency_key": idempotency_key,
            "remarks": remarks,
            "reverses": reverses,
        }
    )
    wt.flags.ignore_permissions = True
    wt.insert(ignore_permissions=True)
    wt.submit()

    frappe.db.sql(
        """
        UPDATE `tabWallet`
           SET balance_available = %s,
               balance_reserved  = %s,
               balance_total     = %s,
               version           = version + 1,
               modified          = %s
         WHERE name = %s
        """,
        (new_available, new_reserved, new_total, now_datetime(), wallet),
    )

    return wt.name


def reverse(wallet_transaction: str, remarks: Optional[str] = None) -> str:
    """Reverse a submitted Wallet Transaction by posting a new opposite-direction WT.

    Marks the original row's `is_cancelled=1` and `reversed_by=<new>`.
    """
    original = frappe.get_doc("Wallet Transaction", wallet_transaction)
    if original.docstatus != 1:
        frappe.throw(
            _("Only submitted Wallet Transactions can be reversed (got docstatus={0}).").format(
                original.docstatus
            ),
            title=_("Reversal Not Allowed"),
        )
    if original.is_cancelled:
        frappe.throw(
            _("Wallet Transaction {0} is already reversed.").format(wallet_transaction),
            title=_("Already Reversed"),
        )

    opposite_direction = "Credit" if original.direction == "Debit" else "Debit"

    new_name = apply_delta(
        wallet=original.wallet,
        txn_type="Reversal",
        direction=opposite_direction,
        amount=original.amount,
        reference_doctype=original.reference_doctype,
        reference_name=original.reference_name,
        remarks=remarks or f"Reversal of {wallet_transaction}",
        reverses=wallet_transaction,
    )

    # Mark the original — bypassing the append-only guard via from_reversal flag.
    original.flags.from_reversal = True
    original.is_cancelled = 1
    original.reversed_by = new_name
    original.db_update()

    return new_name


def get_balance(wallet: str) -> dict:
    """Read-only balance snapshot. No lock; suitable for display."""
    row = frappe.db.get_value(
        "Wallet",
        wallet,
        (
            "name",
            "customer",
            "currency",
            "balance_available",
            "balance_reserved",
            "balance_total",
            "balance_pending",
            "status",
            "version",
        ),
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Wallet {0} not found.").format(wallet), title=_("Wallet Missing"))
    return row
