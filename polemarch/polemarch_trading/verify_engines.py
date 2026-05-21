"""End-to-end engine smoke test.

Usage:
    bench --site <site> execute polemarch.polemarch_trading.verify_engines.execute

Creates a temporary SMOKE- fixture (Security, Customer, Wallet, Investment
Holdings), exercises the wallet + FIFO + classification engines, asserts
post-conditions, then cleans up.

NO production data is touched. Fixtures are prefixed `SMOKE-` and named
deterministically so re-runs are idempotent.

Fails fast on the first assertion error; the report shows everything up to
the failure plus the traceback.
"""

from __future__ import annotations

import traceback

import frappe
from frappe.utils import flt, now_datetime


_FIXTURE_PREFIX = "SMOKE-"
_TEST_ISIN = "INE000A01010"
_TEST_CUSTOMER = f"{_FIXTURE_PREFIX}cust-001"


def execute(verbose: bool = False) -> dict:
    """Run the smoke test. Returns {"steps": [...], "ok": True/False, "summary": ...}."""
    steps: list[dict] = []

    def step(name: str, fn):
        try:
            detail = fn()
            steps.append({"step": name, "ok": True, "detail": detail})
            return detail
        except Exception as exc:
            steps.append({
                "step": name,
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            raise

    summary = {"started_at": str(now_datetime())}

    try:
        # 0) Pre-clean.
        step("00_pre_cleanup", _cleanup)

        # 1) Setup fixture.
        company = step("01_resolve_company", _resolve_company)
        security = step("02_create_security", _ensure_security)
        item = step("03_resolve_item_for_security", lambda: _ensure_item_for_security(security))
        customer = step("04_create_customer", lambda: _ensure_customer(company))
        wallet = step("05_resolve_or_create_wallet", lambda: _ensure_wallet(customer, company))

        # 2) Wallet engine smoke.
        from polemarch.polemarch_trading import wallet as wallet_engine

        step("06_wallet_deposit_100k", lambda: wallet_engine.apply_delta(
            wallet=wallet, txn_type="Deposit", direction="Credit", amount=100000,
            reference_doctype=None, reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}deposit-1",
            remarks="Smoke test deposit",
        ))
        step("07_assert_balance_100k",
             lambda: _assert_balance(wallet, available=100000, reserved=0, total=100000))

        step("08_wallet_reserve_25k", lambda: wallet_engine.apply_delta(
            wallet=wallet, txn_type="Reservation", direction="Debit", amount=25000,
            reference_doctype=None, reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}reserve-1",
            remarks="Smoke test reservation",
        ))
        step("09_assert_balance_after_reserve",
             lambda: _assert_balance(wallet, available=75000, reserved=25000, total=100000))

        step("10_release_reservation_via_reverse",
             lambda: wallet_engine.reverse(_find_wt(wallet, "Reservation"), remarks="Smoke release"))
        step("11_assert_balance_restored",
             lambda: _assert_balance(wallet, available=100000, reserved=0, total=100000))

        # 3) Idempotency — same key, no double-debit.
        step("12_idempotency_replay_no_double_debit", lambda: wallet_engine.apply_delta(
            wallet=wallet, txn_type="Deposit", direction="Credit", amount=100000,
            reference_doctype=None, reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}deposit-1",
            remarks="Smoke test deposit (replay)",
        ))
        step("13_assert_no_double_credit",
             lambda: _assert_balance(wallet, available=100000, reserved=0, total=100000))

        # 4) FIFO engine smoke — Investment Holdings, classify, then consume.
        holding_a = step("14_holding_A_100u_at_500_Stock_in_Trade",
                         lambda: _create_holding(item, company, qty=100, cost=500,
                                                 classification="Stock in Trade"))
        holding_b = step("15_holding_B_50u_at_600_Stock_in_Trade",
                         lambda: _create_holding(item, company, qty=50, cost=600,
                                                 classification="Stock in Trade"))

        from polemarch.polemarch_trading import fifo as fifo_engine

        plan = step("16_fifo_consume_120_stock_in_trade", lambda: fifo_engine.consume(
            item=item, company=company, classification="Stock in Trade",
            qty_to_sell=120, sale_date="2026-05-20",
        ))
        step("17_assert_fifo_oldest_first", lambda: _assert_fifo_plan(plan, holding_a, holding_b))

        # 5) Classification engine smoke.
        unalloc = step("18_holding_C_30u_at_700_Unallocated",
                       lambda: _create_holding(item, company, qty=30, cost=700,
                                               classification=None))
        step("19_assert_classification_unallocated",
             lambda: _assert_classification(unalloc, "Unallocated"))

        from polemarch.polemarch_trading import classification as cls_engine

        step("20_classify_as_investment",
             lambda: cls_engine.classify_as_investment(unalloc, actor="Administrator"))
        step("21_assert_classification_investment",
             lambda: _assert_classification(unalloc, "Investment"))

        # 6) Cleanup.
        step("99_post_cleanup", _cleanup)

        summary["ok"] = True
        summary["passed"] = sum(1 for s in steps if s["ok"])
        summary["failed"] = 0

    except Exception:
        summary["ok"] = False
        summary["passed"] = sum(1 for s in steps if s["ok"])
        summary["failed"] = sum(1 for s in steps if not s["ok"])
        try:
            _cleanup()
            steps.append({"step": "99_post_cleanup_on_fail", "ok": True})
        except Exception as exc:
            steps.append({"step": "99_post_cleanup_on_fail", "ok": False, "error": str(exc)})

    summary["finished_at"] = str(now_datetime())
    report = {"summary": summary, "steps": steps}

    print("\n=== POLEMARCH ENGINE SMOKE TEST ===")
    print(f"OK steps:     {summary['passed']}")
    print(f"Failed steps: {summary['failed']}")
    print(f"Overall:      {'PASS ✅' if summary['ok'] else 'FAIL ✗'}")
    if summary["failed"] > 0 or verbose:
        for s in steps:
            mark = "✓" if s["ok"] else "✗"
            line = f"  {mark} {s['step']}"
            if "detail" in s and verbose:
                line += f"   detail={s['detail']}"
            if "error" in s:
                line += f"   error={s['error']}"
            print(line)
    print()

    return report


