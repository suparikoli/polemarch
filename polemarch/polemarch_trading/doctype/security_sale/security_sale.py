"""Security Sale — Polemarch disposes inventory.

Phase 12 generalises the doctype: instead of customer-only, the Sale now
takes a (party_type, party) pair plus a `payment_method`. Combinations:

  party_type=Customer, payment_method=Customer Wallet
      Retail customer is buying from Polemarch through their Polemarch
      wallet. FIFO consumes Polemarch's Stock-in-Trade inventory, a
      proprietary Investment Disposal records the gain, the customer
      gets a new customer-owned Investment Holding at trade price, and
      the customer's wallet is debited (Buy Settlement).

  party_type=Customer, payment_method=Bank | Cash | Default Receivable
      Same as above but the cash settles outside the wallet. Bank /
      Cash credits Polemarch immediately; Default Receivable defers
      via the customer's AR ledger.

  party_type=Supplier, payment_method=Bank | Cash | Default Receivable
      Block sale back to a counterparty (rare). NO customer Holding
      gets minted — the Sale doesn't change the counterparty's books.

Always posts:
  - One Revenue JE: DR <payment_account>, CR <revenue_account>
  - One Cost-Recognition JE: DR Trading COGS, CR <inventory bucket>
    (reuses accounting.post_cost_recognition_je_for_disposal)

Legacy `customer` / `paid_to_account` fields stay populated via
back-compat sync in validate so old reports don't break.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime


_VALID_PAYMENT_METHODS_FOR_CUSTOMER = {
    "Default Receivable",
    "Bank",
    "Customer Wallet",
    "Cash",
}
_VALID_PAYMENT_METHODS_FOR_SUPPLIER = {"Default Receivable", "Bank", "Cash"}


class SecuritySale(Document):
    def validate(self):
        self._compute_amount()
        self._validate_qty_rate()
        self._validate_security_tradable()
        self._validate_party_payment_combo()
        self._default_accounts()
        self._sync_legacy_fields()

    def on_submit(self):
        plan = self._fifo_consume()
        if not plan:
            frappe.throw(
                _(
                    "No open {0} Holdings available for Security {1} (Company {2}). "
                    "Either no inventory exists or it's already reserved/disposed."
                ).format(self.from_classification, self.security, self.company)
            )

        # Partial fills aren't safe to silently accept — the operator either
        # picked the wrong classification or wants the sale split.
        covered = sum(flt(p.qty) for p in plan)
        if covered + 0.0001 < flt(self.qty):
            frappe.throw(
                _(
                    "Insufficient {0} inventory for Security {1}: requested {2}, "
                    "available {3}."
                ).format(self.from_classification, self.security, self.qty, covered)
            )

        disposal_name = self._create_investment_disposal(plan)
        customer_holding = self._mint_customer_holding_if_any()
        cogs_je = self._post_cogs_journal_entry(disposal_name)
        wallet_txn = self._post_wallet_transaction_if_any()
        revenue_je = self._post_revenue_journal_entry()

        self.db_set("investment_disposal_ref", disposal_name, update_modified=False)
        self.db_set("cogs_journal_entry_ref", cogs_je, update_modified=False)
        self.db_set("revenue_journal_entry_ref", revenue_je, update_modified=False)
        if customer_holding and hasattr(self, "customer_holding_ref"):
            self.db_set("customer_holding_ref", customer_holding, update_modified=False)
        if wallet_txn and hasattr(self, "wallet_transaction_ref"):
            self.db_set("wallet_transaction_ref", wallet_txn, update_modified=False)

    def on_cancel(self):
        # Reverse JEs and wallet first (no inventory dependency), then
        # cancel the Disposal (its on_cancel restores qty_disposed on the
        # consumed Holdings). Finally delete the customer Holding if any.
        self._reverse_wallet_transaction_if_any()
        self._cancel_journal_entries()
        self._cancel_investment_disposal()
        self._delete_customer_holding_if_any()

    # ── validate-time ────────────────────────────────────────────────

    def _compute_amount(self):
        self.amount = flt(self.qty) * flt(self.rate)

    def _validate_qty_rate(self):
        if flt(self.qty) <= 0:
            frappe.throw(_("Qty must be greater than zero."))
        if flt(self.rate) < 0:
            frappe.throw(_("Rate cannot be negative."))

    def _validate_security_tradable(self):
        sec = frappe.db.get_value(
            "Security", self.security, ["active", "tradable"], as_dict=True
        )
        if not sec:
            frappe.throw(_("Security {0} does not exist.").format(self.security))
        if not sec.active:
            frappe.throw(_("Security {0} is inactive; cannot sell.").format(self.security))
        if not sec.tradable:
            frappe.throw(
                _("Security {0} is not currently tradable.").format(self.security)
            )

    def _validate_party_payment_combo(self):
        if self.party_type == "Customer":
            if self.payment_method not in _VALID_PAYMENT_METHODS_FOR_CUSTOMER:
                frappe.throw(
                    _(
                        "Customer parties accept Default Receivable, Bank, "
                        "Customer Wallet, or Cash."
                    ),
                    title=_("Invalid Payment Method"),
                )
        elif self.party_type == "Supplier":
            if self.payment_method not in _VALID_PAYMENT_METHODS_FOR_SUPPLIER:
                frappe.throw(
                    _(
                        "Suppliers don't have Customer Wallets. "
                        "Pick Default Receivable, Bank, or Cash."
                    ),
                    title=_("Invalid Payment Method"),
                )

    def _default_accounts(self):
        if not self.revenue_account:
            self.revenue_account = _resolve_account_by_name(
                self.company, "Trading Revenue - Securities"
            )
        if self.payment_account:
            return

        if self.payment_method == "Customer Wallet" and self.party_type == "Customer":
            self.payment_account = _wallet_gl_account_for_customer(self.party)
        elif self.payment_method == "Bank":
            self.payment_account = _company_default_bank_account(self.company)
        elif self.payment_method == "Cash":
            self.payment_account = _company_default_cash_account(self.company)
        else:  # Default Receivable
            self.payment_account = frappe.db.get_value(
                "Company", self.company, "default_receivable_account"
            )

    def _sync_legacy_fields(self):
        if self.party_type == "Customer":
            self.customer = self.party
        else:
            self.customer = None
        self.paid_to_account = self.payment_account

    # ── on_submit — Disposal + customer Holding ──────────────────────

    def _fifo_consume(self):
        """Plan a FIFO consumption against Polemarch's PROPRIETARY Holdings
        (customer_filter=None). Non-mutating — the Investment Disposal's
        on_submit is what actually bumps qty_disposed."""
        from polemarch.polemarch_trading import fifo as fifo_engine

        return fifo_engine.consume(
            security=self.security,
            company=self.company,
            classification=self.from_classification,
            qty_to_sell=flt(self.qty),
            sale_date=getdate(self.posting_date),
            customer_filter=None,  # always proprietary inventory
        )

    def _create_investment_disposal(self, plan) -> str:
        disposal = frappe.get_doc({
            "doctype": "Investment Disposal",
            "security": self.security,
            "disposal_date": self.posting_date,
            "company": self.company,
            "customer": self.party if self.party_type == "Customer" else None,
            "sales_invoice": None,
            "polemarch_security_sale": self.name,  # Custom Field (v0_9_0)
            "lots": [
                {
                    "holding": entry.holding,
                    "qty_consumed": entry.qty,
                    "sale_price_per_unit": flt(self.rate),
                }
                for entry in plan
            ],
        })
        disposal.flags.ignore_permissions = True
        disposal.insert(ignore_permissions=True)
        disposal.submit()
        return disposal.name

    def _mint_customer_holding_if_any(self):
        """Customer-Buy via Security Sale: mint a customer-owned Holding at
        trade price. Supplier-side sales don't mint anything on the
        counterparty side."""
        if self.party_type != "Customer":
            return None
        existing = frappe.db.get_value(
            "Investment Holding",
            {
                "purchase_reference": "Security Sale",
                "purchase_reference_link": self.name,
                "customer": self.party,
            },
            "name",
        )
        if existing:
            return existing

        holding = frappe.get_doc({
            "doctype": "Investment Holding",
            "security": self.security,
            "customer": self.party,
            "company": self.company,
            "acquisition_date": getdate(self.posting_date),
            "qty_acquired": flt(self.qty),
            "cost_basis_per_unit": flt(self.rate),
            "purchase_reference": "Security Sale",
            "purchase_reference_link": self.name,
            # Customer-owned shares default to Investment classification —
            # retail buyers hold for capital appreciation. Stamp the
            # at-purchase decision via classified_by / classified_on.
            "classification": "Investment",
            "classified_on": now_datetime(),
            "classified_by": frappe.session.user,
        })
        holding.flags.ignore_permissions = True
        holding.insert(ignore_permissions=True)
        return holding.name

    def _delete_customer_holding_if_any(self):
        if not hasattr(self, "customer_holding_ref") or not self.customer_holding_ref:
            # Lookup by source reference as fallback (back-compat)
            existing = frappe.db.get_value(
                "Investment Holding",
                {
                    "purchase_reference": "Security Sale",
                    "purchase_reference_link": self.name,
                },
                "name",
            )
            if not existing:
                return
            target = existing
        else:
            target = self.customer_holding_ref
        if not frappe.db.exists("Investment Holding", target):
            return
        # Guard: don't delete if the customer has already disposed any of it.
        h = frappe.db.get_value(
            "Investment Holding",
            target,
            ["qty_disposed", "qty_reserved"],
            as_dict=True,
        )
        if h and (flt(h.qty_disposed) > 0 or flt(h.qty_reserved) > 0):
            frappe.throw(
                _(
                    "Cannot cancel Sale {0}: customer Holding {1} already has "
                    "qty_disposed={2} or qty_reserved={3}."
                ).format(self.name, target, h.qty_disposed, h.qty_reserved)
            )
        frappe.delete_doc(
            "Investment Holding", target, ignore_permissions=True, force=True
        )

    # ── on_submit — JEs ──────────────────────────────────────────────

    def _post_cogs_journal_entry(self, disposal_name) -> str:
        """DR Trading COGS, CR inventory bucket — same shape regardless of
        payment_method. The accounting helper is keyed on the Disposal."""
        from polemarch.polemarch_trading import accounting as accounting_engine

        disposal = frappe.get_doc("Investment Disposal", disposal_name)
        return accounting_engine.post_cost_recognition_je_for_disposal(disposal) or ""

    def _post_revenue_journal_entry(self) -> str:
        from polemarch.polemarch_trading.accounting import _existing_je_for_source

        existing = _existing_je_for_source("Security Sale", self.name)
        if existing:
            return existing

        cost_center = self.cost_center or _company_default_cost_center(self.company)

        debit_line = {
            "account": self.payment_account,
            "debit_in_account_currency": self.amount,
            "cost_center": cost_center,
        }
        # ERPNext requires party_type+party on ANY Receivable / Payable
        # account row (not just Default Receivable). Customer Wallet
        # Liability is account_type=Payable, so wallet rails get tagged
        # too. Bank / Cash accounts don't need party tagging.
        if _is_receivable_or_payable(self.payment_account) and self.party_type and self.party:
            debit_line["party_type"] = self.party_type
            debit_line["party"] = self.party

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "posting_date": self.posting_date,
            "company": self.company,
            "user_remark": (
                self.remarks
                or f"Polemarch Security Sale {self.name} — {self.qty} × {self.security} @ {self.rate}"
            ),
            "custom_source_doctype": "Security Sale",
            "custom_source_name": self.name,
            "accounts": [
                debit_line,
                {
                    "account": self.revenue_account,
                    "credit_in_account_currency": self.amount,
                    "cost_center": cost_center,
                },
            ],
        })
        je.flags.ignore_permissions = True
        je.insert(ignore_permissions=True)
        je.submit()
        return je.name

    # ── on_submit — Wallet (customer-side) ───────────────────────────

    def _post_wallet_transaction_if_any(self):
        if self.payment_method != "Customer Wallet":
            return None
        if self.party_type != "Customer":
            return None

        from polemarch.polemarch_trading import wallet as wallet_engine

        wallet_name = f"WAL-{self.party}"
        if not frappe.db.exists("Wallet", wallet_name):
            frappe.throw(
                _("Customer {0} has no Wallet ({1}).").format(self.party, wallet_name),
                title=_("Wallet Missing"),
            )
        # Customer is buying from Polemarch — debit their wallet by net amount.
        # Buy Settlement direction=Debit is (-1, 0, -1) post-Phase-10 fix —
        # decreases available and total directly (no Reservation Release pre-
        # step because this is an instant flow, not the Trade Order lifecycle).
        return wallet_engine.apply_delta(
            wallet=wallet_name,
            txn_type="Buy Settlement",
            direction="Debit",
            amount=flt(self.amount),
            reference_doctype="Security Sale",
            reference_name=self.name,
            idempotency_key=f"sec-sale-settle:{self.name}",
            remarks=f"Buy Settlement for Security Sale {self.name}",
        )

    def _reverse_wallet_transaction_if_any(self):
        if not hasattr(self, "wallet_transaction_ref") or not self.wallet_transaction_ref:
            return
        from polemarch.polemarch_trading import wallet as wallet_engine
        wallet_engine.reverse(
            self.wallet_transaction_ref,
            remarks=f"Reversed on Security Sale {self.name} cancel",
        )

    # ── on_cancel ────────────────────────────────────────────────────

    def _cancel_journal_entries(self):
        from polemarch.polemarch_trading.accounting import cancel_journal_entry_for_source
        cancel_journal_entry_for_source("Security Sale", self.name)
        if self.investment_disposal_ref:
            cancel_journal_entry_for_source(
                "Investment Disposal", self.investment_disposal_ref
            )

    def _cancel_investment_disposal(self):
        if not self.investment_disposal_ref:
            return
        if not frappe.db.exists("Investment Disposal", self.investment_disposal_ref):
            return
        disposal = frappe.get_doc("Investment Disposal", self.investment_disposal_ref)
        if disposal.docstatus == 1:
            disposal.flags.ignore_permissions = True
            disposal.cancel()


# ── helpers ──────────────────────────────────────────────────────────


def _company_default_cost_center(company: str):
    return frappe.db.get_value("Company", company, "cost_center")


def _company_default_bank_account(company: str):
    direct = frappe.db.get_value("Company", company, "default_bank_account")
    if direct:
        return direct
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Bank", "disabled": 0, "is_group": 0},
        "name",
        order_by="creation ASC",
    )


def _company_default_cash_account(company: str):
    direct = frappe.db.get_value("Company", company, "default_cash_account")
    if direct:
        return direct
    return frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Cash", "disabled": 0, "is_group": 0},
        "name",
        order_by="creation ASC",
    )


def _wallet_gl_account_for_customer(customer: str):
    wallet_name = f"WAL-{customer}"
    return frappe.db.get_value("Wallet", wallet_name, "gl_liability_account")


def _is_receivable_account(account: str) -> bool:
    return frappe.db.get_value("Account", account, "account_type") == "Receivable"


def _is_receivable_or_payable(account: str) -> bool:
    return frappe.db.get_value("Account", account, "account_type") in (
        "Receivable", "Payable"
    )


def _resolve_account_by_name(company: str, account_name: str):
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": account_name,
            "disabled": 0,
        },
        "name",
    )
