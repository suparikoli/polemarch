"""Trade Order matching + reservation engine.

Phase 2 scope:
  - `place_reservation(order)` — called on Trade Order submit.
      • Buy + Customer book: debit wallet (Reservation) for net_amount.
      • Sell + Customer book: place Reserve SLLE rows on the customer's
        Security Lots via FIFO.
      • Proprietary orders: no wallet reservation; lot reservation only.
  - `match(order)` — back-office or scheduler-driven. Populates lots_consumed
     for sells via FIFO, transitions Submitted → Matched, creates a Settlement
     Instruction.
  - `cancel_order(order)` — releases reservations and SLLEs.

Counterparty-side P2P matching is Phase 6 (out of scope here). Phase 2 assumes
either Polemarch is the counterparty (Proprietary book on one side) or the
match is admin-driven.
"""

import secrets
from typing import Optional

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime


DEFAULT_T_PLUS = 1  # Default settlement window for unlisted: T+1.


def place_reservation(order) -> None:
    """Called from Trade Order.on_submit. Idempotent on reservation_id."""
    if order.reservation_id:
        # Already reserved — keep idempotent on accidental re-submit.
        return

    reservation_id = secrets.token_hex(16)

    if order.side == "Buy" and order.book == "Customer":
        _reserve_buy_wallet(order, reservation_id)
    elif order.side == "Sell" and order.book == "Customer":
        _reserve_sell_lots(order, reservation_id)
    elif order.side == "Sell" and order.book == "Proprietary":
        _reserve_sell_lots(order, reservation_id)
    # Buy + Proprietary: no wallet (company funds), no lot reservation needed.

    frappe.db.set_value(
        "Trade Order", order.name,
        {"reservation_id": reservation_id, "reserved_at": now_datetime()},
        update_modified=False,
    )


def match(order_name: str, counter_party: Optional[str] = None) -> str:
    """Move a Submitted order to Matched. Returns the Trade Order name."""
    order = frappe.get_doc("Trade Order", order_name)
    if order.order_state != "Submitted":
        frappe.throw(
            _("Trade Order {0}: only Submitted orders can be matched (state={1}).").format(
                order_name, order.order_state
            ),
            title=_("Cannot Match"),
        )

    if order.side == "Sell":
        _populate_lots_consumed_for_sell(order)

    # Mark Matched and create the Settlement Instruction.
    order.transition_to("Matched")
    _create_settlement_instruction(order, counter_party=counter_party)
    return order.name


def cancel_order(order) -> None:
    """Release reservations / SLLEs. Idempotent — safe to re-run."""
    if order.side == "Buy" and order.book == "Customer":
        _release_buy_wallet(order)
    if order.side == "Sell":
        _release_sell_lots(order)
    frappe.db.set_value(
        "Trade Order", order.name,
        "released_at", now_datetime(),
        update_modified=False,
    )


# ── Reservation primitives ──────────────────────────────────────────────


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

    # Idempotent: skip if the release WT already exists.
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


def _reserve_sell_lots(order, reservation_id: str) -> None:
    from polemarch.polemarch_trading import fifo as fifo_engine

    plan = fifo_engine.consume(
        security=order.security,
        portfolio=order.portfolio,
        qty_to_sell=flt(order.qty),
        sale_date=getdate(order.posting_date),
    )

    total_planned = sum(p.qty for p in plan)
    if total_planned + 0.0001 < flt(order.qty):
        # Insufficient inventory — order will still record what is reservable
        # and rely on match() to either reject or facilitate. For Phase 2 we
        # only error on Customer-book sells; Proprietary may legitimately be
        # short-selling against a future acquisition.
        if order.book == "Customer":
            frappe.throw(
                _(
                    "Trade Order {0}: insufficient lots in portfolio {1} for security {2}. "
                    "Requested {3}, available {4}."
                ).format(
                    order.name, order.portfolio, order.security, order.qty, total_planned
                ),
                title=_("Insufficient Inventory"),
            )

    for p in plan:
        slle = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": p.security_lot,
                "entry_type": "Reserve",
                "qty": p.qty,
                "cost_basis_per_unit": p.cost_basis_per_unit,
                "reference_doctype": "Trade Order",
                "reference_name": order.name,
            }
        )
        slle.flags.ignore_permissions = True
        slle.insert(ignore_permissions=True)
        slle.submit()


