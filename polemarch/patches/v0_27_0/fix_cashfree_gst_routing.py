"""v0_27_0 — Cashfree gateway-fee GST routing fix + historical backfill.

Background
──────────
Before this patch the Polemarch wallet-deposit auto-JE poster booked
the GST portion of every Cashfree fee against `wallet_gateway_fee_
gst_input_account`, which on this tenant pointed at "Postal Expenses
- MISPL" — a regular P&L Expense account, NOT a Tax Asset. Result:
ITC on every Cashfree fee was effectively forgone (GST got expensed
instead of recoverable). India Compliance never saw the entries
because the account wasn't in its GST register, so they never flowed
into GSTR-2B / GSTR-3B.

What this patch does
────────────────────
1) Auto-detect the correct Input Tax accounts per Company (from the
   `GST Account` child rows that India Compliance seeds during company
   setup) and write them into the new Polemarch Settings fields:
     - wallet_gateway_fee_cgst_account
     - wallet_gateway_fee_sgst_account
     - wallet_gateway_fee_igst_account
   Setup is idempotent — already-set fields are not overwritten.

2) BACKFILL — for every existing fee JE that hit the legacy
   `gst_input_account`, post a CORRECTIVE Journal Entry that moves
   the GST portion off the wrong expense bucket and onto the proper
   Input Tax accounts. Same-state companies get a 50/50 CGST + SGST
   split; cross-state ones land on IGST.

   We don't cancel + repost the original JE because:
     - The original Bank credit is real (Cashfree actually deducted
       it) — cancelling would create an audit gap.
     - The Wallet Deposit cancel-cascade looks up by source_doctype/
       source_name; a cancelled-and-reposted JE would break it.
   The corrective JE is tagged with `custom_source_doctype=Wallet
   Deposit` + `custom_source_name=<deposit>` so it shows up next to
   the original on the deposit's audit trail.

Idempotent — safe to re-run. Skips deposits already backfilled (by
checking for an existing corrective JE per deposit).
"""

from typing import Optional

import frappe
from frappe.utils import flt


def execute() -> None:
    if not frappe.db.exists("DocType", "Polemarch Settings"):
        return

    auto_set_input_tax_accounts()
    backfilled = backfill_corrective_jes()
    frappe.db.commit()
    print(
        f"v0_27_0: Cashfree GST routing fixed. "
        f"Backfill posted {backfilled} corrective JE(s)."
    )


def auto_set_input_tax_accounts() -> None:
    """Populate the new Polemarch Settings CGST/SGST/IGST fields from
    India Compliance's per-Company GST Account child rows.

    Only writes a field if it's currently empty — operators who already
    chose specific accounts are not overwritten."""
    s = frappe.get_single("Polemarch Settings")

    # India Compliance registers Input/Output/RCM account triplets in
    # the GST Settings.gst_accounts child table, keyed by company +
    # account_type. We want the "Input" triplet for the FIRST company
    # (this app is single-tenant; multi-company can be added later).
    company = frappe.defaults.get_global_default("company")
    if not company:
        print("v0_27_0: no default Company set — skipping auto GST account population")
        return

    input_row = None
    try:
        gst_settings = frappe.get_single("GST Settings")
        for row in gst_settings.gst_accounts or []:
            if row.company == company and row.account_type == "Input":
                input_row = row
                break
    except Exception as e:
        print(f"v0_27_0: GST Settings lookup failed ({e}); skipping auto-set")
        return

    if not input_row:
        print(
            f"v0_27_0: no India Compliance 'Input' GST Account row for "
            f"Company={company} — leaving operator to set CGST/SGST/IGST manually"
        )
        return

    changes = 0
    for fieldname, src in (
        ("wallet_gateway_fee_cgst_account", input_row.cgst_account),
        ("wallet_gateway_fee_sgst_account", input_row.sgst_account),
        ("wallet_gateway_fee_igst_account", input_row.igst_account),
    ):
        if not s.get(fieldname) and src:
            s.set(fieldname, src)
            changes += 1

    if changes:
        s.flags.ignore_validate = True
        s.save(ignore_permissions=True)
        print(
            f"v0_27_0: auto-populated {changes} GST account field(s) on "
            f"Polemarch Settings from India Compliance for Company={company}"
        )


