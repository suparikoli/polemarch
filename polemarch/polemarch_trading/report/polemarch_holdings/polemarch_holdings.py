"""Polemarch Holdings — the single holdings report.

One report, three views (a `View` filter), replacing the separate
by-security / by-lot / ageing reports:

  Summary  one row per security: Investment vs Stock in Trade, cost, market
           value, unrealised gain %
  Lots     one row per open Investment Holding lot — the warehouse view:
           when bought, at what cost, how much is left, split across classes
  Ageing   age blocks, either rolling day-ranges (ERPNext Stock Ageing style)
           or by purchase period, with the 730-day long/short line called out

Everything reads the PER-CLASS quantities (qty_classified_sit /
qty_classified_investment / qty_unclassified), not the legacy single
`classification` Select — a lot can hold both classes at once and the legacy
field can only carry one label.
"""

from typing import Optional

import frappe
from frappe.utils import flt, formatdate, get_quarter_start, getdate, nowdate

LONG_TERM_DAYS = 730


def execute(filters: Optional[dict] = None):
    filters = filters or {}
    view = filters.get("view") or "Summary"
    if view == "Lots":
        return _lots(filters)
    if view == "Ageing":
        return _ageing(filters)
    return _summary(filters)


# ── shared ────────────────────────────────────────────────────────────────
def _fetch(filters):
    company = filters.get("company") or frappe.defaults.get_user_default("Company")
    conds, params = ["ih.docstatus < 2"], {}
    if company:
        conds.append("ih.company = %(company)s"); params["company"] = company
    if filters.get("security"):
        conds.append("ih.security = %(security)s"); params["security"] = filters["security"]
    if not filters.get("include_closed"):
        conds.append("ih.status IN ('Open','Partially Disposed')")
    rows = frappe.db.sql(
        f"""SELECT ih.name AS lot, ih.security, ih.classification, ih.status,
                   ih.acquisition_date, ih.qty_acquired, ih.qty_disposed,
                   COALESCE(ih.qty_reserved,0) AS qty_reserved,
                   ih.cost_basis_per_unit, ih.total_cost,
                   COALESCE(ih.qty_classified_sit,0) AS q_sit,
                   COALESCE(ih.qty_classified_investment,0) AS q_inv,
                   COALESCE(ih.qty_unclassified,0) AS q_unc,
                   ih.purchase_reference, ih.purchase_reference_link,
                   s.security_name, s.security_type, s.last_traded_price
              FROM `tabInvestment Holding` ih
              LEFT JOIN `tabSecurity` s ON s.name = ih.security
             WHERE {' AND '.join(conds)}
             ORDER BY s.security_name, ih.acquisition_date, ih.creation""",
        params, as_dict=True)
    for r in rows:
        r.remaining = flt(r.qty_acquired) - flt(r.qty_disposed) - flt(r.qty_reserved)
        # legacy lots (no child rows) fall back to the single label
        if (flt(r.q_sit) + flt(r.q_inv) + flt(r.q_unc)) <= 0:
            r.q_sit = r.remaining if r.classification == "Stock in Trade" else 0
            r.q_inv = r.remaining if r.classification == "Investment" else 0
            r.q_unc = r.remaining if r.classification not in ("Stock in Trade", "Investment") else 0
    cls = filters.get("classification")
    if cls:
        keep = {"Stock in Trade": "q_sit", "Investment": "q_inv", "Unallocated": "q_unc"}[cls]
        rows = [r for r in rows if flt(r.get(keep)) > 0]
    return [r for r in rows if r.remaining > 0 or filters.get("include_closed")]


def _mv(qty, cost, ltp):
    if flt(ltp) > 0 and qty > 0:
        mv = qty * flt(ltp)
        return mv, mv - cost, ((mv - cost) / cost * 100 if cost else None)
    return None, None, None


