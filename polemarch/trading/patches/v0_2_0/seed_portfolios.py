"""Seed proprietary Portfolios for every active Company.

Creates two rows per company:
  - "Prop - Trading - {abbr}"   (owner_kind=Proprietary, portfolio_type=Trading)
  - "Prop - Investment - {abbr}" (owner_kind=Proprietary, portfolio_type=Investment)

Customer-owned portfolios are created on-demand by the Customer on_update hook
(Phase 1) when custom_is_polemarch_customer flips to 1.
"""

import frappe


def execute():
    # `Company` doctype has no `disabled` column on Frappe v16; omit filter.
    companies = frappe.get_all(
        "Company",
        fields=["name", "abbr"],
    )

    for company in companies:
        for portfolio_type in ("Trading", "Investment"):
            _create_if_missing(company.name, company.abbr, portfolio_type)

    frappe.db.commit()


def _create_if_missing(company, abbr, portfolio_type):
    portfolio_name = f"Prop - {portfolio_type} - {abbr}"

    existing = frappe.db.exists(
        "Portfolio",
        {
            "company": company,
            "portfolio_type": portfolio_type,
            "owner_kind": "Proprietary",
        },
    )
    if existing:
        return

    try:
        doc = frappe.get_doc({
            "doctype": "Portfolio",
            "portfolio_name": portfolio_name,
            "portfolio_type": portfolio_type,
            "owner_kind": "Proprietary",
            "company": company,
            "status": "Active",
            "description": (
                f"Polemarch's own {portfolio_type.lower()} book for {company}. "
                f"Auto-seeded by patch v0_2_0.seed_portfolios."
            ),
        })
        doc.insert(ignore_permissions=True)
    except Exception as exc:
        frappe.log_error(
            f"seed_portfolios: failed to create {portfolio_name}: {exc}",
            "Polemarch Portfolio Seed",
        )
