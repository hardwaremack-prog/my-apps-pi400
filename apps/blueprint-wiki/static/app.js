/* Blueprint - main wiki app */
(function () {
'use strict';
const BP = window.BP, esc = BP.esc;
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
const S = { sections: [], pages: [], diagrams: [], files: [], route: { name: 'home' }, page: null, editing: false, dirty: false,
  saveT: null, range: null, search: '', fileFilter: 'all', pendingEdit: null, listSeq: 0 };
BP.S = S;

/* ---------- icons */
const P = {
  home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>',
  folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/>',
  gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
  flask: '<path d="M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7.5 14h9"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  wrench: '<path d="M15 4a5 5 0 0 0-4.6 6.9L4 17.3V20h2.7l6.4-6.4A5 5 0 0 0 20 9l-3 1-2-2 1-3z"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  star: '<path d="m12 3 2.8 5.8 6.2.9-4.5 4.4 1.1 6.2L12 17.4l-5.6 2.9 1.1-6.2L3 9.7l6.2-.9z"/>',
  box: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
  code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5"/>',
  doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  diagram: '<rect x="3" y="3" width="7" height="6" rx="1"/><rect x="14" y="15" width="7" height="6" rx="1"/><rect x="14" y="3" width="7" height="6" rx="1"/><path d="M6.5 9v9H14M10 6h4"/>',
  files: '<rect x="3" y="5" width="18" height="15" rx="2"/><circle cx="9" cy="10.5" r="1.8"/><path d="m21 16-5-5-9 9"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>', check: '<path d="m5 12.5 4.5 4.5L19 7"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
  print: '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 10h18M3 15h18M10 4v16"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  olist: '<path d="M10 6h10M10 12h10M10 18h10M4 4.5h1.5V9M4 14.5c0-1 2.5-1 2.5.5S4 17 4 19.5h2.5"/>',
  checklist: '<rect x="3" y="4" width="6" height="6" rx="1"/><path d="m4.5 7 1 1 2-2M12 7h9M12 17h9"/><rect x="3" y="14" width="6" height="6" rx="1"/>',
  quote: '<path d="M7 17c-2 0-3-1.5-3-4 0-3 2-6 5-7M16 17c-2 0-3-1.5-3-4 0-3 2-6 5-7"/>', hr: '<path d="M3 12h18"/>',
  callout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 8v5M12 16.5v.01"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>', redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', upload: '<path d="M12 16V5M7 10l5-5 5 5M5 20h14"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>', back: '<path d="M15 5l-7 7 7 7"/>',
  clip: '<path d="m20 11.5-8.3 8.3a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>', tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  layout: '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-4h12v4"/>',
  hand: '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V11M11 10.5V4a1.5 1.5 0 0 1 3 0v6.5M14 6.5a1.5 1.5 0 0 1 3 0V11M17 8.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-2a6 6 0 0 1-5-2.7L4.3 14a1.5 1.5 0 0 1 2.4-1.8L8 14"/>',
  grid: '<path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>', image: '<rect x="3" y="5" width="18" height="15" rx="2"/><circle cx="9" cy="10.5" r="1.8"/><path d="m21 16-5-5-9 9"/>',
  pdf: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>', moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  car: '<path d="M5 16V11l2-5h10l2 5v5M3 16h18v3H3zM7 19v2M17 19v2"/><circle cx="7.5" cy="13.5" r="1"/><circle cx="16.5" cy="13.5" r="1"/>',
  leaf: '<path d="M5 19c0-9 6-14 15-14 0 9-5 15-14 15"/><path d="M5 19 14 10"/>',
};
const ic = n => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${P[n] || P.doc}</svg>`;
BP.ic = ic;
const SECTION_ICONS = ['folder', 'globe', 'gear', 'flask', 'book', 'cpu', 'wrench', 'bolt', 'star', 'box', 'code', 'doc', 'diagram', 'home', 'car', 'leaf'];
const SECTION_COLORS = ['#4da3ff', '#2ee6c5', '#f5a524', '#b57bff', '#ff7a90', '#3ccf91', '#ffd84d', '#ff8c42', '#7aa2ff', '#e86bd0'];

/* ---------- helpers */
async function api(method, url, body, headers) {
  const o = { method, headers: Object.assign({}, headers || {}) };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) o.body = body;
    else { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(url, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || data || r.statusText);
  return data;
}
BP.api = api;
function toast(msg, kind) {
  const t = document.createElement('div');
  t.className = 'toast ' + (kind || '');
  t.textContent = msg;
  $('#toast').appendChild(t);
  setTimeout(() => t.classList.add('out'), 2800);
  setTimeout(() => t.remove(), 3300);
}
BP.toast = toast;
const debounce = (f, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; };
function fmtDate(t) {
  if (!t) return '';
  const d = new Date(t), n = new Date();
  if (d.toDateString() === n.toDateString()) return 'today ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: d.getFullYear() === n.getFullYear() ? undefined : 'numeric' });
}
const fmtSize = b => b < 1024 ? b + ' B' : b < 1048576 ? Math.round(b / 1024) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
const fileKind = f => /^image\//.test(f.type || '') ? 'image' : (f.type === 'application/pdf' || /\.pdf$/i.test(f.name || '')) ? 'pdf' : 'other';
const ext = name => ((name || '').split('.').pop() || 'file').slice(0, 4).toUpperCase();
const secOf = p => (S.sections.some(s => s.id === p.section) ? p.section : (S.sections[0] || {}).id);
const sectionById = id => S.sections.find(s => s.id === id);
const greet = () => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'; };

/* ---------- modals */
function modal({ title, body, wide, buttons, onClose }) {
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal ${wide ? 'wide' : ''}" role="dialog"><header><h3>${esc(title || '')}</h3><button class="btn ghost icon" data-x="1" title="Close">${ic('close')}</button></header><div class="modal-body">${body || ''}</div>${buttons ? '<footer></footer>' : ''}</div>`;
  $('#modalRoot').appendChild(back);
  const el = back.querySelector('.modal');
  let closed = false;
  const close = (v) => {
    if (closed) return;
    closed = true; back.remove();
    document.removeEventListener('keydown', key, true);
    if (onClose) onClose(v);
  };
  const key = e => {
    if (e.key === 'Escape' && back === $$('.modal-back').pop()) { e.preventDefault(); e.stopImmediatePropagation(); close(null); }
  };
  document.addEventListener('keydown', key, true);
  back.addEventListener('mousedown', e => { if (e.target === back) close(null); });
  el.querySelector('[data-x]').addEventListener('click', () => close(null));
  if (buttons) {
    const f = el.querySelector('footer');
    buttons.forEach(b => {
      const btn = document.createElement('button');
      btn.className = 'btn ' + (b.primary ? 'primary' : b.danger ? 'danger solid' : 'ghost');
      btn.innerHTML = b.label;
      btn.addEventListener('click', async () => {
        if (!b.onClick) return close(null);
        const r = await b.onClick();
        if (r !== false) close(r);
      });
      f.appendChild(btn);
    });
  }
  return { el, close };
}
BP.modal = modal;
function ask(title, fields, okLabel) {
  return new Promise(resolve => {
    const body = fields.map((f, i) => `<label class="field">${esc(f.label)}${f.options ? `<select data-i="${i}">${f.options.map(o => `<option value="${esc(o.value)}" ${o.value === f.value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>` : `<input data-i="${i}" type="${f.type || 'text'}" value="${esc(f.value || '')}" placeholder="${esc(f.placeholder || '')}">`}</label>`).join('');
    let done = false;
    const read = () => fields.reduce((o, f, i) => { o[f.name] = m.el.querySelector(`[data-i="${i}"]`).value; return o; }, {});
    const m = modal({ title, body, buttons: [{ label: 'Cancel' }, { label: okLabel || 'OK', primary: true, onClick: () => { done = true; resolve(read()); return true; } }],
      onClose: () => { if (!done) resolve(null); } });
    const first = m.el.querySelector('input,select');
    if (first) { first.focus(); if (first.select) first.select(); }
    m.el.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); done = true; resolve(read()); m.close(); } }));
  });
}
function confirmBox(title, msg, okLabel) {
  return new Promise(resolve => {
    let ok = false;
    modal({ title, body: `<p>${msg}</p>`, buttons: [{ label: 'Cancel' }, { label: okLabel || 'Delete', danger: true, onClick: () => { ok = true; return true; } }], onClose: () => resolve(ok) });
  });
}
function lightbox(html) {
  const lb = document.createElement('div');
  lb.className = 'lightbox';
  lb.innerHTML = `<div class="lb-inner">${html}</div><button class="btn ghost icon lb-x">${ic('close')}</button>`;
  document.body.appendChild(lb);
  const close = () => { lb.remove(); document.removeEventListener('keydown', k, true); };
  const k = e => { if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); } };
  document.addEventListener('keydown', k, true);
  lb.addEventListener('click', e => { if (e.target === lb || e.target.closest('.lb-x') || e.target.tagName === 'IMG') close(); });
}

/* ---------- state + routing */
async function refreshState() {
  const st = await api('GET', '/api/state');
  S.sections = st.sections || []; S.pages = st.pages || []; S.diagrams = st.diagrams || []; S.files = st.files || [];
}
BP.onDiagramsChanged = debounce(async () => {
  try { const st = await api('GET', '/api/state'); S.diagrams = st.diagrams; renderList(); if (S.route.name === 'home' && !BP.diagramIsOpen()) renderMain(); } catch (e) { /* offline */ }
}, 300);
function parseHash() {
  const h = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  const [a, b] = h.split('/');
  if (a === 's' && b) return { name: 'section', id: b };
  if (a === 'p' && b) return { name: 'page', id: b };
  if (a === 'd' && b) return { name: 'diagrams', open: b };
  if (a === 'diagrams') return { name: 'diagrams' };
  if (a === 'files') return { name: 'files' };
  if (a === 'settings') return { name: 'settings' };
  return { name: 'home' };
}
async function onRoute() {
  if (S.dirty) await savePage();
  S.route = parseHash();
  S.editing = false; S.page = null;
  if (S.route.name === 'page') {
    try { S.page = await api('GET', '/api/page/' + encodeURIComponent(S.route.id)); } catch (e) { S.page = null; }
    if (S.page && S.pendingEdit === S.page.id) { S.editing = true; S.pendingEdit = null; }
  }
  renderAll();
  $('#main').scrollTop = 0;
  if (S.route.open) {
    BP.openDiagram(S.route.open, { onClose: () => { history.replaceState(null, '', '#/diagrams'); S.route = { name: 'diagrams' }; renderAll(); } });
  }
  if (S.editing) setTimeout(() => { const t = $('#pTitle'); if (t && /^untitled/i.test(t.value)) t.select(); else focusEnd(); }, 30);
}
const go = h => { if (location.hash === h) onRoute(); else location.hash = h; };
function renderAll() { renderRail(); renderList(); renderMain(); }
function currentSection() {
  if (S.route.name === 'section') return S.route.id;
  if (S.route.name === 'page' && S.page) return secOf(S.page);
  return null;
}

/* ---------- rail (left tabs) */
function renderRail() {
  const cur = currentSection(), rn = S.route.name;
  const tab = (href, icon, label, on, extra, color) => `<a class="tab ${on ? 'on' : ''}" href="${href}" ${color ? `style="--c:${color}"` : ''}>${ic(icon)}<span>${esc(label)}</span>${extra != null ? `<em>${extra}</em>` : ''}</a>`;
  let h = `<div class="brand"><img src="/static/icon.svg" alt=""><span>Blueprint</span></div>`;
  h += tab('#/', 'home', 'Home', rn === 'home');
  h += `<div class="rail-label">Sections</div>`;
  S.sections.forEach(s => { h += tab('#/s/' + s.id, s.icon, s.name, cur === s.id, S.pages.filter(p => secOf(p) === s.id).length, s.color); });
  h += `<button class="tab add" data-act="add-section">${ic('plus')}<span>Add section</span></button>`;
  h += `<div class="rail-label">Toolkit</div>`;
  h += tab('#/diagrams', 'diagram', 'Diagrams', rn === 'diagrams', S.diagrams.length, '#2ee6c5');
  h += tab('#/files', 'files', 'Files & PDFs', rn === 'files', S.files.length, '#f5a524');
  h += `<div class="rail-sp"></div>`;
  h += tab('#/settings', 'settings', 'Settings', rn === 'settings');
  $('#rail').innerHTML = h;
}

/* ---------- list column */
function buildListShell() {
  $('#list').innerHTML = `<div class="list-search">${ic('search')}<input id="q" placeholder="Search everything…  Ctrl+K" autocomplete="off"><button class="btn ghost icon" id="qClear" title="Clear" hidden>${ic('close')}</button></div><div id="listHead"></div><div id="listBody"></div>`;
  const q = $('#q');
  q.addEventListener('input', debounce(() => { S.search = q.value.trim(); $('#qClear').hidden = !S.search; renderList(); }, 180));
  q.addEventListener('keydown', e => {
    if (e.key === 'Enter') { const a = $('#listBody .item'); if (a) a.click(); }
    if (e.key === 'Escape') { q.value = ''; S.search = ''; $('#qClear').hidden = true; renderList(); q.blur(); }
  });
  $('#qClear').addEventListener('click', () => { q.value = ''; S.search = ''; $('#qClear').hidden = true; renderList(); });
}
function pageItem(p, on) {
  const s = sectionById(secOf(p));
  return `<a class="item ${on ? 'on' : ''}" href="#/p/${esc(p.id)}" style="--c:${s ? s.color : 'var(--accent)'}"><span class="t">${p.pinned ? `<i class="pin">${ic('star')}</i>` : ''}${esc(p.title || 'Untitled')}</span><span class="s">${esc(p.excerpt || 'Empty page')}</span><span class="d">${fmtDate(p.updated)}</span></a>`;
}
const sortPages = arr => arr.slice().sort((a, b) => (b.pinned - a.pinned) || ((b.updated || 0) - (a.updated || 0)));
async function renderList() {
  const head = $('#listHead'), body = $('#listBody'), seq = ++S.listSeq;
  if (S.search) {
    head.innerHTML = `<div class="lh"><b>Search</b><span class="muted">“${esc(S.search)}”</span></div>`;
    body.innerHTML = '<div class="muted pad">Searching…</div>';
    let res = [];
    try { res = await api('GET', '/api/search?q=' + encodeURIComponent(S.search)); } catch (e) { /* ignore */ }
    if (seq !== S.listSeq) return;
    body.innerHTML = res.length ? res.map(r => {
      const href = r.kind === 'page' ? '#/p/' + r.id : r.kind === 'diagram' ? '#/d/' + r.id : '#/files';
      const s = r.kind === 'page' ? sectionById(r.section) : null;
      return `<a class="item" href="${href}" ${r.kind === 'file' ? `data-file="${esc(r.id)}"` : ''} style="--c:${s ? s.color : r.kind === 'diagram' ? '#2ee6c5' : '#f5a524'}"><span class="t">${r.kind !== 'page' ? `<i class="kind">${ic(r.kind === 'diagram' ? 'diagram' : 'files')}</i>` : ''}${esc(r.title)}</span><span class="s">${esc(r.snippet || '')}</span></a>`;
    }).join('') : '<div class="empty-small">Nothing found.<br><span class="muted">Try fewer words.</span></div>';
    return;
  }
  const rn = S.route.name;
  if (rn === 'diagrams') {
    head.innerHTML = `<div class="lh"><b>Diagrams</b><button class="btn small primary" data-act="new-diagram">${ic('plus')} New</button></div>`;
    const ds = S.diagrams.slice().sort((a, b) => (b.updated || 0) - (a.updated || 0));
    body.innerHTML = ds.map(d => `<a class="item" href="#/d/${esc(d.id)}" style="--c:#2ee6c5"><span class="t">${esc(d.title)}</span><span class="s">${d.count} shapes</span><span class="d">${fmtDate(d.updated)}</span></a>`).join('') || '<div class="empty-small">No diagrams yet.</div>';
    return;
  }
  if (rn === 'files') {
    head.innerHTML = `<div class="lh"><b>Files & PDFs</b><button class="btn small primary" data-act="upload">${ic('upload')} Upload</button></div>`;
    body.innerHTML = S.files.slice().sort((a, b) => b.mtime - a.mtime).map(f => `<a class="item" href="#/files" data-file="${esc(f.name)}" style="--c:#f5a524"><span class="t"><i class="kind">${ic(fileKind(f) === 'image' ? 'image' : 'pdf')}</i>${esc(f.name)}</span><span class="s">${fmtSize(f.size)}</span></a>`).join('') || '<div class="empty-small">No files yet.</div>';
    return;
  }
  if (rn === 'settings') {
    head.innerHTML = `<div class="lh"><b>Settings</b></div>`;
    body.innerHTML = ['Appearance', 'Sections', 'Backup & move', 'Network access', 'Shortcuts'].map((t, i) => `<a class="item" href="#/settings" data-jump="set${i}"><span class="t">${t}</span></a>`).join('');
    return;
  }
  const sid = currentSection();
  if (sid) {
    const s = sectionById(sid);
    head.innerHTML = `<div class="lh" style="--c:${s.color}"><b>${ic(s.icon)} ${esc(s.name)}</b><button class="btn small primary" data-act="new-page" data-sec="${esc(sid)}">${ic('plus')} Page</button></div>`;
    const ps = sortPages(S.pages.filter(p => secOf(p) === sid));
    body.innerHTML = ps.map(p => pageItem(p, S.page && S.page.id === p.id)).join('') || '<div class="empty-small">No pages here yet.</div>';
    return;
  }
  head.innerHTML = `<div class="lh"><b>Recently updated</b><button class="btn small primary" data-act="new-page">${ic('plus')} Page</button></div>`;
  body.innerHTML = sortPages(S.pages).slice(0, 40).map(p => pageItem(p, false)).join('') || '<div class="empty-small">No pages yet.</div>';
}

/* ---------- main area */
function renderMain() {
  const rn = S.route.name;
  document.title = 'Blueprint';
  if (rn === 'page') return renderPage();
  if (rn === 'section') return renderSection();
  if (rn === 'diagrams') return renderDiagrams();
  if (rn === 'files') return renderFiles();
  if (rn === 'settings') return renderSettings();
  return renderHome();
}
function pageCard(p) {
  const s = sectionById(secOf(p));
  return `<a class="card" href="#/p/${esc(p.id)}" style="--c:${s ? s.color : 'var(--accent)'}"><div class="card-sec">${s ? ic(s.icon) + esc(s.name) : ''}${p.pinned ? `<i class="pin">${ic('star')}</i>` : ''}</div><h3>${esc(p.title || 'Untitled')}</h3><p>${esc(p.excerpt || 'Empty page')}</p><div class="card-foot">${(p.tags || []).slice(0, 3).map(t => `<span class="chip sm">#${esc(t)}</span>`).join('')}<span class="sp"></span>${fmtDate(p.updated)}</div></a>`;
}
async function diagramCards(target, limit) {
  let all = [];
  try { all = await api('GET', '/api/diagrams'); } catch (e) { /* ignore */ }
  all.sort((a, b) => (b.updated || 0) - (a.updated || 0));
  if (limit) all = all.slice(0, limit);
  const el = $(target);
  if (!el) return;
  el.innerHTML = `<button class="card dg new" data-act="new-diagram"><div class="thumb">${ic('plus')}</div><h3>New diagram</h3><p>Network map, flowchart or process</p></button>` +
    all.map(d => `<a class="card dg" href="#/d/${esc(d.id)}"><div class="thumb">${(d.nodes || []).length ? BP.diagramSVG(d, { pad: 16 }) : '<span class="muted">Empty</span>'}</div><h3>${esc(d.title || 'Untitled diagram')}</h3><div class="card-foot">${(d.nodes || []).length} shapes<span class="sp"></span>${fmtDate(d.updated)}${limit ? '' : `<button class="btn ghost icon small" data-del-dg="${esc(d.id)}" title="Delete diagram">${ic('trash')}</button>`}</div></a>`).join('');
}
function renderHome() {
  const pinned = S.pages.filter(p => p.pinned);
  const recent = sortPages(S.pages.filter(p => !p.pinned)).slice(0, 6);
  $('#main').innerHTML = `<div class="wrap">
    <div class="hero"><div><h1>${greet()}</h1><p class="muted">${S.pages.length} pages · ${S.diagrams.length} diagrams · ${S.files.length} files</p></div>
      <div class="hero-actions"><button class="btn primary" data-act="new-page">${ic('plus')} New page</button><button class="btn" data-act="new-diagram">${ic('diagram')} New diagram</button><button class="btn" data-act="upload">${ic('upload')} Upload files</button></div></div>
    ${pinned.length ? `<h2 class="h2">${ic('star')} Pinned</h2><div class="cards">${pinned.map(pageCard).join('')}</div>` : ''}
    <h2 class="h2">${ic('history')} Recently updated</h2><div class="cards">${recent.map(pageCard).join('') || '<p class="muted">Nothing yet. Make your first page!</p>'}</div>
    <h2 class="h2">${ic('diagram')} Recent diagrams</h2><div class="cards dgs" id="homeDg"></div>
  </div>`;
  diagramCards('#homeDg', 5);
}
function renderSection() {
  const s = sectionById(S.route.id);
  if (!s) { $('#main').innerHTML = `<div class="wrap"><div class="empty"><h2>Section not found</h2><a class="btn" href="#/">Go home</a></div></div>`; return; }
  const ps = sortPages(S.pages.filter(p => secOf(p) === s.id));
  $('#main').innerHTML = `<div class="wrap"><div class="hero sec" style="--c:${s.color}"><div><div class="sec-ic">${ic(s.icon)}</div><h1>${esc(s.name)}</h1><p class="muted">${ps.length} page${ps.length === 1 ? '' : 's'}</p></div>
    <div class="hero-actions"><button class="btn primary" data-act="new-page" data-sec="${esc(s.id)}">${ic('plus')} New page</button><button class="btn ghost" data-act="rename-section" data-sec="${esc(s.id)}">${ic('edit')} Rename</button></div></div>
    ${ps.length ? `<div class="cards">${ps.map(pageCard).join('')}</div>` : `<div class="empty"><div class="empty-ic" style="color:${s.color}">${ic(s.icon)}</div><h2>Nothing in ${esc(s.name)} yet</h2><p class="muted">Pages can hold notes, tables, checklists, pictures, PDFs and diagrams.</p><button class="btn primary" data-act="new-page" data-sec="${esc(s.id)}">${ic('plus')} Create the first page</button></div>`}</div>`;
}

/* ---------- page view / edit */
const TB = [
  ['bold', '<b>B</b>', 'Bold (Ctrl+B)'], ['italic', '<i>I</i>', 'Italic (Ctrl+I)'], ['underline', '<u>U</u>', 'Underline (Ctrl+U)'], ['strikeThrough', '<s>S</s>', 'Strikethrough'],
  ['code', ic('code'), 'Inline code'], ['link', ic('link'), 'Web link'], '|',
  ['ul', ic('list'), 'Bullet list'], ['ol', ic('olist'), 'Numbered list'], ['check', ic('checklist'), 'Checklist'], ['quote', ic('quote'), 'Quote'],
  ['pre', '<span class="mono">{ }</span>', 'Code block'], ['callout', ic('callout'), 'Callout box (click its icon to change colour)'], ['table', ic('table'), 'Table'], ['hr', ic('hr'), 'Divider'], '|',
  ['image', ic('image'), 'Insert picture'], ['attach', ic('clip'), 'Attach PDF or file'], ['diagram', ic('diagram'), 'Insert diagram'], ['pagelink', ic('doc'), 'Link to another page  ( [[ )'], '|',
  ['undo', ic('undo'), 'Undo (Ctrl+Z)'], ['redo', ic('redo'), 'Redo (Ctrl+Y)'], ['clear', '<span class="mono">T<sub>x</sub></span>', 'Clear formatting'],
];
function toolbarHTML() {
  return `<div class="toolbar" id="toolbar"><select id="tbBlock" title="Text style"><option value="p">Normal text</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option></select>` +
    TB.map(t => t === '|' ? '<span class="tb-sep"></span>' : `<button data-cmd="${t[0]}" title="${esc(t[2])}">${t[1]}</button>`).join('') +
    `<span class="tb-table" id="tbTable" hidden><span class="tb-sep"></span><button data-cmd="row+" title="Add row below">+ Row</button><button data-cmd="col+" title="Add column to the right">+ Col</button><button data-cmd="row-" title="Delete row">− Row</button><button data-cmd="col-" title="Delete column">− Col</button></span></div>`;
}
function renderPage() {
  const p = S.page, m = $('#main');
  if (!p) { m.innerHTML = `<div class="wrap"><div class="empty"><h2>Page not found</h2><p class="muted">It may have been deleted. Deleted pages are kept in the data/trash folder.</p><a class="btn" href="#/">Go home</a></div></div>`; return; }
  document.title = p.title + ' · Blueprint';
  const s = sectionById(secOf(p)), ed = S.editing;
  m.innerHTML = `<div class="page ${ed ? 'editing' : ''}">
    <div class="page-top">
      <div class="crumbs">${s ? `<a href="#/s/${esc(s.id)}" style="color:${s.color}">${ic(s.icon)} ${esc(s.name)}</a>` : ''}<span class="savestate" id="saveState"></span></div>
      <div class="page-btns">
        ${ed ? `<button class="btn primary" data-act="done">${ic('check')} Done</button>` : `<button class="btn primary" data-act="edit">${ic('edit')} Edit</button>`}
        <button class="btn ghost icon ${p.pinned ? 'on' : ''}" data-act="pin" title="${p.pinned ? 'Unpin' : 'Pin to top'}">${ic('star')}</button>
        <button class="btn ghost icon" data-act="history" title="Version history">${ic('history')}</button>
        <button class="btn ghost icon" data-act="print" title="Print or save as PDF">${ic('print')}</button>
        <button class="btn ghost icon danger" data-act="delete-page" title="Delete page">${ic('trash')}</button>
      </div>
    </div>
    <div class="page-head">
      ${ed ? `<input class="title-input" id="pTitle" value="${esc(p.title)}" placeholder="Page title">` : `<h1 class="page-title">${esc(p.title)}</h1>`}
      <div class="page-meta">${ed
        ? `<label>${ic('folder')}<select id="pSec">${S.sections.map(x => `<option value="${esc(x.id)}" ${x.id === secOf(p) ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label><label class="grow">${ic('tag')}<input id="pTags" value="${esc((p.tags || []).join(', '))}" placeholder="tags, separated, by commas"></label>`
        : `${(p.tags || []).map(t => `<button class="chip" data-tag="${esc(t)}">#${esc(t)}</button>`).join('')}<span class="muted small">Updated ${fmtDate(p.updated)}</span>`}</div>
    </div>
    ${ed ? toolbarHTML() : ''}
    <div class="page-body"><article id="content" class="content" ${ed ? 'contenteditable="true" spellcheck="true"' : ''}>${sanitize(p.html || '')}</article><nav class="toc" id="toc"></nav></div>
  </div>`;
  const c = $('#content');
  if (ed && !c.innerHTML.trim()) c.innerHTML = '<p><br></p>';
  if (!ed && !c.textContent.trim() && !c.querySelector('.bp-embed,figure,table,hr')) c.innerHTML = `<p class="muted">This page is empty. Press <b>Edit</b> (or Ctrl+E) to start writing.</p>`;
  hydrate(c, ed);
  buildTOC();
  if (ed) setupEditor();
}
function buildTOC() {
  const toc = $('#toc'), c = $('#content');
  if (!toc || !c) return;
  const hs = $$('h1,h2,h3', c);
  if (S.editing || hs.length < 3) { toc.innerHTML = ''; return; }
  toc.innerHTML = '<div class="toc-h">On this page</div>' + hs.map((h, i) => { h.dataset.toc = i; return `<a class="l${h.tagName[1]}" data-toc-go="${i}">${esc(h.textContent)}</a>`; }).join('');
}

/* sanitize + embeds */
const ALLOWED = new Set('P BR B STRONG I EM U S STRIKE DEL CODE PRE A UL OL LI H1 H2 H3 H4 H5 H6 BLOCKQUOTE TABLE THEAD TBODY TFOOT TR TH TD HR FIGURE FIGCAPTION IMG DIV SPAN SUB SUP MARK KBD SMALL'.split(' '));
const DROP = new Set('SCRIPT STYLE META LINK TITLE IFRAME OBJECT EMBED NOSCRIPT SVG BUTTON INPUT SELECT TEXTAREA FORM TEMPLATE'.split(' '));
function sanitize(html, pasted) {
  const doc = new DOMParser().parseFromString('<body>' + html + '</body>', 'text/html');
  const walk = node => {
    [...node.children].forEach(el => {
      if (DROP.has(el.tagName)) { el.remove(); return; }
      walk(el);
      if (!ALLOWED.has(el.tagName) || (pasted && el.tagName === 'SPAN')) { el.replaceWith(...el.childNodes); return; }
      [...el.attributes].forEach(a => {
        const n = a.name.toLowerCase();
        const ok = n === 'href' || n === 'src' || n === 'alt' || n === 'colspan' || n === 'rowspan' || (!pasted && (n === 'class' || n.startsWith('data-')));
        if (!ok || ((n === 'href' || n === 'src') && /^\s*(javascript|vbscript|data:text)/i.test(a.value))) el.removeAttribute(a.name);
      });
      if (pasted && el.tagName === 'DIV') { const p = document.createElement('p'); p.append(...el.childNodes); el.replaceWith(p); }
    });
  };
  walk(doc.body);
  return doc.body.innerHTML;
}
const dgCache = new Map();
BP.diagramCache = dgCache;
function getDiagram(id) {
  if (!dgCache.has(id)) dgCache.set(id, api('GET', '/api/diagram/' + encodeURIComponent(id)).catch(e => { dgCache.delete(id); throw e; }));
  return dgCache.get(id);
}
function hydrate(root, editing) {
  $$('figure.bp-fig', root).forEach(f => {
    f.contentEditable = 'false';
    let cap = f.querySelector('figcaption');
    if (!cap) { cap = document.createElement('figcaption'); f.appendChild(cap); }
    cap.contentEditable = editing ? 'true' : 'false';
  });
  $$('.bp-embed', root).forEach(el => { el.contentEditable = 'false'; if (el.dataset.ready !== String(editing)) renderEmbed(el, editing); });
  $$('a[href]', root).forEach(a => { if (!a.getAttribute('href').startsWith('#')) { a.target = '_blank'; a.rel = 'noopener'; } });
}
async function renderEmbed(el, editing) {
  el.dataset.ready = String(editing);
  const t = el.dataset.type;
  const rm = editing ? `<button class="emb-btn" data-emb="remove" title="Remove from page">${ic('close')}</button>` : '';
  if (t === 'pdf') {
    const src = el.dataset.src || '', name = el.dataset.name || decodeURIComponent(src.split('/').pop());
    const open = el.dataset.open !== 'false';
    el.innerHTML = `<div class="emb-head"><span class="emb-badge pdf">PDF</span><b>${esc(name)}</b><span class="sp"></span><button class="emb-btn" data-emb="toggle">${open ? 'Hide' : 'Show'}</button>${open ? `<button class="emb-btn" data-emb="taller" title="Make the viewer taller">↕</button>` : ''}<a class="emb-btn" href="${esc(src)}" target="_blank">${ic('external')} Open</a><a class="emb-btn" href="${esc(src)}?download=1">${ic('download')}</a>${rm}</div>${open ? `<iframe src="${esc(src)}#view=FitH" style="height:${parseInt(el.dataset.h, 10) || 700}px" loading="lazy" title="${esc(name)}"></iframe>` : ''}`;
  } else if (t === 'file') {
    const src = el.dataset.src || '', name = el.dataset.name || decodeURIComponent(src.split('/').pop());
    el.innerHTML = `<div class="emb-head"><span class="emb-badge">${esc(ext(name))}</span><b>${esc(name)}</b><span class="muted small">${el.dataset.size ? fmtSize(+el.dataset.size) : ''}</span><span class="sp"></span><a class="emb-btn" href="${esc(src)}" target="_blank">${ic('external')} Open</a><a class="emb-btn" href="${esc(src)}?download=1">${ic('download')} Download</a>${rm}</div>`;
  } else if (t === 'diagram') {
    el.innerHTML = `<div class="emb-head"><span class="emb-badge dg">${ic('diagram')}</span><b class="muted">Loading diagram…</b></div>`;
    let d;
    try { d = await getDiagram(el.dataset.id); } catch (e) {
      el.innerHTML = `<div class="emb-head"><span class="emb-badge dg">${ic('diagram')}</span><b class="muted">Diagram not found</b><span class="sp"></span>${rm}</div>`; return;
    }
    el.innerHTML = `<div class="emb-head"><span class="emb-badge dg">${ic('diagram')}</span><b>${esc(d.title || 'Untitled diagram')}</b><span class="sp"></span><button class="emb-btn" data-emb="edit-dg">${ic('edit')} Edit diagram</button><button class="emb-btn" data-emb="png" title="Download as PNG">${ic('download')} PNG</button>${rm}</div><div class="emb-dg" data-emb="zoom" title="Click to enlarge">${(d.nodes || []).length ? BP.diagramSVG(d, { pad: 24 }) : '<div class="emb-empty">Empty diagram. Press <b>Edit diagram</b> to draw it.</div>'}</div>`;
  }
}
function refreshDiagramEmbeds(id) {
  dgCache.delete(id);
  $$(`.bp-embed[data-type="diagram"][data-id="${CSS.escape(id)}"]`).forEach(el => { el.dataset.ready = ''; renderEmbed(el, S.editing); });
}
function serialize() {
  const c = $('#content').cloneNode(true);
  $$('.bp-embed', c).forEach(el => { el.innerHTML = ''; el.removeAttribute('data-ready'); });
  $$('[contenteditable]', c).forEach(el => el.removeAttribute('contenteditable'));
  $$('figure.bp-fig figcaption', c).forEach(fc => { if (!fc.textContent.trim()) fc.remove(); });
  $$('.sel', c).forEach(el => el.classList.remove('sel'));
  $$('[data-toc]', c).forEach(el => el.removeAttribute('data-toc'));
  $$('a[target]', c).forEach(a => { a.removeAttribute('target'); a.removeAttribute('rel'); });
  $$('[class=""]', c).forEach(el => el.removeAttribute('class'));
  return c.innerHTML;
}

/* saving */
function setSave(t) { const s = $('#saveState'); if (s) s.textContent = t; }
function markDirty() { S.dirty = true; setSave('Unsaved'); clearTimeout(S.saveT); S.saveT = setTimeout(savePage, 1200); }
async function savePage(extra) {
  clearTimeout(S.saveT);
  const p = S.page, c = $('#content');
  if (!p || !c) { S.dirty = false; return; }
  const t = $('#pTitle'), tg = $('#pTags'), sc = $('#pSec');
  if (t) p.title = t.value.trim() || 'Untitled';
  if (tg) p.tags = tg.value.split(',').map(x => x.trim().replace(/^#/, '')).filter(Boolean);
  if (sc) p.section = sc.value;
  if (S.editing || c.dataset.touched) p.html = serialize();
  S.dirty = false;
  setSave('Saving…');
  try {
    const r = await api('PUT', '/api/page/' + p.id, Object.assign({}, p, extra || {}));
    p.updated = r.updated;
    const i = S.pages.findIndex(x => x.id === r.id);
    if (i >= 0) S.pages[i] = r; else S.pages.push(r);
    setSave('Saved ✓');
    renderRail(); if (!S.search) renderList();
  } catch (e) { S.dirty = true; setSave('Save failed'); toast('Could not save: ' + e.message, 'err'); }
}
async function setEditing(on) {
  if (!S.page) return;
  if (!on && S.dirty) await savePage();
  S.editing = on;
  renderPage();
  if (on) setTimeout(focusEnd, 20);
}

/* editor */
function focusEnd() {
  const c = $('#content');
  if (!c || !S.editing) return;
  c.focus();
  const r = document.createRange(); r.selectNodeContents(c); r.collapse(false);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  S.range = r.cloneRange();
}
function saveRange() {
  const s = getSelection(), c = $('#content');
  if (s.rangeCount && c && c.contains(s.getRangeAt(0).startContainer)) S.range = s.getRangeAt(0).cloneRange();
}
function restoreRange() {
  const c = $('#content');
  if (!c) return false;
  c.focus();
  const s = getSelection();
  if (S.range && c.contains(S.range.startContainer)) { s.removeAllRanges(); s.addRange(S.range); }
  else { const r = document.createRange(); r.selectNodeContents(c); r.collapse(false); s.removeAllRanges(); s.addRange(r); }
  return true;
}
function closestSel(sel) {
  const s = getSelection();
  if (!s.rangeCount) return null;
  let n = s.getRangeAt(0).startContainer;
  if (n.nodeType === 3) n = n.parentNode;
  const c = $('#content');
  const el = n && n.closest ? n.closest(sel) : null;
  return el && c && c.contains(el) ? el : null;
}
function insertHTML(html) {
  restoreRange();
  document.execCommand('insertHTML', false, html);
  hydrate($('#content'), true);
  saveRange();
  markDirty();
}
function setupEditor() {
  const c = $('#content');
  try { document.execCommand('defaultParagraphSeparator', false, 'p'); } catch (e) { /* old browser */ }
  c.addEventListener('input', () => { markDirty(); wikiTrigger(); });
  c.addEventListener('keyup', saveRange);
  c.addEventListener('mouseup', saveRange);
  c.addEventListener('paste', onPaste);
  c.addEventListener('dragover', e => { if ([...(e.dataTransfer.types || [])].includes('Files')) { e.preventDefault(); c.classList.add('drop'); } });
  c.addEventListener('dragleave', () => c.classList.remove('drop'));
  c.addEventListener('drop', onDrop);
  c.addEventListener('keydown', onEditorKey);
  ['pTitle', 'pTags'].forEach(id => $('#' + id).addEventListener('input', markDirty));
  $('#pSec').addEventListener('change', markDirty);
  $('#pTitle').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); focusEnd(); } });
  const tb = $('#toolbar');
  tb.addEventListener('mousedown', e => { if (e.target.closest('button')) e.preventDefault(); });
  tb.addEventListener('click', e => { const b = e.target.closest('[data-cmd]'); if (b) exec(b.dataset.cmd); });
  $('#tbBlock').addEventListener('change', e => { restoreRange(); document.execCommand('formatBlock', false, '<' + e.target.value + '>'); markDirty(); saveRange(); });
}
document.addEventListener('selectionchange', () => {
  if (!S.editing) return;
  const c = $('#content');
  const s = getSelection();
  if (!c || !s.rangeCount || !c.contains(s.getRangeAt(0).startContainer)) return;
  saveRange();
  const blk = closestSel('h1,h2,h3,p,li,pre,blockquote,td,th');
  const sel = $('#tbBlock');
  if (sel && blk) sel.value = /^H[1-3]$/.test(blk.tagName) ? blk.tagName.toLowerCase() : 'p';
  const tt = $('#tbTable');
  if (tt) tt.hidden = !closestSel('table');
});
function onEditorKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    const li = closestSel('ul.checklist > li');
    if (li) setTimeout(() => { const n = closestSel('ul.checklist > li'); if (n && n !== li) n.removeAttribute('data-done'); }, 0);
  }
  if (e.key === 'Tab') {
    if (closestSel('li')) { e.preventDefault(); document.execCommand(e.shiftKey ? 'outdent' : 'indent'); markDirty(); }
    else if (closestSel('pre')) { e.preventDefault(); document.execCommand('insertText', false, '  '); }
  }
}
function wikiTrigger() {
  const s = getSelection();
  if (!s.rangeCount) return;
  const r = s.getRangeAt(0), n = r.startContainer, o = r.startOffset;
  if (n.nodeType !== 3 || o < 2 || n.data.slice(o - 2, o) !== '[[') return;
  const del = document.createRange(); del.setStart(n, o - 2); del.setEnd(n, o); del.deleteContents();
  saveRange();
  pagePicker().then(p => { if (p) insertHTML(`<a href="#/p/${esc(p.id)}" class="wikilink">${esc(p.title)}</a>&nbsp;`); else restoreRange(); });
}
async function onPaste(e) {
  const cd = e.clipboardData;
  if (!cd) return;
  const files = [...(cd.files || [])];
  if (files.length) { e.preventDefault(); saveRange(); for (const f of files) await uploadInsert(f); return; }
  if (closestSel('pre')) { e.preventDefault(); document.execCommand('insertText', false, cd.getData('text/plain')); return; }
  const html = cd.getData('text/html');
  if (html) { e.preventDefault(); document.execCommand('insertHTML', false, sanitize(html, true)); hydrate($('#content'), true); }
}
async function onDrop(e) {
  const files = [...(e.dataTransfer.files || [])];
  const c = $('#content');
  c.classList.remove('drop');
  if (!files.length) return;
  e.preventDefault();
  const r = document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null;
  if (r && c.contains(r.startContainer)) { S.range = r; }
  for (const f of files) await uploadInsert(f);
}
async function upload(f) {
  const info = await api('POST', '/api/upload', f, { 'X-Filename': encodeURIComponent(f.name || 'pasted.png') });
  S.files.push(info);
  renderRail();
  return info;
}
function embedFor(info) {
  const k = fileKind(info);
  if (k === 'image') return `<figure class="bp-fig" data-w="100"><img src="${esc(info.url)}" alt="${esc(info.name)}"><figcaption></figcaption></figure><p><br></p>`;
  if (k === 'pdf') return `<div class="bp-embed" data-type="pdf" data-src="${esc(info.url)}" data-name="${esc(info.name)}"></div><p><br></p>`;
  return `<div class="bp-embed" data-type="file" data-src="${esc(info.url)}" data-name="${esc(info.name)}" data-size="${info.size}"></div><p><br></p>`;
}
async function uploadInsert(f) {
  setSave('Uploading ' + (f.name || 'file') + '…');
  try { const info = await upload(f); insertHTML(embedFor(info)); }
  catch (e) { toast('Upload failed: ' + e.message, 'err'); setSave(''); }
}
function pickFiles(accept, multiple) {
  return new Promise(resolve => {
    const inp = $('#filePick');
    inp.accept = accept || ''; inp.multiple = !!multiple; inp.value = '';
    inp.onchange = () => resolve([...inp.files]);
    inp.click();
  });
}
const tableHTML = (r, c) => `<table><thead><tr>${Array.from({ length: c }, (_, i) => `<th>Column ${i + 1}</th>`).join('')}</tr></thead><tbody>${Array.from({ length: r - 1 }, () => `<tr>${'<td><br></td>'.repeat(c)}</tr>`).join('')}</tbody></table><p><br></p>`;
function tableOp(op) {
  const cell = closestSel('td,th');
  if (!cell) return;
  const tr = cell.parentNode, table = cell.closest('table'), idx = [...tr.children].indexOf(cell);
  if (op === 'row+') { const n = document.createElement('tr'); n.innerHTML = '<td><br></td>'.repeat(tr.children.length); if (tr.parentNode.tagName === 'THEAD') { const tb = table.tBodies[0] || table.createTBody(); tb.insertBefore(n, tb.firstChild); } else tr.after(n); }
  if (op === 'row-') { if (tr.parentNode.tagName !== 'THEAD') tr.remove(); }
  if (op === 'col+') $$('tr', table).forEach(row => { const ref = row.children[idx]; const n = document.createElement(ref && ref.tagName === 'TH' ? 'th' : 'td'); n.innerHTML = ref && ref.tagName === 'TH' ? 'Column' : '<br>'; if (ref) ref.after(n); else row.appendChild(n); });
  if (op === 'col-') { if (tr.children.length > 1) $$('tr', table).forEach(row => { if (row.children[idx]) row.children[idx].remove(); }); }
  markDirty();
}
async function exec(cmd) {
  restoreRange();
  const selText = esc(getSelection().toString());
  switch (cmd) {
    case 'bold': case 'italic': case 'underline': case 'strikeThrough': case 'undo': case 'redo': document.execCommand(cmd); break;
    case 'clear': document.execCommand('removeFormat'); document.execCommand('formatBlock', false, '<p>'); break;
    case 'ul': document.execCommand('insertUnorderedList'); break;
    case 'ol': document.execCommand('insertOrderedList'); break;
    case 'check': {
      const ul = closestSel('ul');
      if (ul && ul.classList.contains('checklist')) ul.classList.remove('checklist');
      else { if (!ul) document.execCommand('insertUnorderedList'); const u = closestSel('ul'); if (u) u.classList.add('checklist'); }
      break;
    }
    case 'quote': document.execCommand('formatBlock', false, closestSel('blockquote') ? '<p>' : '<blockquote>'); break;
    case 'pre': document.execCommand('formatBlock', false, closestSel('pre') ? '<p>' : '<pre>'); break;
    case 'code': document.execCommand('insertHTML', false, `<code>${selText || 'code'}</code>&#8203;`); break;
    case 'callout': insertHTML(`<div class="callout info"><p>${selText || 'Note: '}</p></div><p><br></p>`); return;
    case 'table': insertHTML(tableHTML(3, 3)); return;
    case 'hr': insertHTML('<hr><p><br></p>'); return;
    case 'row+': case 'row-': case 'col+': case 'col-': tableOp(cmd); return;
    case 'link': {
      saveRange();
      const v = await ask('Add a web link', [{ name: 'url', label: 'Address', placeholder: 'https://…' }, ...(selText ? [] : [{ name: 'text', label: 'Text to show', placeholder: 'optional' }])], 'Add link');
      if (!v || !v.url) { restoreRange(); return; }
      const url = /^[a-z]+:|^#|^\//i.test(v.url) ? v.url : 'https://' + v.url;
      restoreRange();
      if (selText) document.execCommand('createLink', false, url);
      else document.execCommand('insertHTML', false, `<a href="${esc(url)}">${esc(v.text || url)}</a>&nbsp;`);
      hydrate($('#content'), true);
      break;
    }
    case 'image': { saveRange(); const fs = await pickFiles('image/*', true); for (const f of fs) await uploadInsert(f); return; }
    case 'attach': { saveRange(); const fs = await pickFiles('', true); for (const f of fs) await uploadInsert(f); return; }
    case 'diagram': saveRange(); diagramDialog(); return;
    case 'pagelink': { saveRange(); const p = await pagePicker(); if (p) insertHTML(`<a href="#/p/${esc(p.id)}" class="wikilink">${esc(p.title)}</a>&nbsp;`); return; }
  }
  saveRange();
  markDirty();
}
function diagramDialog() {
  const list = S.diagrams.slice().sort((a, b) => (b.updated || 0) - (a.updated || 0));
  const body = `<div class="pick-actions"><button class="btn primary" data-pick="new">${ic('plus')} New blank diagram</button><button class="btn accent" data-pick="gen">${ic('bolt')} Generate from text</button></div>
    <div class="pick-label">Or show an existing diagram on this page</div>
    <div class="pick-list">${list.map(d => `<button class="pick" data-pick="${esc(d.id)}">${ic('diagram')}<span>${esc(d.title)}</span><em>${d.count} shapes</em></button>`).join('') || '<p class="muted">No diagrams yet.</p>'}</div>`;
  const m = modal({ title: 'Insert a diagram', body });
  m.el.addEventListener('click', e => {
    const b = e.target.closest('[data-pick]');
    if (!b) return;
    m.close();
    const v = b.dataset.pick;
    const embed = id => insertHTML(`<div class="bp-embed" data-type="diagram" data-id="${esc(id)}"></div><p><br></p>`);
    if (v === 'new' || v === 'gen') {
      BP.openDiagram(null, { title: (S.page ? S.page.title + ' diagram' : 'Untitled diagram'), generate: v === 'gen', onClose: d => { if (d) embed(d.id); } });
    } else embed(v);
  });
}
function pagePicker() {
  return new Promise(resolve => {
    let done = false;
    const m = modal({
      title: 'Link to a page',
      body: `<input class="pp-q" id="ppQ" placeholder="Type to find a page, or a new title…"><div class="pick-list" id="ppList"></div>`,
      onClose: () => { if (!done) resolve(null); },
    });
    const q = m.el.querySelector('#ppQ'), list = m.el.querySelector('#ppList');
    const draw = () => {
      const v = q.value.trim().toLowerCase();
      const ps = sortPages(S.pages).filter(p => !S.page || p.id !== S.page.id).filter(p => !v || (p.title || '').toLowerCase().includes(v)).slice(0, 30);
      list.innerHTML = ps.map(p => `<button class="pick" data-id="${esc(p.id)}">${ic('doc')}<span>${esc(p.title)}</span><em>${esc((sectionById(secOf(p)) || {}).name || '')}</em></button>`).join('') +
        (v ? `<button class="pick new" data-new="1">${ic('plus')}<span>Create new page “${esc(q.value.trim())}”</span></button>` : '');
    };
    draw();
    q.focus();
    q.addEventListener('input', draw);
    q.addEventListener('keydown', e => { if (e.key === 'Enter') { const b = list.querySelector('.pick'); if (b) b.click(); } });
    list.addEventListener('click', async e => {
      const b = e.target.closest('.pick');
      if (!b) return;
      done = true;
      if (b.dataset.new) {
        const p = await createPage(q.value.trim(), S.page ? secOf(S.page) : (S.sections[0] || {}).id, '<p><br></p>');
        m.close(); resolve(p);
      } else { m.close(); resolve(S.pages.find(p => p.id === b.dataset.id)); }
    });
  });
}

/* ---------- page templates */
const TEMPLATES = {
  blank: { name: 'Blank', desc: 'Start from nothing', icon: 'doc', html: '<p><br></p>' },
  project: { name: 'Project', desc: 'Goals, parts list, timeline', icon: 'folder', html:
`<h2>Overview</h2><p>What is this project and why are you doing it?</p>
<h2>Goals</h2><ul class="checklist"><li>First goal</li><li>Second goal</li></ul>
<h2>Parts &amp; materials</h2><table><thead><tr><th>Item</th><th>Qty</th><th>Where from</th><th>Cost</th></tr></thead><tbody><tr><td><br></td><td><br></td><td><br></td><td><br></td></tr><tr><td><br></td><td><br></td><td><br></td><td><br></td></tr></tbody></table>
<h2>Timeline</h2><table><thead><tr><th>Date</th><th>Milestone</th><th>Status</th></tr></thead><tbody><tr><td><br></td><td><br></td><td><br></td></tr></tbody></table>
<h2>Notes</h2><p><br></p>` },
  device: { name: 'Device record', desc: 'Hostname, IP, model, changelog', icon: 'cpu', html:
`<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody><tr><td>Hostname</td><td><br></td></tr><tr><td>IP address</td><td><br></td></tr><tr><td>MAC address</td><td><br></td></tr><tr><td>Make / model</td><td><br></td></tr><tr><td>Operating system</td><td><br></td></tr><tr><td>Location</td><td><br></td></tr><tr><td>Purchased / warranty</td><td><br></td></tr></tbody></table>
<div class="callout warn"><p>Keep passwords in a password manager, not on wiki pages.</p></div>
<h2>Connections</h2><p>Insert a diagram here with the diagram button.</p>
<h2>Configuration notes</h2><pre>paste config or commands here</pre>
<h2>Change log</h2><table><thead><tr><th>Date</th><th>Change</th><th>By</th></tr></thead><tbody><tr><td><br></td><td><br></td><td><br></td></tr></tbody></table>` },
  network: { name: 'Network overview', desc: 'Topology, subnets, devices, services', icon: 'globe', html:
`<h2>Topology</h2><div class="callout tip"><p>Use the diagram button, then <b>Generate from text</b> with a list like <code>Router -&gt; Switch -&gt; PC</code>.</p></div>
<h2>Subnets &amp; VLANs</h2><table><thead><tr><th>VLAN</th><th>Subnet</th><th>Gateway</th><th>Purpose</th></tr></thead><tbody><tr><td>1</td><td>192.168.1.0/24</td><td>192.168.1.1</td><td>Main LAN</td></tr></tbody></table>
<h2>Devices</h2><table><thead><tr><th>Name</th><th>IP</th><th>Type</th><th>Notes</th></tr></thead><tbody><tr><td><br></td><td><br></td><td><br></td><td><br></td></tr></tbody></table>
<h2>Services &amp; ports</h2><table><thead><tr><th>Service</th><th>Host</th><th>Port</th><th>URL</th></tr></thead><tbody><tr><td><br></td><td><br></td><td><br></td><td><br></td></tr></tbody></table>` },
  sop: { name: 'Procedure (how-to)', desc: 'Step by step instructions', icon: 'gear', html:
`<h2>Purpose</h2><p>What this procedure is for and when to use it.</p>
<h2>Before you start</h2><ul class="checklist"><li>Tools / access needed</li><li>Backup taken</li></ul>
<h2>Steps</h2><ol><li>First step</li><li>Second step</li><li>Third step</li></ol>
<h2>Check it worked</h2><p><br></p>
<div class="callout danger"><p><b>If it goes wrong:</b> how to undo or roll back.</p></div>` },
  prototype: { name: 'Prototype log', desc: 'Idea, build notes, test results', icon: 'flask', html:
`<h2>Idea</h2><p>What you're trying to prove or build.</p>
<h2>Build notes</h2><p>Wiring, code, parts, photos (drag pictures in).</p>
<h2>Test results</h2><table><thead><tr><th>Date</th><th>Test</th><th>Result</th><th>Notes</th></tr></thead><tbody><tr><td><br></td><td><br></td><td><br></td><td><br></td></tr></tbody></table>
<h2>Next version</h2><ul class="checklist"><li>Change to try next</li></ul>` },
  trouble: { name: 'Troubleshooting', desc: 'Symptom, steps tried, fix', icon: 'wrench', html:
`<h2>Symptom</h2><p>What's happening?</p>
<h2>Setup</h2><p>Device, versions, what changed recently.</p>
<h2>Things tried</h2><ul class="checklist"><li>Turned it off and on again</li></ul>
<h2>Cause</h2><p><br></p>
<h2>Fix</h2><div class="callout tip"><p>What finally fixed it.</p></div>` },
  meeting: { name: 'Notes', desc: 'Date, people, notes, actions', icon: 'book', html:
`<p><b>Date:</b> <br><b>With:</b> </p><h2>Notes</h2><ul><li><br></li></ul><h2>Action items</h2><ul class="checklist"><li><br></li></ul>` },
};
async function createPage(title, section, html) {
  const id = BP.uid('p');
  const r = await api('PUT', '/api/page/' + id, { title: title || 'Untitled', section, tags: [], html: html || '<p><br></p>' });
  S.pages.push(r);
  renderRail();
  return r;
}
function newPageDialog(sectionId) {
  const sec = sectionId || currentSection() || (S.sections[0] || {}).id;
  let tpl = 'blank';
  const body = `<label class="field">Title<input id="npTitle" placeholder="e.g. Garage Pi weather station"></label>
    <label class="field">Section<select id="npSec">${S.sections.map(s => `<option value="${esc(s.id)}" ${s.id === sec ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
    <div class="pick-label">Template</div>
    <div class="tpl-grid">${Object.keys(TEMPLATES).map(k => `<button class="tpl ${k === tpl ? 'on' : ''}" data-tpl="${k}">${ic(TEMPLATES[k].icon)}<b>${TEMPLATES[k].name}</b><span>${TEMPLATES[k].desc}</span></button>`).join('')}</div>`;
  const make = async () => {
    const title = m.el.querySelector('#npTitle').value.trim() || (tpl === 'blank' ? 'Untitled' : TEMPLATES[tpl].name);
    try {
      const p = await createPage(title, m.el.querySelector('#npSec').value, TEMPLATES[tpl].html);
      S.pendingEdit = p.id;
      go('#/p/' + p.id);
    } catch (e) { toast('Could not create page: ' + e.message, 'err'); return false; }
    return true;
  };
  const m = modal({ title: 'New page', body, wide: true, buttons: [{ label: 'Cancel' }, { label: 'Create page', primary: true, onClick: make }] });
  m.el.querySelector('#npTitle').focus();
  m.el.querySelector('#npTitle').addEventListener('keydown', async e => { if (e.key === 'Enter') { e.preventDefault(); if (await make()) m.close(); } });
  m.el.querySelector('.tpl-grid').addEventListener('click', e => {
    const b = e.target.closest('[data-tpl]');
    if (!b) return;
    tpl = b.dataset.tpl;
    $$('.tpl', m.el).forEach(x => x.classList.toggle('on', x === b));
  });
}

/* history */
async function showHistory() {
  if (S.dirty) await savePage();
  const p = S.page;
  let list = [];
  try { list = await api('GET', '/api/history/' + p.id); } catch (e) { /* none */ }
  if (!list.length) {
    modal({ title: 'Version history', body: `<p>No earlier versions yet.</p><p class="muted">Blueprint keeps a copy of the page each time you come back to edit it after a break, so you can always go back.</p>` });
    return;
  }
  let chosen = null;
  const m = modal({
    title: 'Version history · ' + p.title, wide: true,
    body: `<div class="hist"><div class="hist-list">${list.map(v => `<button class="pick" data-ts="${v.ts}">${ic('history')}<span>${new Date(+v.ts).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</span><em>${esc(v.title || '')}</em></button>`).join('')}</div><div class="hist-view"><article class="content" id="histView"><p class="muted">Pick a version on the left to preview it.</p></article></div></div>`,
    buttons: [{ label: 'Close' }, { label: 'Restore this version', primary: true, onClick: async () => {
      if (!chosen) { toast('Pick a version first'); return false; }
      await api('PUT', '/api/page/' + p.id, Object.assign({}, chosen, { id: p.id, snapshot: true }));
      await refreshState();
      S.page = await api('GET', '/api/page/' + p.id);
      S.editing = false; renderAll();
      toast('Version restored. The version you replaced is kept in history too.');
      return true;
    } }],
  });
  m.el.querySelector('.hist-list').addEventListener('click', async e => {
    const b = e.target.closest('[data-ts]');
    if (!b) return;
    $$('.hist-list .pick', m.el).forEach(x => x.classList.toggle('on', x === b));
    chosen = await api('GET', '/api/history/' + p.id + '/' + b.dataset.ts);
    const v = m.el.querySelector('#histView');
    v.innerHTML = `<h1>${esc(chosen.title)}</h1>` + sanitize(chosen.html || '');
    hydrate(v, false);
  });
}

/* ---------- diagrams view */
function renderDiagrams() {
  $('#main').innerHTML = `<div class="wrap"><div class="hero"><div><h1>Diagrams</h1><p class="muted">Network maps, wiring, flowcharts and process diagrams. Drop them into any page.</p></div>
    <div class="hero-actions"><button class="btn primary" data-act="new-diagram">${ic('plus')} New diagram</button><button class="btn accent" data-act="gen-diagram">${ic('bolt')} Generate from text</button></div></div>
    <div class="cards dgs" id="allDg"><p class="muted">Loading…</p></div></div>`;
  diagramCards('#allDg');
}

/* ---------- files view */
function renderFiles() {
  const f = S.fileFilter;
  const list = S.files.filter(x => f === 'all' || fileKind(x) === f).sort((a, b) => b.mtime - a.mtime);
  const counts = { all: S.files.length, image: S.files.filter(x => fileKind(x) === 'image').length, pdf: S.files.filter(x => fileKind(x) === 'pdf').length, other: S.files.filter(x => fileKind(x) === 'other').length };
  $('#main').innerHTML = `<div class="wrap"><div class="hero"><div><h1>Files & PDFs</h1><p class="muted">Everything you have uploaded. Drop files anywhere on this screen to add them.</p></div>
    <div class="hero-actions"><button class="btn primary" data-act="upload">${ic('upload')} Upload files</button></div></div>
    <div class="seg">${[['all', 'All'], ['image', 'Pictures'], ['pdf', 'PDFs'], ['other', 'Other']].map(([k, l]) => `<button class="${k === f ? 'on' : ''}" data-ff="${k}">${l} <em>${counts[k]}</em></button>`).join('')}</div>
    <div class="file-grid" id="fileGrid">${list.map(x => {
      const k = fileKind(x);
      return `<div class="fcard" data-file="${esc(x.name)}"><div class="fthumb ${k}">${k === 'image' ? `<img src="${esc(x.url)}" loading="lazy" alt="">` : `<span>${k === 'pdf' ? 'PDF' : esc(ext(x.name))}</span>`}</div><div class="fname" title="${esc(x.name)}">${esc(x.name)}</div><div class="fmeta">${fmtSize(x.size)} · ${fmtDate(x.mtime)}<span class="sp"></span><a class="btn ghost icon small" href="${esc(x.url)}?download=1" title="Download">${ic('download')}</a><button class="btn ghost icon small" data-del-file="${esc(x.name)}" title="Delete">${ic('trash')}</button></div></div>`;
    }).join('') || `<div class="empty"><div class="empty-ic">${ic('files')}</div><h2>No files here yet</h2><p class="muted">Drag pictures and PDFs onto this screen, or press Upload.</p></div>`}</div></div>`;
}
function openFile(name) {
  const f = S.files.find(x => x.name === name);
  if (!f) return;
  const k = fileKind(f);
  if (k === 'image') lightbox(`<img src="${esc(f.url)}" alt="">`);
  else if (k === 'pdf') modal({ title: f.name, wide: true, body: `<iframe class="pdf-view" src="${esc(f.url)}#view=FitH" title="${esc(f.name)}"></iframe><p class="muted small" style="margin-top:8px"><a href="${esc(f.url)}" target="_blank">Open in a new tab</a> · <a href="${esc(f.url)}?download=1">Download</a></p>` });
  else window.open(f.url + '?download=1', '_blank');
}
async function uploadMany(files) {
  if (!files.length) return;
  let ok = 0;
  for (const f of files) { try { await upload(f); ok++; } catch (e) { toast('Upload failed: ' + f.name, 'err'); } }
  if (ok) toast(`Uploaded ${ok} file${ok === 1 ? '' : 's'}`);
  renderList();
  if (S.route.name === 'files' || S.route.name === 'home') renderMain();
}

/* ---------- settings */
async function renderSettings() {
  const theme = (() => { try { return localStorage.getItem('bp-theme') || 'auto'; } catch (e) { return 'auto'; } })();
  $('#main').innerHTML = `<div class="wrap narrow"><h1>Settings</h1>
    <section class="set" id="set0"><h2>Appearance</h2><div class="seg">${[['auto', 'Match system'], ['light', 'Light'], ['dark', 'Dark']].map(([k, l]) => `<button class="${k === theme ? 'on' : ''}" data-theme-set="${k}">${l}</button>`).join('')}</div></section>
    <section class="set" id="set1"><h2>Sections (left-hand tabs)</h2><p class="muted small">Rename, recolour, reorder or remove the tabs down the left side.</p><div id="secRows"></div><button class="btn" data-act="add-section">${ic('plus')} Add section</button></section>
    <section class="set" id="set2"><h2>Backup & move</h2><p class="muted small">Download everything (pages, diagrams, pictures, PDFs) as one zip. Import that zip on another Pi or computer running Blueprint to copy it all across. Importing adds to what is there and replaces pages with the same name.</p>
      <div class="row"><a class="btn primary" href="/api/export">${ic('download')} Download backup</a><button class="btn" data-act="import">${ic('upload')} Import a backup</button></div></section>
    <section class="set" id="set3"><h2>Network access</h2><div id="netInfo" class="muted">Checking…</div></section>
    <section class="set" id="set4"><h2>Keyboard shortcuts</h2><table class="keys"><tbody>
      <tr><td><kbd>Ctrl</kbd> <kbd>K</kbd></td><td>Search</td></tr><tr><td><kbd>Ctrl</kbd> <kbd>E</kbd></td><td>Edit page / done</td></tr>
      <tr><td><kbd>Ctrl</kbd> <kbd>S</kbd></td><td>Save now</td></tr><tr><td><kbd>[[</kbd></td><td>Link to a page (while editing)</td></tr>
      <tr><td><kbd>Tab</kbd></td><td>Indent a list item</td></tr><tr><td><kbd>Ctrl</kbd> <kbd>P</kbd></td><td>Print / save page as PDF</td></tr></tbody></table></section>
  </div>`;
  renderSectionRows();
  try {
    const i = await api('GET', '/api/info');
    const el = $('#netInfo');
    if (el) el.innerHTML = `<p>Open Blueprint from any phone, tablet or computer on the same network:</p>${(i.ips.length ? i.ips : [location.hostname]).map(ip => `<p><code class="big">http://${esc(ip)}:${i.port}</code></p>`).join('')}<p class="small">Computer name: <code>${esc(i.host)}</code> (also try <code>http://${esc(i.host)}.local:${i.port}</code>)<br>Data folder: <code>${esc(i.data)}</code><br>Version ${esc(i.version)}</p>`;
  } catch (e) { /* ignore */ }
}
function renderSectionRows() {
  const box = $('#secRows');
  if (!box) return;
  box.innerHTML = S.sections.map((s, i) => `<div class="sec-row" data-i="${i}">
    <span class="sec-sw" style="color:${s.color}">${ic(s.icon)}</span>
    <input class="sec-name" value="${esc(s.name)}">
    <select class="sec-icon" title="Icon">${SECTION_ICONS.map(n => `<option value="${n}" ${n === s.icon ? 'selected' : ''}>${n}</option>`).join('')}</select>
    <input type="color" class="sec-color" value="${esc(s.color)}" title="Colour">
    <button class="btn ghost icon small" data-sec-mv="-1" title="Move up">↑</button><button class="btn ghost icon small" data-sec-mv="1" title="Move down">↓</button>
    <button class="btn ghost icon small danger" data-sec-del="1" title="Remove">${ic('trash')}</button></div>`).join('');
}
const saveSections = debounce(async () => { try { S.sections = await api('PUT', '/api/sections', S.sections); renderRail(); } catch (e) { toast('Could not save sections', 'err'); } }, 400);
async function addSection() {
  const v = await ask('New section', [{ name: 'name', label: 'Name', placeholder: 'e.g. Home Lab' }, { name: 'icon', label: 'Icon', value: 'folder', options: SECTION_ICONS.map(n => ({ value: n, label: n })) }], 'Add');
  if (!v || !v.name.trim()) return;
  let base = v.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section', id = base, n = 2;
  while (S.sections.some(s => s.id === id)) id = base + '-' + n++;
  S.sections.push({ id, name: v.name.trim(), icon: v.icon, color: SECTION_COLORS[S.sections.length % SECTION_COLORS.length] });
  S.sections = await api('PUT', '/api/sections', S.sections);
  go('#/s/' + id);
}
async function removeSection(i) {
  const s = S.sections[i];
  if (S.sections.length < 2) { toast('You need at least one section'); return; }
  const pages = S.pages.filter(p => secOf(p) === s.id);
  const target = S.sections.find(x => x.id !== s.id);
  const ok = await confirmBox('Remove section', pages.length ? `“${esc(s.name)}” has ${pages.length} page(s). They will move to “${esc(target.name)}”.` : `Remove “${esc(s.name)}”?`, 'Remove');
  if (!ok) return;
  for (const ps of pages) {
    const full = await api('GET', '/api/page/' + ps.id);
    full.section = target.id;
    await api('PUT', '/api/page/' + ps.id, full);
  }
  S.sections.splice(i, 1);
  S.sections = await api('PUT', '/api/sections', S.sections);
  await refreshState();
  renderAll();
}

/* ---------- global events */
document.addEventListener('click', async e => {
  const t = e.target;
  const jump = t.closest('[data-jump]');
  if (jump) { e.preventDefault(); const el = $('#' + jump.dataset.jump); if (el) el.scrollIntoView({ behavior: 'smooth' }); return; }
  const fileLink = t.closest('#listBody [data-file], .fcard[data-file]');
  if (fileLink && !t.closest('a.btn, [data-del-file]')) { e.preventDefault(); if (S.route.name !== 'files' && !S.search) go('#/files'); openFile(fileLink.dataset.file); return; }
  const tg = t.closest('[data-tag]');
  if (tg) { const q = $('#q'); q.value = tg.dataset.tag; S.search = tg.dataset.tag; $('#qClear').hidden = false; renderList(); return; }
  const tocGo = t.closest('[data-toc-go]');
  if (tocGo) { const h = $(`#content [data-toc="${tocGo.dataset.tocGo}"]`); if (h) h.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  const ff = t.closest('[data-ff]');
  if (ff) { S.fileFilter = ff.dataset.ff; renderFiles(); return; }
  const th = t.closest('[data-theme-set]');
  if (th) { setTheme(th.dataset.themeSet); renderSettings(); return; }
  const delDg = t.closest('[data-del-dg]');
  if (delDg) {
    e.preventDefault(); e.stopPropagation();
    if (await confirmBox('Delete diagram', 'Pages that show this diagram will say it is missing. It is moved to the data/trash folder, not destroyed.')) {
      await api('DELETE', '/api/diagram/' + delDg.dataset.delDg); await refreshState(); renderAll();
    }
    return;
  }
  const delF = t.closest('[data-del-file]');
  if (delF) {
    if (await confirmBox('Delete file', `Delete “${esc(delF.dataset.delFile)}”? Pages that use it will show a broken link. It is moved to the data/trash folder.`)) {
      await api('DELETE', '/api/file/' + encodeURIComponent(delF.dataset.delFile)); await refreshState(); renderAll();
    }
    return;
  }
  const secMv = t.closest('[data-sec-mv]'), secDel = t.closest('[data-sec-del]');
  if (secMv || secDel) {
    const i = +t.closest('.sec-row').dataset.i;
    if (secDel) { removeSection(i); return; }
    const j = i + (+secMv.dataset.secMv);
    if (j < 0 || j >= S.sections.length) return;
    [S.sections[i], S.sections[j]] = [S.sections[j], S.sections[i]];
    renderSectionRows(); saveSections(); return;
  }
  // embeds + content interactions
  const emb = t.closest('[data-emb]');
  if (emb) {
    const host = emb.closest('.bp-embed'), a = emb.dataset.emb;
    if (a === 'remove') { host.remove(); markDirty(); return; }
    if (a === 'toggle') { host.dataset.open = host.dataset.open === 'false' ? 'true' : 'false'; touchSave(host); return; }
    if (a === 'taller') { host.dataset.h = (parseInt(host.dataset.h, 10) || 700) + 300; touchSave(host); return; }
    if (a === 'edit-dg') { const id = host.dataset.id; BP.openDiagram(id, { onClose: () => refreshDiagramEmbeds(id) }); return; }
    if (a === 'png') { const d = await getDiagram(host.dataset.id); BP.diagramPNG(d, (d.title || 'diagram').replace(/[^\w.-]+/g, '-')); return; }
    if (a === 'zoom' && !S.editing) { const svg = emb.querySelector('svg'); if (svg) lightbox(`<div class="lb-svg">${svg.outerHTML}</div>`); return; }
  }
  const content = t.closest('#content');
  if (content) {
    const li = t.closest('ul.checklist > li');
    if (li && e.clientX - li.getBoundingClientRect().left < 28) {
      if (li.dataset.done === 'true') li.removeAttribute('data-done'); else li.dataset.done = 'true';
      touchSave(li); return;
    }
    const callout = t.closest('.callout');
    if (callout && S.editing && e.clientX - callout.getBoundingClientRect().left < 40) {
      const order = ['info', 'tip', 'warn', 'danger'];
      const cur = order.find(k => callout.classList.contains(k)) || 'info';
      callout.classList.remove(cur); callout.classList.add(order[(order.indexOf(cur) + 1) % order.length]);
      markDirty(); return;
    }
    const fig = t.closest('figure.bp-fig');
    if (fig && t.tagName === 'IMG') {
      if (!S.editing) { lightbox(`<img src="${esc(t.src)}" alt="">`); return; }
      showFigBar(fig); return;
    }
  }
  if (!t.closest('#figBar')) hideFigBar();
  const a = t.closest('[data-act]');
  if (!a) return;
  switch (a.dataset.act) {
    case 'add-section': addSection(); break;
    case 'rename-section': {
      const s = sectionById(a.dataset.sec);
      const v = await ask('Rename section', [{ name: 'name', label: 'Name', value: s.name }], 'Save');
      if (v && v.name.trim()) { s.name = v.name.trim(); S.sections = await api('PUT', '/api/sections', S.sections); renderAll(); }
      break;
    }
    case 'new-page': newPageDialog(a.dataset.sec); break;
    case 'new-diagram': BP.openDiagram(null, { onClose: () => { refreshState().then(renderAll); } }); break;
    case 'gen-diagram': BP.openDiagram(null, { generate: true, onClose: () => { refreshState().then(renderAll); } }); break;
    case 'upload': uploadMany(await pickFiles('', true)); break;
    case 'import': {
      const fs = await pickFiles('.zip,application/zip', false);
      if (!fs.length) return;
      try {
        const c = await api('POST', '/api/import', fs[0]);
        await refreshState(); renderAll();
        toast(`Imported ${c.pages} pages, ${c.diagrams} diagrams, ${c.files} files`);
      } catch (err) { toast('Import failed: ' + err.message, 'err'); }
      break;
    }
    case 'edit': setEditing(true); break;
    case 'done': setEditing(false); break;
    case 'pin': S.page.pinned = !S.page.pinned; await savePage(); renderPage(); break;
    case 'history': showHistory(); break;
    case 'print': if (S.editing) await setEditing(false); setTimeout(() => window.print(), 50); break;
    case 'delete-page': {
      if (!(await confirmBox('Delete page', `Delete “${esc(S.page.title)}”? It is moved to the data/trash folder, not destroyed.`))) return;
      const sec = secOf(S.page);
      await api('DELETE', '/api/page/' + S.page.id);
      S.dirty = false; S.pages = S.pages.filter(p => p.id !== S.page.id); S.page = null;
      go('#/s/' + sec);
      break;
    }
  }
});
document.addEventListener('input', e => {
  const row = e.target.closest('.sec-row');
  if (!row) return;
  const s = S.sections[+row.dataset.i];
  if (e.target.classList.contains('sec-name')) s.name = e.target.value;
  if (e.target.classList.contains('sec-color')) { s.color = e.target.value; row.querySelector('.sec-sw').style.color = s.color; }
  if (e.target.classList.contains('sec-icon')) { s.icon = e.target.value; row.querySelector('.sec-sw').innerHTML = ic(s.icon); }
  saveSections();
});
function touchSave(node) {
  const c = $('#content');
  if (node.classList && node.classList.contains('bp-embed')) renderEmbed(node, S.editing);
  if (S.editing) { markDirty(); return; }
  if (c) { c.dataset.touched = '1'; savePage().then(() => { delete c.dataset.touched; }); }
}
/* figure size bar */
function showFigBar(fig) {
  hideFigBar();
  fig.classList.add('sel');
  const bar = document.createElement('div');
  bar.id = 'figBar';
  bar.innerHTML = ['25', '50', '75', '100'].map(w => `<button data-w="${w}" class="${fig.dataset.w === w ? 'on' : ''}">${w}%</button>`).join('') +
    `<span class="tb-sep"></span><button data-al="left" title="Align left">⇤</button><button data-al="center" title="Centre">↔</button><button data-al="right" title="Align right">⇥</button><span class="tb-sep"></span><button data-rm="1" title="Remove picture">${ic('trash')}</button>`;
  document.body.appendChild(bar);
  const r = fig.getBoundingClientRect();
  bar.style.top = Math.max(8, r.top - 44) + 'px';
  bar.style.left = Math.max(8, r.left + r.width / 2 - 150) + 'px';
  bar.addEventListener('mousedown', e => e.preventDefault());
  bar.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.w) fig.dataset.w = b.dataset.w;
    if (b.dataset.al) fig.dataset.align = b.dataset.al;
    if (b.dataset.rm) { fig.remove(); hideFigBar(); markDirty(); return; }
    markDirty(); showFigBar(fig);
  });
}
function hideFigBar() { const b = $('#figBar'); if (b) b.remove(); $$('figure.sel').forEach(f => f.classList.remove('sel')); }
$('#main').addEventListener('scroll', hideFigBar);

/* drag files onto the Files screen */
document.addEventListener('dragover', e => { if (S.route.name === 'files' && [...(e.dataTransfer.types || [])].includes('Files')) { e.preventDefault(); document.body.classList.add('dropping'); } });
document.addEventListener('dragleave', e => { if (!e.relatedTarget) document.body.classList.remove('dropping'); });
document.addEventListener('drop', e => {
  document.body.classList.remove('dropping');
  if (S.route.name === 'files' && e.dataTransfer.files.length && !e.target.closest('#content')) { e.preventDefault(); uploadMany([...e.dataTransfer.files]); }
});

document.addEventListener('keydown', e => {
  if (BP.diagramIsOpen && BP.diagramIsOpen()) return;
  if (document.querySelector('.modal-back')) return;
  const mod = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
  if (mod && k === 'k') { e.preventDefault(); $('#q').focus(); $('#q').select(); }
  else if (mod && k === 's') { e.preventDefault(); if (S.page) savePage(); }
  else if (mod && k === 'e' && S.route.name === 'page') { e.preventDefault(); setEditing(!S.editing); }
});
window.addEventListener('beforeunload', () => {
  if (!S.dirty || !S.page || !$('#content')) return;
  const p = Object.assign({}, S.page, { html: serialize() });
  const t = $('#pTitle'); if (t) p.title = t.value.trim() || 'Untitled';
  try { fetch('/api/page/' + p.id, { method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }); } catch (e) { /* ignore */ }
});
window.addEventListener('hashchange', onRoute);

/* theme */
function setTheme(t) {
  try { localStorage.setItem('bp-theme', t); } catch (e) { /* ignore */ }
  if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
}

/* ---------- start */
(async function init() {
  buildListShell();
  try { await refreshState(); }
  catch (e) { $('#main').innerHTML = `<div class="wrap"><div class="empty"><h2>Can't reach the Blueprint server</h2><p class="muted">Make sure server.py is running, then reload this page.</p></div></div>`; return; }
  onRoute();
})();
})();
