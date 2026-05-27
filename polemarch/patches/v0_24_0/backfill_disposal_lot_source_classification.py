"""v0_24_0 — backfill `source_classification` on existing Investment Disposal Lot
rows + re-sync `qty_disposed_<class>` on every linked Investment Holding.

Phase 24B-1 introduced child-table classification on Investment Holding and
two derived counters (`qty_disposed_sit`, `qty_disposed_investment`) that
mirror physical disposals routed through each classification bucket. The
problem: `Investment Disposal._apply_to_holdings` only bumped the top-level
`qty_disposed`, never the per-class counters. So `Σ(child rows) - Σ(qty_disposed_<class>)`
drifts away from `qty_remaining`, eventually tripping `_validate_classification_rows`
on any subsequent save of the IH.

This patch:

  1. Adds `source_classification` to every Disposal Lot from history:
     - If the parent Disposal is back-linked to a Security Sale (Phase 9
       `polemarch_security_sale` Custom Field), use the Sale's `from_classification`.
     - Otherwise, fall back to the linked IH's current top-level
       `classification` field at patch-run time. This is best-effort —
       the rollup may have back-synced it from child rows, but for
       legacy data that's the closest we have.

  2. Re-derives `qty_disposed_sit` / `qty_disposed_investment` on every
     IH that has at least one submitted Disposal Lot referencing it, by
     summing `lot.qty_consumed` grouped by `lot.source_classification`
     (post-backfill, so step 1 must run first).

Idempotent. Re-runs are no-ops once every Lot has a source_classification.
"""

import frappe
from frappe.utils import flt


def execute():
    if not frappe.db.has_column("Investment Disposal Lot", "source_classification"):
        # Doctype JSON sync hasn't happened yet — bail. bench migrate
        # re-runs patches after schema sync, so this is benign on first
        # pass and self-heals on the next.
        return

    # ── Step 1: backfill source_classification on Disposal Lots ──────────
    lots = frappe.db.sql(
        """
        SELECT dl.name, dl.parent, dl.holding, dl.qty_consumed,
               COALESCE(dl.source_classification, '') AS sc,
               id.polemarch_security_sale,
               ih.classification AS ih_classification
        FROM `tabInvestment Disposal Lot` dl
        JOIN `tabInvestment Disposal` id ON id.name = dl.parent
        LEFT JOIN `tabInvestment Holding` ih ON ih.name = dl.holding
        WHERE id.docstatus = 1
        """,
        as_dict=True,
    )

    backfilled = 0
    for lot in lots:
        if lot.sc in ("Stock in Trade", "Investment"):
            continue
        derived = None
        if lot.polemarch_security_sale:
            derived = frappe.db.get_value(
                "Security Sale", lot.polemarch_security_sale, "from_classification"
            )
        if not derived:
            # Legacy fallback — IH's top-level classification at patch time.
            derived = lot.ih_classification
        if derived not in ("Stock in Trade", "Investment"):
            # Skip lots we genuinely can't attribute — leave NULL. The
            # accounting helper has its own legacy fallback for these.
            continue
        frappe.db.set_value(
            "Investment Disposal Lot",
            lot.name,
            "source_classification",
            derived,
            update_modified=False,
        )
        backfilled += 1

    frappe.db.commit()

    # ── Step 2: re-derive qty_disposed_<class> per Holding ───────────────
    holding_class_totals = frappe.db.sql(
        """
        SELECT dl.holding,
               dl.source_classification,
               COALESCE(SUM(dl.qty_consumed), 0) AS qty
        FROM `tabInvestment Disposal Lot` dl
        JOIN `tabInvestment Disposal` id ON id.name = dl.parent
        WHERE id.docstatus = 1
          AND dl.source_classification IN ('Stock in Trade', 'Investment')
        GROUP BY dl.holding, dl.source_classification
        """,
        as_dict=True,
    )

    # Aggregate into {holding: {sit, inv}}
    by_holding: dict = {}
    for row in holding_class_totals:
        by_holding.setdefault(row.holding, {"sit": 0.0, "inv": 0.0})
        bucket = "sit" if row.source_classification == "Stock in Trade" else "inv"
        by_holding[row.holding][bucket] += flt(row.qty)

    resynced = 0
    for holding_name, totals in by_holding.items():
        if not frappe.db.exists("Investment Holding", holding_name):
            continue
        frappe.db.set_value(
            "Investment Holding",
            holding_name,
            {
                "qty_disposed_sit": totals["sit"],
                "qty_disposed_investment": totals["inv"],
            },
            update_modified=False,
        )
        resynced += 1

    frappe.db.commit()

    if backfilled or resynced:
        print(
            f"Disposal Lot source_classification backfill: "
            f"populated {backfilled} lots; re-derived qty_disposed_<class> on {resynced} Holdings."
        )
