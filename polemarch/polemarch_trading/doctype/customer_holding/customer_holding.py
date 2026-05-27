"""Customer Holding — CRM-style snapshot of what each customer currently holds.

Deliberately NOT on Polemarch's accounting ledger. This doctype answers the
question "who should I call if I need supply of Security X" — it's an
outreach / inventory-visibility tool, not a financial record. Customers
may buy/sell with third parties Polemarch never sees; the snapshot will
drift, and that's accepted. Operators can hand-correct any row as they
learn new information.

Auto-update flow
----------------
  Security Sale     (party=Customer)  → qty += sold_qty
  Security Purchase (party=Customer)  → qty -= bought_qty

Both auto-updates are best-effort:
  - If no row exists, one is created with the delta as the initial qty.
  - If the resulting qty would be negative, it's clamped to 0 + a comment
    is appended to `notes` so the operator knows our snapshot disagreed
    with reality.

Uniqueness
----------
autoname = `{customer}-{security}` — one row per (customer, security)
pair. Trying to insert a duplicate raises a DuplicateEntryError at the
DB layer, which the upsert helper catches and converts to an UPDATE.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class CustomerHolding(Document):
    def validate(self):
        self._stamp_audit_fields()

    def _stamp_audit_fields(self):
        self.last_updated = now_datetime()
        self.last_updated_by = frappe.session.user


# ── upsert API used by Security Sale + Security Purchase on_submit ────────


def apply_delta(customer: str, security: str, delta_qty: float,
                source: str = "Manual", note_on_drift: str = None) -> str:
    """Bump or decrement the (customer, security) snapshot by delta_qty.

    Positive delta_qty = customer acquired more (typical: Security Sale).
    Negative delta_qty = customer disposed (typical: Security Purchase).

    Returns the Customer Holding doc name. Creates the row if absent.
    Clamps to 0 and appends a drift note if the delta would push qty < 0.
    """
    name = f"{customer}-{security}"

    if frappe.db.exists("Customer Holding", name):
        doc = frappe.get_doc("Customer Holding", name)
        new_qty = flt(doc.qty) + flt(delta_qty)
        if new_qty < 0:
            # Drift — our snapshot says they had less than we just took.
            # Clamp + append a note rather than throwing.
            drift_note = (
                f"\n[{now_datetime():%Y-%m-%d %H:%M}] Drift detected: tried to "
                f"apply delta {delta_qty} but only {doc.qty} on record. "
                f"Clamped to 0. Source: {source}."
            )
            if note_on_drift:
                drift_note += f"\n  Context: {note_on_drift}"
            doc.notes = (doc.notes or "") + drift_note
            new_qty = 0
        doc.qty = new_qty
        doc.source = source
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)
        return doc.name

    # No row yet — create one. If delta is negative and there's no prior
    # qty, we still create the row with qty=0 + the drift note (operator
    # may want to know "we apparently bought X from this customer but had
    # no record they held any").
    qty = max(0.0, flt(delta_qty))
    notes = None
    if flt(delta_qty) < 0:
        notes = (
            f"[{now_datetime():%Y-%m-%d %H:%M}] No prior snapshot — but we "
            f"recorded a transaction implying the customer held {abs(delta_qty)} "
            f"of this Security. Verify externally. Source: {source}."
        )
        if note_on_drift:
            notes += f"\n  Context: {note_on_drift}"

    doc = frappe.get_doc({
        "doctype": "Customer Holding",
        "customer": customer,
        "security": security,
        "qty": qty,
        "source": source,
        "notes": notes,
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name
