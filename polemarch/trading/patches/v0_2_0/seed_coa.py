"""Seed Polemarch Chart of Accounts additions for every active Company.

Per-company, idempotent (skip-if-exists). Resolves parent groups by lookup
on (root_type, account_name fragment) — never by hard-coded name — so the
patch works against any standard ERPNext CoA template.
"""

import frappe


# Logical name → (account_type, root_type, account_number, parent_lookup_fragments)
# parent_lookup_fragments is a list of (account_name LIKE, root_type) tuples
# tried in order; first hit wins. Falls back to the root group if all miss.
ACCOUNTS = [
    # Assets
    ("Securities Inventory - Trading",
     "Stock", "Asset", "1410",
     [("Current Assets", "Asset"), ("Stock Assets", "Asset")]),
    ("Long-Term Investments",
     "Investments", "Asset", "1510",
     [("Investments", "Asset"), ("Application of Funds", "Asset")]),
    ("Settlement Receivable",
     "Receivable", "Asset", "1310",
     [("Accounts Receivable", "Asset"), ("Current Assets", "Asset")]),
    ("Trade Suspense - Buy",
     "Temporary", "Asset", "1450",
     [("Current Assets", "Asset")]),
    # Liabilities
    ("Customer Wallet Liability",
     "Payable", "Liability", "2310",
     [("Current Liabilities", "Liability"), ("Source of Funds", "Liability")]),
    ("Settlement Payable",
     "Payable", "Liability", "2330",
     [("Accounts Payable", "Liability"), ("Current Liabilities", "Liability")]),
    ("Trade Suspense - Sell",
     "Temporary", "Liability", "2410",
     [("Current Liabilities", "Liability")]),
    # Income
    ("Trading Revenue - Securities",
     "Income Account", "Income", "4110",
     [("Direct Income", "Income"), ("Income", "Income")]),
    ("Brokerage Revenue",
     "Income Account", "Income", "4310",
     [("Indirect Income", "Income"), ("Income", "Income")]),
    ("Platform Fees Revenue",
     "Income Account", "Income", "4320",
     [("Indirect Income", "Income"), ("Income", "Income")]),
    ("Capital Gains - LT - Realised",
     "Income Account", "Income", "4410",
     [("Indirect Income", "Income"), ("Income", "Income")]),
    ("Capital Gains - ST - Realised",
     "Income Account", "Income", "4420",
     [("Indirect Income", "Income"), ("Income", "Income")]),
    # Expense
    ("Trading COGS - Securities",
     "Cost of Goods Sold", "Expense", "5110",
     [("Cost of Goods Sold", "Expense"), ("Direct Expenses", "Expense"), ("Expenses", "Expense")]),
    ("Capital Loss - LT - Realised",
     "Expense Account", "Expense", "5410",
     [("Indirect Expenses", "Expense"), ("Expenses", "Expense")]),
    ("Capital Loss - ST - Realised",
     "Expense Account", "Expense", "5420",
     [("Indirect Expenses", "Expense"), ("Expenses", "Expense")]),
]


def execute():
    # `Company` doctype has no `disabled` column on Frappe v16 / ERPNext v16 —
    # confirmed via information_schema on test.polemarch.in. Filter without it.
    companies = frappe.get_all(
        "Company",
        fields=["name", "abbr", "default_currency"],
    )
    if not companies:
        frappe.log_error(
            "seed_coa: no active Company found; skipping.",
            "Polemarch CoA Seed",
        )
        return

    for company in companies:
        for logical_name, account_type, root_type, number, parent_fragments in ACCOUNTS:
            _create_account_if_missing(
                company.name,
                company.abbr,
                company.default_currency,
                logical_name,
                account_type,
                root_type,
                number,
                parent_fragments,
            )

    frappe.db.commit()


def _create_account_if_missing(company, abbr, currency, logical_name, account_type, root_type, number, parent_fragments):
    full_name = f"{logical_name} - {abbr}"

    if frappe.db.exists("Account", full_name):
        return

    parent = _resolve_parent(company, root_type, parent_fragments)
    if not parent:
        frappe.log_error(
            f"seed_coa: could not resolve a parent group for {full_name} (root_type={root_type}). "
            f"Tried fragments: {parent_fragments}. Skipped — create the account manually.",
            "Polemarch CoA Seed",
        )
        return

    try:
        doc = frappe.get_doc({
            "doctype": "Account",
            "account_name": logical_name,
            "parent_account": parent,
            "company": company,
            "account_type": _safe_account_type(account_type),
            "root_type": root_type,
            "is_group": 0,
            "account_number": number,
            "account_currency": currency,
        })
        doc.insert(ignore_permissions=True)
    except Exception as exc:
        frappe.log_error(
            f"seed_coa: failed to create {full_name} under parent {parent}: {exc}",
            "Polemarch CoA Seed",
        )


def _resolve_parent(company, root_type, parent_fragments):
    # Try each fragment in order — first hit wins.
    for fragment, frag_root in parent_fragments:
        like = f"%{fragment}%"
        candidate = frappe.db.get_value(
            "Account",
            {
                "company": company,
                "is_group": 1,
                "root_type": frag_root,
                "account_name": ["like", like],
                "disabled": 0,
            },
            "name",
            order_by="lft asc",
        )
        if candidate:
            return candidate

    # Fall back to the root group of the right root_type.
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "is_group": 1,
            "root_type": root_type,
            "parent_account": ["in", ("", None)],
        },
        "name",
    )


def _safe_account_type(account_type):
    # ERPNext only accepts specific account_type strings; guard against custom values.
    # The Account.account_type field is a Select; setting an unknown value throws.
    # Known-safe values cover all our seeds: Stock, Investments, Receivable, Temporary,
    # Payable, Income Account, Cost of Goods Sold, Expense Account.
    allowed = {
        "Stock", "Investments", "Receivable", "Temporary", "Payable",
        "Income Account", "Cost of Goods Sold", "Expense Account",
        "Tax", "Bank", "Cash", "Equity", "Fixed Asset", "Asset Received But Not Billed",
    }
    return account_type if account_type in allowed else ""
