"""Investment Disposal — one row per (item × sale event).

Created automatically by `polemarch.overrides.sales_invoice.on_submit`
when a Sales Invoice with brand=Polemarch line items is submitted: for
each such line, the FIFO matcher walks oldest-first across open
`Investment Holding` rows, consumes `qty_remaining` until the sale qty
is satisfied, and emits one `Investment Disposal Lot` per consumed
holding. Holding-period math (LTCG vs STCG) is computed PER LOT —
because a single sale of 100 units may touch one lot from 2022 (LTCG)
and one from 2024 (STCG), each carrying a different tax treatment.

The 730-day threshold is the Income Tax Act's long-term cutoff for
unlisted equity (2 years). For listed equity it would be 365 days
(1 year). Polemarch's universe is unlisted / pre-IPO so 730 is the
default; expose as a setting later if you start listing.

Submit semantics: this doc IS submittable. On submit, holdings are
locked (qty_disposed updates committed). On cancel, the consumption
is reversed — qty_disposed decremented, status restored. Implements
the Frappe ledger-doc pattern so capital-gains entries can't be
silently mutated post-fact.

Phase 1 scope: NO automatic Journal Entry posting. The accountant
should manually post:
    DR Bank / Cash               total_sale_value
    CR Investment Asset (CA)     total_cost_basis
    CR Realized Capital Gains    realized_gain   (or DR if loss)
when reconciling the Sales Invoice payment. Phase 2 candidate.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate

LONG_TERM_THRESHOLD_DAYS = 730  # 24 months — unlisted equity cutoff per IT Act


class InvestmentDisposal(Document):
    def validate(self):
        self._compute_per_lot_metrics()
        self._roll_up_totals()

    def on_submit(self):
        self._apply_to_holdings(direction=+1)
        self._post_journal_entry()

    def on_cancel(self):
        self._cancel_journal_entry()
        self._apply_to_holdings(direction=-1)

    # ── derived-field math ────────────────────────────────────────────

    def _compute_per_lot_metrics(self):
        if not self.disposal_date:
            return
        disposal_date = getdate(self.disposal_date)
        for lot in self.lots or []:
            if not lot.holding:
                continue
            # Re-fetch — fetch_from values are only filled on form
            # render, not from server-side appends.
            holding = frappe.db.get_value(
                "Investment Holding",
                lot.holding,
                ["acquisition_date", "cost_basis_per_unit"],
                as_dict=True,
            )
            if not holding:
                continue
            lot.acquisition_date = holding.acquisition_date
            lot.cost_basis_per_unit = flt(holding.cost_basis_per_unit)

            held_days = (disposal_date - getdate(holding.acquisition_date)).days
            lot.holding_period_days = held_days
            lot.is_long_term = 1 if held_days > LONG_TERM_THRESHOLD_DAYS else 0

            qty = flt(lot.qty_consumed)
            lot.cost_basis_amount = qty * lot.cost_basis_per_unit
            lot.sale_amount = qty * flt(lot.sale_price_per_unit)
            lot.realized_gain = lot.sale_amount - lot.cost_basis_amount

    def _roll_up_totals(self):
        total_qty = 0.0
        total_cost = 0.0
        total_sale = 0.0
        ltcg = 0.0
        stcg = 0.0
        for lot in self.lots or []:
            total_qty += flt(lot.qty_consumed)
            total_cost += flt(lot.cost_basis_amount)
            total_sale += flt(lot.sale_amount)
            if lot.is_long_term:
                ltcg += flt(lot.realized_gain)
            else:
                stcg += flt(lot.realized_gain)
        self.total_qty_sold = total_qty
        self.total_cost_basis = total_cost
        self.total_sale_value = total_sale
        self.realized_gain = total_sale - total_cost
        self.ltcg_amount = ltcg
        self.stcg_amount = stcg
        if total_qty:
            self.sale_price_per_unit = total_sale / total_qty

    # ── holding state mutation ────────────────────────────────────────

    def _apply_to_holdings(self, direction: int):
        """direction = +1 on submit (consume), −1 on cancel (release).
        Updates `Investment Holding.qty_disposed` and triggers status
        recompute via the holding's own validate."""
        for lot in self.lots or []:
            if not lot.holding or not lot.qty_consumed:
                continue
            holding = frappe.get_doc("Investment Holding", lot.holding)
            holding.qty_disposed = flt(holding.qty_disposed or 0) + (
                direction * flt(lot.qty_consumed)
            )
            if holding.qty_disposed < 0:
                # Defensive — cancellation can't push below 0. If it
                # would, clamp and warn rather than throw, since the
                # accountant may have already amended other disposals
                # and we don't want a chain of errors.
                holding.qty_disposed = 0
            holding.flags.ignore_permissions = True
            holding.save(ignore_permissions=True)

    # ── Phase 3 auto-JE: cost recognition ────────────────────────────────

    def _post_journal_entry(self):
        """Phase 3a — emit a cost-recognition Journal Entry:

            DR  Trading COGS - Securities
            CR  Securities Inventory - Trading   (Trading-portfolio lots)
            CR  Long-Term Investments            (Investment-portfolio lots)

        Gated by feature flag `AUTO_POST_CAPITAL_GAINS_JE`. Idempotent —
        looks up an existing JE by (custom_source_doctype, custom_source_name).
        Failures are logged but never block the Disposal submit; the daily
        audit job will surface any missing JEs for manual reconciliation.
        """
        try:
            from polemarch.polemarch_trading.feature_flags import is_enabled

            if not is_enabled("AUTO_POST_CAPITAL_GAINS_JE"):
                return

            from polemarch.polemarch_trading import accounting as accounting_engine

            je_name = accounting_engine.post_cost_recognition_je_for_disposal(self)
            if je_name and hasattr(self, "journal_entry_ref"):
                self.db_set("journal_entry_ref", je_name, update_modified=False)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Polemarch Capital Gains JE",
            )

    def _cancel_journal_entry(self):
        """Cancel the paired cost-recognition JE on Disposal cancel.
        ERPNext's JE.cancel() emits reverse GL entries natively.
        """
        try:
            from polemarch.polemarch_trading.feature_flags import is_enabled

            if not is_enabled("AUTO_POST_CAPITAL_GAINS_JE"):
                return

            from polemarch.polemarch_trading import accounting as accounting_engine

            accounting_engine.cancel_journal_entry_for_source(self.doctype, self.name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Polemarch Capital Gains JE Cancel",
            )


