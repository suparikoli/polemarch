"""Backfill the `custom_portfolio` Custom Field on every existing Investment Holding.

Every legacy Investment Holding is assigned to its company's proprietary
Trading portfolio (`Prop - Trading - {abbr}`), seeded by patch
`seed_portfolios`. Holdings that the admin wants reclassified as
Investment-book can be moved later via the Portfolio Transfer doctype
(Phase 4) or by a one-time bulk SQL update outside this patch.

Idempotent: only writes rows where `custom_portfolio` is blank.
"""

import frappe


def execute():
    holdings = frappe.get_all(
        "Investment Holding",
        fields=["name", "company", "custom_portfolio"],
    )

    abbr_cache = {}
    portfolio_cache = {}
    updated = 0
    missing = []

    for h in holdings:
        if h.custom_portfolio:
            continue
        if not h.company:
            missing.append({"holding": h.name, "reason": "no company"})
            continue

        abbr = abbr_cache.get(h.company)
        if abbr is None:
            abbr = frappe.db.get_value("Company", h.company, "abbr")
            abbr_cache[h.company] = abbr or ""

        if not abbr:
            missing.append({"holding": h.name, "reason": f"company {h.company} has no abbr"})
            continue

        portfolio = portfolio_cache.get((h.company, "Trading"))
        if portfolio is None:
            portfolio = frappe.db.get_value(
                "Portfolio",
                {
                    "company": h.company,
                    "portfolio_type": "Trading",
                    "owner_kind": "Proprietary",
                },
                "name",
            )
            portfolio_cache[(h.company, "Trading")] = portfolio or ""

        if not portfolio:
            missing.append({"holding": h.name, "reason": "no Prop Trading portfolio for company"})
            continue

        frappe.db.set_value(
            "Investment Holding", h.name, "custom_portfolio", portfolio, update_modified=False
        )
        updated += 1

    frappe.db.commit()

    if missing:
        frappe.log_error(
            f"backfill_portfolio_on_holdings: updated={updated}, skipped={len(missing)}. "
            f"First 20 skipped: {missing[:20]}",
            "Polemarch Portfolio Backfill",
        )
