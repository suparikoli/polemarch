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
        self._validate_classification_rows()
        self._compute_derived_fields()
        self._update_status()

    def _validate_classification_rows(self):
        """Phase 24B-1: enforce the child-table classification invariants.

        - Σ qty across all rows ≤ qty_remaining (can't classify more than
          we hold).
        - Each row qty > 0 (negative / zero rows are noise).
        - Append-only: existing rows can't be edited or deleted. Compare
          against get_doc_before_save() to detect tampering. New rows
          must come after the last existing row's idx.
        - Window check: new rows can only be added when now <=
          classification_deadline (or there's no deadline yet, e.g.
          first save).

        Skips silently when the Custom Field isn't installed yet (early
        deploy / v0_24_0 patch hasn't run).
        """
        if not hasattr(self, "classifications"):
            return
        rows = self.classifications or []
        if not rows:
            return

        # Per-row sanity
        for r in rows:
            if flt(r.qty) <= 0:
                frappe.throw(
                    _("Classification row {0}: qty must be > 0.").format(r.idx),
                    title=_("Invalid Classification Qty"),
                )
            if r.classification not in ("Stock in Trade", "Investment"):
                frappe.throw(
                    _("Classification row {0}: classification must be Stock in Trade or Investment.").format(r.idx),
                    title=_("Invalid Classification"),
                )

        # Σ qty (NET of per-class disposals) ≤ qty_remaining.
        # Phase 24B-2: Portfolio Transfer bumps qty_disposed_<class> when
        # shares move out, so the appropriate check is NET, not gross.
        # Example: 100 SiT row + 60 Investment row + qty_disposed_sit=60
        # → net = (100-60) + 60 = 100 ≤ qty_remaining (still 100). OK.
        sit_total = sum(flt(r.qty) for r in rows if r.classification == "Stock in Trade")
        inv_total = sum(flt(r.qty) for r in rows if r.classification == "Investment")
        sit_net = sit_total - flt(getattr(self, "qty_disposed_sit", 0) or 0)
        inv_net = inv_total - flt(getattr(self, "qty_disposed_investment", 0) or 0)
        net_total = max(0, sit_net) + max(0, inv_net)
        cap = flt(self.qty_remaining or self.qty_acquired)
        if net_total > cap + 0.0001:
            frappe.throw(
                _(
                    "Net classified qty ({0}) exceeds available {1}. "
                    "Only Unclassified shares can be classified."
                ).format(net_total, cap),
                title=_("Classification Exceeds Available"),
            )

        # Append-only enforcement: compare against the persisted version.
        # System-driven flows (FIFO disposal, smoke tests) can opt out via
        # flags.allow_classification_edit if needed in the future.
        if not self.is_new() and not getattr(self.flags, "allow_classification_edit", False):
            prev = self.get_doc_before_save()
            if prev is not None:
                prev_by_name = {r.name: r for r in (prev.classifications or [])}
                for r in rows:
                    if r.name in prev_by_name:
                        old = prev_by_name[r.name]
                        if (
                            old.classification != r.classification
                            or flt(old.qty) != flt(r.qty)
                            or old.classified_on != r.classified_on
                            or old.classified_by != r.classified_by
                        ):
                            frappe.throw(
                                _(
                                    "Classification row {0} is append-only. To reclassify, "
                                    "create a Portfolio Transfer with appropriate from/to."
                                ).format(r.idx),
                                title=_("Append-Only Violated"),
                            )
                # Deleted rows from previous save → also forbidden
                kept_names = {r.name for r in rows}
                for old_name in prev_by_name:
                    if old_name not in kept_names:
                        frappe.throw(
                            _("Cannot delete existing classification rows. They are append-only."),
                            title=_("Append-Only Violated"),
                        )

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

        # Phase 24B-1: derive child-table classification rollup. Only sets
        # attrs if the Custom Fields exist (v0_24_0 patch installed).
        if hasattr(self, "qty_classified_sit"):
            self._compute_classification_rollup()

    def _compute_classification_rollup(self):
        """Aggregate the classifications child table into the rollup fields.

        Phase 24B-2: each per-class total is NET of per-class disposals.
        qty_disposed_sit / qty_disposed_investment are bumped by
        Portfolio Transfer when shares move between classifications
        (and will be bumped by FIFO sales once Phase 24C lands), so
        the displayed qty_classified_* reflects what's still HELD in
        that class — not the cumulative ever-classified total.

        Keeps the legacy `classification` Select in sync as a
        back-compat shim so existing readers (FIFO, list views,
        holdings_summary report) keep working until Phase 24C cuts
        them over to the child-table directly.
        """
        sit_total = 0.0
        inv_total = 0.0
        for row in (self.classifications or []):
            if row.classification == "Stock in Trade":
                sit_total += flt(row.qty)
            elif row.classification == "Investment":
                inv_total += flt(row.qty)

        # Subtract per-class disposals (set by Portfolio Transfer when
        # shares get reclassified out of this class, and by FIFO sales
        # in Phase 24C).
        sit_net = max(0, sit_total - flt(getattr(self, "qty_disposed_sit", 0) or 0))
        inv_net = max(0, inv_total - flt(getattr(self, "qty_disposed_investment", 0) or 0))

        self.qty_classified_sit = sit_net
        self.qty_classified_investment = inv_net
        self.qty_unclassified = max(0, flt(self.qty_remaining) - sit_net - inv_net)

        # Back-compat: derive the legacy single `classification` field.
        if sit_net > 0 and inv_net == 0:
            self.classification = "Stock in Trade"
        elif inv_net > 0 and sit_net == 0:
            self.classification = "Investment"
        elif sit_net > 0 and inv_net > 0:
            # Mixed — keep whatever was set originally (if any) so we don't
            # silently flip a previously-classified record.
            self.classification = self.classification or "Stock in Trade"
        elif self.qty_unclassified > 0:
            self.classification = "Unallocated"

    def _update_status(self):
        if flt(self.qty_remaining) <= 0:
            self.status = "Fully Disposed"
        elif flt(self.qty_disposed) > 0:
            self.status = "Partially Disposed"
        else:
            self.status = "Open"


