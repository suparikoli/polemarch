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
            # The smoke result is JSON-dumped by bench's `execute` command, but
            # some step returns (e.g. fifo.ConsumedHolding dataclasses) aren't
            # serialisable out of the box. Stringify those for the record so
            # printing the summary never crashes — the caller still gets the
            # live object back as the return value.
            steps.append({"step": name, "ok": True, "detail": _safe_detail(detail)})
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
        customer = step("03_create_customer", lambda: _ensure_customer(company))
        supplier = step("04_create_supplier", _ensure_supplier)
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

        # 4) Security Purchase smoke — mint Holdings via the new doctype.
        sp_a = step(
            "14_security_purchase_A_100u_at_500",
            lambda: _create_security_purchase(security, company, supplier, qty=100, rate=500),
        )
        sp_b = step(
            "15_security_purchase_B_50u_at_600",
            lambda: _create_security_purchase(security, company, supplier, qty=50, rate=600),
        )
        holding_a = step(
            "16_assert_holding_A_minted",
            lambda: _assert_holding_from_purchase(sp_a, expected_qty=100, expected_cost=500),
        )
        holding_b = step(
            "17_assert_holding_B_minted",
            lambda: _assert_holding_from_purchase(sp_b, expected_qty=50, expected_cost=600),
        )
        step(
            "18_assert_purchase_je_posted",
            lambda: _assert_je_for_source("Security Purchase", sp_a),
        )

        # 5) Classification — both Holdings start Unallocated; bulk-classify
        # as Stock in Trade so they're eligible for the FIFO step.
        from polemarch.polemarch_trading import classification as cls_engine

        step("19_assert_holding_A_unallocated",
             lambda: _assert_classification(holding_a, "Unallocated"))
        step("20_force_classify_A_stock_in_trade",
             lambda: _force_classification(holding_a, "Stock in Trade"))
        step("21_force_classify_B_stock_in_trade",
             lambda: _force_classification(holding_b, "Stock in Trade"))

        # 6) FIFO engine smoke — query by security, expect oldest-first.
        from polemarch.polemarch_trading import fifo as fifo_engine

        plan = step("22_fifo_consume_120_by_security", lambda: fifo_engine.consume(
            security=security, company=company, classification="Stock in Trade",
            qty_to_sell=120, sale_date="2026-05-20",
        ))
        step("23_assert_fifo_oldest_first", lambda: _assert_fifo_plan(plan, holding_a, holding_b))

        # 7) Security Sale smoke — sells 80 units, creates Disposal + JEs.
        ss = step(
            "24_security_sale_80u_at_700",
            lambda: _create_security_sale(
                security, company, customer, qty=80, rate=700,
                from_classification="Stock in Trade",
            ),
        )
        step("25_assert_disposal_created", lambda: _assert_disposal_for_sale(ss))
        step("26_assert_revenue_je_posted",
             lambda: _assert_je_for_source("Security Sale", ss))
        step("27_assert_holding_A_qty_disposed_80",
             lambda: _assert_holding_qty_disposed(holding_a, 80))

        # 8) Classification engine smoke — manual Unallocated → Investment.
        sp_c = step(
            "28_security_purchase_C_30u_at_700_unalloc",
            lambda: _create_security_purchase(security, company, supplier, qty=30, rate=700),
        )
        holding_c = step(
            "29_assert_holding_C_minted",
            lambda: _assert_holding_from_purchase(sp_c, expected_qty=30, expected_cost=700),
        )
        step("30_classify_C_as_investment",
             lambda: cls_engine.classify_as_investment(holding_c, actor="Administrator"))
        step("31_assert_classification_investment",
             lambda: _assert_classification(holding_c, "Investment"))

        # 9) Customer-Buy via Security Sale + Customer Wallet — exercises
        # the instant flow that replaced the Trade Order lifecycle in
        # Phase 12 / 13. The customer pays from their Polemarch wallet,
        # Polemarch's Stock-in-Trade is FIFO-consumed, and a new customer
        # Holding mints at the trade price.
        step("32_wallet_topup_50k", lambda: wallet_engine.apply_delta(
            wallet=wallet, txn_type="Deposit", direction="Credit", amount=50000,
            reference_doctype=None, reference_name=None,
            idempotency_key=f"{_FIXTURE_PREFIX}deposit-2",
            remarks="Smoke top-up for Wallet Sale",
        ))
        step("33_assert_balance_150k",
             lambda: _assert_balance(wallet, available=150000, reserved=0, total=150000))

        # Mint 10 more proprietary Stock-in-Trade units to consume.
        sp_d = step(
            "34_security_purchase_D_10u_at_650",
            lambda: _create_security_purchase(security, company, supplier, qty=10, rate=650),
        )
        holding_d = step(
            "35_assert_holding_D_minted",
            lambda: _assert_holding_from_purchase(sp_d, expected_qty=10, expected_cost=650),
        )
        step("36_force_classify_D_stock_in_trade",
             lambda: _force_classification(holding_d, "Stock in Trade"))

        ss_wallet = step(
            "37_security_sale_wallet_10u_at_750",
            lambda: _create_security_sale(
                security, company, customer, qty=10, rate=750,
                from_classification="Stock in Trade",
                party_type="Customer",
                payment_method="Customer Wallet",
            ),
        )
        step("38_assert_wallet_after_sale",
             lambda: _assert_balance(wallet, available=142500, reserved=0, total=142500))
        step("39_assert_sale_disposal_created",
             lambda: _assert_disposal_for_sale(ss_wallet))
        step("40_assert_customer_holding_from_sale",
             lambda: _assert_customer_holding_for_sale(
                 ss_wallet, customer, expected_qty=10, expected_cost=750,
             ))

        # 10) Cleanup.
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


