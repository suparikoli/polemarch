import frappe
from frappe import _

from polemarch.install import (
    MITHTECH_SERVICES_BRAND,
    MITHTECH_SERVICES_TAX_TEMPLATE,
    POLEMARCH_BRAND,
    POLEMARCH_TAX_TEMPLATE,
)


def before_validate(doc, method=None):
    """Swap to the brand-specific tax template before validate runs.

    Why before_validate (not validate): India Compliance's validate
    pass does state-aware GST resolution — for intra-state it
    suppresses IGST, for inter-state it suppresses CGST+SGST. That
    pass needs to see the FINAL tax template, not the auto-filled
    default. Swapping inside `validate` left IC's resolution stale
    and produced ₹180 tax (36%, all three taxes summed) on what
    should have been a ₹90 (18%) intra-state Mithtech Services
    invoice."""
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        # The mixed-brand throw lives in `validate` so the user error
        # surfaces consistently regardless of how the doc is built.
        return
    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    is_mithtech = bool(brands) and brands == {MITHTECH_SERVICES_BRAND}
    _apply_brand_tax_template(doc, is_polemarch, is_mithtech)


def validate(doc, method=None):
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Invoice cannot mix {0} and {1} branded items. Split into two invoices.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    doc.custom_is_polemarch_invoice = 1 if is_polemarch else 0

    if is_polemarch:
        # Securities are excluded from GST under Schedule III of the
        # CGST Act. Override the gst_treatment that IC's
        # `set_default_treatment` may have written ("Nil-Rated" or
        # "Taxable" depending on whether `has_gst_taxes` saw any GST
        # rows on the swapped-in `Polemarch - No GST` template).
        for row in doc.get("items") or []:
            row.gst_treatment = "Non-GST"

    _apply_cost_center(doc, is_polemarch)


def _apply_brand_tax_template(doc, is_polemarch: bool, is_mithtech: bool):
    """Force the brand-specific tax template, overriding whatever
    ERPNext / India Compliance auto-filled.

    Why unconditional: the previous version only set the template
    `if not doc.taxes_and_charges`, which never fired because
    India Compliance's earlier validate pass had already filled it
    with the company's default GST template — so Polemarch
    share-transfer invoices ended up with 18% GST applied (₹88,395.76
    on a ₹4,91,087.50 line total). Securities are excluded from GST
    under Schedule III of the CGST Act, so this MUST end up at the
    `Polemarch - No GST` template (which has empty `taxes:[]`).

    For Mithtech-Services-branded fee invoices we likewise force
    `Mithtech Services - GST 18%` so the GSTR-1 cost-center
    classification is correct.

    Mechanism: change `taxes_and_charges`, then call
    `set_other_charges()` (an ERPNext AccountsController helper) which
    clears the existing rows and refills from the new template — so
    Polemarch invoices end up with no tax rows and Mithtech ones with
    the right CGST/SGST/IGST trio.

    Additionally, for Polemarch share invoices, set each item's
    `gst_treatment = "Non-GST"`. This stops India Compliance's
    `set_default_treatment` (which runs against the new empty
    template) from labelling the rows "Nil-Rated" instead of the
    legally-correct "Non-GST" classification for securities.
    """
    target_title = None
    if is_polemarch:
        target_title = POLEMARCH_TAX_TEMPLATE
    elif is_mithtech:
        target_title = MITHTECH_SERVICES_TAX_TEMPLATE
    if not target_title:
        return

    template_name = frappe.db.get_value(
        "Sales Taxes and Charges Template",
        {"title": target_title, "company": doc.company},
        "name",
    )
    if not template_name:
        # Tax templates are auto-created by `polemarch.install.setup()`
        # for every company. If we got here without one, the install
        # hook hasn't run yet for this company — surface it loudly so
        # the operator runs `bench --site <site> migrate` rather than
        # silently letting India Compliance's default template through.
        frappe.log_error(
            title="Polemarch tax template missing",
            message=f"Expected '{target_title}' template for company "
                    f"{doc.company}; falling back to whatever was set. "
                    f"Run `bench migrate` to recreate.",
        )
        return

    if doc.taxes_and_charges != template_name:
        doc.taxes_and_charges = template_name
        # `set_other_charges()` clears `doc.taxes` and refills from the
        # new template. Because we run in `before_validate`, the
        # downstream ERPNext + IC validate pipeline will recompute
        # item-level GST fields (cgst_rate, sgst_rate, …) from these
        # fresh tax rows with proper state-aware resolution, so we
        # don't need to call `calculate_taxes_and_totals()` ourselves.
        if hasattr(doc, "set_other_charges"):
            doc.set_other_charges()
        else:
            doc.set("taxes", [])


def on_submit(doc, method=None):
    if doc.get("custom_medusa_order_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_orders.mirror_status",
            queue="short",
            invoice_name=doc.name,
            new_status="paid",
            enqueue_after_commit=True,
        )


def on_cancel(doc, method=None):
    if doc.get("custom_medusa_order_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_orders.mirror_status",
            queue="short",
            invoice_name=doc.name,
            new_status="canceled",
            enqueue_after_commit=True,
        )


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