def _release_sell_lots(order) -> None:
    """Post Reserve Release SLLE rows mirroring each open Reserve row."""
    if order.side != "Sell":
        return

    reserves = frappe.get_all(
        "Security Lot Ledger Entry",
        filters={
            "reference_doctype": "Trade Order",
            "reference_name": order.name,
            "entry_type": "Reserve",
            "is_cancelled": 0,
            "docstatus": 1,
        },
        fields=["name", "security_lot", "qty", "cost_basis_per_unit"],
    )
    for row in reserves:
        # Idempotency: skip if a Reserve Release for this Reserve already exists.
        if frappe.db.exists(
            "Security Lot Ledger Entry",
            {
                "reference_doctype": "Trade Order",
                "reference_name": order.name,
                "entry_type": "Reserve Release",
                "reverses": row.name,
                "is_cancelled": 0,
            },
        ):
            continue

        release = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": row.security_lot,
                "entry_type": "Reserve Release",
                "qty": row.qty,
                "cost_basis_per_unit": row.cost_basis_per_unit,
                "reverses": row.name,
                "reference_doctype": "Trade Order",
                "reference_name": order.name,
            }
        )
        release.flags.ignore_permissions = True
        release.insert(ignore_permissions=True)
        release.submit()

        # Cancel the Reserve row via the reversal-flag.
        original = frappe.get_doc("Security Lot Ledger Entry", row.name)
        original.flags.from_reversal = True
        original.is_cancelled = 1
        original.reversed_by = release.name
        original.db_update()


# ── Match-time FIFO ─────────────────────────────────────────────────────


def _populate_lots_consumed_for_sell(order) -> None:
    """Replay FIFO at match-time; convert Reserve SLLEs into the lots_consumed
    child table. The actual Consume SLLE rows are written at settle-time, not
    match-time, so a Matched-but-Failed settlement can reverse cleanly."""
    from polemarch.polemarch_trading import fifo as fifo_engine

    plan = fifo_engine.consume(
        security=order.security,
        portfolio=order.portfolio,
        qty_to_sell=flt(order.qty),
        sale_date=getdate(order.posting_date),
    )

    order.set("lots_consumed", [])
    for p in plan:
        sale_amt = p.qty * flt(order.price)
        cost_amt = p.qty * p.cost_basis_per_unit
        order.append(
            "lots_consumed",
            {
                "security_lot": p.security_lot,
                "acquisition_date": p.acquisition_date,
                "qty_consumed": p.qty,
                "cost_basis_per_unit": p.cost_basis_per_unit,
                "sale_price_per_unit": flt(order.price),
                "holding_period_days": p.holding_period_days,
                "is_long_term": int(p.is_long_term),
                "cost_basis_amount": cost_amt,
                "sale_amount": sale_amt,
                "realized_gain": sale_amt - cost_amt,
            },
        )
    order.flags.from_state_transition = True
    order.db_update()
    order.update_children()


# ── Settlement Instruction creation ────────────────────────────────────


def _create_settlement_instruction(order, counter_party: Optional[str] = None) -> str:
    if order.settlement_instruction:
        return order.settlement_instruction

    expected = order.expected_settlement_date or add_days(getdate(order.posting_date), DEFAULT_T_PLUS)

    si = frappe.get_doc(
        {
            "doctype": "Settlement Instruction",
            "trade_order": order.name,
            "qty": flt(order.qty),
            "gross_amount": flt(order.gross_amount),
            "net_amount": flt(order.net_amount),
            "expected_settlement_date": expected,
            "settlement_state": "Pending",
        }
    )
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
