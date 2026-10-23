/* Blueprint - drag-and-drop diagram editor (network maps, flowcharts, process diagrams) */
(function () {
'use strict';
const BP = window.BP, esc = BP.esc, SH = BP.SHAPES;
const SNAP = 10;
let D = null, opts = {}, isOpen = false, root = null;
let sel = new Set(), selEdge = null, view = { x: 0, y: 0, k: 1 };
let undoS = [], redoS = [], drag = null, hover = null, space = false, hand = false, clip = null, pasteN = 0;
let snapOn = true, saveT = null, dirty = false, raf = 0, labelEd = null, fieldSnap = null;
const el = {};

const FILLS = ['#ffffff', '#eaf1ff', '#e3f7f4', '#fff1e0', '#fde8e8', '#f1eafd', '#fff6c4', '#eef1f5', '#1f2a3d'];
const STROKES = ['#3b6fd8', '#16a394', '#e0892b', '#d64545', '#8a55d6', '#c51a4a', '#c9a227', '#475569', '#7a8aa5'];
const TEXTS = ['#1c2433', '#ffffff', '#3b6fd8', '#d64545', '#16a394', '#5b6780'];

const map = () => { const m = {}; D.nodes.forEach(n => { m[n.id] = n; }); return m; };
const snap = v => snapOn ? Math.round(v / SNAP) * SNAP : Math.round(v);
const snapState = () => JSON.stringify({ nodes: D.nodes, edges: D.edges });
const isIcon = n => !!(SH[n.type] && SH[n.type].icon);
const ic = n => (BP.ic ? BP.ic(n) : '');

function build() {
  if (root) return;
  root = document.getElementById('dgm');
  root.innerHTML = `
  <div class="dgm-top">
    <button class="btn ghost" data-d="close" title="Back to wiki">${ic('back')}</button>
    <input id="dTitle" class="dgm-title" spellcheck="false" placeholder="Diagram name">
    <div class="dgm-group">
      <button class="btn ghost icon" data-d="undo" title="Undo (Ctrl+Z)">${ic('undo')}</button>
      <button class="btn ghost icon" data-d="redo" title="Redo (Ctrl+Y)">${ic('redo')}</button>
    </div>
    <div class="dgm-group">
      <button class="btn accent" data-d="generate" title="Type a description and let Blueprint draw it">${ic('bolt')} Generate</button>
      <button class="btn ghost" data-d="layout-TB" title="Tidy everything top to bottom">${ic('layout')} Auto-layout</button>
    </div>
    <div class="dgm-group">
      <button class="btn ghost icon" data-d="hand" id="dHand" title="Pan mode (or hold Space / right-drag)">${ic('hand')}</button>
      <button class="btn ghost icon on" data-d="snap" id="dSnap" title="Snap to grid">${ic('grid')}</button>
    </div>
    <span class="sp"></span>
    <span id="dStatus" class="dgm-status"></span>
    <button class="btn ghost" data-d="svg" title="Download as SVG">SVG</button>
    <button class="btn ghost" data-d="png" title="Download as PNG image">PNG</button>
    <button class="btn primary" data-d="close">${ic('check')} Done</button>
  </div>
  <div class="dgm-body">
    <aside class="dgm-pal"><div class="pal-search">${ic('search')}<input id="dPalQ" placeholder="Find a shape…"></div><div id="dPal"></div></aside>
    <div class="dgm-canvas" id="dCanvas">
      <svg id="dSvg" tabindex="-1"><defs>
        <pattern id="dG1" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" class="gl"/></pattern>
        <pattern id="dG2" width="100" height="100" patternUnits="userSpaceOnUse"><path d="M100 0H0V100" fill="none" class="gl2"/></pattern>
      </defs><rect width="100%" height="100%" fill="url(#dG1)"/><rect width="100%" height="100%" fill="url(#dG2)"/><g id="dWorld"></g></svg>
      <div class="dgm-hint" id="dHint"><h3>Empty canvas</h3><p>Drag shapes in from the left, double-click to add a box,<br>or describe it in words and let Blueprint draw it.</p><button class="btn accent" data-d="generate">${ic('bolt')} Generate from text</button></div>
      <div class="dgm-zoom"><button data-d="zout" title="Zoom out">−</button><button data-d="zreset" id="dZoom" title="Reset zoom">100%</button><button data-d="zin" title="Zoom in">+</button><button data-d="fit" title="Fit to screen (F)">Fit</button></div>
      <div class="dgm-help">Scroll = zoom · Right-drag / Space = pan · Drag blue dots to connect</div>
    </div>
    <aside class="dgm-props" id="dProps"></aside>
  </div>`;
  ['dTitle', 'dPal', 'dPalQ', 'dCanvas', 'dSvg', 'dWorld', 'dHint', 'dZoom', 'dProps', 'dStatus', 'dHand', 'dSnap', 'dG1', 'dG2']
    .forEach(id => { el[id] = document.getElementById(id); });
  palette('');
  el.dPalQ.addEventListener('input', () => palette(el.dPalQ.value));
  el.dTitle.addEventListener('input', markDirty);
  root.addEventListener('click', onToolbar);
  el.dPal.addEventListener('pointerdown', onPalDown);
  el.dSvg.addEventListener('pointerdown', onDown);
  el.dSvg.addEventListener('dblclick', onDbl);
  el.dSvg.addEventListener('wheel', onWheel, { passive: false });
  el.dSvg.addEventListener('contextmenu', e => e.preventDefault());
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  document.addEventListener('keydown', onKey);
  document.addEventListener('keyup', e => { if (e.key === ' ') { space = false; el.dCanvas.classList.toggle('grab', hand); } });
  window.addEventListener('resize', () => { if (isOpen) schedule(); });
  el.dProps.addEventListener('focusin', e => { if (e.target.matches('[data-f]')) fieldSnap = snapState(); });
  el.dProps.addEventListener('input', onField);
  el.dProps.addEventListener('change', onField);
  el.dProps.addEventListener('click', onPropClick);
}

/* ---------- palette */
function preview(type) {
  const s = SH[type];
  if (s.icon) return `<svg viewBox="0 0 40 40"><g color="${s.stroke}" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${BP.ICONS[s.icon]}</g></svg>`;
  const n = BP.newNode(type, 0, 0, { label: type === 'text' ? 'Aa' : '', fs: type === 'text' ? 30 : 13 });
  if (type === 'zone') { n.w = 160; n.h = 110; n.x = -80; n.y = -55; n.label = ''; }
  return `<svg viewBox="${n.x - 3} ${n.y - 3} ${n.w + 6} ${n.h + 6}">${BP.drawNode(n)}</svg>`;
}
function palette(f) {
  f = (f || '').toLowerCase().trim();
  let h = '';
  BP.CATS.forEach(cat => {
    const items = Object.keys(SH).filter(k => SH[k].cat === cat && (!f || SH[k].name.toLowerCase().includes(f) || k.includes(f)));
    if (!items.length) return;
    h += `<div class="pal-cat">${cat}</div><div class="pal-grid">` +
      items.map(k => `<div class="pal-item" data-shape="${k}" title="Drag onto the canvas, or click to add">${preview(k)}<span>${esc(SH[k].name)}</span></div>`).join('') + '</div>';
  });
  el.dPal.innerHTML = h || '<p class="muted small" style="padding:12px">No shapes match.</p>';
}
function onPalDown(e) {
  const it = e.target.closest('.pal-item');
  if (!it || e.button !== 0) return;
  e.preventDefault();
  const type = it.dataset.shape, sx = e.clientX, sy = e.clientY;
  const ghost = document.createElement('div');
  ghost.className = 'pal-ghost'; ghost.innerHTML = preview(type);
  document.body.appendChild(ghost);
  const move = ev => { ghost.style.left = ev.clientX + 'px'; ghost.style.top = ev.clientY + 'px'; };
  move(e);
  const up = ev => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); ghost.remove();
    const r = el.dCanvas.getBoundingClientRect();
    const inside = ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 6) addShape(type, freeSpot());
    else if (inside) addShape(type, toWorld(ev));
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}
function freeSpot() {
  const c = { x: (el.dCanvas.clientWidth / 2 - view.x) / view.k, y: (el.dCanvas.clientHeight / 2 - view.y) / view.k };
  for (let i = 0; i < 30; i++) {
    if (!D.nodes.some(n => Math.abs(n.x + n.w / 2 - c.x) < 12 && Math.abs(n.y + n.h / 2 - c.y) < 12)) break;
    c.x += 30; c.y += 30;
  }
  return c;
}
function addShape(type, p) {
  commit();
  const n = BP.newNode(type, p.x, p.y);
  n.x = snap(n.x); n.y = snap(n.y);
  if (type === 'zone') D.nodes.unshift(n); else D.nodes.push(n);
  sel = new Set([n.id]); selEdge = null;
  changed();
}

