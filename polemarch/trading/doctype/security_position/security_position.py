import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SecurityPosition(Document):
    """Materialised view of (portfolio, security) holdings.

    Updated atomically by `polemarch.trading.position.apply_delta(...)` which
    holds a row-level `FOR UPDATE` lock. A nightly recomputation job verifies
    the row matches the underlying Security Lot rollup.
    """

    def validate(self):
        # Available = held − reserved; enforce structural invariant.
        held = flt(self.qty_held)
        reserved = flt(self.qty_reserved)
        if reserved < 0 or held < 0:
            frappe.throw(
                _("Security Position quantities cannot be negative."),
                title=_("Negative Position"),
            )
        if reserved > held:
            frappe.throw(
                _("Security Position {0}: reserved ({1}) cannot exceed held ({2}).").format(
                    self.name, reserved, held
                ),
                title=_("Reservation Exceeds Holdings"),
            )
        self.qty_available = held - reserved

        if held > 0:
            self.avg_cost = flt(self.total_cost) / held
            self.market_value = held * flt(self.last_marked_price)
            self.unrealized_pnl = flt(self.market_value) - flt(self.total_cost)
        else:
            self.avg_cost = 0
            self.market_value = 0
            self.unrealized_pnl = 0

    def on_trash(self):
        if flt(self.qty_held) > 0:
            frappe.throw(
                _("Cannot delete Security Position {0}: qty_held = {1} > 0.").format(
                    self.name, self.qty_held
                ),
                title=_("Position Not Empty"),
            )
