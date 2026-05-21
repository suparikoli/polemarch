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
LOW_ORDER_FEE_ITEM_CODE = "POLEMARCH-LOW-ORDER-FEE"
LOW_ORDER_FEE_HSN = "997152"  # same SAC as processing fee — both are facilitation services


def after_install():
    setup()


def after_migrate():
    setup()


POLEMARCH_NAMING_SERIES = "POL-.YYYY.-.#####"


def setup():
    _ensure_polemarch_trading_module()
    _create_customer_group()
    _create_custom_fields()
    _make_hsn_optional_on_item()
    _add_polemarch_naming_series()
    _create_processing_fee_item()
    _create_low_order_fee_item()
    _seed_default_sync_mappings()


def _ensure_polemarch_trading_module():
    """Guarantee `Polemarch Trading` Module Def exists + its doctypes are
    schema-synced before any patches that depend on them run.

    Background: on a freshly-wiped site (`tabModule Def` row missing), the
    default `bench migrate` flow's schema-sync step skips the trading module
    — it scans `modules.txt`, but if there's no matching Module Def row it
    won't create one. Patches then run against nonexistent tables and
    silently no-op inside try/except. Operator has to manually
    `frappe.model.sync.sync_for("polemarch", force=1)` to recover.

    This guard runs on every `after_install` / `after_migrate` and:
      1. Creates the Module Def if missing.
      2. Triggers a non-forced sync of all polemarch modules — idempotent on
         healthy sites, schema-creating on dirty ones.
    """
    if not frappe.db.exists("Module Def", "Polemarch Trading"):
        frappe.get_doc(
            {
                "doctype": "Module Def",
                "module_name": "Polemarch Trading",
                "app_name": "polemarch",
            }
        ).insert(ignore_permissions=True)

    # Idempotent schema sync. `force=0` means existing doctypes are
    # untouched; missing ones get created. Avoids the silent-no-op patches.
    try:
        from frappe.model.sync import sync_for
        sync_for("polemarch", force=0)
    except Exception:
        # Don't block setup() on a sync hiccup — log and continue. Operator
        # can always run `bench migrate` or sync_for(force=1) manually.
        frappe.log_error(frappe.get_traceback(), "Polemarch Trading sync guard")


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
                "insert_after": "description",
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


def _make_hsn_optional_on_item():
    """India Compliance installs `Item.gst_hsn_code` as a mandatory
    Custom Field. Polemarch share items (brand=Polemarch) are
    securities under Schedule III — out of GST scope — and carry no
    HSN/SAC. Drop the `reqd` flag on the Custom Field so those rows
    can save with the HSN field empty. India Compliance still
    enforces HSN on Sales Invoice items via its own validate, but
    only for items whose `gst_treatment` is "Taxable"; Polemarch
    items get "Non-GST" treatment via the auto-linked
    `Polemarch - Non-GST` ITT, so the SI-level check passes too.

    Idempotent — flips the flag and updates `modified` so the
    in-process Frappe meta cache invalidates."""
    cf_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Item", "fieldname": "gst_hsn_code"},
        "name",
    )
    if not cf_name:
        return
    current = frappe.db.get_value("Custom Field", cf_name, "reqd")
    if current == 0:
        return
    frappe.db.set_value("Custom Field", cf_name, "reqd", 0)
    frappe.clear_cache(doctype="Item")


def _create_processing_fee_item():
    """Service item used when a Medusa order carries a processing fee
    (HSN 997152, GST 18%). Lives under Mithtech Services brand so the
    fee invoice is correctly classified for GSTR-1."""
    _create_service_fee_item(
        code=PROCESSING_FEE_ITEM_CODE,
        name="Polemarch Processing Fee",
        hsn=PROCESSING_FEE_HSN,
        description="Processing / facilitation fee for Polemarch trades. HSN 997152, GST 18%.",
    )


def _create_low_order_fee_item():
    """Low-order surcharge applied to small Polemarch trades (typically
    below ₹1L) to recoup the fixed processing cost. Same HSN as the
    processing fee — both are facilitation services from MISPL's
    perspective — but a separate Item so accounting can break out the
    two fee streams on GSTR-1 and the customer-facing invoice."""
    _create_service_fee_item(
        code=LOW_ORDER_FEE_ITEM_CODE,
        name="Polemarch Low Order Fee",
        hsn=LOW_ORDER_FEE_HSN,
        description="Low-order surcharge for small Polemarch trades (typically <₹1L). HSN 997152, GST 18%.",
    )


def _create_service_fee_item(*, code: str, name: str, hsn: str, description: str):
    """Shared scaffold for the two Mithtech-Services facilitation fee
    Items. Identical schema, just differ in code/name/description and
    HSN (currently same for both)."""
    if frappe.db.exists("Item", code):
        return
    services_group = (
        frappe.db.exists("Item Group", "Services")
        or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or "All Item Groups"
    )
    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": code,
            "item_name": name,
            "item_group": services_group,
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "include_item_in_manufacturing": 0,
            "description": description,
            "gst_hsn_code": hsn if frappe.db.exists("GST HSN Code", hsn) else None,
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
    # Classification — sync_customers.upsert_from_medusa enforces
    # customer_group = "Polemarch" as an invariant regardless of
    # whether Medusa sends a value, but this row makes it
    # configurable from the admin UI if a future requirement needs
    # different segmentation.
    ("metadata.customer_group",             "customer_group",                 None,                                "Defaults to 'Polemarch' if Medusa doesn't send a value (enforced as invariant by the sync code)."),
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
    # Classification — `polemarch.overrides.item.validate` enforces
    # item_group = "Polemarch Securities" for brand=Polemarch items
    # regardless of what Medusa sends. Mapping row is informational +
    # provides a config override hook if a future requirement needs
    # a different sub-group (e.g. "Polemarch Bonds").
    ("metadata.item_group",                 "item_group",                     None,                                "Defaults to 'Polemarch Securities' (enforced by the Item validate hook for brand=Polemarch)."),
    # NO HSN/SAC — Polemarch shares are excluded from GST under
    # Schedule III. The Item validate hook clears any HSN that
    # accidentally lands on a brand=Polemarch row.
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
