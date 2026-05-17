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
    _seed_default_sync_mappings()


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
            # Registered name (as per PAN) — split form so the
            # Medusa-side mapper can write each part separately.
            {
                "fieldname": "custom_first_name",
                "label": "First Name (PAN)",
                "fieldtype": "Data",
                "insert_after": "custom_kyc_verified_on",
            },
            {
                "fieldname": "custom_middle_name",
                "label": "Middle Name (PAN)",
                "fieldtype": "Data",
                "insert_after": "custom_first_name",
            },
            {
                "fieldname": "custom_last_name",
                "label": "Last Name (PAN)",
                "fieldtype": "Data",
                "insert_after": "custom_middle_name",
            },
            {
                "fieldname": "custom_dob",
                "label": "Date of Birth",
                "fieldtype": "Date",
                "insert_after": "custom_last_name",
            },
            # Aadhaar — store last-4 + SHA-256 hash, never the raw
            # 12-digit number. Mapper transforms (`Mask Aadhaar`)
            # handle the redaction at write time.
            {
                "fieldname": "custom_aadhaar_last4",
                "label": "Aadhaar (last 4)",
                "fieldtype": "Data",
                "length": 4,
                "no_copy": 1,
                "insert_after": "custom_dob",
                "description": "Last 4 digits only. UIDAI rules forbid storing the raw 12-digit Aadhaar in third-party systems.",
            },
            {
                "fieldname": "custom_aadhaar_hash",
                "label": "Aadhaar (hash)",
                "fieldtype": "Data",
                "length": 64,
                "no_copy": 1,
                "read_only": 1,
                "hidden": 1,
                "insert_after": "custom_aadhaar_last4",
                "description": "SHA-256 of the full Aadhaar number for matching. Never displayed.",
            },
            {
                "fieldname": "custom_address_as_per_pan",
                "label": "Address (as per PAN)",
                "fieldtype": "Small Text",
                "insert_after": "custom_aadhaar_hash",
            },
            {
                "fieldname": "custom_client_id",
                "label": "Polemarch Client ID",
                "fieldtype": "Data",
                "unique": 1,
                "no_copy": 1,
                "in_standard_filter": 1,
                "insert_after": "custom_address_as_per_pan",
                "description": "Polemarch-issued client ID, format NNNNYYWW. Set when the Medusa customer record is created.",
            },
            {
                "fieldname": "custom_vba_id",
                "label": "Verified Bank Account (VBA)",
                "fieldtype": "Data",
                "no_copy": 1,
                "insert_after": "custom_client_id",
                "description": "Pointer to the customer's verified primary bank (penny-drop or NPCI verified). Format `bank_details:<rowname>`.",
            },
            {
                "fieldname": "custom_polemarch_dashboard_tab",
                "label": "Polemarch",
                "fieldtype": "Tab Break",
                "insert_after": "custom_vba_id",
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
            # Fee breakdown — Medusa carries platform fee / low-order
            # fee / stamp duty in `order.metadata`. The Polemarch app
            # mirrors them onto the SI for GSTR-1 and customer-
            # reporting visibility.
            {
                "fieldname": "custom_platform_fee",
                "label": "Platform Fee",
                "fieldtype": "Currency",
                "options": "currency",
                "insert_after": "custom_medusa_order_id",
            },
            {
                "fieldname": "custom_low_order_fee",
                "label": "Low Order Fee",
                "fieldtype": "Currency",
                "options": "currency",
                "insert_after": "custom_platform_fee",
            },
            {
                "fieldname": "custom_stamp_duty",
                "label": "Stamp Duty",
                "fieldtype": "Currency",
                "options": "currency",
                "insert_after": "custom_low_order_fee",
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


# ────────────────────────────────────────────────────────────────────
# Default Polemarch Sync Mapping
# ────────────────────────────────────────────────────────────────────

DEFAULT_CUSTOMER_MAPPINGS = [
    # Identity (Medusa Customer.* → ERPNext Customer.*)
    ("first_name",                          "custom_first_name",              None,                                "Registered first name (as per PAN)"),
    ("metadata.middle_name",                "custom_middle_name",             None,                                "Registered middle name (as per PAN)"),
    ("last_name",                           "custom_last_name",               None,                                "Registered last name (as per PAN)"),
    ("metadata.full_name",                  "customer_name",                  "Concatenate First+Middle+Last",     "Combined customer name (used in invoices and shipping)"),
    ("email",                               "email_id",                       None,                                "Primary email; used by Frappe's Communication API"),
    ("phone",                               "mobile_no",                      None,                                "Primary phone; +91 prefix expected"),
    # KYC + regulatory
    ("metadata.pan",                        "pan",                            "Upper",                             "PAN — 10 alphanumeric chars"),
    ("metadata.aadhaar_last4",              "custom_aadhaar_last4",           None,                                "Aadhaar last 4 digits — never the full number (UIDAI rule)"),
    ("metadata.aadhaar_hash",               "custom_aadhaar_hash",            None,                                "SHA-256 of full Aadhaar for matching; written by Medusa side only"),
    ("metadata.dob",                        "custom_dob",                     "ISO Date",                          "Date of birth (ISO format)"),
    ("metadata.address_as_per_pan",         "custom_address_as_per_pan",      "Trim",                              "Address exactly as printed on PAN — for KYC records"),
    # Polemarch-issued identifiers
    ("metadata.client_id",                  "custom_client_id",               None,                                "Polemarch client ID (NNNNYYWW, weekly resetting)"),
    ("metadata.kyc_status",                 "custom_kyc_status",              None,                                "Maps medusa kyc state → ERPNext select: Verified / In Review / Rejected / Not Started"),
    ("metadata.kyc_rejection_reason",       "custom_kyc_status_reason",       None,                                "Free-text reason from KYC vendor (visible on Customer form)"),
    # Audit identity
    ("id",                                  "custom_medusa_customer_id",      None,                                "Medusa customer id (audit reference)"),
]

DEFAULT_BANK_MAPPINGS = [
    # Each row is per element of `customer.metadata.bank_accounts[]`
    ("bank_name",                           "bank_name",                      None,                                "Bank name (HDFC, ICICI, etc.)"),
    ("ifsc",                                "bank_code",                      "Upper",                             "IFSC code — 11 chars, uppercase"),
    ("ac_number",                           "ac_number",                      None,                                "Account number — digits only"),
    ("account_holder",                      "account_holder",                 None,                                "Name on the bank account (PAN match required for VBA)"),
    ("micr",                                "micr",                           None,                                "MICR code (9 digits)"),
    ("branch",                              "bank_branch",                    None,                                "Branch name"),
    ("cheque_image_url",                    "cheque_image",                   None,                                "Public URL of the cancelled cheque image"),
    ("is_primary",                          "is_primary",                     None,                                "Mark primary bank — only one is_primary=1 per customer"),
    ("is_verified",                         "custom_vba_status",              None,                                "Verified Bank Account status (after penny-drop / NPCI check)"),
]

DEFAULT_DEMAT_MAPPINGS = [
    ("depository",                          "depository",                     "Upper",                             "NSDL or CDSL"),
    ("dp_id",                               "dp_id",                          None,                                "DP ID — issued by the depository to the DP"),
    ("client_id",                           "client_id",                      None,                                "Client ID at the DP (NOT the polemarch client_id)"),
    ("bo_id",                               "bo_id",                          None,                                "Beneficial Owner ID (BOID) — full 16-digit demat account number"),
    ("dp_name",                             "dp_name",                        None,                                "Friendly DP name (e.g. Zerodha, Groww)"),
    ("broker_name",                         "broker_name",                    None,                                "Broker name if different from DP"),
    ("primary_bo_name",                     "primary_bo_name",                None,                                "Name on the demat account (must match PAN)"),
    ("primary_bo_pan",                      "primary_bo_pan",                 "Upper",                             "PAN of the primary BO holder"),
    ("cmr_url",                             "cmr_copy",                       None,                                "CMR (Client Master Report) PDF URL — uploaded during KYC"),
    ("is_primary",                          "is_primary",                     None,                                "Primary demat marker — only one per customer"),
]

DEFAULT_ITEM_MAPPINGS = [
    ("metadata.isin",                       "custom_isin",                    "Upper",                             "ISIN — 12-char uppercase identifier (e.g. INE002A01018)"),
    ("title",                               "item_name",                      None,                                "Share name — e.g. 'Reliance Industries Ltd'"),
    ("handle",                              "item_code",                      None,                                "Slug — used as the ERPNext Item primary key"),
    ("metadata.rta",                        "custom_rta",                     None,                                "Registrar and Transfer Agent (Link Intime, Karvy, etc.)"),
    ("metadata.last_traded_price",          "custom_last_traded_price",       None,                                "LTP from market data — refreshed by the price-scraper job"),
    # NO HSN/SAC — shares are excluded from GST under Schedule III.
    # The non-GST treatment comes from the `Polemarch - Non-GST` ITT
    # auto-linked by `polemarch.overrides.item.validate`.
]

DEFAULT_ORDER_MAPPINGS = [
    ("id",                                  "custom_medusa_order_id",         None,                                "Medusa order id (audit reference)"),
    ("display_id",                          "po_no",                          None,                                "Human-readable order number (shown to the buyer)"),
    ("metadata.platform_fee_paise",         "custom_platform_fee",            "Paise to Rupees",                   "Platform fee from Medusa, stored in paise; converted to rupees"),
    ("metadata.low_order_fee_paise",        "custom_low_order_fee",           "Paise to Rupees",                   "Low-order surcharge applied below the threshold (e.g. < ₹1L)"),
    ("metadata.stamp_duty_paise",           "custom_stamp_duty",              "Paise to Rupees",                   "0.015% stamp duty as per state regulations"),
]

DEFAULT_ORDER_ITEM_MAPPINGS = [
    # Each row is per element of `order.items[]`
    ("variant.metadata.isin",               "custom_isin",                    "Upper",                             "ISIN — written onto the Sales Invoice Item row"),
    ("variant.product.title",               "item_name",                      None,                                "Share name on the line item"),
    ("variant.sku",                         "item_code",                      None,                                "SKU — resolves to ERPNext Item via item_code"),
    ("quantity",                            "qty",                            None,                                "Number of shares"),
    ("unit_price",                          "rate",                           "Paise to Rupees",                   "Per-share price; Medusa stores in paise"),
]


def _seed_default_sync_mappings():
    """Insert default Polemarch Sync Mapping rows. Idempotent —
    skips any (section, medusa_path) pair already present so admins
    can edit the seed rows in place without them being overwritten on
    the next migrate."""
    settings_name = "Polemarch Sync Mapping"
    if not frappe.db.exists("DocType", settings_name):
        # First migrate before the doctype itself is synced — bail.
        return
    doc = frappe.get_single(settings_name)

    _merge_rows(doc, "customer_mappings",      DEFAULT_CUSTOMER_MAPPINGS)
    _merge_rows(doc, "bank_account_mappings",  DEFAULT_BANK_MAPPINGS)
    _merge_rows(doc, "demat_account_mappings", DEFAULT_DEMAT_MAPPINGS)
    _merge_rows(doc, "item_mappings",          DEFAULT_ITEM_MAPPINGS)
    _merge_rows(doc, "order_mappings",         DEFAULT_ORDER_MAPPINGS)
    _merge_rows(doc, "order_item_mappings",    DEFAULT_ORDER_ITEM_MAPPINGS)

    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)


def _merge_rows(parent, table_field: str, defaults: list):
    existing_paths = {r.medusa_path for r in parent.get(table_field) or []}
    for medusa_path, erp_field, transform, description in defaults:
        if medusa_path in existing_paths:
            continue
        parent.append(table_field, {
            "is_enabled": 1,
            "medusa_path": medusa_path,
            "erpnext_field": erp_field,
            "transform": transform or "",
            "direction": "Bidirectional",
            "is_required": 0,
            "description": description,
        })
