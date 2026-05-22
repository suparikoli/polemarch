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

    // 2-line cell: per-unit on top, total muted below.
    // Column header already says "per unit · total" — no need for a /u suffix.
    function dual(perUnit, total) {
        const top = (perUnit == null)
            ? '<div>—</div>'
            : `<div style="white-space: nowrap; font-weight: 500;">${fmt_c(perUnit)}</div>`;
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
        const baseCell = `
            <div style="white-space: nowrap; font-weight: 500;">${fmt_c(perUnit)}</div>
            <div style="white-space: nowrap; font-size: 0.85em; margin-top: 2px; opacity: 0.85;">${fmt_c(lcm)}</div>
        `;
        if (fair == null) {
            return `<div style="color: var(--text-muted);">
                ${baseCell}
                <div style="font-size: 0.8em; margin-top: 1px; font-style: italic;">no price</div>
            </div>`;
        }
        if (fair < cost) {
            // Amber write-down. Use a brighter orange that survives dark mode.
            const writedown = cost - lcm;
            return `<div style="color: #f59e0b; font-weight: 500;">
                ${baseCell}
                <div style="white-space: nowrap; font-size: 0.8em; margin-top: 1px;">↓ ${fmt_c(writedown)}</div>
            </div>`;
        }
        // Healthy green — pick a vivid shade that reads on both themes.
        return `<div style="color: #22c55e; font-weight: 500;">${baseCell}</div>`;
    }

    function row(label, units, cost, fair, lcm, opts = {}) {
        const weight = opts.bold ? 'font-weight: 600;' : '';
        const border = opts.topBorder ? 'border-top: 2px solid var(--border-color);' : 'border-top: 1px solid var(--border-color);';
        // Unclassified rows with 0 qty render muted (placeholder for the
        // operator's reference) but stay visible so they always see the bucket.
        const muted = opts.unclassified && !units;
        const rowOpacity = muted ? 'opacity: 0.55;' : '';
        const td = `padding: 8px 12px; vertical-align: top; ${weight} ${border} ${rowOpacity}`;
        const td_r = `${td} text-align: right;`;
        // Label gets a subtle hint when Unclassified > 0 (this row is "live").
        const labelHtml = opts.unclassified && units
            ? `${label} <small style="color: var(--text-muted); font-weight: normal;">(awaiting classification)</small>`
            : label;
        // Empty cells render as "—" when the value is 0; otherwise normal dual.
        const costCell = !units
            ? '<span style="color: var(--text-muted)">—</span>'
            : dual(unit(cost, units), cost);
        const fairCell = (fair == null)
            ? '<span style="color: var(--text-muted)">—</span>'
            : (!units ? '<span style="color: var(--text-muted)">—</span>' : dual(unit(fair, units), fair));
        const lcmCell = !units
            ? '<span style="color: var(--text-muted)">—</span>'
            : lcm_dual(cost, fair, lcm, units);
        return `
          <tr>
            <td style="${td}">${labelHtml}</td>
            <td style="${td_r}">${fmt_n(units)}</td>
            <td style="${td_r}">${costCell}</td>
            <td style="${td_r}">${fairCell}</td>
            <td style="${td_r}">${lcmCell}</td>
          </tr>
        `;
    }

    // Always render Stock in Trade, Investment, and Unclassified rows —
    // even with 0 qty — so the operator sees the classification model at
    // a glance. Phase 24: Unclassified is the pool of shares within the
    // 5-day window that haven't been assigned to SiT or Investment yet.
    const rows = [];
    rows.push(row('Stock in Trade', data.sit_units || 0, data.sit_cost || 0, data.sit_market, data.sit_lcm || 0));
    rows.push(row('Investment',     data.inv_units || 0, data.inv_cost || 0, data.inv_market, data.inv_lcm || 0));
    rows.push(row('Unclassified',   data.unalloc_units || 0, data.unalloc_cost || 0, data.unalloc_market, data.unalloc_lcm || 0,
                  { unclassified: true }));
    rows.push(row('TOTAL', data.total_units, data.total_cost, data.total_market, data.total_lcm,
                  { bold: true, topBorder: true }));

    // Plain-string INR formatter for the subtitle (frappe.format returns
    // a block-floated element for Currency that breaks inline flow).
    const inrPlain = (v) => '₹ ' + Number(v).toLocaleString('en-IN', {
        maximumFractionDigits: 2, minimumFractionDigits: 2,
    });
    const ltp = data.last_traded_price;
    const subtitle = ltp
        ? `Latest Price: <b style="color: var(--text-color);">${inrPlain(ltp)}</b> &nbsp;·&nbsp; per-unit values are weighted averages within each classification.`
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
