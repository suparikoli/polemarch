"""Cross-cutting doc_event handlers for the trading subsystem.

These are wired into `polemarch/hooks.py` as `doc_events`. They live in this
module (not on the doctype controllers themselves) because they react to
events on standard ERPNext doctypes (Purchase Invoice, Bank Transaction,
Journal Entry) that Polemarch shouldn't subclass.
"""

import frappe
from frappe.utils import flt


# ── Purchase Invoice: proprietary share acquisitions ─────────────────────


def purchase_invoice_on_submit(doc, method=None):
    """When a Purchase Invoice with brand=Polemarch lines is submitted, create
    one Investment Holding per line. New holdings start `classification =
    Unallocated` and have 2 working days to be manually classified as
    Investment (else they auto-classify to Stock in Trade via the daily
    scheduler).

    The expense account override (routing cost to Securities Inventory -
    Trading by default) is handled in `purchase_invoice_validate` BEFORE
    ERPNext's GL engine reads the line's expense account.
    """
    if not _has_polemarch_lines(doc):
        return

    for line in doc.items or []:
        if not _is_polemarch_line(line):
            continue
        _create_investment_holding_from_pi_line(doc, line)


def purchase_invoice_validate(doc, method=None):
    """Override the expense account on every brand=Polemarch line BEFORE
    ERPNext posts GL. Routes the cost to Securities Inventory - Trading
    (the default — the classification engine reclassifies later if needed)."""
    if not _has_polemarch_lines(doc):
        return

    for line in doc.items or []:
        if not _is_polemarch_line(line):
            continue
        account = _resolve_polemarch_expense_account(doc.company, line)
        if account:
            line.expense_account = account


def purchase_invoice_on_cancel(doc, method=None):
    """Cancel any Investment Holdings created by this PI submit.
    Idempotent — safe to re-run."""
    if not frappe.db.table_exists("Investment Holding"):
        return

    holdings = frappe.get_all(
        "Investment Holding",
        filters={
            "purchase_reference": "Purchase Invoice",
            "purchase_reference_link": doc.name,
        },
        fields=["name", "qty_disposed", "qty_reserved"],
    )
    for row in holdings:
        # If any quantity has been disposed or reserved, we can't safely delete
        # — fall back to leaving the holding in place with a comment.
        if flt(row.qty_disposed) > 0 or flt(row.get("qty_reserved", 0)) > 0:
            frappe.get_doc("Investment Holding", row.name).add_comment(
                "Comment",
                f"Source Purchase Invoice {doc.name} was cancelled but this "
                f"Holding has disposed/reserved qty — left in place for audit. "
                f"Operator must reconcile manually.",
            )
            continue
        frappe.delete_doc("Investment Holding", row.name, ignore_permissions=True)


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
    """Map a Polemarch line to the default Securities Inventory account.

    Classification (Stock in Trade vs Investment) happens AFTER purchase
    via the 2-working-day rule, so all incoming Holdings default to the
    Trading inventory account. Portfolio Transfer reclassifies them later
    if needed (with a corresponding inventory GL move).
    """
    abbr = frappe.db.get_value("Company", company, "abbr")
    if not abbr:
        return ""
    target_account = f"Securities Inventory - Trading - {abbr}"
    if frappe.db.exists("Account", target_account):
        return target_account
    return ""


def _create_investment_holding_from_pi_line(doc, line) -> None:
    """One Investment Holding per PI line. classification = Unallocated by
    default (the InvestmentHolding.before_insert hook stamps the
    classification_deadline = creation + 2 working days)."""
    if frappe.db.exists(
        "Investment Holding",
        {"purchase_reference": "Purchase Invoice", "purchase_reference_link": doc.name},
    ):
        # Already minted for this PI — idempotent re-submit.
        return

    item_code = line.item_code

    holding = frappe.get_doc({
        "doctype": "Investment Holding",
        "item": item_code,
        "company": doc.company,
        "acquisition_date": doc.posting_date,
        "qty_acquired": flt(line.qty),
        "cost_basis_per_unit": flt(line.rate),
        "purchase_reference": "Purchase Invoice",
        "purchase_reference_link": doc.name,
    })
    holding.flags.ignore_permissions = True
    holding.insert(ignore_permissions=True)