/* ---------- rendering */
function toWorld(ev) {
  const r = el.dSvg.getBoundingClientRect();
  return { x: (ev.clientX - r.left - view.x) / view.k, y: (ev.clientY - r.top - view.y) / view.k };
}
function schedule() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); }
function render() {
  if (!D) return;
  const t = `translate(${view.x} ${view.y}) scale(${view.k})`, k = view.k;
  el.dWorld.setAttribute('transform', t);
  el.dG1.setAttribute('patternTransform', t);
  el.dG2.setAttribute('patternTransform', t);
  const m = map();
  let h = '';
  D.nodes.forEach(n => { if (n.type === 'zone') h += BP.drawNode(n); });
  D.edges.forEach(e => { h += BP.drawEdge(e, m, { sel: selEdge === e.id, dir: D.layoutDir }); });
  D.nodes.forEach(n => { if (n.type !== 'zone') h += BP.drawNode(n); });
  sel.forEach(id => {
    const n = m[id];
    if (n) h += `<rect class="sel-box" x="${n.x - 4}" y="${n.y - 4}" width="${n.w + 8}" height="${n.h + 8}" rx="8" style="stroke-width:${1.6 / k}px;stroke-dasharray:${5 / k} ${4 / k}"/>`;
  });
  if (sel.size === 1 && !drag) {
    const n = m[[...sel][0]];
    if (n) { const s = 11 / k; h += `<rect class="h-resize" data-id="${esc(n.id)}" x="${n.x + n.w + 4 - s / 2}" y="${n.y + n.h + 4 - s / 2}" width="${s}" height="${s}" rx="${2 / k}"/>`; }
  }
  const portIds = new Set();
  if (!drag) { if (hover) portIds.add(hover); if (sel.size === 1) portIds.add([...sel][0]); }
  if (drag && drag.mode === 'link') {
    const a = BP.port(m[drag.from], drag.side);
    if (drag.target && m[drag.target]) { const n = m[drag.target]; h += `<rect class="link-target" x="${n.x - 5}" y="${n.y - 5}" width="${n.w + 10}" height="${n.h + 10}" rx="10" style="stroke-width:${2.5 / k}px"/>`; }
    h += `<path class="link-temp" d="M${a.x},${a.y} L${drag.cur.x},${drag.cur.y}" style="stroke-width:${2 / k}px;stroke-dasharray:${6 / k} ${4 / k}"/>`;
  }
  portIds.forEach(id => {
    const n = m[id];
    if (!n || n.type === 'zone') return;
    ['t', 'r', 'b', 'l'].forEach(s => { const p = BP.port(n, s); h += `<circle class="port" data-id="${esc(n.id)}" data-side="${s}" cx="${p.x}" cy="${p.y}" r="${6 / k}" style="stroke-width:${1.8 / k}px"/>`; });
  });
  if (drag && drag.mode === 'box') {
    const b = rectOf(drag.start, drag.cur);
    h += `<rect class="rubber" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" style="stroke-width:${1 / k}px"/>`;
  }
  el.dWorld.innerHTML = h;
  el.dZoom.textContent = Math.round(k * 100) + '%';
  el.dHint.hidden = D.nodes.length > 0;
}
const rectOf = (a, b) => ({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) });
function hitNode(p, pad, exclude) {
  for (let i = D.nodes.length - 1; i >= 0; i--) {
    const n = D.nodes[i];
    if (n.type === 'zone' || n.id === exclude) continue;
    if (p.x >= n.x - pad && p.x <= n.x + n.w + pad && p.y >= n.y - pad && p.y <= n.y + n.h + pad) return n;
  }
  return null;
}
const inside = (n, z) => { const cx = n.x + n.w / 2, cy = n.y + n.h / 2; return cx > z.x && cx < z.x + z.w && cy > z.y && cy < z.y + z.h; };

