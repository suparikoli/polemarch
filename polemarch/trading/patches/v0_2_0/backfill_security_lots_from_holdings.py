"""Create a Security Lot per legacy Investment Holding.

The new Security Lot row uses the EXACT same name as the source
Investment Holding (e.g. INV-HOLD-2026-00001) so SLLE mirror-write can
reference the lot by the holding name directly — no name translation
table needed.

Each holding must already have:
  - `custom_portfolio` set (by Phase 0 patch `backfill_portfolio_on_holdings`)
  - a Security row created from its Item (by Phase 0 patch
    `backfill_security_from_polemarch_items`)
  - Item.custom_security pointing at that Security

Holdings missing any prerequisite are skipped and logged.
"""

import frappe
from frappe.utils import flt


def execute():
    if not frappe.db.table_exists("Security Lot"):
        return

    holdings = frappe.get_all(
        "Investment Holding",
        fields=[
            "name",
            "item",
            "company",
            "acquisition_date",
            "qty_acquired",
            "cost_basis_per_unit",
            "qty_disposed",
            "purchase_reference",
            "purchase_reference_link",
            "notes",
        ],
    )

    skipped = []
    created = 0

    for h in holdings:
        if frappe.db.exists("Security Lot", h.name):
            continue

        portfolio = frappe.db.get_value(
            "Investment Holding", h.name, "custom_portfolio"
        )
        if not portfolio:
            skipped.append({"holding": h.name, "reason": "no custom_portfolio"})
            continue

        security = (
            frappe.db.get_value("Item", h.item, "custom_security") if h.item else None
        )
        if not security:
            skipped.append({"holding": h.name, "reason": f"no Security for Item {h.item}"})
            continue

        portfolio_row = frappe.db.get_value(
            "Portfolio", portfolio, ("owner_kind", "customer"), as_dict=True
        )
        owning_customer = (
            portfolio_row.customer if portfolio_row and portfolio_row.owner_kind == "Customer" else None
        )

        try:
            doc = frappe.get_doc(
                {
                    "doctype": "Security Lot",
                    "name": h.name,  # 1:1 mapping to legacy holding name
                    "security": security,
                    "portfolio": portfolio,
                    "owning_customer": owning_customer,
                    "acquisition_date": h.acquisition_date,
                    "qty_acquired": flt(h.qty_acquired),
                    "cost_basis_per_unit": flt(h.cost_basis_per_unit),
                    "purchase_reference": h.purchase_reference,
                    "purchase_reference_link": h.purchase_reference_link,
                    "notes": h.notes,
                }
            )
            # Set name directly — autoname would assign POL-LOT-… and break the 1:1 mapping.
            doc.flags.name_set = True
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True, set_name=h.name)
            created += 1
        except Exception as exc:
            skipped.append({"holding": h.name, "reason": str(exc)[:200]})

    frappe.db.commit()

    if skipped:
        frappe.log_error(
            f"backfill_security_lots_from_holdings: created={created}, skipped={len(skipped)}. "
            f"First 20: {skipped[:20]}",
            "Polemarch Security Lot Backfill",
        )
