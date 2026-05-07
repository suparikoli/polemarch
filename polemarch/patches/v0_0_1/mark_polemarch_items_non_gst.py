"""Tag Sales Invoice / Sales Order items belonging to Polemarch-branded
shares as `gst_treatment = "Non-GST"`, matching the legally-correct
classification (securities are excluded from GST under Schedule III of
the CGST Act).

Why this is needed:
The previous version of `polemarch.overrides.sales_invoice.validate`
guarded its tax-template assignment with `if not doc.taxes_and_charges`,
which never fired because India Compliance's earlier validate had
already filled the field with the company's default GST template. As
a result, every Polemarch share invoice ended up with 18% GST baked
into `total_taxes_and_charges` and items labelled "Taxable" — neither
fact reflected reality.

This patch:
  1. Re-saves every existing Sales Invoice / Sales Order with
     `custom_is_polemarch_invoice` (or `_order`) = 1, which now runs
     the corrected validate hook and switches the template to
     `Polemarch - No GST`. For SUBMITTED (docstatus=1) docs the SQL
     row is updated directly since you can't re-validate a submitted
     ledger entry — the tax treatment label is what matters for GSTR
     filings.
  2. Items on those rows get `gst_treatment = "Non-GST"` directly.

Idempotent — safe to re-run.
"""

import frappe


def execute():
    _patch_doctype("Sales Invoice", "Sales Invoice Item", "custom_is_polemarch_invoice")
    _patch_doctype("Sales Order", "Sales Order Item", "custom_is_polemarch_order")


def _patch_doctype(parent_dt: str, child_dt: str, flag_field: str):
    if not frappe.db.has_column(parent_dt, flag_field):
        # Custom field not installed yet — earlier migrate run will
        # create it; this patch is a no-op until then.
        return

    docs = frappe.get_all(
        parent_dt,
        filters={flag_field: 1},
        pluck="name",
    )
    if not docs:
        return

    # Tag each child item directly. Faster than re-saving the parent
    # for ~hundreds of historical rows, and avoids tripping any other
    # app's validate hook on a backfill.
    for parent in docs:
        frappe.db.sql(
            f"""
            UPDATE `tab{child_dt}`
               SET gst_treatment = 'Non-GST'
             WHERE parent = %s
               AND parenttype = %s
               AND (gst_treatment IS NULL OR gst_treatment != 'Non-GST')
            """,
            (parent, parent_dt),
        )
    frappe.db.commit()
    frappe.logger().info(
        f"polemarch: patched {len(docs)} {parent_dt} record(s) → gst_treatment=Non-GST"
    )
