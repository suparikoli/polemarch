"""Regression tests for the six classification / FIFO bugs fixed 2026-09-21.

Run against a live site:
    bench --site <site> execute \
      polemarch.polemarch_trading.tests.test_classification_and_fifo.run

Creates its own Security / Customer / documents under a TEST- prefix, asserts,
then tears everything down. Every test maps to a bug that reached production:

  1 intended_classification overridden to Stock in Trade for Customer parties
  2 nil-consideration movements crashed on a zero/zero Journal Entry
  3 Portfolio Transfer bumped qty_disposed on an in-place reclassification
  4 split lots reported wholly under the legacy single `classification`
  5 fifo.consume offered a split lot's whole qty to either class
  6 the classification window was forfeited by choosing a class at create time
  + gifts charged to COGS instead of a promotion expense head
"""

import frappe
from frappe.utils import flt

# Runs on any site: the company is the site's default company (MISPL on
# production), the accounts follow its abbreviation.
CO = frappe.defaults.get_global_default("company") if getattr(frappe.local, "site", None) else "MITHTECH INNOVATIVE SOLUTIONS PVT LTD"
ABBR = frappe.db.get_value("Company", CO, "abbr") if getattr(frappe.local, "site", None) else "MISPL"
ISIN = "ZZ9TEST00019"   # valid shape: 2 letters + 9 alnum + 1 digit
CUST = "TEST Counterparty (polemarch tests)"


def _acc(account_name):
    """Account.name by account_name: sites that number their chart prefix
    the number, so the name cannot be composed from the label."""
    return frappe.db.get_value("Account", {"company": CO, "account_name": account_name, "is_group": 0}, "name") \
        or f"{account_name} - {ABBR}"


CLEARING = _acc("Securities Trade Clearing")
GIFT_ACC = _acc("Securities Gifted - Business Promotion")
COGS_ACC = _acc("Trading COGS - Securities")
REVENUE_ACC = _acc("Trading Revenue - Securities")


def _cust_name():
    return frappe.db.get_value("Customer", {"customer_name": CUST}, "name") or CUST


def _ensure_account(account_name, root_type, parent_fragment):
    """The two MISPL-specific accounts the test posts to, created on demand."""
    if frappe.db.exists("Account", {"company": CO, "account_name": account_name}):
        return
    parent = frappe.db.get_value("Account", {"company": CO, "is_group": 1, "root_type": root_type,
                                             "account_name": ["like", f"%{parent_fragment}%"]}, "name", order_by="lft ASC")
    frappe.get_doc({"doctype": "Account", "account_name": account_name, "company": CO, "parent_account": parent,
                    "root_type": root_type, "is_group": 0}).insert(ignore_permissions=True)


def _fill_mandatory_from_existing(doc):
    for df in frappe.get_meta(doc.doctype).fields:
        if df.reqd and not doc.get(df.fieldname) and df.fieldtype not in ("Table", "Table MultiSelect"):
            value = frappe.db.get_value(doc.doctype, {df.fieldname: ["is", "set"]}, df.fieldname)
            if value is not None:
                doc.set(df.fieldname, value)

_results = []


def _check(name, got, want, note=""):
    ok = got == want
    _results.append((ok, name, got, want, note))
    return ok


