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
        render_passbook_tabs(frm);
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
    mount.find('.polemarch-holdings-panel, .polemarch-security-breakdown').remove();

    frappe.call({
        method: 'polemarch.api.holdings_summary.get_security_rollup',
        args: { security: frm.doc.name },
    }).then((r) => {
        if (!r || !r.message) return;
        const data = r.message;
        // LCM table goes first into the mount so it lands BELOW the
        // breakdown cards (prepend reverses insertion order).
        const lcm_html = build_panel_html(data, frm.doc.name);
        mount.prepend(lcm_html);
        // Then breakdown cards (4-cell summary) — prepended ABOVE the LCM
        // table. Mirrors the Investment Holding form's at-a-glance panel.
        const cards_html = build_security_breakdown_html(data, frm.doc.name);
        if (cards_html) mount.prepend(cards_html);
    });
}

// Mirrors the IH form's render_classification_breakdown — 4 colored cards
// showing per-classification qty + book value + percentage, summed across
// all open Investment Holdings for this security.
function build_security_breakdown_html(data, security) {
    if (!data.has_holdings) return '';

    const sit = Number(data.sit_units || 0);
    const inv = Number(data.inv_units || 0);
    const unc = Number(data.unalloc_units || 0);
    const total = Number(data.total_units || 0);
    if (total === 0) return '';

    const sit_cost = Number(data.sit_cost || 0);
    const inv_cost = Number(data.inv_cost || 0);
    const unc_cost = Number(data.unalloc_cost || 0);
    const tot_cost = Number(data.total_cost || 0);

    const fmt_n = (v) => frappe.format(v, { fieldtype: 'Float', precision: 0 });
    const fmt_c = (v) => frappe.format(v, { fieldtype: 'Currency' });
    const pct = (v) => total ? ((v / total) * 100).toFixed(1) + '%' : '0%';

    function cell(label, qty, cost, color) {
        const muted = qty === 0;
        return `
          <td style="padding: 10px 14px; vertical-align: top; ${muted ? 'opacity: 0.5;' : ''}">
            <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">${label}</div>
            <div style="font-size: 20px; font-weight: 600; color: ${color}; margin-top: 2px; white-space: nowrap;">${fmt_n(qty)}</div>
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 2px;">${fmt_c(cost)} · ${pct(qty)}</div>
          </td>
        `;
    }

    return `
      <div class="polemarch-security-breakdown" style="
          margin: 0 0 12px 0;
          background: var(--card-bg);
          border: 1px solid var(--border-color);
          border-radius: 6px; overflow: hidden;">
        <table style="width: 100%; border-collapse: collapse; margin: 0;">
          <tr>
            ${cell('Stock in Trade', sit, sit_cost, '#0ea5e9')}
            <td style="width:1px; background: var(--border-color);"></td>
            ${cell('Investment',     inv, inv_cost, '#22c55e')}
            <td style="width:1px; background: var(--border-color);"></td>
            ${cell('Unclassified',   unc, unc_cost, '#9ca3af')}
            <td style="width:1px; background: var(--border-color);"></td>
            ${cell('Total Holdings', total, tot_cost, 'var(--text-color)')}
          </tr>
        </table>
      </div>
    `;
}

