"""Security Purchase — Polemarch acquires inventory.

Phase 12 generalises the doctype: instead of supplier-only, the Purchase
now takes a (party_type, party) pair plus a `payment_method`. The four
combinations the operator typically picks:

  party_type=Supplier, payment_method=Default Payable
      Classic off-market proprietary acquisition. JE defers cash
      settlement via Creditors; pay later through ERPNext Payment Entry.

  party_type=Supplier, payment_method=Bank | Cash
      Polemarch pays the supplier directly. JE credits the chosen
      bank / cash account immediately.

  party_type=Customer, payment_method=Customer Wallet
      A Polemarch customer is selling shares back to us. FIFO consumes
      the customer's Investment Holdings (their Disposal records their
      capital gains). Polemarch mints proprietary Stock-in-Trade
      inventory at trade price. A Wallet Transaction credits the
      customer's wallet (Sell Payout) for the trade value.

  party_type=Customer, payment_method=Bank | Cash | Default Payable
      Same as above but Polemarch settles cash outside the wallet —
      via direct bank payment, cash, or deferred against the customer's
      payable ledger.

Either way the on_submit posts ONE Journal Entry (DR Securities Inventory,
CR <payment_account>) tagged with `custom_source_doctype=Security Purchase`
so audits + cancel-on-source can reverse it.

The legacy `supplier` / `paid_from_account` fields stay populated via the
back-compat sync in `validate` so old reports don't break.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime


_VALID_PAYMENT_METHODS_FOR_SUPPLIER = {"Default Payable", "Bank", "Cash"}
_VALID_PAYMENT_METHODS_FOR_CUSTOMER = {
    "Default Payable",
    "Bank",
    "Customer Wallet",
    "Cash",
}


class SecurityPurchase(Document):
    def validate(self):
        self._compute_amount()
        self._validate_qty_rate()
        self._validate_security_tradable()
        self._validate_party_payment_combo()
        self._default_accounts()
        self._sync_legacy_fields()

    def on_submit(self):
        # Order matters: if Customer is selling to us, consume their Holdings
        # FIRST (creates the Disposal, decrements customer's qty_remaining),
        # THEN mint our new proprietary Holding, THEN post the JE + wallet
        # txn. The Disposal write needs to land before the new Holding so
        # FIFO consistency holds for any concurrent reads.
        disposal_name = self._consume_customer_holdings_if_any()
        holding_name = self._create_investment_holding()
        wallet_txn = self._post_wallet_transaction_if_any()
        je_name = self._post_journal_entry()

        self.db_set("journal_entry_ref", je_name, update_modified=False)
        self.db_set("investment_holding_ref", holding_name, update_modified=False)
        if disposal_name and hasattr(self, "customer_disposal_ref"):
            self.db_set("customer_disposal_ref", disposal_name, update_modified=False)
        if wallet_txn and hasattr(self, "wallet_transaction_ref"):
            self.db_set("wallet_transaction_ref", wallet_txn, update_modified=False)

    def on_cancel(self):
        self._guard_holding_untouched()
        self._reverse_wallet_transaction_if_any()
        self._cancel_customer_disposal_if_any()
        self._delete_investment_holding()
        self._cancel_journal_entry()

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
            frappe.throw(_("Security {0} is inactive; cannot purchase.").format(self.security))
        if not sec.tradable:
            frappe.throw(
                _("Security {0} is not currently tradable.").format(self.security)
            )

    def _validate_party_payment_combo(self):
        if self.party_type == "Supplier":
            if self.payment_method not in _VALID_PAYMENT_METHODS_FOR_SUPPLIER:
                frappe.throw(
                    _(
                        "Suppliers don't have Customer Wallets. "
                        "Pick Default Payable, Bank, or Cash."
                    ),
                    title=_("Invalid Payment Method"),
                )
        elif self.party_type == "Customer":
            if self.payment_method not in _VALID_PAYMENT_METHODS_FOR_CUSTOMER:
                frappe.throw(
                    _(
                        "Customer parties accept Default Payable, Bank, "
                        "Customer Wallet, or Cash."
                    ),
                    title=_("Invalid Payment Method"),
                )

    def _default_accounts(self):
        """Auto-fill `cost_to_account` and `payment_account` if the operator
        left them blank. Account-name lookup goes through `account_name`
        (not the composite `name`) because ERPNext composes Account.name as
        `<account_number> - <account_name> - <abbr>` when account_number is
        set."""
        if not self.cost_to_account:
            self.cost_to_account = _resolve_account_by_name(
                self.company, "Securities Inventory - Trading"
            )

        if self.payment_account:
            return

        if self.payment_method == "Customer Wallet" and self.party_type == "Customer":
            self.payment_account = _wallet_gl_account_for_customer(self.party)
        elif self.payment_method == "Bank":
            self.payment_account = _company_default_bank_account(self.company)
        elif self.payment_method == "Cash":
            self.payment_account = _company_default_cash_account(self.company)
        else:  # Default Payable
            self.payment_account = frappe.db.get_value(
                "Company", self.company, "default_payable_account"
            )

    def _sync_legacy_fields(self):
        """Keep the legacy `supplier` / `paid_from_account` columns populated
        from the new (party_type, party, payment_account) tuple so any
        external reports or sync jobs that still read the old fields don't
        break overnight."""
        if self.party_type == "Supplier":
            self.supplier = self.party
        else:
            self.supplier = None
        self.paid_from_account = self.payment_account

    # ── on_submit — Customer-Sell branch ─────────────────────────────

    def _consume_customer_holdings_if_any(self):
        """Only fires when party_type=Customer — the customer is the seller.
        FIFO consumes their Investment-class Holdings, creates a customer
        Disposal that tracks per-lot LTCG/STCG. Polemarch's books don't
        recognise gain/loss here (that's the customer's tax issue); the
        Disposal exists purely as the customer's audit trail."""
        if self.party_type != "Customer":
            return None

        from polemarch.polemarch_trading import fifo as fifo_engine

        plan = fifo_engine.consume(
            security=self.security,
            company=self.company,
            classification="Investment",
            qty_to_sell=flt(self.qty),
            sale_date=getdate(self.posting_date),
            customer_filter=self.party,
        )
        if not plan:
            frappe.throw(
                _(
                    "Customer {0} has no consumable Investment Holdings of {1} "
                    "(or qty < {2}). Confirm the customer actually owns these shares."
                ).format(self.party, self.security, self.qty),
                title=_("Insufficient Customer Inventory"),
            )

        covered = sum(flt(p.qty) for p in plan)
        if covered + 0.0001 < flt(self.qty):
            frappe.throw(
                _(
                    "Insufficient Investment inventory under customer {0}: "
                    "requested {1}, available {2}."
                ).format(self.party, self.qty, covered),
                title=_("Insufficient Customer Inventory"),
            )

        disposal = frappe.get_doc({
            "doctype": "Investment Disposal",
            "security": self.security,
            "company": self.company,
            "disposal_date": getdate(self.posting_date),
            "sales_invoice": None,
            "customer": self.party,
            "polemarch_security_purchase": self.name
                if frappe.db.has_column(
                    "Investment Disposal", "polemarch_security_purchase"
                )
                else None,
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

    def _cancel_customer_disposal_if_any(self):
        if not hasattr(self, "customer_disposal_ref") or not self.customer_disposal_ref:
            return
        if not frappe.db.exists("Investment Disposal", self.customer_disposal_ref):
            return
        disposal = frappe.get_doc(
            "Investment Disposal", self.customer_disposal_ref
        )
        if disposal.docstatus == 1:
            disposal.flags.ignore_permissions = True
            disposal.cancel()

    # ── on_submit — Holding mint ─────────────────────────────────────

    def _create_investment_holding(self) -> str:
        existing = frappe.db.get_value(
            "Investment Holding",
            {
                "purchase_reference": "Security Purchase",
                "purchase_reference_link": self.name,
                "customer": ["in", [None, ""]],
            },
            "name",
        )
        if existing:
            return existing

        # When party_type=Customer (Polemarch buying from a customer), the
        # new Holding is proprietary Stock-in-Trade by default — we
        # acquired this inventory to resell. The classification picker on
        # the form still wins for Supplier-side purchases.
        if self.party_type == "Customer":
            classification = "Stock in Trade"
        else:
            classification = (
                getattr(self, "intended_classification", None) or "Unallocated"
            )

        holding_fields = {
            "doctype": "Investment Holding",
            "security": self.security,
            "customer": None,  # Proprietary — Polemarch-owned
            "company": self.company,
            "acquisition_date": getdate(self.posting_date),
            "qty_acquired": flt(self.qty),
            "cost_basis_per_unit": flt(self.rate),
            "purchase_reference": "Security Purchase",
            "purchase_reference_link": self.name,
            "classification": classification,
        }
        if classification != "Unallocated":
            holding_fields["classified_by"] = frappe.session.user
            holding_fields["classified_on"] = now_datetime()

        holding = frappe.get_doc(holding_fields)
        holding.flags.ignore_permissions = True
        holding.insert(ignore_permissions=True)
        return holding.name

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
        # Polemarch is buying from the customer; the customer's wallet gets
        # credited (we now owe them this much, increasing balance_total +
        # balance_available). Sell Payout already maps to (+1, 0, +1) in the
        # wallet's delta map.
        return wallet_engine.apply_delta(
            wallet=wallet_name,
            txn_type="Sell Payout",
            direction="Credit",
            amount=flt(self.amount),
            reference_doctype="Security Purchase",
            reference_name=self.name,
            idempotency_key=f"sec-purchase-payout:{self.name}",
            remarks=f"Sell Payout for Security Purchase {self.name}",
        )

    def _reverse_wallet_transaction_if_any(self):
        if not hasattr(self, "wallet_transaction_ref") or not self.wallet_transaction_ref:
            return
        from polemarch.polemarch_trading import wallet as wallet_engine
        wallet_engine.reverse(
            self.wallet_transaction_ref,
            remarks=f"Reversed on Security Purchase {self.name} cancel",
        )

    # ── on_submit — Journal Entry ────────────────────────────────────

    def _post_journal_entry(self) -> str:
        from polemarch.polemarch_trading.accounting import _existing_je_for_source

        existing = _existing_je_for_source("Security Purchase", self.name)
        if existing:
            return existing

        cost_center = self.cost_center or _company_default_cost_center(self.company)

        # Credit-side party tagging: for Default Payable + Supplier, set
        # party_type=Supplier, party=<supplier> so the JE row hits the
        # supplier's AP ledger correctly. For the Wallet rail the credit
        # account is the customer's Wallet Liability GL — no party tag.
        credit_line = {
            "account": self.payment_account,
            "credit_in_account_currency": self.amount,
            "cost_center": cost_center,
        }
        if (
            self.payment_method == "Default Payable"
            and _is_payable_account(self.payment_account)
        ):
            if self.party_type == "Supplier":
                credit_line["party_type"] = "Supplier"
                credit_line["party"] = self.party
            elif self.party_type == "Customer":
                # Customer parties for payable accounts — Polemarch owes
                # the customer outside the wallet (rare but legal).
                credit_line["party_type"] = "Customer"
                credit_line["party"] = self.party

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "posting_date": self.posting_date,
            "company": self.company,
            "user_remark": (
                self.remarks
                or f"Polemarch Security Purchase {self.name} — {self.qty} × {self.security} @ {self.rate}"
            ),
            "custom_source_doctype": "Security Purchase",
            "custom_source_name": self.name,
            "accounts": [
                {
                    "account": self.cost_to_account,
                    "debit_in_account_currency": self.amount,
                    "cost_center": cost_center,
                },
                credit_line,
            ],
        })
        je.flags.ignore_permissions = True
        je.insert(ignore_permissions=True)
        je.submit()
        return je.name

    # ── on_cancel ────────────────────────────────────────────────────

    def _guard_holding_untouched(self):
        if not self.investment_holding_ref:
            return
        h = frappe.db.get_value(
            "Investment Holding",
            self.investment_holding_ref,
            ["qty_disposed", "qty_reserved"],
            as_dict=True,
        )
        if not h:
            return
        if flt(h.qty_disposed) > 0 or flt(h.qty_reserved) > 0:
            frappe.throw(
                _(
                    "Cannot cancel Security Purchase {0}: linked Holding {1} has "
                    "qty_disposed={2} or qty_reserved={3}. Reverse the disposal "
                    "or release the reservation first."
                ).format(
                    self.name,
                    self.investment_holding_ref,
                    h.qty_disposed,
                    h.qty_reserved,
                )
            )

    def _delete_investment_holding(self):
        if not self.investment_holding_ref:
            return
        if not frappe.db.exists("Investment Holding", self.investment_holding_ref):
            return
        frappe.delete_doc(
            "Investment Holding",
            self.investment_holding_ref,
            ignore_permissions=True,
            force=True,
        )

    def _cancel_journal_entry(self):
        from polemarch.polemarch_trading.accounting import cancel_journal_entry_for_source
        cancel_journal_entry_for_source("Security Purchase", self.name)


# ── helpers ──────────────────────────────────────────────────────────


def _company_default_cost_center(company: str):
    return frappe.db.get_value("Company", company, "cost_center")


def _company_default_bank_account(company: str):
    """Return Company.default_bank_account if set, else the first non-disabled
    Bank-typed account."""
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


def _is_payable_account(account: str) -> bool:
    return frappe.db.get_value("Account", account, "account_type") == "Payable"


def _resolve_account_by_name(company: str, account_name: str):
    """Look up an Account by its `account_name` field (not the composite
    `name`). Returns the canonical Account.name suitable for storing in a
    Link field, or None if not found.
    """
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": account_name,
            "disabled": 0,
        },
        "name",
    )
