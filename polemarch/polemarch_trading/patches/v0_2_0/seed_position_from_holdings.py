"""Seed Security Position rows from the new Security Lot ground truth.

Recomputes every (portfolio, security) tuple that has at least one open
Security Lot. Delegates to `polemarch.polemarch_trading.position.recompute_from_lots`
to avoid duplicating the rollup SQL.

Idempotent.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Security Position"):
        return
    if not frappe.db.table_exists("Security Lot"):
        return

    from polemarch.polemarch_trading import position as position_engine

    tuples = frappe.db.sql(
        """
        SELECT DISTINCT portfolio, security
          FROM `tabSecurity Lot`
         WHERE status IN ('Open', 'Partially Disposed')
        """,
        as_dict=True,
    )

    created = 0
    failed = []
    for row in tuples:
        try:
            position_engine.recompute_from_lots(row.portfolio, row.security)
            created += 1
        except Exception as exc:
            failed.append({"portfolio": row.portfolio, "security": row.security, "reason": str(exc)[:200]})

    frappe.db.commit()

    if failed:
        frappe.log_error(
            f"seed_position_from_holdings: recomputed={created}, failed={len(failed)}. "
            f"First 20: {failed[:20]}",
            "Polemarch Position Seed",
        )
