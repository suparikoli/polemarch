// Validation Script for DP Details child table
frappe.ui.form.on('DP Details', {
    primary_bo_pan: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);
        let pan_regex = /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/;

        if (row.primary_bo_pan && !pan_regex.test(row.primary_bo_pan)) {
            frappe.msgprint(__('Invalid PAN format. Expected: ABCDE1234F'));
            frappe.model.set_value(cdt, cdn, 'primary_bo_pan', '');
        }
    },

    bo_id: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);

        if (row.bo_id) {
            if (/^IN\d{14}$/.test(row.bo_id)) {
                frappe.model.set_value(cdt, cdn, 'depository', 'NSDL');
            } else if (/^\d{16}$/.test(row.bo_id)) {
                frappe.model.set_value(cdt, cdn, 'depository', 'CDSL');
            } else {
                frappe.msgprint(__('Invalid BO ID. Must be 16 characters. NSDL: IN + 14 digits, CDSL: 16 digits.'));
                frappe.model.set_value(cdt, cdn, 'bo_id', '');
                return;
            }

            frappe.model.set_value(cdt, cdn, 'dp_id', row.bo_id.slice(0, 8));
            frappe.model.set_value(cdt, cdn, 'client_id', row.bo_id.slice(8));
        }
    },

    dp_id: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);

        if (row.dp_id) {
            if (/^IN\d{6}$/.test(row.dp_id)) {
                frappe.model.set_value(cdt, cdn, 'depository', 'NSDL');
            } else if (/^\d{8}$/.test(row.dp_id)) {
                frappe.model.set_value(cdt, cdn, 'depository', 'CDSL');
            } else {
                frappe.msgprint(__('Invalid DP ID. NSDL: IN + 6 digits, CDSL: 8 digits.'));
                frappe.model.set_value(cdt, cdn, 'dp_id', '');
                return;
            }
        }

        if (row.dp_id && row.client_id) {
            frappe.model.set_value(cdt, cdn, 'bo_id', row.dp_id + row.client_id);
        }
    },

    client_id: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);

        if (row.client_id && !/^\d{8}$/.test(row.client_id)) {
            frappe.msgprint(__('Invalid Client ID. Must be exactly 8 digits.'));
            frappe.model.set_value(cdt, cdn, 'client_id', '');
            return;
        }

        if (row.dp_id && row.client_id) {
            frappe.model.set_value(cdt, cdn, 'bo_id', row.dp_id + row.client_id);
        }
    },

    is_primary: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);
        if (row.is_primary && frm.doc.custom_dp_details) {
            frm.doc.custom_dp_details.forEach(r => {
                if (r.name !== row.name) {
                    frappe.model.set_value(r.doctype, r.name, 'is_primary', 0);
                }
            });
            frm.refresh_field('custom_dp_details');
        }
    }
});