/* ---------- undo */
function commit() { pushUndo(snapState()); }
function pushUndo(s) { undoS.push(s); if (undoS.length > 150) undoS.shift(); redoS = []; }
function restore(s) {
  const o = JSON.parse(s);
  D.nodes = o.nodes; D.edges = o.edges;
  sel = new Set([...sel].filter(id => D.nodes.some(n => n.id === id)));
  if (selEdge && !D.edges.some(e => e.id === selEdge)) selEdge = null;
  changed();
}
function undo() { if (!undoS.length) return; redoS.push(snapState()); restore(undoS.pop()); }
function redo() { if (!redoS.length) return; undoS.push(snapState()); restore(redoS.pop()); }
function changed() { render(); props(); markDirty(); }

/* ---------- pointer */
function onDown(e) {
  if (labelEd) finishLabel(true);
  const p = toWorld(e), t = e.target;
  if (e.button === 1 || e.button === 2 || space || hand) {
    e.preventDefault();
    drag = { mode: 'pan', sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
    el.dCanvas.classList.add('panning'); return;
  }
  if (e.button !== 0) return;
  if (t.classList.contains('port')) { drag = { mode: 'link', from: t.dataset.id, side: t.dataset.side, cur: p, target: null }; render(); return; }
  if (t.classList.contains('h-resize')) {
    const n = map()[t.dataset.id];
    drag = { mode: 'resize', n, start: p, w: n.w, h: n.h, snapshot: snapState(), moved: false }; return;
  }
  const ne = t.closest('.node'), ee = t.closest('.edge');
  if (ne) {
    const id = ne.dataset.id;
    selEdge = null;
    if (e.shiftKey || e.ctrlKey || e.metaKey) { if (sel.has(id)) sel.delete(id); else sel.add(id); }
    else if (!sel.has(id)) sel = new Set([id]);
    const m = map(), moving = new Set(sel);
    sel.forEach(sid => { const z = m[sid]; if (z && z.type === 'zone') D.nodes.forEach(n => { if (n.id !== z.id && inside(n, z)) moving.add(n.id); }); });
    drag = { mode: 'move', start: p, orig: new Map([...moving].filter(i => m[i]).map(i => [i, { x: m[i].x, y: m[i].y }])), snapshot: snapState(), moved: false };
    render(); props(); return;
  }
  if (ee) { selEdge = ee.dataset.id; sel.clear(); render(); props(); return; }
  if (!e.shiftKey) { sel.clear(); selEdge = null; }
  drag = { mode: 'box', start: p, cur: p, base: new Set(sel) };
  render(); props();
}
function onMove(e) {
  if (!isOpen) return;
  if (!drag) {
    if (el.dSvg.contains(e.target)) {
      const n = hitNode(toWorld(e), 14 / view.k);
      const id = n ? n.id : null;
      if (id !== hover) { hover = id; schedule(); }
    }
    return;
  }
  const p = toWorld(e);
  switch (drag.mode) {
    case 'pan': view.x = drag.vx + e.clientX - drag.sx; view.y = drag.vy + e.clientY - drag.sy; schedule(); break;
    case 'move': {
      const dx = p.x - drag.start.x, dy = p.y - drag.start.y;
      if (!drag.moved) { if (Math.hypot(dx, dy) * view.k < 3) return; drag.moved = true; pushUndo(drag.snapshot); }
      const m = map(), sdx = snap(dx), sdy = snap(dy);
      drag.orig.forEach((o, id) => { if (m[id]) { m[id].x = Math.round(o.x + sdx); m[id].y = Math.round(o.y + sdy); } });
      schedule(); break;
    }
    case 'resize': {
      if (!drag.moved) { drag.moved = true; pushUndo(drag.snapshot); }
      drag.n.w = Math.max(30, snap(drag.w + p.x - drag.start.x));
      drag.n.h = Math.max(24, snap(drag.h + p.y - drag.start.y));
      schedule(); break;
    }
    case 'link': { drag.cur = p; const n = hitNode(p, 6 / view.k, drag.from); drag.target = n ? n.id : null; schedule(); break; }
    case 'box': {
      drag.cur = p;
      const b = rectOf(drag.start, p);
      sel = new Set(drag.base);
      D.nodes.forEach(n => { if (n.x >= b.x && n.y >= b.y && n.x + n.w <= b.x + b.w && n.y + n.h <= b.y + b.h) sel.add(n.id); });
      schedule(); break;
    }
  }
}
function newEdge(a, b) {
  const m = map(), na = m[a], nb = m[b];
  const net = na && nb && isIcon(na) && isIcon(nb);
  return { id: BP.uid('e'), from: a, to: b, label: '', style: D.edgeStyle || 'orth', arrow: net ? 'none' : 'end',
    dash: !!(na && na.type === 'ap' && net), color: '', width: 2 };
}
function onUp(e) {
  if (!isOpen || !drag) return;
  const d = drag; drag = null;
  el.dCanvas.classList.remove('panning');
  if (d.mode === 'link') {
    const p = toWorld(e);
    if (d.target) {
      if (!D.edges.some(x => x.from === d.from && x.to === d.target)) { commit(); D.edges.push(newEdge(d.from, d.target)); changed(); }
      else render();
      return;
    }
    const a = map()[d.from];
    if (a && Math.hypot(p.x - (a.x + a.w / 2), p.y - (a.y + a.h / 2)) > Math.max(a.w, a.h) * 0.8) {
      commit();
      const t = (a.type === 'decision' || a.type === 'terminator') ? 'process' : a.type;
      const n = BP.newNode(t, p.x, p.y, { label: isIcon(a) ? SH[t].name.split(' /')[0] : '' });
      n.x = snap(n.x); n.y = snap(n.y);
      D.nodes.push(n); D.edges.push(newEdge(d.from, n.id));
      sel = new Set([n.id]); selEdge = null;
      changed();
      setTimeout(() => editLabel(n), 0);
    } else render();
    return;
  }
  if ((d.mode === 'move' || d.mode === 'resize') && d.moved) { changed(); return; }
  render(); props();
}
function onWheel(e) {
  e.preventDefault();
  if (e.shiftKey) { view.x -= e.deltaX || e.deltaY; view.y -= e.deltaX ? e.deltaY : 0; schedule(); return; }
  const r = el.dSvg.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, view.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016)));
}
function zoomAt(mx, my, k) {
  k = Math.min(4, Math.max(0.1, k));
  view.x = mx - (mx - view.x) * (k / view.k);
  view.y = my - (my - view.y) * (k / view.k);
  view.k = k; schedule();
}
function fit() {
  const cw = el.dCanvas.clientWidth, ch = el.dCanvas.clientHeight;
  if (!D.nodes.length) { view = { x: cw / 2, y: ch / 2, k: 1 }; schedule(); return; }
  const b = BP.bounds(D.nodes);
  const k = Math.min(1.4, Math.max(0.1, Math.min((cw - 100) / b.w, (ch - 100) / b.h)));
  view = { k, x: (cw - b.w * k) / 2 - b.x * k, y: (ch - b.h * k) / 2 - b.y * k };
  schedule();
}

