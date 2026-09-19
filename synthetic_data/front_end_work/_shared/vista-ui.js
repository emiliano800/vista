/* Minimal shared engine for the six demo front ends.
 *
 * Each company page supplies a module config; this file renders navigation,
 * KPI strips, searchable/sortable/paginated tables, a record-detail panel with
 * related records, and a documents viewer. Every element gets a `vt-*` class so
 * each company's stylesheet can give it a completely different look.
 *
 *   VistaUI.mount({ modules: [...], nav: '#nav', content: '#content' });
 *
 * module = { id, label, group?, kpis?: [{label, value: fn}], tables?: [tableSpec], docs?: true, explorer?: true }
 * tableSpec = { title, table, columns?: [...], detail?: { related: [{ table, fk, title, key? }] }, note? }
 */
(function () {
  const D = window.VISTA_DATA;
  const state = { module: null, filters: {}, sort: {}, page: {}, detail: null, doc: null };
  const PAGE = 25;

  // ---- data helpers -------------------------------------------------------
  function rows(name) {
    const t = D.tables[name];
    if (!t) return [];
    if (!t._objs) t._objs = t.rows.map((r) => Object.fromEntries(t.columns.map((c, i) => [c, r[i]])));
    return t._objs;
  }
  function num(v) {
    if (v === null || v === undefined || v === '') return NaN;
    if (typeof v === 'number') return v;
    return parseFloat(String(v).replace(/[$,\s]/g, ''));
  }
  function sum(list, col) { return list.reduce((a, r) => a + (isNaN(num(r[col])) ? 0 : num(r[col])), 0); }
  function money(v) {
    const n = num(v);
    if (isNaN(n)) return v == null ? '' : String(v);
    return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
  }
  function count(list, pred) { return pred ? list.filter(pred).length : list.length; }
  const MONEY_RE = /(premium|amount|total|balance|cost|price|value|revenue|paid|reserve|incurred|commission|fee|limit|due|prem|comm_est|rate\b|extended|bal|current|over_90|1_30|31_60|61_90)/i;
  const NOMONEY_RE = /(pct|percent|qty|count|id$|number|date|hours|days|months|year|zip|code|score|mod$|rate_per|line$)/i;
  function isMoneyCol(c) { return MONEY_RE.test(c) && !NOMONEY_RE.test(c); }
  function fmtCell(col, v) {
    if (v === null || v === undefined) return '';
    if (isMoneyCol(col)) { const n = num(v); if (!isNaN(n) && Math.abs(n) >= 1) return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 2 }); }
    return String(v);
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
  function statusClass(v) {
    const s = String(v || '').toLowerCase();
    if (/(past due|overdue|open|hold|declined|expired|reopened|fail|reject|late|negative|dispute|variance|escalat|>60|>90)/.test(s)) return 'vt-bad';
    if (/(pending|partial|in progress|in review|quoted|submitted|marketing|draft|sent|planned|released|awaiting|scheduled)/.test(s)) return 'vt-warn';
    if (/(paid|closed|active|in force|issued|complete|passed|approved|delivered|bound|accepted|match|received|shipped|current)/.test(s)) return 'vt-good';
    return '';
  }
  const STATUS_RE = /(status|stage|result|disposition|hold|match)/i;

  // ---- rendering ----------------------------------------------------------
  let cfg;
  function h(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content; }

  function renderNav() {
    const nav = document.querySelector(cfg.nav);
    const groups = [];
    cfg.modules.forEach((m) => {
      let g = groups.find((x) => x.name === (m.group || ''));
      if (!g) { g = { name: m.group || '', items: [] }; groups.push(g); }
      g.items.push(m);
    });
    nav.innerHTML = groups.map((g) => `
      <div class="vt-nav-group">
        ${g.name ? `<div class="vt-nav-group-label">${esc(g.name)}</div>` : ''}
        ${g.items.map((m) => `<a href="#${m.id}" class="vt-nav-item ${state.module === m.id ? 'vt-active' : ''}" data-module="${m.id}">${m.icon ? `<span class="vt-nav-icon">${m.icon}</span>` : ''}<span>${esc(m.label)}</span></a>`).join('')}
      </div>`).join('');
  }

  function renderKpis(m) {
    if (!m.kpis || !m.kpis.length) return '';
    return `<div class="vt-kpis">${m.kpis.map((k) => {
      let v; try { v = k.value(); } catch (e) { v = '—'; }
      return `<div class="vt-kpi"><div class="vt-kpi-value">${esc(v)}</div><div class="vt-kpi-label">${esc(k.label)}</div>${k.sub ? `<div class="vt-kpi-sub">${esc(k.sub)}</div>` : ''}</div>`;
    }).join('')}</div>`;
  }

  function tableKey(m, spec, i) { return `${m.id}:${spec.table}:${i}`; }

  function renderTable(m, spec, i) {
    const key = tableKey(m, spec, i);
    const t = D.tables[spec.table];
    if (!t) {
      return `<section class="vt-card vt-missing"><h2 class="vt-card-title">${esc(spec.title)}</h2><div class="vt-empty">Dataset <code>${esc(spec.table)}</code> is not available in this company's exports.${spec.missingNote ? ' ' + esc(spec.missingNote) : ''}</div></section>`;
    }
    const cols = spec.columns && spec.columns.length ? spec.columns.filter((c) => t.columns.includes(c)) : t.columns;
    const all = rows(spec.table);
    let list = all;
    const q = (state.filters[key] || '').toLowerCase();
    if (q) list = list.filter((r) => t.columns.some((c) => String(r[c] ?? '').toLowerCase().includes(q)));
    if (spec.filter) list = list.filter(spec.filter);
    const srt = state.sort[key];
    if (srt) {
      list = [...list].sort((a, b) => {
        const x = a[srt.col], y = b[srt.col];
        const nx = num(x), ny = num(y);
        let c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x ?? '').localeCompare(String(y ?? ''));
        return srt.dir === 'asc' ? c : -c;
      });
    }
    const page = state.page[key] || 0;
    const pages = Math.max(1, Math.ceil(list.length / PAGE));
    const slice = list.slice(page * PAGE, (page + 1) * PAGE);
    const capped = t.total > t.rows.length ? ` (showing first ${t.rows.length} of ${t.total})` : '';
    return `
      <section class="vt-card" data-tkey="${esc(key)}">
        <div class="vt-card-head">
          <div>
            <h2 class="vt-card-title">${esc(spec.title)}</h2>
            <div class="vt-source">${esc(t.source)}${capped}${spec.note ? ' · ' + esc(spec.note) : ''}</div>
          </div>
          <div class="vt-toolbar">
            <input class="vt-search" type="search" placeholder="Search ${esc(spec.title)}…" value="${esc(state.filters[key] || '')}" data-search="${esc(key)}">
            <span class="vt-count">${list.length} rows</span>
          </div>
        </div>
        <div class="vt-table-wrap">
          <table class="vt-table">
            <thead><tr>${cols.map((c) => `<th data-sort="${esc(c)}" class="${srt && srt.col === c ? 'vt-sorted-' + srt.dir : ''} ${isMoneyCol(c) ? 'vt-num' : ''}">${esc(c)}</th>`).join('')}</tr></thead>
            <tbody>${slice.map((r) => `<tr class="vt-row" data-row="${all.indexOf(r)}">${cols.map((c) => {
              const v = fmtCell(c, r[c]);
              const cls = [isMoneyCol(c) ? 'vt-num' : '', STATUS_RE.test(c) ? statusClass(r[c]) : ''].join(' ').trim();
              return `<td class="${cls}">${STATUS_RE.test(c) && cls ? `<span class="vt-pill ${cls}">${esc(v)}</span>` : esc(v)}</td>`;
            }).join('')}</tr>`).join('')}
            ${slice.length ? '' : '<tr><td class="vt-empty" colspan="' + cols.length + '">No matching rows</td></tr>'}</tbody>
          </table>
        </div>
        <div class="vt-pager">
          <button class="vt-btn" data-page="${esc(key)}" data-dir="-1" ${page === 0 ? 'disabled' : ''}>‹ Prev</button>
          <span>Page ${page + 1} of ${pages}</span>
          <button class="vt-btn" data-page="${esc(key)}" data-dir="1" ${page >= pages - 1 ? 'disabled' : ''}>Next ›</button>
        </div>
      </section>`;
  }

  function renderDetail() {
    const d = state.detail;
    if (!d) return '';
    const t = D.tables[d.table];
    const r = rows(d.table)[d.index];
    const spec = d.spec;
    const related = (spec.detail && spec.detail.related) || [];
    return `
      <aside class="vt-detail">
        <div class="vt-detail-head">
          <div><div class="vt-detail-kicker">${esc(spec.title)} record</div><h3 class="vt-detail-title">${esc(r[spec.detail && spec.detail.titleCol] ?? r[t.columns[1]] ?? r[t.columns[0]])}</h3></div>
          <button class="vt-btn vt-close" data-close-detail>✕</button>
        </div>
        <dl class="vt-fields">${t.columns.map((c) => `<div class="vt-field"><dt>${esc(c)}</dt><dd>${esc(fmtCell(c, r[c]))}</dd></div>`).join('')}</dl>
        ${related.map((rel) => {
          const rt = D.tables[rel.table];
          if (!rt) return `<div class="vt-related"><h4>${esc(rel.title)}</h4><div class="vt-empty">Not available in this company's exports.</div></div>`;
          const keyCol = rel.key || (spec.detail && spec.detail.key) || t.columns[0];
          const val = r[keyCol];
          const list = rows(rel.table).filter((x) => String(x[rel.fk]) === String(val));
          const cols = rel.columns && rel.columns.length ? rel.columns.filter((c) => rt.columns.includes(c)) : rt.columns.slice(0, 7);
          return `<div class="vt-related"><h4>${esc(rel.title)} <span class="vt-count">${list.length}</span></h4>
            ${list.length ? `<div class="vt-table-wrap"><table class="vt-table vt-mini"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
            <tbody>${list.slice(0, 15).map((x) => `<tr>${cols.map((c) => `<td class="${isMoneyCol(c) ? 'vt-num' : ''}">${esc(fmtCell(c, x[c]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<div class="vt-empty">None</div>'}
          </div>`;
        }).join('')}
      </aside>`;
  }

  function renderDocs(m) {
    const names = Object.keys(D.docs).sort();
    const cur = state.doc || names[0];
    const doc = D.docs[cur];
    return `<section class="vt-card vt-docs">
      <div class="vt-card-head"><div><h2 class="vt-card-title">Documents & emails</h2><div class="vt-source">${names.length} files</div></div></div>
      <div class="vt-docs-body">
        <ul class="vt-doc-list">${names.map((n) => `<li class="vt-doc-item ${n === cur ? 'vt-active' : ''}" data-doc="${esc(n)}"><span class="vt-doc-ext">${esc(n.split('.').pop())}</span>${esc(n)}</li>`).join('')}</ul>
        <div class="vt-doc-view">${doc ? `<div class="vt-source">${esc(doc.source)}</div><pre class="vt-doc-text">${esc(doc.text)}</pre>` : ''}</div>
      </div></section>`;
  }

  function renderExplorer(m) {
    const names = Object.keys(D.tables).sort();
    return `<section class="vt-card"><div class="vt-card-head"><div><h2 class="vt-card-title">All data files</h2><div class="vt-source">${names.length} tables bundled from the company's exports</div></div></div>
      <div class="vt-table-wrap"><table class="vt-table"><thead><tr><th>Dataset</th><th>Source file</th><th class="vt-num">Rows</th><th class="vt-num">Columns</th><th>Columns</th></tr></thead>
      <tbody>${names.map((n) => { const t = D.tables[n]; return `<tr class="vt-row" data-explore="${esc(n)}"><td>${esc(n)}</td><td>${esc(t.source)}</td><td class="vt-num">${t.total}</td><td class="vt-num">${t.columns.length}</td><td class="vt-cols">${esc(t.columns.slice(0, 8).join(', '))}${t.columns.length > 8 ? '…' : ''}</td></tr>`; }).join('')}</tbody></table></div></section>
      ${state.explore ? renderTable(m, { title: state.explore, table: state.explore }, 'x') : ''}`;
  }

  function render() {
    const m = cfg.modules.find((x) => x.id === state.module) || cfg.modules[0];
    state.module = m.id;
    renderNav();
    const content = document.querySelector(cfg.content);
    let html = `<div class="vt-module-head"><h1 class="vt-module-title">${esc(m.label)}</h1>${m.description ? `<p class="vt-module-desc">${esc(m.description)}</p>` : ''}</div>`;
    html += renderKpis(m);
    if (m.explorer) html += renderExplorer(m);
    if (m.tables) html += `<div class="vt-tables">${m.tables.map((s, i) => renderTable(m, s, i)).join('')}</div>`;
    if (m.docs) html += renderDocs(m);
    content.innerHTML = html;
    const old = document.querySelector('.vt-detail'); if (old) old.remove();
    if (state.detail && state.detail.module === m.id) document.body.appendChild(h(renderDetail()));
    document.querySelectorAll('[data-title]').forEach((el) => { el.textContent = m.label; });
    if (cfg.onRender) cfg.onRender(m);
  }

  function findSpec(m, key) {
    if (key.endsWith(':x')) return { title: state.explore, table: state.explore };
    const parts = key.split(':');
    return m.tables[parseInt(parts[2], 10)];
  }

  function bind() {
    document.addEventListener('click', (e) => {
      const nav = e.target.closest('[data-module]');
      if (nav) { e.preventDefault(); state.module = nav.dataset.module; state.detail = null; location.hash = state.module; render(); return; }
      const th = e.target.closest('th[data-sort]');
      if (th) {
        const key = th.closest('[data-tkey]').dataset.tkey; const col = th.dataset.sort;
        const cur = state.sort[key];
        state.sort[key] = cur && cur.col === col && cur.dir === 'asc' ? { col, dir: 'desc' } : { col, dir: 'asc' };
        render(); return;
      }
      const pg = e.target.closest('[data-page]');
      if (pg && !pg.disabled) { const k = pg.dataset.page; state.page[k] = (state.page[k] || 0) + parseInt(pg.dataset.dir, 10); render(); return; }
      const closeBtn = e.target.closest('[data-close-detail]');
      if (closeBtn) { state.detail = null; render(); return; }
      const doc = e.target.closest('[data-doc]');
      if (doc) { state.doc = doc.dataset.doc; render(); return; }
      const ex = e.target.closest('[data-explore]');
      if (ex) { state.explore = ex.dataset.explore; render(); return; }
      const row = e.target.closest('tr.vt-row[data-row]');
      if (row) {
        const card = row.closest('[data-tkey]'); const m = cfg.modules.find((x) => x.id === state.module);
        const spec = findSpec(m, card.dataset.tkey);
        state.detail = { module: m.id, table: spec.table, index: parseInt(row.dataset.row, 10), spec };
        render(); return;
      }
    });
    document.addEventListener('input', (e) => {
      const s = e.target.closest('[data-search]');
      if (s) { state.filters[s.dataset.search] = s.value; state.page[s.dataset.search] = 0; render(); const el = document.querySelector(`[data-search="${CSS.escape(s.dataset.search)}"]`); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }
    });
    window.addEventListener('hashchange', () => { const id = location.hash.slice(1); if (id && cfg.modules.some((m) => m.id === id) && id !== state.module) { state.module = id; state.detail = null; render(); } });
  }

  window.VistaUI = {
    mount(config) {
      cfg = config;
      const id = location.hash.slice(1);
      state.module = cfg.modules.some((m) => m.id === id) ? id : cfg.modules[0].id;
      bind();
      render();
    },
    rows, sum, count, money, num, data: D,
    fmtInt(n) { return Number(n).toLocaleString('en-US'); },
    pct(a, b) { return b ? Math.round((a / b) * 100) + '%' : '—'; },
  };
})();
