"""End-to-end engine smoke test.

Usage:
    bench --site <site> execute polemarch.polemarch_trading.verify_engines.execute

Creates a temporary fixture (Security, Portfolio, Customer, Wallet, Security
Lot), exercises the wallet + FIFO + position engines, asserts post-conditions,
then cleans up. Returns a dict of `step -> {ok, detail}`.

NO production data is touched. Fixtures are prefixed `SMOKE-` and named
deterministically so re-runs are idempotent (cleanup at start AND end).

Fails fast on the first assertion error; the report shows everything up to
the failure plus the traceback.
"""

from __future__ import annotations

import traceback

import frappe
from frappe.utils import flt, now_datetime


_FIXTURE_PREFIX = "SMOKE-"
_TEST_ISIN = "INE000A01010"  # synthetic ISIN, valid format, doesn't collide
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
        # 0) Pre-clean (idempotent re-run).
        step("00_pre_cleanup", _cleanup)

        # 1) Setup fixture.
        company = step("01_resolve_company", _resolve_company)
        portfolio = step(
            "02_create_proprietary_portfolio",
            lambda: _ensure_portfolio(company, "Trading", "Proprietary", None),
        )
        security = step("03_create_security", _ensure_security)
        customer = step("04_create_customer", lambda: _ensure_customer(company))
        wallet = step("05_resolve_or_create_wallet", lambda: _ensure_wallet(customer, company))

        # 2) Wallet engine — deposit + reverse.
        from polemarch.polemarch_trading import wallet as wallet_engine

        deposit_wt = step("06_wallet_deposit_100k", lambda: wallet_engine.apply_delta(
            wallet=wallet,
            txn_type="Deposit",
            direction="Credit",
            amount=100000,
            reference_doctype=None,
            reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}deposit-1",
            remarks="Smoke test deposit",
        ))
        step("07_assert_balance_100k", lambda: _assert_balance(wallet, available=100000, reserved=0, total=100000))

        # Reservation.
        step("08_wallet_reserve_25k", lambda: wallet_engine.apply_delta(
            wallet=wallet,
            txn_type="Reservation",
            direction="Debit",
            amount=25000,
            reference_doctype=None,
            reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}reserve-1",
            remarks="Smoke test reservation",
        ))
        step("09_assert_balance_after_reserve",
             lambda: _assert_balance(wallet, available=75000, reserved=25000, total=100000))

        # Release via reverse() (the engine's reversal-via-new-row primitive).
        step("10_release_reservation_via_reverse",
             lambda: wallet_engine.reverse(_find_wt(wallet, "Reservation"), remarks="Smoke release"))
        step("11_assert_balance_restored",
             lambda: _assert_balance(wallet, available=100000, reserved=0, total=100000))

        # 3) Idempotency — same key, no double-debit.
        step("12_idempotency_replay_no_double_debit", lambda: wallet_engine.apply_delta(
            wallet=wallet,
            txn_type="Deposit",
            direction="Credit",
            amount=100000,
            reference_doctype=None,
            reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}deposit-1",  # same key as step 06
            remarks="Smoke test deposit (replay)",
        ))
        step("13_assert_no_double_credit",
             lambda: _assert_balance(wallet, available=100000, reserved=0, total=100000))

        # 4) FIFO engine — create two lots at different prices + dates, consume.
        lot_a = step("14_create_lot_A_100units_at_500", lambda: _create_lot(
            security=security, portfolio=portfolio, company=company,
            acq_date="2024-01-01", qty=100, cost=500,
        ))
        lot_b = step("15_create_lot_B_50units_at_600", lambda: _create_lot(
            security=security, portfolio=portfolio, company=company,
            acq_date="2024-06-01", qty=50, cost=600,
        ))

        from polemarch.polemarch_trading import fifo as fifo_engine
        plan = step("16_fifo_consume_120_units", lambda: fifo_engine.consume(
            security=security, portfolio=portfolio, qty_to_sell=120, sale_date="2026-05-20",
        ))
        step("17_assert_fifo_walked_oldest_first", lambda: _assert_fifo_plan(plan, lot_a, lot_b))

        # 5) Final cleanup.
        step("99_post_cleanup", _cleanup)

        summary["ok"] = True
        summary["passed"] = sum(1 for s in steps if s["ok"])
        summary["failed"] = 0

    except Exception:
        summary["ok"] = False
        summary["passed"] = sum(1 for s in steps if s["ok"])
        summary["failed"] = sum(1 for s in steps if not s["ok"])
        # Best-effort cleanup on failure too.
        try:
            _cleanup()
            steps.append({"step": "99_post_cleanup_on_fail", "ok": True})
        except Exception as exc:
            steps.append({"step": "99_post_cleanup_on_fail", "ok": False, "error": str(exc)})

    summary["finished_at"] = str(now_datetime())
    report = {"summary": summary, "steps": steps}

    # Stdout-friendly summary.
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
    # Prefer Mithtech (the operator company) if present; otherwise first
    # non-test-fixture Company.
    for candidate in ("Mithtech Innovative Solutions PVT LTD",):
        if frappe.db.exists("Company", candidate):
            return candidate
    company = frappe.db.get_value(
        "Company", {"name": ["not like", "_T%"]}, "name", order_by="creation ASC"
    )
    if not company:
        raise RuntimeError("No usable Company found.")
    return company


