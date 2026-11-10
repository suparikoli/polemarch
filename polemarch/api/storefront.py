"""Storefront (Medusa) integration endpoints.

Called by packages/medusa-plugin-erpnext with the API key of a user that
has only the `Polemarch Storefront Integration` role. Every endpoint is
idempotent, so the storefront can retry any call.

Order lifecycle (one Storefront Order per Medusa order):

  order_funded       wallet paid  -> Reservation per line (+ one for fees)
                                     and a DRAFT Security Sale per line
  order_transferred  shares moved -> each Sale submitted on the transfer
                                     date (releases its reservation, posts
                                     the Buy Settlement, FIFO, JEs, Customer
                                     Holding); fees released and debited
  order_cancelled    before transfer: reservations released, drafts
                     deleted; after transfer: flagged Needs Review only

Every other wallet movement (top-ups, fees, payouts, promo credits) is
mirrored by `wallet_movement` as a wallet-leg-only Wallet Transaction: no
Journal Entry. The books coordinator posts the cash legs.

`customer_holdings` returns e16's Customer Holding snapshot, which is the
master the storefront portfolio shows.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import convert_utc_to_system_timezone, flt, get_datetime, getdate, now_datetime

from framith_wallet import wallet as wallet_engine
from framith_wallet.utils import default_company, wallet_settings
from framith_wallet.wallet import ensure_wallet, wallet_name
from polemarch.polemarch_trading.wallet_links import describe_trade, tag

STOREFRONT_ROLE = "Polemarch Storefront Integration"

# Medusa wallet kinds that belong to the order lifecycle above.
_ORDER_KINDS = {"order_debit", "order_reversal", "refund"}
_ORDER_REFERENCES = {"order", "cart"}


def _guard():
    frappe.only_for([STOREFRONT_ROLE, "System Manager"], message=_("Storefront integration only."))


def _parse(payload):
    return frappe.parse_json(payload) if isinstance(payload, str) else payload


def _system_dt(value):
    """Medusa sends ISO timestamps in UTC; Frappe stores system-time datetimes."""
    if not value:
        return None
    dt = get_datetime(value)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return get_datetime(convert_utc_to_system_timezone(dt)).replace(tzinfo=None)


# ── masters ──────────────────────────────────────────────────────────────


def ensure_customer(c: dict) -> str:
    """Customer for a storefront customer: by medusa_customer_id, then by
    email, else created in the Polemarch group. Gets a wallet."""
    mid = c.get("medusa_customer_id")
    if not mid:
        frappe.throw(_("medusa_customer_id is required."))
    name = frappe.db.get_value("Customer", {"medusa_customer_id": mid}, "name")
    if not name and c.get("email"):
        name = frappe.db.get_value("Customer", {"email_id": c["email"]}, "name")
        if name:
            frappe.db.set_value("Customer", name, "medusa_customer_id", mid, update_modified=False)
    if not name:
        doc = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": (c.get("name") or c.get("email") or mid).strip(),
            "customer_type": "Individual",
            "customer_group": "Polemarch" if frappe.db.exists("Customer Group", "Polemarch")
            else frappe.db.get_single_value("Selling Settings", "customer_group"),
            "territory": frappe.db.get_single_value("Selling Settings", "territory")
            or frappe.db.get_value("Territory", {"is_group": 0}, "name"),
            "email_id": c.get("email"),
            "mobile_no": c.get("phone"),
            "medusa_customer_id": mid,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        name = doc.name
    if not ensure_wallet(name, default_company()):
        frappe.throw(_("Customer wallets are not enabled on {0}.").format(default_company()), title=_("Wallet Disabled"))
    return name


def ensure_security(isin: str, title: Optional[str] = None) -> str:
    if frappe.db.exists("Security", isin):
        return isin
    doc = frappe.get_doc({
        "doctype": "Security", "isin": isin, "security_name": title or isin,
        "security_type": "Unlisted Shares", "tradable": 1, "active": 1,
        "notes": "Created by the storefront integration.",
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


@frappe.whitelist(methods=["POST"])
def upsert_security(payload):
    """A storefront product: refresh the price and page link of its Security.
    A new ISIN gets a Security only with `create` (orders create the ones
    they need), so the books' master list holds what was traded, not the
    whole catalogue. The name of an existing Security is the books' and is
    never overwritten."""
    _guard()
    p = _parse(payload)
    if not frappe.db.exists("Security", p["isin"]) and not p.get("create"):
        return {"skipped": "no Security for this ISIN"}
    name = ensure_security(p["isin"], p.get("title"))
    values = {}
    if p.get("price") is not None:
        values.update(last_traded_price=flt(p["price"]), price_updated_on=now_datetime())
    if "page_url" in p:
        values["polemarch_page_url"] = p.get("page_url")
    if values:
        frappe.db.set_value("Security", name, values)
    return {"security": name}


@frappe.whitelist(methods=["POST"])
def upsert_customer(payload):
    _guard()
    name = ensure_customer(_parse(payload))
    return {"customer": name, "wallet": wallet_name(name)}


# ── orders ───────────────────────────────────────────────────────────────


def _reserve(wallet, amount, key, order, medusa_id):
    return wallet_engine.apply_delta(
        wallet=wallet, txn_type="Reservation", direction="Debit", amount=amount,
        reference_doctype="Storefront Order", reference_name=order, idempotency_key=key,
        remarks=f"Reserved for storefront order {order}", origin="Medusa", medusa_id=medusa_id,
    )


def _release(wallet, wt, key, order, remarks):
    return wallet_engine.apply_delta(
        wallet=wallet, txn_type="Reservation Release", direction="Credit",
        amount=flt(frappe.db.get_value("Wallet Transaction", wt, "amount")),
        reference_doctype="Storefront Order", reference_name=order, idempotency_key=key,
        remarks=remarks, origin="Medusa",
    )


def _summary(so):
    return {"storefront_order": so.name, "status": so.status,
            "security_sales": [l.security_sale for l in so.lines if l.security_sale]}


@frappe.whitelist(methods=["POST"])
def order_funded(payload):
    """Reserve the wallet and draft one Security Sale per line."""
    _guard()
    o = _parse(payload)
    if frappe.db.exists("Storefront Order", o["id"]):
        return _summary(frappe.get_doc("Storefront Order", o["id"]))

    customer = ensure_customer(o["customer"])
    wallet = wallet_name(customer)
    company = default_company()
    funded_on = _system_dt(o.get("funded_at")) or now_datetime()
    lines = [dict(l, amount=flt(flt(l["qty"]) * flt(l["unit_price"]), 2)) for l in o["lines"]]
    lines_amount = flt(sum(l["amount"] for l in lines), 2)
    wallet_debit = flt(o.get("wallet_debit"), 2)
    fee = flt(wallet_debit - lines_amount, 2)

    so = frappe.get_doc({
        "doctype": "Storefront Order", "medusa_order_id": o["id"], "display_id": str(o.get("display_id") or ""),
        "customer": customer, "company": company, "status": "Funded", "funded_on": funded_on,
        "wallet_debit": wallet_debit, "lines_amount": lines_amount, "fee_amount": max(fee, 0),
        "lines": [{"medusa_line_id": l["id"], "security": ensure_security(l["isin"], l.get("title")),
                   "qty": flt(l["qty"]), "rate": flt(l["unit_price"]), "amount": l["amount"]} for l in lines],
    })
    if fee < -0.01:
        so.status = "Needs Review"
        so.notes = f"Wallet debit ₹{wallet_debit} is less than the shares ₹{lines_amount}: nothing reserved or drafted."
    so.flags.ignore_permissions = True
    so.insert(ignore_permissions=True)
    if so.status == "Needs Review":
        return _summary(so)

    for row in so.lines:
        row.wallet_reservation = _reserve(wallet, row.amount, f"sf-res:{row.medusa_line_id}", so.name, row.medusa_line_id)
        ss = frappe.get_doc({
            "doctype": "Security Sale", "company": company, "security": row.security,
            "posting_date": getdate(funded_on), "party_type": "Customer", "party": customer,
            "from_classification": "Stock in Trade", "qty": row.qty, "rate": row.rate,
            "payment_method": "Customer Wallet", "storefront_order": so.name,
            "medusa_line_id": row.medusa_line_id, "wallet_reservation": row.wallet_reservation,
            "remarks": f"[STOREFRONT order #{so.display_id} {so.name}] submitted automatically on share transfer",
        })
        ss.flags.ignore_permissions = True
        ss.insert(ignore_permissions=True)
        row.security_sale = ss.name
        tag(row.wallet_reservation, particulars=f"Reserved: {describe_trade('Bought', row.security, row.qty, row.rate)}",
            security=row.security, qty=row.qty, rate=row.rate, security_sale=ss.name, storefront_order=so.name)
    if so.fee_amount > 0.005:
        so.fee_reservation = _reserve(wallet, so.fee_amount, f"sf-res-fee:{so.name}", so.name, so.name)
        tag(so.fee_reservation, particulars=f"Reserved: fees on order #{so.display_id}", storefront_order=so.name)
    so.save(ignore_permissions=True)
    return _summary(so)


@frappe.whitelist(methods=["POST"])
def order_transferred(payload):
    """Submit the order's Security Sales on the transfer date and settle fees."""
    _guard()
    p = _parse(payload)
    if not frappe.db.exists("Storefront Order", p["id"]):
        frappe.throw(_("No Storefront Order {0}: it was funded before the integration went live. "
                       "Book its sale by hand.").format(p["id"]), title=_("Unknown Order"))
    so = frappe.get_doc("Storefront Order", p["id"])
    if so.status == "Transferred":
        return _summary(so)
    if so.status != "Funded":
        frappe.throw(_("Storefront Order {0} is {1}.").format(so.name, so.status), title=_("Not Funded"))

    transfer_date = getdate(_system_dt(p.get("transferred_at")) or now_datetime())
    for row in so.lines:
        ss = frappe.get_doc("Security Sale", row.security_sale)
        if ss.docstatus == 0:
            ss.posting_date = ss.transfer_date = transfer_date
            ss.flags.ignore_permissions = True
            ss.submit()
    wallet = wallet_name(so.customer)
    if so.fee_reservation:
        release = _release(wallet, so.fee_reservation, f"sf-fee-release:{so.name}", so.name, f"Fee reservation released for {so.name}")
        tag(release, particulars=f"Released: fees on order #{so.display_id}", storefront_order=so.name, linked_transaction=so.fee_reservation)
        so.fee_settlement = wallet_engine.apply_delta(
            wallet=wallet, txn_type="Fee", direction="Debit", amount=so.fee_amount,
            reference_doctype="Storefront Order", reference_name=so.name, idempotency_key=f"sf-fee:{so.name}",
            remarks=f"Storefront order fees for {so.name}", origin="Medusa", medusa_id=so.name,
        )
        tag(so.fee_settlement, particulars=f"Fees on order #{so.display_id}", storefront_order=so.name, linked_transaction=so.fee_reservation)
    so.status = "Transferred"
    so.transfer_date = transfer_date
    so.save(ignore_permissions=True)
    return _summary(so)