# ── helpers ──────────────────────────────────────────────────────────────


def _safe_detail(value):
    """Best-effort JSON-friendly coercion for step-detail recording.

    Frappe's `json_handler` covers datetimes, decimals, and frappe documents
    but not arbitrary dataclasses (e.g. `fifo.ConsumedHolding`). We walk the
    common containers and fall back to `str(...)` for anything exotic so the
    summary print can't crash on the happy path.
    """
    import dataclasses
    from datetime import date, datetime

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _safe_detail(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_detail(v) for v in value]
    if dataclasses.is_dataclass(value):
        return _safe_detail(dataclasses.asdict(value))
    return str(value)


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


def _lookup_account(company: str, account_name: str):
    """Resolve an Account by account_name (not the composite `name`)."""
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_name": account_name, "disabled": 0},
        "name",
    )


def _ensure_supplier() -> str:
    """Security Purchase requires a Supplier counterparty. Reuse a seeded
    test supplier if present, else mint one keyed off the fixture prefix.
    """
    name = f"{_FIXTURE_PREFIX}supplier-001"
    if frappe.db.exists("Supplier", name):
        return name
    doc = frappe.get_doc({
        "doctype": "Supplier",
        "supplier_name": name,
        "supplier_type": "Individual",
        "supplier_group": frappe.db.get_value(
            "Supplier Group", {"is_group": 0}, "name", order_by="lft ASC"
        ) or "All Supplier Groups",
        "country": frappe.db.get_value("Country", {"name": "India"}, "name") or None,
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


def _create_security_purchase(security: str, company: str, supplier: str,
                              qty: float, rate: float,
                              party_type: str = "Supplier",
                              payment_method: str = "Default Payable") -> str:
    """Submit a Security Purchase. Returns the submitted SP name. Its
    on_submit handler mints the linked Investment Holding + posts the JE.

    party_type defaults to Supplier (proprietary acquisition). Pass
    party_type="Customer" + supplier=<Customer name> to exercise the
    customer-Sell path (FIFO consumes the customer's Holdings, mints
    Polemarch's Stock-in-Trade inventory).
    """
    cost_to = _lookup_account(company, "Securities Inventory - Trading")
    if not cost_to:
        raise RuntimeError(
            f"Securities Inventory - Trading account not found for {company}"
        )
    # The controller's _default_accounts will fill payment_account based on
    # payment_method + party — pass through so the test exercises that path.
    sp = frappe.get_doc({
        "doctype": "Security Purchase",
        "security": security,
        "company": company,
        "posting_date": frappe.utils.today(),
        "party_type": party_type,
        "party": supplier,  # name is "supplier" for back-compat with callers
        "payment_method": payment_method,
        "qty": qty,
        "rate": rate,
        "cost_to_account": cost_to,
        "remarks": f"{_FIXTURE_PREFIX}smoke",
    })
    sp.flags.ignore_permissions = True
    sp.insert(ignore_permissions=True)
    sp.submit()
    return sp.name


def _create_security_sale(security: str, company: str, customer: str,
                          qty: float, rate: float, from_classification: str,
                          party_type: str = "Customer",
                          payment_method: str = "Default Receivable") -> str:
    """Submit a Security Sale. Controller auto-resolves payment_account from
    (payment_method, party). Defaults exercise customer-buy via deferred AR.
    Pass payment_method="Customer Wallet" to exercise the wallet rail."""
    revenue = _lookup_account(company, "Trading Revenue - Securities")
    if not revenue:
        raise RuntimeError(
            f"Trading Revenue - Securities account not found for {company}"
        )
    ss = frappe.get_doc({
        "doctype": "Security Sale",
        "security": security,
        "company": company,
        "posting_date": frappe.utils.today(),
        "party_type": party_type,
        "party": customer,
        "payment_method": payment_method,
        "from_classification": from_classification,
        "qty": qty,
        "rate": rate,
        "revenue_account": revenue,
        "remarks": f"{_FIXTURE_PREFIX}smoke",
    })
    ss.flags.ignore_permissions = True
    ss.insert(ignore_permissions=True)
    ss.submit()
    return ss.name


def _force_classification(holding: str, classification: str) -> dict:
    """Smoke shortcut: skip the 2-working-day timer and slam a Holding
    into the requested classification. Production flow would go through
    polemarch_trading.classification.classify_as_investment or the daily
    auto-classifier."""
    frappe.db.set_value(
        "Investment Holding",
        holding,
        {
            "classification": classification,
            "classified_on": now_datetime(),
            "classified_by": "Administrator",
        },
        update_modified=False,
    )
    frappe.db.commit()
    return {"holding": holding, "classification": classification}


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


def _assert_holding_from_purchase(sp_name: str, expected_qty: float,
                                  expected_cost: float) -> str:
    """Returns the Investment Holding name minted by the given Security Purchase."""
    sp = frappe.db.get_value(
        "Security Purchase", sp_name,
        ("investment_holding_ref", "journal_entry_ref"),
        as_dict=True,
    )
    if not sp or not sp.investment_holding_ref:
        raise AssertionError(f"Security Purchase {sp_name}: no Holding linked back")
    if not sp.journal_entry_ref:
        raise AssertionError(f"Security Purchase {sp_name}: no JE linked back")
    h = frappe.db.get_value(
        "Investment Holding", sp.investment_holding_ref,
        ("qty_acquired", "cost_basis_per_unit", "security"),
        as_dict=True,
    )
    if not h:
        raise AssertionError(f"Holding {sp.investment_holding_ref} disappeared")
    if flt(h.qty_acquired) != flt(expected_qty):
        raise AssertionError(
            f"Holding {sp.investment_holding_ref}: qty {h.qty_acquired} != {expected_qty}"
        )
    if flt(h.cost_basis_per_unit) != flt(expected_cost):
        raise AssertionError(
            f"Holding {sp.investment_holding_ref}: cost {h.cost_basis_per_unit} != {expected_cost}"
        )
    return sp.investment_holding_ref


def _assert_je_for_source(source_doctype: str, source_name: str) -> dict:
    je = frappe.db.get_value(
        "Journal Entry",
        {
            "custom_source_doctype": source_doctype,
            "custom_source_name": source_name,
            "docstatus": 1,
        },
        ("name", "total_debit", "total_credit"),
        as_dict=True,
    )
    if not je:
        raise AssertionError(
            f"No submitted JE found for source ({source_doctype}, {source_name})"
        )
    if flt(je.total_debit) != flt(je.total_credit):
        raise AssertionError(
            f"JE {je.name} unbalanced: DR {je.total_debit} CR {je.total_credit}"
        )
    return {"je": je.name, "debit": flt(je.total_debit), "credit": flt(je.total_credit)}


def _assert_disposal_for_sale(ss_name: str) -> dict:
    ss = frappe.db.get_value(
        "Security Sale", ss_name,
        ("investment_disposal_ref", "revenue_journal_entry_ref", "cogs_journal_entry_ref"),
        as_dict=True,
    )
    if not ss or not ss.investment_disposal_ref:
        raise AssertionError(f"Security Sale {ss_name}: no Disposal linked back")
    disposal = frappe.db.get_value(
        "Investment Disposal", ss.investment_disposal_ref,
        ("docstatus", "total_qty_sold", "polemarch_security_sale"),
        as_dict=True,
    )
    if not disposal:
        raise AssertionError(f"Disposal {ss.investment_disposal_ref} not found")
    if disposal.docstatus != 1:
        raise AssertionError(
            f"Disposal {ss.investment_disposal_ref} not submitted (docstatus={disposal.docstatus})"
        )
    if disposal.polemarch_security_sale != ss_name:
        raise AssertionError(
            f"Disposal {ss.investment_disposal_ref}: back-link mismatch "
            f"({disposal.polemarch_security_sale} != {ss_name})"
        )
    return {
        "disposal": ss.investment_disposal_ref,
        "revenue_je": ss.revenue_journal_entry_ref,
        "cogs_je": ss.cogs_journal_entry_ref,
        "qty_sold": flt(disposal.total_qty_sold),
    }


def _assert_holding_qty_disposed(holding: str, expected: float) -> dict:
    actual = frappe.db.get_value("Investment Holding", holding, "qty_disposed")
    if flt(actual) != flt(expected):
        raise AssertionError(
            f"Holding {holding}: qty_disposed {actual} != {expected}"
        )
    return {"holding": holding, "qty_disposed": flt(actual)}


def _assert_customer_holding_for_sale(sale_name: str, customer: str,
                                      expected_qty: float, expected_cost: float) -> dict:
    """Verify Security Sale.on_submit bumped the Customer Holding snapshot.

    Phase 15: customer holdings are CRM data on the standalone Customer
    Holding doctype, not part of the inventory ledger. expected_cost is
    accepted for API compatibility but ignored — Polemarch doesn't track
    customer cost basis.
    """
    security = frappe.db.get_value("Security Sale", sale_name, "security")
    if not security:
        raise AssertionError(f"Security Sale {sale_name} not found")
    ch_name = f"{customer}-{security}"
    row = frappe.db.get_value(
        "Customer Holding",
        ch_name,
        ("name", "qty", "source", "security"),
        as_dict=True,
    )
    if not row:
        raise AssertionError(
            f"No Customer Holding snapshot for {customer} / {security} "
            f"(expected after Security Sale {sale_name})"
        )
    if flt(row.qty) < flt(expected_qty):
        raise AssertionError(
            f"Customer Holding {row.name}: qty {row.qty} < expected {expected_qty} "
            f"(snapshot should reflect the sold qty)"
        )
    if row.source != "Security Sale":
        raise AssertionError(
            f"Customer Holding {row.name}: source {row.source} != Security Sale"
        )
    return {
        "customer_holding": row.name,
        "security": row.security,
        "qty": flt(row.qty),
        "source": row.source,
    }


# ── cleanup ──────────────────────────────────────────────────────────────


def _cleanup():
    """Drop all SMOKE- fixtures + their dependents.

    Cancels first (rather than delete-cascade-style) so any GL Entries
    posted under a JE that touched a real company account reverse cleanly.
    Then deletes the fixture rows + the JEs / Disposals they spawned.
    """
    test_customer = _TEST_CUSTOMER
    test_supplier = f"{_FIXTURE_PREFIX}supplier-001"

    # --- Cancel + delete Security Sales (cancels Disposal + JEs via on_cancel)
    ss_names = [
        r[0] for r in frappe.db.sql(
            "SELECT name FROM `tabSecurity Sale` WHERE remarks = %s",
            (f"{_FIXTURE_PREFIX}smoke",),
        )
    ]
    for name in ss_names:
        doc = frappe.get_doc("Security Sale", name)
        if doc.docstatus == 1:
            try:
                doc.flags.ignore_permissions = True
                doc.cancel()
            except Exception:
                pass
        frappe.delete_doc("Security Sale", name, force=True, ignore_permissions=True)

    # --- Cancel + delete Security Purchases (cancels JE + Holding via on_cancel)
    sp_names = [
        r[0] for r in frappe.db.sql(
            "SELECT name FROM `tabSecurity Purchase` WHERE remarks = %s",
            (f"{_FIXTURE_PREFIX}smoke",),
        )
    ]
    for name in sp_names:
        doc = frappe.get_doc("Security Purchase", name)
        if doc.docstatus == 1:
            try:
                doc.flags.ignore_permissions = True
                doc.cancel()
            except Exception:
                pass
        frappe.delete_doc("Security Purchase", name, force=True, ignore_permissions=True)

    # --- Belt-and-braces: any leftover Disposals / JEs / Holdings keyed back
    #     to the smoke fixtures, e.g. from a previous failed run.
    frappe.db.sql(
        "DELETE FROM `tabInvestment Disposal Lot` "
        "WHERE parent IN (SELECT name FROM (SELECT name FROM `tabInvestment Disposal` "
        "                                    WHERE customer = %s) AS x)",
        (test_customer,),
    )
    frappe.db.sql(
        "DELETE FROM `tabInvestment Disposal` WHERE customer = %s",
        (test_customer,),
    )
    frappe.db.sql(
        "DELETE FROM `tabJournal Entry Account` "
        "WHERE parent IN (SELECT name FROM (SELECT name FROM `tabJournal Entry` "
        "                                    WHERE user_remark LIKE %s) AS x)",
        (f"%{_FIXTURE_PREFIX}smoke%",),
    )
    frappe.db.sql(
        "DELETE FROM `tabJournal Entry` WHERE user_remark LIKE %s",
        (f"%{_FIXTURE_PREFIX}smoke%",),
    )
    frappe.db.sql(
        "DELETE FROM `tabInvestment Holding` "
        "WHERE notes LIKE %s OR purchase_reference_link IN (%s) OR security = %s",
        (f"%{_FIXTURE_PREFIX}%", "", _TEST_ISIN),
    )

    # --- Wallet + Customer + Supplier
    frappe.db.sql(
        "DELETE FROM `tabWallet Transaction` WHERE wallet = %s",
        (f"WAL-{test_customer}",),
    )
    frappe.db.sql("DELETE FROM `tabWallet` WHERE customer = %s", (test_customer,))
    frappe.db.sql(
        "DELETE FROM `tabCustomer Holding` WHERE customer = %s",
        (test_customer,),
    )
    frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (test_customer,))
    frappe.db.sql("DELETE FROM `tabSupplier` WHERE name = %s", (test_supplier,))

    # --- Security itself (no Item linked back since Phase 9 dropped Security.item)
    frappe.db.sql("DELETE FROM `tabSecurity` WHERE isin = %s", (_TEST_ISIN,))

    frappe.db.commit()
    return {"cleaned": True}
