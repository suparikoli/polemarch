import frappe

from polemarch.install import POLEMARCH_CUSTOMER_GROUP


def validate(doc, method=None):
    has_dp = bool(doc.get("custom_dp_details"))
    in_polemarch_group = doc.customer_group == POLEMARCH_CUSTOMER_GROUP
    doc.custom_is_polemarch_customer = 1 if (has_dp or in_polemarch_group) else 0


def on_update(doc, method=None):
    if doc.custom_is_polemarch_customer:
        # Medusa-side plugin owns Frappe→Medusa sync via REST API. Frappe no
        # longer pushes customer updates outbound; if Medusa needs to know
        # about a KYC status change, it'll either re-fetch on its own
        # schedule or rely on a Frappe-API-key-protected webhook on the
        # Medusa side (out of scope for this app).
        _maybe_ensure_wallet(doc)


def _maybe_ensure_wallet(doc):
    """Phase 1: when a Customer is (or becomes) a Polemarch customer, ensure
    a Wallet exists. Gated by feature flag and skipped silently if Wallet
    doctype is not yet migrated (pre-Phase-1 deployments)."""
    try:
        from polemarch.polemarch_trading.feature_flags import is_enabled

        if not is_enabled("AUTO_CREATE_WALLET_ON_POLEMARCH_FLAG"):
            return
        if not frappe.db.table_exists("Wallet"):
            return

        wallet_name = f"WAL-{doc.name}"
        if frappe.db.exists("Wallet", wallet_name):
            return

        company = _resolve_default_company()
        if not company:
            return

        liability_account = _resolve_wallet_liability_account(company)
        if not liability_account:
            frappe.log_error(
                f"Wallet auto-create for {doc.name} skipped: "
                f"Customer Wallet Liability account missing for company {company}.",
                "Polemarch Wallet Bootstrap",
            )
            return

        wallet = frappe.get_doc(
            {
                "doctype": "Wallet",
                "customer": doc.name,
                "company": company,
                "currency": frappe.db.get_value("Company", company, "default_currency") or "INR",
                "gl_liability_account": liability_account,
                "status": "Active",
            }
        )
        wallet.flags.ignore_permissions = True
        wallet.insert(ignore_permissions=True)
    except Exception:
        # Never block customer save on Wallet bootstrap failure.
        frappe.log_error(frappe.get_traceback(), "Polemarch Wallet Bootstrap")


def _resolve_default_company():
    # Prefer the global default Company; fall back to the first active one.
    default = frappe.defaults.get_global_default("company")
    if default:
        return default
    row = frappe.db.get_value("Company", {"disabled": 0}, "name")
    return row


def _resolve_wallet_liability_account(company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    if not abbr:
        return None
    candidate = f"Customer Wallet Liability - {abbr}"
    if frappe.db.exists("Account", candidate):
        return candidate
    # Forward-compat: tolerate variations from CoA-seed patch edits.
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": ["like", "%Customer Wallet Liability%"],
            "disabled": 0,
        },
        "name",
    )
