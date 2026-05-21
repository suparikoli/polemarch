"""Settlement orchestration — funding, clearing, and failure handling.

`run_pending()` is the hourly scheduler entrypoint. It walks Settlement
Instructions whose `expected_settlement_date <= today` and `settlement_state
= Pending`, then attempts to transition each one to Funded. Actual cash and
DP transfers are out-of-band (handled by ops teams), so the runner is mostly
a state-advancer plus reconciliation helper.

Individual transitions are exposed as `fund / clear / fail` so the API and
back-office UIs can drive them explicitly. The runner is for catch-up only.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime


_BATCH_SIZE = 50


def run_pending() -> dict:
    """Walk Pending Settlement Instructions due today or earlier.

    For Phase 2 we don't auto-fund — that requires bank reconciliation that
    Phase 0 doesn't ship. Instead this job:

      1. Detects stale Pending instructions (>= 3 days past expected date)
         and logs them as Audit-Log entries.
      2. Returns a summary dict {checked, stale} for visibility.
    """
    if not frappe.db.table_exists("Settlement Instruction"):
        return {"checked": 0, "stale": 0}

    today = frappe.utils.today()
    pending = frappe.get_all(
        "Settlement Instruction",
        filters={
            "settlement_state": "Pending",
            "expected_settlement_date": ["<=", today],
            "docstatus": 1,
        },
        fields=["name", "expected_settlement_date", "customer", "trade_order"],
        limit_page_length=_BATCH_SIZE * 10,
    )

    stale = []
    cutoff = frappe.utils.add_days(today, -3)
    for row in pending:
        if row.expected_settlement_date and str(row.expected_settlement_date) <= cutoff:
            stale.append(row)

    if stale:
        frappe.log_error(
            f"Settlement runner: {len(stale)} stale Pending settlements (3+ days past T+N). "
            f"First 20: {[r.name for r in stale[:20]]}",
            "Polemarch Settlement Runner",
        )

    return {"checked": len(pending), "stale": len(stale)}


def fund(settlement_name: str) -> str:
    """Transition Pending → Funded.

    Buy side: the buyer's wallet reservation is converted into a final debit
              (Reservation Release credit + Buy Settlement debit, net=0 on
              reserved+available but moves balance_reserved → 0).
    Sell side: post the seller's wallet credit (Sell Payout) — the customer
               receives the proceeds into their wallet.
    """
    si = frappe.get_doc("Settlement Instruction", settlement_name)
    if si.settlement_state != "Pending":
        frappe.throw(
            _("Settlement {0}: only Pending instructions can be funded (state={1}).").format(
                settlement_name, si.settlement_state
            ),
            title=_("Cannot Fund"),
        )

    order = frappe.get_doc("Trade Order", si.trade_order)
    wallet_txn = None

    if order.side == "Buy" and order.book == "Customer":
        wallet_txn = _convert_reservation_to_final_debit(order)
        si.buyer_wallet_txn = wallet_txn

    elif order.side == "Sell" and order.book == "Customer":
        wallet_txn = _credit_seller_proceeds(order)
        si.seller_wallet_txn = wallet_txn

    si.transition_to("Funded")

    # The Trade Order state machine requires Matched → Settling → Settled.
    # When the API layer drives settlement via `settle_order`, the Settling
    # transition happens explicitly. But back-office / scheduler / runner-
    # driven callers go straight to fund() + clear(), so we advance here so
    # the engine is self-consistent without relying on the API layer.
    if order.order_state == "Matched":
        order.reload()
        order.transition_to("Settling")
    return si.name


def clear(
    settlement_name: str,
    dp_transfer_ref: Optional[str] = None,
    seller_bank_payout_ref: Optional[str] = None,
) -> str:
    """Transition Funded → Cleared. Advances the Trade Order to Settled."""
    si = frappe.get_doc("Settlement Instruction", settlement_name)
    if si.settlement_state != "Funded":
        frappe.throw(
            _("Settlement {0}: only Funded instructions can be cleared (state={1}).").format(
                settlement_name, si.settlement_state
            ),
            title=_("Cannot Clear"),
        )

    si.transition_to(
        "Cleared",
        dp_transfer_ref=dp_transfer_ref,
        seller_bank_payout_ref=seller_bank_payout_ref,
    )

    # Advance Trade Order. For sells, create the Investment Disposal now —
    # at clear-time, not match-time, so reversal stays cheap.
    order = frappe.get_doc("Trade Order", si.trade_order)
    if order.side == "Sell":
        _create_investment_disposal_for_trade_order(order)
        # Release reserved qty (FIFO planner deterministically gives back the
        # same Holdings); the consume side of the equation was just recorded
        # by Investment Disposal which decrements qty_remaining.
        from polemarch.polemarch_trading import matching as matching_engine
        matching_engine._release_sell_holdings(order)

    order.transition_to("Settled")
    return si.name


def fail(settlement_name: str, reason: str) -> str:
    """Transition Funded → Failed. Reverses wallet/SLLE postings; Trade Order
    returns to Matched for admin decision (retry or cancel)."""
    si = frappe.get_doc("Settlement Instruction", settlement_name)
    if si.settlement_state != "Funded":
        frappe.throw(
            _("Settlement {0}: only Funded instructions can be marked Failed (state={1}).").format(
                settlement_name, si.settlement_state
            ),
            title=_("Cannot Fail"),
        )

    from polemarch.polemarch_trading import wallet as wallet_engine

    if si.buyer_wallet_txn:
        wallet_engine.reverse(si.buyer_wallet_txn, remarks=f"Settlement {si.name} failed: {reason}")
    if si.seller_wallet_txn:
        wallet_engine.reverse(si.seller_wallet_txn, remarks=f"Settlement {si.name} failed: {reason}")

    si.transition_to("Failed", reason=reason)
    # Roll the Trade Order back; admin can retry by calling fund() again.
    order = frappe.get_doc("Trade Order", si.trade_order)
    if order.order_state == "Settling":
        order.flags.from_state_transition = True
        order.order_state = "Matched"
        order.db_update()
    return si.name


def on_instruction_cancelled(si) -> None:
    """Called from Settlement Instruction.on_cancel — releases reservations
    so the Trade Order can transition cleanly to Cancelled."""
    order = frappe.get_doc("Trade Order", si.trade_order)
    from polemarch.polemarch_trading import matching as matching_engine

    if order.side == "Buy" and order.book == "Customer":
        matching_engine._release_buy_wallet(order)
    elif order.side == "Sell":
        matching_engine._release_sell_holdings(order)


# ── Internal helpers ────────────────────────────────────────────────────


def _convert_reservation_to_final_debit(order):
    """Buy side at funding: release the Reservation (credit available, debit
    reserved) then post the Buy Settlement (debit reserved, debit total).

    Result on wallet: available unchanged from pre-reservation; reserved 0;
    total = pre-reservation - net_amount. (The customer has effectively paid.)
    """
    from polemarch.polemarch_trading import wallet as wallet_engine

    # Step 1: release the reservation if not already.
    release_idem = f"release:{order.reservation_id}" if order.reservation_id else None
    if release_idem and not frappe.db.exists(
        "Wallet Transaction", {"idempotency_key": release_idem}
    ):
        wallet_engine.apply_delta(
            wallet=f"WAL-{order.customer}",
            txn_type="Reservation Release",
            direction="Credit",
            amount=flt(order.net_amount),
            reference_doctype="Trade Order",
            reference_name=order.name,
            idempotency_key=release_idem,
            remarks=f"Reservation release at settlement for {order.name}",
        )

    # Step 2: post the Buy Settlement debit.
    return wallet_engine.apply_delta(
        wallet=f"WAL-{order.customer}",
        txn_type="Buy Settlement",
        direction="Debit",
        amount=flt(order.net_amount),
        reference_doctype="Trade Order",
        reference_name=order.name,
        idempotency_key=f"buy-settle:{order.name}",
        remarks=f"Buy Settlement for Trade Order {order.name}",
    )


def _credit_seller_proceeds(order):
    """Sell side at funding: credit the seller's wallet with net_amount."""
    from polemarch.polemarch_trading import wallet as wallet_engine

    return wallet_engine.apply_delta(
        wallet=f"WAL-{order.customer}",
        txn_type="Sell Payout",
        direction="Credit",
        amount=flt(order.net_amount),
        reference_doctype="Trade Order",
        reference_name=order.name,
        idempotency_key=f"sell-payout:{order.name}",
        remarks=f"Sell Payout for Trade Order {order.name}",
    )


