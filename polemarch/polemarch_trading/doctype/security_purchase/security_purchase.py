"""Security Purchase — direct, ERPNext-bypassing proprietary share acquisition.

Replaces the Purchase-Invoice-with-Polemarch-Item flow. A submitted Security
Purchase posts a Journal Entry for the GL leg and mints one Investment
Holding (keyed by Security, not Item) for the inventory leg. No Items,
no Item Tax Templates, no India-Compliance GST validation.

Lifecycle
---------
on_submit:
    1. Compute amount = qty * rate; validate accounts exist.
    2. Post Journal Entry:
        DR  cost_to_account            (default Securities Inventory - Trading)
        CR  paid_from_account          (Supplier creditor or Bank)
       Tagged with custom_source_doctype = "Security Purchase",
                   custom_source_name   = self.name
       so the daily audit job + reverse-on-cancel can find it.
    3. Create Investment Holding:
        security                = self.security
        company                 = self.company
        acquisition_date        = self.posting_date
        qty_acquired            = self.qty
        cost_basis_per_unit     = self.rate
        purchase_reference      = "Security Purchase"
        purchase_reference_link = self.name
       The Holding's `before_insert` controller stamps classification =
       Unallocated + classification_deadline = creation + 2 working days
       per the v0_8_0 classification engine.
    4. db_set journal_entry_ref + investment_holding_ref back on self.

on_cancel:
    1. If the linked Investment Holding has been touched (qty_disposed > 0
       or qty_reserved > 0), throw — operator must reverse the disposal
       first; we don't silently leave dangling inventory.
    2. Delete the Holding.
    3. Cancel the Journal Entry (ERPNext auto-reverses GL).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class SecurityPurchase(Document):
    def validate(self):
        self._compute_amount()
        self._validate_qty_rate()
        self._validate_security_tradable()
        self._default_accounts()

    def on_submit(self):
        je_name = self._post_journal_entry()
        holding_name = self._create_investment_holding()
        # db_set avoids re-running validate; update_modified=False so the
        # submit timestamp doesn't drift.
        self.db_set("journal_entry_ref", je_name, update_modified=False)
        self.db_set("investment_holding_ref", holding_name, update_modified=False)

    def on_cancel(self):
        self._guard_holding_untouched()
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

    def _default_accounts(self):
        """If the operator leaves accounts blank, pick sensible defaults.
        Both are still `reqd=1` in the JSON, so this only ever runs when the
        user explicitly cleared a fetched default — useful for the API path
        where we construct the doc programmatically. Account-name lookup
        goes through `account_name` (not `name`) because ERPNext composes
        Account.name as `<account_number> - <account_name> - <abbr>` when
        an account_number is set, so the literal string won't match."""
        if not self.cost_to_account:
            self.cost_to_account = _resolve_account_by_name(
                self.company, "Securities Inventory - Trading"
            )
        if not self.paid_from_account and self.supplier:
            default_payable = frappe.db.get_value(
                "Company", self.company, "default_payable_account"
            )
            if default_payable:
                self.paid_from_account = default_payable

    # ── on_submit ────────────────────────────────────────────────────

    def _post_journal_entry(self) -> str:
        from polemarch.polemarch_trading.accounting import _existing_je_for_source

        existing = _existing_je_for_source("Security Purchase", self.name)
        if existing:
            # Idempotent — submit was retried. Reuse the prior JE.
            return existing

        cost_center = self.cost_center or _company_default_cost_center(self.company)

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
                {
                    "account": self.paid_from_account,
                    "credit_in_account_currency": self.amount,
                    "party_type": "Supplier" if _is_payable_account(self.paid_from_account) else None,
                    "party": self.supplier if _is_payable_account(self.paid_from_account) else None,
                    "cost_center": cost_center,
                },
            ],
        })
        je.flags.ignore_permissions = True
        je.insert(ignore_permissions=True)
        je.submit()
        return je.name

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

        # Carry the operator's classification choice through to the new
        # Holding. Default is Unallocated (the InvestmentHolding.before_insert
        # hook starts the 2-working-day timer in that case). If the operator
        # picked Stock in Trade or Investment, stamp classified_by /
        # classified_on so audits show this was a deliberate at-purchase
        # decision, not the auto-classifier.
        classification = (
            getattr(self, "intended_classification", None) or "Unallocated"
        )
        holding_fields = {
            "doctype": "Investment Holding",
            "security": self.security,
            # `item` stays on the JSON until the v0_9_0 drop patch ships;
            # leave it NULL — the Custom Field `security` is the new identity.
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
            holding_fields["classified_on"] = frappe.utils.now_datetime()

        holding = frappe.get_doc(holding_fields)
        holding.flags.ignore_permissions = True
        holding.insert(ignore_permissions=True)
        return holding.name

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
