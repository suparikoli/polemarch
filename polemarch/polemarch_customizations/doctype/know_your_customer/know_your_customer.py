# Copyright (c) 2025, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document


class KnowYourCustomer(Document):

    def validate(self):
        """Run validations on child tables"""
        # Validate DP Details
        if hasattr(self, 'dp_details'):
            for row in self.dp_details:
                self.validate_dp_details(row)

        # Validate Bank Details
        if hasattr(self, 'bank_details'):
            for row in self.bank_details:
                self.validate_bank_details(row)

    def validate_dp_details(self, row):
        """Validate DP Details row"""

        # 1. PAN Validation: 5 letters + 4 digits + 1 letter
        pan_regex = r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$'
        if row.primary_bo_pan and not re.match(pan_regex, row.primary_bo_pan):
            frappe.throw(f"Invalid PAN format in row {row.idx}: {row.primary_bo_pan}")

        # 2. Client ID Validation: Exactly 8 digits
        client_regex = r'^\d{8}$'
        if row.client_id and not re.match(client_regex, row.client_id):
            frappe.throw(f"Invalid Client ID in row {row.idx}: {row.client_id}")

        # 3. DP ID Validation
        if row.dp_id:
            if row.dp_id.startswith("IN"):  # NSDL
                dp_regex = r'^IN\d{6}$'
                if not re.match(dp_regex, row.dp_id):
                    frappe.throw(f"Invalid NSDL DP ID in row {row.idx}: {row.dp_id}")
            else:  # CDSL
                dp_regex = r'^\d{8}$'
                if not re.match(dp_regex, row.dp_id):
                    frappe.throw(f"Invalid CDSL DP ID in row {row.idx}: {row.dp_id}")

        # 4. BO ID Validation
        if row.bo_id:
            if row.bo_id.startswith("IN"):  # NSDL
                bo_regex = r'^IN\d{14}$'
                if not re.match(bo_regex, row.bo_id):
                    frappe.throw(f"Invalid NSDL BO ID in row {row.idx}: {row.bo_id}")
            else:  # CDSL
                bo_regex = r'^\d{16}$'
                if not re.match(bo_regex, row.bo_id):
                    frappe.throw(f"Invalid CDSL BO ID in row {row.idx}: {row.bo_id}")

        # 5. Auto-generate BO ID if DP ID + Client ID are present
        if row.dp_id and row.client_id and not row.bo_id:
            row.bo_id = row.dp_id + row.client_id

        # 6. Auto-generate DP ID and Client ID if BO ID is present
        if row.bo_id and (not row.dp_id or not row.client_id):
            if row.bo_id.startswith("IN"):  # NSDL
                row.dp_id = row.bo_id[:8]
                row.client_id = row.bo_id[8:]
            else:  # CDSL
                row.dp_id = row.bo_id[:8]
                row.client_id = row.bo_id[8:]

        # 7. Depository auto-selection
        if row.dp_id and row.dp_id.startswith("IN"):
            row.depository = "NSDL"
        else:
            row.depository = "CDSL"

    def validate_bank_details(self, row):
        """Validate Bank Details row"""
        # 1. Mandatory fields if is_primary
        if row.is_primary:
            if not row.bank_name:
                frappe.throw(f"Bank Name is required for primary bank in row {row.idx}")
            if not row.bank_branch:
                frappe.throw(f"Bank Branch is required for primary bank in row {row.idx}")
            if not row.account_holder:
                frappe.throw(f"Account Holder's Name is required for primary bank in row {row.idx}")

        # 2. Bank Account Number numeric & length
        if row.ac_number and not row.ac_number.isdigit():
            frappe.throw(f"Bank Account Number must be numeric in row {row.idx}")
        if row.ac_number and (len(row.ac_number) < 9 or len(row.ac_number) > 18):
            frappe.throw(f"Bank Account Number must be 9-18 digits in row {row.idx}")

        # 3. IFSC / SWIFT / BIC validation
        if row.bank_code:
            if re.match(r'^[A-Z]{4}0[A-Z0-9]{6}$', row.bank_code):
                pass  # valid IFSC
            elif re.match(r'^[A-Z0-9]{8}([A-Z0-9]{3})?$', row.bank_code):
                pass  # valid SWIFT/BIC
            else:
                frappe.throw(f"Invalid IFSC / SWIFT / BIC in row {row.idx}: {row.bank_code}")

        # 4. MICR numeric if provided
        if row.micr and not row.micr.isdigit():
            frappe.throw(f"MICR must be numeric in row {row.idx}")
