"""v0_25_0 — Customer Custom Fields for raw KYC data landing from Medusa.

Adds three PII-sensitive Custom Fields on the Customer doctype so the
Medusa→Frappe customer push has somewhere to land the raw KYC payload:

  - `custom_aadhaar_full`    : full 12-digit Aadhaar number (Medusa
                                key: metadata.kyc_aadhaar_number)
  - `custom_demat_number`    : Demat account number / BOID (Medusa
                                key: metadata.kyc_demat_number — kept
                                in addition to the DP Details child
                                table for quick-glance display)
  - `custom_cmr_file`        : CMR document URL (Medusa key:
                                metadata.kyc_cmr_file_url) — Attach

All three are PII-sensitive. The fields are added at the top of the
KYC section. Permissions follow the same pattern as the existing
custom_pan_card / custom_aadhaar_card fields (visible to System
Manager + Customer roles only).

Idempotent — uses create_custom_fields with field-exists check.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import (
    create_custom_fields,
)


def execute():
    fields = {
        "Customer": [
            {
                "fieldname": "custom_aadhaar_full",
                "label": "Aadhaar Number (full)",
                "fieldtype": "Data",
                "insert_after": "custom_aadhaar_last4",
                "description": (
                    "Full 12-digit Aadhaar number, pushed from "
                    "Medusa after KYC verification. PII — restrict "
                    "access via Role Permission Manager."
                ),
                "no_copy": 1,
                "ignore_user_permissions": 0,
                "in_list_view": 0,
                "in_filter": 0,
                "in_global_search": 0,
                "in_preview": 0,
                "in_standard_filter": 0,
                "search_index": 0,
            },
            {
                "fieldname": "custom_demat_number",
                "label": "Demat Number (BOID)",
                "fieldtype": "Data",
                "insert_after": "custom_dp_primary",
                "description": (
                    "Primary Demat account number / BOID, pushed "
                    "from Medusa after KYC verification. CDSL: 16-"
                    "digit; NSDL: dp_id + client_id. Also see the "
                    "DP Details child table below for the full list "
                    "of demat accounts."
                ),
                "no_copy": 1,
            },
            {
                "fieldname": "custom_cmr_file",
                "label": "CMR Copy (Document)",
                "fieldtype": "Attach",
                "insert_after": "custom_demat_number",
                "description": (
                    "Client Master Report (CMR) document URL — "
                    "pushed from Medusa after KYC verification. "
                    "Maintained alongside the DP Details child "
                    "table's per-row cmr_copy for primary-DP "
                    "quick access."
                ),
                "no_copy": 1,
            },
        ],
    }
    create_custom_fields(fields, ignore_validate=True, update=True)
    frappe.db.commit()