@frappe.whitelist(methods=["POST"])
def order_cancelled(payload):
    _guard()
    p = _parse(payload)
    if not frappe.db.exists("Storefront Order", p["id"]):
        return {"status": "unknown"}
    so = frappe.get_doc("Storefront Order", p["id"])
    if so.status == "Cancelled":
        return _summary(so)
    if so.status == "Transferred":
        so.status = "Needs Review"
        so.notes = (so.notes or "") + f"\nCancelled on the storefront after transfer ({p.get('reason') or 'no reason'}): the books coordinator decides."
        so.save(ignore_permissions=True)
        return _summary(so)

    wallet = wallet_name(so.customer)
    drafts = []
    for row in so.lines:
        if row.security_sale and frappe.db.get_value("Security Sale", row.security_sale, "docstatus") == 0:
            drafts.append(row.security_sale)
            row.security_sale = None
        if row.wallet_reservation:
            # The draft goes, so its rows stop pointing at it.
            tag(row.wallet_reservation, security_sale=None)
            release = _release(wallet, row.wallet_reservation, f"sf-cancel:{row.wallet_reservation}", so.name, f"Order {so.name} cancelled")
            tag(release, particulars=f"Released: order #{so.display_id} cancelled", security=row.security, qty=row.qty, rate=row.rate,
                storefront_order=so.name, linked_transaction=row.wallet_reservation)
    if so.fee_reservation:
        release = _release(wallet, so.fee_reservation, f"sf-cancel:{so.fee_reservation}", so.name, f"Order {so.name} cancelled")
        tag(release, particulars=f"Released: fees on cancelled order #{so.display_id}", storefront_order=so.name, linked_transaction=so.fee_reservation)
    so.status = "Cancelled"
    so.cancelled_on = now_datetime()
    so.save(ignore_permissions=True)
    for name in drafts:   # unlinked above, so the delete passes the link check
        frappe.delete_doc("Security Sale", name, ignore_permissions=True)
    return _summary(so)