# ── fixtures ─────────────────────────────────────────────────────────────


def _resolve_company() -> str:
    for candidate in ("Mithtech Innovative Solutions PVT LTD",):
        if frappe.db.exists("Company", candidate):
            return candidate
    company = frappe.db.get_value(
        "Company", {"name": ["not like", "_T%"]}, "name", order_by="creation ASC"
    )
    if not company:
        raise RuntimeError("No usable Company found.")
    return company


def _ensure_security() -> str:
    if frappe.db.exists("Security", _TEST_ISIN):
        return _TEST_ISIN
    doc = frappe.get_doc({
        "doctype": "Security",
        "isin": _TEST_ISIN,
        "security_name": f"{_FIXTURE_PREFIX}Synthetic Equity",
        "company_name": "Smoke Test Issuer Pvt Ltd",
        "security_type": "Equity",
        "tradable": 1, "active": 1,
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_item_for_security(security: str) -> str:
    """Smoke needs an Item to back Investment Holding rows. Use the
    Security's linked Item if present; else create a minimal Polemarch-brand
    Item and link it. """
    item = frappe.db.get_value("Security", security, "item")
    if item and frappe.db.exists("Item", item):
        return item

    # Need an Item Group; reuse Polemarch Securities if seeded.
    item_group = "Polemarch Securities" if frappe.db.exists(
        "Item Group", "Polemarch Securities"
    ) else "All Item Groups"
    if not frappe.db.exists("Item", security):
        item_doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": security,
            "item_name": f"{_FIXTURE_PREFIX}{security}",
            "item_group": item_group,
            "brand": "Polemarch" if frappe.db.exists("Brand", "Polemarch") else None,
            "is_stock_item": 0,
            "stock_uom": "Nos" if frappe.db.exists("UOM", "Nos") else None,
        })
        item_doc.flags.ignore_permissions = True
        item_doc.insert(ignore_permissions=True)
    frappe.db.set_value("Security", security, "item", security, update_modified=False)
    return security


