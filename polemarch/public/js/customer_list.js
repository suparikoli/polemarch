frappe.listview_settings["Customer"] = frappe.listview_settings["Customer"] || {};

(function () {
	const prev = frappe.listview_settings["Customer"].onload;
	frappe.listview_settings["Customer"].onload = function (listview) {
		if (typeof prev === "function") prev(listview);
		listview.page.add_inner_button(__("+ Polemarch Customer"), () => {
			polemarch_open_onboarding_dialog(() => listview.refresh());
		}).removeClass("btn-default").addClass("btn-primary");
	};
})();

window.polemarch_open_onboarding_dialog = function (on_done) {
	const d = new frappe.ui.Dialog({
		title: __("Onboard Polemarch Customer"),
		size: "large",
		fields: [
			{ fieldtype: "Section Break", label: __("Identity") },
			{ fieldname: "customer_name", fieldtype: "Data", label: __("Full Name"), reqd: 1 },
			{ fieldname: "email_id", fieldtype: "Data", label: __("Email"), reqd: 1, options: "Email" },
			{ fieldtype: "Column Break" },
			{ fieldname: "mobile_no", fieldtype: "Data", label: __("Mobile No"), reqd: 1 },
			{ fieldname: "pan", fieldtype: "Data", label: __("PAN"), reqd: 1, description: __("e.g. AAAPL1234C") },

			{ fieldtype: "Section Break", label: __("DP Details (Primary)") },
			{ fieldname: "dp_dp_id", fieldtype: "Data", label: __("DP ID"), reqd: 1 },
			{ fieldname: "dp_client_id", fieldtype: "Data", label: __("Client ID"), reqd: 1 },
			{ fieldname: "dp_bo_id", fieldtype: "Data", label: __("BO ID"), reqd: 1, description: __("16-digit demat account") },
			{ fieldtype: "Column Break" },
			{
				fieldname: "dp_depository",
				fieldtype: "Select",
				label: __("Depository"),
				options: "\nNSDL\nCDSL",
				reqd: 1,
			},
			{ fieldname: "dp_dp_name", fieldtype: "Data", label: __("DP Name"), reqd: 1, description: __("e.g. STOCK HOLDING CORPORATION OF INDIA LTD") },
			{ fieldname: "dp_broker_name", fieldtype: "Data", label: __("Broker Name") },
			{ fieldname: "dp_cmr_copy", fieldtype: "Attach", label: __("CMR Copy"), reqd: 1, description: __("Client Master Report PDF") },

			{ fieldtype: "Section Break", label: __("Bank Details (Primary)") },
			{ fieldname: "bank_bank_name", fieldtype: "Data", label: __("Bank Name"), reqd: 1 },
			{ fieldname: "bank_bank_branch", fieldtype: "Data", label: __("Branch") },
			{ fieldname: "bank_bank_code", fieldtype: "Data", label: __("IFSC"), reqd: 1, description: __("e.g. HDFC0000419") },
			{ fieldtype: "Column Break" },
			{ fieldname: "bank_ac_number", fieldtype: "Data", label: __("Account Number"), reqd: 1 },
			{ fieldname: "bank_micr", fieldtype: "Data", label: __("MICR") },
			{ fieldname: "bank_cheque_image", fieldtype: "Attach", label: __("Cancelled Cheque"), reqd: 1 },
		],
		primary_action_label: __("Create Customer"),
		primary_action(values) {
			const payload = {
				customer_name: values.customer_name,
				email_id: values.email_id,
				mobile_no: values.mobile_no,
				pan: values.pan,
				dp: {
					dp_id: values.dp_dp_id,
					client_id: values.dp_client_id,
					bo_id: values.dp_bo_id,
					depository: values.dp_depository,
					dp_name: values.dp_dp_name,
					broker_name: values.dp_broker_name,
					primary_bo_name: values.customer_name,
					primary_bo_pan: values.pan,
					cmr_copy: values.dp_cmr_copy,
				},
				bank: {
					bank_name: values.bank_bank_name,
					bank_branch: values.bank_bank_branch,
					bank_code: values.bank_bank_code,
					ac_number: values.bank_ac_number,
					account_holder: values.customer_name,
					cheque_image: values.bank_cheque_image,
					micr: values.bank_micr,
				},
			};

			d.disable_primary_action();
			frappe.call({
				method: "polemarch.api.onboarding.create_polemarch_customer",
				args: { payload },
				freeze: true,
				freeze_message: __("Creating Polemarch customer..."),
				callback: ({ message }) => {
					if (message && message.name) {
						frappe.show_alert({
							message: __("Customer {0} created.", [message.name]),
							indicator: "green",
						});
						d.hide();
						if (typeof on_done === "function") on_done();
						frappe.set_route("Form", "Customer", message.name);
					}
				},
				error: () => {
					d.enable_primary_action();
				},
			});
		},
	});
	d.show();
};