def backfill_corrective_jes() -> int:
    """For each historical fee JE that booked GST to the legacy
    expense account, post a corrective JE that reclassifies the GST
    portion onto the proper Input Tax CGST/SGST/IGST accounts."""
    from polemarch.polemarch_trading.doctype.polemarch_settings.polemarch_settings import (
        get_gateway_fee_config,
    )

    cfg = get_gateway_fee_config()
    if not cfg.get("accounts_configured"):
        print("v0_27_0: gateway fee accounts not configured — skipping backfill")
        return 0
    legacy_acct = cfg.get("gst_input_account")
    if not legacy_acct:
        # Nothing to reclassify from.
        return 0

    # Find every submitted JE on a Wallet Deposit that:
    #   - posted a DEBIT against the legacy gst_input_account
    #   - belongs to a deposit (custom_source_doctype = "Wallet Deposit")
    # We use the legacy account name from the cfg as the marker —
    # any tenant whose past JEs used a DIFFERENT account is handled
    # by tweaking the cfg fallback (or this is a no-op for them).
    rows = frappe.db.sql(
        """
        SELECT je.name AS original_je, je.posting_date, je.company,
               je.custom_source_name AS deposit_name,
               jea.debit AS gst_amount
        FROM `tabJournal Entry` je
        JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
        WHERE je.custom_source_doctype = 'Wallet Deposit'
          AND je.docstatus = 1
          AND jea.account = %s
          AND jea.debit > 0
        ORDER BY je.posting_date, je.creation
        """,
        (legacy_acct,),
        as_dict=True,
    )

    posted = 0
    for r in rows:
        if _has_corrective_je(r["deposit_name"]):
            continue
        corrective = _post_corrective_je(
            company=r["company"],
            posting_date=r["posting_date"],
            deposit_name=r["deposit_name"],
            original_je=r["original_je"],
            gst_amount=flt(r["gst_amount"]),
            legacy_account=legacy_acct,
            cfg=cfg,
        )
        if corrective:
            posted += 1
            print(
                f"v0_27_0: backfilled GST routing for {r['deposit_name']} "
                f"original={r['original_je']} corrective={corrective}"
            )

    return posted


def _has_corrective_je(deposit_name: str) -> bool:
    """A corrective JE for a deposit is one tagged
    `custom_source_doctype=Wallet Deposit` + `custom_source_name=<dep>`
    whose user_remark starts with 'v0_27_0 GST'."""
    return bool(
        frappe.db.sql(
            """
            SELECT name FROM `tabJournal Entry`
            WHERE custom_source_doctype = 'Wallet Deposit'
              AND custom_source_name = %s
              AND docstatus = 1
              AND user_remark LIKE 'v0_27_0 GST%%'
            LIMIT 1
            """,
            (deposit_name,),
        )
    )


def _post_corrective_je(
    company: str,
    posting_date: str,
    deposit_name: str,
    original_je: str,
    gst_amount: float,
    legacy_account: str,
    cfg: dict,
) -> Optional[str]:
    """Post a JE that credits the legacy expense account by gst_amount
    and debits the proper Input Tax account(s) by the same.

    Net P&L effect: reverses the expense (zero impact on bank because
    we don't touch bank — the original ₹23.60 bank credit stays).
    Net BS effect: adds gst_amount to Input Tax Asset."""
    strategy = cfg.get("gst_strategy")
    accts = cfg.get("gst_accounts", {}) or {}
    cost_center = frappe.db.get_value("Company", company, "cost_center")

    accounts = [
        # Reverse the legacy expense (was Dr → now Cr to wipe it).
        {
            "account": legacy_account,
            "credit_in_account_currency": gst_amount,
            "cost_center": cost_center,
        },
    ]
    if strategy == "intra_state":
        half = round(gst_amount / 2.0, 2)
        accounts.extend([
            {
                "account": accts["cgst"],
                "debit_in_account_currency": half,
                "cost_center": cost_center,
            },
            {
                "account": accts["sgst"],
                "debit_in_account_currency": round(gst_amount - half, 2),
                "cost_center": cost_center,
            },
        ])
    elif strategy == "inter_state":
        accounts.append({
            "account": accts["igst"],
            "debit_in_account_currency": gst_amount,
            "cost_center": cost_center,
        })
    elif strategy == "legacy_single":
        # Operator hasn't migrated to CGST/SGST/IGST yet — there's no
        # better target than the legacy account itself, so we'd be a
        # no-op. Skip.
        return None
    else:
        return None

    je = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "posting_date": posting_date,
        "company": company,
        "user_remark": (
            f"v0_27_0 GST reclassification for {deposit_name} "
            f"(original JE: {original_je}) — moved ₹{gst_amount} "
            f"from {legacy_account} to Input Tax {strategy}"
        ),
        "custom_source_doctype": "Wallet Deposit",
        "custom_source_name": deposit_name,
        "accounts": accounts,
    })
    je.flags.ignore_permissions = True
    je.insert(ignore_permissions=True)
    je.submit()
    return je.name