def _ensure_customer(company: str) -> str:
    if frappe.db.exists("Customer", _TEST_CUSTOMER):
        return _TEST_CUSTOMER
    doc = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": _TEST_CUSTOMER,
        "customer_type": "Individual",
        "customer_group": frappe.db.exists("Customer Group", "Polemarch") and "Polemarch" or "All Customer Groups",
        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name", order_by="lft ASC"),
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_wallet(customer: str, company: str) -> str:
    name = f"WAL-{customer}"
    if frappe.db.exists("Wallet", name):
        return name
    gl_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Customer Wallet Liability"},
        "name",
    )
    if not gl_account:
        raise RuntimeError(f"Customer Wallet Liability account missing for {company}")
    doc = frappe.get_doc({
        "doctype": "Wallet",
        "customer": customer,
        "company": company,
        "currency": "INR",
        "gl_liability_account": gl_account,
        "status": "Active",
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _create_holding(item: str, company: str, qty: float, cost: float,
                    classification: str | None = None) -> str:
    """Create an Investment Holding. classification=None lets the
    before_insert hook default to Unallocated (the production behaviour)."""
    fields = {
        "doctype": "Investment Holding",
        "item": item,
        "company": company,
        "acquisition_date": "2024-01-01",
        "qty_acquired": qty,
        "cost_basis_per_unit": cost,
        "purchase_reference": "Manual",
        "notes": "Smoke test holding",
    }
    if classification is not None:
        fields["classification"] = classification
        fields["classified_on"] = now_datetime()
        fields["classified_by"] = "Administrator"
    doc = frappe.get_doc(fields)
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


# ── assertions ───────────────────────────────────────────────────────────


def _assert_balance(wallet: str, available: float, reserved: float, total: float) -> dict:
    row = frappe.db.get_value(
        "Wallet", wallet,
        ("balance_available", "balance_reserved", "balance_total"),
        as_dict=True,
    )
    if not row:
        raise AssertionError(f"Wallet {wallet} not found")
    if flt(row.balance_available) != flt(available):
        raise AssertionError(f"balance_available expected {available}, got {row.balance_available}")
    if flt(row.balance_reserved) != flt(reserved):
        raise AssertionError(f"balance_reserved expected {reserved}, got {row.balance_reserved}")
    if flt(row.balance_total) != flt(total):
        raise AssertionError(f"balance_total expected {total}, got {row.balance_total}")
    return {
        "available": flt(row.balance_available),
        "reserved": flt(row.balance_reserved),
        "total": flt(row.balance_total),
    }


def _find_wt(wallet: str, txn_type: str) -> str:
    name = frappe.db.get_value(
        "Wallet Transaction",
        {"wallet": wallet, "txn_type": txn_type, "is_cancelled": 0, "docstatus": 1},
        "name",
        order_by="posting_datetime DESC",
    )
    if not name:
        raise AssertionError(f"No active Wallet Transaction of type {txn_type} on {wallet}")
    return name


def _assert_fifo_plan(plan, holding_a: str, holding_b: str) -> dict:
    if not plan:
        raise AssertionError("FIFO consume returned empty plan")
    total = sum(p.qty for p in plan)
    if flt(total) != 120:
        raise AssertionError(f"FIFO consumed {total} units, expected 120")
    by_h = {p.holding: p.qty for p in plan}
    if flt(by_h.get(holding_a, 0)) != 100:
        raise AssertionError(f"Expected 100 from holding_a (oldest), got {by_h.get(holding_a, 0)}")
    if flt(by_h.get(holding_b, 0)) != 20:
        raise AssertionError(f"Expected 20 from holding_b, got {by_h.get(holding_b, 0)}")
    return {"plan": [(p.holding, p.qty) for p in plan]}


def _assert_classification(holding_name: str, expected: str) -> dict:
    actual = frappe.db.get_value("Investment Holding", holding_name, "classification")
    if actual != expected:
        raise AssertionError(
            f"Investment Holding {holding_name}: expected classification={expected}, got {actual}"
        )
    return {"classification": actual}


# ── cleanup ──────────────────────────────────────────────────────────────


def _cleanup():
    """Drop all SMOKE- fixtures + their dependents."""
    test_customer = _TEST_CUSTOMER

    # Wallet Transactions referencing the smoke wallet.
    frappe.db.sql(
        "DELETE FROM `tabWallet Transaction` WHERE wallet = %s",
        (f"WAL-{test_customer}",),
    )
    # Wallet.
    frappe.db.sql("DELETE FROM `tabWallet` WHERE customer = %s", (test_customer,))
    # Customer.
    frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (test_customer,))
    # Investment Holdings from smoke.
    frappe.db.sql("DELETE FROM `tabInvestment Holding` WHERE notes = 'Smoke test holding'")
    # Security (only if no Items reference it).
    if not frappe.db.exists("Item", {"custom_security": _TEST_ISIN}):
        frappe.db.sql("DELETE FROM `tabSecurity` WHERE isin = %s", (_TEST_ISIN,))
    # Smoke Item.
    frappe.db.sql(
        "DELETE FROM `tabItem` WHERE item_name LIKE %s",
        (f"{_FIXTURE_PREFIX}%",),
    )
    frappe.db.commit()
    return {"cleaned": True}
