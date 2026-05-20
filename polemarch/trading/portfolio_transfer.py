"""Portfolio Transfer Posted-state engine.

`post_transfer(name)` is the only path that takes an Approved Portfolio
Transfer to Posted. It:

  1. Acquires both portfolios' advisory locks in sorted name order
     (deadlock avoidance).
  2. Writes Consume SLLE rows against each source lot.
  3. Creates new Security Lot(s) on the destination portfolio.
     - At-cost transfer (default): cost_basis_per_unit = source.cost_basis_per_unit
       (one new lot per source lot, preserving lot identity)
     - FMV transfer (future): cost_basis_per_unit = transfer.fmv_per_unit
       (single consolidated lot at transfer date)
     This implementation uses the **at-cost** policy by default. FMV mode
     is reserved for cross-beneficial-owner transfers.
  4. Posts a Journal Entry (DR destination-inventory / CR source-inventory)
     at total cost basis. No P&L impact at default policy.
  5. Cross-links `reversal_of` ↔ `reversed_by` if this transfer reverses
     a prior one.
  6. Transitions Approved → Posted.

Idempotent: re-running on an already-Posted transfer is a no-op (returns the
existing JE name).
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime


def post_transfer(name: str) -> str:
    """Drive an Approved Portfolio Transfer to Posted. Returns its name."""
    pt = frappe.get_doc("Portfolio Transfer", name)
    if pt.transfer_state == "Posted":
        return pt.name
    if pt.transfer_state != "Approved":
        frappe.throw(
            _(
                "Portfolio Transfer {0}: must be Approved before posting (state={1})."
            ).format(name, pt.transfer_state),
            title=_("Cannot Post"),
        )

    if not pt.lots_consumed:
        frappe.throw(
            _("Portfolio Transfer {0}: no lots planned (empty lots_consumed table).").format(name),
            title=_("Nothing to Post"),
        )

    # 1) Sorted advisory locks for the two portfolios.
    keys = sorted([f"polemarch:pt:{pt.from_portfolio}", f"polemarch:pt:{pt.to_portfolio}"])
    for key in keys:
        result = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (key, 5), as_list=True)
        if not result or not result[0] or not result[0][0]:
            frappe.throw(
                _("Could not acquire Portfolio Transfer lock for {0}.").format(key),
                title=_("Lock Timeout"),
            )

    # 2) Consume SLLE per source lot.
    _write_consume_slles(pt)

    # 3) Create destination lots.
    _create_destination_lots(pt)

    # 4) Post the JE.
    je_name = _post_je(pt)
    if je_name:
        frappe.db.set_value(
            "Portfolio Transfer", pt.name, "journal_entry", je_name, update_modified=False
        )

    # 5) Cross-link reversal pointers.
    if pt.reversal_of and not frappe.db.get_value("Portfolio Transfer", pt.reversal_of, "reversed_by"):
        frappe.db.set_value(
            "Portfolio Transfer",
            pt.reversal_of,
            "reversed_by",
            pt.name,
            update_modified=False,
        )

    # 6) Transition state.
    pt.reload()
    pt.transition_to("Posted")
    return pt.name


# ── helpers ──────────────────────────────────────────────────────────────


def _write_consume_slles(pt) -> None:
    for lot in pt.lots_consumed or []:
        if frappe.db.exists(
            "Security Lot Ledger Entry",
            {
                "reference_doctype": "Portfolio Transfer",
                "reference_name": pt.name,
                "security_lot": lot.source_lot,
                "entry_type": "Consume",
                "is_cancelled": 0,
            },
        ):
            continue

        slle = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": lot.source_lot,
                "entry_type": "Consume",
                "qty": flt(lot.qty_consumed),
                "cost_basis_per_unit": flt(lot.original_cost_basis_per_unit),
                "sale_price_per_unit": flt(lot.transfer_fmv_per_unit),
                "reference_doctype": "Portfolio Transfer",
                "reference_name": pt.name,
                "holding_period_days": lot.holding_period_days_at_transfer,
                "is_long_term": lot.is_long_term_at_transfer,
                "realized_gain": 0,
            }
        )
        slle.flags.ignore_permissions = True
        slle.insert(ignore_permissions=True)
        slle.submit()


def _create_destination_lots(pt) -> None:
    """At-cost policy: one new Security Lot per source lot on the destination
    portfolio. Preserves lot identity for FIFO ordering on subsequent sales."""
    to_portfolio = frappe.db.get_value(
        "Portfolio", pt.to_portfolio, ("owner_kind", "customer"), as_dict=True
    )
    owning_customer = (
        to_portfolio.customer
        if to_portfolio and to_portfolio.owner_kind == "Customer"
        else None
    )

    for lot in pt.lots_consumed or []:
        if lot.new_lot:
            # Already created on a prior run; idempotent.
            continue

        source = frappe.db.get_value(
            "Security Lot",
            lot.source_lot,
            ("acquisition_date", "purchase_reference", "purchase_reference_link"),
            as_dict=True,
        )
        if not source:
            continue

        new_lot = frappe.get_doc(
            {
                "doctype": "Security Lot",
                "security": pt.security,
                "portfolio": pt.to_portfolio,
                "owning_customer": owning_customer,
                "acquisition_date": source.acquisition_date,
                "qty_acquired": flt(lot.qty_consumed),
                "cost_basis_per_unit": flt(lot.original_cost_basis_per_unit),
                "purchase_reference": "Portfolio Transfer",
                "purchase_reference_link": pt.name,
                "notes": (
                    f"Created by Portfolio Transfer {pt.name} from source lot {lot.source_lot}. "
                    f"Holding-period clock preserved from source acquisition date."
                ),
            }
        )
        new_lot.flags.ignore_permissions = True
        new_lot.insert(ignore_permissions=True)

        # Acquire SLLE for the new lot.
        acq = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": new_lot.name,
                "entry_type": "Acquire",
                "qty": flt(lot.qty_consumed),
                "cost_basis_per_unit": flt(lot.original_cost_basis_per_unit),
                "reference_doctype": "Portfolio Transfer",
                "reference_name": pt.name,
            }
        )
        acq.flags.ignore_permissions = True
        acq.insert(ignore_permissions=True)
        acq.submit()

        frappe.db.set_value(
            "Portfolio Transfer Lot", lot.name, "new_lot", new_lot.name, update_modified=False
        )


def _post_je(pt) -> Optional[str]:
    """At-cost reclassification JE:

        DR  <destination-inventory-account>   total_cost
        CR  <source-inventory-account>        total_cost

    No P&L. Idempotent via custom_source_doctype/name on Journal Entry.
    """
    if not frappe.db.exists(
        "Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_doctype"}
    ):
        # Phase 3 patch hasn't run; skip cleanly.
        return None

    existing = frappe.db.get_value(
        "Journal Entry",
        {
            "custom_source_doctype": "Portfolio Transfer",
            "custom_source_name": pt.name,
            "docstatus": ["!=", 2],
        },
        "name",
    )
    if existing:
        return existing

    total_cost = sum(flt(l.cost_basis_amount) for l in pt.lots_consumed or [])
    if total_cost <= 0:
        return None

    abbr = frappe.db.get_value("Company", pt.company, "abbr")
    if not abbr:
        return None

    from_account = _portfolio_inventory_account(pt.from_portfolio, abbr, pt.company)
    to_account = _portfolio_inventory_account(pt.to_portfolio, abbr, pt.company)
    if not from_account or not to_account:
        frappe.log_error(
            f"Portfolio Transfer {pt.name}: inventory accounts missing "
            f"(from={from_account}, to={to_account}); JE skipped.",
            "Polemarch Portfolio Transfer JE",
        )
        return None

    cost_center = frappe.db.get_value(
        "Cost Center",
        {"cost_center_name": "Polemarch", "company": pt.company, "is_group": 0},
        "name",
    )

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.posting_date = pt.transfer_date or getdate()
    je.company = pt.company
    je.user_remark = (
        f"Polemarch Portfolio Transfer | {pt.name} | "
        f"{pt.from_portfolio} → {pt.to_portfolio} | {pt.security}"
    )
    je.custom_source_doctype = "Portfolio Transfer"
    je.custom_source_name = pt.name
    je.append("accounts", {
        "account": to_account,
        "debit_in_account_currency": total_cost,
        "cost_center": cost_center,
        "reference_type": "Portfolio Transfer",
        "reference_name": pt.name,
    })
    je.append("accounts", {
        "account": from_account,
        "credit_in_account_currency": total_cost,
        "cost_center": cost_center,
        "reference_type": "Portfolio Transfer",
        "reference_name": pt.name,
    })
    je.flags.ignore_permissions = True
    je.insert(ignore_permissions=True)
    je.submit()
    return je.name


def _portfolio_inventory_account(portfolio: str, abbr: str, company: str) -> Optional[str]:
    portfolio_type = frappe.db.get_value("Portfolio", portfolio, "portfolio_type")
    logical = (
        "Long-Term Investments" if portfolio_type == "Investment"
        else "Securities Inventory - Trading"
    )
    candidate = f"{logical} - {abbr}"
    if frappe.db.exists("Account", candidate):
        return candidate
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_name": ["like", f"{logical}%"], "disabled": 0},
        "name",
    )
