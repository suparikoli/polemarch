"""Investment Holding Classification — child table on Investment Holding.

One row per classification action. Rows are append-only: once inserted
they cannot be edited or deleted (enforced by parent IH validate).

Lifecycle:
  - Operator clicks "Classify Qty" on the Investment Holding form, picks
    qty + target classification (SiT or Investment), submits. A new row
    lands here with classified_on=now, classified_by=current_user,
    auto_classified=False.
  - At day-5 from acquisition, if the parent Holding still has
    qty_unclassified > 0, the scheduler auto-adds one row with
    classification=Stock in Trade, qty=qty_unclassified,
    classified_by=Administrator, auto_classified=True.
  - After day-5 the parent Holding is locked: no more rows can be added.
    SiT ↔ Investment moves now require Portfolio Transfer with JEs.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class InvestmentHoldingClassification(Document):
    def before_insert(self):
        if not self.classified_on:
            self.classified_on = now_datetime()
        if not self.classified_by:
            self.classified_by = frappe.session.user
