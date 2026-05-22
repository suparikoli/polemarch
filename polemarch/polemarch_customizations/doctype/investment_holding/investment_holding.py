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


# ── Split & Classify shortcut ────────────────────────────────────────────


@frappe.whitelist()
def split_and_classify(holding: str, split_qty: float, target_classification: str) -> dict:
    """One-shot Portfolio Transfer driver.

    Used by the "Split & Classify" button on the Investment Holding form:
    splits `split_qty` units off the given holding into a new Holding
    classified as `target_classification`. Drives a Portfolio Transfer
    through Draft → Pending Approval → Approved → Posted in one server
    call so operators don't have to navigate the 4-step state machine
    for routine intra-book splits.

    Args:
        holding: Investment Holding name to split off from.
        split_qty: how many units to move into the new classification.
        target_classification: "Stock in Trade" or "Investment".

    Returns: dict with
        portfolio_transfer  : PT name (created + posted)
        source_holding      : the original (post-decrement)
        new_holding         : the new Holding in target_classification
    """
    split_qty = flt(split_qty)
    source = frappe.get_doc("Investment Holding", holding)

    # ── validation ──────────────────────────────────────────────────────
    if source.classification == target_classification:
        frappe.throw(
            _("Target classification ({0}) is the same as the current one — nothing to split.").format(
                target_classification
            )
        )
    if source.classification == "Unallocated":
        frappe.throw(
            _(
                "Classify the source Holding to Stock in Trade or Investment first; "
                "Split & Classify only moves between those two."
            )
        )
    if target_classification == "Unallocated":
        frappe.throw(
            _("Cannot split into Unallocated. Pick Stock in Trade or Investment.")
        )
    if split_qty <= 0:
        frappe.throw(_("Split quantity must be greater than zero."))

    available = (
        flt(source.qty_acquired)
        - flt(source.qty_disposed or 0)
        - flt(getattr(source, "qty_reserved", 0) or 0)
    )
    if split_qty > available + 0.0001:
        frappe.throw(
            _("Split quantity ({0}) exceeds available {1} on this Holding.").format(
                split_qty, available
            )
        )

    # ── create the Portfolio Transfer ───────────────────────────────────
    # FMV = cost_basis: this is a same-day reclassification, no P&L impact.
    # preset_source_lot tells populate_lots_from_fifo to target THIS holding
    # specifically instead of running FIFO order — the operator clicked
    # Split on a specific row and expects that row to be the one consumed.
    pt = frappe.get_doc({
        "doctype": "Portfolio Transfer",
        "security": source.security,
        "company": source.company,
        "from_classification": source.classification,
        "to_classification": target_classification,
        "qty": split_qty,
        "fmv_per_unit": flt(source.cost_basis_per_unit),
        "transfer_date": frappe.utils.today(),
        "reason": "Split & Classify shortcut: same-cost reclassification",
    })
    pt.flags.ignore_permissions = True
    pt.flags.preset_source_lot = source.name
    pt.insert(ignore_permissions=True)

    # ── drive the state machine to Posted in one shot ───────────────────
    # Submit pushes Draft → Pending Approval (handled in before_submit).
    pt.submit()

    # Pending Approval → Approved.
    # bypass_approval_checks skips the dual-control gate (self-approve +
    # Compliance Officer role): this is a system-driven shortcut, not a
    # human approval, and the same operator both "requests" and "approves".
    pt.reload()
    pt.flags.ignore_permissions = True
    pt.flags.bypass_approval_checks = True
    pt.transition_to("Approved")

    # Approved → Posted: post_transfer() is the engine — it consumes from
    # source Holdings via the FIFO plan, mints the new Holding on the
    # target classification at the original cost basis, posts the
    # reclassification JE (DR target inventory, CR source inventory),
    # and calls transition_to("Posted") at the end.
    from polemarch.polemarch_trading.portfolio_transfer import post_transfer
    post_transfer(pt.name)

    # Resolve the new Holding spawned by the PT (linked back via
    # purchase_reference="Portfolio Transfer" + purchase_reference_link=pt.name).
    new_holding = frappe.db.get_value(
        "Investment Holding",
        {
            "purchase_reference": "Portfolio Transfer",
            "purchase_reference_link": pt.name,
            "classification": target_classification,
        },
        "name",
    )

    return {
        "portfolio_transfer": pt.name,
        "source_holding": source.name,
        "new_holding": new_holding,
    }
