"""Journal Entry builders for the trading subsystem.

Phase 3a (this file) ships cost-recognition JEs only:

    DR  Trading COGS - Securities         (single bucket — both Trading and
                                           Investment portfolios)
    CR  Securities Inventory - Trading     (for Trading portfolio lots)
    CR  Long-Term Investments              (for Investment portfolio lots)

The native Sales Invoice GL already books `Debtors Dr / Trading Revenue Cr`
at sale_value, so gross margin (Sale − COGS) falls out automatically. LTCG
and STCG buckets remain on the Investment Disposal record itself for IT-
return reporting and are NOT GL-bucketed in Phase 3a. Phase 3b would split
revenue → capital-gains buckets at the cost of complexity that most
operators don't need.

Idempotency: every JE we emit carries `custom_source_doctype` and
`custom_source_name`. Re-posting the same source doc returns the existing
JE name instead of duplicating.

Reversal: cancelling the source Investment Disposal calls
`cancel_journal_entry_for_source(...)` which cancels the linked JE via the
ERPNext stdlib path — reverse GL entries are emitted natively by JE.cancel().
"""

from typing import Optional

import frappe
from frappe.utils import flt, getdate


def post_cost_recognition_je_for_disposal(disposal) -> Optional[str]:
    """Build + insert + submit a cost-recognition JE for an Investment Disposal.

    Returns the JE name (existing or newly created), or None if there's
    nothing to post (zero cost basis).

    Idempotent: existing JE for the same source doc short-circuits.
    """
    if not _je_custom_fields_present():
        # Phase 3 patch hasn't migrated yet; abort cleanly.
        return None

    existing = _existing_je_for_source(disposal.doctype, disposal.name)
    if existing:
        return existing

    cost_lines = _build_cost_lines_for_disposal(disposal)
    if not cost_lines:
        return None  # No cost to recognise (e.g., zero-cost transfer).

    abbr = frappe.db.get_value("Company", disposal.company, "abbr")
    if not abbr:
        frappe.log_error(
            f"Investment Disposal {disposal.name}: company {disposal.company} has no abbr; "
            "cost JE skipped.",
            "Polemarch Cost JE",
        )
        return None

    cogs_account = _resolve_account(disposal.company, "Trading COGS - Securities", abbr)
    if not cogs_account:
        frappe.log_error(
            f"Investment Disposal {disposal.name}: Trading COGS account missing for "
            f"company {disposal.company}; cost JE skipped.",
            "Polemarch Cost JE",
        )
        return None

    cost_center = _resolve_cost_center(disposal)
    je_accounts = []

    # Cr — inventory accounts, grouped by portfolio bucket.
    for account_logical, amount in cost_lines.items():
        if flt(amount) <= 0:
            continue
        account_name = _resolve_account(disposal.company, account_logical, abbr)
        if not account_name:
            frappe.log_error(
                f"Investment Disposal {disposal.name}: account {account_logical} - {abbr} "
                "missing; cost JE skipped.",
                "Polemarch Cost JE",
            )
            return None
        je_accounts.append(
            {
                "account": account_name,
                "credit_in_account_currency": flt(amount),
                "cost_center": cost_center,
                # JE Account.reference_type is a Select restricted to ERPNext-
                # native voucher doctypes (SI, PI, Payment Entry, ...). The
                # custom Disposal back-link lives on the parent JE via
                # custom_source_doctype / custom_source_name (set below).
            }
        )

    if not je_accounts:
        return None

    total_cost = sum(line["credit_in_account_currency"] for line in je_accounts)

    # Dr — single COGS bucket.
    je_accounts.insert(
        0,
        {
            "account": cogs_account,
            "debit_in_account_currency": total_cost,
            "cost_center": cost_center,
        },
    )

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.posting_date = disposal.disposal_date or getdate()
    je.company = disposal.company
    je.user_remark = (
        f"Polemarch cost recognition | Investment Disposal: {disposal.name}"
    )
    je.custom_source_doctype = disposal.doctype
    je.custom_source_name = disposal.name
    for row in je_accounts:
        je.append("accounts", row)
    je.flags.ignore_permissions = True
    je.insert(ignore_permissions=True)
    je.submit()

    return je.name


