# Copyright (c) 2025, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document


class BankDetails(Document):
	def validate(self):
		self.validate_ac_number()
		self.validate_bank_code()
		self.validate_micr()

	def validate_ac_number(self):
		if self.ac_number and not re.match(r"^\d+$", self.ac_number):
			frappe.throw(f"Bank Account Number must be numeric (Row {self.idx})")

	def validate_bank_code(self):
		if not self.bank_code:
			return
		ifsc = re.match(r"^[A-Z]{4}0[A-Z0-9]{6}$", self.bank_code)
		swift = re.match(r"^[A-Z0-9]{8}([A-Z0-9]{3})?$", self.bank_code)
		if not ifsc and not swift:
			frappe.throw(f"Invalid IFSC / SWIFT / BIC code in Row {self.idx}")

	def validate_micr(self):
		if self.micr and not re.match(r"^\d+$", self.micr):
			frappe.throw(f"MICR must be numeric (Row {self.idx})")
