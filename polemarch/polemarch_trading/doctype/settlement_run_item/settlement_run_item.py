from frappe.model.document import Document


class SettlementRunItem(Document):
    """Per-instruction line of a Settlement Run batch. Records each item's
    pre-run state and the outcome (Funded / Cleared / Failed / Skipped)
    when the batch processes it."""

    pass
