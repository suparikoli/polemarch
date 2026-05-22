// Security — form-script enhancements.
//
// Phase 21: renders a "Polemarch Holdings" rollup panel right under the form
// header. Pulls the current holdings (SiT + Investment + cost + market value
// + unrealised gain) for THIS security from
// polemarch.api.holdings_summary.get_security_rollup. Read-only — purely an
// at-a-glance panel so operators don't have to leave the form to check what
// the company currently owns of this share.

frappe.ui.form.on('Security', {
    refresh(frm) {
        if (frm.is_new()) return;
        render_holdings_panel(frm);
    },
});

function render_holdings_panel(frm) {
    const wrapper = frm.dashboard.wrapper.find('.polemarch-holdings-panel');
    if (wrapper.length) wrapper.remove();

    frappe.call({
        method: 'polemarch.api.holdings_summary.get_security_rollup',
        args: { security: frm.doc.name },
    }).then((r) => {
        if (!r || !r.message) return;
        const data = r.message;
        const html = build_panel_html(data, frm.doc.name);
        frm.dashboard.wrapper.prepend(
            `<div class="polemarch-holdings-panel" style="margin-bottom: 12px">${html}</div>`,
        );
    });
}

function build_panel_html(data, security) {
    if (!data.has_holdings) {
        return `
            <div class="alert alert-secondary" style="margin-bottom:0">
              <b>Polemarch Holdings:</b> none currently held.
            </div>
        `;
    }

    const fmt_n  = (v) => v == null ? '—' : frappe.format(v, { fieldtype: 'Float',    precision: 0 });
    const fmt_c  = (v) => v == null ? '—' : frappe.format(v, { fieldtype: 'Currency' });
    const fmt_p  = (v) => v == null ? '—' : frappe.format(v, { fieldtype: 'Percent' });
    const gain   = data.unrealised_gain;
    const gain_c = gain == null ? '' : (gain >= 0 ? 'green' : 'red');

    return `
      <div class="alert alert-info" style="margin-bottom:0">
        <table style="width:100%; border:0">
          <tr style="font-weight:bold; border-bottom:1px solid #ddd">
            <td colspan="2" style="padding:4px 8px">
              Polemarch Holdings — ${frappe.utils.escape_html(data.security_name || security)}
              <a href="/app/investment-holding/view/list?security=${encodeURIComponent(security)}&status=%5B%22in%22%2C%5B%22Open%22%2C%22Partially%20Disposed%22%5D%5D"
                 style="float:right; font-weight:normal; font-size:0.9em">
                view lots →
              </a>
            </td>
          </tr>
          <tr>
            <td style="padding:4px 8px; width:50%">
              <b>Stock in Trade:</b> ${fmt_n(data.sit_units)} units · ${fmt_c(data.sit_cost)} cost
            </td>
            <td style="padding:4px 8px; width:50%">
              <b>Investment:</b>     ${fmt_n(data.inv_units)} units · ${fmt_c(data.inv_cost)} cost
            </td>
          </tr>
          <tr style="border-top:1px solid #ddd">
            <td style="padding:4px 8px">
              <b>Total Units:</b> ${fmt_n(data.total_units)}
              &nbsp; · &nbsp;
              <b>Total Cost:</b> ${fmt_c(data.total_cost)}
              &nbsp; · &nbsp;
              <b>Lots:</b> ${data.lots}
            </td>
            <td style="padding:4px 8px">
              <b>Latest Price:</b> ${fmt_c(data.last_traded_price)}
              &nbsp; · &nbsp;
              <b>Market Value:</b> ${fmt_c(data.market_value)}
              &nbsp; · &nbsp;
              <b>Unrealised Gain:</b>
              <span style="color:${gain_c}">${fmt_c(gain)} (${fmt_p(data.unrealised_gain_pct)})</span>
            </td>
          </tr>
        </table>
      </div>
    `;
}
