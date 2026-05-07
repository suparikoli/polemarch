"""Repair `Mithtech Services - GST 18% - <abbr>` Sales Taxes and Charges
Templates that were created with two compounding bugs:

  1. `_gst_account()` used `LIKE '%Output Tax CGST%'` which alphabetically
     matched `Output Tax CGST Refund - <abbr>` instead of the regular
     output account — so the template's account_head pointed at refund
     accounts that India Compliance doesn't recognise as GST
     accounts at all (`gst_tax_type` stayed NULL on the rows).

  2. The template included an IGST 18 row alongside CGST 9 + SGST 9.
     India Compliance's intra/inter-state suppression doesn't reduce a
     mixed-shape template, so intra-state Mithtech invoices ended up
     with all three taxes applied (36% total instead of 18%).

We *update in place* (not delete-and-recreate) so submitted Sales
Invoice / Sales Order ledger rows that reference the template by name
keep their reference intact. The existing rows are wiped and rebuilt
with the corrected accounts + intra-state shape (CGST 9 + SGST 9), so
the next save against the template will pick up the right tax
behaviour. Idempotent — safe to re-run.

Note on already-posted invoices: their `taxes` child rows are
immutable copies snapshotted at submission time. Re-submitting (after
cancel) under the corrected template will produce a clean ledger.
This patch does NOT touch them.
"""

import frappe


def execute():
    templates = frappe.get_all(
        "Sales Taxes and Charges Template",
        filters={"title": "Mithtech Services - GST 18%"},
        fields=["name", "company"],
    )

    if not templates:
        return

    repaired = 0
    for tpl in templates:
        company = tpl["company"]
        cgst = _gst_account(company, "Output Tax CGST")
        sgst = _gst_account(company, "Output Tax SGST")
        if not (cgst and sgst):
            frappe.logger().warning(
                f"polemarch: cannot repair {tpl['name']} — "
                f"non-Refund Output Tax CGST/SGST accounts not found "
                f"for company {company}. Skipping."
            )
            continue

        if not _needs_repair(tpl["name"], cgst, sgst):
            continue

        # Wipe + rebuild the child rows. Direct SQL avoids tripping
        # any save hooks that might reject the half-state.
        frappe.db.delete("Sales Taxes and Charges", {"parent": tpl["name"]})
        for idx, (acct, desc) in enumerate(
            [(cgst, "CGST"), (sgst, "SGST")], start=1
        ):
            frappe.get_doc(
                {
                    "doctype": "Sales Taxes and Charges",
                    "parenttype": "Sales Taxes and Charges Template",
                    "parent": tpl["name"],
                    "parentfield": "taxes",
                    "idx": idx,
                    "charge_type": "On Net Total",
                    "account_head": acct,
                    "description": desc,
                    "rate": 9,
                }
            ).insert(ignore_permissions=True)
        repaired += 1
        frappe.logger().info(
            f"polemarch: repaired {tpl['name']} — "
            f"now CGST={cgst}, SGST={sgst}"
        )

    if repaired:
        frappe.db.commit()
        frappe.logger().info(
            f"polemarch: repaired {repaired} Mithtech tax template(s)"
        )


def _gst_account(company: str, account_name_part: str):
    return frappe.db.get_value(
        "Account",
        [
            ["company", "=", company],
            ["account_name", "like", f"{account_name_part}%"],
            ["account_name", "not like", "%Refund%"],
            ["account_name", "not like", "%RCM%"],
            ["is_group", "=", 0],
        ],
        "name",
    )


def _needs_repair(template_name: str, expected_cgst: str, expected_sgst: str) -> bool:
    """A template needs repair if it has IGST rows, Refund-account
    rows, or a row count other than 2 (CGST + SGST)."""
    rows = frappe.get_all(
        "Sales Taxes and Charges",
        filters={"parent": template_name},
        fields=["account_head", "description", "rate"],
    )
    if len(rows) != 2:
        return True
    accounts = {r["account_head"] for r in rows}
    if expected_cgst not in accounts or expected_sgst not in accounts:
        return True
    if any("Refund" in (r["account_head"] or "") for r in rows):
        return True
    if any((r["description"] or "").upper() == "IGST" for r in rows):
        return True
    return False
