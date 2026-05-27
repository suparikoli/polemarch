"""v0_24_0 — backfill `classifications` child rows on any IH that has a
non-Unallocated legacy `classification` field set but zero child rows.

Gap 5 root cause: pre-this-patch, Security Purchase's
`_create_investment_holding` set the legacy `classification` field on a
newly-minted IH (e.g. "Stock in Trade") but didn't add a matching row to
the `classifications` Table. The IH's own `_compute_classification_rollup`
then ran on first save, saw zero child rows, and back-synced the legacy
field to "Unallocated".

The earlier `backfill_classification_child_rows` patch (Phase 24B-1) seeded
child rows from the legacy field for IHs created BEFORE the patch ran. But
IHs created AFTER the Phase-24B-1 patch (via Purchase) skipped the seeding
step entirely — leaving them in the broken Unallocated state even though
the operator picked SiT or Investment on the Purchase.

This patch re-runs the same seed step against the broken IHs: find every
IH where the legacy `classification` is "Stock in Trade" or "Investment"
but `classifications` is empty, then insert a single child row with
`qty = qty_acquired` (treating the entire original lot as classified at
the legacy field's value).

Idempotent. Re-runs are no-ops once every IH has the right child rows.
"""

import frappe
from frappe.utils import flt, now_datetime


def execute():
    if not frappe.db.has_column("Investment Holding", "classification"):
        return
    if not frappe.db.exists("DocType", "Investment Holding Classification"):
        return

    # Find every IH where no child rows exist. Source the intended
    # classification from one of (in priority order):
    #   1. The legacy `classification` field on the IH (if SiT or Investment)
    #   2. The source Security Purchase's `intended_classification` field
    #      (catches IHs that were silently back-synced to Unallocated by
    #      the rollup running before any child row existed — the Gap 5 bug)
    candidates = frappe.db.sql(
        """
        SELECT ih.name,
               ih.classification          AS ih_classification,
               sp.intended_classification AS sp_intended,
               ih.qty_acquired,
               COALESCE(ih.classified_by, 'Administrator')   AS classified_by,
               ih.classified_on
        FROM `tabInvestment Holding` ih
        LEFT JOIN `tabSecurity Purchase` sp
               ON sp.name = ih.purchase_reference_link
              AND ih.purchase_reference = 'Security Purchase'
        WHERE NOT EXISTS (
              SELECT 1 FROM `tabInvestment Holding Classification` ihc
              WHERE ihc.parent = ih.name
          )
        """,
        as_dict=True,
    )

    seeded = 0
    fixed_legacy_field = 0
    for c in candidates:
        # Prefer ih.classification if it's SiT/Investment; else fall back to
        # the source Purchase's intended_classification.
        derived = None
        if c.ih_classification in ("Stock in Trade", "Investment"):
            derived = c.ih_classification
        elif c.sp_intended in ("Stock in Trade", "Investment"):
            derived = c.sp_intended

        if derived not in ("Stock in Trade", "Investment"):
            continue  # Genuinely Unallocated; leave alone.

        child = frappe.get_doc(
            {
                "doctype": "Investment Holding Classification",
                "parenttype": "Investment Holding",
                "parentfield": "classifications",
                "parent": c.name,
                "classification": derived,
                "qty": flt(c.qty_acquired),
                "classified_by": c.classified_by,
                "classified_on": c.classified_on or now_datetime(),
                "auto_classified": 0,
                "notes": (
                    "Seeded by Gap 5 backfill from "
                    + ("IH.classification" if c.ih_classification == derived else "Security Purchase.intended_classification")
                ),
            }
        )
        child.flags.ignore_permissions = True
        child.insert(ignore_permissions=True)
        seeded += 1

        # Heal the legacy field on the IH too, if the rollup had
        # overwritten it to "Unallocated".
        if c.ih_classification != derived:
            frappe.db.set_value(
                "Investment Holding", c.name, "classification", derived,
                update_modified=False,
            )
            fixed_legacy_field += 1

    frappe.db.commit()

    if seeded:
        print(
            f"Gap 5 backfill: seeded {seeded} classification child rows "
            f"({fixed_legacy_field} also had the legacy `classification` field healed)."
        )
