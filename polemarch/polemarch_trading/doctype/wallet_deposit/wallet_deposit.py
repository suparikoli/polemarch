"""Wallet Deposit — operator-recorded customer-funds-in.

Closes the manual-GL gap that existed before Phase 17. Submitting a
Wallet Deposit posts BOTH legs atomically:

  1. Wallet Transaction:
        Credit Deposit on WAL-<customer>
        Δ: balance_available + amount, balance_total + amount

  2. Journal Entry:
        DR  <Receiving Account>            amount
        CR  Customer Wallet Liability      amount   (party=Customer)

The wallet's gl_liability_account is the CR target. Party_type=Customer
is tagged on the JE row for per-customer attribution in standard ERPNext
party reports — note: ERPNext only ENFORCES party for Payable/Receivable
account_types; Customer Wallet Liability is account_type="" (Phase 13
fix) so the tag is informational, not mandatory.

Idempotency: `reference_no` (when set) drives the WT's idempotency_key,
so re-submitting the same bank UTR is a no-op rather than a double-credit.

On cancel:
  - Reverse the Wallet Transaction (Reversal Debit -amount on the wallet).
  - Cancel the Journal Entry (ERPNext auto-reverses GL).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class WalletDeposit(Document):
    def validate(self):
        self._resolve_wallet()
        self._validate_amount()
        self._validate_customer_wallet_active()
        self._default_receiving_account()

    def on_submit(self):
        wt_name = self._post_wallet_transaction()
        je_name = self._post_journal_entry()
        self.db_set("wallet_transaction_ref", wt_name, update_modified=False)
        self.db_set("journal_entry_ref", je_name, update_modified=False)

    def on_cancel(self):
        self._reverse_wallet_transaction()
        self._cancel_journal_entry()

    # ── validate ─────────────────────────────────────────────────────

    def _resolve_wallet(self):
        if not self.customer:
            return
        wallet_name = f"WAL-{self.customer}"
        if frappe.db.exists("Wallet", wallet_name):
            self.wallet = wallet_name

    def _validate_amount(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("Amount must be greater than zero."))

    def _validate_customer_wallet_active(self):
        if not self.wallet:
            frappe.throw(
                _("Customer {0} has no Wallet ({1}).").format(
                    self.customer, f"WAL-{self.customer}"
                ),
                title=_("Wallet Missing"),
            )
        status = frappe.db.get_value("Wallet", self.wallet, "status")
        if status != "Active":
            frappe.throw(
                _("Wallet {0} is not Active (status={1}).").format(self.wallet, status),
                title=_("Wallet Frozen"),
            )

    def _default_receiving_account(self):
        if self.bank_or_cash_account:
            return
        direct = frappe.db.get_value("Company", self.company, "default_bank_account")
        if direct:
            self.bank_or_cash_account = direct
            return
        self.bank_or_cash_account = frappe.db.get_value(
            "Account",
            {
                "company": self.company,
                "account_type": ["in", ["Bank", "Cash"]],
                "disabled": 0,
                "is_group": 0,
            },
            "name",
            order_by="account_type ASC, creation ASC",
        )

    # ── on_submit ────────────────────────────────────────────────────

    def _post_wallet_transaction(self) -> str:
        from polemarch.polemarch_trading import wallet as wallet_engine
        idem = (
            f"wallet-deposit:{self.reference_no}"
            if self.reference_no
            else f"wallet-deposit:{self.name}"
        )
        return wallet_engine.apply_delta(
            wallet=self.wallet,
            txn_type="Deposit",
            direction="Credit",
            amount=flt(self.amount),
            reference_doctype="Wallet Deposit",
            reference_name=self.name,
            idempotency_key=idem,
            remarks=self.remarks
                or f"Wallet Deposit {self.name} via {self.mode or 'unspecified mode'}",
        )

    def _post_journal_entry(self) -> str:
        from polemarch.polemarch_trading.accounting import _existing_je_for_source

        existing = _existing_je_for_source("Wallet Deposit", self.name)
        if existing:
            return existing

        liability = frappe.db.get_value("Wallet", self.wallet, "gl_liability_account")
        if not liability:
            frappe.throw(
                _("Wallet {0}: gl_liability_account not set.").format(self.wallet),
                title=_("Wallet GL Account Missing"),
            )

        cost_center = frappe.db.get_value("Company", self.company, "cost_center")

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "posting_date": self.posting_date,
            "company": self.company,
            "user_remark": self.remarks or f"Wallet Deposit {self.name}",
            "cheque_no": self.reference_no or self.name,
            "cheque_date": self.posting_date,
            "custom_source_doctype": "Wallet Deposit",
            "custom_source_name": self.name,
            "accounts": [
                {
                    "account": self.bank_or_cash_account,
                    "debit_in_account_currency": flt(self.amount),
                    "cost_center": cost_center,
                },
                {
                    "account": liability,
                    "credit_in_account_currency": flt(self.amount),
                    "party_type": "Customer",
                    "party": self.customer,
                    "cost_center": cost_center,
                },
            ],
        })
        je.flags.ignore_permissions = True
        je.insert(ignore_permissions=True)
        je.submit()
        return je.name

    # ── on_cancel ────────────────────────────────────────────────────

    def _reverse_wallet_transaction(self):
        if not self.wallet_transaction_ref:
            return
        from polemarch.polemarch_trading import wallet as wallet_engine
        wallet_engine.reverse(
            self.wallet_transaction_ref,
            remarks=f"Reversed on Wallet Deposit {self.name} cancel",
        )

    def _cancel_journal_entry(self):
        from polemarch.polemarch_trading.accounting import cancel_journal_entry_for_source
        cancel_journal_entry_for_source("Wallet Deposit", self.name)
