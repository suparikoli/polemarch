// Wallet — client-side enhancements.
//
// Renders a "Passbook" panel at the top of every Wallet form showing the
// last 100 Wallet Transactions in classic bank-statement format:
//
//   Date/Time              Txn Type              Reference        DR       CR        Available After
//   ─────────────────────  ────────────────────  ───────────────  ───────  ────────  ────────────────
//   22 May 2026 13:01      Credit · Deposit      WD-2026-0001     —        ₹25,000   ₹25,000.00
//   22 May 2026 14:32      Debit · Reservation   SP-2026-0044     ₹5,000   —         ₹20,000.00
//   22 May 2026 14:45      Credit · Reservation Release SP-2026-0044  —    ₹5,000    ₹25,000.00
//   ...
//
// Cancelled (reversed) rows render strikethrough + muted. Click a row's
// reference to drill into the source doc (SP/SS/Wallet Deposit/etc).
//
// Same approach as the IH form's classifications panel — pure JS, no
// server-side endpoint needed; uses frappe.db.get_list on the Wallet
// Transaction child.

frappe.ui.form.on('Wallet', {
    refresh(frm) {
        if (frm.is_new()) return;
        render_passbook(frm);
    },
});

function render_passbook(frm) {
    const d = frm.dashboard || {};
    const mount = (d.wrapper && d.wrapper.length) ? d.wrapper
        : (d.parent && d.parent.length) ? d.parent
        : $(frm.wrapper).find('.layout-main-section, .form-page').first();
    mount.find('.polemarch-wallet-passbook').remove();

    frappe.db.get_list('Wallet Transaction', {
        filters: { wallet: frm.doc.name, docstatus: 1 },
        fields: [
            'name', 'posting_datetime', 'txn_type', 'direction', 'amount',
            'balance_available_after', 'balance_reserved_after', 'balance_total_after',
            'reference_doctype', 'reference_name',
            'is_cancelled', 'reversed_by', 'reverses', 'remarks',
        ],
        order_by: 'posting_datetime desc, creation desc',
        limit: 100,
    }).then((rows) => {
        const html = build_passbook_html(frm, rows || []);
        mount.prepend(html);
    });
}

