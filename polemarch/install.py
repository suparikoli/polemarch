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
            # Mithtech-only escape hatch. Customers serviced through other
            # MISPL lines of business (e.g. consulting / staff augmentation)
            # never trade on Polemarch and shouldn't see the trading UI.
            # When this is ticked: the validate hook forces
            # `custom_is_polemarch_customer = 0`, the Polemarch dashboard
            # tab is hidden, and the KYC fields + indicator + action
            # buttons all collapse out of the form.
            {
                "fieldname": "custom_is_mithtech_only",
                "label": "Mithtech Only Customer",
                "fieldtype": "Check",
                "no_copy": 1,
                "in_standard_filter": 1,
                "insert_after": "customer_group",
                "description": "Tick if this customer is for Mithtech-only services and should NOT see the Polemarch / KYC tabs.",
            },
            {
                "fieldname": "custom_is_polemarch_customer",
                "label": "Is Polemarch Customer",
                "fieldtype": "Check",
                "read_only": 1,
                "no_copy": 1,
                "in_standard_filter": 1,
                "insert_after": "custom_bank_details",
                "depends_on": "eval:!doc.custom_is_mithtech_only",
                "description": "Auto-set when a DP Details row is added or when the customer is in the Polemarch group.",
            },
            {
                "fieldname": "custom_kyc_status",
                "label": "KYC Status",
                "fieldtype": "Select",
                "options": "Not Started\nIn Review\nVerified\nRejected",
                "default": "Not Started",
                "in_standard_filter": 1,
                "no_copy": 1,
                "insert_after": "custom_is_polemarch_customer",
                "depends_on": "eval:!doc.custom_is_mithtech_only",
            },
            {
                "fieldname": "custom_kyc_status_reason",
                "label": "KYC Status Reason",
                "fieldtype": "Small Text",
                "depends_on": "eval:!doc.custom_is_mithtech_only && doc.custom_kyc_status === 'Rejected'",
                "no_copy": 1,
                "insert_after": "custom_kyc_status",
            },
            {
                "fieldname": "custom_kyc_verified_on",
                "label": "KYC Verified On",
                "fieldtype": "Datetime",
                "read_only": 1,
                "no_copy": 1,
                "depends_on": "eval:!doc.custom_is_mithtech_only && doc.custom_kyc_status === 'Verified'",
                "insert_after": "custom_kyc_status_reason",
            },
            # Customer name + contact details (first / middle / last name,
            # email, phone) live on the standard ERPNext Contact linked to
            # this Customer — see the Contacts panel in the Desk sidebar.
            # The old custom_first_name / custom_middle_name / custom_last_name
            # fields were dropped in v0_16_0; use the Contact instead.
            {
                "fieldname": "custom_dob",
                "label": "Date of Birth",
                "fieldtype": "Date",
                "insert_after": "custom_kyc_verified_on",
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
                "description": "Polemarch-issued client ID, format NNNNYYWW.",
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
                "depends_on": "eval:!doc.custom_is_mithtech_only && doc.custom_is_polemarch_customer",
            },
            {
                "fieldname": "custom_polemarch_dashboard_html",
                "label": "Polemarch Dashboard",
                "fieldtype": "HTML",
                "insert_after": "custom_polemarch_dashboard_tab",
                "depends_on": "eval:!doc.custom_is_mithtech_only && doc.custom_is_polemarch_customer",
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
    """Service item for the Polemarch processing / facilitation fee
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


# The Frappe app is an internal-only ERPNext customization; any external
# integration is the responsibility of whatever upstream system calls
# Frappe's REST API.
