"""Seed one Wallet per existing Polemarch customer.

Idempotent. Skipped customers (no resolvable Company / liability account)
are logged via frappe.log_error for manual remediation.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Wallet"):
        return

    customers = frappe.get_all(
        "Customer",
        filters={"custom_is_polemarch_customer": 1, "disabled": 0},
        fields=["name"],
    )

    skipped = []
    created = 0

    for cust in customers:
        wallet_name = f"WAL-{cust.name}"
        if frappe.db.exists("Wallet", wallet_name):
            continue

        company = _default_company()
        if not company:
            skipped.append({"customer": cust.name, "reason": "no Company"})
            continue

        liability = _wallet_liability_account(company)
        if not liability:
            skipped.append(
                {"customer": cust.name, "reason": f"no Wallet Liability account for {company}"}
            )
            continue

        try:
            currency = frappe.db.get_value("Company", company, "default_currency") or "INR"
            doc = frappe.get_doc(
                {
                    "doctype": "Wallet",
                    "customer": cust.name,
                    "company": company,
                    "currency": currency,
                    "gl_liability_account": liability,
                    "status": "Active",
                }
            )
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
            created += 1
        except Exception as exc:
            skipped.append({"customer": cust.name, "reason": str(exc)[:200]})

    frappe.db.commit()

    if skipped:
        frappe.log_error(
            f"seed_wallets_for_polemarch_customers: created={created}, skipped={len(skipped)}. "
            f"First 20: {skipped[:20]}",
            "Polemarch Wallet Seed",
        )


def _default_company():
    return (
        frappe.defaults.get_global_default("company")
        or frappe.db.get_value("Company", {"disabled": 0}, "name")
    )


def _wallet_liability_account(company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    if abbr and frappe.db.exists("Account", f"Customer Wallet Liability - {abbr}"):
        return f"Customer Wallet Liability - {abbr}"
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": ["like", "%Customer Wallet Liability%"],
            "disabled": 0,
        },
        "name",
    )
