// Security — form-script enhancements.
//
// Phase 21 + 23 + 24: renders a "Polemarch's Holdings" panel above the form
// showing a per-classification breakdown with weighted-average unit prices:
//
//   Classification    Units      Cost (₹/u · total)    Fair Value          LCM (₹/u · total)
//   ────────────────  ────────   ────────────────────  ──────────────────  ────────────────────
//   Stock in Trade    74,000     ₹6.00/u · ₹4,44,000   ₹7.00/u · ₹5,18,000  ₹6.00/u · ₹4,44,000
//   Investment        26,000     ₹6.00/u · ₹1,56,000   ₹7.00/u · ₹1,82,000  ₹6.00/u · ₹1,56,000
//   ────────────────  ────────   ────────────────────  ──────────────────  ────────────────────
//   Total            100,000     ₹6.00/u · ₹6,00,000   ₹7.00/u · ₹7,00,000  ₹6.00/u · ₹6,00,000
//
// LCM = Lower of Cost or Market (per-classification min(cost, fair)).
// Per-unit values are weighted averages within the classification.
// All colours use Frappe CSS variables so the panel auto-adapts to dark mode.

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
    function get_mount() {
        const d = frm.dashboard || {};
        if (d.wrapper && d.wrapper.length) return d.wrapper;
        if (d.parent  && d.parent.length)  return d.parent;
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
        mount.prepend(html);
    });
}

