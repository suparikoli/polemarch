"""Polemarch Settings — singleton holding cross-cutting config for the
Polemarch trading subsystem.

Currently houses:
  - Medusa sync master toggle + HMAC webhook secret
  - Wallet payment-gateway fee config (company-borne, NOT deducted from
    customer's wallet credit)

Read via `frappe.get_single("Polemarch Settings")` from controllers + the
wallet-sync API. The Wallet Deposit controller reads the gateway-fee
section when posting the auto side-JE on Cashfree-sourced deposits.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PolemarchSettings(Document):
    def validate(self):
        self._validate_gateway_fee_accounts()

    def _validate_gateway_fee_accounts(self):
        # If gateway-fee config is partial (some accounts set, some not),
        # the Wallet Deposit controller would fail unexpectedly. Catch it
        # at settings-save time with a friendly error.
        accounts_set = [
            bool(self.wallet_gateway_fee_expense_account),
            bool(self.wallet_gateway_fee_gst_input_account),
            bool(self.wallet_gateway_fee_bank_account),
        ]
        if any(accounts_set) and not all(accounts_set):
            frappe.throw(
                _(
                    "All three gateway-fee accounts must be set together "
                    "(Expense, GST Input, Bank), or left blank. Currently "
                    "{0} of 3 are set."
                ).format(sum(accounts_set))
            )
        # Sanity-check percentage range
        if flt(self.wallet_gateway_fee_gst_rate) < 0 or flt(self.wallet_gateway_fee_gst_rate) > 100:
            frappe.throw(_("GST rate must be between 0 and 100."))


def get_gateway_fee_config() -> dict:
    """Convenience accessor for the wallet gateway fee section.

    Returns a dict with the fixed fee, GST rate, accounts, and a
    computed total fee + GST amount for the per-deposit JE.
    `accounts_configured` is True only when ALL THREE accounts are set
    (Expense / GST Input / Bank). If False, the caller should skip the
    auto fee-JE and let the operator post manually."""
    s = frappe.get_single("Polemarch Settings")
    fixed = flt(s.wallet_gateway_fee_fixed)
    rate = flt(s.wallet_gateway_fee_gst_rate)
    gst = round(fixed * rate / 100.0, 2)
    return {
        "fixed_fee": fixed,
        "gst_rate": rate,
        "gst_amount": gst,
        "total_fee_with_gst": fixed + gst,
        "expense_account": s.wallet_gateway_fee_expense_account,
        "gst_input_account": s.wallet_gateway_fee_gst_input_account,
        "bank_account": s.wallet_gateway_fee_bank_account,
        "accounts_configured": bool(
            s.wallet_gateway_fee_expense_account
            and s.wallet_gateway_fee_gst_input_account
            and s.wallet_gateway_fee_bank_account
        ),
    }


def get_medusa_webhook_secret() -> str | None:
    """Decrypt the stored Medusa webhook secret. Returns None if unset."""
    s = frappe.get_single("Polemarch Settings")
    # Password fieldtype decryption — frappe.utils.password.get_decrypted_password
    if not s.medusa_webhook_secret:
        return None
    try:
        from frappe.utils.password import get_decrypted_password
        return get_decrypted_password(
            "Polemarch Settings", "Polemarch Settings", "medusa_webhook_secret",
        )
    except Exception:
        return None


def is_medusa_sync_enabled() -> bool:
    return bool(frappe.db.get_single_value("Polemarch Settings", "enable_medusa_sync"))