# ── views ─────────────────────────────────────────────────────────────────
def _summary(filters):
    agg = {}
    for r in _fetch(filters):
        a = agg.setdefault(r.security, {
            "security": r.security, "security_name": r.security_name,
            "security_type": r.security_type, "last_traded_price": r.last_traded_price,
            "inv_units": 0.0, "sit_units": 0.0, "unalloc_units": 0.0,
            "inv_cost": 0.0, "sit_cost": 0.0, "total_units": 0.0, "total_cost": 0.0, "lots": 0})
        cpu = flt(r.cost_basis_per_unit)
        a["inv_units"] += flt(r.q_inv); a["inv_cost"] += flt(r.q_inv) * cpu
        a["sit_units"] += flt(r.q_sit); a["sit_cost"] += flt(r.q_sit) * cpu
        a["unalloc_units"] += flt(r.q_unc)
        a["total_units"] += r.remaining; a["total_cost"] += r.remaining * cpu
        a["lots"] += 1
    rows = sorted(agg.values(), key=lambda x: -x["total_cost"])
    for a in rows:
        a["market_value"], a["unrealised_gain"], a["unrealised_gain_pct"] = _mv(
            a["total_units"], a["total_cost"], a["last_traded_price"])
    if rows:
        t = {k: sum(flt(r[k]) for r in rows) for k in
             ("inv_units", "sit_units", "unalloc_units", "inv_cost", "sit_cost",
              "total_units", "total_cost", "lots")}
        t |= {"security": "TOTAL", "security_name": "", "security_type": "", "last_traded_price": None}
        priced = [r for r in rows if r.get("market_value") is not None]
        if priced:
            mv = sum(r["market_value"] for r in priced); c = sum(r["total_cost"] for r in priced)
            t |= {"market_value": mv, "unrealised_gain": mv - c,
                  "unrealised_gain_pct": ((mv - c) / c * 100 if c else None)}
        rows.append(t)
    cols = [
        {"label": "Security", "fieldname": "security", "fieldtype": "Link", "options": "Security", "width": 120},
        {"label": "Name", "fieldname": "security_name", "fieldtype": "Data", "width": 200},
        {"label": "Investment", "fieldname": "inv_units", "fieldtype": "Float", "width": 110, "precision": 0},
        {"label": "Stock in Trade", "fieldname": "sit_units", "fieldtype": "Float", "width": 120, "precision": 0},
        {"label": "Unallocated", "fieldname": "unalloc_units", "fieldtype": "Float", "width": 105, "precision": 0},
        {"label": "Total Units", "fieldname": "total_units", "fieldtype": "Float", "width": 105, "precision": 0},
        {"label": "Investment Cost", "fieldname": "inv_cost", "fieldtype": "Currency", "width": 135},
        {"label": "SiT Cost", "fieldname": "sit_cost", "fieldtype": "Currency", "width": 120},
        {"label": "Total Cost", "fieldname": "total_cost", "fieldtype": "Currency", "width": 130},
        {"label": "Last Price", "fieldname": "last_traded_price", "fieldtype": "Currency", "width": 100},
        {"label": "Current Value", "fieldname": "market_value", "fieldtype": "Currency", "width": 130},
        {"label": "Unrealised", "fieldname": "unrealised_gain", "fieldtype": "Currency", "width": 120},
        {"label": "Profit %", "fieldname": "unrealised_gain_pct", "fieldtype": "Percent", "width": 90},
        {"label": "Lots", "fieldname": "lots", "fieldtype": "Int", "width": 60},
    ]
    return cols, rows


