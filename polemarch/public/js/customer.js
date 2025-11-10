frappe.ui.form.on('Customer', {
    refresh(frm) {
        update_primary_details(frm);
    },
    after_save(frm) {
        update_primary_details(frm);
    },
    custom_dp_details_on_form_rendered(frm) {
        update_primary_details(frm);
    },
    custom_bank_details_on_form_rendered(frm) {
        update_primary_details(frm);
    },
    custom_dp_details_add(frm) {
        update_primary_details(frm);
    },
    custom_bank_details_add(frm) {
        update_primary_details(frm);
    },
    custom_dp_details_remove(frm) {
        update_primary_details(frm);
    },
    custom_bank_details_remove(frm) {
        update_primary_details(frm);
    }
});

function update_primary_details(frm) {
    render_primary_dp(frm);
    render_primary_bank(frm);
}

// --- Helper: Render Primary DP ---
function render_primary_dp(frm) {
    const dp_details = frm.doc.custom_dp_details || [];
    const primary_dp = dp_details.find(d => d.is_primary == 1 || d.is_primary === "1");

    if (primary_dp) {
        const dp_text = `Depository: ${primary_dp.depository || ''}, BO ID: ${primary_dp.bo_id || ''}, DP ID: ${primary_dp.dp_id || ''}, Client ID: ${primary_dp.client_id || ''}`;
        frappe.model.set_value(frm.doctype, frm.docname, 'custom_dp_primary', dp_text);
    } else {
        frappe.model.set_value(frm.doctype, frm.docname, 'custom_dp_primary', 'No primary DP selected.');
    }
}

// --- Helper: Render Primary Bank ---
function render_primary_bank(frm) {
    const bank_details = frm.doc.custom_bank_details || [];
    const primary_bank = bank_details.find(b => b.is_primary == 1 || b.is_primary === "1");

    if (primary_bank) {
        const bank_text = `Bank: ${primary_bank.bank_name || ''}, Branch: ${primary_bank.bank_branch || ''}, Account No: ${primary_bank.ac_number || ''}, IFSC/SWIFT/BIC: ${primary_bank.bank_code || ''}`;
        frappe.model.set_value(frm.doctype, frm.docname, 'custom_bank_primary', bank_text);
    } else {
        frappe.model.set_value(frm.doctype, frm.docname, 'custom_bank_primary', 'No primary bank selected.');
    }
}