def cancel_journal_entry_for_source(source_doctype: str, source_name: str) -> Optional[str]:
    """Cancel the JE linked to this source doc, if any.

    Returns the cancelled JE name, or None if no JE was linked.
    ERPNext's JE.cancel() emits the reverse GL entries automatically.
    """
    if not _je_custom_fields_present():
        return None

    name = _existing_je_for_source(source_doctype, source_name)
    if not name:
        return None

    je = frappe.get_doc("Journal Entry", name)
    if je.docstatus == 1:
        je.flags.ignore_permissions = True
        je.cancel()
    return name


# ── Helpers ──────────────────────────────────────────────────────────────


def _build_cost_lines_for_disposal(disposal) -> dict:
    """Return {logical_account_name: total_cost_to_credit}.

    Routes each consumed lot's cost to the matching inventory bucket
    based on the lot's `source_classification` (Phase 24B-1 / Gap 4 fix):

      - Stock in Trade  → Securities Inventory - Trading
      - Investment      → Long-Term Investments
      - Unallocated / blank → fall back to the IH's top-level
        `classification` (legacy lots created before source_classification
        existed). The legacy path is drift-prone — the `_compute_classification_rollup`
        on IH back-syncs top-level from child rows after each save, so a
        lot's classification at submit time may differ from what's read
        here at JE-build time. The per-lot field fixes this for new
        Disposals; old ones rely on the backfill patch.
    """
    by_bucket = {
        "Securities Inventory - Trading": 0.0,
        "Long-Term Investments": 0.0,
    }

    for lot in disposal.lots or []:
        if not lot.holding or not lot.qty_consumed:
            continue

        src_class = (lot.get("source_classification") or "").strip()
        if not src_class or src_class == "Unallocated":
            # Legacy fallback — read IH top-level. New Disposals always
            # have source_classification populated by the Sale controller.
            src_class = (
                frappe.db.get_value("Investment Holding", lot.holding, "classification")
                or "Stock in Trade"
            )

        bucket = (
            "Long-Term Investments" if src_class == "Investment"
            else "Securities Inventory - Trading"
        )
        by_bucket[bucket] += flt(lot.cost_basis_amount)

    return {k: v for k, v in by_bucket.items() if v > 0}


def _resolve_account(company: str, logical_name: str, abbr: str) -> Optional[str]:
    """Resolve `<logical> - <abbr>`, falling back to LIKE search if the
    suffix convention differs. Returns None if unresolved."""
    candidate = f"{logical_name} - {abbr}"
    if frappe.db.exists("Account", candidate):
        return candidate
    fallback = frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": ["like", f"{logical_name}%"],
            "disabled": 0,
        },
        "name",
    )
    return fallback


def _resolve_cost_center(disposal) -> Optional[str]:
    """Inherit cost center from the source Sales Invoice's first share-line
    if available; otherwise fall back to a brand-name cost center."""
    if disposal.sales_invoice:
        cc = frappe.db.sql(
            """
            SELECT cost_center
              FROM `tabSales Invoice Item`
             WHERE parent = %s AND cost_center IS NOT NULL AND cost_center != ''
             ORDER BY idx ASC
             LIMIT 1
            """,
            (disposal.sales_invoice,),
            as_list=True,
        )
        if cc and cc[0][0]:
            return cc[0][0]

    return frappe.db.get_value(
        "Cost Center",
        {"cost_center_name": "Polemarch", "company": disposal.company, "is_group": 0},
        "name",
    )


def _existing_je_for_source(source_doctype: str, source_name: str) -> Optional[str]:
    return frappe.db.get_value(
        "Journal Entry",
        {
            "custom_source_doctype": source_doctype,
            "custom_source_name": source_name,
            "docstatus": ["!=", 2],
        },
        "name",
    )


def _je_custom_fields_present() -> bool:
    """Phase-3 patch installs `custom_source_doctype` + `custom_source_name`
    on Journal Entry. Until the patch runs, the auto-JE flow is a no-op."""
    return bool(
        frappe.db.exists("Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_doctype"})
        and frappe.db.exists("Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_name"})
    )