// Convert a classification_deadline timestamp into a human-readable
// countdown for the Unclassified row. Examples:
//   "auto-classifies as SiT in 3d 4h"     (3+ days remaining)
//   "auto-classifies as SiT in 18h 22m"   (under 24 hours)
//   "auto-classifies as SiT in 22m"       (under an hour)
//   "PAST DEADLINE — scheduler will flip on next run"  (already expired)
// Colors: green > 24h, amber 6–24h, red < 6h or expired.
function format_deadline_countdown(deadline_str) {
    if (!deadline_str) {
        return '<span style="color: var(--text-muted)">no classification deadline set</span>';
    }
    const deadline = new Date(deadline_str.replace(' ', 'T'));
    const now = new Date();
    const diff_ms = deadline - now;

    if (diff_ms <= 0) {
        return `<span style="color: #ef4444;">⚠ past deadline (${moment(deadline).format('D MMM, HH:mm')}) — scheduler will flip to SiT on next run</span>`;
    }

    const totalMin = Math.floor(diff_ms / 60000);
    const days = Math.floor(totalMin / (60 * 24));
    const hours = Math.floor((totalMin % (60 * 24)) / 60);
    const minutes = totalMin % 60;

    let pretty;
    if (days >= 1) {
        pretty = `${days}d ${hours}h`;
    } else if (hours >= 1) {
        pretty = `${hours}h ${minutes}m`;
    } else {
        pretty = `${minutes}m`;
    }

    // Color thresholds in HOURS.
    const total_hours = diff_ms / 3600000;
    let color;
    if (total_hours > 24) color = '#22c55e';      // green: plenty of time
    else if (total_hours > 6) color = '#f59e0b';  // amber: act soon
    else color = '#ef4444';                       // red: urgent

    return `<span style="color: ${color};">⏱ auto-classifies as SiT in <b>${pretty}</b></span>
            <small style="color: var(--text-muted)"> (${moment(deadline).format('D MMM, HH:mm')})</small>`;
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
        // Label gets a countdown when Unclassified has shares.
        let labelHtml;
        if (opts.unclassified && units) {
            const countdown = format_deadline_countdown(opts.deadline);
            labelHtml = `
                <div>${label}</div>
                <div style="font-size: 0.85em; font-weight: normal; margin-top: 2px;">
                    ${countdown}
                </div>
            `;
        } else if (opts.unclassified && !units) {
            labelHtml = `${label} <small style="color: var(--text-muted); font-weight: normal;">(no shares awaiting classification)</small>`;
        } else {
            labelHtml = label;
        }
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
    // 5-day window that haven't been assigned to SiT or Investment yet;
    // we surface the earliest classification_deadline as a countdown so
    // operators don't accidentally let them auto-flip to SiT.
    const rows = [];
    rows.push(row('Stock in Trade', data.sit_units || 0, data.sit_cost || 0, data.sit_market, data.sit_lcm || 0));
    rows.push(row('Investment',     data.inv_units || 0, data.inv_cost || 0, data.inv_market, data.inv_lcm || 0));
    rows.push(row('Unclassified',   data.unalloc_units || 0, data.unalloc_cost || 0, data.unalloc_market, data.unalloc_lcm || 0,
                  { unclassified: true, deadline: data.earliest_classification_deadline }));
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


// Passbook + Counterparties tabs — every Purchase, Sale and Portfolio
// Transfer of this security by transfer date, computed live by
// polemarch_trading.passbook (nothing stored on the Security).
function render_passbook_tabs(frm) {
    const pb = frm.fields_dict.passbook_html;
    const cp = frm.fields_dict.counterparties_html;
    if (!pb || !cp) return;
    const loading = `<div style="color: var(--text-muted); padding: 8px 0;">${__('Loading…')}</div>`;
    pb.$wrapper.html(loading);
    cp.$wrapper.html(loading);

    frappe.call({
        method: 'polemarch.polemarch_trading.api.passbook.get_security_passbook',
        args: { security: frm.doc.name },
    }).then((r) => pb.$wrapper.html(build_passbook_html(r.message || {}, frm.doc.name)));

    frappe.call({
        method: 'polemarch.polemarch_trading.api.passbook.get_security_counterparties',
        args: { security: frm.doc.name },
    }).then((r) => cp.$wrapper.html(build_counterparties_html(r.message || [], frm.doc.name)));
}

// Plain-string formatters: frappe.format wraps Float/Currency in a
// right-aligned block div, which breaks inline text.
const pb_fmt = {
    n: (v) => (v ? format_number(v, null, 0) : ''),
    c: (v) => format_currency(v || 0, 'INR'),
    d: (v) => (v ? frappe.datetime.str_to_user(v) : ''),
    esc: (v) => frappe.utils.escape_html(v || ''),
    link: (dt, name) => (name ? `<a href="${frappe.utils.get_form_link(dt, name)}">${frappe.utils.escape_html(name)}</a>` : ''),
};

const pb_css = {
    table: 'width: 100%; border-collapse: collapse; font-size: 12.5px;',
    th: 'padding: 6px 8px; text-align: left; font-weight: 600; white-space: nowrap; background: var(--bg-color); border-bottom: 1px solid var(--border-color);',
    th_r: 'padding: 6px 8px; text-align: right; font-weight: 600; white-space: nowrap; background: var(--bg-color); border-bottom: 1px solid var(--border-color);',
    td: 'padding: 5px 8px; border-bottom: 1px solid var(--border-color); vertical-align: top;',
    td_r: 'padding: 5px 8px; border-bottom: 1px solid var(--border-color); text-align: right; white-space: nowrap; vertical-align: top;',
};

// Payment type + one line per payment leg, each opening its document.
function pb_payment_cell(e) {
    if (!e.payment_type && !(e.payments || []).length) return '';
    const lines = (e.payments || []).map((p) => {
        const doc = p.wallet_transaction ? pb_fmt.link('Wallet Transaction', p.wallet_transaction)
            : p.journal_entry ? pb_fmt.link('Journal Entry', p.journal_entry) : '';
        const via = [pb_fmt.esc(p.paid_through), pb_fmt.esc(p.reference)].filter(Boolean).join(' · ');
        return `<div class="small text-muted">${pb_fmt.c(p.amount)} ${via} ${doc}</div>`;
    }).join('');
    return `<div>${pb_fmt.esc(e.payment_type)}</div>${lines}`;
}

function pb_report_link(security, view) {
    const q = new URLSearchParams({ security, view }).toString();
    return `/app/query-report/Securities Passbook?${q}`;
}

function build_passbook_html(data, security) {
    const entries = data.entries || [];
    if (!entries.length) {
        return `<div style="color: var(--text-muted); padding: 8px 0;">${__('No submitted Security Purchase, Security Sale or Portfolio Transfer for this security.')}</div>`;
    }
    const ok = data.qty_ok && data.cost_ok;
    const check = ok
        ? `<div style="padding: 8px 12px; margin-bottom: 10px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 12.5px;">
             ✓ ${__('Closing balance')} <b>${pb_fmt.n(data.closing_qty) || 0}</b> = ${__('Total Units')} ·
             ${__('closing cost')} <b>${pb_fmt.c(data.closing_cost)}</b> = ${__('Total Cost (book)')}
             <a style="float: right;" href="${pb_report_link(security, 'Passbook')}">${__('open as report')} →</a>
           </div>`
        : `<div style="padding: 8px 12px; margin-bottom: 10px; border: 1px solid var(--red-500); color: var(--red-600); border-radius: 6px; font-size: 12.5px;">
             ⚠ ${__('Passbook does not tie to the Security totals')}:
             ${__('closing qty')} <b>${pb_fmt.n(data.closing_qty) || 0}</b> vs ${__('Total Units')} <b>${pb_fmt.n(data.qty_total) || 0}</b>;
             ${__('closing cost')} <b>${pb_fmt.c(data.closing_cost)}</b> vs ${__('Total Cost (book)')} <b>${pb_fmt.c(data.cost_total)}</b>.
           </div>`;

    const rows = entries.map((e) => {
        const reclass = e.direction === 'Reclass';
        const party = e.party ? pb_fmt.link(e.party_type, e.party) : `<span style="color: var(--text-muted)">${__('own books')}</span>`;
        const cls = reclass
            ? `${pb_fmt.esc(e.classification)} · ${pb_fmt.n(e.reclass_qty)}${e.reversal_of ? ` <small>(${__('reverses')} ${pb_fmt.esc(e.reversal_of)})</small>` : ''}`
            : pb_fmt.esc(e.classification);
        return `<tr>
            <td style="${pb_css.td} white-space: nowrap;">${pb_fmt.d(e.date)}</td>
            <td style="${pb_css.td} white-space: nowrap;">${pb_fmt.link(e.voucher_type, e.voucher_no)}</td>
            <td style="${pb_css.td}">${party}</td>
            <td style="${pb_css.td}">${cls}</td>
            <td style="${pb_css.td_r} color: var(--green-600);">${pb_fmt.n(e.qty_in)}</td>
            <td style="${pb_css.td_r} color: var(--red-600);">${pb_fmt.n(e.qty_out)}</td>
            <td style="${pb_css.td_r}">${reclass ? '' : pb_fmt.c(e.rate)}</td>
            <td style="${pb_css.td_r}">${reclass ? '' : pb_fmt.c(e.amount)}</td>
            <td style="${pb_css.td_r} font-weight: 600;">${pb_fmt.n(e.balance_qty) || 0}</td>
            <td style="${pb_css.td_r}">${pb_fmt.c(e.balance_cost)}</td>
            <td style="${pb_css.td} min-width: 170px;">${pb_payment_cell(e)}</td>
        </tr>`;
    }).join('');

    return `${check}
      <div style="overflow-x: auto;">
        <table style="${pb_css.table}">
          <thead><tr>
            <th style="${pb_css.th}">${__('Transfer Date')}</th>
            <th style="${pb_css.th}">${__('Document')}</th>
            <th style="${pb_css.th}">${__('Party')}</th>
            <th style="${pb_css.th}">${__('Classification')}</th>
            <th style="${pb_css.th_r}">${__('In')}</th>
            <th style="${pb_css.th_r}">${__('Out')}</th>
            <th style="${pb_css.th_r}">${__('Rate')}</th>
            <th style="${pb_css.th_r}">${__('Amount')}</th>
            <th style="${pb_css.th_r}">${__('Balance Qty')}</th>
            <th style="${pb_css.th_r}">${__('Balance Cost (FIFO)')}</th>
            <th style="${pb_css.th}">${__('Payment')}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
}

function build_counterparties_html(groups, security) {
    if (!groups.length) {
        return `<div style="color: var(--text-muted); padding: 8px 0;">${__('No counterparties yet.')}</div>`;
    }
    const head = `
      <div style="display: grid; grid-template-columns: 2.4fr repeat(5, 1fr) 0.6fr; gap: 8px; padding: 6px 12px; font-weight: 600; font-size: 12px; background: var(--bg-color); border-bottom: 1px solid var(--border-color);">
        <span>${__('Party')}</span>
        <span style="text-align: right;">${__('Bought from')}</span>
        <span style="text-align: right;">${__('Sold to')}</span>
        <span style="text-align: right;">${__('Net Qty')}</span>
        <span style="text-align: right;">${__('Paid')}</span>
        <span style="text-align: right;">${__('Received')}</span>
        <span style="text-align: right;">${__('Count')}</span>
      </div>`;
    const body = groups.map((g) => {
        const lines = g.entries.map((e) => `<tr>
            <td style="${pb_css.td} white-space: nowrap;">${pb_fmt.d(e.date)}</td>
            <td style="${pb_css.td} white-space: nowrap;">${pb_fmt.link(e.voucher_type, e.voucher_no)}</td>
            <td style="${pb_css.td}">${pb_fmt.esc(e.classification)}</td>
            <td style="${pb_css.td_r} color: var(--green-600);">${pb_fmt.n(e.qty_in)}</td>
            <td style="${pb_css.td_r} color: var(--red-600);">${pb_fmt.n(e.qty_out)}</td>
            <td style="${pb_css.td_r}">${pb_fmt.c(e.rate)}</td>
            <td style="${pb_css.td_r}">${pb_fmt.c(e.amount)}</td>
            <td style="${pb_css.td}">${pb_payment_cell(e)}</td>
        </tr>`).join('');
        return `
          <details style="border-bottom: 1px solid var(--border-color);">
            <summary style="display: grid; grid-template-columns: 2.4fr repeat(5, 1fr) 0.6fr; gap: 8px; padding: 7px 12px; cursor: pointer; font-size: 12.5px;">
              <span>${pb_fmt.link(g.party_type, g.party)} <small style="color: var(--text-muted)">${pb_fmt.esc(g.party_type)}</small></span>
              <span style="text-align: right; color: var(--green-600);">${pb_fmt.n(g.qty_in)}</span>
              <span style="text-align: right; color: var(--red-600);">${pb_fmt.n(g.qty_out)}</span>
              <span style="text-align: right;">${pb_fmt.n(g.net_qty) || 0}</span>
              <span style="text-align: right;">${g.amount_paid ? pb_fmt.c(g.amount_paid) : ''}</span>
              <span style="text-align: right;">${g.amount_received ? pb_fmt.c(g.amount_received) : ''}</span>
              <span style="text-align: right;">${g.count}</span>
            </summary>
            <div style="padding: 0 12px 10px 28px; overflow-x: auto;">
              <table style="${pb_css.table}">
                <thead><tr>
                  <th style="${pb_css.th}">${__('Transfer Date')}</th>
                  <th style="${pb_css.th}">${__('Document')}</th>
                  <th style="${pb_css.th}">${__('Classification')}</th>
                  <th style="${pb_css.th_r}">${__('In')}</th>
                  <th style="${pb_css.th_r}">${__('Out')}</th>
                  <th style="${pb_css.th_r}">${__('Rate')}</th>
                  <th style="${pb_css.th_r}">${__('Amount')}</th>
                  <th style="${pb_css.th}">${__('Payment')}</th>
                </tr></thead>
                <tbody>${lines}</tbody>
              </table>
            </div>
          </details>`;
    }).join('');
    return `
      <div style="margin-bottom: 8px; font-size: 12.5px; color: var(--text-muted);">
        ${__('Bought from / sold to, by party. Click a row to see its movements.')}
        <a style="float: right;" href="${pb_report_link(security, 'Counterparties')}">${__('open as report')} →</a>
      </div>
      <div style="border: 1px solid var(--border-color); border-radius: 6px; overflow: hidden;">${head}${body}</div>`;
}
