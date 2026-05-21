"""Portfolio Transfer — formal Trading ↔ Investment reclassification.

State machine (hand-rolled; not Frappe Workflow):

    Draft            → Pending Approval (on submit; populates lots via FIFO)
    Pending Approval → Approved         (Compliance Officer; cannot self-approve)
    Pending Approval → Rejected         (with reason)
    Rejected         → Draft            (resubmit allowed)
    Approved         → Posted           (consumes from-side Investment
                                         Holdings via FIFO, mints new
                                         to-side Holdings at FMV basis,
                                         posts the inventory JE)
    Posted           → terminal         (reversal only via NEW Portfolio
                                         Transfer with reversal_of=<original>)

`transition_to(...)` is the only sanctioned path that mutates `transfer_state`
post-submit. `on_update_after_submit` blocks anything else.

Post a posted transfer's reversal: create a new Portfolio Transfer with
`reversal_of=<original>`, swapped from/to portfolios, same qty, FMV as of
the reversal date. Approval cycle runs again; on Posted the original gets
its `reversed_by` cross-reference filled.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime


_ALLOWED_TRANSITIONS = {
    "Draft":            {"Pending Approval"},
    "Pending Approval": {"Approved", "Rejected"},
    "Rejected":         {"Draft"},
    "Approved":         {"Posted", "Rejected"},
    "Posted":           set(),
    "Reversed":         set(),
}


_LONG_TERM_THRESHOLD_DAYS = 730


class PortfolioTransfer(Document):
    def validate(self):
        self._validate_portfolios_share_company()
        self._validate_directional_change()
        self._validate_qty_fmv()
        self._compute_totals()
        if self.is_new() and not self.requested_by:
            self.requested_by = frappe.session.user

    def before_save(self):
        # In Draft, refresh the FIFO consumption plan on every save so the
        # approver sees the current lot allocation.
        if self.transfer_state in ("Draft", None, ""):
            self.populate_lots_from_fifo()

    def before_submit(self):
        if self.transfer_state in (None, "", "Draft"):
            self.transfer_state = "Pending Approval"

    def on_update_after_submit(self):
        if getattr(self.flags, "from_state_transition", False):
            return
        old = self.get_doc_before_save()
        if old and old.transfer_state != self.transfer_state:
            frappe.throw(
                _(
                    "Portfolio Transfer state must be changed via transition_to(...). "
                    "Got {0} → {1}."
                ).format(old.transfer_state, self.transfer_state),
                title=_("Illegal State Transition"),
            )

    def on_cancel(self):
        if self.transfer_state in ("Approved", "Posted", "Reversed"):
            frappe.throw(
                _(
                    "Cannot cancel a Portfolio Transfer in state {0}. To undo a Posted "
                    "transfer, create a new Portfolio Transfer with reversal_of=<this>."
                ).format(self.transfer_state),
                title=_("Cancel Not Allowed"),
            )

    # ── State-machine API ────────────────────────────────────────────────

    def transition_to(self, new_state: str, **kwargs):
        if self.transfer_state == new_state:
            return
        allowed = _ALLOWED_TRANSITIONS.get(self.transfer_state, set())
        if new_state not in allowed:
            frappe.throw(
                _("Portfolio Transfer {0}: transition {1} → {2} not allowed.").format(
                    self.name, self.transfer_state, new_state
                ),
                title=_("Illegal State Transition"),
            )

        actor = kwargs.get("actor") or frappe.session.user
        ts = now_datetime()

        if new_state == "Approved":
            if actor == self.requested_by:
                frappe.throw(
                    _(
                        "Portfolio Transfer {0}: cannot self-approve. Requested by {1}, "
                        "approval attempted by {2}."
                    ).format(self.name, self.requested_by, actor),
                    title=_("Self-Approval Forbidden"),
                )
            if "Polemarch Compliance Officer" not in frappe.get_roles(actor):
                frappe.throw(
                    _("Only Polemarch Compliance Officer may approve Portfolio Transfers."),
                    title=_("Insufficient Role"),
                )
            self.approved_by = actor
            self.approved_on = ts

        elif new_state == "Rejected":
            self.rejected_by = actor
            self.rejection_reason = kwargs.get("reason") or self.rejection_reason
            if not self.rejection_reason:
                frappe.throw(
                    _("A reason is required to reject a Portfolio Transfer."),
                    title=_("Reason Required"),
                )

        elif new_state == "Posted":
            self.posted_on = ts

        self.flags.from_state_transition = True
        self.transfer_state = new_state
        self.db_update()
        frappe.db.commit()

    # ── FIFO planning ────────────────────────────────────────────────────

    def populate_lots_from_fifo(self):
        from polemarch.polemarch_trading import fifo as fifo_engine

        if not self.from_classification or not self.security or not self.qty or not self.company:
            return

        plan = fifo_engine.consume(
            security=self.security,
            company=self.company,
            classification=self.from_classification,
            qty_to_sell=flt(self.qty),
            sale_date=getdate(self.transfer_date),
        )

        self.set("lots_consumed", [])
        for p in plan:
            fmv_amt = p.qty * flt(self.fmv_per_unit)
            cost_amt = p.qty * p.cost_basis_per_unit
            self.append(
                "lots_consumed",
                {
                    "source_lot": p.holding,
                    "qty_consumed": p.qty,
                    "original_cost_basis_per_unit": p.cost_basis_per_unit,
                    "transfer_fmv_per_unit": flt(self.fmv_per_unit),
                    "holding_period_days_at_transfer": p.holding_period_days,
                    "is_long_term_at_transfer": int(p.is_long_term),
                    "cost_basis_amount": cost_amt,
                    "fmv_amount": fmv_amt,
                    "deemed_gain": fmv_amt - cost_amt,
                },
            )

    # ── Validations ──────────────────────────────────────────────────────

    def _validate_portfolios_share_company(self):
        # Renamed from old "portfolios share company"; now validates
        # classifications differ + company is set.
        if not self.from_classification or not self.to_classification:
            return
        if self.from_classification == self.to_classification:
            frappe.throw(
                _("Portfolio Transfer requires distinct from/to classifications."),
                title=_("Same Classification"),
            )
        if not self.company:
            frappe.throw(_("Company is required."), title=_("Company Required"))

    def _validate_directional_change(self):
        # With only 2 classifications (Stock in Trade ↔ Investment), the
        # only valid transition is between them. Same-classification is
        # already blocked by `_validate_portfolios_share_company`. Nothing
        # else to check here.
        return

    def _validate_qty_fmv(self):
        if flt(self.qty) <= 0:
            frappe.throw(_("Transfer qty must be > 0."), title=_("Invalid Quantity"))
        if flt(self.fmv_per_unit) <= 0:
            frappe.throw(_("FMV per unit must be > 0."), title=_("Invalid FMV"))
        if self.fmv_source in ("Manual", "Valuation Report") and not (
            self.valuation_reference and self.valuation_reference.strip()
        ):
            frappe.throw(
                _("Manual / Valuation Report FMV requires a valuation_reference."),
                title=_("Valuation Reference Required"),
            )

    def _compute_totals(self):
        self.total_fmv = flt(self.qty) * flt(self.fmv_per_unit)
