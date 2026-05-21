"""Cross-cutting doc_event handlers for the trading subsystem.

These are wired into `polemarch/hooks.py` as `doc_events`. They live in this
module (not on the doctype controllers themselves) because they react to
events on standard ERPNext doctypes (Purchase Invoice, Bank Transaction,
Journal Entry) that Polemarch shouldn't subclass.
"""

import frappe
from frappe.utils import flt, getdate, now_datetime


# ── Purchase Invoice: proprietary share acquisitions ─────────────────────


def purchase_invoice_on_submit(doc, method=None):
    """Phase 2: when a Purchase Invoice with brand=Polemarch lines is
    submitted, mint a Security Lot per line + emit an Acquire SLLE row.

    The expense account override (routing cost to Securities Inventory -
    Trading / Long-Term Investments) is handled in `purchase_invoice_validate`
    BEFORE ERPNext's GL engine reads the line's expense account.
    """
    if not frappe.db.table_exists("Security Lot"):
        return
    if not _has_polemarch_lines(doc):
        return

    for line in doc.items or []:
        if not _is_polemarch_line(line):
            continue
        _create_security_lot_from_pi_line(doc, line)


def purchase_invoice_validate(doc, method=None):
    """Override the expense account on every brand=Polemarch line BEFORE
    ERPNext posts GL. Routes the cost to Securities Inventory - Trading
    (Trading book) or Long-Term Investments (Investment book), determined
    by the security's default portfolio."""
    if not _has_polemarch_lines(doc):
        return

    for line in doc.items or []:
        if not _is_polemarch_line(line):
            continue
        account = _resolve_polemarch_expense_account(doc.company, line)
        if account:
            line.expense_account = account


def purchase_invoice_on_cancel(doc, method=None):
    """If we minted Security Lots on submit, cancel the corresponding SLLE
    Acquire rows and soft-close the lots. Idempotent — safe to re-run."""
    if not frappe.db.table_exists("Security Lot Ledger Entry"):
        return

    # SLLEs we wrote against this PI are reference_doctype="Purchase Invoice"
    # and reference_name=<pi name>. Walk and reverse.
    acquires = frappe.get_all(
        "Security Lot Ledger Entry",
        filters={
            "reference_doctype": "Purchase Invoice",
            "reference_name": doc.name,
            "entry_type": "Acquire",
            "is_cancelled": 0,
            "docstatus": 1,
        },
        fields=["name", "security_lot", "qty", "cost_basis_per_unit"],
    )
    for row in acquires:
        reversal = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": row.security_lot,
                "entry_type": "Reversal",
                "qty": row.qty,
                "cost_basis_per_unit": row.cost_basis_per_unit,
                "reverses": row.name,
                "reference_doctype": "Purchase Invoice",
                "reference_name": doc.name,
            }
        )
        reversal.flags.ignore_permissions = True
        reversal.insert(ignore_permissions=True)
        reversal.submit()

        original = frappe.get_doc("Security Lot Ledger Entry", row.name)
        original.flags.from_reversal = True
        original.is_cancelled = 1
        original.reversed_by = reversal.name
        original.db_update()


# ── Bank Transaction: deposit reconciliation (Phase 5 — wallet credit) ──


def bank_transaction_on_submit(doc, method=None):
    """Stub for Phase 5. Bank Transaction reconciliation will resolve to a
    Customer by VBA ID and credit their wallet via `wallet.apply_delta`.
    Today it's a no-op so submitting a Bank Transaction doesn't fail in
    Phase 2 deployments that wire the hook eagerly."""
    return


# ── Journal Entry validate — audit hook ─────────────────────────────────


def journal_entry_validate(doc, method=None):
    """Light audit hook: stamp every Polemarch-tagged JE with normalised
    fields. Phase 3 will add custom_source_doctype / custom_source_name
    custom fields and check them here for idempotency."""
    return


# ── Internals ────────────────────────────────────────────────────────────


