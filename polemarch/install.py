import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


POLEMARCH_BRAND = "Polemarch"
MITHTECH_SERVICES_BRAND = "Mithtech Services"
POLEMARCH_CUSTOMER_GROUP = "Polemarch"
POLEMARCH_ITEM_GROUP = "Polemarch Securities"
# Item Tax Template applied to every Polemarch-branded Item — drives
# `gst_treatment = "Non-GST"` on Sales Invoice / Sales Order line
# rows so India Compliance computes 0 tax for share-transfer trades
# (securities are excluded from GST under Schedule III, CGST Act).
POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE = "Polemarch - Non-GST"
PROCESSING_FEE_ITEM_CODE = "POLEMARCH-PROC-FEE"
PROCESSING_FEE_HSN = "997152"


def after_install():
    setup()


def after_migrate():
    setup()


POLEMARCH_NAMING_SERIES = "POL-.YYYY.-.#####"


def setup():
    _create_brands()
    _create_customer_group()
    _create_item_group()
    _create_custom_fields()
    _create_polemarch_non_gst_item_tax_template()
    _add_polemarch_naming_series()
    _create_processing_fee_item()


def _create_brands():
    for brand in (POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND):
        if not frappe.db.exists("Brand", brand):
            frappe.get_doc({"doctype": "Brand", "brand": brand}).insert(ignore_permissions=True)


