frappe.ui.form.on('Customer', {
    refresh(frm) {
        update_primary_details(frm);
        render_polemarch_indicators(frm);
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

// --- Helper: Polemarch indicators + actions ---
function render_polemarch_indicators(frm) {
    if (frm.is_new()) return;
    if (frm.doc.custom_is_polemarch_customer) {
        frm.dashboard.add_indicator(__("Polemarch"), "green");
        const kyc = frm.doc.custom_kyc_status || "Not Started";
        const kyc_color = { "Verified": "green", "Rejected": "red", "In Review": "orange", "Not Started": "grey" }[kyc] || "grey";
        frm.dashboard.add_indicator(__("KYC: {0}", [kyc]), kyc_color);

        if (kyc !== "Verified") {
            frm.add_custom_button(__("Verify KYC"), () => {
                frappe.confirm(
                    __("Mark KYC verified for {0}?", [frm.doc.customer_name]),
                    () => _kyc_call(frm, "polemarch.api.kyc.verify_kyc")
                );
            }, __("KYC"));
        }
        if (kyc !== "Rejected") {
            frm.add_custom_button(__("Reject KYC"), () => {
                frappe.prompt(
                    [{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }],
                    ({ reason }) => _kyc_call(frm, "polemarch.api.kyc.reject_kyc", { reason }),
                    __("Reject KYC"),
                    __("Send & Reject")
                );
            }, __("KYC"));
        }
        if (kyc === "Not Started") {
            frm.add_custom_button(__("Mark In Review"), () => {
                _kyc_call(frm, "polemarch.api.kyc.mark_in_review");
            }, __("KYC"));
        }

        render_polemarch_dashboard(frm);
    }
}

function _kyc_call(frm, method, extra) {
    frappe.call({
        method,
        args: Object.assign({ customer: frm.doc.name }, extra || {}),
        freeze: true,
        freeze_message: __("Updating KYC..."),
        callback: ({ message }) => {
            if (message && message.ok) {
                frappe.show_alert({ message: __("KYC: {0}", [message.status]), indicator: "green" });
                frm.reload_doc();
            }
        },
    });
}

function render_polemarch_dashboard(frm) {
    const wrapper = frm.fields_dict.custom_polemarch_dashboard_html
        && frm.fields_dict.custom_polemarch_dashboard_html.$wrapper;
    if (!wrapper) return;

    wrapper.html(`<div class="text-muted" style="padding:20px">${__("Loading…")}</div>`);

    frappe.call({
        method: "polemarch.api.customer_dashboard.get_dashboard",
        args: { customer: frm.doc.name },
        callback: ({ message }) => {
            if (!message) return;
            wrapper.html(_build_polemarch_dashboard_html(message, frm.doc.name));
        },
    });
}

function _build_polemarch_dashboard_html(d, customer) {
    const fmt = (n) => frappe.format(n, { fieldtype: "Currency", options: d.currency || "INR" }, { inline: true }, null);
    const rec = (d.recent_trades || []).map((t) => `
        <tr>
            <td><a href="/app/sales-invoice/${encodeURIComponent(t.name)}">${frappe.utils.escape_html(t.name)}</a></td>
            <td>${frappe.utils.escape_html(t.posting_date || "")}</td>
            <td class="text-right">${fmt(t.grand_total || 0)}</td>
            <td><span class="indicator ${_status_color(t.status)}">${frappe.utils.escape_html(t.status || "")}</span></td>
        </tr>
    `).join("") || `<tr><td colspan="4" class="text-muted text-center" style="padding:12px">${__("No Polemarch trades yet.")}</td></tr>`;

    const kyc = (d.kyc && d.kyc.items || []).map((i) => `
        <li style="padding:4px 0">
            <span style="display:inline-block;width:18px">${i.ok ? "✓" : "✗"}</span>
            <span style="${i.ok ? "" : "color:#b54708"}">${frappe.utils.escape_html(i.label)}</span>
        </li>
    `).join("");
    const completeness = d.kyc ? d.kyc.completeness : 0;
    const completeness_color = completeness === 100 ? "#0a7847" : completeness >= 60 ? "#b54708" : "#b91c1c";

    const link_invoices = `/app/sales-invoice/view/list?customer=${encodeURIComponent(customer)}&custom_is_polemarch_invoice=1`;
    const link_orders = `/app/sales-order/view/list?customer=${encodeURIComponent(customer)}&custom_is_polemarch_order=1`;

    return `
        <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px">
            <a href="${link_invoices}" style="text-decoration:none;color:inherit">
                <div style="border:1px solid var(--border-color);border-radius:8px;padding:12px">
                    <div class="text-muted small">${__("Trades (Submitted)")}</div>
                    <div style="font-size:22px;font-weight:600">${d.total_orders}</div>
                </div>
            </a>
            <div style="border:1px solid var(--border-color);border-radius:8px;padding:12px">
                <div class="text-muted small">${__("Total Invested")}</div>
                <div style="font-size:22px;font-weight:600">${fmt(d.total_invested)}</div>
            </div>
            <a href="${link_orders}" style="text-decoration:none;color:inherit">
                <div style="border:1px solid var(--border-color);border-radius:8px;padding:12px">
                    <div class="text-muted small">${__("Pending Invoices")}</div>
                    <div style="font-size:22px;font-weight:600">${d.pending_orders}</div>
                </div>
            </a>
            <div style="border:1px solid var(--border-color);border-radius:8px;padding:12px">
                <div class="text-muted small">${__("KYC Completeness")}</div>
                <div style="font-size:22px;font-weight:600;color:${completeness_color}">${completeness}%</div>
            </div>
        </div>

        <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px">
            <div>
                <h5>${__("Recent Trades")}</h5>
                <table class="table table-bordered" style="margin-top:8px">
                    <thead><tr>
                        <th>${__("Invoice")}</th>
                        <th>${__("Date")}</th>
                        <th class="text-right">${__("Amount")}</th>
                        <th>${__("Status")}</th>
                    </tr></thead>
                    <tbody>${rec}</tbody>
                </table>
            </div>
            <div>
                <h5>${__("KYC Checklist")}</h5>
                <ul style="list-style:none;padding-left:0;margin-top:8px">${kyc}</ul>
            </div>
        </div>
    `;
}

function _status_color(status) {
    const s = (status || "").toLowerCase();
    if (s === "paid") return "green";
    if (s === "overdue") return "red";
    if (s === "unpaid") return "orange";
    if (s === "cancelled" || s === "canceled") return "grey";
    return "blue";
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
