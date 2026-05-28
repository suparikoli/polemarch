import frappe

from polemarch.install import POLEMARCH_CUSTOMER_GROUP


def validate(doc, method=None):
    # Mithtech-only customers are explicitly OFF the Polemarch path —
    # operator-managed escape hatch for non-trading lines of business.
    # Skip the primary-contact requirement (Mithtech-only customers
    # don't need DP / KYC plumbing). Every other customer in this
    # tenant is implicitly a Polemarch customer; the legacy
    # `custom_is_polemarch_customer` auto-derived flag was retired
    # in v0_26_0 since `not custom_is_mithtech_only` is the only
    # gate the rest of the codebase actually needs.
    if doc.get("custom_is_mithtech_only"):
        _sync_customer_name_from_primary_contact(doc)
        return
    _sync_customer_name_from_primary_contact(doc)
    _enforce_primary_contact_for_polemarch(doc)


def after_insert(doc, method=None):
    """Auto-create a placeholder Contact for every new Customer.

    Phase 16 made the standard ERPNext Contact the source of truth for
    name / email / phone, but Frappe doesn't auto-create one when a
    Customer is inserted. Without this hook, customers created via API
    / scripts / smoke tests get no Contact and the Phase-16 sync hook
    has nothing to sync from. So: create a placeholder Contact with
    first_name = customer_name, link it via Dynamic Link, and set
    customer_primary_contact.

    Operators open the Contact afterwards to split first/middle/last,
    add email + phone, etc. The auto-create is purely a safety net so
    no Customer ever exists without a Contact.

    Idempotent: skipped if customer_primary_contact is already set or
    any Contact is already linked to this Customer.
    """
    _ensure_primary_contact(doc.name, doc.customer_name)


def _ensure_primary_contact(customer_name: str, display_name: str) -> str | None:
    """Create-or-reuse a Contact for the given Customer. Returns the Contact name."""
    # Reuse if customer_primary_contact already populated.
    existing_primary = frappe.db.get_value(
        "Customer", customer_name, "customer_primary_contact"
    )
    if existing_primary and frappe.db.exists("Contact", existing_primary):
        return existing_primary

    # Reuse if any Contact is already linked via Dynamic Link.
    linked = frappe.db.sql(
        """
        SELECT parent FROM `tabDynamic Link`
         WHERE link_doctype = 'Customer'
           AND link_name    = %s
           AND parenttype   = 'Contact'
         ORDER BY creation ASC
         LIMIT 1
        """,
        (customer_name,),
        as_dict=True,
    )
    if linked:
        contact_name = linked[0].parent
        frappe.db.set_value(
            "Customer", customer_name, "customer_primary_contact", contact_name,
            update_modified=False,
        )
        return contact_name

    # Otherwise — create a placeholder Contact.
    contact = frappe.get_doc({
        "doctype": "Contact",
        "first_name": display_name or customer_name,
        "links": [
            {"link_doctype": "Customer", "link_name": customer_name},
        ],
    })
    contact.flags.ignore_permissions = True
    contact.insert(ignore_permissions=True)

    frappe.db.set_value(
        "Customer", customer_name, "customer_primary_contact", contact.name,
        update_modified=False,
    )
    return contact.name


def _enforce_primary_contact_for_polemarch(doc):
    """Polemarch customers MUST have a primary Contact. Skipped for brand-new
    docs — after_insert auto-creates the placeholder Contact, but that fires
    after validate. Catches the case where an operator clears the primary_
    contact link or imports a Polemarch customer without one.

    A Polemarch customer is anyone NOT explicitly opted out via
    `custom_is_mithtech_only` — the caller (validate) already short-
    circuits for mithtech-only docs before reaching this helper."""
    if doc.is_new():
        return
    if doc.get("custom_is_mithtech_only"):
        return
    if doc.customer_primary_contact:
        return
    frappe.throw(
        frappe._(
            "Polemarch customers must have a primary Contact. Open the "
            "Contacts panel on this Customer and link one (or set "
            "Customer Primary Contact directly)."
        ),
        title=frappe._("Primary Contact Required"),
    )


