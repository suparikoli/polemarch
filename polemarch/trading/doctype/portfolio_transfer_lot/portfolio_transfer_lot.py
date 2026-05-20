from frappe.model.document import Document


class PortfolioTransferLot(Document):
    """Child of Portfolio Transfer. All math is filled in by the parent's
    `populate_lots_from_fifo` step; no controller logic here."""

    pass