# ── wallet ───────────────────────────────────────────────────────────────


def _txn_type(t, historic):
    order_related = t["kind"] in _ORDER_KINDS or (
        t["kind"] == "manual_adjust" and (t.get("reference_type") or "") in _ORDER_REFERENCES
    )
    if order_related and not historic:
        return None
    if t["direction"] == "credit":
        return "Deposit" if t["kind"] == "vba_credit" else "Adjustment"
    if t["kind"] == "order_debit":
        return "Buy Settlement"
    if t["kind"] in ("payin_fee_debit", "payout_fee_debit"):
        return "Fee"
    if t["kind"] == "withdrawal_debit":
        return "Withdrawal"
    return "Adjustment"


_KIND_LABELS = {
    "vba_credit": "Top-up via bank transfer",
    "payin_fee_debit": "Pay-in fee on top-up",
    "payout_fee_debit": "Payout fee",
    "withdrawal_debit": "Payout to bank",
    "referral_credit": "Referral credit (promo)",
    "points_conversion": "Points converted (promo)",
    "commission_credit": "Commission credit",
    "commission_reversal": "Commission reversed",
    "order_debit": "Storefront order payment",
    "refund": "Storefront order refund",
    "manual_adjust": "Manual adjustment",
}


def _particulars(t):
    label = _KIND_LABELS.get(t["kind"], t["kind"])
    if t.get("note") and t["kind"] == "manual_adjust":
        return f"{label}: {t['note']}"[:140]
    refs = []
    if t.get("cashfree_event_id"):
        label = label.replace("bank transfer", "Cashfree")
        refs.append(f"Cashfree ref {t['cashfree_event_id']}")
    if t["kind"] in ("vba_credit", "payin_fee_debit") and t.get("reference_id"):
        refs.append(f"UTR {t['reference_id']}")
    return f"{label} ({', '.join(refs)})" if refs else label


