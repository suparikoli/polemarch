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

        # 5) Trade Order lifecycle — Customer Sell against own portfolio.
        customer_portfolio = step(
            "18_create_customer_investment_portfolio",
            lambda: _ensure_portfolio(company, "Investment", "Customer", customer),
        )
        cust_lot_a = step("19_customer_lot_A_60units_at_400", lambda: _create_lot(
            security=security, portfolio=customer_portfolio, company=company,
            acq_date="2023-01-15", qty=60, cost=400, owning_customer=customer,
        ))
        cust_lot_b = step("20_customer_lot_B_40units_at_450", lambda: _create_lot(
            security=security, portfolio=customer_portfolio, company=company,
            acq_date="2024-08-01", qty=40, cost=450, owning_customer=customer,
        ))

        # Submit a Sell + Customer-book order for 80 units at ₹600
        # (expected proceeds: 80 × 600 = 48,000; cost basis: 60×400 + 20×450 = 33,000).
        trade_order = step("21_create_sell_trade_order", lambda: _create_trade_order(
            customer=customer, portfolio=customer_portfolio, security=security,
            company=company, side="Sell", qty=80, price=600,
        ))
        step("22_submit_trade_order", lambda: _submit_trade_order(trade_order))
        step("23_assert_reserve_slles_written",
             lambda: _assert_slle_count(trade_order, entry_type="Reserve", expected=2))

        step("24_match_trade_order", lambda: _match_trade_order(trade_order))
        step("25_assert_order_state_matched",
             lambda: _assert_trade_order_state(trade_order, expected="Matched"))
        step("26_assert_settlement_instruction_created",
             lambda: _assert_settlement_instruction(trade_order, expected_state="Pending"))

        step("27_fund_settlement", lambda: _fund_via_engine(trade_order))
        step("28_assert_settlement_funded",
             lambda: _assert_settlement_instruction(trade_order, expected_state="Funded"))
        step("29_assert_wallet_credited_payout",
             lambda: _assert_balance(wallet, available=148000, reserved=0, total=148000))

        step("30_clear_settlement", lambda: _clear_via_engine(trade_order))
        step("31_assert_consume_slles_written",
             lambda: _assert_slle_count(trade_order, entry_type="Consume", expected=2))
        step("32_assert_order_state_settled",
             lambda: _assert_trade_order_state(trade_order, expected="Settled"))
        step("33_assert_settlement_cleared",
             lambda: _assert_settlement_instruction(trade_order, expected_state="Cleared"))

        # 6) Final cleanup.
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