/* ---------- labels */
function onDbl(e) {
  const t = e.target, ne = t.closest('.node'), ee = t.closest('.edge');
  if (ne) { editLabel(map()[ne.dataset.id]); return; }
  if (ee) { editEdgeLabel(D.edges.find(x => x.id === ee.dataset.id)); return; }
  const p = toWorld(e);
  commit();
  const n = BP.newNode('process', p.x, p.y, { label: '' });
  n.x = snap(n.x); n.y = snap(n.y);
  D.nodes.push(n); sel = new Set([n.id]); selEdge = null;
  changed(); editLabel(n);
}
function overlay(tag, box, value, fs) {
  const t = document.createElement(tag);
  t.className = 'dgm-label-ed';
  t.value = value || '';
  Object.assign(t.style, { left: box.x + 'px', top: box.y + 'px', width: box.w + 'px', height: box.h + 'px', fontSize: fs + 'px' });
  el.dCanvas.appendChild(t);
  t.focus(); t.select();
  t.addEventListener('keydown', ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); finishLabel(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); finishLabel(false); }
  });
  t.addEventListener('blur', () => finishLabel(true));
  return t;
}
function editLabel(n) {
  if (!n) return;
  finishLabel(true);
  const k = view.k, top = isIcon(n) ? 50 : 0;
  const ta = overlay('textarea', { x: n.x * k + view.x, y: (n.y + top) * k + view.y, w: Math.max(80, n.w * k), h: Math.max(30, (n.h - top) * k) }, n.label, Math.max(11, (n.fs || 13) * k));
  labelEd = { ta, target: n, node: n, before: snapState() };
}
function editEdgeLabel(e) {
  if (!e) return;
  finishLabel(true);
  const g = BP.edgeGeom(e, map(), D.layoutDir);
  if (!g) return;
  const k = view.k;
  const inp = overlay('input', { x: g.mid.x * k + view.x - 80, y: g.mid.y * k + view.y - 15, w: 160, h: 30 }, e.label, 13);
  labelEd = { ta: inp, target: e, before: snapState() };
}
function finishLabel(save) {
  if (!labelEd) return;
  const { ta, target, node, before } = labelEd;
  labelEd = null;
  const v = ta.value;
  ta.remove();
  if (save && v !== (target.label || '')) { pushUndo(before); target.label = v; if (node) BP.fitNode(node); changed(); }
}