def _lots(filters):
    today = getdate(nowdate())
    out = []
    for r in _fetch(filters):
        cpu = flt(r.cost_basis_per_unit)
        r.qty_remaining = r.remaining
        r.remaining_cost = r.remaining * cpu
        r.age_days = (today - getdate(r.acquisition_date)).days if r.acquisition_date else None
        r.term = ("Long" if r.age_days >= LONG_TERM_DAYS else "Short") if r.age_days is not None else None
        if flt(r.q_sit) > 0 and flt(r.q_inv) > 0:
            r.classification = f"Split {flt(r.q_inv):,.0f} Inv / {flt(r.q_sit):,.0f} SiT"
        r.market_value, r.unrealised_gain, r.unrealised_gain_pct = _mv(
            r.remaining, r.remaining_cost, r.last_traded_price)
        out.append(r)
    if out:
        t = {k: sum(flt(r[k]) for r in out) for k in
             ("qty_acquired", "qty_remaining", "q_inv", "q_sit", "remaining_cost", "total_cost")}
        t |= {"lot": "TOTAL", "security": None, "security_name": "", "classification": ""}
        out.append(t)
    cols = [
        {"label": "Lot", "fieldname": "lot", "fieldtype": "Link", "options": "Investment Holding", "width": 150},
        {"label": "Security", "fieldname": "security", "fieldtype": "Link", "options": "Security", "width": 115},
        {"label": "Name", "fieldname": "security_name", "fieldtype": "Data", "width": 175},
        {"label": "Holding Type", "fieldname": "classification", "fieldtype": "Data", "width": 165},
        {"label": "Acquired", "fieldname": "acquisition_date", "fieldtype": "Date", "width": 95},
        {"label": "Age", "fieldname": "age_days", "fieldtype": "Int", "width": 65},
        {"label": "Term", "fieldname": "term", "fieldtype": "Data", "width": 60},
        {"label": "Qty Bought", "fieldname": "qty_acquired", "fieldtype": "Float", "width": 100, "precision": 0},
        {"label": "Qty Left", "fieldname": "qty_remaining", "fieldtype": "Float", "width": 95, "precision": 0},
        {"label": "of which Inv", "fieldname": "q_inv", "fieldtype": "Float", "width": 105, "precision": 0},
        {"label": "of which SiT", "fieldname": "q_sit", "fieldtype": "Float", "width": 105, "precision": 0},
        {"label": "Cost / Unit", "fieldname": "cost_basis_per_unit", "fieldtype": "Currency", "width": 100},
        {"label": "Cost of Purchase", "fieldname": "total_cost", "fieldtype": "Currency", "width": 130},
        {"label": "Cost Remaining", "fieldname": "remaining_cost", "fieldtype": "Currency", "width": 125},
        {"label": "Current Value", "fieldname": "market_value", "fieldtype": "Currency", "width": 120},
        {"label": "Profit %", "fieldname": "unrealised_gain_pct", "fieldtype": "Percent", "width": 85},
        {"label": "Bought Via", "fieldname": "purchase_reference_link", "fieldtype": "Dynamic Link",
         "options": "purchase_reference", "width": 150},
    ]
    return cols, out


def _period_of(d, mode):
    d = getdate(d)
    if mode == "Purchase Month":
        return d.strftime("%Y-%m"), formatdate(d, "MMM yyyy")
    if mode == "Purchase Quarter":
        q = get_quarter_start(d)
        return q.strftime("%Y-%m"), f"Q{((q.month - 1) // 3) + 1} {q.year}"
    fy = d.year if d.month >= 4 else d.year - 1
    return f"{fy}", f"FY{str(fy)[2:]}-{str(fy + 1)[2:]}"


