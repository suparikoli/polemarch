frappe.ui.form.on('Customer', {
    refresh(frm) {
        render_primary_dp(frm);
        render_primary_bank(frm);
    },
    after_save(frm) {
        render_primary_dp(frm);
        render_primary_bank(frm);
    }
});

// --- Helper: Render Primary DP ---
function render_primary_dp(frm) {
    const dp_details = frm.doc.custom_dp_details || [];
    // Frappe Check fields are stored as "1" or 1
    const primary_dp = dp_details.find(d => d.is_primary == 1 || d.is_primary === "1");

    const html_field = frm.fields_dict.custom_primary_dp?.$wrapper;
    if (!html_field) return;

    if (primary_dp) {
        const html = `
            <div style="border:1px solid #ccc; border-radius:8px; padding:10px; margin-bottom:10px;">
                <p><b>Depository:</b> ${primary_dp.depository || ''}</p>
                <p><b>BO ID:</b> ${primary_dp.bo_id || ''}</p>
            </div>
        `;
        html_field.html(html);
    } else {
        html_field.html('<div style="color:gray;">No primary DP selected.</div>');
    }
}

// --- Helper: Render Primary Bank ---
function render_primary_bank(frm) {
    const bank_details = frm.doc.custom_bank_details || [];
    const primary_bank = bank_details.find(b => b.is_primary == 1 || b.is_primary === "1");

    const html_field = frm.fields_dict.custom_primary_bank_?.$wrapper; // note underscore
    if (!html_field) return;

    if (primary_bank) {
        const html = `
            <div style="border:1px solid #ccc; border-radius:8px; padding:10px;">
                <p><b>Bank Name:</b> ${primary_bank.bank_name || ''}</p>
                <p><b>IFSC/SWIFT/BIC:</b> ${primary_bank.bank_code || ''}</p>
                <p><b>Account Number:</b> ${primary_bank.ac_number || ''}</p>
            </div>
        `;
        html_field.html(html);
    } else {
        html_field.html('<div style="color:gray;">No primary bank selected.</div>');
    }
}
