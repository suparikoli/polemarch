"""Investment Holding — one row per (item × acquisition lot) that
Polemarch owns on its own books.

Lifecycle:
  - Created manually (or by a Purchase Invoice hook in Phase 2) when
    Polemarch acquires shares to its own inventory.
  - Consumed FIFO by `Investment Disposal` when those shares are sold
    out to retail. Each disposal subtracts from `qty_remaining` and
    bumps `qty_disposed`. `status` auto-updates Open →
    Partially Disposed → Fully Disposed.

The cost-basis snapshot lives here, not on the Item: the same security
can sit across multiple lots at different acquisition prices, and
capital-gains math (LTCG / STCG) needs the per-lot acquisition_date
+ cost_basis_per_unit. FIFO matches oldest lot first by default.

Not submittable — this is master data, edited freely until disposal
locks portions of it. Hard delete is allowed only when no Disposal
rows reference it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class InvestmentHolding(Document):
    def validate(self):
        self._validate_qty_consistency()
        self._compute_derived_fields()
        self._update_status()

    def before_insert(self):
        # Set classification + deadline if the new fields are present.
        # Done in before_insert so the values land in the inserted row.
        self._set_initial_classification()

    def _set_initial_classification(self):
        """First-save defaults for classification fields. Safe to call before
        the v0_8_0 patch has installed the Custom Fields — getattr falls
        through cleanly."""
        if not hasattr(self, "classification"):
            return
        if not self.classification:
            self.classification = "Unallocated"
        if not self.classification_deadline:
            try:
                from polemarch.polemarch_trading.classification import compute_deadline
                self.classification_deadline = compute_deadline(
                    frappe.utils.now_datetime(), self.company
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Polemarch Classification deadline compute",
                )

    def on_trash(self):
        # Block deletion if any Investment Disposal Lot references this
        # holding — the cost-basis snapshot is needed to recompute
        # capital gains during audits.
        consumed = frappe.db.exists(
            "Investment Disposal Lot",
            {"holding": self.name},
        )
        if consumed:
            frappe.throw(
                _(
                    "Cannot delete Investment Holding {0}: it is referenced "
                    "by one or more Investment Disposal records. Cancel and "
                    "amend those first."
                ).format(self.name)
            )

    def _validate_qty_consistency(self):
        if flt(self.qty_acquired) <= 0:
            frappe.throw(_("Qty Acquired must be greater than zero."))
        if flt(self.cost_basis_per_unit) < 0:
            frappe.throw(_("Cost Basis per Unit cannot be negative."))
        if flt(self.qty_disposed or 0) > flt(self.qty_acquired):
            frappe.throw(
                _("Qty Disposed ({0}) cannot exceed Qty Acquired ({1}).").format(
                    self.qty_disposed, self.qty_acquired
                )
            )

    def _compute_derived_fields(self):
        self.qty_disposed = flt(self.qty_disposed or 0)
        self.qty_remaining = flt(self.qty_acquired) - self.qty_disposed
        self.total_cost = flt(self.qty_acquired) * flt(self.cost_basis_per_unit)
        self.remaining_cost = self.qty_remaining * flt(self.cost_basis_per_unit)

    def _update_status(self):
        if flt(self.qty_remaining) <= 0:
            self.status = "Fully Disposed"
        elif flt(self.qty_disposed) > 0:
            self.status = "Partially Disposed"
        else:
            self.status = "Open"
