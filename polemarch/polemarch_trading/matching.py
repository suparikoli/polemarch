"""Trade Order matching + reservation engine.

Refactored to operate on Investment Holding directly (no Security Lot).
FIFO is by `creation` ASC, filtered by classification.

Public surface:
  - place_reservation(order)  — called on Trade Order submit.
        Buy + Customer: wallet Reservation Debit for net_amount.
        Sell: FIFO scan + bump qty_reserved on the chosen holdings.
  - match(order_name)         — Submitted → Matched. Creates Settlement Instruction.
  - cancel_order(order)       — releases reservations.

Settlement actually creates Investment Disposal rows; this module only
plans + reserves.
"""

import secrets
from typing import Optional

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime


DEFAULT_T_PLUS = 1  # T+1 settlement window for unlisted securities.


def place_reservation(order) -> None:
    """Called from Trade Order.on_submit. Idempotent on reservation_id."""
    if order.reservation_id:
        return

    reservation_id = secrets.token_hex(16)

    if order.side == "Buy" and order.book == "Customer":
        _reserve_buy_wallet(order, reservation_id)
    elif order.side == "Sell":
        _reserve_sell_holdings(order)

    frappe.db.set_value(
        "Trade Order", order.name,
        {"reservation_id": reservation_id, "reserved_at": now_datetime()},
        update_modified=False,
    )


def match(order_name: str, counter_party: Optional[str] = None) -> str:
    """Move a Submitted order to Matched. Settlement Instruction is created."""
    order = frappe.get_doc("Trade Order", order_name)
    if order.order_state != "Submitted":
        frappe.throw(
            _("Trade Order {0}: only Submitted orders can be matched (state={1}).").format(
                order_name, order.order_state
            ),
            title=_("Cannot Match"),
        )

    order.transition_to("Matched")
    _create_settlement_instruction(order, counter_party=counter_party)
    return order.name


def cancel_order(order) -> None:
    """Release reservations + qty_reserved. Idempotent."""
    if order.side == "Buy" and order.book == "Customer":
        _release_buy_wallet(order)
    if order.side == "Sell":
        _release_sell_holdings(order)
    frappe.db.set_value(
        "Trade Order", order.name,
        "released_at", now_datetime(),
        update_modified=False,
    )


# ── reservation primitives ──────────────────────────────────────────────


def _reserve_buy_wallet(order, reservation_id: str) -> None:
    from polemarch.polemarch_trading import wallet as wallet_engine

    wallet_name = f"WAL-{order.customer}"
    if not frappe.db.exists("Wallet", wallet_name):
        frappe.throw(
            _("Wallet {0} not found for customer {1}.").format(wallet_name, order.customer),
            title=_("Wallet Missing"),
        )

    wallet_engine.apply_delta(
        wallet=wallet_name,
        txn_type="Reservation",
        direction="Debit",
        amount=flt(order.net_amount),
        reference_doctype="Trade Order",
        reference_name=order.name,
        idempotency_key=f"reserve:{reservation_id}",
        remarks=f"Reservation for Trade Order {order.name}",
    )


def _release_buy_wallet(order) -> None:
    if not order.reservation_id:
        return

    from polemarch.polemarch_trading import wallet as wallet_engine

    wallet_name = f"WAL-{order.customer}"
    if not frappe.db.exists("Wallet", wallet_name):
        return

    release_idem = f"release:{order.reservation_id}"
    if frappe.db.exists("Wallet Transaction", {"idempotency_key": release_idem}):
        return

    wallet_engine.apply_delta(
        wallet=wallet_name,
        txn_type="Reservation Release",
        direction="Credit",
        amount=flt(order.net_amount),
        reference_doctype="Trade Order",
        reference_name=order.name,
        idempotency_key=release_idem,
        remarks=f"Reservation release for Trade Order {order.name}",
    )


def _reserve_sell_holdings(order) -> None:
    """FIFO scan Investment Holdings, bump qty_reserved by the consumption plan."""
    from polemarch.polemarch_trading import fifo as fifo_engine

    classification = _classification_for_order(order)
    plan = fifo_engine.consume(
        security=order.security,
        company=order.company,
        classification=classification,
        qty_to_sell=flt(order.qty),
        sale_date=getdate(order.posting_date),
        customer_filter=_seller_customer_filter(order),
    )

    if not plan:
        if order.book == "Customer":
            frappe.throw(
                _("Insufficient {0} inventory for Trade Order {1}: no consumable holdings found.").format(
                    classification, order.name
                ),
                title=_("Insufficient Inventory"),
            )
        return

    total_planned = sum(p.qty for p in plan)
    if total_planned + 0.0001 < flt(order.qty) and order.book == "Customer":
        frappe.throw(
            _(
                "Trade Order {0}: insufficient {1} inventory. Requested {2}, available {3}."
            ).format(order.name, classification, order.qty, total_planned),
            title=_("Insufficient Inventory"),
        )

    fifo_engine.reserve(plan)


def _release_sell_holdings(order) -> None:
    """On cancel: re-run FIFO and reverse the reservation."""
    if order.side != "Sell":
        return

    from polemarch.polemarch_trading import fifo as fifo_engine

    classification = _classification_for_order(order)
    plan = fifo_engine.consume(
        security=order.security,
        company=order.company,
        classification=classification,
        qty_to_sell=flt(order.qty),
        sale_date=getdate(order.posting_date),
        customer_filter=_seller_customer_filter(order),
    )
    if plan:
        fifo_engine.release_reservation(plan)


# ── helpers ──────────────────────────────────────────────────────────────


def _classification_for_order(order) -> str:
    """Classification of the SELLER's inventory — what FIFO will consume.

      customer-Sell → customer disposes their Investment-class Holdings
      customer-Buy  → Polemarch disposes Stock-in-Trade inventory
      proprietary   → Stock-in-Trade only (proprietary book never holds
                      Investment-class inventory in this engine)
    """
    if order.book == "Customer" and order.side == "Sell":
        return "Investment"
    return "Stock in Trade"


def _seller_customer_filter(order):
    """FIFO customer_filter for the side that DISPOSES. Returns the Customer
    name when the seller is a customer (customer-Sell), else None to scope
    against the proprietary pool."""
    if order.book == "Customer" and order.side == "Sell":
        return order.customer
    return None


def _create_settlement_instruction(order, counter_party: Optional[str] = None) -> str:
    if order.settlement_instruction:
        return order.settlement_instruction

    expected = order.expected_settlement_date or add_days(getdate(order.posting_date), DEFAULT_T_PLUS)

    si = frappe.get_doc({
        "doctype": "Settlement Instruction",
        "trade_order": order.name,
        "qty": flt(order.qty),
        "gross_amount": flt(order.gross_amount),
        "net_amount": flt(order.net_amount),
        "expected_settlement_date": expected,
        "settlement_state": "Pending",
    })
    si.flags.ignore_permissions = True
    si.insert(ignore_permissions=True)
    si.submit()

    frappe.db.set_value(
        "Trade Order", order.name,
        {
            "settlement_instruction": si.name,
            "settlement_status": "Pending",
            "expected_settlement_date": expected,
        },
        update_modified=False,
    )
    return si.name