def _setup():
    if not frappe.db.exists("Security", ISIN):
        frappe.get_doc({"doctype": "Security", "isin": ISIN, "security_name": "TEST Security",
                        "security_type": "Unlisted Shares", "tradable": 1, "active": 1}
                       ).insert(ignore_permissions=True)
    _ensure_account("Securities Trade Clearing", "Asset", "Current Assets")
    _ensure_account("Securities Gifted - Business Promotion", "Expense", "Indirect Expenses")
    if not frappe.db.get_value("Customer", {"customer_name": CUST}, "name"):
        d = frappe.get_doc({"doctype": "Customer", "customer_name": CUST,
                        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
                        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name")})
        _fill_mandatory_from_existing(d)
        d.insert(ignore_permissions=True)


def _buy(qty, rate, cls, date="2025-04-01"):
    d = frappe.get_doc({
        "doctype": "Security Purchase", "company": CO, "security": ISIN, "posting_date": date,
        "party_type": "Customer", "party": _cust_name(), "qty": qty, "rate": rate,
        "intended_classification": cls, "payment_method": "Bank", "payment_account": CLEARING,
    })
    d.insert(ignore_permissions=True); d.submit()
    return d


def _sell(qty, rate, from_cls, date="2026-01-01"):
    d = frappe.get_doc({
        "doctype": "Security Sale", "company": CO, "security": ISIN, "posting_date": date,
        "party_type": "Customer", "party": _cust_name(), "qty": qty, "rate": rate,
        "from_classification": from_cls, "payment_method": "Bank", "payment_account": CLEARING,
    })
    d.insert(ignore_permissions=True); d.submit()
    return d


def _bal(acc):
    return flt(frappe.db.sql(
        "select sum(debit-credit) from `tabGL Entry` where account=%s and is_cancelled=0", acc)[0][0] or 0)


def run():
    _teardown(quiet=True)          # clear any residue from a previous run FIRST --
    _setup()                       # teardown drops the test masters, so setup comes after
    from polemarch.polemarch_trading import fifo

    # ── 1. intended_classification must survive a Customer-party purchase ──
    p_inv = _buy(10000, 5.00, "Investment")
    h_inv = frappe.get_doc("Investment Holding", {"purchase_reference_link": p_inv.name})
    _check("bug1 Investment purchase keeps its classification", h_inv.classification, "Investment")

    # ── 6. the classification window must stay open on an explicit choice ──
    _check("bug6 classification_deadline stamped on an explicit class",
           bool(h_inv.classification_deadline), True)

    # ── default is now Stock in Trade ──
    meta_default = frappe.get_meta("Security Purchase").get_field("intended_classification").default
    _check("default intended_classification", meta_default, "Stock in Trade")

    p_sit = _buy(20000, 4.00, "Stock in Trade", "2025-04-02")
    h_sit = frappe.get_doc("Investment Holding", {"purchase_reference_link": p_sit.name})
    _check("Stock in Trade purchase classification", h_sit.classification, "Stock in Trade")

    # ── 2. a nil-consideration purchase must not attempt a JE ──
    p_zero = _buy(500, 0.0, "Stock in Trade", "2025-04-03")
    p_zero.reload()   # NB: reload() returns the doc, so it cannot be used inside an `or`
    _check("bug2 zero-cost purchase posts no JE",
           (p_zero.journal_entry_ref or "") == "", True)
    _check("bug2 zero-cost purchase still creates a holding",
           bool(frappe.db.exists("Investment Holding", {"purchase_reference_link": p_zero.name})), True)

    # ── 3. Portfolio Transfer must not shrink the lot ──
    from polemarch.polemarch_trading import portfolio_transfer as pt
    before_rem = flt(frappe.db.get_value("Investment Holding", h_sit.name, "qty_remaining"))
    t = frappe.get_doc({"doctype": "Portfolio Transfer", "company": CO, "transfer_date": "2025-06-01",
                        "security": ISIN, "isin": ISIN, "from_classification": "Stock in Trade",
                        "to_classification": "Investment", "qty": 5000, "fmv_per_unit": 4.00,
                        "fmv_source": "Manual", "valuation_reference": "at cost (test)", "reason": "regression test"})
    t.insert(ignore_permissions=True); t.submit()
    t.flags.bypass_approval_checks = True; t.transition_to("Approved"); pt.post_transfer(t.name)
    h_sit.reload()
    _check("bug3 lot size unchanged by reclassification", flt(h_sit.qty_remaining), before_rem)
    _check("bug3 split recorded: SiT", flt(h_sit.qty_classified_sit), 15000.0)
    _check("bug3 split recorded: Investment", flt(h_sit.qty_classified_investment), 5000.0)
    _check("bug3 status not flipped to disposed", h_sit.status, "Open")

    # ── 4. reports must read per-class, not the legacy label ──
    from frappe.desk.query_report import run as qrun
    summ = qrun("Polemarch Holdings", filters={"company": CO, "security": ISIN, "view": "Summary"},
                ignore_prepared_report=True)["result"]
    row = [r for r in summ if r.get("security") == ISIN][0]
    _check("bug4 summary Investment units", flt(row["inv_units"]), 15000.0, "10,000 + 5,000 reclassified")
    _check("bug4 summary Stock in Trade units", flt(row["sit_units"]), 15500.0, "15,000 + 500 zero-cost")
    lots = qrun("Polemarch Holdings", filters={"company": CO, "security": ISIN, "view": "Lots"},
                ignore_prepared_report=True)["result"]
    split = [r for r in lots if r.get("lot") == h_sit.name][0]
    _check("bug4 lot view shows the split label", str(split["classification"]).startswith("Split"), True)
    age = qrun("Polemarch Holdings", filters={"company": CO, "security": ISIN, "view": "Ageing"},
               ignore_prepared_report=True)["result"]
    classes = {r.get("classification") for r in age if r.get("security") != "TOTAL"}
    _check("bug4 ageing splits a mixed lot across both rows",
           {"Stock in Trade", "Investment"} <= classes, True)

    # ── 5. FIFO must cap availability per class ──
    plan_sit = fifo.consume(security=ISIN, company=CO, classification="Stock in Trade", qty_to_sell=99999)
    _check("bug5 SiT availability capped", sum(flt(p.qty) for p in plan_sit), 15500.0)
    plan_inv = fifo.consume(security=ISIN, company=CO, classification="Investment", qty_to_sell=99999)
    _check("bug5 Investment availability capped", sum(flt(p.qty) for p in plan_inv), 15000.0)
    _check("bug5 each lot carries its source class",
           all(p.classification == "Stock in Trade" for p in plan_sit), True)

    # ── cascade: Stock in Trade first, then Investment ──
    casc = fifo.consume_cascade(security=ISIN, company=CO, qty_to_sell=20000)
    _check("cascade total planned", sum(flt(p.qty) for p in casc), 20000.0)
    _check("cascade takes all SiT first",
           sum(flt(p.qty) for p in casc if p.classification == "Stock in Trade"), 15500.0)
    _check("cascade then spills into Investment",
           sum(flt(p.qty) for p in casc if p.classification == "Investment"), 4500.0)
    _check("cascade order is SiT before Investment",
           [p.classification for p in casc][-1], "Investment")

    # ── gift: cost to the promotion head, not COGS ──
    gift_before, cogs_before = _bal(GIFT_ACC), _bal(COGS_ACC)
    s_gift = _sell(100, 0.0, "Stock in Trade", "2026-02-01")
    s_gift.reload()
    _check("bug2 zero-value sale posts no revenue JE",
           (s_gift.revenue_journal_entry_ref or "") == "", True)
    _check("gift cost hits the promotion head", _bal(GIFT_ACC) - gift_before, 400.0, "100 @ 4.00")
    _check("gift cost does NOT hit COGS", _bal(COGS_ACC) - cogs_before, 0.0)

    # ── a normal sale still books revenue + COGS ──
    cogs_b, rev_b = _bal(COGS_ACC), _bal(REVENUE_ACC)
    _sell(1000, 6.00, "Stock in Trade", "2026-02-02")
    _check("normal sale books COGS", _bal(COGS_ACC) - cogs_b, 4000.0, "1,000 @ 4.00")
    _check("normal sale books revenue", _bal(REVENUE_ACC) - rev_b, -6000.0)

    # ── disposal bumps the per-class counter, not just the total ──
    h_sit.reload()
    _check("disposal reduced SiT, not Investment", flt(h_sit.qty_classified_investment), 5000.0)

    tb = frappe.db.sql("select sum(debit), sum(credit) from `tabGL Entry` where is_cancelled=0")[0]
    _check("trial balance still balances", round(flt(tb[0]) - flt(tb[1]), 2), 0.0)

    out = _report()
    try:
        _teardown()
    except Exception as e:
        out += f"\n  (teardown warning: {e})"
    return out


def _report():
    lines, failed = [], 0
    for ok, name, got, want, note in _results:
        if not ok:
            failed += 1
        lines.append(f"  {'PASS' if ok else 'FAIL'}  {name:<52} got={got!r} want={want!r}"
                     + (f"  ({note})" if note else ""))
    lines.insert(0, f"{len(_results) - failed}/{len(_results)} passed")
    return "\n".join(lines)


def _cancel_source_jes():
    """Cancel every JE these tests caused, BEFORE the source docs are deleted.

    Deleting a submitted source document does not reverse its Journal Entry, and
    a Portfolio Transfer's JE is never cancelled by the doc lifecycle at all.
    An earlier version of this teardown deleted first and left 3 live JEs behind,
    which shifted Long-Term Investments, Securities Inventory and the clearing
    account on real books. Always run this first.
    """
    names = set()
    for dt in ("Security Purchase", "Security Sale", "Portfolio Transfer"):
        for n in frappe.get_all(dt, filters={"security": ISIN}, pluck="name"):
            for f in ("journal_entry_ref", "revenue_journal_entry_ref",
                      "cogs_journal_entry_ref", "journal_entry"):
                if frappe.get_meta(dt).has_field(f):
                    v = frappe.db.get_value(dt, n, f)
                    if v:
                        names.add(v)
            names |= set(frappe.get_all("Journal Entry",
                         filters={"custom_source_doctype": dt, "custom_source_name": n},
                         pluck="name"))
    for n in frappe.get_all("Investment Disposal", filters={"security": ISIN}, pluck="name"):
        names |= set(frappe.get_all("Journal Entry",
                     filters={"custom_source_doctype": "Investment Disposal",
                              "custom_source_name": n}, pluck="name"))
    for n in names:
        if frappe.db.get_value("Journal Entry", n, "docstatus") == 1:
            je = frappe.get_doc("Journal Entry", n)
            je.flags.ignore_permissions = True
            try: je.cancel()
            except Exception: pass
    frappe.db.commit()


def _teardown(quiet=False):
    _cancel_source_jes()
    for dt in ("Security Sale", "Security Purchase"):
        for n in frappe.get_all(dt, filters={"security": ISIN}, pluck="name"):
            d = frappe.get_doc(dt, n)
            if d.docstatus == 1:
                try: d.cancel()
                except Exception: pass
            frappe.db.set_value(dt, n, "docstatus", 0, update_modified=False)
    for n in frappe.get_all("Investment Disposal", filters={"security": ISIN}, pluck="name"):
        frappe.delete_doc("Investment Disposal", n, force=True, ignore_permissions=True, delete_permanently=True)
    for n in frappe.get_all("Portfolio Transfer", filters={"security": ISIN}, pluck="name"):
        # a Posted/Approved transfer refuses both cancel and delete, so drop the
        # docstatus first -- this is test scaffolding, not a business path.
        frappe.db.set_value("Portfolio Transfer", n, "docstatus", 0, update_modified=False)
        frappe.delete_doc("Portfolio Transfer", n, force=True, ignore_permissions=True, delete_permanently=True)
    for n in frappe.get_all("Investment Holding", filters={"security": ISIN}, pluck="name"):
        frappe.delete_doc("Investment Holding", n, force=True, ignore_permissions=True, delete_permanently=True)
    for dt in ("Security Sale", "Security Purchase"):
        for n in frappe.get_all(dt, filters={"security": ISIN}, pluck="name"):
            frappe.delete_doc(dt, n, force=True, ignore_permissions=True, delete_permanently=True)
    for dt, n in (("Security", ISIN), ("Customer", _cust_name())):
        if frappe.db.exists(dt, n):
            try:
                frappe.delete_doc(dt, n, force=True, ignore_permissions=True, delete_permanently=True)
            except Exception:
                pass
    frappe.db.commit()
