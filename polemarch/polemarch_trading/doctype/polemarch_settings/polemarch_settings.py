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
        #
        # The GST leg is satisfied by ANY of:
        #   - CGST + SGST both set (intra-state split)
        #   - IGST set (inter-state single line)
        #   - legacy gst_input_account set (back-compat single line)
        gst_ok = bool(
            (self.wallet_gateway_fee_cgst_account
             and self.wallet_gateway_fee_sgst_account)
            or self.wallet_gateway_fee_igst_account
            or self.wallet_gateway_fee_gst_input_account
        )
        non_gst_set = [
            bool(self.wallet_gateway_fee_expense_account),
            bool(self.wallet_gateway_fee_bank_account),
        ]
        any_set = any(non_gst_set) or gst_ok
        all_required = all(non_gst_set) and gst_ok
        if any_set and not all_required:
            frappe.throw(
                _(
                    "Gateway-fee accounts must be set together: "
                    "Expense + Bank + a GST route (either CGST+SGST, "
                    "IGST, or the legacy GST Input Account)."
                )
            )
        # CGST + SGST should always come as a pair — half a split is a
        # configuration mistake that would silently mis-route ITC.
        if bool(self.wallet_gateway_fee_cgst_account) != bool(
            self.wallet_gateway_fee_sgst_account
        ):
            frappe.throw(
                _("Set CGST and SGST accounts together — the split "
                  "is meaningless with only one.")
            )
        # Sanity-check percentage range
        if flt(self.wallet_gateway_fee_gst_rate) < 0 or flt(self.wallet_gateway_fee_gst_rate) > 100:
            frappe.throw(_("GST rate must be between 0 and 100."))


def get_gateway_fee_config() -> dict:
    """Convenience accessor for the wallet gateway fee section.

    Returns a dict with the fixed fee, GST rate, the per-deposit GST
    routing strategy, and computed totals.

    GST routing resolution (priority order):
      1. CGST + SGST both set → intra-state split (50/50 of gst_amount)
      2. IGST set → single-line inter-state
      3. legacy `gst_input_account` set → single-line back-compat
      4. nothing set → `accounts_configured=False`; caller should skip
         the auto-JE and let the operator post manually.

    `accounts_configured` is True iff expense + bank + at least one of
    the GST routes above are populated."""
    s = frappe.get_single("Polemarch Settings")
    fixed = flt(s.wallet_gateway_fee_fixed)
    rate = flt(s.wallet_gateway_fee_gst_rate)
    gst = round(fixed * rate / 100.0, 2)

    cgst_acct = (s.wallet_gateway_fee_cgst_account or "").strip()
    sgst_acct = (s.wallet_gateway_fee_sgst_account or "").strip()
    igst_acct = (s.wallet_gateway_fee_igst_account or "").strip()
    legacy_acct = (s.wallet_gateway_fee_gst_input_account or "").strip()

    if cgst_acct and sgst_acct:
        gst_strategy = "intra_state"
        gst_accounts: dict = {"cgst": cgst_acct, "sgst": sgst_acct}
    elif igst_acct:
        gst_strategy = "inter_state"
        gst_accounts = {"igst": igst_acct}
    elif legacy_acct:
        gst_strategy = "legacy_single"
        gst_accounts = {"legacy": legacy_acct}
    else:
        gst_strategy = None
        gst_accounts = {}

    return {
        "fixed_fee": fixed,
        "gst_rate": rate,
        "gst_amount": gst,
        "total_fee_with_gst": fixed + gst,
        "expense_account": s.wallet_gateway_fee_expense_account,
        "bank_account": s.wallet_gateway_fee_bank_account,
        "gst_strategy": gst_strategy,
        "gst_accounts": gst_accounts,
        # back-compat shim — old callers expect this key
        "gst_input_account": legacy_acct or None,
        "accounts_configured": bool(
            s.wallet_gateway_fee_expense_account
            and s.wallet_gateway_fee_bank_account
            and gst_strategy is not None
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