def _sync_customer_name_from_primary_contact(doc):
    """If the Customer has a primary Contact linked, set `customer_name`
    to the Contact's full name. Operators set name + email + phone once on
    the standard Contact; the Customer's display name follows automatically.

    Only fires for Individual customers — Company customers usually have a
    legal name distinct from any human contact. Skipped silently when the
    customer_primary_contact link isn't set yet (e.g. brand-new Customer
    being created before a Contact exists).
    """
    if doc.customer_type and doc.customer_type != "Individual":
        return
    primary_contact = doc.get("customer_primary_contact")
    if not primary_contact:
        return
    if not frappe.db.exists("Contact", primary_contact):
        return

    full_name = frappe.db.get_value("Contact", primary_contact, "full_name")
    if not full_name:
        # Compose from first/middle/last if full_name isn't denormalised yet.
        c = frappe.db.get_value(
            "Contact",
            primary_contact,
            ("first_name", "middle_name", "last_name"),
            as_dict=True,
        )
        if not c:
            return
        full_name = " ".join(
            part for part in (c.first_name, c.middle_name, c.last_name) if part
        ).strip()
    if full_name and full_name != doc.customer_name:
        doc.customer_name = full_name


def on_update(doc, method=None):
    # Every non-Mithtech-only customer is a Polemarch customer (the
    # legacy `custom_is_polemarch_customer` flag was retired in
    # v0_26_0). Wallet bootstrap fires for all of them.
    if not doc.get("custom_is_mithtech_only"):
        _maybe_ensure_wallet(doc)


def _maybe_ensure_wallet(doc):
    """Phase 1: when a Customer is (or becomes) a Polemarch customer, ensure
    a Wallet exists. Gated by feature flag and skipped silently if Wallet
    doctype is not yet migrated (pre-Phase-1 deployments)."""
    try:
        from polemarch.polemarch_trading.feature_flags import is_enabled

        if not is_enabled("AUTO_CREATE_WALLET_ON_POLEMARCH_FLAG"):
            return
        if not frappe.db.table_exists("Wallet"):
            return

        wallet_name = f"WAL-{doc.name}"
        if frappe.db.exists("Wallet", wallet_name):
            return

        company = _resolve_default_company()
        if not company:
            return

        liability_account = _resolve_wallet_liability_account(company)
        if not liability_account:
            frappe.log_error(
                f"Wallet auto-create for {doc.name} skipped: "
                f"Customer Wallet Liability account missing for company {company}.",
                "Polemarch Wallet Bootstrap",
            )
            return

        wallet = frappe.get_doc(
            {
                "doctype": "Wallet",
                "customer": doc.name,
                "company": company,
                "currency": frappe.db.get_value("Company", company, "default_currency") or "INR",
                "gl_liability_account": liability_account,
                "status": "Active",
            }
        )
        wallet.flags.ignore_permissions = True
        wallet.insert(ignore_permissions=True)
    except Exception:
        # Never block customer save on Wallet bootstrap failure.
        frappe.log_error(frappe.get_traceback(), "Polemarch Wallet Bootstrap")


def _resolve_default_company():
    # Prefer the global default Company; fall back to the first active one.
    default = frappe.defaults.get_global_default("company")
    if default:
        return default
    row = frappe.db.get_value("Company", {"disabled": 0}, "name")
    return row


def _resolve_wallet_liability_account(company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    if not abbr:
        return None
    candidate = f"Customer Wallet Liability - {abbr}"
    if frappe.db.exists("Account", candidate):
        return candidate
    # Forward-compat: tolerate variations from CoA-seed patch edits.
    return frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_name": ["like", "%Customer Wallet Liability%"],
            "disabled": 0,
        },
        "name",
    )
