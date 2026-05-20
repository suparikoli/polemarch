"""Operator-runnable install verification.

Usage:
    bench --site <site> execute polemarch.trading.verify_install.execute

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
    "Security", "Portfolio",
    "Wallet", "Wallet Transaction",
    "Security Lot", "Security Lot Ledger Entry", "Security Position",
    "Trade Order", "Trade Order Lot Consumption",
    "Settlement Instruction",
    "Portfolio Transfer", "Portfolio Transfer Lot",
    "Polemarch Audit Log",
    "Polemarch API Idempotency Log", "Polemarch API Log",
    "Settlement Run", "Settlement Run Item",
]

_REQUIRED_JE_CUSTOM_FIELDS = ["custom_source_doctype", "custom_source_name"]
_REQUIRED_ITEM_CUSTOM_FIELDS = ["custom_security"]
_REQUIRED_IH_CUSTOM_FIELDS = ["custom_portfolio"]

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

    # 4) Roles seeded.
    for role in _REQUIRED_ROLES:
        if frappe.db.exists("Role", role):
            ok.append(f"Role present: {role}")
        else:
            errors.append(f"Role MISSING: {role}")

    # 5) Per-company CoA accounts.
    companies = frappe.get_all(
        "Company", filters={"disabled": 0}, fields=["name", "abbr"]
    )
    for company in companies:
        if not company.abbr:
            warnings.append(f"Company {company.name} has no abbr — CoA check skipped")
            continue
        for fragment in _REQUIRED_ACCOUNT_FRAGMENTS:
            candidate = f"{fragment} - {company.abbr}"
            if frappe.db.exists("Account", candidate):
                if verbose:
                    ok.append(f"Account present: {candidate}")
            else:
                # LIKE fallback — operator may have edited names.
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
                        f"Account variant: '{fragment} - {company.abbr}' missing, "
                        f"fallback resolved to '{fallback}'"
                    )
                else:
                    errors.append(f"Account MISSING: {candidate} (no LIKE fallback either)")

    # 6) Polemarch - Non-GST ITT per company.
    for company in companies:
        if not company.abbr:
            continue
        candidate = f"Polemarch - Non-GST - {company.abbr}"
        if frappe.db.exists("Item Tax Template", candidate):
            ok.append(f"ITT present: {candidate}")
        else:
            warnings.append(f"ITT MISSING: {candidate} (Polemarch share items will fail GST routing)")

    # 7) Hourly + daily scheduler entries (smoke check — read hooks at import time).
    try:
        from polemarch import hooks as polemarch_hooks  # noqa: F401
        required_hourly = [
            "polemarch.medusa.reconcile.run_hourly",
            "polemarch.trading.settlement.run_pending",
        ]
        required_daily = [
            "polemarch.trading.audit.verify_wallet_balance_matches_ledger",
            "polemarch.trading.audit.verify_security_position_matches_lots",
            "polemarch.trading.audit.verify_holding_lot_ledger_mirror",
            "polemarch.trading.audit.verify_holding_disposal_chain",
            "polemarch.trading.api._idempotency.purge_expired",
        ]
        hourly = getattr(polemarch_hooks, "scheduler_events", {}).get("hourly", [])
        daily = getattr(polemarch_hooks, "scheduler_events", {}).get("daily", [])
        for entry in required_hourly:
            (ok if entry in hourly else errors).append(f"scheduler.hourly {'present' if entry in hourly else 'MISSING'}: {entry}")
        for entry in required_daily:
            (ok if entry in daily else errors).append(f"scheduler.daily {'present' if entry in daily else 'MISSING'}: {entry}")
    except Exception as exc:
        warnings.append(f"Could not inspect hooks.py: {exc}")

    # 8) Feature flags — report state (informational, not error).
    try:
        from polemarch.trading import feature_flags
        for flag in ("MIRROR_WRITE_SLLE", "AUTO_CREATE_WALLET_ON_POLEMARCH_FLAG",
                     "CREATE_TRADE_ORDER_FROM_MEDUSA", "AUTO_POST_CAPITAL_GAINS_JE",
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