# ── Split & Classify shortcut ────────────────────────────────────────────


@frappe.whitelist()
def classify_qty(holding: str, qty: float, classification: str, notes: str = "") -> dict:
    """Phase 24B-1: add a classification child row to the holding.

    Allows partial classification of an Unallocated portion. Append-only
    via the parent's validate guard. Returns the new row's totals so the
    UI can refresh without a full reload.

    Args:
        holding: Investment Holding name.
        qty: how many shares to classify (must be > 0 and ≤ qty_unclassified).
        classification: "Stock in Trade" or "Investment".
        notes: optional free-text note saved on the child row.
    """
    qty = flt(qty)
    if qty <= 0:
        frappe.throw(_("Classify qty must be greater than zero."))
    if classification not in ("Stock in Trade", "Investment"):
        frappe.throw(
            _("Classification must be Stock in Trade or Investment (got {0}).").format(classification)
        )

    h = frappe.get_doc("Investment Holding", holding)
    if not hasattr(h, "classifications"):
        frappe.throw(
            _("This site hasn't installed the Phase 24B classification table yet. Run bench migrate."),
            title=_("Schema Out of Date"),
        )

    # Check deadline (operator can only classify within the 5-business-day
    # window from acquisition; after that, Portfolio Transfer + JEs).
    from frappe.utils import now_datetime, get_datetime
    deadline = getattr(h, "classification_deadline", None)
    if deadline and get_datetime(deadline) < now_datetime():
        frappe.throw(
            _(
                "Classification deadline ({0}) has passed for this holding. "
                "Use Portfolio Transfer to move shares between classifications."
            ).format(deadline),
            title=_("Classification Window Closed"),
        )

    h.append("classifications", {
        "classification": classification,
        "qty": qty,
        "classified_on": frappe.utils.now_datetime(),
        "classified_by": frappe.session.user,
        "auto_classified": 0,
        "notes": notes,
    })
    h.flags.ignore_permissions = True
    h.save(ignore_permissions=True)

    return {
        "holding": holding,
        "added_qty": qty,
        "classification": classification,
        "qty_classified_sit": h.qty_classified_sit,
        "qty_classified_investment": h.qty_classified_investment,
        "qty_unclassified": h.qty_unclassified,
    }


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
