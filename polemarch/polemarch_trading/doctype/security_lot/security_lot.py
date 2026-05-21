import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SecurityLot(Document):
    """Acquisition lot. Derived quantities are computed from SLLE rollup.

    The row itself is mutable for housekeeping (status / lot_state / notes),
    but qty_disposed / qty_remaining / qty_reserved are NEVER set by callers
    — they are recomputed from the Security Lot Ledger Entry table on every
    `validate()`.
    """

    def validate(self):
        self._validate_qty_acquired()
        self._validate_owner_kind_consistency()
        self._compute_total_cost()
        self._recompute_from_slle()
        self._update_status()

    def on_trash(self):
        if frappe.db.table_exists("Security Lot Ledger Entry"):
            if frappe.db.exists("Security Lot Ledger Entry", {"security_lot": self.name}):
                frappe.throw(
                    _("Cannot delete Security Lot {0}: SLLE rows reference it.").format(self.name),
                    title=_("Security Lot In Use"),
                )

    def _validate_qty_acquired(self):
        if flt(self.qty_acquired) <= 0:
            frappe.throw(
                _("Security Lot qty_acquired must be > 0 (got {0}).").format(self.qty_acquired),
                title=_("Invalid Quantity"),
            )
        if flt(self.cost_basis_per_unit) < 0:
            frappe.throw(
                _("Cost basis per unit cannot be negative."),
                title=_("Invalid Cost Basis"),
            )

    def _validate_owner_kind_consistency(self):
        if not self.portfolio:
            return
        owner_kind = frappe.db.get_value("Portfolio", self.portfolio, "owner_kind")
        if owner_kind == "Proprietary" and self.owning_customer:
            frappe.throw(
                _(
                    "Security Lot {0}: portfolio {1} is Proprietary; owning_customer must be blank."
                ).format(self.name, self.portfolio),
                title=_("Portfolio Owner Conflict"),
            )
        if owner_kind == "Customer" and not self.owning_customer:
            frappe.throw(
                _(
                    "Security Lot {0}: portfolio {1} is Customer-owned; owning_customer is required."
                ).format(self.name, self.portfolio),
                title=_("Portfolio Owner Required"),
            )

    def _compute_total_cost(self):
        self.total_cost = flt(self.qty_acquired) * flt(self.cost_basis_per_unit)

    def _recompute_from_slle(self):
        if self.is_new() or not frappe.db.table_exists("Security Lot Ledger Entry"):
            # Pre-Phase-1 or row hasn't been inserted yet — defaults are fine.
            self.qty_disposed = self.qty_disposed or 0
            self.qty_reserved = self.qty_reserved or 0
            self.qty_remaining = flt(self.qty_acquired) - flt(self.qty_disposed)
            self.remaining_cost = flt(self.qty_remaining) * flt(self.cost_basis_per_unit)
            return

        # Consume rollup: sum of Consume entries minus their cancelling Reversals
        # (Reversal entries with reverses=<Consume entry> negate the original).
        rows = frappe.db.sql(
            """
            SELECT entry_type,
                   COALESCE(SUM(CASE WHEN is_cancelled = 0 THEN qty ELSE 0 END), 0) AS active_qty
              FROM `tabSecurity Lot Ledger Entry`
             WHERE security_lot = %(lot)s
               AND docstatus = 1
             GROUP BY entry_type
            """,
            {"lot": self.name},
            as_dict=True,
        )
        by_type = {r.entry_type: flt(r.active_qty) for r in rows}

        consumed = by_type.get("Consume", 0) - by_type.get("Reversal", 0)
        # Reserve / Reserve Release net out for outstanding reservations.
        reserved = by_type.get("Reserve", 0) - by_type.get("Reserve Release", 0)

        self.qty_disposed = max(consumed, 0)
        self.qty_reserved = max(reserved, 0)
        self.qty_remaining = flt(self.qty_acquired) - flt(self.qty_disposed)
        self.remaining_cost = flt(self.qty_remaining) * flt(self.cost_basis_per_unit)

    def _update_status(self):
        if flt(self.qty_remaining) <= 0:
            self.status = "Fully Disposed"
            self.lot_state = "Closed"
        elif flt(self.qty_disposed) > 0:
            self.status = "Partially Disposed"
            if self.lot_state in (None, "", "Sealed"):
                self.lot_state = "Open"
        else:
            self.status = "Open"
            if self.lot_state in (None, "", "Sealed"):
                self.lot_state = "Open"
