"""Tests for polemarch.api.storefront (the Medusa integration).

Run against a TEST site only (it submits and cancels accounting documents):
    bench --site fixerp.localhost execute \
      polemarch.polemarch_trading.tests.test_storefront.run

Needs customer wallets enabled on the default company. Reuses the TEST-
Security scaffolding of test_classification_and_fifo.
"""

import frappe
from frappe.utils import flt

from polemarch.api import storefront as sf
from polemarch.polemarch_trading.tests import test_classification_and_fifo as base

MID = "cus_TEST_storefront_1"
EMAIL = "test-storefront-1@polemarch.test"
CUSTOMER = {"medusa_customer_id": MID, "name": "TEST Storefront Buyer", "email": EMAIL, "phone": "9000000001"}
_results = []


def _check(name, got, want):
    _results.append((got == want, name, got, want))


def _bal(customer):
    w = frappe.db.get_value("Wallet", f"WAL-{customer}", ["balance_available", "balance_reserved", "balance_total"], as_dict=True)
    return (flt(w.balance_available), flt(w.balance_reserved), flt(w.balance_total))


def _order(oid, lines, debit):
    return {"id": oid, "display_id": oid[-3:], "funded_at": "2026-02-02T04:30:00Z", "wallet_debit": debit,
            "customer": CUSTOMER, "lines": [{"id": f"{oid}-l{i}", "isin": base.ISIN, "title": "TEST Security",
                                              "qty": q, "unit_price": r} for i, (q, r) in enumerate(lines)]}


def _txn(tid, direction, kind, amount, **kw):
    return dict({"id": tid, "customer": CUSTOMER, "direction": direction, "kind": kind, "bucket": "main",
                 "amount": amount, "created_at": "2026-02-01T05:00:00Z"}, **kw)