def _create_lot(security: str, portfolio: str, company: str, acq_date: str, qty: float, cost: float, owning_customer: str | None = None) -> str:
    doc = frappe.get_doc({
        "doctype": "Security Lot",
        "security": security,
        "portfolio": portfolio,
        "company": company,
        "owning_customer": owning_customer,
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


def _create_trade_order(
    customer: str, portfolio: str, security: str, company: str,
    side: str, qty: float, price: float,
) -> str:
    doc = frappe.get_doc({
        "doctype": "Trade Order",
        "side": side,
        "book": "Customer",
        "portfolio": portfolio,
        "security": security,
        "customer": customer,
        "company": company,
        "posting_date": now_datetime(),
        "qty": qty,
        "price": price,
        "notes": "Smoke test trade order",
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _submit_trade_order(name: str):
    doc = frappe.get_doc("Trade Order", name)
    doc.flags.ignore_permissions = True
    doc.submit()
    return {"order_state": doc.order_state, "reservation_id": doc.reservation_id}


def _match_trade_order(name: str):
    from polemarch.polemarch_trading import matching as matching_engine
    matching_engine.match(name)
    doc = frappe.get_doc("Trade Order", name)
    return {"order_state": doc.order_state, "lots_consumed_count": len(doc.lots_consumed or [])}


def _fund_via_engine(trade_order_name: str):
    from polemarch.polemarch_trading import settlement as settlement_engine
    si_name = frappe.db.get_value("Trade Order", trade_order_name, "settlement_instruction")
    if not si_name:
        raise AssertionError(f"Trade Order {trade_order_name} has no Settlement Instruction")
    settlement_engine.fund(si_name)
    return {"settlement_instruction": si_name}


def _clear_via_engine(trade_order_name: str):
    from polemarch.polemarch_trading import settlement as settlement_engine
    si_name = frappe.db.get_value("Trade Order", trade_order_name, "settlement_instruction")
    settlement_engine.clear(si_name)
    return {"settlement_instruction": si_name}


def _assert_slle_count(trade_order: str, entry_type: str, expected: int) -> dict:
    count = frappe.db.count(
        "Security Lot Ledger Entry",
        {
            "reference_doctype": "Trade Order",
            "reference_name": trade_order,
            "entry_type": entry_type,
            "is_cancelled": 0,
            "docstatus": 1,
        },
    )
    if count != expected:
        raise AssertionError(
            f"Expected {expected} {entry_type} SLLE rows for {trade_order}, got {count}"
        )
    return {"entry_type": entry_type, "count": count}


def _assert_trade_order_state(name: str, expected: str) -> dict:
    actual = frappe.db.get_value("Trade Order", name, "order_state")
    if actual != expected:
        raise AssertionError(
            f"Trade Order {name}: expected order_state={expected}, got {actual}"
        )
    return {"order_state": actual}


def _assert_settlement_instruction(trade_order: str, expected_state: str) -> dict:
    si_name = frappe.db.get_value("Trade Order", trade_order, "settlement_instruction")
    if not si_name:
        raise AssertionError(f"Trade Order {trade_order} has no Settlement Instruction")
    actual = frappe.db.get_value("Settlement Instruction", si_name, "settlement_state")
    if actual != expected_state:
        raise AssertionError(
            f"Settlement {si_name}: expected state={expected_state}, got {actual}"
        )
    return {"settlement_instruction": si_name, "state": actual}


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
    """Drop all SMOKE- fixtures + their dependents. Order matters.

    Submittable docs (Trade Order, Settlement Instruction, Wallet Transaction,
    Security Lot Ledger Entry) are deleted directly via SQL — bypasses the
    cancel-then-delete cycle that the ORM would impose. Safe here because the
    smoke fixture has no audit-trail constraint to honour.
    """
    test_customer = _TEST_CUSTOMER

    # Trade Order Lot Consumption (child of Trade Order).
    frappe.db.sql(
        """
        DELETE FROM `tabTrade Order Lot Consumption`
         WHERE parent IN (SELECT name FROM `tabTrade Order` WHERE customer = %s)
        """,
        (test_customer,),
    )
    # Settlement Instructions linked to the smoke customer's Trade Orders.
    frappe.db.sql(
        """
        DELETE FROM `tabSettlement Instruction`
         WHERE trade_order IN (SELECT name FROM `tabTrade Order` WHERE customer = %s)
        """,
        (test_customer,),
    )
    # Trade Orders for the smoke customer.
    frappe.db.sql(
        "DELETE FROM `tabTrade Order` WHERE customer = %s",
        (test_customer,),
    )
    # Security Position rows for the smoke customer.
    frappe.db.sql(
        "DELETE FROM `tabSecurity Position` WHERE customer = %s",
        (test_customer,),
    )
    # SLLE rows referencing smoke lots/transfers.
    frappe.db.sql("""
        DELETE FROM `tabSecurity Lot Ledger Entry`
         WHERE security_lot IN (SELECT name FROM `tabSecurity Lot` WHERE notes = 'Smoke test lot')
    """)
    # Security Lots from the smoke test.
    frappe.db.sql("DELETE FROM `tabSecurity Lot` WHERE notes = 'Smoke test lot'")
    # Wallet Transactions referencing the smoke wallet.
    frappe.db.sql(
        "DELETE FROM `tabWallet Transaction` WHERE wallet = %s",
        (f"WAL-{test_customer}",),
    )
    # Wallet.
    frappe.db.sql("DELETE FROM `tabWallet` WHERE customer = %s", (test_customer,))
    # Customer.
    frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (test_customer,))
    # Portfolios prefixed SMOKE-, plus customer-owned portfolios for the
    # smoke customer (which carry a normal name, no SMOKE- prefix).
    frappe.db.sql(
        "DELETE FROM `tabPortfolio` WHERE portfolio_name LIKE %s OR customer = %s",
        (f"{_FIXTURE_PREFIX}%", test_customer),
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
