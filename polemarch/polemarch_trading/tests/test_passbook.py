"""Tests for the Transfer Date field and the Securities Passbook.

Run against a TEST site only (it submits and cancels accounting documents):
    bench --site fixerp.localhost execute \
      polemarch.polemarch_trading.tests.test_passbook.run

Reuses the TEST- Security / Customer scaffolding of
test_classification_and_fifo and adds a second counterparty.
"""

import frappe

from polemarch.polemarch_trading.tests import test_classification_and_fifo as base

CUST_B = "TEST Counterparty B (polemarch tests)"
_results = []


def _check(name, got, want, note=""):
    _results.append((got == want, name, got, want, note))


def _cust_b():
    name = frappe.db.get_value("Customer", {"customer_name": CUST_B}, "name")
    if name:
        return name
    d = frappe.get_doc({"doctype": "Customer", "customer_name": CUST_B,
                        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
                        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name")})
    base._fill_mandatory_from_existing(d)
    return d.insert(ignore_permissions=True).name


def _doc(doctype, party, qty, rate, date, transfer_date=None, **kw):
    d = frappe.get_doc({
        "doctype": doctype, "company": base.CO, "security": base.ISIN, "posting_date": date,
        "transfer_date": transfer_date, "party_type": "Customer", "party": party,
        "qty": qty, "rate": rate, "payment_method": "Bank", "payment_account": base.CLEARING, **kw,
    })
    d.insert(ignore_permissions=True)
    d.submit()
    return d


def run(keep=False):
    if frappe.local.site == "e16.mith.tech":
        frappe.throw("test_passbook submits and cancels accounting documents: never run it on e16.")
    from polemarch.polemarch_trading import passbook
    from polemarch.polemarch_trading import portfolio_transfer as pt

    base._teardown(quiet=True)
    _teardown_b()
    base._setup()
    a, b = base._cust_name(), _cust_b()

    # ── Transfer Date ──
    p1 = _doc("Security Purchase", a, 1000, 5.0, "2025-04-01", intended_classification="Stock in Trade")
    _check("transfer_date defaults to posting_date", str(p1.transfer_date), "2025-04-01")
    p2 = _doc("Security Purchase", b, 500, 6.0, "2025-04-05", "2025-04-03", intended_classification="Stock in Trade")
    _check("explicit transfer_date kept", str(p2.transfer_date), "2025-04-03")
    s1 = _doc("Security Sale", a, 300, 7.0, "2025-04-10", from_classification="Stock in Trade")
    s2 = _doc("Security Sale", b, 200, 8.0, "2025-04-10", "2025-04-04", from_classification="Stock in Trade")

    gl_before = frappe.db.count("GL Entry")
    s1.transfer_date = "2025-04-12"
    s1.save(ignore_permissions=True)      # update-after-submit
    _check("transfer_date editable after submit", str(frappe.db.get_value("Security Sale", s1.name, "transfer_date")), "2025-04-12")
    _check("editing transfer_date posts no GL", frappe.db.count("GL Entry"), gl_before)
    _check("Security Sale list sorts by transfer_date", frappe.get_meta("Security Sale").sort_field, "transfer_date")
    _check("Security Purchase list sorts by transfer_date", frappe.get_meta("Security Purchase").sort_field, "transfer_date")

    t = frappe.get_doc({"doctype": "Portfolio Transfer", "company": base.CO, "transfer_date": "2025-04-11",
                        "security": base.ISIN, "isin": base.ISIN, "from_classification": "Stock in Trade",
                        "to_classification": "Investment", "qty": 100, "fmv_per_unit": 5.0,
                        "fmv_source": "Manual", "valuation_reference": "at cost (test)", "reason": "passbook test"})
    t.insert(ignore_permissions=True); t.submit()
    t.flags.bypass_approval_checks = True; t.transition_to("Approved"); pt.post_transfer(t.name)

    # ── Passbook ──
    pb = passbook.get_security_passbook(base.ISIN)
    order = [e["voucher_no"] for e in pb["entries"]]
    _check("passbook ordered by transfer date", order, [p1.name, p2.name, s2.name, t.name, s1.name])
    _check("running qty", [e["balance_qty"] for e in pb["entries"]], [1000.0, 1500.0, 1300.0, 1300.0, 1000.0])
    _check("running FIFO cost", [e["balance_cost"] for e in pb["entries"]], [5000.0, 8000.0, 7000.0, 7000.0, 5500.0])
    _check("reclass moves no qty", (pb["entries"][3]["qty_in"], pb["entries"][3]["qty_out"], pb["entries"][3]["reclass_qty"]), (0.0, 0.0, 100.0))
    _check("closing qty = Security.qty_total", (pb["closing_qty"], pb["qty_ok"]), (1000.0, True))
    _check("closing cost = Security.cost_total", (pb["closing_cost"], pb["cost_ok"]), (5500.0, True))

    # ── Counterparties ──
    cps = {g["party"]: g for g in passbook.get_counterparties(passbook.get_entries(security=base.ISIN))}
    _check("two counterparties", sorted(cps), sorted([a, b]))
    _check("A totals", (cps[a]["qty_in"], cps[a]["qty_out"], cps[a]["amount_paid"], cps[a]["amount_received"], cps[a]["count"]),
           (1000.0, 300.0, 5000.0, 2100.0, 2))
    _check("B net qty", cps[b]["net_qty"], 300.0)

    # ── Filters keep the real running balance ──
    only_b = passbook.get_entries(security=base.ISIN, party=b)
    _check("party filter", [e["voucher_no"] for e in only_b], [p2.name, s2.name])
    _check("filtered row keeps the book balance", only_b[-1]["balance_qty"], 1300.0)
    _check("direction filter", [e["voucher_no"] for e in passbook.get_entries(security=base.ISIN, direction="Out")], [s2.name, s1.name])
    in_range = passbook.get_entries(security=base.ISIN, from_date="2025-04-04", to_date="2025-04-11")
    _check("date range uses transfer date", [e["voucher_no"] for e in in_range], [s2.name, t.name])

    # ── Report ──
    from frappe.desk.query_report import run as qrun
    rep = qrun("Securities Passbook", filters={"security": base.ISIN, "view": "Passbook"}, ignore_prepared_report=True)["result"]
    _check("report passbook rows", len(rep), 5)
    rep_cp = qrun("Securities Passbook", filters={"security": base.ISIN, "view": "Counterparties"}, ignore_prepared_report=True)["result"]
    _check("report counterparties: 2 parents + 4 movements", (sum(1 for r in rep_cp if r.get("indent") == 0), sum(1 for r in rep_cp if r.get("indent") == 1)), (2, 4))
    party_view = qrun("Securities Passbook", filters={"party_type": "Customer", "party": a, "view": "Counterparties"}, ignore_prepared_report=True)["result"]
    _check("party view covers the party's securities", {r["security"] for r in party_view}, {base.ISIN})

    out = _report()
    if keep:    # leave the TEST- documents for a look at the form; re-run to clear
        return out
    try:
        base._teardown()
        _teardown_b()
    except Exception as e:
        out += f"\n  (teardown warning: {e})"
    return out


def _teardown_b():
    name = frappe.db.get_value("Customer", {"customer_name": CUST_B}, "name")
    if name:
        try:
            frappe.delete_doc("Customer", name, force=True, ignore_permissions=True, delete_permanently=True)
        except Exception:
            pass
        frappe.db.commit()


def _report():
    failed = sum(1 for r in _results if not r[0])
    lines = [f"{len(_results) - failed}/{len(_results)} passed"]
    for ok, name, got, want, note in _results:
        lines.append(f"  {'PASS' if ok else 'FAIL'}  {name:<48} got={got!r} want={want!r}" + (f"  ({note})" if note else ""))
    return "\n".join(lines)
