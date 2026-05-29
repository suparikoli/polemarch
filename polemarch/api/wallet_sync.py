"""Wallet sync API — called by the Medusa erpnext-plugin to mirror
wallet activity from the Medusa side into ERPNext.

Endpoints:

  record_deposit(customer, amount, posting_date=None, gateway_ref=None,
                 source="cashfree", remarks=None)
      Creates a Wallet Deposit + (for source="cashfree") posts a side
      JE booking the gateway fee as a company expense. The customer's
      wallet is credited the FULL `amount` — the fee is NEVER deducted
      from their balance.

  record_withdrawal(customer, amount, posting_date=None,
                    gateway_ref=None, remarks=None)
      Creates a Wallet Withdrawal. No gateway-fee handling on the
      withdrawal side (gateway charges only apply to pay-ins).

These are write-only endpoints (Medusa → Frappe push for wallet
events). The READ side — listing Frappe-originated wallet docs back
to Medusa for the pull cron — used to live here as
`list_for_medusa()`, but is now covered by the canonical mapping
("Wallet Transaction → Wallet Deposit / Withdrawal" rows in the
plugin's canonical-mappings.ts), which uses Frappe's standard
`frappe.client.get_list` API. Same for customer + security listing
— those endpoints (catalog_sync.py) have been dropped entirely as of
v0_25_0.

All endpoints are idempotent by `gateway_ref`: re-posting the same
reference returns the existing doc instead of creating a duplicate.

Security: relies on Frappe API token auth (the Medusa plugin uses a
dedicated API user with limited scope). Each endpoint also enforces
`Polemarch Settings.enable_medusa_sync == 1`.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt

from polemarch.polemarch_trading.doctype.polemarch_settings.polemarch_settings import (
    get_gateway_fee_config,
    is_medusa_sync_enabled,
)


_VALID_DEPOSIT_SOURCES = {"cashfree", "manual", "bank_transfer", "razorpay", "stripe"}


@frappe.whitelist()
def record_deposit(
    customer: str,
    amount: float,
    posting_date: Optional[str] = None,
    gateway_ref: Optional[str] = None,
    source: str = "cashfree",
    remarks: Optional[str] = None,
) -> dict:
    """Record a wallet deposit. For source='cashfree' (the default),
    also posts a side JE booking the gateway fee as a company
    expense — the customer's wallet credit is NOT reduced by the fee."""
    if not is_medusa_sync_enabled():
        frappe.throw(
            _("Medusa sync is disabled. Enable it in Polemarch Settings."),
            title=_("Sync Disabled"),
        )
    if source not in _VALID_DEPOSIT_SOURCES:
        frappe.throw(
            _("Unknown deposit source {0}. Valid: {1}").format(
                source, ", ".join(sorted(_VALID_DEPOSIT_SOURCES))
            ),
            title=_("Invalid Source"),
        )
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} does not exist.").format(customer))
    amt = flt(amount)
    if amt <= 0:
        frappe.throw(_("Amount must be greater than zero."))

    # Idempotency: if a Wallet Deposit with this gateway_ref already
    # exists (reference_no), return it instead of creating another.
    if gateway_ref:
        existing = frappe.db.get_value(
            "Wallet Deposit",
            {"reference_no": gateway_ref, "customer": customer, "docstatus": 1},
            ["name", "amount"],
            as_dict=True,
        )
        if existing:
            return {
                "ok": True,
                "deposit": existing.name,
                "amount": flt(existing.amount),
                "fee_je": _find_fee_je_for_deposit(existing.name),
                "idempotent_skip": True,
            }

    posting_date = posting_date or frappe.utils.today()
    company = _company_for_customer_wallet(customer)

    deposit_fields: dict = {
        "doctype": "Wallet Deposit",
        "customer": customer,
        "company": company,
        "posting_date": posting_date,
        "amount": amt,
        "bank_or_cash_account": _bank_account_for_company(company),
        "mode": "Bank Transfer" if source != "manual" else "Other",
        "reference_no": gateway_ref,
        "remarks": remarks
            or f"Wallet deposit via {source} (gateway_ref={gateway_ref or 'none'})",
    }
    # C1 fix: tag every API-created doc so the pull cron knows to skip it
    if frappe.db.has_column("Wallet Deposit", "medusa_originated"):
        deposit_fields["medusa_originated"] = 1
    deposit = frappe.get_doc(deposit_fields)
    deposit.flags.ignore_permissions = True
    deposit.insert(ignore_permissions=True)
    deposit.submit()

    # Post the gateway-fee side JE for cashfree-sourced deposits.
    fee_je_name = None
    if source == "cashfree":
        fee_je_name = _post_gateway_fee_je(
            company=company,
            posting_date=posting_date,
            deposit_name=deposit.name,
            gateway_ref=gateway_ref,
        )

    frappe.db.commit()
    return {
        "ok": True,
        "deposit": deposit.name,
        "amount": amt,
        "fee_je": fee_je_name,
        "idempotent_skip": False,
    }


