"""Backfill `classification` on existing Investment Holding rows.

Pre-v0_8_0, the routing concept was `custom_portfolio` (a Link to Portfolio,
values were typically `Trading` and `Investment`). Post-v0_8_0, that is
replaced with `classification` — a Select that takes one of three values:

    Unallocated | Stock in Trade | Investment

Mapping rule:
    custom_portfolio = "Trading"     → classification = "Stock in Trade"
    custom_portfolio = "Investment"  → classification = "Investment"
    anything else / NULL             → classification = "Unallocated"

Holdings that land in `Unallocated` get a `classification_deadline` set to
the past so the daily auto-classifier picks them up on the next run and
locks them as Stock in Trade. That keeps legacy data flowing without
forcing the operator to hand-classify every old row.

`classified_by` / `classified_on` are intentionally left NULL — these track
a *manual* classification decision, and the backfill is structural, not a
user action.

Idempotent. Re-running re-applies the mapping but only touches rows whose
classification is empty (the Custom Field's default of `Unallocated` is
applied at insert time, so most rows hit on the first run only).
"""

from datetime import timedelta

import frappe
from frappe.utils import now_datetime


def execute():
    # The classification Custom Field must exist before we can update it.
    # The add_classification_fields_to_investment_holding patch runs
    # earlier in the same v0_8_0 batch, so this should always be true —
    # but guard so a partial state doesn't crash migrate.
    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "classification"}
    ):
        return

    # The legacy `custom_portfolio` column only exists on sites that ran the
    # pre-v0_8_0 schema. Sites created after v0_14_0 dropped it (or installed
    # fresh) never had it, and there is nothing to backfill from.
    if "custom_portfolio" not in frappe.db.get_table_columns("Investment Holding"):
        return

    # Pull holdings whose classification is empty. `IS NULL OR = ''` handles
    # both pre-field-creation rows and any that ended up with an empty
    # string post-field-creation.
    rows = frappe.db.sql(
        """
        SELECT name, custom_portfolio
          FROM `tabInvestment Holding`
         WHERE classification IS NULL OR classification = ''
        """,
        as_dict=True,
    )

    if not rows:
        return

    past_deadline = now_datetime() - timedelta(days=1)

    for row in rows:
        portfolio = (row.custom_portfolio or "").strip()
        if portfolio == "Trading":
            classification = "Stock in Trade"
            deadline = None
        elif portfolio == "Investment":
            classification = "Investment"
            deadline = None
        else:
            # Unknown / empty portfolio — drop into Unallocated and set a
            # past deadline so the auto-classifier sweeps it on next tick.
            classification = "Unallocated"
            deadline = past_deadline

        frappe.db.set_value(
            "Investment Holding",
            row.name,
            {
                "classification": classification,
                "classification_deadline": deadline,
            },
            update_modified=False,
        )

    frappe.db.commit()
