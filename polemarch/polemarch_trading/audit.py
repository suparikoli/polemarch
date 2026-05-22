"""Daily reconciliation jobs.

These jobs DETECT mismatches; they do NOT auto-heal. Auto-syncing one source
to another risks hiding real corruption. Each job:

  1. Computes the expected vs actual value per scope.
  2. Logs the breakdown to a dedicated Audit Log row (when the doctype exists).
  3. Falls back to `frappe.log_error` so first-week deployments still get
     something queryable before Polemarch Audit Log exists.

Jobs are wired into `scheduler_events.daily` in hooks.py.
"""

import frappe
from frappe.utils import flt, now_datetime


_DELTA_TOLERANCE = 0.01


def verify_wallet_balance_matches_ledger():
    """Three-way check: Wallet header vs Wallet Transaction sum vs GL Liability sum."""
    if not frappe.db.table_exists("Wallet"):
        return

    wallets = frappe.get_all(
        "Wallet",
        fields=["name", "customer", "company", "balance_total", "gl_liability_account"],
    )

    mismatches = []
    for w in wallets:
        ledger_sum = _wallet_transaction_signed_sum(w.name)
        gl_balance = _gl_party_balance(w.gl_liability_account, "Customer", w.customer)

        # GL liability accounts carry a credit balance for wallet credit;
        # `_gl_party_balance` returns SUM(credit) - SUM(debit) which matches.
        if (
            abs(flt(w.balance_total) - flt(ledger_sum)) > _DELTA_TOLERANCE
            or abs(flt(w.balance_total) - flt(gl_balance)) > _DELTA_TOLERANCE
        ):
            mismatches.append(
                {
                    "wallet": w.name,
                    "customer": w.customer,
                    "header_total": flt(w.balance_total),
                    "ledger_sum": flt(ledger_sum),
                    "gl_balance": flt(gl_balance),
                }
            )

        _stamp_recon(w.name, flt(w.balance_total) - flt(ledger_sum))

    _log_audit("verify_wallet_balance_matches_ledger", len(wallets), mismatches)


def verify_holding_disposal_chain():
    """Investment Holding qty_disposed must equal SUM(Investment Disposal Lot.qty_consumed)."""
    if not frappe.db.table_exists("Investment Holding"):
        return

    rows = frappe.db.sql(
        """
        SELECT ih.name           AS holding,
               ih.qty_disposed   AS stored_qty_disposed,
               COALESCE(SUM(CASE WHEN idl.docstatus != 2 AND id_parent.docstatus = 1
                                  THEN idl.qty_consumed ELSE 0 END), 0)
                            AS lot_sum_qty_disposed
          FROM `tabInvestment Holding` ih
          LEFT JOIN `tabInvestment Disposal Lot` idl ON idl.holding = ih.name
          LEFT JOIN `tabInvestment Disposal` id_parent ON id_parent.name = idl.parent
         GROUP BY ih.name
        """,
        as_dict=True,
    )

    mismatches = [
        {
            "holding": r.holding,
            "stored": flt(r.stored_qty_disposed),
            "computed": flt(r.lot_sum_qty_disposed),
        }
        for r in rows
        if abs(flt(r.stored_qty_disposed) - flt(r.lot_sum_qty_disposed)) > _DELTA_TOLERANCE
    ]

    _log_audit("verify_holding_disposal_chain", len(rows), mismatches)


def _wallet_transaction_signed_sum(wallet: str) -> float:
    row = frappe.db.sql(
        """
        SELECT COALESCE(
                 SUM(CASE WHEN direction = 'Credit' AND is_cancelled = 0 THEN amount
                          WHEN direction = 'Debit'  AND is_cancelled = 0 THEN -amount
                          ELSE 0 END),
                 0) AS signed_sum
          FROM `tabWallet Transaction`
         WHERE wallet = %s
           AND docstatus = 1
        """,
        (wallet,),
        as_dict=True,
    )
    return flt(row[0].signed_sum if row else 0)


def _gl_party_balance(account: str, party_type: str, party: str) -> float:
    if not account or not party:
        return 0
    row = frappe.db.sql(
        """
        SELECT COALESCE(SUM(credit) - SUM(debit), 0) AS balance
          FROM `tabGL Entry`
         WHERE account     = %s
           AND party_type  = %s
           AND party       = %s
           AND is_cancelled = 0
        """,
        (account, party_type, party),
        as_dict=True,
    )
    return flt(row[0].balance if row else 0)


def _stamp_recon(wallet_name: str, delta: float):
    frappe.db.set_value(
        "Wallet",
        wallet_name,
        {"last_recon_at": now_datetime(), "last_recon_delta": delta},
        update_modified=False,
    )


def _log_audit(job: str, checked: int, mismatches: list):
    if not mismatches:
        return
    # Try the dedicated audit doctype; fall back to log_error.
    if frappe.db.table_exists("Polemarch Audit Log"):
        try:
            log = frappe.get_doc(
                {
                    "doctype": "Polemarch Audit Log",
                    "job": job,
                    "checked_count": checked,
                    "mismatch_count": len(mismatches),
                    "details": frappe.as_json(mismatches[:50]),
                }
            )
            log.flags.ignore_permissions = True
            log.insert(ignore_permissions=True)
            return
        except Exception:
            pass

    frappe.log_error(
        f"{job}: {len(mismatches)}/{checked} mismatches. First 20: {mismatches[:20]}",
        f"Polemarch Audit: {job}",
    )
