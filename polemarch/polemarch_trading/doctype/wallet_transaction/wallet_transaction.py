import frappe
from frappe import _
from frappe.model.document import Document


# (direction, txn_type) consistency map. Anything outside this set is rejected.
_VALID_PAIRS = {
    ("Credit", "Deposit"),
    ("Debit", "Withdrawal"),
    ("Debit", "Reservation"),
    ("Credit", "Reservation Release"),
    ("Debit", "Buy Settlement"),
    ("Credit", "Sell Payout"),
    ("Debit", "Fee"),
    ("Credit", "Fee Refund"),
    ("Debit", "Adjustment"),
    ("Credit", "Adjustment"),
    ("Debit", "Reversal"),
    ("Credit", "Reversal"),
}


class WalletTransaction(Document):
    """Append-only wallet ledger entry.

    The doctype is submittable so cancellation goes through ERPNext's
    standard docstatus machinery. Edits after submit are forbidden except
    for the reversal flow which sets `is_cancelled`/`reversed_by` and is
    driven exclusively through `polemarch.polemarch_trading.wallet.reverse(...)`.
    """

    def validate(self):
        self._validate_amount_positive()
        self._validate_direction_txn_type_pair()
        self._validate_wallet_status()

    def before_submit(self):
        # The wallet engine fills balance_*_after before calling submit.
        # If a caller bypassed the engine and submits directly, force a
        # snapshot from the wallet header (best-effort; the engine path is
        # the only one that guarantees correctness under concurrency).
        if not self.balance_total_after:
            wallet = frappe.db.get_value(
                "Wallet",
                self.wallet,
                ("balance_available", "balance_reserved", "balance_total"),
                as_dict=True,
            )
            if wallet:
                self.balance_available_after = wallet.balance_available
                self.balance_reserved_after = wallet.balance_reserved
                self.balance_total_after = wallet.balance_total

    def on_update_after_submit(self):
        # Only `is_cancelled` and `reversed_by` may change post-submit, and
        # only through the reversal flow (flagged on the doc).
        if not getattr(self.flags, "from_reversal", False):
            frappe.throw(
                _(
                    "Wallet Transaction {0} is append-only. Use the reversal flow to "
                    "post a new compensating row instead of editing this one."
                ).format(self.name),
                title=_("Append-Only Ledger Violated"),
            )

    def on_cancel(self):
        # Hard-block cancel — reversal-via-new-row is the only undo path.
        frappe.throw(
            _(
                "Wallet Transaction {0} cannot be cancelled directly. Create a "
                "Reversal Wallet Transaction with reverses=<this row> instead."
            ).format(self.name),
            title=_("Cancel Not Allowed"),
        )

    def _validate_amount_positive(self):
        if (self.amount or 0) <= 0:
            frappe.throw(
                _("Wallet Transaction amount must be > 0 (got {0}).").format(self.amount),
                title=_("Invalid Amount"),
            )

    def _validate_direction_txn_type_pair(self):
        if (self.direction, self.txn_type) not in _VALID_PAIRS:
            frappe.throw(
                _("Direction {0} is not valid for txn_type {1}.").format(
                    self.direction, self.txn_type
                ),
                title=_("Invalid Transaction Direction"),
            )

    def _validate_wallet_status(self):
        if not self.wallet:
            return
        status = frappe.db.get_value("Wallet", self.wallet, "status")
        if status == "Closed":
            frappe.throw(
                _("Wallet {0} is Closed; no further transactions allowed.").format(self.wallet),
                title=_("Wallet Closed"),
            )
        if status == "Frozen" and self.txn_type not in ("Adjustment", "Reversal"):
            frappe.throw(
                _(
                    "Wallet {0} is Frozen; only Adjustment / Reversal transactions are permitted."
                ).format(self.wallet),
                title=_("Wallet Frozen"),
            )
