"""Operator-runnable install verification.

Usage:
    bench --site <site> execute polemarch.polemarch_trading.verify_install.execute

Prints (and returns) a checklist of the trading subsystem's structural
prerequisites: doctypes, CoA accounts, custom fields, ITT, roles, and
feature flags. Designed to be re-run anytime to confirm a deployment is
healthy.

Returns a dict with three sections:
  - ok:        sorted list of checks that passed
  - warnings:  non-fatal issues (e.g., feature flag OFF — expected)
  - errors:    blocking issues (missing doctype / CoA account / ITT)
"""

import frappe


_REQUIRED_DOCTYPES = [
    "Security",
    "Wallet", "Wallet Transaction",
    "Portfolio Transfer", "Portfolio Transfer Lot",
    "Polemarch Audit Log",
    # Phase 9 — standalone Security purchase + sale doctypes
    "Security Purchase",
    "Security Sale",
    # Phase 11 — Security Type master (Lead Source pattern)
    "Security Type",
    # Phase 15 — Customer Holding (CRM snapshot, not part of GL ledger)
    "Customer Holding",
    # Phase 17 — Standalone Wallet Deposit/Withdrawal (closes the manual-GL gap)
    "Wallet Deposit",
    "Wallet Withdrawal",
]

_REQUIRED_JE_CUSTOM_FIELDS = ["custom_source_doctype", "custom_source_name"]
# `Item.custom_security` is kept for one release so the v0_9_0 Holding /
# Disposal backfills can run on existing sites. Future phases may drop it.
_REQUIRED_ITEM_CUSTOM_FIELDS = ["custom_security"]
# Investment Holding gets:
#   - classification          (Phase 8 — Unallocated / Stock in Trade / Investment)
#   - classification_deadline (Phase 8 — 2-working-day auto-classification timer)
#   - qty_reserved            (Phase 8 — FIFO reservation bucket for open Sell orders)
#   - security                (Phase 9 — standalone trading identity, no Item lookup)
# (custom_portfolio was dropped in v0_14_0; customer was dropped in v0_15_0 —
#  customer holdings now live on the separate Customer Holding doctype.)
_REQUIRED_IH_CUSTOM_FIELDS = [
    "classification",
    "classification_deadline",
    "qty_reserved",
    "security",
]
# Investment Disposal gets:
#   - polemarch_security_sale      (Phase 9 — back-link → Security Sale)
#   - security                     (Phase 9 — standalone trading identity)
# (polemarch_trade_order Custom Field was dropped in v0_13_0 along with the
# Trade Order doctype itself.)
_REQUIRED_ID_CUSTOM_FIELDS = [
    "polemarch_security_sale",
    "security",
]

_REQUIRED_ROLES = [
    "Polemarch Settlement Officer",
    "Polemarch Compliance Officer",
    "Polemarch Trader",
]

_REQUIRED_ACCOUNT_FRAGMENTS = [
    "Securities Inventory - Trading",
    "Long-Term Investments",
    "Customer Wallet Liability",
    "Settlement Payable",
    "Trade Suspense - Buy",
    "Trade Suspense - Sell",
    "Trading Revenue - Securities",
    "Trading COGS - Securities",
    "Capital Gains - LT - Realised",
    "Capital Gains - ST - Realised",
]


