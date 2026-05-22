"""Security.qty_sit / qty_investment / qty_total / cost_total cache.

These fields on the Security doctype are denormalised rollups of all open
Investment Holdings for that security. Kept fresh by:

  - Investment Holding doc_events (after_insert, on_update, on_trash) →
    `refresh_for_security(holding.security)`
  - Daily audit task `audit_security_holdings_cache` reconciles cache vs
    truth and logs any drift to Polemarch Audit Log.

The cache is per-Company in the sense that all qty/cost values represent
the SUM across all companies; in a multi-company site you'd want to
split this per-company, but the current Polemarch tenancy is single-
company (Mithtech). Add company-scoped cache fields when that changes.

Refresh strategy: small writes via `db.set_value` (skip validate / save
hooks) so the recompute is cheap and doesn't re-trigger on its own
on_update. `update_modified=False` keeps the audit trail clean.
"""

import frappe
from frappe.utils import flt


def refresh_for_security(security: str) -> dict:
    """Recompute and persist qty/cost cache fields on a single Security.

    Returns the computed values so callers (audit task) can compare.
    Safe to call on a non-existent Security name (silently returns zeros).
    """
    if not security:
        return _zero()
    if not frappe.db.exists("Security", security):
        return _zero()

    row = frappe.db.sql(
        """
        SELECT
            SUM(IF(classification='Stock in Trade', qty_remaining, 0))                          AS qty_sit,
            SUM(IF(classification='Investment',     qty_remaining, 0))                          AS qty_investment,
            SUM(qty_remaining)                                                                  AS qty_total,
            SUM(qty_remaining * cost_basis_per_unit)                                           AS cost_total
        FROM `tabInvestment Holding`
        WHERE security = %s
          AND status IN ('Open', 'Partially Disposed')
          AND qty_remaining > 0
        """,
        (security,),
        as_dict=True,
    )[0]

    values = {
        "qty_sit":        flt(row["qty_sit"]),
        "qty_investment": flt(row["qty_investment"]),
        "qty_total":      flt(row["qty_total"]),
        "cost_total":     flt(row["cost_total"]),
    }

    frappe.db.set_value(
        "Security",
        security,
        values,
        update_modified=False,
    )
    return values


def refresh_all() -> int:
    """Recompute the cache for every Security. Returns count refreshed."""
    names = frappe.db.get_list("Security", pluck="name", limit_page_length=0)
    for n in names:
        refresh_for_security(n)
    return len(names)


def _zero() -> dict:
    return {"qty_sit": 0.0, "qty_investment": 0.0, "qty_total": 0.0, "cost_total": 0.0}


# ─── doc_events hooks (Investment Holding) ──────────────────────────────────


def on_investment_holding_change(doc, method=None):
    """Single entry-point wired to after_insert / on_update / on_trash /
    on_cancel on Investment Holding via hooks.py."""
    security = getattr(doc, "security", None)
    if not security:
        return
    try:
        refresh_for_security(security)
    except Exception:
        # Cache refresh failures must not break the Investment Holding write.
        # The daily audit task will catch drift and re-converge.
        frappe.log_error(
            frappe.get_traceback(),
            "Polemarch Holdings cache refresh",
        )


# ─── Daily audit (drift check) ──────────────────────────────────────────────


def audit_security_holdings_cache() -> None:
    """Wired to scheduler_events.daily in hooks.py.

    Recomputes the cache for every Security and compares against the
    persisted value. Any drift > 0.5 units OR > ₹1 in cost is logged to
    Polemarch Audit Log + re-persisted (self-healing).
    """
    from frappe.utils import now_datetime

    drift_rows = []
    names = frappe.db.get_list("Security", pluck="name", limit_page_length=0)
    checked = 0
    for n in names:
        checked += 1
        before = frappe.db.get_value(
            "Security",
            n,
            ["qty_sit", "qty_investment", "qty_total", "cost_total"],
            as_dict=True,
        ) or {}
        after = refresh_for_security(n)

        if (
            abs(flt(before.get("qty_sit"))        - after["qty_sit"])        > 0.5
            or abs(flt(before.get("qty_investment")) - after["qty_investment"]) > 0.5
            or abs(flt(before.get("qty_total"))   - after["qty_total"])   > 0.5
            or abs(flt(before.get("cost_total"))  - after["cost_total"])  > 1.0
        ):
            drift_rows.append(
                f"{n}: "
                f"qty_sit {flt(before.get('qty_sit'))}→{after['qty_sit']}, "
                f"qty_investment {flt(before.get('qty_investment'))}→{after['qty_investment']}, "
                f"qty_total {flt(before.get('qty_total'))}→{after['qty_total']}, "
                f"cost_total ₹{flt(before.get('cost_total'))}→₹{after['cost_total']}"
            )

    try:
        frappe.get_doc({
            "doctype": "Polemarch Audit Log",
            "job": "audit_security_holdings_cache",
            "ran_at": now_datetime(),
            "checked_count": checked,
            "mismatch_count": len(drift_rows),
            "details": (
                "\n".join(drift_rows)
                if drift_rows
                else "All Security cache rows reconcile cleanly."
            ),
            "notes": "Self-healing: drifting rows were re-persisted from truth.",
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Polemarch Holdings cache audit log write",
        )
