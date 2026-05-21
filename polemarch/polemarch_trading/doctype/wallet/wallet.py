import frappe
from frappe import _
from frappe.model.document import Document


class Wallet(Document):
    """Per-customer cash wallet.

    Balances are a materialised view of the immutable Wallet Transaction
    ledger. Direct `doc.save()` of balance fields is forbidden — use
    `polemarch.polemarch_trading.wallet.apply_delta(...)` which holds a row-level
    `FOR UPDATE` lock for the duration of the mutation.
    """

    def validate(self):
        self._validate_balance_consistency()
        self._validate_non_negative_balances()
        self._validate_status_transition()

    def on_trash(self):
        if frappe.db.exists("Wallet Transaction", {"wallet": self.name}):
            frappe.throw(
                _("Cannot delete Wallet {0}: Wallet Transaction history exists.").format(self.name),
                title=_("Wallet In Use"),
            )

    def _validate_balance_consistency(self):
        total = (self.balance_available or 0) + (self.balance_reserved or 0)
        # Allow ±₹0.01 tolerance for floating-point rounding (Currency is 6dp).
        if abs((self.balance_total or 0) - total) > 0.01:
            frappe.throw(
                _(
                    "Wallet balance inconsistency: total {0} != available {1} + reserved {2}"
                ).format(self.balance_total, self.balance_available, self.balance_reserved),
                title=_("Wallet Balance Invariant Violated"),
            )

    def _validate_non_negative_balances(self):
        for field in ("balance_total", "balance_available", "balance_reserved", "balance_pending"):
            value = self.get(field) or 0
            if value < 0:
                frappe.throw(
                    _("Wallet {0} cannot be negative ({1}={2}).").format(self.name, field, value),
                    title=_("Negative Wallet Balance"),
                )

    def _validate_status_transition(self):
        if self.is_new():
            return
        old_status = (self.get_doc_before_save() or {}).get("status") if not isinstance(
            self.get_doc_before_save(), type(None)
        ) else None
        if old_status is None:
            old_status = frappe.db.get_value("Wallet", self.name, "status")
        if old_status == "Closed" and self.status != "Closed":
            frappe.throw(
                _("Wallet {0} is Closed (terminal); cannot transition back to {1}.").format(
                    self.name, self.status
                ),
                title=_("Wallet Status Locked"),
            )
