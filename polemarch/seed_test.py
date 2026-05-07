"""One-shot test-data seeder for test.polemarch.in. Idempotent — safe to re-run."""
import frappe
from frappe.utils import nowdate

POLEMARCH_BRAND = "Polemarch"
POLEMARCH_CG = "Polemarch"
POLEMARCH_IG = "Polemarch Securities"
TEST_COMPANY = None  # discovered at runtime


def _company():
    global TEST_COMPANY
    if TEST_COMPANY:
        return TEST_COMPANY
    # pin to MISPL — matches install.py defaults
    if frappe.db.exists("Company", "Mithtech Innovative Solutions PVT LTD"):
        TEST_COMPANY = "Mithtech Innovative Solutions PVT LTD"
    else:
        c = frappe.get_doc({
            "doctype": "Company",
            "company_name": "Mithtech Innovative Solutions PVT LTD",
            "abbr": "MISPL",
            "default_currency": "INR",
            "country": "India",
        }).insert(ignore_permissions=True)
        TEST_COMPANY = c.name
    return TEST_COMPANY


def _gstin_check_digit(gstin_14):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    factor, total, mod = 1, 0, 36
    for ch in gstin_14:
        digit = factor * chars.find(ch)
        digit = (digit // mod) + (digit % mod)
        total += digit
        factor = 2 if factor == 1 else 1
    return chars[(mod - (total % mod)) % mod]


def _make_gstin(state_code, pan, entity_digit="1"):
    base = f"{state_code}{pan}{entity_digit}Z"  # 14 chars
    return base + _gstin_check_digit(base)


def _ensure_company_address(company):
    """Create a Company Address with GSTIN for India Compliance."""
    title = f"{company} - HQ"
    addr_name = frappe.db.get_value("Address", {"address_title": title}, "name")
    company_gstin = _make_gstin("27", "AABCM1234E")  # MH state 27, valid checksum
    if not addr_name:
        addr = frappe.get_doc({
            "doctype": "Address",
            "address_title": title,
            "address_type": "Office",
            "address_line1": "Unit 304, Polemarch Tower",
            "address_line2": "Bandra Kurla Complex",
            "city": "Mumbai",
            "state": "Maharashtra",
            "country": "India",
            "pincode": "400051",
            "gst_state": "Maharashtra",
            "gst_state_number": "27",
            "gstin": company_gstin,
            "is_primary_address": 1,
            "is_shipping_address": 1,
            "links": [{"link_doctype": "Company", "link_name": company}],
        })
        addr.flags.ignore_mandatory = True
        addr.insert(ignore_permissions=True, ignore_mandatory=True)
        addr_name = addr.name
    if not frappe.db.get_value("Company", company, "gstin"):
        frappe.db.set_value("Company", company, "gstin", company_gstin, update_modified=False)
    return addr_name


def _ensure_warehouse():
    company = _company()
    abbr = frappe.db.get_value("Company", company, "abbr")
    wh = f"Stores - {abbr}"
    if not frappe.db.exists("Warehouse", wh):
        # try any warehouse for the company
        wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
    return wh


def _ensure_uom():
    if not frappe.db.exists("UOM", "Nos"):
        frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(ignore_permissions=True)


SHARE_HSN = "998311"  # financial / advisory; securities themselves aren't goods but India Compliance forces a code


def _ensure_hsn():
    if not frappe.db.exists("GST HSN Code", SHARE_HSN):
        frappe.get_doc({
            "doctype": "GST HSN Code",
            "hsn_code": SHARE_HSN,
            "description": "Management consulting and financial services (placeholder for share trades).",
        }).insert(ignore_permissions=True)


def ensure_items():
    """Create 2 Polemarch-branded share Items if missing."""
    _ensure_uom()
    _ensure_hsn()
    items = [
        ("RELIANCE-EQ", "Reliance Industries Ltd", "INE002A01018"),
        ("TCS-EQ", "Tata Consultancy Services Ltd", "INE467B01029"),
    ]
    created = []
    for code, name, isin in items:
        if frappe.db.exists("Item", code):
            created.append(("exists", code))
            continue
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": code,
            "item_name": name,
            "item_group": POLEMARCH_IG,
            "brand": POLEMARCH_BRAND,
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "include_item_in_manufacturing": 0,
            "description": f"Listed equity share — {name}",
            "gst_hsn_code": SHARE_HSN,
        })
        # custom_isin field is on Item (added via custom field)
        doc.set("custom_isin", isin)
        doc.flags.from_medusa_sync = True  # avoid sync push
        doc.insert(ignore_permissions=True)
        created.append(("created", code))
    return created