def _create_customer_group():
    if frappe.db.exists("Customer Group", POLEMARCH_CUSTOMER_GROUP):
        return
    parent = frappe.db.get_value("Customer Group", {"is_group": 1, "parent_customer_group": ""}) or "All Customer Groups"
    frappe.get_doc(
        {
            "doctype": "Customer Group",
            "customer_group_name": POLEMARCH_CUSTOMER_GROUP,
            "parent_customer_group": parent,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True)


def _create_item_group():
    if frappe.db.exists("Item Group", POLEMARCH_ITEM_GROUP):
        return
    parent = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ""}) or "All Item Groups"
    frappe.get_doc(
        {
            "doctype": "Item Group",
            "item_group_name": POLEMARCH_ITEM_GROUP,
            "parent_item_group": parent,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True)


def _create_custom_fields():
    fields = {
        "Customer": [
            {
                "fieldname": "custom_is_polemarch_customer",
                "label": "Is Polemarch Customer",
                "fieldtype": "Check",
                "read_only": 1,
                "no_copy": 1,
                "in_standard_filter": 1,
                "insert_after": "custom_bank_details",
                "description": "Auto-set when a DP Details row is added or when the customer is in the Polemarch group.",
            },
            {
                "fieldname": "custom_medusa_customer_id",
                "label": "Medusa Customer ID",
                "fieldtype": "Data",
                "read_only": 1,
                "no_copy": 1,
                "unique": 1,
                "insert_after": "custom_is_polemarch_customer",
            },
            {
                "fieldname": "custom_kyc_status",
                "label": "KYC Status",
                "fieldtype": "Select",
                "options": "Not Started\nIn Review\nVerified\nRejected",
                "default": "Not Started",
                "in_standard_filter": 1,
                "no_copy": 1,
                "insert_after": "custom_medusa_customer_id",
            },
            {
                "fieldname": "custom_kyc_status_reason",
                "label": "KYC Status Reason",
                "fieldtype": "Small Text",
                "depends_on": "eval:doc.custom_kyc_status === 'Rejected'",
                "no_copy": 1,
                "insert_after": "custom_kyc_status",
            },
            {
                "fieldname": "custom_kyc_verified_on",
                "label": "KYC Verified On",
                "fieldtype": "Datetime",
                "read_only": 1,
                "no_copy": 1,
                "depends_on": "eval:doc.custom_kyc_status === 'Verified'",
                "insert_after": "custom_kyc_status_reason",
            },
            {
                "fieldname": "custom_polemarch_dashboard_tab",
                "label": "Polemarch",
                "fieldtype": "Tab Break",
                "insert_after": "custom_kyc_verified_on",
                "depends_on": "eval:doc.custom_is_polemarch_customer",
            },
            {
                "fieldname": "custom_polemarch_dashboard_html",
                "label": "Polemarch Dashboard",
                "fieldtype": "HTML",
                "insert_after": "custom_polemarch_dashboard_tab",
                "depends_on": "eval:doc.custom_is_polemarch_customer",
            },
        ],
        "Item": [
            {
                "fieldname": "custom_medusa_product_id",
                "label": "Medusa Product ID",
                "fieldtype": "Data",
                "read_only": 1,
                "no_copy": 1,
                "unique": 1,
                "insert_after": "custom_rta",
            },
        ],
        "Sales Invoice": [
            {
                "fieldname": "custom_is_polemarch_invoice",
                "label": "Is Polemarch Invoice",
                "fieldtype": "Check",
                "read_only": 1,
                "no_copy": 1,
                "in_standard_filter": 1,
                "insert_after": "is_return",
                "description": "Set automatically when all line items are Polemarch-branded.",
            },
            {
                "fieldname": "custom_medusa_order_id",
                "label": "Medusa Order ID",
                "fieldtype": "Data",
                "read_only": 1,
                "no_copy": 1,
                "insert_after": "custom_is_polemarch_invoice",
            },
        ],
        "Sales Order": [
            {
                "fieldname": "custom_is_polemarch_order",
                "label": "Is Polemarch Order",
                "fieldtype": "Check",
                "read_only": 1,
                "no_copy": 1,
                "in_standard_filter": 1,
                "insert_after": "order_type",
            },
            {
                "fieldname": "custom_medusa_order_id",
                "label": "Medusa Order ID",
                "fieldtype": "Data",
                "read_only": 1,
                "no_copy": 1,
                "insert_after": "custom_is_polemarch_order",
            },
        ],
    }
    create_custom_fields(fields, ignore_validate=True, update=True)


def _create_polemarch_non_gst_item_tax_template():
    """Create one `Polemarch - Non-GST` Item Tax Template per company.

    `gst_treatment = "Non-GST"` is what India Compliance checks when
    deciding whether to compute GST on a Sales Invoice / Sales Order
    line. Linking this template to every Polemarch-branded Item (via
    `polemarch.overrides.item.validate`) makes IC zero out tax for
    those rows automatically — no document-level tax-template swap
    needed.

    Mithtech Services items intentionally have NO custom ITT — they
    use ERPNext's standard chart-of-accounts GST treatment, which
    handles intra- vs inter-state correctly via India Compliance's
    state-aware logic. The fee item carries HSN 997152 and gets the
    right 18% via the customer's place_of_supply.
    """
    for company in frappe.get_all("Company", pluck="name"):
        name = _itt_name(company, POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE)
        if frappe.db.exists("Item Tax Template", name):
            continue
        rows = _zero_rate_tax_rows(company)
        if not rows:
            # Company has no GST output accounts yet — skip for now;
            # next migrate after Chart of Accounts is set up will pick
            # it up (idempotent).
            continue
        frappe.get_doc(
            {
                "doctype": "Item Tax Template",
                "title": POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE,
                "company": company,
                "gst_treatment": "Non-GST",
                # India Compliance reads `gst_treatment = "Non-GST"`
                # to zero tax computation, but Frappe's `Item Tax
                # Template` requires `taxes` to have at least one
                # row (the `tax_type` field is mandatory). We supply
                # rows pointing at the company's GST output accounts
                # at rate 0 — same shape ERPNext's standard
                # "Exempted" ITT uses.
                "taxes": rows,
            }
        ).insert(ignore_permissions=True)


def _itt_name(company: str, title: str) -> str:
    return f"{title} - {frappe.db.get_value('Company', company, 'abbr')}"


def _zero_rate_tax_rows(company: str) -> list:
    """Return one row per GST output account at rate 0, suitable for
    Item Tax Template Detail. The accounts are looked up by the
    standard naming convention `Output Tax {CGST|SGST|IGST} - <abbr>`,
    skipping Refund / RCM variants."""
    rows = []
    for keyword in ("Output Tax CGST", "Output Tax SGST", "Output Tax IGST"):
        account = frappe.db.get_value(
            "Account",
            [
                ["company", "=", company],
                ["account_name", "like", f"{keyword}%"],
                ["account_name", "not like", "%Refund%"],
                ["account_name", "not like", "%RCM%"],
                ["is_group", "=", 0],
            ],
            "name",
        )
        if account:
            rows.append({"tax_type": account, "tax_rate": 0})
    return rows


def _create_processing_fee_item():
    """Service item used when a Medusa order carries a processing fee
    (HSN 997152, GST 18%). Lives under Mithtech Services brand so the
    fee invoice is correctly classified for GSTR-1."""
    if frappe.db.exists("Item", PROCESSING_FEE_ITEM_CODE):
        return
    services_group = (
        frappe.db.exists("Item Group", "Services")
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )
    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": PROCESSING_FEE_ITEM_CODE,
            "item_name": "Polemarch Processing Fee",
            "item_group": services_group,
            "brand": MITHTECH_SERVICES_BRAND,
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "include_item_in_manufacturing": 0,
            "description": "Processing / facilitation fee for Polemarch trades. HSN 997152, GST 18%.",
            "gst_hsn_code": PROCESSING_FEE_HSN if frappe.db.exists("GST HSN Code", PROCESSING_FEE_HSN) else None,
        }
    ).insert(ignore_permissions=True)


def _add_polemarch_naming_series():
    """Append POL-.YYYY.-.##### to Sales Invoice naming_series options
    via Property Setter. Idempotent — checks the existing options before
    rewriting."""
    field = frappe.get_meta("Sales Invoice").get_field("naming_series")
    if not field:
        return
    options = (field.options or "").strip()
    lines = [line.strip() for line in options.split("\n") if line.strip()]
    if POLEMARCH_NAMING_SERIES in lines:
        return
    lines.append(POLEMARCH_NAMING_SERIES)
    new_options = "\n".join(lines)
    frappe.db.set_value(
        "Property Setter",
        {"doc_type": "Sales Invoice", "field_name": "naming_series", "property": "options"},
        "value",
        new_options,
    )
    if not frappe.db.exists(
        "Property Setter",
        {"doc_type": "Sales Invoice", "field_name": "naming_series", "property": "options"},
    ):
        frappe.get_doc({
            "doctype": "Property Setter",
            "doctype_or_field": "DocField",
            "doc_type": "Sales Invoice",
            "field_name": "naming_series",
            "property": "options",
            "property_type": "Text",
            "value": new_options,
        }).insert(ignore_permissions=True)
    frappe.clear_cache(doctype="Sales Invoice")