@frappe.whitelist(methods=["POST"])
def wallet_movement(payload, historic=0):
    """Mirror one Medusa wallet_transaction (amount in rupees). Order
    movements are skipped (the order lifecycle carries them) unless
    `historic` — the cut-over replay of pre-integration history."""
    _guard()
    t = _parse(payload)
    txn_type = _txn_type(t, int(historic or 0))
    if not txn_type:
        return {"skipped": "order lifecycle"}
    customer = ensure_customer(t["customer"])
    wt = wallet_engine.apply_delta(
        wallet=wallet_name(customer), txn_type=txn_type,
        direction="Credit" if t["direction"] == "credit" else "Debit", amount=flt(t["amount"], 2),
        idempotency_key=f"medusa-wt:{t['id']}", origin="Medusa", medusa_id=t["id"],
        posting_datetime=_system_dt(t.get("created_at")),
        remarks=f"Storefront {t['kind']} ({t.get('bucket') or 'main'})"
        + (f" ref {t.get('reference_type')} {t.get('reference_id')}" if t.get("reference_id") else ""),
    )
    ref = t.get("reference_id")
    linked = None
    if ref and t["kind"] != "vba_credit":   # a fee or its refund belongs with the top-up on the same UTR
        linked = frappe.db.get_value("Wallet Transaction", {
            "wallet": wallet_name(customer), "external_reference": ref, "txn_type": "Deposit", "name": ["!=", wt]}, "name")
    tag(wt, particulars=_particulars(t), external_reference=ref, cashfree_reference=t.get("cashfree_event_id"), linked_transaction=linked)
    return {"wallet_transaction": wt, "balance": frappe.db.get_value(
        "Wallet", wallet_name(customer), ["balance_available", "balance_reserved", "balance_total"], as_dict=True)}


# ── reads ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["GET"])
def customer_holdings(since: Optional[str] = None):
    """e16's Customer Holding snapshot for storefront customers."""
    _guard()
    conds, values = ["coalesce(c.medusa_customer_id, '') != ''"], {}
    if since:
        conds.append("h.modified > %(since)s")
        values["since"] = _system_dt(since) or since
    rows = frappe.db.sql(
        f"""SELECT c.medusa_customer_id, h.security AS isin, h.security_name, h.qty, h.modified
            FROM `tabCustomer Holding` h JOIN `tabCustomer` c ON c.name = h.customer
            WHERE {' AND '.join(conds)} ORDER BY h.modified""",
        values, as_dict=True,
    )
    return {"as_of": now_datetime(), "holdings": rows}


@frappe.whitelist(methods=["GET"])
def ping():
    _guard()
    company = default_company()
    s = wallet_settings(company)
    return {"user": frappe.session.user, "company": company,
            "wallet_enabled": bool(s.get("enable_customer_wallet")), "wallet_account": s.get("wallet_liability_account")}
