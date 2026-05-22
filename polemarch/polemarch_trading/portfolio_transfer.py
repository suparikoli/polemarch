"""Portfolio Transfer Posted-state engine.

Refactored to operate on Investment Holding `classification` field rather
than Security Lots. The transfer moves shares between classifications
(Stock in Trade ↔ Investment), with optional FMV revaluation.

`post_transfer(name)` is the only path that takes an Approved Portfolio
Transfer to Posted. It:

  1. Walks lots_consumed (the FIFO plan against Investment Holdings).
  2. For each: marks the source Holding as fully disposed (qty_disposed +=
     qty_transferred) and creates a NEW Investment Holding on the target
     classification at the original cost basis (at-cost default).
  3. Posts a Journal Entry (DR target-inventory / CR source-inventory) at
     total cost basis. No P&L impact at default policy.
  4. Cross-links `reversal_of` ↔ `reversed_by` if applicable.
  5. Transitions Approved → Posted.

Idempotent: re-running on an already-Posted transfer is a no-op.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime


def post_transfer(name: str) -> str:
    pt = frappe.get_doc("Portfolio Transfer", name)
    if pt.transfer_state == "Posted":
        return pt.name
    if pt.transfer_state != "Approved":
        frappe.throw(
            _("Portfolio Transfer {0}: must be Approved before posting (state={1}).").format(
                name, pt.transfer_state
            ),
            title=_("Cannot Post"),
        )

    if not pt.lots_consumed:
        frappe.throw(
            _("Portfolio Transfer {0}: no source holdings planned.").format(name),
            title=_("Nothing to Post"),
        )

    keys = sorted([
        f"polemarch:pt:{pt.from_classification or 'src'}",
        f"polemarch:pt:{pt.to_classification or 'dst'}",
    ])
    for key in keys:
        result = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (key, 5), as_list=True)
        if not result or not result[0] or not result[0][0]:
            frappe.throw(
                _("Could not acquire Portfolio Transfer lock for {0}.").format(key),
                title=_("Lock Timeout"),
            )

    _transfer_holdings(pt)
    je_name = _post_je(pt)
    if je_name:
        frappe.db.set_value(
            "Portfolio Transfer", pt.name, "journal_entry", je_name, update_modified=False
        )

    if pt.reversal_of and not frappe.db.get_value("Portfolio Transfer", pt.reversal_of, "reversed_by"):
        frappe.db.set_value(
            "Portfolio Transfer", pt.reversal_of, "reversed_by", pt.name, update_modified=False
        )

    pt.reload()
    pt.transition_to("Posted")
    return pt.name


# ── helpers ──────────────────────────────────────────────────────────────


def _transfer_holdings(pt) -> None:
    """Consume from source Holdings, mint new Holdings on target classification."""
    for lot in pt.lots_consumed or []:
        if lot.get("new_lot"):
            continue
        if not lot.source_lot:
            continue

        source = frappe.get_doc("Investment Holding", lot.source_lot)
        qty = flt(lot.qty_consumed)
        if qty <= 0:
            continue

        source.qty_disposed = flt(source.qty_disposed or 0) + qty
        source.flags.ignore_permissions = True
        source.save(ignore_permissions=True)

        new_holding = frappe.get_doc({
            "doctype": "Investment Holding",
            # Carry both keys forward: `security` is the new primary identity,
            # `item` stays populated so legacy queries on Item-keyed Holdings
            # still see the new row. New-world Holdings (from Security
            # Purchase) won't have `item` — getattr handles that case.
            "security": getattr(source, "security", None),
            "item": getattr(source, "item", None),
            "company": source.company,
            "acquisition_date": source.acquisition_date,
            "qty_acquired": qty,
            "cost_basis_per_unit": flt(lot.original_cost_basis_per_unit),
            "purchase_reference": "Portfolio Transfer",
            "purchase_reference_link": pt.name,
            "classification": pt.to_classification,
            "classified_on": now_datetime(),
            "classified_by": frappe.session.user,
            "notes": (
                f"Created by Portfolio Transfer {pt.name} from source Holding {source.name}. "
                f"Holding-period clock preserved from source acquisition date."
            ),
        })
        new_holding.flags.ignore_permissions = True
        new_holding.insert(ignore_permissions=True)

        frappe.db.set_value(
            "Portfolio Transfer Lot", lot.name, "new_lot", new_holding.name, update_modified=False
        )


def _post_je(pt) -> Optional[str]:
    """At-cost reclassification JE:

        DR  <to_classification inventory account>     total_cost
        CR  <from_classification inventory account>   total_cost

    Idempotent via custom_source_doctype/name on Journal Entry.
    """
    if not frappe.db.exists(
        "Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_doctype"}
    ):
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

    from_account = _classification_inventory_account(pt.from_classification, abbr, pt.company)
    to_account = _classification_inventory_account(pt.to_classification, abbr, pt.company)
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
        f"{pt.from_classification} → {pt.to_classification} | {pt.security}"
    )
    je.custom_source_doctype = "Portfolio Transfer"
    je.custom_source_name = pt.name
    # Note: JE Account.reference_type is a stock ERPNext Select with a hard-
    # coded list (Sales Invoice / Purchase Invoice / Journal Entry / etc.) —
    # Polemarch doctypes aren't in the allowlist. The PT linkage lives on
    # the JE header (custom_source_doctype + custom_source_name) and on the
    # `polemarch_portfolio_transfer` link in dashboards, so leaving the
    # per-row reference empty is fine and avoids the stock validation throw.
    je.append("accounts", {
        "account": to_account,
        "debit_in_account_currency": total_cost,
        "cost_center": cost_center,
    })
    je.append("accounts", {
        "account": from_account,
        "credit_in_account_currency": total_cost,
        "cost_center": cost_center,
    })
    je.flags.ignore_permissions = True
    je.insert(ignore_permissions=True)
    je.submit()
    return je.name


def _classification_inventory_account(classification: str, abbr: str, company: str) -> Optional[str]:
    logical = (
        "Long-Term Investments" if classification == "Investment"
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