def ensure_customer():
    """Create a Polemarch customer with DP Details, Bank Details, KYC = Verified."""
    name = "Test Polemarch Customer"
    if frappe.db.exists("Customer", name):
        cust = frappe.get_doc("Customer", name)
    else:
        cust = frappe.new_doc("Customer")
        cust.customer_name = name
        cust.customer_type = "Individual"
        cust.customer_group = POLEMARCH_CG
        cust.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
        cust.email_id = "test.customer@example.com"
        cust.mobile_no = "9999900000"
        cust.set("pan", "ABCDE1234F")
        cust.set("custom_kyc_status", "Verified")
    # DP Details child
    cust.set("custom_dp_details", [])
    cust.append("custom_dp_details", {
        "dp_name": "CDSL Demat Services",
        "depository": "CDSL",
        "broker_name": "Test Broker",
        "dp_id": "12081600",
        "client_id": "00012345",
        "bo_id": "1208160000012345",
        "is_primary": 1,
        "primary_bo_name": "Test Polemarch Customer",
        "primary_bo_pan": "ABCDE1234F",
        "cmr_copy": "/files/placeholder.txt",  # test seed: skip real upload
    })
    # Bank Details child
    cust.set("custom_bank_details", [])
    cust.append("custom_bank_details", {
        "bank_name": "HDFC Bank",
        "bank_branch": "Bandra Kurla Complex",
        "ac_number": "50100123456789",
        "bank_code": "HDFC0000001",
        "account_holder": "Test Polemarch Customer",
        "is_primary": 1,
        "cheque_image": "/files/placeholder.txt",  # test seed: skip real upload
    })
    cust.flags.from_medusa_sync = True
    cust.flags.ignore_mandatory = True
    if cust.is_new():
        cust.insert(ignore_permissions=True, ignore_mandatory=True)
    else:
        cust.save(ignore_permissions=True)
    return cust.name


def ensure_sales_invoice(customer_name):
    """Create + submit a Sales Invoice with both Polemarch items."""
    company = _company()
    company_address = _ensure_company_address(company)
    wh = _ensure_warehouse()
    si = frappe.new_doc("Sales Invoice")
    si.customer = customer_name
    si.company = company
    si.company_address = company_address
    si.posting_date = nowdate()
    si.due_date = nowdate()
    si.naming_series = "POL-.YYYY.-.#####"
    si.append("items", {
        "item_code": "RELIANCE-EQ",
        "qty": 100,
        "rate": 2850.50,
        "warehouse": wh,
    })
    si.append("items", {
        "item_code": "TCS-EQ",
        "qty": 50,
        "rate": 4120.75,
        "warehouse": wh,
    })
    si.flags.from_medusa_sync = True  # suppress medusa push
    si.set_missing_values()
    si.insert(ignore_permissions=True)
    si.submit()
    return si.name


def ensure_mithtech_invoice(customer_name):
    """Sales Invoice using the Mithtech Services brand processing-fee item.
    The validate hook should pick the 'Mithtech Services - GST 18%' template
    and leave custom_is_polemarch_invoice = 0."""
    company = _company()
    company_address = _ensure_company_address(company)
    wh = _ensure_warehouse()
    si = frappe.new_doc("Sales Invoice")
    si.customer = customer_name
    si.company = company
    si.company_address = company_address
    si.posting_date = nowdate()
    si.due_date = nowdate()
    # default naming series — NOT POL-, so we can see the difference
    si.append("items", {
        "item_code": "POLEMARCH-PROC-FEE",
        "qty": 1,
        "rate": 500.00,
        "warehouse": wh,
    })
    si.flags.from_medusa_sync = True
    si.set_missing_values()
    si.insert(ignore_permissions=True)
    si.submit()
    return si.name


def seed():
    items = ensure_items()
    cust = ensure_customer()
    si_pol = ensure_sales_invoice(cust)
    si_mith = ensure_mithtech_invoice(cust)
    frappe.db.commit()
    return {
        "items": items,
        "customer": cust,
        "polemarch_invoice": si_pol,
        "mithtech_invoice": si_mith,
    }