def _ensure_portfolio(company: str, ptype: str, owner_kind: str, customer: str | None) -> str:
    filters = {"company": company, "portfolio_type": ptype, "owner_kind": owner_kind}
    if customer:
        filters["customer"] = customer
    existing = frappe.db.get_value("Portfolio", filters, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Portfolio",
        "portfolio_name": f"{_FIXTURE_PREFIX}{owner_kind} - {ptype} - {company}",
        "portfolio_type": ptype,
        "owner_kind": owner_kind,
        "company": company,
        "customer": customer,
        "status": "Active",
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


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
    doc.flags.from_medusa_sync = True  # bypass the on_update Medusa push
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_wallet(customer: str, company: str) -> str:
    name = f"WAL-{customer}"
    if frappe.db.exists("Wallet", name):
        return name
    abbr = frappe.db.get_value("Company", company, "abbr")
    gl_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Customer Wallet Liability"},
        "name",
    )
    if not gl_account:
        raise RuntimeError(f"Customer Wallet Liability account missing for {company} (abbr={abbr})")
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


def _create_lot(security: str, portfolio: str, company: str, acq_date: str, qty: float, cost: float) -> str:
    doc = frappe.get_doc({
        "doctype": "Security Lot",
        "security": security,
        "portfolio": portfolio,
        "company": company,
        "acquisition_date": acq_date,
        "qty_acquired": qty,
        "cost_basis_per_unit": cost,
        "purchase_reference": "Manual",
        "notes": "Smoke test lot",
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    # Mint an Acquire SLLE so the FIFO scan sees inventory.
    slle = frappe.get_doc({
        "doctype": "Security Lot Ledger Entry",
        "security_lot": doc.name,
        "posting_datetime": now_datetime(),
        "entry_type": "Acquire",
        "qty": qty,
        "cost_basis_per_unit": cost,
        "reference_doctype": "Security Lot",
        "reference_name": doc.name,
    })
    slle.flags.ignore_permissions = True
    slle.insert(ignore_permissions=True)
    slle.submit()
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
        raise AssertionError(
            f"balance_available expected {available}, got {row.balance_available}"
        )
    if flt(row.balance_reserved) != flt(reserved):
        raise AssertionError(
            f"balance_reserved expected {reserved}, got {row.balance_reserved}"
        )
    if flt(row.balance_total) != flt(total):
        raise AssertionError(
            f"balance_total expected {total}, got {row.balance_total}"
        )
    return {"available": flt(row.balance_available), "reserved": flt(row.balance_reserved),
            "total": flt(row.balance_total)}


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


def _assert_fifo_plan(plan, lot_a: str, lot_b: str) -> dict:
    if not plan:
        raise AssertionError("FIFO consume returned empty plan")
    total = sum(p.qty for p in plan)
    if flt(total) != 120:
        raise AssertionError(f"FIFO consumed {total} units, expected 120")
    # Oldest first: lot_a (100 units) should be fully consumed before lot_b touched.
    by_lot = {p.security_lot: p.qty for p in plan}
    if flt(by_lot.get(lot_a, 0)) != 100:
        raise AssertionError(
            f"Expected 100 units from lot_a (oldest), got {by_lot.get(lot_a, 0)}"
        )
    if flt(by_lot.get(lot_b, 0)) != 20:
        raise AssertionError(
            f"Expected 20 units from lot_b (remaining), got {by_lot.get(lot_b, 0)}"
        )
    return {"plan": [(p.security_lot, p.qty) for p in plan]}


# ── cleanup ──────────────────────────────────────────────────────────────


def _cleanup():
    """Drop all SMOKE- fixtures + their dependents. Order matters."""
    # SLLE rows referencing smoke lots/transfers.
    frappe.db.sql("""
        DELETE FROM `tabSecurity Lot Ledger Entry`
         WHERE security_lot IN (SELECT name FROM `tabSecurity Lot` WHERE notes = 'Smoke test lot')
    """)
    # Security Lots from the smoke test.
    frappe.db.sql("DELETE FROM `tabSecurity Lot` WHERE notes = 'Smoke test lot'")
    # Wallet Transactions referencing the smoke wallet.
    frappe.db.sql(
        f"DELETE FROM `tabWallet Transaction` WHERE wallet = %s",
        (f"WAL-{_TEST_CUSTOMER}",),
    )
    # Wallet.
    frappe.db.sql("DELETE FROM `tabWallet` WHERE customer = %s", (_TEST_CUSTOMER,))
    # Customer.
    frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (_TEST_CUSTOMER,))
    # Portfolios prefixed SMOKE-.
    frappe.db.sql(
        "DELETE FROM `tabPortfolio` WHERE portfolio_name LIKE %s",
        (f"{_FIXTURE_PREFIX}%",),
    )
    # Security (only if no Items reference it).
    has_items = frappe.db.exists("Item", {"custom_security": _TEST_ISIN})
    if not has_items:
        frappe.db.sql("DELETE FROM `tabSecurity` WHERE isin = %s", (_TEST_ISIN,))
    # Polemarch API Idempotency Log rows seeded by the smoke test.
    frappe.db.sql(
        "DELETE FROM `tabPolemarch API Idempotency Log` WHERE idempotency_key LIKE %s",
        (f"{_FIXTURE_PREFIX}%",),
    )
    frappe.db.commit()
    return {"cleaned": True}
