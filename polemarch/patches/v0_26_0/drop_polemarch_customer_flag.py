"""v0_26_0 — retire the `custom_is_polemarch_customer` flag.

Operator decision: the two-flag design
  - `custom_is_polemarch_customer`  (auto-derived, read-only)
  - `custom_is_mithtech_only`       (operator-toggleable)
was redundant. Every customer in this tenant defaults to Polemarch
unless the operator explicitly opts them out via Mithtech-only. So
the auto-derived flag is dead weight — the validate hook short-
circuited it whenever `mithtech_only=1` anyway, and the rest of the
codebase always combined the two as
`is_polemarch AND NOT mithtech_only`. That collapses to
`NOT mithtech_only`.

This patch:
  1. Deletes the Customer.`custom_is_polemarch_customer` Custom Field
     and ANY Property Setters / hidden customizations referencing it.
  2. Drops the underlying column from `tabCustomer` (frappe's
     Custom Field deletion does this for free, but we double-check
     in case the field was promoted to a doctype field somewhere).
  3. Rewrites any existing Frappe Webhook condition strings that
     reference `doc.custom_is_polemarch_customer` so they don't
     AttributeError between migrate and the next Medusa reseed.
     Medusa's seeder will overwrite these the moment its build is
     redeployed; this is belt-and-suspenders for the deploy window.
  4. Re-syncs the Customer doctype meta cache so the form picks
     up the schema change immediately.

Idempotent — re-running after the field is gone is a no-op.

Filters / depends_on clauses / Frappe Webhook conditions / canonical
mapping pull_filters that referenced the old field were updated in
the same commit (install.py + sales_invoice_list.js + workspace JSON
+ Number Card JSON + Medusa plugin canonical-mappings + Frappe
Webhook seeder).
"""

import re

import frappe


def execute():
    dropped_cf = 0
    dropped_ps = 0
    rewritten_wh = 0

    # 1. Custom Field row.
    cf_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Customer", "fieldname": "custom_is_polemarch_customer"},
        "name",
    )
    if cf_name:
        frappe.delete_doc(
            "Custom Field", cf_name, force=True, ignore_permissions=True
        )
        dropped_cf = 1

    # 2. Any Property Setters keyed on the field (e.g. hidden, label
    #    overrides from earlier experiments).
    ps_rows = frappe.get_all(
        "Property Setter",
        filters={
            "doc_type": "Customer",
            "field_name": "custom_is_polemarch_customer",
        },
        pluck="name",
    )
    for ps in ps_rows:
        frappe.delete_doc(
            "Property Setter", ps, force=True, ignore_permissions=True
        )
        dropped_ps += 1

    # 3. Frappe Webhook condition rewrites. The Medusa-seeded Customer
    #    webhooks previously used:
    #      doc.custom_is_polemarch_customer and not doc.custom_is_mithtech_only
    #    After the field is dropped, that condition AttributeErrors and
    #    the webhook silently no-ops. Rewrite to the new form so the
    #    flow keeps working until Medusa's seeder next runs.
    if frappe.db.table_exists("Webhook"):
        for wh in frappe.get_all(
            "Webhook",
            filters={"webhook_doctype": "Customer"},
            fields=["name", "condition"],
        ):
            old = wh.get("condition") or ""
            if "custom_is_polemarch_customer" not in old:
                continue
            new = re.sub(
                r"doc\.custom_is_polemarch_customer\s+and\s+",
                "",
                old,
            )
            new = re.sub(
                r"\s+and\s+doc\.custom_is_polemarch_customer",
                "",
                new,
            )
            # Standalone reference with nothing to AND with.
            new = re.sub(
                r"^\s*doc\.custom_is_polemarch_customer\s*$",
                "1",
                new,
            )
            if new != old:
                frappe.db.set_value(
                    "Webhook", wh["name"], "condition", new, update_modified=False
                )
                rewritten_wh += 1

    # 4. Drop the underlying column if it still exists. Custom Field
    #    deletion above should have done this, but be defensive — if
    #    someone manually `ALTER TABLE ADD COLUMN` ed it the row
    #    delete won't clean up the column.
    #
    #    ALTER TABLE triggers an implicit commit in MariaDB and Frappe
    #    refuses to run such statements inside an open transaction
    #    (frappe.db.check_implicit_commit). Flush the pending state
    #    explicitly first.
    if frappe.db.has_column("Customer", "custom_is_polemarch_customer"):
        frappe.db.commit()
        frappe.db.sql(
            "ALTER TABLE `tabCustomer` DROP COLUMN `custom_is_polemarch_customer`"
        )
        frappe.db.commit()

    # 5. Clear meta cache so the next form render picks up the
    #    schema change without a `bench restart`.
    frappe.clear_cache(doctype="Customer")

    frappe.db.commit()
    print(
        f"v0_26_0 retired custom_is_polemarch_customer: "
        f"dropped {dropped_cf} Custom Field, {dropped_ps} Property Setter(s), "
        f"rewrote {rewritten_wh} Webhook condition(s)."
    )