/* ---------- editing ops */
function del() {
  if (!sel.size && !selEdge) return;
  commit();
  if (selEdge) { D.edges = D.edges.filter(e => e.id !== selEdge); selEdge = null; }
  if (sel.size) { D.nodes = D.nodes.filter(n => !sel.has(n.id)); D.edges = D.edges.filter(e => !sel.has(e.from) && !sel.has(e.to)); sel.clear(); }
  changed();
}
function copy() {
  if (!sel.size) return;
  clip = { nodes: D.nodes.filter(n => sel.has(n.id)).map(n => ({ ...n })), edges: D.edges.filter(e => sel.has(e.from) && sel.has(e.to)).map(e => ({ ...e })) };
  pasteN = 0;
}
function paste() {
  if (!clip) return;
  commit();
  pasteN++;
  const ids = new Map();
  const nodes = clip.nodes.map(n => { const c = { ...n, id: BP.uid('n'), x: n.x + 30 * pasteN, y: n.y + 30 * pasteN }; ids.set(n.id, c.id); return c; });
  const edges = clip.edges.map(e => ({ ...e, id: BP.uid('e'), from: ids.get(e.from), to: ids.get(e.to) }));
  D.nodes.push(...nodes); D.edges.push(...edges);
  sel = new Set(nodes.map(n => n.id)); selEdge = null;
  changed();
}
function order(front) {
  if (!sel.size) return;
  commit();
  const picked = D.nodes.filter(n => sel.has(n.id)), rest = D.nodes.filter(n => !sel.has(n.id));
  D.nodes = front ? rest.concat(picked) : picked.concat(rest);
  changed();
}
function selected() { const m = map(); return [...sel].map(id => m[id]).filter(Boolean); }
function align(how) {
  const ns = selected();
  if (ns.length < 2) return;
  commit();
  const b = BP.bounds(ns);
  ns.forEach(n => {
    if (how === 'l') n.x = b.x; if (how === 'r') n.x = b.x + b.w - n.w; if (how === 'c') n.x = Math.round(b.x + b.w / 2 - n.w / 2);
    if (how === 't') n.y = b.y; if (how === 'b') n.y = b.y + b.h - n.h; if (how === 'm') n.y = Math.round(b.y + b.h / 2 - n.h / 2);
  });
  changed();
}
function distribute(axis) {
  const ns = selected();
  if (ns.length < 3) return;
  commit();
  const c = n => axis === 'h' ? n.x + n.w / 2 : n.y + n.h / 2;
  ns.sort((a, b) => c(a) - c(b));
  const a = c(ns[0]), z = c(ns[ns.length - 1]), step = (z - a) / (ns.length - 1);
  ns.forEach((n, i) => { const t = a + step * i; if (axis === 'h') n.x = Math.round(t - n.w / 2); else n.y = Math.round(t - n.h / 2); });
  changed();
}
function wrapZone() {
  const ns = selected().filter(n => n.type !== 'zone');
  if (!ns.length) return;
  commit();
  const b = BP.bounds(ns);
  const z = BP.newNode('zone', 0, 0, { x: b.x - 30, y: b.y - 48, w: b.w + 60, h: b.h + 78, label: 'Group' });
  D.nodes.unshift(z);
  sel = new Set([z.id]);
  changed();
  setTimeout(() => editLabel(z), 0);
}
function autoLayout(dir, onlySel) {
  const ns = (onlySel ? selected() : D.nodes).filter(n => n.type !== 'zone');
  if (!ns.length) return;
  commit();
  const b = BP.bounds(ns);
  const ids = new Set(ns.map(n => n.id));
  BP.layout(ns, D.edges.filter(e => ids.has(e.from) && ids.has(e.to)), dir, { x0: b.x, y0: b.y });
  if (dir) D.layoutDir = dir;
  changed();
  if (!onlySel) fit();
}

/* ---------- keyboard */
function onKey(e) {
  if (!isOpen || document.querySelector('.modal-back')) return;
  const tg = e.target, typing = tg && (tg.tagName === 'INPUT' || tg.tagName === 'TEXTAREA' || tg.tagName === 'SELECT' || tg.isContentEditable);
  const mod = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
  if (mod && k === 's') { e.preventDefault(); save(); return; }
  if (typing) return;
  if (e.key === ' ') { space = true; el.dCanvas.classList.add('grab'); e.preventDefault(); return; }
  if (mod && k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
  if (mod && k === 'y') { e.preventDefault(); redo(); return; }
  if (mod && k === 'a') { e.preventDefault(); sel = new Set(D.nodes.map(n => n.id)); selEdge = null; render(); props(); return; }
  if (mod && k === 'd') { e.preventDefault(); copy(); paste(); return; }
  if (mod && k === 'c') { copy(); return; }
  if (mod && k === 'v') { e.preventDefault(); paste(); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); del(); return; }
  if (e.key === 'Escape') { e.preventDefault(); if (sel.size || selEdge) { sel.clear(); selEdge = null; render(); props(); } else close(); return; }
  if (e.key === 'Enter' && sel.size === 1) { e.preventDefault(); editLabel(selected()[0]); return; }
  if (!mod && k === 'f') { fit(); return; }
  if (!mod && (k === '+' || k === '=')) { zoomAt(el.dCanvas.clientWidth / 2, el.dCanvas.clientHeight / 2, view.k * 1.2); return; }
  if (!mod && k === '-') { zoomAt(el.dCanvas.clientWidth / 2, el.dCanvas.clientHeight / 2, view.k / 1.2); return; }
  const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  if (arrows[e.key] && sel.size) {
    e.preventDefault();
    commit();
    const s = e.shiftKey ? 20 : (snapOn ? SNAP : 1);
    selected().forEach(n => { n.x += arrows[e.key][0] * s; n.y += arrows[e.key][1] * s; });
    changed();
  }
}

/* ---------- toolbar */
function onToolbar(e) {
  const b = e.target.closest('[data-d]');
  if (!b) return;
  const a = b.dataset.d, cw = el.dCanvas.clientWidth, ch = el.dCanvas.clientHeight;
  switch (a) {
    case 'close': close(); break;
    case 'undo': undo(); break;
    case 'redo': redo(); break;
    case 'generate': generateDialog(); break;
    case 'layout-TB': autoLayout(D.layoutDir || 'TB'); break;
    case 'hand': hand = !hand; el.dHand.classList.toggle('on', hand); el.dCanvas.classList.toggle('grab', hand); break;
    case 'snap': snapOn = !snapOn; el.dSnap.classList.toggle('on', snapOn); break;
    case 'zin': zoomAt(cw / 2, ch / 2, view.k * 1.25); break;
    case 'zout': zoomAt(cw / 2, ch / 2, view.k / 1.25); break;
    case 'zreset': zoomAt(cw / 2, ch / 2, 1); break;
    case 'fit': fit(); break;
    case 'svg': BP.downloadBlob(new Blob([BP.diagramSVG(D, { bg: '#ffffff' })], { type: 'image/svg+xml' }), fileName() + '.svg'); break;
    case 'png': BP.diagramPNG(D, fileName()); break;
  }
}
const fileName = () => (el.dTitle.value || 'diagram').trim().replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '') || 'diagram';

