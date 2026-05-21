"""Security Sale — direct, ERPNext-bypassing proprietary share disposal.

Replaces the Sales-Invoice-with-Polemarch-Item flow. A submitted Security
Sale:

  1. FIFO-consumes Investment Holdings filtered by `from_classification`
     (Stock in Trade or Investment) and `security` — never mixed.
  2. Creates + submits one Investment Disposal carrying the consumed lots
     and the LTCG / STCG breakdown (the canonical capital-gains audit row).
  3. Posts two Journal Entries:
       - Revenue JE   : DR paid_to_account, CR revenue_account
                        (gross sale proceeds)
       - COGS JE      : DR Trading COGS,    CR <inventory bucket>
                        (cost recognition, routed by classification)
     Both JEs carry custom_source_doctype = "Security Sale" so audits +
     reverse-on-cancel can find them.
  4. db_set's the three back-link fields on self.

No Items, no Sales Invoice, no India Compliance GST validation. The
Investment Disposal continues to be the source of truth for per-lot
capital-gains tracking — Security Sale is the operator-facing input
that drives it.

On cancel:
  Reverse JEs (Revenue + COGS) via cancel_journal_entry_for_source,
  then cancel the Investment Disposal (its own on_cancel decrements
  qty_disposed on each Holding, restoring inventory).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class SecuritySale(Document):
    def validate(self):
        self._compute_amount()
        self._validate_qty_rate()
        self._validate_security_tradable()
        self._default_accounts()

    def on_submit(self):
        plan = self._fifo_consume()
        if not plan:
            frappe.throw(
                _(
                    "No open {0} Holdings available for Security {1} (Company {2}). "
                    "Either no inventory exists or it's already reserved/disposed."
                ).format(self.from_classification, self.security, self.company)
            )

        # If FIFO couldn't fully cover the qty, fail loud — partial fills are
        # not safe to silently accept (the operator likely picked the wrong
        # classification or rate).
        covered = sum(flt(p.qty) for p in plan)
        if covered < flt(self.qty):
            frappe.throw(
                _(
                    "Insufficient {0} inventory for Security {1}: requested {2}, "
                    "available {3}."
                ).format(self.from_classification, self.security, self.qty, covered)
            )

        disposal_name = self._create_investment_disposal(plan)
        cogs_je = self._post_cogs_journal_entry(disposal_name)
        revenue_je = self._post_revenue_journal_entry()

        self.db_set("investment_disposal_ref", disposal_name, update_modified=False)
        self.db_set("cogs_journal_entry_ref", cogs_je, update_modified=False)
        self.db_set("revenue_journal_entry_ref", revenue_je, update_modified=False)

    def on_cancel(self):
        # Order: reverse JEs FIRST (they don't depend on the Disposal), then
        # cancel the Disposal (which decrements qty_disposed on each Holding
        # via its own on_cancel).
        self._cancel_journal_entries()
        self._cancel_investment_disposal()

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

    def _default_accounts(self):
        """ERPNext composes Account.name as `<account_number> - <account_name>
        - <abbr>` when account_number is set, so the literal-string compose
        won't match a populated CoA. Resolve via `account_name` instead."""
        if not self.revenue_account:
            self.revenue_account = _resolve_account_by_name(
                self.company, "Trading Revenue - Securities"
            )
        if not self.paid_to_account:
            default_receivable = frappe.db.get_value(
                "Company", self.company, "default_receivable_account"
            )
            if default_receivable:
                self.paid_to_account = default_receivable

    # ── on_submit ────────────────────────────────────────────────────

    def _fifo_consume(self):
        """Plan a FIFO consumption against open Holdings. Non-mutating —
        the Investment Disposal's on_submit is what actually bumps
        qty_disposed via _apply_to_holdings(direction=+1)."""
        from polemarch.polemarch_trading import fifo as fifo_engine

        return fifo_engine.consume(
            security=self.security,
            company=self.company,
            classification=self.from_classification,
            qty_to_sell=flt(self.qty),
            sale_date=getdate(self.posting_date),
        )

    def _create_investment_disposal(self, plan) -> str:
        """Build + submit one Investment Disposal from the FIFO plan.

        Investment Disposal is submittable; its on_submit calls
        _apply_to_holdings(+1) which writes the qty_disposed updates.
        We don't post the cost JE through Investment Disposal — Security
        Sale owns that posting directly via _post_cogs_journal_entry below.
        """
        disposal = frappe.get_doc({
            "doctype": "Investment Disposal",
            "disposal_date": self.posting_date,
            "company": self.company,
            "customer": self.customer,
            "sales_invoice": None,  # not coming from an SI in this flow
            "polemarch_security_sale": self.name,  # Custom Field, set in v0_9_0 patch
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

    def _post_cogs_journal_entry(self, disposal_name) -> str:
        """Reuse the existing cost-recognition poster — it builds DR COGS
        CR inventory(bucket) keyed on each lot's classification. Idempotent
        on (custom_source_doctype, custom_source_name)."""
        from polemarch.polemarch_trading import accounting as accounting_engine

        disposal = frappe.get_doc("Investment Disposal", disposal_name)
        # The accounting helper keys the JE on (disposal.doctype, disposal.name)
        # — that's correct: the COGS leg belongs to the Disposal, not the Sale.
        return accounting_engine.post_cost_recognition_je_for_disposal(disposal) or ""

    def _post_revenue_journal_entry(self) -> str:
        from polemarch.polemarch_trading.accounting import _existing_je_for_source

        existing = _existing_je_for_source("Security Sale", self.name)
        if existing:
            return existing

        cost_center = self.cost_center or _company_default_cost_center(self.company)
        is_receivable = _is_receivable_account(self.paid_to_account)

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
                {
                    "account": self.paid_to_account,
                    "debit_in_account_currency": self.amount,
                    "party_type": "Customer" if is_receivable else None,
                    "party": self.customer if is_receivable else None,
                    "cost_center": cost_center,
                },
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

    # ── on_cancel ────────────────────────────────────────────────────

    def _cancel_journal_entries(self):
        from polemarch.polemarch_trading.accounting import cancel_journal_entry_for_source
        cancel_journal_entry_for_source("Security Sale", self.name)
        if self.investment_disposal_ref:
            # Cancel the COGS JE — keyed on the Disposal, not the Sale.
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


def _is_receivable_account(account: str) -> bool:
    return frappe.db.get_value("Account", account, "account_type") == "Receivable"


def _resolve_account_by_name(company: str, account_name: str):
    """Look up an Account by its `account_name` field. ERPNext composes
    Account.name as `<account_number> - <account_name> - <abbr>` when
    account_number is set, so a literal compose by abbr suffix won't
    match. Returns the canonical Account.name or None."""
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": account_name,
            "disabled": 0,
        },
        "name",
    )