def execute(verbose: bool = False) -> dict:
    ok = []
    warnings = []
    errors = []

    # 1) Doctypes present in DB schema.
    for dt in _REQUIRED_DOCTYPES:
        if frappe.db.table_exists(dt):
            ok.append(f"DocType present: {dt}")
        else:
            errors.append(f"DocType MISSING: {dt}")

    # 2) JE custom fields (Phase 3).
    for fn in _REQUIRED_JE_CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": "Journal Entry", "fieldname": fn}):
            ok.append(f"Custom Field present: Journal Entry.{fn}")
        else:
            errors.append(f"Custom Field MISSING: Journal Entry.{fn}")

    # 3) Item / Investment Holding custom fields (Phase 0).
    for fn in _REQUIRED_ITEM_CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": fn}):
            ok.append(f"Custom Field present: Item.{fn}")
        else:
            errors.append(f"Custom Field MISSING: Item.{fn}")
    for fn in _REQUIRED_IH_CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": "Investment Holding", "fieldname": fn}):
            ok.append(f"Custom Field present: Investment Holding.{fn}")
        else:
            errors.append(f"Custom Field MISSING: Investment Holding.{fn}")
    for fn in _REQUIRED_ID_CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": "Investment Disposal", "fieldname": fn}):
            ok.append(f"Custom Field present: Investment Disposal.{fn}")
        else:
            errors.append(f"Custom Field MISSING: Investment Disposal.{fn}")

    # 4) Roles seeded.
    for role in _REQUIRED_ROLES:
        if frappe.db.exists("Role", role):
            ok.append(f"Role present: {role}")
        else:
            errors.append(f"Role MISSING: {role}")

    # 5) Per-company CoA accounts. (Company has no `disabled` column on v16.)
    # ERPNext composes Account.name as `<account_number> - <account_name> - <abbr>`
    # when account_number is set, so the bare `<fragment> - <abbr>` literal
    # rarely matches. Use account_name (not name) as the lookup key.
    companies = frappe.get_all("Company", fields=["name", "abbr"])
    for company in companies:
        if not company.abbr:
            warnings.append(f"Company {company.name} has no abbr — CoA check skipped")
            continue
        for fragment in _REQUIRED_ACCOUNT_FRAGMENTS:
            found = frappe.db.get_value(
                "Account",
                {
                    "company": company.name,
                    "account_name": fragment,
                    "disabled": 0,
                },
                "name",
            )
            if found:
                if verbose:
                    ok.append(f"Account present: {found}")
                continue
            # Account-name didn't match exactly — try LIKE fallback (operator
            # may have renamed). If still nothing, that's a real error.
            fallback = frappe.db.get_value(
                "Account",
                {
                    "company": company.name,
                    "account_name": ["like", f"{fragment}%"],
                    "disabled": 0,
                },
                "name",
            )
            if fallback:
                warnings.append(
                    f"Account renamed: expected account_name='{fragment}' for company "
                    f"{company.abbr}; found similar '{fallback}'"
                )
            else:
                errors.append(
                    f"Account MISSING: account_name='{fragment}' for company {company.abbr}"
                )

    # 6) Polemarch - Non-GST ITT per company.
    for company in companies:
        if not company.abbr:
            continue
        candidate = f"Polemarch - Non-GST - {company.abbr}"
        if frappe.db.exists("Item Tax Template", candidate):
            ok.append(f"ITT present: {candidate}")
        else:
            warnings.append(f"ITT MISSING: {candidate} (Polemarch share items will fail GST routing)")

    # 6b) Phase 21 — Holdings visibility (Report + Number Cards).
    if frappe.db.exists("Report", "Polemarch Holdings by Security"):
        ok.append("Report present: Polemarch Holdings by Security")
    else:
        errors.append("Report MISSING: Polemarch Holdings by Security")
    for card in [
        "Polemarch — Total Holdings Value",
        "Polemarch — Stock in Trade Value",
        "Polemarch — Investment Value",
        "Polemarch — Unclassified Value",
        "Polemarch — Unrealised Gain",
    ]:
        if frappe.db.exists("Number Card", card):
            ok.append(f"Number Card present: {card}")
        else:
            errors.append(f"Number Card MISSING: {card}")

    # 6c) Phase 22 — Security cache columns (in_list_view qty rollups).
    for col in ("qty_sit", "qty_investment", "qty_total", "cost_total"):
        if frappe.db.has_column("Security", col):
            ok.append(f"Column present: Security.{col}")
        else:
            errors.append(f"Column MISSING: Security.{col}")

    # 7) Daily scheduler entries (smoke check — read hooks at import time).
    # The hourly settlement.run_pending scheduler was dropped in v0_13_0
    # along with Trade Order.
    try:
        from polemarch import hooks as polemarch_hooks  # noqa: F401
        required_daily = [
            "polemarch.polemarch_trading.audit.verify_wallet_balance_matches_ledger",
            "polemarch.polemarch_trading.audit.verify_wallet_liability_aggregate_matches_gl",
            "polemarch.polemarch_trading.audit.verify_holding_disposal_chain",
            "polemarch.polemarch_trading.holdings_cache.audit_security_holdings_cache",
        ]
        daily = getattr(polemarch_hooks, "scheduler_events", {}).get("daily", [])
        for entry in required_daily:
            (ok if entry in daily else errors).append(f"scheduler.daily {'present' if entry in daily else 'MISSING'}: {entry}")
    except Exception as exc:
        warnings.append(f"Could not inspect hooks.py: {exc}")

    # 8) Feature flags — report state (informational, not error).
    try:
        from polemarch.polemarch_trading import feature_flags
        for flag in ("AUTO_CREATE_WALLET_ON_POLEMARCH_FLAG",
                     "AUTO_POST_CAPITAL_GAINS_JE",
                     "CUSTOMER_ROLE_SCOPING"):
            state = "ON" if feature_flags.is_enabled(flag) else "OFF"
            (warnings if state == "OFF" else ok).append(f"Feature flag {flag}={state}")
    except Exception as exc:
        warnings.append(f"Could not read feature_flags: {exc}")

    report = {
        "ok": sorted(ok),
        "warnings": sorted(warnings),
        "errors": sorted(errors),
        "summary": {
            "ok_count": len(ok),
            "warning_count": len(warnings),
            "error_count": len(errors),
            "healthy": len(errors) == 0,
        },
    }

    print("\n=== POLEMARCH TRADING — INSTALL VERIFICATION ===\n")
    print(f"OK:       {len(ok)}")
    print(f"WARNINGS: {len(warnings)}")
    print(f"ERRORS:   {len(errors)}")
    print(f"HEALTHY:  {report['summary']['healthy']}")
    if errors:
        print("\n--- ERRORS (blocking) ---")
        for e in report["errors"]:
            print(f"  ✗ {e}")
    if warnings:
        print("\n--- WARNINGS (non-blocking) ---")
        for w in report["warnings"]:
            print(f"  ⚠ {w}")
    if verbose and ok:
        print("\n--- OK ---")
        for o in report["ok"]:
            print(f"  ✓ {o}")
    print()

    return report
