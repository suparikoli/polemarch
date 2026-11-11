app_name = "polemarch"
app_title = "Polemarch Customizations"
app_publisher = "Mithtech Innovative Solutions PVT LTD"
app_description = "All customizations needed for polemarch"
app_email = "manoj311093@gmail.com"
app_license = "mit"

# The customer wallet (Wallet, Wallet Deposit, Wallet Withdrawal, Wallet
# Transaction) and the line of credit live in the generic `framith_wallet` app;
# polemarch only adds its own rules through the customer_wallet_* hooks below.
required_apps = ["framith_wallet"]

after_install = "polemarch.install.after_install"
after_migrate = "polemarch.install.after_migrate"

app_logo_url = "/assets/polemarch/images/polemarch-favicon.png"
website_context = {
    "favicon": "/assets/polemarch/images/polemarch-favicon.png",
    "splash_image": "/assets/polemarch/images/polemarch-favicon.png",
}

doctype_js = {
    "Customer": "public/js/customer.js",
    "Sales Invoice": "public/js/sales_invoice.js",
}

doctype_list_js = {
    "Customer": "public/js/customer_list.js",
}

doc_events = {
    "Customer": {
        "before_insert": "polemarch.overrides.crm.customer_before_insert",
        "validate": "polemarch.overrides.customer.validate",
        "on_update": "polemarch.overrides.customer.on_update",
        # Phase 18 — auto-create a placeholder Contact for every new Customer
        # so the Phase-16 Contact-first model isn't bypassed by API / scripts.
        "after_insert": "polemarch.overrides.customer.after_insert",
    },
    # NOTE: the Item `validate` override (item_group forcing + Non-GST ITT
    # auto-linking) was removed — shares are now modelled by the `Security`
    # doctype, not brand=Polemarch ERPNext Items, and GST is mapped to Items
    # + taxes natively in ERPNext rather than force-injected by this app.
    "Sales Invoice": {
        "validate": "polemarch.overrides.sales_invoice.validate",
        "on_submit": "polemarch.overrides.sales_invoice.on_submit",
        "on_cancel": "polemarch.overrides.sales_invoice.on_cancel",
    },
    "Sales Order": {
        "validate": "polemarch.overrides.sales_order.validate",
    },
    # Mithtech-only tick follows the CRM funnel (see overrides/crm.py).
    "Opportunity": {"before_insert": "polemarch.overrides.crm.opportunity_before_insert"},
    "Prospect": {"before_insert": "polemarch.overrides.crm.prospect_before_insert"},
    # Phase 2 — proprietary share purchases route cost to Securities
    # Inventory / Long-Term Investments and mint Investment Holdings (one
    # per PI line) in `Unallocated` classification. Validates the expense
    # account BEFORE GL posting.
    "Purchase Invoice": {
        "validate": "polemarch.polemarch_trading.hooks.purchase_invoice_validate",
        "on_submit": "polemarch.polemarch_trading.hooks.purchase_invoice_on_submit",
        "on_cancel": "polemarch.polemarch_trading.hooks.purchase_invoice_on_cancel",
    },
    # Phase 22 — keep the Security.qty_sit / qty_investment / qty_total /
    # cost_total cache fields fresh whenever an Investment Holding changes.
    # The cache backs the at-a-glance columns on the Security list view.
    "Investment Holding": {
        "after_insert": "polemarch.polemarch_trading.holdings_cache.on_investment_holding_change",
        "on_update":    "polemarch.polemarch_trading.holdings_cache.on_investment_holding_change",
        "on_trash":     "polemarch.polemarch_trading.holdings_cache.on_investment_holding_change",
    },
}

scheduler_events = {
    "daily": [
        # Reconciliation jobs — each detects mismatches and logs them;
        # none of them auto-heal. The two wallet audits run from framith_wallet
        # and report into Polemarch Audit Log via the customer_wallet_audit_log hook.
        "polemarch.polemarch_trading.audit.verify_holding_disposal_chain",
        # Phase 22 — recompute Security.qty_sit/qty_investment/cost_total
        # cache and log any drift. Self-healing (re-persists the truth).
        "polemarch.polemarch_trading.holdings_cache.audit_security_holdings_cache",
        # Phase 26 — Securities Inventory + Long-Term Investments GL balance
        # vs sum of open Investment Holdings (per classification). Detects
        # the kind of orphan-JE drift that accumulated until the manual
        # cleanup in Phase 26. Does NOT auto-heal.
        "polemarch.polemarch_trading.audit.verify_inventory_account_matches_holdings",
    ],
}


# Phase 5 — customer-role scoping for trading doctypes.
# Each entry is gated by the `CUSTOMER_ROLE_SCOPING` feature flag inside the
# permissions module; with the flag OFF every fragment returns "" / True so
# the standard role permissions take over. Flip the flag in site_config.json
# once mappings between Users and Customers are verified.
permission_query_conditions = {
    "Investment Disposal":   "polemarch.polemarch_trading.permissions.investment_disposal_perm_query",
    "Portfolio Transfer":    "polemarch.polemarch_trading.permissions.portfolio_transfer_perm_query",
}

has_permission = {
    "Investment Disposal":   "polemarch.polemarch_trading.permissions.investment_disposal_has_permission",
    "Portfolio Transfer":    "polemarch.polemarch_trading.permissions.portfolio_transfer_has_permission",
}

fixtures = [
    {"doctype": "Customer Group", "filters": [["name", "=", "Polemarch"]]},
]

# ── framith_wallet extension points ──────────────────────────────────────
# Polemarch-specific wallet rules stay here; the wallet itself is generic.
customer_wallet_operator_roles = ["Polemarch Trader", "Polemarch Settlement Officer"]
customer_wallet_customer_filter = ["polemarch.overrides.customer.wallet_customer_filter"]
customer_wallet_audit_log = ["polemarch.polemarch_trading.audit.log_wallet_audit"]
