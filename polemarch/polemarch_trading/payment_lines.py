"""How a Security Sale / Purchase was paid: `payment_type` and `payments`.

Descriptive only: `payment_method` / `payment_account` stay the accounting
rail and post the GL. Rows are filled on submit from what the document did;
the books coordinator can add or correct rows after submit (for example a
bank Journal Entry and UTR). Rows are written directly because the document
is already submitted when on_submit runs.
"""

import frappe
from frappe.utils import flt

WALLET, DIRECT = "Wallet", "Direct Transfer"


def header_for(rows, amount) -> str:
    types = {r.get("payment_type") for r in rows}
    if WALLET in types and DIRECT in types:
        return "Wallet + Direct Transfer"
    if types:
        return next(iter(types))
    return "No Payment" if flt(amount) <= 0 else ""


def add_row(doc, **values):
    row = doc.append("payments", values)
    row.db_insert()
    return row


def record_on_submit(doc, wallet_txn, journal_entry):
    """Fill the rows for this submit. Wallet rows are rebuilt, because a Sale
    cancelled and resubmitted (R13) has a new settlement; rows the books
    coordinator added by hand stay."""
    if doc.payment_method == "Customer Wallet":
        frappe.db.delete("Security Payment", {"parent": doc.name, "parenttype": doc.doctype, "payment_type": WALLET})
        doc.set("payments", [r for r in doc.get("payments") if r.payment_type != WALLET])
        if wallet_txn:
            add_row(doc, payment_type=WALLET, payment_date=doc.posting_date, amount=doc.amount,
                    paid_through="Customer wallet", wallet_transaction=wallet_txn)
    elif doc.payment_method in ("Bank", "Cash") and flt(doc.amount) > 0 and not doc.get("payments"):
        add_row(doc, payment_type=DIRECT, payment_date=doc.posting_date, amount=doc.amount,
                paid_through=doc.payment_account, journal_entry=journal_entry)
    doc.db_set("payment_type", header_for(doc.get("payments"), doc.amount), update_modified=False)


def summarise(doctype_rows) -> dict:
    """{parent: "₹1,566.00 Wallet POL-WT-… · ₹2,270.00 Direct Transfer UTR …"}"""
    out = {}
    for r in doctype_rows:
        ref = r.wallet_transaction or r.reference or r.journal_entry or ""
        text = f"₹{flt(r.amount):,.2f} {r.paid_through or r.payment_type}" + (f" {ref}" if ref else "")
        out.setdefault(r.parent, []).append(text)
    return {k: " · ".join(v) for k, v in out.items()}
