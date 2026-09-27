"""Daily reconciliation jobs.

The two wallet audits (header vs ledger vs GL, and Σ wallets vs liability
account) run from `customer_wallet.audit`; `log_wallet_audit` below is the
`customer_wallet_audit_log` hook that lands their results in Polemarch Audit Log.

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


def log_wallet_audit(job: str, checked: int, mismatches: list):
    """customer_wallet_audit_log hook — write wallet audit results to Polemarch Audit Log."""
    _log_audit(job, checked, mismatches)


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



# ── Inventory account vs Investment Holdings (Phase 26 follow-up) ───────────


def verify_inventory_account_matches_holdings():
    """Compare Securities Inventory + Long-Term Investments GL balances
    against the sum of open Investment Holdings, broken down by
    classification. Any drift > ₹100 lands in Polemarch Audit Log so
    it doesn't sit unnoticed (as it did before Phase 26).

    Does NOT auto-heal — Phase 26 cleaned up smoke-test residue manually
    after rooting it out, and the verify_engines cleanup was hardened to
    cancel JEs properly. If new drift appears, it points at a real bug
    (e.g. a hook that posts a JE but skips on cancel), and self-healing
    would mask the underlying issue.
    """
    if not frappe.db.exists("DocType", "Polemarch Audit Log"):
        return

    companies = frappe.get_all("Company", fields=["name", "abbr"])
    drift_rows = []
    checked = 0

    for company in companies:
        if not company.abbr:
            continue
        # Expected balances per classification from the live Holdings.
        expected_by_cls = {}
        for cls in ("Stock in Trade", "Investment", "Unallocated"):
            row = frappe.db.sql(
                """
                SELECT COALESCE(SUM(qty_remaining * cost_basis_per_unit), 0)
                  FROM `tabInvestment Holding`
                 WHERE company        = %s
                   AND classification = %s
                   AND status         IN ('Open', 'Partially Disposed')
                """,
                (company.name, cls),
            )
            expected_by_cls[cls] = flt(row[0][0]) if row else 0

        # GL balances on the two inventory accounts.
        # Today Unallocated cost sits in Securities Inventory (changes in Phase 24B
        # when the Pending Classification suspense account ships).
        sit_acct = frappe.db.get_value(
            "Account",
            {"company": company.name, "account_name": "Securities Inventory - Trading", "disabled": 0},
            "name",
        )
        inv_acct = frappe.db.get_value(
            "Account",
            {"company": company.name, "account_name": "Long-Term Investments", "disabled": 0},
            "name",
        )
        if not (sit_acct and inv_acct):
            continue
        checked += 1

        sit_gl = _account_balance(sit_acct)
        inv_gl = _account_balance(inv_acct)

        # SiT account holds SiT + Unallocated; Investment account holds Investment.
        sit_expected = expected_by_cls["Stock in Trade"] + expected_by_cls["Unallocated"]
        inv_expected = expected_by_cls["Investment"]

        sit_delta = sit_gl - sit_expected
        inv_delta = inv_gl - inv_expected

        if abs(sit_delta) > 100:
            drift_rows.append(
                f"{company.abbr}: {sit_acct} GL=₹{sit_gl:,.2f} vs expected ₹{sit_expected:,.2f} (Δ ₹{sit_delta:+,.2f})"
            )
        if abs(inv_delta) > 100:
            drift_rows.append(
                f"{company.abbr}: {inv_acct} GL=₹{inv_gl:,.2f} vs expected ₹{inv_expected:,.2f} (Δ ₹{inv_delta:+,.2f})"
            )

    try:
        frappe.get_doc({
            "doctype": "Polemarch Audit Log",
            "job": "verify_inventory_account_matches_holdings",
            "ran_at": now_datetime(),
            "checked_count": checked,
            "mismatch_count": len(drift_rows),
            "details": (
                "\n".join(drift_rows)
                if drift_rows
                else "All inventory account balances reconcile cleanly with live Holdings."
            ),
            "notes": "Detects drift > ₹100. Does NOT auto-heal — drift points at a real bug (e.g. JE posted but cancel skipped).",
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Polemarch Inventory Account audit log write",
        )


def _account_balance(account: str) -> float:
    row = frappe.db.sql(
        "SELECT COALESCE(SUM(debit - credit), 0) FROM `tabGL Entry` WHERE account=%s AND is_cancelled=0",
        (account,),
    )
    return flt(row[0][0]) if row else 0.0
