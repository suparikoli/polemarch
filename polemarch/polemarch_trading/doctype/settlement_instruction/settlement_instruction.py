"""Settlement Instruction — per-Trade-Order T+N obligation tracker.

State machine:

    Pending → Funded             (wallet debit posted; for Buy this is the
                                  hold-to-final-debit conversion)
    Funded → Cleared             (DP transfer + payout cleared; Trade Order
                                  advances to Settled)
    Funded → Failed              (DP rejected or payout failed; reverse
                                  postings, refund wallet)
    Pending → Cancelled          (admin-only; trade cancelled pre-settlement)
    Failed → Pending             (admin retry only)
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


_ALLOWED_TRANSITIONS = {
    "Pending":   {"Funded", "Cancelled"},
    "Funded":    {"Cleared", "Failed"},
    "Cleared":   set(),
    "Failed":    {"Pending"},
    "Cancelled": set(),
}


class SettlementInstruction(Document):
    def validate(self):
        self._validate_amounts()
        self._validate_dates()

    def before_submit(self):
        # Submit is the Pending-state confirmation. Transitions Pending → Funded
        # are explicit (via `transition_to`); submit doesn't change state.
        if not self.settlement_state:
            self.settlement_state = "Pending"

    def on_update_after_submit(self):
        if not getattr(self.flags, "from_state_transition", False):
            old = self.get_doc_before_save()
            if old and old.settlement_state != self.settlement_state:
                frappe.throw(
                    _(
                        "Settlement Instruction state must be changed via transition_to(...) — "
                        "direct edits not allowed (got {0} → {1})."
                    ).format(old.settlement_state, self.settlement_state),
                    title=_("Illegal Settlement Transition"),
                )

    def on_cancel(self):
        # Pre-funded cancellation: transition to Cancelled, refund any reservations.
        if self.settlement_state == "Cancelled":
            return
        if self.settlement_state not in ("Pending", "Funded"):
            frappe.throw(
                _("Cannot cancel Settlement Instruction in state {0}.").format(self.settlement_state),
                title=_("Cancel Not Allowed"),
            )
        # Reset Trade Order settlement_status, but matching/refund work is
        # delegated to the runner so cancellation stays idempotent.
        from polemarch.polemarch_trading import settlement as settlement_engine

        settlement_engine.on_instruction_cancelled(self)
        self.flags.from_state_transition = True
        self.settlement_state = "Cancelled"

    # ── State-machine API ────────────────────────────────────────────────

    def transition_to(self, new_state: str, **kwargs):
        if self.settlement_state == new_state:
            return
        allowed = _ALLOWED_TRANSITIONS.get(self.settlement_state, set())
        if new_state not in allowed:
            frappe.throw(
                _("Settlement Instruction {0}: transition {1} → {2} not allowed.").format(
                    self.name, self.settlement_state, new_state
                ),
                title=_("Illegal Settlement Transition"),
            )

        self.flags.from_state_transition = True
        self.settlement_state = new_state
        ts = now_datetime()
        if new_state == "Funded":
            self.funded_on = ts
        elif new_state == "Cleared":
            self.cleared_on = ts
        elif new_state == "Failed":
            self.failed_on = ts
            self.failure_reason = kwargs.get("reason") or self.failure_reason

        if kwargs.get("seller_bank_payout_ref"):
            self.seller_bank_payout_ref = kwargs["seller_bank_payout_ref"]
        if kwargs.get("dp_transfer_ref"):
            self.dp_transfer_ref = kwargs["dp_transfer_ref"]
            self.dp_transferred_on = ts

        self.db_update()
        frappe.db.commit()

        # Mirror state onto the Trade Order's settlement_status column so the
        # UI / API can read it without joining.
        if self.trade_order:
            mirror = {
                "Pending": "Pending",
                "Funded": "Funded",
                "Cleared": "Cleared",
                "Failed": "Failed",
                "Cancelled": "Pending",
            }
            frappe.db.set_value(
                "Trade Order", self.trade_order,
                "settlement_status", mirror[new_state],
                update_modified=False,
            )

    # ── Validations ──────────────────────────────────────────────────────

    def _validate_amounts(self):
        if flt(self.qty) <= 0:
            frappe.throw(_("Settlement qty must be > 0."), title=_("Invalid Quantity"))
        if flt(self.net_amount) <= 0:
            frappe.throw(_("Settlement net_amount must be > 0."), title=_("Invalid Amount"))

    def _validate_dates(self):
        if not self.expected_settlement_date:
            frappe.throw(_("Expected settlement date is required."), title=_("Date Required"))