/* ---------- properties panel */
function swatches(list, target, cur) {
  return `<div class="sw">${list.map(c => `<button class="swb ${c === cur ? 'on' : ''}" data-color="${c}" data-t="${target}" style="background:${c}" title="${c}"></button>`).join('')}<label class="swc" title="Custom colour"><input type="color" data-cpick="${target}" value="${/^#[0-9a-f]{6}$/i.test(cur || '') ? cur : '#3b6fd8'}"></label></div>`;
}
function typeOptions(cur) {
  return BP.CATS.map(c => `<optgroup label="${c}">${Object.keys(SH).filter(k => SH[k].cat === c).map(k => `<option value="${k}" ${k === cur ? 'selected' : ''}>${esc(SH[k].name)}</option>`).join('')}</optgroup>`).join('');
}
function props() {
  const p = el.dProps;
  if (!D) { p.innerHTML = ''; return; }
  const m = map();
  if (selEdge) {
    const e = D.edges.find(x => x.id === selEdge);
    if (!e) { selEdge = null; return props(); }
    const opt = (v, cur, l) => `<option value="${v}" ${v === cur ? 'selected' : ''}>${l}</option>`;
    p.innerHTML = `<div class="pp-h">Connection</div>
      <div class="pp-sub">${esc((m[e.from] || {}).label || '?')} → ${esc((m[e.to] || {}).label || '?')}</div>
      <label>Label<input data-f="label" value="${esc(e.label)}" placeholder="e.g. 1 Gbps, VLAN 10, Yes"></label>
      <label>Route<select data-f="style">${opt('orth', e.style || 'orth', 'Elbow')}${opt('straight', e.style, 'Straight')}${opt('curve', e.style, 'Curved')}</select></label>
      <label>Arrows<select data-f="arrow">${opt('end', e.arrow || 'end', 'Arrow at end')}${opt('none', e.arrow, 'No arrows')}${opt('both', e.arrow, 'Both ends')}${opt('start', e.arrow, 'Arrow at start')}</select></label>
      <label class="chk"><input type="checkbox" data-f="dash" ${e.dash ? 'checked' : ''}> Dashed (wireless, VPN, optional)</label>
      <label>Thickness<input type="range" min="1" max="6" step="0.5" data-f="width" value="${e.width || 2}"></label>
      <div class="pp-l">Colour</div>${swatches(STROKES, 'ecolor', e.color || '#7a8aa5')}
      <div class="pp-btns"><button class="btn small" data-p="reverse">⇄ Reverse</button><button class="btn small danger" data-p="delete">${ic('trash')} Delete</button></div>`;
    return;
  }
  if (sel.size === 1) {
    const n = m[[...sel][0]];
    if (!n) { sel.clear(); return props(); }
    const icon = isIcon(n);
    p.innerHTML = `<div class="pp-h">${esc(SH[n.type] ? SH[n.type].name : n.type)}</div>
      <label>Label<textarea data-f="label" rows="2">${esc(n.label)}</textarea></label>
      <label>${icon ? 'IP address / detail' : n.type === 'zone' ? 'Subnet / detail' : 'Sub-text'}<input data-f="sub" value="${esc(n.sub)}" placeholder="${icon ? 'e.g. 192.168.1.10' : 'optional'}"></label>
      <label>Shape<select data-f="type">${typeOptions(n.type)}</select></label>
      <div class="pp-row"><label>Width<input type="number" data-f="w" value="${n.w}" min="20" step="10"></label><label>Height<input type="number" data-f="h" value="${n.h}" min="20" step="10"></label><label>Text<input type="number" data-f="fs" value="${n.fs || 13}" min="8" max="48"></label></div>
      ${n.type === 'text' ? '' : `<div class="pp-l">Fill</div>${swatches(FILLS, 'fill', n.fill)}<div class="pp-l">Outline / accent</div>${swatches(STROKES, 'stroke', n.stroke)}`}
      <div class="pp-l">Text colour</div>${swatches(TEXTS, 'color', n.color)}
      <label>Notes<textarea data-f="notes" rows="4" placeholder="Model, serial, config, login location, links… (shows on hover)">${esc(n.notes)}</textarea></label>
      <div class="pp-btns"><button class="btn small" data-p="front">Bring to front</button><button class="btn small" data-p="back">Send to back</button><button class="btn small" data-p="dup">Duplicate</button><button class="btn small danger" data-p="delete">${ic('trash')} Delete</button></div>`;
    return;
  }
  if (sel.size > 1) {
    p.innerHTML = `<div class="pp-h">${sel.size} shapes selected</div>
      <div class="pp-l">Align</div><div class="pp-btns g3"><button class="btn small" data-p="al-l">Left</button><button class="btn small" data-p="al-c">Centre</button><button class="btn small" data-p="al-r">Right</button><button class="btn small" data-p="al-t">Top</button><button class="btn small" data-p="al-m">Middle</button><button class="btn small" data-p="al-b">Bottom</button></div>
      <div class="pp-l">Distribute evenly</div><div class="pp-btns"><button class="btn small" data-p="di-h">Across</button><button class="btn small" data-p="di-v">Down</button></div>
      <div class="pp-l">Fill</div>${swatches(FILLS, 'fill', '')}<div class="pp-l">Outline</div>${swatches(STROKES, 'stroke', '')}
      <div class="pp-btns"><button class="btn small" data-p="zone">Wrap in a group</button><button class="btn small" data-p="lay-sel">Tidy selection</button><button class="btn small" data-p="dup">Duplicate</button><button class="btn small danger" data-p="delete">${ic('trash')} Delete</button></div>`;
    return;
  }
  const ds = D.edgeStyle || 'orth';
  p.innerHTML = `<div class="pp-h">Diagram</div>
    <p class="pp-tip">Drag shapes in from the left. Hover a shape and drag from a blue dot to connect it. Drop a connection on empty space to create the next step.</p>
    <button class="btn accent full" data-p="generate">${ic('bolt')} Generate from text…</button>
    <div class="pp-l">Auto-layout</div><div class="pp-btns"><button class="btn small" data-p="lay-TB">Top → down</button><button class="btn small" data-p="lay-LR">Left → right</button></div>
    <label>New connections<select data-f="edgeStyle" data-doc="1"><option value="orth" ${ds === 'orth' ? 'selected' : ''}>Elbow</option><option value="straight" ${ds === 'straight' ? 'selected' : ''}>Straight</option><option value="curve" ${ds === 'curve' ? 'selected' : ''}>Curved</option></select></label>
    <div class="pp-stats">${D.nodes.length} shapes · ${D.edges.length} connections</div>
    <div class="pp-l">Shortcuts</div>
    <ul class="pp-keys"><li><kbd>Dbl-click</kbd> add box / rename</li><li><kbd>Del</kbd> delete</li><li><kbd>Ctrl D</kbd> duplicate</li><li><kbd>Ctrl Z</kbd> undo</li><li><kbd>Shift</kbd> drag to multi-select</li><li><kbd>Arrows</kbd> nudge</li><li><kbd>F</kbd> fit to screen</li></ul>`;
}
function onField(e) {
  const f = e.target.dataset.f;
  if (!f || !D) return;
  if (e.type === 'change' && e.target.type !== 'checkbox' && e.target.tagName !== 'SELECT') return;
  if (fieldSnap) { pushUndo(fieldSnap); fieldSnap = null; }
  let v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  if (e.target.dataset.doc) { D[f] = v; markDirty(); return; }
  if (selEdge) {
    const ed = D.edges.find(x => x.id === selEdge);
    if (!ed) return;
    ed[f] = f === 'width' ? parseFloat(v) : v;
  } else if (sel.size === 1) {
    const n = selected()[0];
    if (!n) return;
    if (f === 'type') {
      const was = isIcon(n), d = BP.newNode(v, 0, 0);
      n.type = v;
      if (was !== isIcon(n) || v === 'decision' || v === 'zone' || v === 'connector') { n.w = d.w; n.h = d.h; }
      n.fill = d.fill; n.stroke = d.stroke;
      if (v === 'zone') { D.nodes = [n].concat(D.nodes.filter(x => x !== n)); }
      BP.fitNode(n); render(); props(); markDirty(); return;
    }
    if (f === 'w' || f === 'h' || f === 'fs') { v = parseFloat(v); if (!(v > 0)) return; }
    n[f] = v;
    if (f === 'label' || f === 'sub' || f === 'fs') BP.fitNode(n);
  }
  render(); markDirty();
}
function onPropClick(e) {
  const sw = e.target.closest('[data-color]');
  if (sw) { applyColor(sw.dataset.t, sw.dataset.color); return; }
  const b = e.target.closest('[data-p]');
  if (!b) return;
  const a = b.dataset.p;
  if (a === 'delete') del();
  else if (a === 'dup') { copy(); paste(); }
  else if (a === 'front') order(true);
  else if (a === 'back') order(false);
  else if (a === 'reverse') { const ed = D.edges.find(x => x.id === selEdge); if (ed) { commit(); [ed.from, ed.to] = [ed.to, ed.from]; [ed.fromSide, ed.toSide] = [ed.toSide, ed.fromSide]; changed(); } }
  else if (a.startsWith('al-')) align(a.slice(3));
  else if (a.startsWith('di-')) distribute(a.slice(3));
  else if (a === 'zone') wrapZone();
  else if (a === 'lay-sel') autoLayout(D.layoutDir || 'TB', true);
  else if (a === 'lay-TB') autoLayout('TB');
  else if (a === 'lay-LR') autoLayout('LR');
  else if (a === 'generate') generateDialog();
}
el.applyPick = null;
document.addEventListener('input', e => { if (isOpen && e.target.dataset && e.target.dataset.cpick) applyColor(e.target.dataset.cpick, e.target.value, true); });
let colorSnapAt = 0;
function applyColor(t, c, live) {
  const now = Date.now();
  if (!live || now - colorSnapAt > 800) commit();
  colorSnapAt = now;
  if (t === 'ecolor') { const ed = D.edges.find(x => x.id === selEdge); if (ed) ed.color = c; }
  else selected().forEach(n => { n[t] = c; });
  render(); markDirty();
  if (!live) props();
}