function build_panel_html(data, security) {
    if (!data.has_holdings) {
        return `
          <div class="polemarch-holdings-panel" style="
              margin: 0 0 16px 0; padding: 12px 16px;
              background: var(--card-bg); color: var(--text-color);
              border: 1px solid var(--border-color); border-radius: 6px;
              font-size: 13px;">
            <b>Polemarch's Holdings:</b> <span style="color: var(--text-muted)">none currently held.</span>
          </div>
        `;
    }

    const fmt_n = (v) => (v == null) ? '—' : frappe.format(v, { fieldtype: 'Float',    precision: 0 });
    const fmt_c = (v) => (v == null) ? '—' : frappe.format(v, { fieldtype: 'Currency' });

    // Weighted-average unit price for a classification:
    //   avg_cost  = cost_value / units    (Σ qty×basis / Σ qty)
    //   avg_lcm   = lcm_value  / units
    //   fair_unit = last_traded_price     (same for all classifications)
    function unit(value, units) {
        if (value == null || !units) return null;
        return value / units;
    }

    // 2-line cell: per-unit on top (large, bold), total below (muted, smaller).
    // Each line is a div with nowrap so the table cell can't break inside a line.
    function dual(perUnit, total) {
        const top = (perUnit == null)
            ? '<div>—</div>'
            : `<div style="white-space: nowrap;">${fmt_c(perUnit)}<span style="color: var(--text-muted); font-size: 0.85em;">&nbsp;/ unit</span></div>`;
        const bot = (total == null)
            ? ''
            : `<div style="white-space: nowrap; color: var(--text-muted); font-size: 0.85em; margin-top: 2px;">${fmt_c(total)}</div>`;
        return `${top}${bot}`;
    }

    // LCM cell with colour semantics: green = LCM=cost (healthy);
    // amber = LCM=fair (write-down); muted = no price (LCM defaults to cost).
    function lcm_dual(cost, fair, lcm, units) {
        if (lcm == null) return '<div>—</div>';
        const perUnit = unit(lcm, units);
        if (fair == null) {
            // No price — LCM = cost by fallback. Show muted with footnote.
            return `<div style="color: var(--text-muted);">
                <div style="white-space: nowrap;">${fmt_c(perUnit)}<span style="font-size: 0.85em;">&nbsp;/ unit</span></div>
                <div style="white-space: nowrap; font-size: 0.85em; margin-top: 2px;">${fmt_c(lcm)}</div>
                <div style="font-size: 0.8em; margin-top: 1px; font-style: italic;">no price</div>
            </div>`;
        }
        if (fair < cost) {
            // Write-down: LCM = fair, less than cost. Amber with delta annotation.
            const writedown = cost - lcm;
            return `<div style="color: var(--orange-500, #d97706);">
                <div style="white-space: nowrap;">${fmt_c(perUnit)}<span style="font-size: 0.85em;">&nbsp;/ unit</span></div>
                <div style="white-space: nowrap; font-size: 0.85em; margin-top: 2px;">${fmt_c(lcm)}</div>
                <div style="white-space: nowrap; font-size: 0.8em; margin-top: 1px;">↓ ${fmt_c(writedown)} write-down</div>
            </div>`;
        }
        // Healthy: LCM = cost, fair ≥ cost. Green.
        return `<div style="color: var(--green-500, #16a34a);">
            <div style="white-space: nowrap;">${fmt_c(perUnit)}<span style="font-size: 0.85em;">&nbsp;/ unit</span></div>
            <div style="white-space: nowrap; font-size: 0.85em; margin-top: 2px;">${fmt_c(lcm)}</div>
        </div>`;
    }

    function row(label, units, cost, fair, lcm, opts = {}) {
        const weight = opts.bold ? 'font-weight: 600;' : '';
        const border = opts.topBorder ? 'border-top: 2px solid var(--border-color);' : 'border-top: 1px solid var(--border-color);';
        const td = `padding: 8px 12px; vertical-align: top; ${weight} ${border}`;
        const td_r = `${td} text-align: right;`;
        return `
          <tr>
            <td style="${td}">${label}</td>
            <td style="${td_r}">${fmt_n(units)}</td>
            <td style="${td_r}">${dual(unit(cost, units), cost)}</td>
            <td style="${td_r}">${fair == null
                ? '<span style="color: var(--text-muted)">—</span>'
                : dual(unit(fair, units), fair)}</td>
            <td style="${td_r}">${lcm_dual(cost, fair, lcm, units)}</td>
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

    const ltp = data.last_traded_price;
    const subtitle = ltp
        ? `Latest Price <b>${fmt_c(ltp)}</b> · per-unit values are weighted averages within each classification.`
        : `Latest Price not set — fair value unknown, LCM defaults to cost. <a href="#" onclick="cur_frm.scroll_to_field('last_traded_price'); return false;">Set price ↓</a>`;

    return `
      <div class="polemarch-holdings-panel" style="
          margin: 0 0 16px 0;
          background: var(--card-bg);
          color: var(--text-color);
          border: 1px solid var(--border-color);
          border-radius: 6px;
          overflow: hidden;
          font-size: 13px;">

        <div style="padding: 10px 14px; background: var(--bg-color); border-bottom: 1px solid var(--border-color);">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <b style="font-size: 14px;">Polemarch's Holdings — ${frappe.utils.escape_html(data.security_name || security)}</b>
            <a href="/app/investment-holding/view/list?security=${encodeURIComponent(security)}&status=%5B%22in%22%2C%5B%22Open%22%2C%22Partially%20Disposed%22%5D%5D"
               style="font-size: 12px;">
              view ${data.lots} lot${data.lots === 1 ? '' : 's'} →
            </a>
          </div>
          <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">${subtitle}</div>
        </div>

        <table style="width: 100%; border-collapse: collapse; margin: 0;">
          <thead>
            <tr style="background: var(--bg-color); color: var(--text-color);">
              <th style="padding: 8px 12px; text-align: left;  font-weight: 600; white-space: nowrap;">Classification</th>
              <th style="padding: 8px 12px; text-align: right; font-weight: 600; white-space: nowrap;">Units</th>
              <th style="padding: 8px 12px; text-align: right; font-weight: 600; white-space: nowrap;">
                Cost<br>
                <span style="color: var(--text-muted); font-weight: normal; font-size: 0.85em;">per unit · total</span>
              </th>
              <th style="padding: 8px 12px; text-align: right; font-weight: 600; white-space: nowrap;">
                Fair Value<br>
                <span style="color: var(--text-muted); font-weight: normal; font-size: 0.85em;">per unit · total</span>
              </th>
              <th style="padding: 8px 12px; text-align: right; font-weight: 600; white-space: nowrap;">
                LCM<br>
                <span style="color: var(--text-muted); font-weight: normal; font-size: 0.85em;">lower of cost / fair</span>
              </th>
            </tr>
          </thead>
          <tbody>${rows.join('')}</tbody>
        </table>

        <div style="padding: 8px 14px; background: var(--bg-color); border-top: 1px solid var(--border-color); font-size: 11px; color: var(--text-muted);">
          LCM = Lower of Cost or Market. Per-unit cost = Σ(qty × cost_basis) ÷ Σ qty (weighted average across all lots in that classification). Stock in Trade is valued at min(cost, fair); for Investment, the same rule flags potential write-downs.
        </div>
      </div>
    `;
}
