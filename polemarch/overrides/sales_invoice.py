import frappe
from frappe import _

from polemarch.install import MITHTECH_SERVICES_BRAND, POLEMARCH_BRAND


def validate(doc, method=None):
    """Polemarch-side Sales Invoice rules — purely classification +
    cost-center concerns. The 0% GST treatment for share-transfer
    invoices is handled at the **Item** level via the
    `Polemarch - Non-GST` Item Tax Template (auto-linked to every
    `brand=Polemarch` item by `polemarch.overrides.item.validate`).
    India Compliance reads each line's effective ITT and stamps
    `gst_treatment = "Non-GST"` on the row, which zeroes computed tax
    automatically — no invoice-level template juggling needed.

    Earlier versions of this hook tried to swap the Sales Taxes and
    Charges Template per-brand (Polemarch / Mithtech Services); that
    fought IC's intra/inter-state suppression and produced 36% tax on
    intra-state Mithtech invoices. The Item-Tax-Template approach is
    IC's intended mechanism for non-GST goods/services.
    """
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Invoice cannot mix {0} and {1} branded items. Split into two invoices.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    doc.custom_is_polemarch_invoice = 1 if is_polemarch else 0
    _apply_cost_center(doc, is_polemarch)


def on_submit(doc, method=None):
    _create_investment_disposals(doc)
    if doc.get("custom_medusa_order_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_orders.mirror_status",
            queue="short",
            invoice_name=doc.name,
            new_status="paid",
            enqueue_after_commit=True,
        )


def on_cancel(doc, method=None):
    _cancel_investment_disposals(doc)
    if doc.get("custom_medusa_order_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_orders.mirror_status",
            queue="short",
            invoice_name=doc.name,
            new_status="canceled",
            enqueue_after_commit=True,
        )


def _create_investment_disposals(doc):
    """For each brand=Polemarch line on a submitted Sales Invoice,
    FIFO-match against open `Investment Holding` rows and emit one
    `Investment Disposal` per line item.

    One Disposal per line (not per SI) makes per-item capital-gains
    reporting and amendment cleaner: cancelling one share's disposal
    doesn't touch the others.

    Skip silently when no matching holdings exist (e.g. the seller is
    in facilitator-only mode for that ISIN). The accountant can post
    a manual disposal later if needed.
    """
    from polemarch.polemarch_customizations.doctype.investment_disposal.investment_disposal import (
        fifo_consume,
    )

    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand != POLEMARCH_BRAND:
            continue
        consumed, fully_covered = fifo_consume(
            row.item_code, doc.company, row.qty
        )
        if not consumed:
            # No open inventory — facilitator-only sale (or first time
            # this item is being sold and an admin forgot to enter the
            # acquisition lot). Log a comment on the SI so it surfaces
            # in the audit trail; don't block the submit.
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "Sales Invoice",
                "reference_name": doc.name,
                "content": (
                    f"No Investment Holding rows found for "
                    f"<b>{row.item_code}</b> ({row.qty} units sold). "
                    f"Capital gains were NOT booked automatically. "
                    f"Either this is a facilitator-only trade or an "
                    f"acquisition lot is missing."
                ),
            }).insert(ignore_permissions=True)
            continue

        disposal = frappe.new_doc("Investment Disposal")
        disposal.disposal_date = doc.posting_date
        disposal.item = row.item_code
        disposal.company = doc.company
        disposal.sales_invoice = doc.name
        disposal.customer = doc.customer
        for c in consumed:
            disposal.append("lots", {
                "holding": c["holding"],
                "qty_consumed": c["qty"],
                "sale_price_per_unit": row.rate,
            })
        if not fully_covered:
            disposal.notes = (
                "Partial inventory cover — open holdings did not fully "
                "satisfy the sale qty. Remaining units treated as "
                "facilitator-only and excluded from cost-basis math."
            )
        disposal.flags.ignore_permissions = True
        disposal.insert(ignore_permissions=True)
        disposal.submit()


def _cancel_investment_disposals(doc):
    """When a Sales Invoice is cancelled, cancel its child Investment
    Disposals so the underlying holdings get qty_disposed restored.
    Disposal's own on_cancel handles the holding state mutation."""
    disposal_names = frappe.get_all(
        "Investment Disposal",
        filters={"sales_invoice": doc.name, "docstatus": 1},
        pluck="name",
    )
    for name in disposal_names:
        disposal = frappe.get_doc("Investment Disposal", name)
        disposal.flags.ignore_permissions = True
        disposal.cancel()


def _line_brands(doc) -> set:
    brands = set()
    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand:
            brands.add(brand)
    return brands


def _apply_cost_center(doc, is_polemarch: bool):
    target_brand = POLEMARCH_BRAND if is_polemarch else MITHTECH_SERVICES_BRAND
    cc = frappe.db.get_value(
        "Cost Center",
        {"cost_center_name": target_brand, "company": doc.company, "is_group": 0},
        "name",
    )
    if not cc:
        return
    for row in doc.get("items") or []:
        if not row.cost_center:
            row.cost_center = cc