@frappe.whitelist()
def record_withdrawal(
    customer: str,
    amount: float,
    posting_date: Optional[str] = None,
    gateway_ref: Optional[str] = None,
    remarks: Optional[str] = None,
) -> dict:
    """Record a wallet withdrawal (customer pulls funds back out). No
    gateway-fee booking on the withdrawal side — fees apply only on
    pay-ins."""
    if not is_medusa_sync_enabled():
        frappe.throw(
            _("Medusa sync is disabled."), title=_("Sync Disabled"),
        )
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} does not exist.").format(customer))
    amt = flt(amount)
    if amt <= 0:
        frappe.throw(_("Amount must be greater than zero."))

    if gateway_ref:
        existing = frappe.db.get_value(
            "Wallet Withdrawal",
            {"reference_no": gateway_ref, "customer": customer, "docstatus": 1},
            ["name", "amount"],
            as_dict=True,
        )
        if existing:
            return {
                "ok": True,
                "withdrawal": existing.name,
                "amount": flt(existing.amount),
                "idempotent_skip": True,
            }

    posting_date = posting_date or frappe.utils.today()
    company = _company_for_customer_wallet(customer)

    withdrawal_fields: dict = {
        "doctype": "Wallet Withdrawal",
        "customer": customer,
        "company": company,
        "posting_date": posting_date,
        "amount": amt,
        "bank_or_cash_account": _bank_account_for_company(company),
        "reference_no": gateway_ref,
        "remarks": remarks
            or f"Wallet withdrawal (gateway_ref={gateway_ref or 'none'})",
    }
    if frappe.db.has_column("Wallet Withdrawal", "medusa_originated"):
        withdrawal_fields["medusa_originated"] = 1
    wd = frappe.get_doc(withdrawal_fields)
    wd.flags.ignore_permissions = True
    wd.insert(ignore_permissions=True)
    wd.submit()
    frappe.db.commit()
    return {
        "ok": True,
        "withdrawal": wd.name,
        "amount": amt,
        "idempotent_skip": False,
    }


# ── helpers ─────────────────────────────────────────────────────────