/* ---------- generate from text */
function generateDialog() {
  const T = BP.DGM_TEMPLATES;
  const body = `<div class="gen">
    <div class="gen-row">
      <label>Start from<select id="gTpl"><option value="">Write my own</option>${T.map((t, i) => `<option value="${i}">${esc(t.name)}</option>`).join('')}</select></label>
      <label>Kind<select id="gMode"><option value="auto">Work it out</option><option value="network">Network / system</option><option value="flow">Flowchart / process</option></select></label>
      <label>Direction<select id="gDir"><option value="TB">Top → down</option><option value="LR">Left → right</option></select></label>
    </div>
    <textarea id="gText" rows="11" spellcheck="false" placeholder="Internet -> Router -> Switch\nSwitch -> Desktop PC (192.168.1.20), Raspberry Pi, Printer\n\nor just list the steps of a process, one per line"></textarea>
    <details class="gen-help"><summary>How to write it</summary>
      <ul>
        <li><code>A -> B -> C</code> connects things in a chain</li>
        <li><code>Switch -> PC 1, PC 2, Printer</code> fans out to several things</li>
        <li><code>Router -> Switch : 1 Gbps</code> puts a label on the connection</li>
        <li><code>NAS (192.168.1.40)</code> adds an address or detail line</li>
        <li><code>Garage box [pi]</code> forces a shape: router, switch, firewall, cloud, ap, server, nas, database, pc, laptop, phone, printer, camera, pi, mcu, sensor, power, process, decision, start, end, document, note</li>
        <li>A plain list of steps (no arrows) becomes a flowchart. A line ending in <code>?</code> becomes a decision: Yes carries on, No loops back.</li>
        <li>Names like router, switch, Wi-Fi, NAS, Pi, ESP32 pick the right icon by themselves. Lines starting with <code>#</code> are ignored.</li>
      </ul></details>
    <label class="chk"><input type="checkbox" id="gReplace" ${D.nodes.length ? '' : 'checked'}> Replace what is on the canvas (otherwise it is added beside it)</label>
  </div>`;
  const m = BP.modal({
    title: 'Generate a diagram from text', body, wide: true,
    buttons: [{ label: 'Cancel' }, { label: 'Generate', primary: true, onClick: () => generate() }],
  });
  const tp = m.el.querySelector('#gTpl');
  tp.addEventListener('change', () => {
    const t = T[tp.value];
    if (!t) return;
    m.el.querySelector('#gText').value = t.text;
    m.el.querySelector('#gMode').value = t.mode;
    m.el.querySelector('#gDir').value = t.dir;
  });
  setTimeout(() => m.el.querySelector('#gText').focus(), 30);
  function generate() {
    const text = m.el.querySelector('#gText').value, dir = m.el.querySelector('#gDir').value;
    const res = BP.parseDSL(text, m.el.querySelector('#gMode').value);
    if (!res.nodes.length) { BP.toast('Type something to draw first'); return false; }
    const replace = m.el.querySelector('#gReplace').checked;
    commit();
    const ids = new Map();
    const nodes = res.nodes.map(r => { const n = BP.fitNode(BP.newNode(r.type, 0, 0, { label: r.name, sub: r.sub || '' })); ids.set(r.key, n.id); return n; });
    const byId = new Map(nodes.map(n => [n.id, n]));
    const edges = res.edges.map(e => {
      const a = byId.get(ids.get(e.from)), b = byId.get(ids.get(e.to));
      const net = isIcon(a) && isIcon(b);
      return { id: BP.uid('e'), from: a.id, to: b.id, label: e.label || '', style: 'orth', arrow: net ? 'none' : 'end', dash: !!e.dash, color: '', width: 2 };
    });
    let x0 = 0, y0 = 0;
    if (!replace && D.nodes.length) { const b = BP.bounds(D.nodes); x0 = b.x + b.w + 140; y0 = b.y; }
    BP.layout(nodes, edges, dir, { x0, y0 });
    if (replace) { D.nodes = nodes; D.edges = edges; } else { D.nodes.push(...nodes); D.edges.push(...edges); }
    D.layoutDir = dir;
    if (replace && (!el.dTitle.value || /^untitled/i.test(el.dTitle.value)) && tp.value !== '') el.dTitle.value = T[tp.value].name;
    sel = new Set(); selEdge = null;
    changed(); fit();
    return true;
  }
}

