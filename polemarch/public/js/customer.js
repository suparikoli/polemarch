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
    // Mithtech-only customers opt out of the entire Polemarch path —
    // no KYC indicator, no KYC action buttons, no dashboard render.
    if (frm.doc.custom_is_mithtech_only) return;
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
    const fmt_n = (n) => frappe.format(n || 0, { fieldtype: "Float", precision: 0 });

    // ── Top stat cards — Wallet · Holdings · Trades · KYC ──
    const wallet = d.wallet;
    const positions = d.positions;

    const wallet_card = wallet
        ? `<a href="/app/wallet/${encodeURIComponent(wallet.wallet)}" style="text-decoration:none;color:inherit">
              <div style="border:1px solid var(--border-color);border-left:4px solid #4F46E5;border-radius:8px;padding:12px">
                  <div class="text-muted small">${__("Wallet Available")}</div>
                  <div style="font-size:22px;font-weight:600">${fmt(wallet.balance_available)}</div>
                  <div class="text-muted" style="font-size:11px;margin-top:4px">
                      ${__("Total: {0}", [fmt(wallet.balance_total)])} ·
                      ${__("Reserved: {0}", [fmt(wallet.balance_reserved)])}
                  </div>
              </div>
           </a>`
        : `<div style="border:1px solid var(--border-color);border-left:4px solid #9CA3AF;border-radius:8px;padding:12px;opacity:0.6">
              <div class="text-muted small">${__("Wallet")}</div>
              <div style="font-size:14px;color:var(--text-muted);margin-top:6px">${__("Not provisioned")}</div>
           </div>`;

    const positions_card = positions && positions.count > 0
        ? `<a href="/app/customer-holding/view/list?customer=${encodeURIComponent(customer)}" style="text-decoration:none;color:inherit">
              <div style="border:1px solid var(--border-color);border-left:4px solid #22C55E;border-radius:8px;padding:12px">
                  <div class="text-muted small">${__("Holdings")}</div>
                  <div style="font-size:22px;font-weight:600">${positions.count}</div>
                  <div class="text-muted" style="font-size:11px;margin-top:4px">
                      ${__("{0} units total", [fmt_n(positions.total_qty)])}
                  </div>
              </div>
           </a>`
        : `<div style="border:1px solid var(--border-color);border-left:4px solid #9CA3AF;border-radius:8px;padding:12px;opacity:0.6">
              <div class="text-muted small">${__("Holdings")}</div>
              <div style="font-size:14px;color:var(--text-muted);margin-top:6px">${__("None yet")}</div>
           </div>`;

    const link_invoices = `/app/sales-invoice/view/list?customer=${encodeURIComponent(customer)}&custom_is_polemarch_invoice=1`;
    const trades_card = `<a href="${link_invoices}" style="text-decoration:none;color:inherit">
        <div style="border:1px solid var(--border-color);border-left:4px solid #0EA5E9;border-radius:8px;padding:12px">
            <div class="text-muted small">${__("Trades (Submitted)")}</div>
            <div style="font-size:22px;font-weight:600">${d.total_orders}</div>
            <div class="text-muted" style="font-size:11px;margin-top:4px">
                ${__("Invested: {0}", [fmt(d.total_invested)])}
            </div>
        </div>
    </a>`;

    const completeness = d.kyc ? d.kyc.completeness : 0;
    const completeness_color = completeness === 100 ? "#22C55E" : completeness >= 60 ? "#F59E0B" : "#EF4444";
    const kyc_card = `<div style="border:1px solid var(--border-color);border-left:4px solid ${completeness_color};border-radius:8px;padding:12px">
        <div class="text-muted small">${__("KYC Completeness")}</div>
        <div style="font-size:22px;font-weight:600;color:${completeness_color}">${completeness}%</div>
        <div class="text-muted" style="font-size:11px;margin-top:4px">
            ${(d.kyc && d.kyc.items || []).filter(i => i.ok).length} / ${(d.kyc && d.kyc.items || []).length} ${__("checks passed")}
        </div>
    </div>`;

    // ── Wallet detail panel ──
    const wallet_panel = wallet
        ? `<div style="border:1px solid var(--border-color);border-radius:8px;padding:14px;background:var(--card-bg)">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                  <h5 style="margin:0">${__("Wallet")} — <a href="/app/wallet/${encodeURIComponent(wallet.wallet)}" style="font-size:0.9em">${wallet.wallet}</a></h5>
                  <span class="indicator ${wallet.status === 'Active' ? 'green' : 'grey'}">${frappe.utils.escape_html(wallet.status || '')}</span>
              </div>
              <table style="width:100%;margin-top:8px">
                  <tr><td class="text-muted">${__("Available")}</td><td class="text-right" style="font-weight:600;color:#22C55E">${fmt(wallet.balance_available)}</td></tr>
                  <tr><td class="text-muted">${__("Reserved")}</td><td class="text-right" style="color:#F59E0B">${fmt(wallet.balance_reserved)}</td></tr>
                  <tr style="border-top:1px solid var(--border-color)"><td class="text-muted" style="padding-top:6px"><b>${__("Total")}</b></td><td class="text-right" style="font-weight:600;padding-top:6px">${fmt(wallet.balance_total)}</td></tr>
              </table>
              ${wallet.last_transaction ? `
                  <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border-color);font-size:12px;color:var(--text-muted)">
                      ${__("Last: ")} <b>${wallet.last_transaction.direction} ${wallet.last_transaction.txn_type}</b>
                      ${fmt(wallet.last_transaction.amount)} ·
                      ${frappe.utils.escape_html(wallet.last_transaction.posting_datetime || '')}
                  </div>
              ` : ''}
              <div style="margin-top:10px;font-size:12px">
                  <a href="/app/wallet-transaction/view/list?wallet=${encodeURIComponent(wallet.wallet)}">${__("View transactions →")}</a> ·
                  <a href="/app/wallet-deposit/new?customer=${encodeURIComponent(customer)}">${__("Deposit")}</a> ·
                  <a href="/app/wallet-withdrawal/new?customer=${encodeURIComponent(customer)}">${__("Withdrawal")}</a>
              </div>
           </div>`
        : `<div style="border:1px solid var(--border-color);border-radius:8px;padding:14px;background:var(--card-bg)">
              <h5 style="margin:0">${__("Wallet")}</h5>
              <div class="text-muted" style="margin-top:8px">${__("No wallet provisioned for this customer.")}</div>
              <div style="margin-top:8px;font-size:12px">
                  <a href="/app/wallet/new?customer=${encodeURIComponent(customer)}">${__("Create Wallet →")}</a>
              </div>
           </div>`;

    // ── Holdings table ──
    const holding_rows = positions && positions.rows && positions.rows.length
        ? positions.rows.map((p) => `
              <tr>
                  <td><a href="/app/security/${encodeURIComponent(p.security)}">${frappe.utils.escape_html(p.security)}</a></td>
                  <td>${frappe.utils.escape_html(p.security_name || '')}</td>
                  <td class="text-right">${fmt_n(p.qty_held)}</td>
                  <td class="text-muted" style="font-size:11px">${frappe.utils.escape_html(p.source || '')}</td>
              </tr>
          `).join("")
        : `<tr><td colspan="4" class="text-muted text-center" style="padding:12px">${__("No holdings yet.")}</td></tr>`;

    const holdings_panel = `<div style="border:1px solid var(--border-color);border-radius:8px;padding:14px;background:var(--card-bg)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <h5 style="margin:0">${__("Holdings")} ${positions && positions.count ? `<span class="text-muted" style="font-weight:normal">(${positions.count})</span>` : ''}</h5>
            <a href="/app/customer-holding/view/list?customer=${encodeURIComponent(customer)}" style="font-size:12px">${__("View all →")}</a>
        </div>
        <table class="table" style="margin:0;font-size:13px">
            <thead><tr>
                <th style="font-weight:600">${__("Security")}</th>
                <th style="font-weight:600">${__("Name")}</th>
                <th class="text-right" style="font-weight:600">${__("Qty Held")}</th>
                <th style="font-weight:600">${__("Source")}</th>
            </tr></thead>
            <tbody>${holding_rows}</tbody>
        </table>
        <div class="text-muted" style="font-size:11px;margin-top:6px">
            ${__("Customer Holdings are a CRM snapshot of what each customer holds in their own demat — NOT on Polemarch's books.")}
        </div>
    </div>`;

    // ── Recent Trades + KYC Checklist ──
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

    return `
        <!-- Top stat cards -->
        <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px">
            ${wallet_card}
            ${positions_card}
            ${trades_card}
            ${kyc_card}
        </div>

        <!-- Wallet + Holdings detail row -->
        <div style="display:grid;grid-template-columns:1fr 2fr;gap:16px;margin-bottom:16px">
            ${wallet_panel}
            ${holdings_panel}
        </div>

        <!-- Recent Trades + KYC Checklist row -->
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