def _create_investment_disposal_for_trade_order(order) -> Optional[str]:
    """At Clear time, materialise the FIFO consumption as an Investment
    Disposal (with child Investment Disposal Lot rows). The legacy controller
    handles all the math: realized gain, LTCG/STCG split, qty_disposed updates
    on Investment Holding rows, and (if AUTO_POST_CAPITAL_GAINS_JE is enabled)
    the cost-recognition Journal Entry.

    Idempotent: re-runs are skipped if a Disposal already exists for this
    Trade Order.
    """
    existing = frappe.db.get_value(
        "Investment Disposal",
        {"polemarch_trade_order": order.name, "docstatus": ["!=", 2]},
        "name",
    )
    if existing:
        return existing

    from polemarch.polemarch_trading import fifo as fifo_engine
    from polemarch.polemarch_trading import matching as matching_engine

    classification = matching_engine._classification_for_order(order)
    item = matching_engine._item_for_security(order.security)
    plan = fifo_engine.consume(
        item=item,
        company=order.company,
        classification=classification,
        qty_to_sell=flt(order.qty),
        sale_date=getdate(order.posting_date),
        customer_filter=order.customer if order.book == "Customer" else None,
    )
    if not plan:
        return None

    disposal = frappe.get_doc({
        "doctype": "Investment Disposal",
        "item": item,
        "company": order.company,
        "disposal_date": getdate(order.posting_date),
        "sales_invoice": None,  # Trade Order is the origin, not an SI
        "polemarch_trade_order": order.name,
        "total_qty_sold": flt(order.qty),
        "sale_price_per_unit": flt(order.price),
        "lots": [
            {
                "holding": p.holding,
                "qty_consumed": p.qty,
                "sale_price_per_unit": flt(order.price),
            }
            for p in plan
        ],
    })
    disposal.flags.ignore_permissions = True
    disposal.insert(ignore_permissions=True)
    disposal.submit()
    return disposal.name
