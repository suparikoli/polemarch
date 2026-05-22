// Security — form-script enhancements.
//
// Phase 21 + 23: renders a "Polemarch's Holdings" panel under the form
// header showing a per-classification breakdown:
//
//   Classification    Units     Cost (₹)    Fair Value (₹)    LCM (₹)
//   ────────────────  ────────  ──────────  ────────────────  ──────────
//   Stock in Trade    74,000    ₹444,000    ₹518,000          ₹444,000
//   Investment        26,000    ₹156,000    ₹182,000          ₹156,000
//   Unallocated        0
//   ────────────────  ────────  ──────────  ────────────────  ──────────
//   Total            100,000    ₹600,000    ₹700,000          ₹600,000
//
// LCM = Lower of Cost or Market (per-classification min(cost, fair)).
// When `last_traded_price` is blank, fair = "—" and LCM falls back to
// cost. Backed by polemarch.api.holdings_summary.get_security_rollup.

frappe.ui.form.on('Security', {
    refresh(frm) {
        if (frm.is_new()) return;
        render_holdings_panel(frm);
    },
});

function render_holdings_panel(frm) {
    // Frappe versions differ on where the dashboard exposes its wrapper:
    //  - v13–15:   frm.dashboard.wrapper (jQuery)
    //  - v16:      frm.dashboard.parent  (jQuery)  — wrapper is undefined
    // Fall back to the form's main layout section so the panel always lands
    // in a visible spot, even if both dashboard accessors change.
    function get_mount() {
        const d = frm.dashboard || {};
        if (d.wrapper && d.wrapper.length) return d.wrapper;
        if (d.parent  && d.parent.length)  return d.parent;
        // Last-resort: prepend to the form layout's main section.
        const $layout = $(frm.wrapper).find('.layout-main-section, .form-page').first();
        return $layout.length ? $layout : $(frm.wrapper);
    }

    const mount = get_mount();
    mount.find('.polemarch-holdings-panel').remove();

    frappe.call({
        method: 'polemarch.api.holdings_summary.get_security_rollup',
        args: { security: frm.doc.name },
    }).then((r) => {
        if (!r || !r.message) return;
        const data = r.message;
        const html = build_panel_html(data, frm.doc.name);
        mount.prepend(
            `<div class="polemarch-holdings-panel" style="margin: 0 0 12px 0">${html}</div>`,
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

    const fmt_n = (v) => v == null || v === 0 && v !== 0
        ? '—'
        : frappe.format(v, { fieldtype: 'Float', precision: 0 });
    const fmt_c = (v) => v == null ? '—' : frappe.format(v, { fieldtype: 'Currency' });
    const fmt_p = (v) => v == null ? '—' : frappe.format(v, { fieldtype: 'Percent' });

    // Highlight LCM by colour:
    //  green  = fair value ≥ cost (LCM = cost, no write-down)
    //  amber  = fair value < cost (LCM = fair, write-down needed)
    //  grey   = no fair value (LCM defaults to cost — operator should set Latest Price)
    function lcm_cell(cost, fair, lcm) {
        if (fair == null) {
            return `<span style="color:#9ca3af">${fmt_c(lcm)} <small>(no price)</small></span>`;
        }
        if (fair < cost) {
            return `<span style="color:#d97706"><b>${fmt_c(lcm)}</b> <small>↓ ${fmt_c(cost - fair)}</small></span>`;
        }
        return `<span style="color:#16a34a">${fmt_c(lcm)}</span>`;
    }

    function row(label, units, cost, fair, lcm, opts = {}) {
        const weight = opts.bold ? 'font-weight:bold;' : '';
        const border = opts.topBorder ? 'border-top:2px solid #e5e7eb;' : '';
        return `
          <tr style="${border}">
            <td style="padding:5px 10px;${weight}">${label}</td>
            <td style="padding:5px 10px;text-align:right;${weight}">${fmt_n(units)}</td>
            <td style="padding:5px 10px;text-align:right;${weight}">${fmt_c(cost)}</td>
            <td style="padding:5px 10px;text-align:right;${weight}">${fmt_c(fair)}</td>
            <td style="padding:5px 10px;text-align:right;${weight}">${lcm_cell(cost, fair, lcm)}</td>
          </tr>
        `;
    }

    const rows = [];
    if (data.sit_units > 0) {
        rows.push(row('Stock in Trade', data.sit_units, data.sit_cost, data.sit_market, data.sit_lcm));
    }
    if (data.inv_units > 0) {
        rows.push(row('Investment', data.inv_units, data.inv_cost, data.inv_market, data.inv_lcm));
    }
    if (data.unalloc_units > 0) {
        rows.push(row('Unallocated', data.unalloc_units, data.unalloc_cost, data.unalloc_market, data.unalloc_lcm));
    }
    rows.push(row('TOTAL', data.total_units, data.total_cost, data.total_market, data.total_lcm,
                  { bold: true, topBorder: true }));

    const subtitle = data.last_traded_price
        ? `Latest Price <b>${fmt_c(data.last_traded_price)}</b>`
        : `Latest Price not set — fair value unknown, LCM defaults to cost. <a href="#" onclick="cur_frm.scroll_to_field('last_traded_price'); return false;">Set price ↓</a>`;

    return `
      <div class="alert alert-info" style="margin-bottom:0">
        <div style="margin-bottom:8px">
          <b style="font-size:1.05em">Polemarch's Holdings — ${frappe.utils.escape_html(data.security_name || security)}</b>
          <a href="/app/investment-holding/view/list?security=${encodeURIComponent(security)}&status=%5B%22in%22%2C%5B%22Open%22%2C%22Partially%20Disposed%22%5D%5D"
             style="float:right; font-size:0.9em">
            view ${data.lots} lot${data.lots === 1 ? '' : 's'} →
          </a>
          <div style="font-size:0.9em; color:#6b7280; margin-top:2px">${subtitle}</div>
        </div>
        <table style="width:100%; border-collapse:collapse; background:white; border-radius:4px;">
          <thead>
            <tr style="background:#f3f4f6; border-bottom:1px solid #e5e7eb">
              <th style="padding:6px 10px; text-align:left">Classification</th>
              <th style="padding:6px 10px; text-align:right">Units</th>
              <th style="padding:6px 10px; text-align:right">Cost (Purchase Value)</th>
              <th style="padding:6px 10px; text-align:right">Fair Value</th>
              <th style="padding:6px 10px; text-align:right">LCM <small>(lower of cost / fair)</small></th>
            </tr>
          </thead>
          <tbody>${rows.join('')}</tbody>
        </table>
        <div style="font-size:0.85em; color:#6b7280; margin-top:6px">
          LCM = Lower of Cost or Market. Stock in Trade is conservatively valued at min(cost, fair value).
          For Investment, the same rule shows where a write-down would be needed if the market drops below cost.
        </div>
      </div>
    `;
}