def run():
    if frappe.local.site == "e16.mith.tech":
        frappe.throw("test_storefront submits and cancels accounting documents: never run it on e16.")
    _teardown()
    base._teardown(quiet=True)
    base._setup()
    base._buy(1000, 5.0, "Stock in Trade", "2026-01-01")

    # ── customer + wallet ──
    # Pre-create the Customer: test sites carry extra mandatory fields.
    d = frappe.get_doc({"doctype": "Customer", "customer_name": CUSTOMER["name"], "medusa_customer_id": MID,
                        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
                        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name")})
    base._fill_mandatory_from_existing(d)
    d.insert(ignore_permissions=True)
    cust = sf.ensure_customer(CUSTOMER)
    _check("customer keyed on medusa id", frappe.db.get_value("Customer", {"medusa_customer_id": MID}, "name"), cust)
    _check("same customer on retry", sf.ensure_customer(CUSTOMER), cust)
    _check("wallet created", bool(frappe.db.exists("Wallet", f"WAL-{cust}")), True)

    # ── wallet mirror ──
    sf.wallet_movement(_txn("wt_T1", "credit", "vba_credit", 10000))
    sf.wallet_movement(_txn("wt_T1", "credit", "vba_credit", 10000))
    _check("top-up mirrored once", _bal(cust), (10000.0, 0.0, 10000.0))
    sf.wallet_movement(_txn("wt_T2", "debit", "payin_fee_debit", 35.4))
    _check("pay-in fee mirrored as Fee", frappe.db.get_value("Wallet Transaction", {"medusa_id": "wt_T2"}, "txn_type"), "Fee")
    sf.wallet_movement(_txn("wt_T3", "credit", "referral_credit", 500, bucket="promo"))
    _check("promo credit mirrored", _bal(cust), (10464.6, 0.0, 10464.6))
    _check("no Journal Entry for mirrored movements",
           frappe.db.count("Wallet Transaction", {"medusa_id": ["in", ["wt_T1", "wt_T2", "wt_T3"]], "journal_entry": ["is", "set"]}), 0)
    _check("live order_debit skipped", sf.wallet_movement(_txn("wt_T4", "debit", "order_debit", 1060, reference_type="cart")),
           {"skipped": "order lifecycle"})
    _check("order refund skipped", sf.wallet_movement(_txn("wt_T5", "credit", "manual_adjust", 1060, reference_type="order"))["skipped"],
           "order lifecycle")

    # ── order funded → reservations + drafts ──
    r1 = sf.order_funded(_order("order_TEST_1", [(100, 7.0), (50, 7.0)], 1060.0))
    so = frappe.get_doc("Storefront Order", "order_TEST_1")
    _check("order funded", (so.status, flt(so.lines_amount), flt(so.fee_amount)), ("Funded", 1050.0, 10.0))
    _check("lines + fee reserved", _bal(cust), (9404.6, 1060.0, 10464.6))
    _check("drafts per line", [frappe.db.get_value("Security Sale", n, "docstatus") for n in r1["security_sales"]], [0, 0])
    _check("funded is idempotent", sf.order_funded(_order("order_TEST_1", [(100, 7.0), (50, 7.0)], 1060.0)), r1)
    _check("drafts post no GL", frappe.db.count("GL Entry", {"voucher_no": ["in", [frappe.db.get_value("Security Sale", n, "revenue_journal_entry_ref") or "-" for n in r1["security_sales"]]]}), 0)

    # ── transferred → submitted on the transfer date ──
    rev_before = base._bal(base.REVENUE_ACC)
    sf.order_transferred({"id": "order_TEST_1", "transferred_at": "2026-02-05T06:00:00Z"})
    ss = [frappe.get_doc("Security Sale", n) for n in r1["security_sales"]]
    _check("sales submitted", [s.docstatus for s in ss], [1, 1])
    _check("posted on the transfer date", {(str(s.posting_date), str(s.transfer_date)) for s in ss}, {("2026-02-05", "2026-02-05")})
    _check("wallet settled: reserve gone, fee debited", _bal(cust), (9404.6, 0.0, 9404.6))
    _check("revenue booked", base._bal(base.REVENUE_ACC) - rev_before, -1050.0)
    holding = frappe.db.get_value("Customer Holding", {"customer": cust, "security": base.ISIN}, "qty")
    _check("customer holding updated", flt(holding), 150.0)
    _check("transferred is idempotent", sf.order_transferred({"id": "order_TEST_1"})["status"], "Transferred")
    _check("passbook sees the sales", sum(1 for e in __import__("polemarch.polemarch_trading.passbook", fromlist=["x"]).get_entries(security=base.ISIN, party=cust)), 2)

    # ── holdings read ──
    rows = [h for h in sf.customer_holdings()["holdings"] if h.medusa_customer_id == MID]
    _check("customer_holdings returns e16 qty", [(h.isin, flt(h.qty)) for h in rows], [(base.ISIN, 150.0)])

    # ── cancel before transfer ──
    r2 = sf.order_funded(_order("order_TEST_2", [(10, 7.0)], 70.0))
    _check("second order reserved", _bal(cust), (9334.6, 70.0, 9404.6))
    sf.order_cancelled({"id": "order_TEST_2", "reason": "test"})
    _check("cancel releases the reservation", _bal(cust), (9404.6, 0.0, 9404.6))
    _check("cancel deletes the draft", frappe.db.exists("Security Sale", r2["security_sales"][0]), None)
    _check("cancel after transfer only flags", sf.order_cancelled({"id": "order_TEST_1"})["status"], "Needs Review")
    _check("unknown order cancel is a no-op", sf.order_cancelled({"id": "order_TEST_none"}), {"status": "unknown"})

    # ── short wallet ──
    frappe.db.savepoint("sf_short")
    try:
        sf.order_funded(_order("order_TEST_3", [(1000, 100.0)], 100000.0))
        _check("short wallet refused", "accepted", "refused")
    except frappe.ValidationError:
        frappe.db.rollback(save_point="sf_short")
        _check("short wallet refused", "refused", "refused")
    _check("nothing left behind on refusal", frappe.db.exists("Storefront Order", "order_TEST_3"), None)

    # ── cut-over replay ──
    sf.wallet_movement(_txn("wt_T6", "debit", "order_debit", 404.6, reference_type="cart"), historic=1)
    _check("historic order debit applied as Buy Settlement", (frappe.db.get_value("Wallet Transaction", {"medusa_id": "wt_T6"}, "txn_type"), _bal(cust)[0]),
           ("Buy Settlement", 9000.0))

    # ── guard ──
    frappe.set_user("Guest")
    try:
        sf.ping()
        _check("Guest refused", "allowed", "refused")
    except frappe.PermissionError:
        _check("Guest refused", "refused", "refused")
    frappe.set_user("Administrator")

    out = _report()
    try:
        _teardown()
        base._teardown()
    except Exception as e:
        out += f"\n  (teardown warning: {e})"
    return out


def _teardown():
    frappe.set_user("Administrator")
    cust = frappe.db.get_value("Customer", {"medusa_customer_id": MID}, "name")
    for n in frappe.get_all("Storefront Order", filters={"medusa_order_id": ["like", "order_TEST_%"]}, pluck="name"):
        frappe.delete_doc("Storefront Order", n, force=True, ignore_permissions=True, delete_permanently=True)
    base._teardown(quiet=True)   # cancels the Sales, which reverse their own wallet rows
    if cust:
        wallet = f"WAL-{cust}"
        for n in frappe.get_all("Wallet Transaction", filters={"wallet": wallet}, pluck="name"):
            frappe.db.set_value("Wallet Transaction", n, "docstatus", 0, update_modified=False)
            frappe.delete_doc("Wallet Transaction", n, force=True, ignore_permissions=True, delete_permanently=True)
        for dt, flt_ in (("Wallet", {"customer": cust}), ("Customer Holding", {"customer": cust})):
            for n in frappe.get_all(dt, filters=flt_, pluck="name"):
                frappe.delete_doc(dt, n, force=True, ignore_permissions=True, delete_permanently=True)
        for n in frappe.get_all("Contact", filters={"email_id": EMAIL}, pluck="name"):
            frappe.delete_doc("Contact", n, force=True, ignore_permissions=True, delete_permanently=True)
        try:
            frappe.delete_doc("Customer", cust, force=True, ignore_permissions=True, delete_permanently=True)
        except Exception:
            pass
    frappe.db.commit()


def _report():
    failed = sum(1 for r in _results if not r[0])
    lines = [f"{len(_results) - failed}/{len(_results)} passed"]
    for ok, name, got, want in _results:
        lines.append(f"  {'PASS' if ok else 'FAIL'}  {name:<46} got={got!r} want={want!r}")
    return "\n".join(lines)
