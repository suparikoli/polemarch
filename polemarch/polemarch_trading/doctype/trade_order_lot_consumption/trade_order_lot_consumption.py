from frappe.model.document import Document


class TradeOrderLotConsumption(Document):
    """Child table for Trade Order. Mirrors Investment Disposal Lot's audit
    columns but links to Security Lot. All computed values are written by
    the matching engine; no controller logic needed here."""

    pass