# ──────────────────────────────────────────────────────────────────────
# FIFO matcher — used by `polemarch.overrides.sales_invoice.on_submit`
# to build a Disposal from a Sales Invoice line item.
# ──────────────────────────────────────────────────────────────────────


def fifo_consume(item_code: str, company: str, qty_to_sell: float):
    """Walk Investment Holding rows for `item_code` in `company`,
    oldest-first, building a list of (holding_name, qty_consumed)
    pairs that sum to `qty_to_sell`. Returns the list, plus a flag
    indicating whether qty was fully covered.

    Does NOT mutate holdings — the caller (Investment Disposal's
    on_submit) is responsible for committing qty_disposed updates so
    the operation is undoable.
    """
    qty_to_sell = flt(qty_to_sell)
    if qty_to_sell <= 0:
        return [], True

    # Concurrency: row-lock the candidate holdings so two concurrent SI
    # submits for the same (item, company) can't both consume the oldest
    # lot. The lock is released when the calling request commits.
    holdings = frappe.db.sql(
        """
        SELECT name, qty_remaining, acquisition_date
          FROM `tabInvestment Holding`
         WHERE item = %s
           AND company = %s
           AND status IN ('Open', 'Partially Disposed')
         ORDER BY acquisition_date ASC, creation ASC
         FOR UPDATE
        """,
        (item_code, company),
        as_dict=True,
    )

    consumed = []
    remaining = qty_to_sell
    for h in holdings:
        if remaining <= 0:
            break
        avail = flt(h["qty_remaining"])
        if avail <= 0:
            continue
        take = min(avail, remaining)
        consumed.append({"holding": h["name"], "qty": take})
        remaining -= take

    return consumed, remaining <= 0