def _ageing(filters):
    today = getdate(nowdate())
    r1, r2, r3 = (int(filters.get("range1") or 90), int(filters.get("range2") or 180),
                  int(filters.get("range3") or 365))
    blocks = filters.get("blocks_by") or "Age (days)"
    show_cost = int(filters.get("show_cost") or 0)

    # explode each lot into its per-class parts so a split lot lands in BOTH rows
    parts = []
    for r in _fetch(filters):
        for cls, q in (("Stock in Trade", flt(r.q_sit)), ("Investment", flt(r.q_inv)),
                       ("Unallocated", flt(r.q_unc))):
            if q > 0:
                p = frappe._dict(r); p.classification, p.qty = cls, q
                parts.append(p)

    if blocks != "Age (days)":
        periods, agg = {}, {}
        for p in parts:
            if not p.acquisition_date:
                continue
            k, lbl = _period_of(p.acquisition_date, blocks); periods[k] = lbl
            a = agg.setdefault((p.security_name or p.security, p.security, p.classification), {
                "security": p.security, "security_name": p.security_name,
                "classification": p.classification, "qty": 0.0, "cost": 0.0})
            a[k] = a.get(k, 0.0) + (p.qty * flt(p.cost_basis_per_unit) if show_cost else p.qty)
            a["qty"] += p.qty; a["cost"] += p.qty * flt(p.cost_basis_per_unit)
        keys = sorted(periods); rows = [v for _k, v in sorted(agg.items())]
        if rows:
            t = {k: sum(flt(r.get(k) or 0) for r in rows) for k in keys + ["qty", "cost"]}
            t |= {"security": "TOTAL", "security_name": "", "classification": ""}
            rows.append(t)
        u = "Cost" if show_cost else "Qty"
        cols = [
            {"label": "Security", "fieldname": "security", "fieldtype": "Link", "options": "Security", "width": 120},
            {"label": "Name", "fieldname": "security_name", "fieldtype": "Data", "width": 185},
            {"label": "Holding Type", "fieldname": "classification", "fieldtype": "Data", "width": 120},
        ] + [{"label": f"{periods[k]} ({u})", "fieldname": k,
              "fieldtype": "Currency" if show_cost else "Float", "width": 118, "precision": 0} for k in keys] + [
            {"label": "Total Qty", "fieldname": "qty", "fieldtype": "Float", "width": 105, "precision": 0},
            {"label": "Total Cost", "fieldname": "cost", "fieldtype": "Currency", "width": 135}]
        return cols, rows

    agg = {}
    for p in parts:
        a = agg.setdefault((p.security_name or p.security, p.security, p.classification), {
            "b1": 0.0, "b2": 0.0, "b3": 0.0, "b4": 0.0,
            "security": p.security, "security_name": p.security_name,
            "classification": p.classification, "qty": 0.0, "cost": 0.0,
            "long_term_qty": 0.0, "_aw": 0.0,
            "earliest": p.acquisition_date, "latest": p.acquisition_date})
        age = (today - getdate(p.acquisition_date)).days if p.acquisition_date else 0
        v = p.qty * flt(p.cost_basis_per_unit) if show_cost else p.qty
        a["b1" if age <= r1 else "b2" if age <= r2 else "b3" if age <= r3 else "b4"] += v
        a["qty"] += p.qty; a["cost"] += p.qty * flt(p.cost_basis_per_unit); a["_aw"] += age * p.qty
        if age >= LONG_TERM_DAYS:
            a["long_term_qty"] += p.qty
        if p.acquisition_date:
            if getdate(p.acquisition_date) < getdate(a["earliest"]): a["earliest"] = p.acquisition_date
            if getdate(p.acquisition_date) > getdate(a["latest"]): a["latest"] = p.acquisition_date
    rows = []
    for _k, a in sorted(agg.items()):
        a["avg_age"] = a["_aw"] / a["qty"] if a["qty"] else 0
        a["short_term_qty"] = a["qty"] - a["long_term_qty"]
        rows.append(a)
    if rows:
        t = {k: sum(flt(r[k]) for r in rows) for k in
             ("b1", "b2", "b3", "b4", "qty", "cost", "long_term_qty", "short_term_qty")}
        t |= {"security": "TOTAL", "security_name": "", "classification": "",
              "avg_age": (sum(r["_aw"] for r in rows) / t["qty"] if t["qty"] else 0)}
        rows.append(t)
    u = "Cost" if show_cost else "Qty"
    ft = "Currency" if show_cost else "Float"
    cols = [
        {"label": "Security", "fieldname": "security", "fieldtype": "Link", "options": "Security", "width": 120},
        {"label": "Name", "fieldname": "security_name", "fieldtype": "Data", "width": 185},
        {"label": "Holding Type", "fieldname": "classification", "fieldtype": "Data", "width": 120},
        {"label": f"0-{r1} d ({u})", "fieldname": "b1", "fieldtype": ft, "width": 115, "precision": 0},
        {"label": f"{r1}-{r2} d ({u})", "fieldname": "b2", "fieldtype": ft, "width": 115, "precision": 0},
        {"label": f"{r2}-{r3} d ({u})", "fieldname": "b3", "fieldtype": ft, "width": 115, "precision": 0},
        {"label": f"{r3}+ d ({u})", "fieldname": "b4", "fieldtype": ft, "width": 115, "precision": 0},
        {"label": "Total Qty", "fieldname": "qty", "fieldtype": "Float", "width": 100, "precision": 0},
        {"label": "Total Cost", "fieldname": "cost", "fieldtype": "Currency", "width": 130},
        {"label": "Avg Age", "fieldname": "avg_age", "fieldtype": "Float", "width": 85, "precision": 0},
        {"label": f"Short (<{LONG_TERM_DAYS}d)", "fieldname": "short_term_qty", "fieldtype": "Float", "width": 130, "precision": 0},
        {"label": f"Long (>={LONG_TERM_DAYS}d)", "fieldname": "long_term_qty", "fieldtype": "Float", "width": 130, "precision": 0},
        {"label": "Earliest", "fieldname": "earliest", "fieldtype": "Date", "width": 95},
        {"label": "Latest", "fieldname": "latest", "fieldtype": "Date", "width": 95},
    ]
    return cols, rows
