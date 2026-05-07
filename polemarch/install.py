import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


POLEMARCH_BRAND = "Polemarch"
MITHTECH_SERVICES_BRAND = "Mithtech Services"
POLEMARCH_CUSTOMER_GROUP = "Polemarch"
POLEMARCH_ITEM_GROUP = "Polemarch Securities"
POLEMARCH_TAX_TEMPLATE = "Polemarch - No GST"
MITHTECH_SERVICES_TAX_TEMPLATE = "Mithtech Services - GST 18%"
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
    _create_tax_templates_for_all_companies()
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


def _create_tax_templates_for_all_companies():
    for company in frappe.get_all("Company", pluck="name"):
        _create_no_gst_template(company)
        _create_gst_18_template(company)


def _template_name(company: str, suffix: str) -> str:
    return f"{suffix} - {frappe.db.get_value('Company', company, 'abbr')}"


def _create_no_gst_template(company: str):
    name = _template_name(company, POLEMARCH_TAX_TEMPLATE)
    if frappe.db.exists("Sales Taxes and Charges Template", name):
        return
    frappe.get_doc(
        {
            "doctype": "Sales Taxes and Charges Template",
            "title": POLEMARCH_TAX_TEMPLATE,
            "company": company,
            "is_default": 0,
            "taxes": [],
        }
    ).insert(ignore_permissions=True)


def _create_gst_18_template(company: str):
    """Mithtech Services GST 18% template — INTRA-STATE only (CGST 9 +
    SGST 9). India's GST machinery requires a separate template for
    inter-state (IGST 18) transactions; mixing both rate types in one
    template makes IC apply *all three* (36%) instead of letting it
    pick the right pair based on place_of_supply, since IC's intra/
    inter suppression is template-shape-aware.

    Polemarch's facilitation business is overwhelmingly intra-state
    (Maharashtra customers buying from MISPL, also in Maharashtra), so
    we ship one template here. If/when out-of-state Mithtech billing
    is needed, add a sibling `Mithtech Services - GST 18% Out-of-state`
    template with the IGST 18 row.
    """
    name = _template_name(company, MITHTECH_SERVICES_TAX_TEMPLATE)
    if frappe.db.exists("Sales Taxes and Charges Template", name):
        return
    output_cgst = _gst_account(company, "Output Tax CGST")
    output_sgst = _gst_account(company, "Output Tax SGST")
    if not (output_cgst and output_sgst):
        return
    frappe.get_doc(
        {
            "doctype": "Sales Taxes and Charges Template",
            "title": MITHTECH_SERVICES_TAX_TEMPLATE,
            "company": company,
            "is_default": 0,
            "taxes": [
                {"charge_type": "On Net Total", "account_head": output_cgst, "description": "CGST", "rate": 9},
                {"charge_type": "On Net Total", "account_head": output_sgst, "description": "SGST", "rate": 9},
            ],
        }
    ).insert(ignore_permissions=True)


def _gst_account(company: str, account_name_part: str):
    """Resolve an Output GST account by name fragment.

    Filters out Refund / RCM / Reverse-Charge accounts because they
    share the same prefix (e.g. `Output Tax CGST` AND `Output Tax CGST
    Refund` both match `LIKE '%Output Tax CGST%'`) — and ERPNext orders
    them alphabetically, so the broken `Refund` variant tends to win.
    Earlier templates created via the unfiltered query ended up with
    `Output Tax CGST Refund - MISPL` as the account_head, which India
    Compliance can't recognise as a regular GST account, so its
    intra/inter-state suppression silently failed and 36% tax got
    applied. The patch
    `polemarch.patches.v0_0_1.recreate_mithtech_tax_template` cleans
    up legacy rows that were created with this bug.
    """
    # Use the list-of-conditions form so we can chain TWO filters on
    # `account_name` (the dict form would silently let the second key
    # overwrite the first — a footgun that was the original cause of
    # the Refund-account bug).
    return frappe.db.get_value(
        "Account",
        [
            ["company", "=", company],
            ["account_name", "like", f"{account_name_part}%"],
            ["account_name", "not like", "%Refund%"],
            ["account_name", "not like", "%RCM%"],
            ["is_group", "=", 0],
        ],
        "name",
    )


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