function build_passbook_html(frm, rows) {
    const fmt = (v) => frappe.format(v || 0, { fieldtype: 'Currency', options: frm.doc.currency || 'INR' });
    const dt = (s) => s ? moment(s).format('D MMM YYYY, HH:mm') : '';

    // Color cues: Credit = green-ish on amount, Debit = red-ish.
    // Cancelled rows muted + strikethrough.

    const header_html = `
      <tr style="background: var(--bg-color); color: var(--text-color);">
        <th style="padding: 8px 10px; text-align: left;  font-weight: 600;">Date / Time</th>
        <th style="padding: 8px 10px; text-align: left;  font-weight: 600;">Transaction</th>
        <th style="padding: 8px 10px; text-align: left;  font-weight: 600;">Reference</th>
        <th style="padding: 8px 10px; text-align: right; font-weight: 600; color: #ef4444;">DR</th>
        <th style="padding: 8px 10px; text-align: right; font-weight: 600; color: #22c55e;">CR</th>
        <th style="padding: 8px 10px; text-align: right; font-weight: 600;">Available After</th>
        <th style="padding: 8px 10px; text-align: right; font-weight: 600; color: var(--text-muted);">Total After</th>
      </tr>
    `;

    const body_html = rows.length === 0
        ? `<tr><td colspan="7" style="padding: 16px; text-align: center; color: var(--text-muted);">
              No Wallet Transactions yet. Use Wallet Deposit or Wallet Withdrawal to fund this wallet.
           </td></tr>`
        : rows.map((r) => {
              const cancelled = r.is_cancelled || r.reversed_by;
              const cell_style = cancelled
                  ? 'padding: 6px 10px; vertical-align: middle; text-decoration: line-through; opacity: 0.55;'
                  : 'padding: 6px 10px; vertical-align: middle;';
              const is_credit = r.direction === 'Credit';
              const ref_html = (r.reference_doctype && r.reference_name)
                  ? `<a href="/app/${frappe.router.slug(r.reference_doctype)}/${encodeURIComponent(r.reference_name)}" style="font-size: 12px;">${frappe.utils.escape_html(r.reference_name)}</a>`
                  : `<span style="color: var(--text-muted); font-size: 12px;">—</span>`;
              const txn_label = `<span style="font-weight: 500;">${frappe.utils.escape_html(r.txn_type || '')}</span>`;
              const dir_label = `<span style="font-size: 11px; color: ${is_credit ? '#22c55e' : '#ef4444'}; margin-left: 6px;">${is_credit ? '↑ CR' : '↓ DR'}</span>`;
              const cancelled_note = cancelled
                  ? `<div style="font-size: 11px; color: #ef4444; margin-top: 2px;">↺ reversed${r.reversed_by ? ` by <a href="/app/wallet-transaction/${encodeURIComponent(r.reversed_by)}">${r.reversed_by}</a>` : ''}</div>`
                  : '';
              return `
                <tr style="border-top: 1px solid var(--border-color);">
                  <td style="${cell_style} font-size: 12px; color: var(--text-muted);">${dt(r.posting_datetime)}</td>
                  <td style="${cell_style}">
                      ${txn_label}${dir_label}
                      ${cancelled_note}
                  </td>
                  <td style="${cell_style}">${ref_html}</td>
                  <td style="${cell_style} text-align: right; color: ${is_credit ? 'var(--text-muted)' : '#ef4444'}; font-variant-numeric: tabular-nums;">
                      ${is_credit ? '—' : fmt(r.amount)}
                  </td>
                  <td style="${cell_style} text-align: right; color: ${is_credit ? '#22c55e' : 'var(--text-muted)'}; font-variant-numeric: tabular-nums;">
                      ${is_credit ? fmt(r.amount) : '—'}
                  </td>
                  <td style="${cell_style} text-align: right; font-weight: 600; font-variant-numeric: tabular-nums;">
                      ${fmt(r.balance_available_after)}
                  </td>
                  <td style="${cell_style} text-align: right; color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums;">
                      ${fmt(r.balance_total_after)}
                  </td>
                </tr>
              `;
          }).join('');

    const view_all_link = `/app/wallet-transaction/view/list?wallet=${encodeURIComponent(frm.doc.name)}`;

    return `
      <div class="polemarch-wallet-passbook" style="
          margin: 0 0 16px 0;
          background: var(--card-bg);
          border: 1px solid var(--border-color);
          border-radius: 6px;
          overflow: hidden;">

        <div style="padding: 10px 14px; background: var(--bg-color); border-bottom: 1px solid var(--border-color);
                    display: flex; justify-content: space-between; align-items: center;">
          <div>
            <b style="font-size: 14px;">Passbook</b>
            <span style="color: var(--text-muted); font-size: 12px; margin-left: 8px;">
              ${rows.length === 100 ? 'showing last 100' : `${rows.length} transaction${rows.length === 1 ? '' : 's'}`}
            </span>
          </div>
          <div style="font-size: 12px;">
            <a href="${view_all_link}">View all →</a> &nbsp;·&nbsp;
            <a href="/app/wallet-deposit/new?customer=${encodeURIComponent(frm.doc.customer || '')}">Deposit</a> &nbsp;·&nbsp;
            <a href="/app/wallet-withdrawal/new?customer=${encodeURIComponent(frm.doc.customer || '')}">Withdrawal</a>
          </div>
        </div>

        <div style="overflow-x: auto;">
          <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
            <thead>${header_html}</thead>
            <tbody>${body_html}</tbody>
          </table>
        </div>

        <div style="padding: 8px 14px; background: var(--bg-color); border-top: 1px solid var(--border-color);
                    font-size: 11px; color: var(--text-muted);">
          Passbook is append-only — reversed rows render strikethrough but stay visible
          for audit. Available After = balance available for new purchases (excludes reserved).
          Total After = grand total including reserved.
        </div>
      </div>
    `;
}
