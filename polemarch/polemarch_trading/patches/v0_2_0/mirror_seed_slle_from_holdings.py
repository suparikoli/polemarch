"""One-shot mirror seed: build SLLE history from existing Investment Disposals.

For every Security Lot created by `backfill_security_lots_from_holdings`,
emit:

  1. One Acquire SLLE row per lot (snapshot of acquisition).
  2. One Consume SLLE row per Investment Disposal Lot that referenced
     this holding (snapshot of each historical disposal).

After this runs, the SLLE rollup matches the legacy Investment Holding's
`qty_disposed`. The daily audit job (`verify_holding_lot_ledger_mirror`)
will go quiet.

Idempotent: skips lots that already have any SLLE row.
"""

import frappe
from frappe.utils import flt, get_datetime, getdate


def execute():
    if not frappe.db.table_exists("Security Lot Ledger Entry"):
        return
    if not frappe.db.table_exists("Security Lot"):
        return

    lots = frappe.get_all(
        "Security Lot",
        fields=["name", "qty_acquired", "cost_basis_per_unit", "acquisition_date"],
    )

    seeded_acquire = 0
    seeded_consume = 0

    for lot in lots:
        # Skip if any SLLE already exists for this lot — idempotency guard.
        if frappe.db.exists("Security Lot Ledger Entry", {"security_lot": lot.name}):
            continue

        # 1) Acquire row at acquisition_date.
        acq_dt = get_datetime(f"{lot.acquisition_date} 00:00:00") if lot.acquisition_date else get_datetime()
        try:
            acq = frappe.get_doc(
                {
                    "doctype": "Security Lot Ledger Entry",
                    "security_lot": lot.name,
                    "posting_datetime": acq_dt,
                    "entry_type": "Acquire",
                    "qty": flt(lot.qty_acquired),
                    "cost_basis_per_unit": flt(lot.cost_basis_per_unit),
                    "reference_doctype": "Investment Holding",
                    "reference_name": lot.name,
                }
            )
            acq.flags.ignore_permissions = True
            acq.insert(ignore_permissions=True)
            acq.submit()
            seeded_acquire += 1
        except Exception as exc:
            frappe.log_error(
                f"mirror_seed_slle: Acquire failed for lot {lot.name}: {exc}",
                "Polemarch SLLE Mirror Seed",
            )
            continue

        # 2) Consume rows from historical Investment Disposal Lots.
        disposal_lots = frappe.get_all(
            "Investment Disposal Lot",
            filters={"holding": lot.name},
            fields=[
                "parent",
                "qty_consumed",
                "cost_basis_per_unit",
                "sale_price_per_unit",
                "holding_period_days",
                "is_long_term",
                "realized_gain",
            ],
        )

        for dl in disposal_lots:
            parent_status = frappe.db.get_value("Investment Disposal", dl.parent, "docstatus")
            if parent_status != 1:
                continue
            disposal_date = frappe.db.get_value("Investment Disposal", dl.parent, "disposal_date")
            consume_dt = (
                get_datetime(f"{disposal_date} 00:00:00") if disposal_date else get_datetime()
            )
            try:
                slle = frappe.get_doc(
                    {
                        "doctype": "Security Lot Ledger Entry",
                        "security_lot": lot.name,
                        "posting_datetime": consume_dt,
                        "entry_type": "Consume",
                        "qty": flt(dl.qty_consumed),
                        "cost_basis_per_unit": flt(dl.cost_basis_per_unit),
                        "sale_price_per_unit": flt(dl.sale_price_per_unit),
                        "reference_doctype": "Investment Disposal",
                        "reference_name": dl.parent,
                        "holding_period_days": dl.holding_period_days,
                        "is_long_term": dl.is_long_term,
                        "realized_gain": flt(dl.realized_gain),
                    }
                )
                slle.flags.ignore_permissions = True
                slle.insert(ignore_permissions=True)
                slle.submit()
                seeded_consume += 1
            except Exception as exc:
                frappe.log_error(
                    f"mirror_seed_slle: Consume failed for lot {lot.name} parent {dl.parent}: {exc}",
                    "Polemarch SLLE Mirror Seed",
                )

    frappe.db.commit()

    if seeded_acquire or seeded_consume:
        frappe.log_error(
            f"mirror_seed_slle_from_holdings: seeded {seeded_acquire} Acquire, {seeded_consume} Consume rows.",
            "Polemarch SLLE Mirror Seed",
        )