def _has_polemarch_lines(doc) -> bool:
    return any(_is_polemarch_line(line) for line in (doc.items or []))


def _is_polemarch_line(line) -> bool:
    if not line.item_code:
        return False
    return frappe.db.get_value("Item", line.item_code, "brand") == "Polemarch"


def _resolve_polemarch_expense_account(company: str, line) -> str:
    """Map a Polemarch line to its inventory account.

    Strategy:
      - If the source Purchase Invoice carries a `custom_portfolio` (Phase 2
        candidate field), use it.
      - Else infer from the Item's linked Security → security_type. Equity
        defaults to Trading; Bonds/Debentures default to Investment. This is
        a heuristic; ops can override per PI line later.
    """
    abbr = frappe.db.get_value("Company", company, "abbr")
    if not abbr:
        return ""

    # Default: Trading.
    target_account = f"Securities Inventory - Trading - {abbr}"

    # Heuristic override: if the Item's Security is a long-term instrument.
    security = frappe.db.get_value("Item", line.item_code, "custom_security")
    if security:
        sec_type = frappe.db.get_value("Security", security, "security_type")
        if sec_type in ("Bond", "Debenture", "SGB"):
            target_account = f"Long-Term Investments - {abbr}"

    if frappe.db.exists("Account", target_account):
        return target_account
    return ""


def _create_security_lot_from_pi_line(doc, line) -> None:
    """One Security Lot per PI line, with a paired Acquire SLLE row.

    Lot naming uses the autoseries POL-LOT-... (Phase 0 backfill uses legacy
    INV-HOLD-... names; this is for net-new acquisitions). Idempotency keyed
    by (Purchase Invoice, child name) — if the lot exists we skip.
    """
    if frappe.db.exists(
        "Security Lot",
        {"purchase_reference": "Purchase Invoice", "purchase_reference_link": doc.name},
    ):
        # Already minted for this PI; assume idempotent re-submit.
        return

    security = frappe.db.get_value("Item", line.item_code, "custom_security")
    if not security:
        frappe.log_error(
            f"Purchase Invoice {doc.name} line {line.idx}: Item {line.item_code} has no "
            f"linked Security; skipping Security Lot creation.",
            "Polemarch PI Hook",
        )
        return

    abbr = frappe.db.get_value("Company", doc.company, "abbr")
    sec_type = frappe.db.get_value("Security", security, "security_type")
    portfolio_type = "Investment" if sec_type in ("Bond", "Debenture", "SGB") else "Trading"
    portfolio = frappe.db.get_value(
        "Portfolio",
        {"company": doc.company, "portfolio_type": portfolio_type, "owner_kind": "Proprietary"},
        "name",
    )
    if not portfolio:
        frappe.log_error(
            f"Purchase Invoice {doc.name}: no proprietary {portfolio_type} portfolio for "
            f"company {doc.company}; skipping.",
            "Polemarch PI Hook",
        )
        return

    rate = flt(line.rate)
    qty = flt(line.qty)

    lot = frappe.get_doc(
        {
            "doctype": "Security Lot",
            "security": security,
            "portfolio": portfolio,
            "acquisition_date": getdate(doc.posting_date),
            "qty_acquired": qty,
            "cost_basis_per_unit": rate,
            "purchase_reference": "Purchase Invoice",
            "purchase_reference_link": doc.name,
        }
    )
    lot.flags.ignore_permissions = True
    lot.insert(ignore_permissions=True)

    slle = frappe.get_doc(
        {
            "doctype": "Security Lot Ledger Entry",
            "security_lot": lot.name,
            "posting_datetime": now_datetime(),
            "entry_type": "Acquire",
            "qty": qty,
            "cost_basis_per_unit": rate,
            "reference_doctype": "Purchase Invoice",
            "reference_name": doc.name,
        }
    )
    slle.flags.ignore_permissions = True
    slle.insert(ignore_permissions=True)
    slle.submit()
