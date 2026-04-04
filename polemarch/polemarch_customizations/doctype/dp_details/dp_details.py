# Copyright (c) 2025, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document


class DPDetails(Document):
	def validate(self):
		self.validate_bo_id()
		self.validate_dp_id()
		self.validate_client_id()
		self.validate_pan()

	def validate_bo_id(self):
		if not self.bo_id:
			return
		if re.match(r"^IN\d{14}$", self.bo_id):
			self.depository = "NSDL"
		elif re.match(r"^\d{16}$", self.bo_id):
			self.depository = "CDSL"
		else:
			frappe.throw("Invalid BO ID. NSDL: IN + 14 digits, CDSL: 16 digits.")

	def validate_dp_id(self):
		if not self.dp_id:
			return
		if re.match(r"^IN\d{6}$", self.dp_id):
			self.depository = "NSDL"
		elif re.match(r"^\d{8}$", self.dp_id):
			self.depository = "CDSL"
		else:
			frappe.throw("Invalid DP ID. NSDL: IN + 6 digits, CDSL: 8 digits.")

	def validate_client_id(self):
		if self.client_id and not re.match(r"^\d{8}$", self.client_id):
			frappe.throw("Invalid Client ID. Must be exactly 8 digits.")

	def validate_pan(self):
		if self.primary_bo_pan and not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", self.primary_bo_pan):
			frappe.throw("Invalid PAN format. Expected: ABCDE1234F")
