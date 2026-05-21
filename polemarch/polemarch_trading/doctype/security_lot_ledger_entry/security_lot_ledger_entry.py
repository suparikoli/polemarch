import frappe
from frappe import _
from frappe.model.document import Document


# Permissions intentionally omit `create` for all roles; rows are inserted
# programmatically only via polemarch.polemarch_trading.fifo / Trade Order / SLLE
# producers, using ignore_permissions=True.
class SecurityLotLedgerEntry(Document):
    """Append-only ledger row mirroring the Stock-Ledger-Entry pattern.

    Every acquisition / reservation / consumption / reversal of a Security
    Lot quantity emits exactly one of these rows. Security Lot's derived
    quantities (`qty_disposed`, `qty_remaining`, `qty_reserved`) are
    recomputed from this table on every `Security Lot.validate()`.

    Edits after submit are forbidden except the reversal-marker fields,
    which are mutated only by the reversal flow.
    """

    def validate(self):
        if (self.qty or 0) <= 0:
            frappe.throw(
                _("SLLE qty must be > 0 (got {0}).").format(self.qty),
                title=_("Invalid Quantity"),
            )

        if self.entry_type == "Consume" and (self.cost_basis_per_unit or 0) <= 0:
            frappe.throw(
                _("SLLE Consume entries require a positive cost_basis_per_unit."),
                title=_("Cost Basis Required"),
            )

        if self.entry_type == "Reversal" and not self.reverses:
            frappe.throw(
                _("SLLE Reversal entries must reference the row they reverse."),
                title=_("Reversal Target Missing"),
            )

    def on_update_after_submit(self):
        if not getattr(self.flags, "from_reversal", False):
            frappe.throw(
                _(
                    "Security Lot Ledger Entry {0} is append-only. Post a Reversal "
                    "row instead of editing this one."
                ).format(self.name),
                title=_("Append-Only Ledger Violated"),
            )

    def on_cancel(self):
        frappe.throw(
            _(
                "Security Lot Ledger Entry {0} cannot be cancelled. Create a Reversal "
                "row with reverses=<this row> instead."
            ).format(self.name),
            title=_("Cancel Not Allowed"),
        )