/* ---------- export helpers */
BP.downloadBlob = function (blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
};
BP.diagramPNG = function (d, name) {
  const svg = BP.diagramSVG(d, { pad: 30, bg: '#ffffff' });
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  const img = new Image();
  img.onload = () => {
    const c = document.createElement('canvas');
    c.width = img.width * 2; c.height = img.height * 2;
    const x = c.getContext('2d'); x.scale(2, 2); x.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    c.toBlob(b => BP.downloadBlob(b, (name || 'diagram') + '.png'));
  };
  img.src = url;
};

/* ---------- open / save / close */
function status(t) { if (el.dStatus) el.dStatus.textContent = t; }
function markDirty() { dirty = true; status('Unsaved'); clearTimeout(saveT); saveT = setTimeout(save, 1500); }
async function save() {
  clearTimeout(saveT);
  if (!D) return;
  D.title = el.dTitle.value.trim() || 'Untitled diagram';
  status('Saving…');
  try {
    await BP.api('PUT', '/api/diagram/' + D.id, D);
    dirty = false; status('Saved ✓');
    if (BP.diagramCache) BP.diagramCache.delete(D.id);
    if (BP.onDiagramsChanged) BP.onDiagramsChanged();
  } catch (err) { status('Save failed'); BP.toast('Diagram save failed: ' + err.message, 'err'); }
}
async function close() {
  finishLabel(true);
  if (dirty) await save();
  isOpen = false; root.hidden = true;
  document.body.classList.remove('dgm-open');
  const d = D; D = null;
  if (opts.onClose) opts.onClose(d);
}
BP.diagramIsOpen = () => isOpen;
BP.openDiagram = async function (id, o) {
  build();
  opts = o || {};
  if (isOpen) await close();
  if (id) {
    try { D = await BP.api('GET', '/api/diagram/' + encodeURIComponent(id)); }
    catch (err) { BP.toast('Could not open that diagram', 'err'); return; }
  } else {
    D = { id: BP.uid('d'), title: opts.title || 'Untitled diagram', nodes: [], edges: [], edgeStyle: 'orth' };
    try { await BP.api('PUT', '/api/diagram/' + D.id, D); } catch (err) { BP.toast('Could not create diagram: ' + err.message, 'err'); return; }
    if (BP.onDiagramsChanged) BP.onDiagramsChanged();
  }
  D.nodes = D.nodes || []; D.edges = D.edges || [];
  sel = new Set(); selEdge = null; undoS = []; redoS = []; dirty = false; hover = null; drag = null;
  el.dTitle.value = D.title || '';
  root.hidden = false; isOpen = true;
  document.body.classList.add('dgm-open');
  status('');
  props();
  requestAnimationFrame(() => { fit(); render(); if (opts.generate) generateDialog(); });
};
})();
