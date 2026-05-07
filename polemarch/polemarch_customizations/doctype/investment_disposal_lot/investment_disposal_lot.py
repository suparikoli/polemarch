"""Investment Disposal Lot — child table on `Investment Disposal`.
Carries one row per FIFO-consumed `Investment Holding`. Math is filled
in by the parent Disposal's validate; nothing custom here."""

from frappe.model.document import Document


class InvestmentDisposalLot(Document):
    pass
