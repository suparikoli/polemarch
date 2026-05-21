"""Trade Order — source-of-truth for an unlisted-share trade lifecycle.

State machine (only the `transition_to` method may move between states):

    Draft → Submitted              (wallet/lot reservation placed)
    Submitted → Matched            (FIFO match for sells; counterparty for buys)
    Submitted → Rejected           (validation failure; reservation released)
    Submitted → Cancelled          (user-cancel pre-match; reservation released)
    Matched → Settling             (Settlement Instruction created)
    Matched → Cancelled            (admin-only; SLLE reversal)
    Settling → Settled             (Settlement Cleared; downstream docs done)
    Settled → Closed               (post-T+N freeze)
    Settled → Cancelled            (admin-only; full reversal)

Direct edits of `order_state` outside the transition method are blocked by
`on_update_after_submit`. Submit moves Draft → Submitted; cancel moves to
Cancelled (releasing reservations).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


_ALLOWED_TRANSITIONS = {
    "Draft":     {"Submitted"},
    "Submitted": {"Matched", "Rejected", "Cancelled"},
    "Matched":   {"Settling", "Cancelled"},
    "Settling":  {"Settled", "Failed"},  # Failed is a settlement substate; mirrored here
    "Settled":   {"Closed", "Cancelled"},
    # Terminal states:
    "Closed":    set(),
    "Cancelled": set(),
    "Rejected":  set(),
}


class TradeOrder(Document):
    def validate(self):
        self._validate_book_customer()
        self._validate_qty_price()
        self._validate_portfolio_alignment()
        self._compute_derived_amounts()

    def before_submit(self):
        # `submit()` is the Draft → Submitted transition. The matching engine
        # places the reservation inside the wallet/SLLE engine; we record the
        # state transition here.
        if self.order_state == "Draft":
            self.order_state = "Submitted"
            self.reserved_at = self.reserved_at or now_datetime()

    def on_submit(self):
        # Hook for the matching/reservation flow lives in the matching module;
        # the doc itself is just a record-keeper after this point.
        from polemarch.polemarch_trading import matching as matching_engine

        matching_engine.place_reservation(self)

    def on_update_after_submit(self):
        # Only the state machine + explicit allow-on-submit fields may change.
        # Any field mutation that survives Frappe's docstatus guard ends up here.
        if not getattr(self.flags, "from_state_transition", False):
            old = self.get_doc_before_save()
            if old and old.order_state != self.order_state:
                frappe.throw(
                    _(
                        "Trade Order state must be changed via transition_to(...) — "
                        "direct edits are not allowed (got {0} → {1})."
                    ).format(old.order_state, self.order_state),
                    title=_("Illegal State Transition"),
                )

    def on_cancel(self):
        # Cancel from any state — engine performs the rollback for reservation
        # / SLLE / downstream docs. Mark terminal Cancelled and stamp release time.
        from polemarch.polemarch_trading import matching as matching_engine

        matching_engine.cancel_order(self)
        self.flags.from_state_transition = True
        self.order_state = "Cancelled"
        self.released_at = self.released_at or now_datetime()

    # ── State-machine API ────────────────────────────────────────────────

    def transition_to(self, new_state: str, reason: str | None = None):
        """Programmatic state transitions, with allow-list enforcement.

        Persists immediately via a single UPDATE; sets `flags.from_state_transition`
        so `on_update_after_submit` permits the change.
        """
        if self.order_state == new_state:
            return
        allowed = _ALLOWED_TRANSITIONS.get(self.order_state, set())
        if new_state not in allowed:
            frappe.throw(
                _("Trade Order {0}: transition {1} → {2} is not allowed.").format(
                    self.name, self.order_state, new_state
                ),
                title=_("Illegal State Transition"),
            )

        self.flags.from_state_transition = True
        self.order_state = new_state
        if new_state == "Cancelled" and reason:
            self.cancellation_reason = reason
        if new_state == "Rejected" and reason:
            self.rejection_reason = reason
        # Use `db_update` to bypass save-time validations that would re-run for
        # a state-only mutation; `flags.from_state_transition` already gates the
        # append-only check.
        self.db_update()
        frappe.db.commit()

    # ── Validations ──────────────────────────────────────────────────────

    def _validate_book_customer(self):
        if self.book == "Customer" and not self.customer:
            frappe.throw(
                _("Customer is required for Customer-book orders."),
                title=_("Customer Required"),
            )
        if self.book == "Proprietary" and self.customer:
            frappe.throw(
                _("Proprietary orders must not have a Customer attached."),
                title=_("Customer Not Allowed"),
            )

    def _validate_qty_price(self):
        if flt(self.qty) <= 0:
            frappe.throw(_("Trade Order qty must be > 0."), title=_("Invalid Quantity"))
        if flt(self.price) <= 0:
            frappe.throw(_("Trade Order price must be > 0."), title=_("Invalid Price"))

    def _validate_portfolio_alignment(self):
        if not self.portfolio:
            return
        portfolio = frappe.db.get_value(
            "Portfolio", self.portfolio, ("owner_kind", "customer", "company"), as_dict=True
        )
        if not portfolio:
            return
        if self.book == "Customer" and portfolio.owner_kind != "Customer":
            frappe.throw(
                _("Portfolio {0} is Proprietary; cannot route a Customer-book order to it.").format(
                    self.portfolio
                ),
                title=_("Portfolio Owner Conflict"),
            )
        if self.book == "Customer" and portfolio.customer != self.customer:
            frappe.throw(
                _(
                    "Portfolio {0} belongs to customer {1}; order is for {2}."
                ).format(self.portfolio, portfolio.customer, self.customer),
                title=_("Portfolio Customer Mismatch"),
            )
        if self.book == "Proprietary" and portfolio.owner_kind != "Proprietary":
            frappe.throw(
                _("Portfolio {0} is Customer-owned; cannot route a Proprietary order to it.").format(
                    self.portfolio
                ),
                title=_("Portfolio Owner Conflict"),
            )

    def _compute_derived_amounts(self):
        self.gross_amount = flt(self.qty) * flt(self.price)
        fees = flt(self.platform_fee) + flt(self.low_order_fee) + flt(self.stamp_duty)
        if self.side == "Buy":
            self.net_amount = flt(self.gross_amount) + fees
        else:
            self.net_amount = flt(self.gross_amount) - fees
