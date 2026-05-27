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
      A Polemarch customer is selling shares back to us. Polemarch mints
      proprietary Stock-in-Trade inventory at trade price. A Wallet
      Transaction credits the customer's wallet (Sell Payout) for the
      trade value. The Customer Holding snapshot is decremented as a
      side-effect (CRM-only, no GL impact) — Phase 15 split customer
      holdings out of the inventory ledger; we don't track their cost
      basis or capital gains.

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

from polemarch.polemarch_trading.controller_helpers import (
    company_default_bank_account,
    company_default_cash_account,
    company_default_cost_center,
    is_receivable_or_payable,
    resolve_account_by_name,
    reverse_wallet_transactions,
    sever_wallet_transaction_link,
    wallet_gl_account_for_customer,
)


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
        self._validate_accounts_resolved()
        self._sync_legacy_fields()

    def on_submit(self):
        # Phase 15: Customer holdings are CRM data, not Polemarch's ledger.
        # When party_type=Customer, we side-effect their snapshot but don't
        # consume any Investment Holding (those are proprietary-only now).
        holding_name = self._create_investment_holding()
        self._decrement_customer_holding_snapshot()
        wallet_txn = self._post_wallet_transaction_if_any()
        je_name = self._post_journal_entry()

        self.db_set("journal_entry_ref", je_name, update_modified=False)
        self.db_set("investment_holding_ref", holding_name, update_modified=False)
        if wallet_txn and hasattr(self, "wallet_transaction_ref"):
            self.db_set("wallet_transaction_ref", wallet_txn, update_modified=False)

    def before_cancel(self):
        # Frappe's check_links runs BEFORE on_cancel and refuses to cancel a
        # doc with active Dynamic Links pointing at it. Our Wallet Transaction
        # (created by _post_wallet_transaction_if_any) has reference_name=this
        # SP, which would block cancel. We handle that link in on_cancel by
        # posting a reversal + severing the reference, but check_links runs
        # before on_cancel so we'd be too late. Sever the WT link HERE so the
        # link check passes; the reversal still happens in on_cancel below.
        self._sever_wallet_transaction_link_if_any()

    def on_cancel(self):
        self._guard_holding_untouched()
        self._reverse_wallet_transaction_if_any()
        self._restore_customer_holding_snapshot()
        self._delete_investment_holding()
        self._cancel_journal_entry()

    def _sever_wallet_transaction_link_if_any(self):
        """Sever the WT.reference link so Frappe's link check passes;
        stash the WT names for on_cancel to reverse. See
        controller_helpers.sever_wallet_transaction_link for the why."""
        self.flags._pending_wt_reversal = sever_wallet_transaction_link(
            "Security Purchase", self.name
        )

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
        """Auto-fill `cost_to_account` and `payment_account` if the
        operator left them blank."""
        if not self.cost_to_account:
            self.cost_to_account = resolve_account_by_name(
                self.company, "Securities Inventory - Trading"
            )

        if self.payment_account:
            return

        if self.payment_method == "Customer Wallet" and self.party_type == "Customer":
            self.payment_account = wallet_gl_account_for_customer(self.party)
        elif self.payment_method == "Bank":
            self.payment_account = company_default_bank_account(self.company)
        elif self.payment_method == "Cash":
            self.payment_account = company_default_cash_account(self.company)
        else:  # Default Payable
            self.payment_account = frappe.db.get_value(
                "Company", self.company, "default_payable_account"
            )

    def _validate_accounts_resolved(self):
        """If `_default_accounts` couldn't fill cost_to_account or
        payment_account (e.g. company missing `Securities Inventory -
        Trading` or no default_payable_account configured), throw a
        descriptive error here instead of letting Frappe's generic
        mandatory check fire."""
        missing = []
        if not self.cost_to_account:
            missing.append(
                _(
                    "Cost To Account couldn't be auto-resolved — Company {0} is "
                    "missing the `Securities Inventory - Trading` account. "
                    "Run the polemarch CoA seeder or pick an account manually."
                ).format(self.company)
            )
        if not self.payment_account:
            if self.payment_method == "Default Payable":
                missing.append(
                    _(
                        "Payment Account couldn't be auto-resolved — Company {0} "
                        "has no `default_payable_account`. Set it on the Company "
                        "or pick the credit account manually."
                    ).format(self.company)
                )
            elif self.payment_method == "Bank":
                missing.append(
                    _(
                        "Payment Account couldn't be auto-resolved — Company {0} "
                        "has no `default_bank_account` and no Bank-type accounts. "
                        "Set the default or pick a Bank account manually."
                    ).format(self.company)
                )
            elif self.payment_method == "Cash":
                missing.append(
                    _(
                        "Payment Account couldn't be auto-resolved — Company {0} "
                        "has no `default_cash_account` and no Cash-type accounts. "
                        "Set the default or pick a Cash account manually."
                    ).format(self.company)
                )
            elif self.payment_method == "Customer Wallet":
                missing.append(
                    _(
                        "Payment Account couldn't be auto-resolved — Customer {0} "
                        "has no Wallet on Company {1}, or the Wallet has no "
                        "gl_liability_account set."
                    ).format(self.party, self.company)
                )
        if missing:
            frappe.throw("\n".join(missing), title=_("Account Auto-Fill Failed"))

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

    # ── on_submit — Customer Holding snapshot side-effect ─────────────

    def _decrement_customer_holding_snapshot(self):
        """When party_type=Customer (Polemarch buying from a customer),
        decrement their Customer Holding snapshot by the purchased qty.

        Side-effect only — no GL impact, no Investment Disposal. Customer
        holdings are CRM data; Polemarch doesn't track their cost basis
        or capital gains. The snapshot helps Polemarch decide who to
        approach when supply is needed."""
        if self.party_type != "Customer":
            return
        from polemarch.polemarch_trading.doctype.customer_holding.customer_holding import (
            apply_delta,
        )
        apply_delta(
            customer=self.party,
            security=self.security,
            delta_qty=-flt(self.qty),
            source="Security Purchase",
            note_on_drift=f"Triggered by Security Purchase {self.name}",
        )

    def _restore_customer_holding_snapshot(self):
        """On cancel, undo the snapshot decrement."""
        if self.party_type != "Customer":
            return
        from polemarch.polemarch_trading.doctype.customer_holding.customer_holding import (
            apply_delta,
        )
        apply_delta(
            customer=self.party,
            security=self.security,
            delta_qty=flt(self.qty),
            source="Security Purchase",
            note_on_drift=f"Restored by Security Purchase {self.name} cancel",
        )

    # ── on_submit — Holding mint ─────────────────────────────────────

    def _create_investment_holding(self) -> str:
        existing = frappe.db.get_value(
            "Investment Holding",
            {
                "purchase_reference": "Security Purchase",
                "purchase_reference_link": self.name,
            },
            "name",
        )
        if existing:
            return existing

        # Investment Holding is proprietary-only post-Phase-15. When
        # party_type=Customer (Polemarch buying from a customer), the new
        # Holding still goes onto Polemarch's books, defaulting to Stock-
        # in-Trade (we acquired inventory to resell). The classification
        # picker on the form wins for Supplier-side purchases.
        if self.party_type == "Customer":
            classification = "Stock in Trade"
        else:
            classification = (
                getattr(self, "intended_classification", None) or "Unallocated"
            )

        holding_fields = {
            "doctype": "Investment Holding",
            "security": self.security,
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
            # Gap 5 fix: seed a matching child row in the `classifications`
            # Table so IH._compute_classification_rollup doesn't back-sync
            # the legacy `classification` field to "Unallocated" on the
            # very first save. Without this, the Purchase persists
            # intended_classification correctly on the Sale-Purchase doc,
            # but the resulting IH always lands as Unallocated regardless
            # of the operator's pick.
            holding_fields["classifications"] = [
                {
                    "classification": classification,
                    "qty": flt(self.qty),
                    "classified_by": frappe.session.user,
                    "classified_on": now_datetime(),
                    "auto_classified": 0,
                    "notes": f"Seeded at Purchase {self.name}",
                }
            ]

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
        """Reverse Wallet Transaction(s) created on submit. Uses the WT
        names that `before_cancel` stashed on `self.flags`."""
        reverse_wallet_transactions(
            getattr(self.flags, "_pending_wt_reversal", None) or [],
            remarks=f"Reversed on Security Purchase {self.name} cancel",
        )

    # ── on_submit — Journal Entry ────────────────────────────────────

    def _post_journal_entry(self) -> str:
        from polemarch.polemarch_trading.accounting import _existing_je_for_source

        existing = _existing_je_for_source("Security Purchase", self.name)
        if existing:
            return existing

        cost_center = self.cost_center or company_default_cost_center(self.company)

        # Credit-side party tagging: for Default Payable + Supplier, set
        # party_type=Supplier, party=<supplier> so the JE row hits the
        # supplier's AP ledger correctly. For the Wallet rail the credit
        # account is the customer's Wallet Liability GL — no party tag.
        credit_line = {
            "account": self.payment_account,
            "credit_in_account_currency": self.amount,
            "cost_center": cost_center,
        }
        # ERPNext requires party_type+party on ANY Receivable / Payable
        # account row regardless of debit/credit direction. Customer
        # Wallet Liability is account_type=Payable, so wallet rails get
        # tagged too. Bank / Cash accounts don't need party tagging.
        if is_receivable_or_payable(self.payment_account) and self.party_type and self.party:
            credit_line["party_type"] = self.party_type
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