def _post_gateway_fee_je(
    company: str,
    posting_date: str,
    deposit_name: str,
    gateway_ref: Optional[str],
) -> Optional[str]:
    """Post a separate JE booking the gateway fee + GST as a company
    expense. Skips silently (returns None) if the settings don't have
    all three accounts configured — the operator can post the fee
    manually until they configure it.

    JE shape (per deposit):
        DR Payment Gateway Fee Expense    <fixed_fee>
        DR GST Input                       <gst_amount>
        CR Bank                            <fixed_fee + gst_amount>

    Tagged with `custom_source_doctype=Wallet Deposit` +
    `custom_source_name=<deposit_name>` so cancel-cascade and audit
    queries can find it."""
    cfg = get_gateway_fee_config()
    if not cfg["accounts_configured"]:
        frappe.log_error(
            f"Wallet Deposit {deposit_name}: gateway-fee JE skipped — "
            f"Polemarch Settings.wallet_gateway_fee_* accounts not all set. "
            f"Configure them in Desk → Polemarch Settings to enable auto-fee posting.",
            "Polemarch Gateway Fee Skip",
        )
        return None
    if cfg["total_fee_with_gst"] <= 0:
        return None

    cost_center = frappe.db.get_value("Company", company, "cost_center")

    # Build the GST-leg account lines per the configured strategy.
    # intra_state → 50/50 CGST + SGST split (typical when the gateway's
    #   GSTIN state == this Company's state, e.g. Cashfree Karnataka ↔
    #   MISPL Karnataka → 9% CGST + 9% SGST).
    # inter_state → single IGST line.
    # legacy_single → fall back to the deprecated single GST Input
    #   account so old tenants don't break before they migrate.
    gst_amount = cfg["gst_amount"]
    gst_lines: list[dict] = []
    strategy = cfg.get("gst_strategy")
    accts = cfg.get("gst_accounts", {}) or {}
    if strategy == "intra_state":
        half = round(gst_amount / 2.0, 2)
        # Avoid a rounding penny drift on odd amounts — anchor the
        # second leg as (gst - half) so the two sum exactly to
        # gst_amount even for ₹3.61 / ₹0.01 edge cases.
        gst_lines = [
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
        ]
    elif strategy == "inter_state":
        gst_lines = [
            {
                "account": accts["igst"],
                "debit_in_account_currency": gst_amount,
                "cost_center": cost_center,
            },
        ]
    elif strategy == "legacy_single":
        gst_lines = [
            {
                "account": accts["legacy"],
                "debit_in_account_currency": gst_amount,
                "cost_center": cost_center,
            },
        ]
    else:
        # Defensive: get_gateway_fee_config().accounts_configured should
        # have already returned False, but belt-and-suspenders.
        return None

    # India Compliance: any JE touching a GST account requires
    # `company_gstin` set on the doc. Lift from the Company master.
    company_gstin = frappe.db.get_value("Company", company, "gstin")

    je = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "posting_date": posting_date,
            "company": company,
            "company_gstin": company_gstin,
            "user_remark": (
                f"Payment gateway fee for Wallet Deposit {deposit_name}"
                + (f" (gateway_ref={gateway_ref})" if gateway_ref else "")
                + f" [{strategy}]"
            ),
            "cheque_no": gateway_ref or f"FEE-{deposit_name}",
            "cheque_date": posting_date,
            "custom_source_doctype": "Wallet Deposit",
            "custom_source_name": deposit_name,
            "accounts": [
                {
                    "account": cfg["expense_account"],
                    "debit_in_account_currency": cfg["fixed_fee"],
                    "cost_center": cost_center,
                },
                *gst_lines,
                {
                    "account": cfg["bank_account"],
                    "credit_in_account_currency": cfg["total_fee_with_gst"],
                    "cost_center": cost_center,
                },
            ],
        }
    )
    je.flags.ignore_permissions = True
    je.insert(ignore_permissions=True)
    je.submit()
    return je.name


def _find_fee_je_for_deposit(deposit_name: str) -> Optional[str]:
    """Locate the gateway-fee JE for a given Wallet Deposit, if any.
    Distinguishes it from the deposit's own JE by checking the
    expense_account debit — the deposit's own JE only touches
    Wallet Liability + Bank, never the Fee Expense account.

    Returns None when:
      - The fee accounts aren't configured (no fees can ever be booked
        on this tenant — common during early setup).
      - The deposit exists but no fee JE was posted (source=manual or
        cashfree fees disabled). Previously this branch hit
        `[][0]` and IndexError'd; the empty-list guard added below
        keeps the idempotent-skip path safe."""
    cfg = get_gateway_fee_config()
    if not cfg["accounts_configured"]:
        return None
    if not cfg["expense_account"]:
        return None
    rows = frappe.db.sql(
        """
        SELECT DISTINCT je.name
        FROM `tabJournal Entry` je
        JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
        WHERE je.custom_source_doctype = 'Wallet Deposit'
          AND je.custom_source_name = %s
          AND je.docstatus = 1
          AND jea.account = %s
          AND jea.debit > 0
        LIMIT 1
        """,
        (deposit_name, cfg["expense_account"]),
    )
    return rows[0][0] if rows else None


def _company_for_customer_wallet(customer: str) -> str:
    """Resolve the company hosting the customer's wallet. Each wallet is
    keyed `WAL-<customer>` and has a `company` field; if no wallet
    exists yet, fall back to the global default."""
    wallet_name = f"WAL-{customer}"
    if frappe.db.exists("Wallet", wallet_name):
        return frappe.db.get_value("Wallet", wallet_name, "company")
    company = frappe.defaults.get_global_default("company")
    if not company:
        frappe.throw(_("No default Company configured."))
    return company


def _bank_account_for_company(company: str) -> str:
    """Find the default Bank or Cash account for the company. Same
    resolution the Wallet Deposit controller does — kept here as a
    one-liner so the API can pass it in explicitly."""
    direct = frappe.db.get_value("Company", company, "default_bank_account")
    if direct:
        return direct
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_type": ["in", ["Bank", "Cash"]],
            "disabled": 0,
            "is_group": 0,
        },
        "name",
        order_by="account_type ASC, creation ASC",
    )
