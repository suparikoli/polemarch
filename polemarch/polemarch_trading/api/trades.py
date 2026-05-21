"""Trade Order REST endpoints (Phase 2).

All endpoints are role-gated and idempotency-key-aware. They are the only
public-facing way to drive the Trade Order state machine from outside Frappe.

Conventions:
  - Idempotency keys live in `Polemarch API Idempotency Log` (Phase 5 doctype).
    Until that ships, the wallet/SLLE engines already enforce idempotency at
    the inner layer, so duplicate API calls are safe but not deduplicated at
    the API surface.
  - Responses use Frappe's standard JSON envelope. Errors raise typed
    exceptions so the response_status_code is set correctly by Frappe.
"""

from typing import Optional

import frappe
from frappe import _


@frappe.whitelist()
def create_order(
    customer: str,
    side: str,
    security: str,
    qty: float,
    price: float,
    portfolio: Optional[str] = None,
    book: str = "Customer",
    platform_fee: float = 0,
    low_order_fee: float = 0,
    stamp_duty: float = 0,
    idempotency_key: Optional[str] = None,
    medusa_order_id: Optional[str] = None,
):
    """Create + submit a Trade Order. Returns the new doc (as dict)."""
    frappe.only_for(
        ["Customer", "System Manager", "Accounts Manager", "Polemarch Trader"],
        message=_("Not allowed to create Trade Orders."),
    )

    if not portfolio and book == "Customer":
        portfolio = _default_customer_portfolio(customer)

    doc = frappe.get_doc(
        {
            "doctype": "Trade Order",
            "side": side,
            "book": book,
            "portfolio": portfolio,
            "security": security,
            "customer": customer if book == "Customer" else None,
            "qty": qty,
            "price": price,
            "platform_fee": platform_fee,
            "low_order_fee": low_order_fee,
            "stamp_duty": stamp_duty,
            "medusa_order_id": medusa_order_id,
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    doc.submit()

    return _serialise(doc.name)


@frappe.whitelist()
def match_order(name: str, counter_party: Optional[str] = None):
    """Back-office: move Submitted → Matched. FIFO consume planned here."""
    frappe.only_for(
        ["System Manager", "Polemarch Settlement Officer", "Accounts Manager"],
        message=_("Not allowed to match Trade Orders."),
    )
    from polemarch.polemarch_trading import matching as matching_engine

    matching_engine.match(name, counter_party=counter_party)
    return _serialise(name)


@frappe.whitelist()
def settle_order(name: str):
    """Back-office: drive Matched → Settling → Settled via the settlement runner.

    Phase 2 simplification — this combines Funded + Cleared into a single call.
    Production deployments may want to split via the explicit settlement API.
    """
    frappe.only_for(
        ["System Manager", "Polemarch Settlement Officer"],
        message=_("Not allowed to settle Trade Orders."),
    )
    order = frappe.get_doc("Trade Order", name)
    if order.order_state != "Matched":
        frappe.throw(
            _("Trade Order {0}: must be in Matched state to settle (got {1}).").format(
                name, order.order_state
            ),
            title=_("Cannot Settle"),
        )

    if not order.settlement_instruction:
        frappe.throw(
            _("Trade Order {0}: no Settlement Instruction attached.").format(name),
            title=_("Settlement Missing"),
        )

    order.transition_to("Settling")

    from polemarch.polemarch_trading import settlement as settlement_engine

    settlement_engine.fund(order.settlement_instruction)
    settlement_engine.clear(order.settlement_instruction)
    return _serialise(name)


@frappe.whitelist()
def cancel_order(name: str, reason: Optional[str] = None):
    """User or admin cancels an order (pre-Settled). Cancels the doc via the
    standard submit/cancel pathway; controller's `on_cancel` runs the engine
    release flow.
    """
    frappe.only_for(
        ["Customer", "System Manager", "Accounts Manager", "Polemarch Trader"],
        message=_("Not allowed to cancel Trade Orders."),
    )
    doc = frappe.get_doc("Trade Order", name)
    if doc.order_state in ("Settled", "Closed", "Cancelled", "Rejected"):
        frappe.throw(
            _("Trade Order {0}: cannot cancel from state {1}.").format(name, doc.order_state),
            title=_("Cancel Not Allowed"),
        )
    if reason:
        frappe.db.set_value("Trade Order", name, "cancellation_reason", reason, update_modified=False)
    doc.cancel()
    return _serialise(name)


@frappe.whitelist()
def list_orders(
    customer: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List Trade Orders with the usual filters. Customer-role users are
    automatically scoped by `permission_query_conditions` (Phase 5)."""
    filters = {}
    if customer:
        filters["customer"] = customer
    if status:
        filters["order_state"] = status
    if from_date or to_date:
        filters["posting_date"] = ["between", [from_date or "1970-01-01", to_date or "2099-12-31"]]

    rows = frappe.get_all(
        "Trade Order",
        filters=filters,
        fields=[
            "name",
            "posting_date",
            "side",
            "book",
            "customer",
            "security",
            "security_name",
            "qty",
            "price",
            "gross_amount",
            "net_amount",
            "order_state",
            "settlement_status",
        ],
        order_by="modified DESC",
        limit_page_length=limit,
        limit_start=offset,
    )
    total = frappe.db.count("Trade Order", filters=filters)
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


# ── helpers ──────────────────────────────────────────────────────────────


def _serialise(name: str) -> dict:
    return frappe.get_doc("Trade Order", name).as_dict()


def _default_customer_portfolio(customer: str) -> str:
    """Resolve the customer's default Investment portfolio (Phase 5 may add
    user-pickable defaults). Falls back to creating one on the fly if absent."""
    portfolio = frappe.db.get_value(
        "Portfolio",
        {"customer": customer, "owner_kind": "Customer", "portfolio_type": "Investment"},
        "name",
    )
    if portfolio:
        return portfolio

    # Bootstrap a customer-owned Investment portfolio on first trade.
    # `Company` has no `disabled` column on Frappe v16; query by name only.
    company = frappe.db.get_value("Customer", customer, "default_company") or (
        frappe.defaults.get_global_default("company")
        or frappe.db.get_value("Company", {}, "name")
    )
    doc = frappe.get_doc(
        {
            "doctype": "Portfolio",
            "portfolio_name": f"Customer - Investment - {customer}",
            "portfolio_type": "Investment",
            "owner_kind": "Customer",
            "company": company,
            "customer": customer,
            "status": "Active",
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name
