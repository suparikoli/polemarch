// Client Script for DP Details child table
frappe.ui.form.on('DP Details', {

    // Validate Primary BO PAN (ABCDE1234F)
    primary_bo_pan: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        let pan_regex = /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/;

        if (row.primary_bo_pan && !pan_regex.test(row.primary_bo_pan)) {
            frappe.msgprint(__('Invalid PAN format. Expected: ABCDE1234F'));
            frappe.model.set_value(cdt, cdn, 'primary_bo_pan', '');
        }
    },

    // Validate BO ID (16 characters, format depends on depository)
    bo_id: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        if (row.bo_id) {
            if (/^IN\d{14}$/.test(row.bo_id)) {
                // NSDL format
                frappe.model.set_value(cdt, cdn, 'depository', 'NSDL');
            } else if (/^\d{16}$/.test(row.bo_id)) {
                // CDSL format
                frappe.model.set_value(cdt, cdn, 'depository', 'CDSL');
            } else {
                frappe.msgprint(__('Invalid BO ID. Must be 16 characters. NSDL: IN + 14 digits, CDSL: 16 digits.'));
                frappe.model.set_value(cdt, cdn, 'bo_id', '');
                return;
            }

            // Auto-generate DP ID and Client ID from BO ID
            frappe.model.set_value(cdt, cdn, 'dp_id', row.bo_id.substr(0, 8));
            frappe.model.set_value(cdt, cdn, 'client_id', row.bo_id.substr(8, 8));
        }
    },

    // Validate DP ID (8 characters, NSDL or CDSL)
    dp_id: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        if (row.dp_id) {
            if (/^IN\d{6}$/.test(row.dp_id)) {
                // NSDL format
                frappe.model.set_value(cdt, cdn, 'depository', 'NSDL');
            } else if (/^\d{8}$/.test(row.dp_id)) {
                // CDSL format
                frappe.model.set_value(cdt, cdn, 'depository', 'CDSL');
            } else {
                frappe.msgprint(__('Invalid DP ID. NSDL: IN + 6 digits, CDSL: 8 digits.'));
                frappe.model.set_value(cdt, cdn, 'dp_id', '');
                return;
            }
        }

        // Auto-generate BO ID if both DP ID and Client ID are present
        if (row.dp_id && row.client_id) {
            frappe.model.set_value(cdt, cdn, 'bo_id', row.dp_id + row.client_id);
        }
    },

    // Validate Client ID (8 digits)
    client_id: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        if (row.client_id && !/^\d{8}$/.test(row.client_id)) {
            frappe.msgprint(__('Invalid Client ID. Must be exactly 8 digits.'));
            frappe.model.set_value(cdt, cdn, 'client_id', '');
            return;
        }

        // Auto-generate BO ID if both DP ID and Client ID are present
        if (row.dp_id && row.client_id) {
            frappe.model.set_value(cdt, cdn, 'bo_id', row.dp_id + row.client_id);
        }
    }
});