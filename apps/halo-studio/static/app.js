/* Halo Studio - shared helpers + layered editor */
'use strict';
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmtBytes = (n) => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB' : (n / 1048576).toFixed(2) + ' MB';

/* ---------------- toast / modal / busy ---------------- */
let toastT;
function toast(msg, err) {
  const t = $('toast');
  t.textContent = msg; t.className = 'toast on' + (err ? ' err' : '');
  clearTimeout(toastT); toastT = setTimeout(() => t.className = 'toast', err ? 6000 : 3200);
}
function modal(title, body, buttons) {
  $('mTitle').textContent = title;
  const mb = $('mBody'); mb.innerHTML = '';
  if (typeof body === 'string') mb.innerHTML = body; else if (body) mb.appendChild(body);
  const bb = $('mBtns'); bb.innerHTML = '';
  (buttons || [{ label: 'Close' }]).forEach(b => {
    const el = document.createElement('button');
    el.className = 'btn' + (b.primary ? ' primary' : '');
    el.textContent = b.label;
    el.onclick = async () => { const keep = b.onClick ? await b.onClick() : false; if (keep !== true) closeModal(); };
    bb.appendChild(el);
  });
  $('modal').classList.remove('hidden');
  setTimeout(() => { const i = mb.querySelector('input,select'); if (i) i.focus(); }, 30);
}
function closeModal() { $('modal').classList.add('hidden'); }
$('modal').addEventListener('pointerdown', e => { if (e.target.id === 'modal') closeModal(); });

let busyTimer;
function busy(on, text) {
  const b = $('busy');
  clearInterval(busyTimer);
  if (!on) { b.classList.remove('on'); return; }
  const t0 = Date.now();
  const upd = () => $('busyText').textContent = text + '  ' + Math.round((Date.now() - t0) / 1000) + 's';
  upd(); busyTimer = setInterval(upd, 500);
  b.classList.add('on');
}

/* ---------------- server ---------------- */
async function api(path, fd, opts = {}) {
  const res = await fetch(path, { method: fd ? 'POST' : 'GET', body: fd, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.error || msg; } catch (e) { }
    throw new Error(msg);
  }
  return res;
}
let INFO = { formats: ['jpeg', 'png'], comfy: { ok: false }, lan_urls: [] };
async function refreshInfo() {
  try {
    INFO = await (await api('/api/info')).json();
    const chip = $('statusChip');
    if (INFO.comfy.ok) {
      chip.className = 'chip ok';
      $('statusText').textContent = 'AI ready · ' + (INFO.comfy.device ? INFO.comfy.device.replace(/^cuda:\d+\s*|\s*:\s*native$/gi, '').slice(0, 30) : 'ComfyUI');
    } else {
      chip.className = 'chip warn';
      $('statusText').textContent = (INFO.rembg ? 'Local AI tools ready' : 'Studio ready') + ' · gen-AI off';
    }
    document.dispatchEvent(new CustomEvent('halo-info', { detail: INFO }));
    aiUiUpdate();
  } catch (e) {
    $('statusChip').className = 'chip'; $('statusText').textContent = 'Server offline';
  }
}

/* ---------------- tabs ---------------- */
function showTab(name) {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + name));
  document.dispatchEvent(new CustomEvent('halo-tab', { detail: name }));
  if (name === 'edit') setTimeout(layoutStage, 0);
  try { localStorage.setItem('halo-tab', name); } catch (e) { }
}
$('tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); });
$('statusChip').onclick = () => showTab('shared');

/* ---------------- image loading ---------------- */
async function loadImageFile(file) {
  try {
    return await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch (e) {
    // HEIC / JPEG XL etc: let the server decode it
    const fd = new FormData(); fd.append('image', file); fd.append('format', 'png');
    const blob = await (await api('/api/convert', fd)).blob();
    return await createImageBitmap(blob);
  }
}
function canvasBlob(c, type = 'image/png', q) { return new Promise(r => c.toBlob(r, type, q)); }
function newCanvas(w, h) { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; }
function cloneCanvas(src) { const c = newCanvas(src.width, src.height); c.getContext('2d').drawImage(src, 0, 0); return c; }
function download(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/* =====================================================================
   EDITOR
   ===================================================================== */
const view = $('view'), vctx = view.getContext('2d');
const overlay = $('overlay'), octx = overlay.getContext('2d');
const wrap = $('stagewrap'), stage = $('stage');
const E = {
  doc: null, zoom: 1, tool: 'brush', sel: null, hist: [], fut: [],
  opts: { size: 24, opacity: 100, soft: 0, tol: 32, allLayers: true, selShape: 'rect', font: 'sans', fsize: 64, bold: true, outline: false },
  adj: { b: 100, c: 100, s: 100, h: 0, bl: 0, extra: '' },
  stroke: null, drag: null, spaceDown: false, cursor: null, dirty: false, uid: 1,
};

function makeLayer(name, w, h) {
  const c = newCanvas(w || E.doc.w, h || E.doc.h);
  return { id: E.uid++, name, canvas: c, ctx: c.getContext('2d'), visible: true, opacity: 1, blend: 'source-over' };
}
const L = () => E.doc && E.doc.layers[E.doc.active];

function newDoc(w, h, bg) {
  E.doc = { w, h, layers: [], active: 0, name: 'untitled' };
  const l = makeLayer('Background');
  if (bg && bg !== 'transparent') { l.ctx.fillStyle = bg; l.ctx.fillRect(0, 0, w, h); }
  E.doc.layers.push(l);
  E.sel = null; E.hist = []; E.fut = []; resetAdj(true);
  $('emptyState').classList.add('hidden'); stage.classList.add('on');
  view.width = w; view.height = h;
  fitZoom(); render(); renderLayers();
}
function openImage(img, name) {
  newDoc(img.width, img.height, 'transparent');
  E.doc.name = (name || 'image').replace(/\.[^.]+$/, '');
  L().name = E.doc.name; L().ctx.drawImage(img, 0, 0);
  render(); renderLayers();
}
function placeImage(img, name) {
  if (!E.doc) return openImage(img, name);
  pushHistory();
  const l = makeLayer((name || 'Image').replace(/\.[^.]+$/, ''));
  const s = Math.min(1, E.doc.w / img.width, E.doc.h / img.height);
  const w = img.width * s, h = img.height * s;
  l.ctx.imageSmoothingQuality = 'high';
  l.ctx.drawImage(img, (E.doc.w - w) / 2, (E.doc.h - h) / 2, w, h);
  E.doc.layers.splice(E.doc.active + 1, 0, l); E.doc.active++;
  render(); renderLayers();
}

/* ---------------- history ---------------- */
function snapshot() {
  const d = E.doc;
  return { w: d.w, h: d.h, active: d.active, name: d.name,
    layers: d.layers.map(l => ({ id: l.id, name: l.name, visible: l.visible, opacity: l.opacity, blend: l.blend, img: cloneCanvas(l.canvas) })) };
}
function restore(s) {
  const d = E.doc; d.w = s.w; d.h = s.h; d.active = s.active; d.name = s.name;
  d.layers = s.layers.map(o => { const c = o.img; return { id: o.id, name: o.name, canvas: c, ctx: c.getContext('2d'), visible: o.visible, opacity: o.opacity, blend: o.blend }; });
  if (view.width !== d.w || view.height !== d.h) { view.width = d.w; view.height = d.h; layoutStage(); }
  render(); renderLayers();
}
function maxHist() {
  const d = E.doc; const bytes = d.w * d.h * 4 * d.layers.length;
  return clamp(Math.floor(900e6 / bytes), 4, 40);
}
function pushHistory() {
  if (!E.doc) return;
  E.hist.push(snapshot()); E.fut = []; E.dirty = true;
  while (E.hist.length > maxHist()) E.hist.shift();
}
function undo() { if (!E.hist.length) return; E.fut.push(snapshot()); restore(E.hist.pop()); }
function redo() { if (!E.fut.length) return; E.hist.push(snapshot()); restore(E.fut.pop()); }
$('btnUndo').onclick = undo; $('btnRedo').onclick = redo;

/* ---------------- rendering ---------------- */
function adjFilter() {
  const a = E.adj;
  let f = '';
  if (a.b !== 100) f += `brightness(${a.b}%) `;
  if (a.c !== 100) f += `contrast(${a.c}%) `;
  if (a.s !== 100) f += `saturate(${a.s}%) `;
  if (a.h !== 0) f += `hue-rotate(${a.h}deg) `;
  if (a.bl > 0) f += `blur(${a.bl}px) `;
  f += a.extra;
  return f.trim() || 'none';
}
const tmpC = newCanvas(1, 1), tmpX = tmpC.getContext('2d');
const adjC = newCanvas(1, 1), adjX = adjC.getContext('2d');
function selPath(ctx, sel, ox = 0, oy = 0) {
  ctx.beginPath();
  if (sel.shape === 'ellipse') ctx.ellipse(sel.x + sel.w / 2 + ox, sel.y + sel.h / 2 + oy, Math.max(.5, sel.w / 2), Math.max(.5, sel.h / 2), 0, 0, Math.PI * 2);
  else ctx.rect(sel.x + ox, sel.y + oy, sel.w, sel.h);
}
function applyStroke(ctx) {
  const s = E.stroke;
  ctx.save();
  if (E.sel) { selPath(ctx, E.sel); ctx.clip(); }
  ctx.globalAlpha = E.opts.opacity / 100;
  ctx.globalCompositeOperation = s.erase ? 'destination-out' : 'source-over';
  if (E.opts.soft > 0) ctx.filter = `blur(${(E.opts.size * E.opts.soft / 200).toFixed(1)}px)`;
  ctx.drawImage(s.canvas, 0, 0);
  ctx.restore();
}
function layerSource(l, i, preview) {
  let src = l.canvas;
  if (preview && i === E.doc.active && E.stroke) {
    if (tmpC.width !== E.doc.w || tmpC.height !== E.doc.h) { tmpC.width = E.doc.w; tmpC.height = E.doc.h; }
    tmpX.clearRect(0, 0, tmpC.width, tmpC.height); tmpX.drawImage(l.canvas, 0, 0);
    applyStroke(tmpX); src = tmpC;
  }
  return src;
}
function drawComposite(ctx, preview) {
  const d = E.doc;
  ctx.clearRect(0, 0, d.w, d.h);
  const f = adjFilter();
  d.layers.forEach((l, i) => {
    if (!l.visible) return;
    let src = layerSource(l, i, preview);
    if (preview && i === d.active && f !== 'none') {
      if (adjC.width !== d.w || adjC.height !== d.h) { adjC.width = d.w; adjC.height = d.h; }
      adjX.save(); adjX.clearRect(0, 0, d.w, d.h);
      if (E.sel) { adjX.drawImage(src, 0, 0); selPath(adjX, E.sel); adjX.clip(); adjX.clearRect(0, 0, d.w, d.h); }
      adjX.filter = f; adjX.drawImage(src, 0, 0); adjX.restore();
      src = adjC;
    }
    ctx.save();
    ctx.globalAlpha = l.opacity;
    ctx.globalCompositeOperation = i === 0 ? 'source-over' : l.blend;
    ctx.drawImage(src, 0, 0);
    ctx.restore();
  });
}
let rq = 0;
function render() { if (!rq) rq = requestAnimationFrame(() => { rq = 0; if (E.doc) { drawComposite(vctx, true); drawOverlay(); } }); }
function compositeCanvas() { const c = newCanvas(E.doc.w, E.doc.h); drawComposite(c.getContext('2d'), false); return c; }
async function compositeBlob() { return canvasBlob(compositeCanvas()); }

/* ---------------- zoom & layout ---------------- */
function layoutStage() {
  if (!E.doc) return;
  const w = Math.round(E.doc.w * E.zoom), h = Math.round(E.doc.h * E.zoom);
  stage.style.width = w + 'px'; stage.style.height = h + 'px';
  const mx = Math.max(24, (wrap.clientWidth - w) / 2), my = Math.max(24, (wrap.clientHeight - h) / 2);
  stage.style.margin = `${my}px ${mx}px`;
  const dpr = window.devicePixelRatio || 1;
  overlay.width = Math.max(1, Math.round(w * dpr)); overlay.height = Math.max(1, Math.round(h * dpr));
  view.classList.toggle('pixel', E.zoom >= 3);
  $('zoomLabel').textContent = Math.round(E.zoom * 100) + '%';
  drawOverlay();
}
function setZoom(z, cx, cy) {
  if (!E.doc) return;
  const old = E.zoom; z = clamp(z, 0.02, 32);
  const r = wrap.getBoundingClientRect();
  cx = cx ?? r.left + wrap.clientWidth / 2; cy = cy ?? r.top + wrap.clientHeight / 2;
  const sr = stage.getBoundingClientRect();
  const dx = (cx - sr.left) / old, dy = (cy - sr.top) / old;
  E.zoom = z; layoutStage();
  const nr = stage.getBoundingClientRect();
  wrap.scrollLeft += (nr.left + dx * z) - cx; wrap.scrollTop += (nr.top + dy * z) - cy;
}
function fitZoom() {
  if (!E.doc) return;
  const z = Math.min((wrap.clientWidth - 48) / E.doc.w, (wrap.clientHeight - 48) / E.doc.h);
  E.zoom = clamp(Math.min(z, E.doc.w < 300 && E.doc.h < 300 ? 8 : 1), 0.02, 32);
  layoutStage();
}
$('zoomIn').onclick = () => setZoom(E.zoom * 1.25);
$('zoomOut').onclick = () => setZoom(E.zoom / 1.25);
$('zoomLabel').onclick = fitZoom;
window.addEventListener('resize', () => {
  if (E.doc && (E.doc.w * E.zoom > wrap.clientWidth - 24 || E.doc.h * E.zoom > wrap.clientHeight - 24)) fitZoom(); else layoutStage();
});
wrap.addEventListener('wheel', e => {
  if (!E.doc || !(e.ctrlKey || e.metaKey || e.altKey)) return;
  e.preventDefault();
  setZoom(E.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY);
}, { passive: false });

/* ---------------- overlay (selection ants, cursor) ---------------- */
let antOff = 0;
setInterval(() => { if (E.sel && E.doc && document.querySelector('#tab-edit.on')) { antOff = (antOff + 1) % 16; drawOverlay(); } }, 110);
function drawOverlay() {
  if (!E.doc) return;
  const dpr = window.devicePixelRatio || 1, z = E.zoom * dpr;
  octx.setTransform(1, 0, 0, 1, 0, 0); octx.clearRect(0, 0, overlay.width, overlay.height);
  octx.setTransform(z, 0, 0, z, 0, 0);
  if (E.sel) {
    let s = E.sel;
    if (E.drag && E.drag.floatOff) s = { ...s, x: s.x + E.drag.floatOff[0], y: s.y + E.drag.floatOff[1] };
    octx.lineWidth = 1 / z * dpr * 1.2;
    octx.setLineDash([5 / E.zoom, 5 / E.zoom]);
    octx.strokeStyle = '#000'; octx.lineDashOffset = -antOff / E.zoom; selPath(octx, s); octx.stroke();
    octx.strokeStyle = '#fff'; octx.lineDashOffset = (-antOff + 5) / E.zoom; selPath(octx, s); octx.stroke();
    octx.setLineDash([]);
  }
  if (E.cursor && (E.tool === 'brush' || E.tool === 'eraser') && !E.spaceDown) {
    octx.lineWidth = 1 / E.zoom;
    octx.beginPath(); octx.arc(E.cursor[0], E.cursor[1], Math.max(0.5, E.opts.size / 2), 0, Math.PI * 2);
    octx.strokeStyle = 'rgba(0,0,0,.7)'; octx.stroke();
    octx.beginPath(); octx.arc(E.cursor[0], E.cursor[1], Math.max(0.5, E.opts.size / 2) + 1 / E.zoom, 0, Math.PI * 2);
    octx.strokeStyle = 'rgba(255,255,255,.8)'; octx.stroke();
  }
}

/* ---------------- tools ---------------- */
const TOOL_KEYS = { v: 'move', m: 'select', b: 'brush', e: 'eraser', g: 'fill', t: 'text', i: 'picker', h: 'hand' };
function setTool(t) {
  E.tool = t;
  document.querySelectorAll('#toolbar button').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
  overlay.style.cursor = { brush: 'none', eraser: 'none', hand: 'grab', move: 'move', text: 'text', picker: 'crosshair', fill: 'crosshair', select: 'crosshair' }[t] || 'default';
  renderToolOpts(); drawOverlay();
}
$('toolbar').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTool(b.dataset.tool); });

function optRange(label, key, min, max, step = 1, suffix = '') {
  return `<label>${label} <output id="o_${key}">${E.opts[key]}${suffix}</output><input type="range" data-k="${key}" data-s="${suffix}" min="${min}" max="${max}" step="${step}" value="${E.opts[key]}"></label>`;
}
function renderToolOpts() {
  const t = E.tool, o = $('toolOpts');
  let h = '';
  if (t === 'brush' || t === 'eraser') h = optRange('Size', 'size', 1, 400, 1, ' px') + optRange('Opacity', 'opacity', 1, 100, 1, '%') + optRange('Softness', 'soft', 0, 100, 1, '%') + '<p class="hint">[ and ] change size. Pen pressure works on tablets.</p>';
  else if (t === 'select') h = `<div class="seg" id="selShape"><button data-v="rect" class="${E.opts.selShape === 'rect' ? 'on' : ''}">Rectangle</button><button data-v="ellipse" class="${E.opts.selShape === 'ellipse' ? 'on' : ''}">Ellipse</button></div>
    <div class="row2"><button class="btn" id="sAll">Select all</button><button class="btn" id="sNone">Deselect</button></div>
    <div class="row2"><button class="btn" id="sCrop">Crop to it</button><button class="btn" id="sLayer">Copy to layer</button></div>
    <div class="row2"><button class="btn" id="sDelete">Delete pixels</button><button class="btn" id="sFillSel">Fill with color</button></div>
    <p class="hint">Hold Shift for a perfect square/circle. Then try <b>Generative fill</b> in the AI panel.</p>`;
  else if (t === 'fill') h = optRange('Tolerance', 'tol', 0, 255) + `<label class="check"><input type="checkbox" id="oAll" ${E.opts.allLayers ? 'checked' : ''}> Detect edges from all layers</label><p class="hint">Great for coloring line art: add a layer under it and fill.</p>`;
  else if (t === 'text') h = optRange('Size', 'fsize', 8, 400, 1, ' px') + `<label>Font <select id="oFont">
      ${[['sans', 'Clean sans'], ['serif', 'Classic serif'], ['impact', 'Meme / Impact'], ['mono', 'Typewriter'], ['hand', 'Handwritten'], ['round', 'Rounded']].map(([v, n]) => `<option value="${v}" ${E.opts.font === v ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
      <label class="check"><input type="checkbox" id="oBold" ${E.opts.bold ? 'checked' : ''}> Bold</label>
      <label class="check"><input type="checkbox" id="oOutline" ${E.opts.outline ? 'checked' : ''}> Black outline</label>
      <p class="hint">Click on the image to type. Each text goes on its own layer.</p>`;
  else if (t === 'move') h = '<p class="hint">Drag to move the current layer. With a selection, only the selected pixels move. Arrow keys nudge 1 px (Shift = 10).</p>';
  else if (t === 'picker') h = '<p class="hint">Click anywhere to grab that color.</p>';
  else if (t === 'hand') h = '<p class="hint">Drag to look around. Ctrl + scroll to zoom. Tip: hold Space with any tool.</p>';
  o.innerHTML = h;
  o.querySelectorAll('input[type=range]').forEach(r => r.oninput = () => { E.opts[r.dataset.k] = +r.value; $('o_' + r.dataset.k).textContent = r.value + r.dataset.s; drawOverlay(); });
  const q = (id) => o.querySelector('#' + id);
  if (q('selShape')) q('selShape').onclick = e => { const b = e.target.closest('button'); if (!b) return; E.opts.selShape = b.dataset.v; renderToolOpts(); };
  if (q('sAll')) { q('sAll').onclick = selectAll; q('sNone').onclick = deselect; q('sCrop').onclick = cropToSel; q('sLayer').onclick = selToLayer; q('sDelete').onclick = deleteSel; q('sFillSel').onclick = fillSel; }
  if (q('oAll')) q('oAll').onchange = e => E.opts.allLayers = e.target.checked;
  if (q('oFont')) { q('oFont').onchange = e => E.opts.font = e.target.value; q('oBold').onchange = e => E.opts.bold = e.target.checked; q('oOutline').onchange = e => E.opts.outline = e.target.checked; }
}
const FONTS = { sans: '"Segoe UI",Roboto,Helvetica,Arial,sans-serif', serif: 'Georgia,"Times New Roman",serif', impact: 'Impact,Haettenschweiler,"Arial Black",sans-serif', mono: '"Courier New",Consolas,monospace', hand: '"Segoe Print","Bradley Hand","Comic Sans MS",cursive', round: '"Arial Rounded MT Bold","Varela Round","Nunito",sans-serif' };

function toDoc(e) {
  const r = overlay.getBoundingClientRect();
  return [(e.clientX - r.left) * E.doc.w / r.width, (e.clientY - r.top) * E.doc.h / r.height];
}
function normRect(a, b, square) {
  let w = b[0] - a[0], h = b[1] - a[1];
  if (square) { const m = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * m; h = Math.sign(h || 1) * m; }
  return { x: Math.round(Math.min(a[0], a[0] + w)), y: Math.round(Math.min(a[1], a[1] + h)), w: Math.round(Math.abs(w)), h: Math.round(Math.abs(h)) };
}

overlay.addEventListener('pointerdown', e => {
  if (!E.doc || e.button === 2) return;
  overlay.setPointerCapture(e.pointerId);
  const p = toDoc(e);
  const tool = (E.spaceDown || e.button === 1) ? 'hand' : E.tool;
  if (tool === 'hand') { E.drag = { pan: true, x: e.clientX, y: e.clientY, sl: wrap.scrollLeft, st: wrap.scrollTop }; wrap.classList.add('panning'); return; }
  const l = L();
  if (tool === 'brush' || tool === 'eraser') {
    if (!l.visible) { toast('That layer is hidden - click its eye to show it'); return; }
    pushHistory();
    const c = newCanvas(E.doc.w, E.doc.h), x = c.getContext('2d');
    x.lineCap = x.lineJoin = 'round'; x.strokeStyle = x.fillStyle = $('color').value;
    E.stroke = { canvas: c, ctx: x, last: p, erase: tool === 'eraser' };
    const pr = e.pointerType === 'pen' ? Math.max(0.1, e.pressure) : 1;
    x.beginPath(); x.arc(p[0], p[1], E.opts.size * pr / 2, 0, Math.PI * 2); x.fill();
    render();
  } else if (tool === 'select') {
    E.drag = { sel: true, start: p }; E.sel = null; drawOverlay();
  } else if (tool === 'move') {
    pushHistory();
    const orig = cloneCanvas(l.canvas);
    if (E.sel) {
      const float = newCanvas(E.doc.w, E.doc.h), fx = float.getContext('2d');
      fx.save(); selPath(fx, E.sel); fx.clip(); fx.drawImage(orig, 0, 0); fx.restore();
      const base = cloneCanvas(orig), bx = base.getContext('2d');
      bx.save(); selPath(bx, E.sel); bx.clip(); bx.clearRect(0, 0, E.doc.w, E.doc.h); bx.restore();
      E.drag = { move: true, start: p, base, float, floatOff: [0, 0] };
    } else E.drag = { move: true, start: p, base: orig };
  } else if (tool === 'picker') {
    const d = vctx.getImageData(clamp(Math.floor(p[0]), 0, E.doc.w - 1), clamp(Math.floor(p[1]), 0, E.doc.h - 1), 1, 1).data;
    $('color').value = '#' + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, '0')).join('');
    toast('Picked ' + $('color').value);
  } else if (tool === 'fill') floodFill(p);
  else if (tool === 'text') { e.preventDefault(); startText(e, p); }
});
overlay.addEventListener('pointermove', e => {
  if (!E.doc) return;
  const p = toDoc(e);
  E.cursor = p;
  const d = E.drag;
  if (d && d.pan) { wrap.scrollLeft = d.sl - (e.clientX - d.x); wrap.scrollTop = d.st - (e.clientY - d.y); return; }
  if (E.stroke) {
    const s = E.stroke, x = s.ctx;
    const pts = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
    for (const ev of pts) {
      const q = toDoc(ev);
      const pr = ev.pointerType === 'pen' ? Math.max(0.1, ev.pressure) : 1;
      x.lineWidth = E.opts.size * pr;
      x.beginPath(); x.moveTo(s.last[0], s.last[1]); x.lineTo(q[0], q[1]); x.stroke();
      s.last = q;
    }
    render(); return;
  }
  if (d && d.sel) { E.sel = { ...normRect(d.start, p, e.shiftKey), shape: E.opts.selShape }; drawOverlay(); return; }
  if (d && d.move) {
    const dx = Math.round(p[0] - d.start[0]), dy = Math.round(p[1] - d.start[1]);
    const l = L(); l.ctx.clearRect(0, 0, E.doc.w, E.doc.h);
    if (d.float) { l.ctx.drawImage(d.base, 0, 0); l.ctx.drawImage(d.float, dx, dy); d.floatOff = [dx, dy]; }
    else l.ctx.drawImage(d.base, dx, dy);
    render(); return;
  }
  if (E.tool === 'brush' || E.tool === 'eraser') drawOverlay();
});
function endPointer() {
  if (E.stroke) { applyStroke(L().ctx); E.stroke = null; render(); updateThumbs(); }
  const d = E.drag;
  if (d && d.sel && E.sel && (E.sel.w < 2 || E.sel.h < 2)) E.sel = null;
  if (d && d.move) { if (d.float && E.sel) { E.sel.x += d.floatOff[0]; E.sel.y += d.floatOff[1]; } updateThumbs(); }
  E.drag = null; wrap.classList.remove('panning'); drawOverlay();
}
overlay.addEventListener('pointerup', endPointer);
overlay.addEventListener('pointercancel', endPointer);
overlay.addEventListener('pointerleave', () => { E.cursor = null; drawOverlay(); });
overlay.addEventListener('contextmenu', e => e.preventDefault());

/* ---------------- fill bucket ---------------- */
function floodFill(p) {
  const w = E.doc.w, h = E.doc.h, x0 = Math.floor(p[0]), y0 = Math.floor(p[1]);
  if (x0 < 0 || y0 < 0 || x0 >= w || y0 >= h) return;
  pushHistory();
  const src = (E.opts.allLayers ? compositeCanvas() : L().canvas).getContext('2d').getImageData(0, 0, w, h).data;
  const i0 = (y0 * w + x0) * 4, tr = src[i0], tg = src[i0 + 1], tb = src[i0 + 2], ta = src[i0 + 3];
  const tol = E.opts.tol, mask = new Uint8Array(w * h);
  const match = (i) => { const j = i * 4; return Math.abs(src[j] - tr) <= tol && Math.abs(src[j + 1] - tg) <= tol && Math.abs(src[j + 2] - tb) <= tol && Math.abs(src[j + 3] - ta) <= tol; };
  const stack = [x0, y0];
  while (stack.length) {
    const y = stack.pop(); let x = stack.pop();
    let i = y * w + x;
    while (x >= 0 && !mask[i] && match(i)) { x--; i--; }
    x++; i++;
    let up = false, dn = false;
    while (x < w && !mask[i] && match(i)) {
      mask[i] = 1;
      if (y > 0) { const k = i - w; if (!mask[k] && match(k)) { if (!up) { stack.push(x, y - 1); up = true; } } else up = false; }
      if (y < h - 1) { const k = i + w; if (!mask[k] && match(k)) { if (!dn) { stack.push(x, y + 1); dn = true; } } else dn = false; }
      x++; i++;
    }
  }
  const col = $('color').value, r = parseInt(col.slice(1, 3), 16), g = parseInt(col.slice(3, 5), 16), b = parseInt(col.slice(5, 7), 16);
  const fc = newCanvas(w, h), fx = fc.getContext('2d'), id = fx.createImageData(w, h), d = id.data;
  for (let i = 0; i < w * h; i++) if (mask[i]) { d[i * 4] = r; d[i * 4 + 1] = g; d[i * 4 + 2] = b; d[i * 4 + 3] = 255; }
  fx.putImageData(id, 0, 0);
  // grow by 1px to hide anti-aliased seams
  const ctx = L().ctx; ctx.save();
  if (E.sel) { selPath(ctx, E.sel); ctx.clip(); }
  ctx.globalAlpha = E.opts.opacity / 100;
  for (const [dx, dy] of [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]]) ctx.drawImage(fc, dx, dy);
  ctx.restore(); render(); updateThumbs();
}

/* ---------------- text ---------------- */
let textAt = null;
function startText(e, p) {
  const ti = $('textInput');
  if (textAt) commitText();
  textAt = p;
  const fs = E.opts.fsize * E.zoom;
  Object.assign(ti.style, { left: e.clientX + 'px', top: (e.clientY - fs * 0.6) + 'px', fontSize: fs + 'px', fontFamily: FONTS[E.opts.font], fontWeight: E.opts.bold ? '700' : '400', color: $('color').value });
  ti.value = ''; ti.classList.remove('hidden'); ti.focus(); setTimeout(() => ti.focus(), 30);
}
function commitText() {
  const ti = $('textInput'); ti.classList.add('hidden');
  const txt = ti.value; const p = textAt; textAt = null;
  if (!txt.trim() || !p) return;
  pushHistory();
  const l = makeLayer('Text: ' + txt.slice(0, 18));
  const x = l.ctx, fs = E.opts.fsize;
  x.font = `${E.opts.bold ? 700 : 400} ${fs}px ${FONTS[E.opts.font]}`;
  x.textBaseline = 'middle'; x.fillStyle = $('color').value;
  txt.split('\n').forEach((line, i) => {
    const y = p[1] + i * fs * 1.15;
    if (E.opts.outline) { x.lineWidth = Math.max(2, fs / 9); x.strokeStyle = '#000'; x.lineJoin = 'round'; x.strokeText(line, p[0], y); }
    x.fillText(line, p[0], y);
  });
  E.doc.layers.splice(E.doc.active + 1, 0, l); E.doc.active++;
  render(); renderLayers();
}
$('textInput').addEventListener('blur', commitText);
$('textInput').addEventListener('keydown', e => { if (e.key === 'Escape') { $('textInput').value = ''; $('textInput').blur(); } if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) $('textInput').blur(); e.stopPropagation(); });

/* ---------------- selection actions ---------------- */
function selectAll() { if (!E.doc) return; E.sel = { x: 0, y: 0, w: E.doc.w, h: E.doc.h, shape: 'rect' }; drawOverlay(); }
function deselect() { E.sel = null; drawOverlay(); }
function selBounds() {
  const s = E.sel; const x = clamp(s.x, 0, E.doc.w), y = clamp(s.y, 0, E.doc.h);
  return { x, y, w: clamp(s.x + s.w, 0, E.doc.w) - x, h: clamp(s.y + s.h, 0, E.doc.h) - y };
}
function cropRect(r) {
  if (!r || r.w < 1 || r.h < 1) return toast('Make a selection first', true);
  pushHistory();
  E.doc.layers.forEach(l => { const c = newCanvas(r.w, r.h); c.getContext('2d').drawImage(l.canvas, -r.x, -r.y); l.canvas = c; l.ctx = c.getContext('2d'); });
  E.doc.w = r.w; E.doc.h = r.h; view.width = r.w; view.height = r.h; E.sel = null;
  fitZoom(); render(); renderLayers();
}
function cropToSel() { if (!E.sel) return toast('Draw a selection with the Select tool first', true); cropRect(selBounds()); }
function selToLayer() {
  if (!E.sel) return toast('Make a selection first', true);
  pushHistory();
  const l = makeLayer(L().name + ' (copy)');
  l.ctx.save(); selPath(l.ctx, E.sel); l.ctx.clip(); l.ctx.drawImage(L().canvas, 0, 0); l.ctx.restore();
  E.doc.layers.splice(E.doc.active + 1, 0, l); E.doc.active++; render(); renderLayers();
}
function deleteSel() {
  if (!E.sel) return;
  pushHistory(); const x = L().ctx; x.save(); selPath(x, E.sel); x.clip(); x.clearRect(0, 0, E.doc.w, E.doc.h); x.restore(); render(); updateThumbs();
}
function fillSel() {
  if (!E.sel) return toast('Make a selection first', true);
  pushHistory(); const x = L().ctx; x.save(); selPath(x, E.sel); x.fillStyle = $('color').value; x.globalAlpha = E.opts.opacity / 100; x.fill(); x.restore(); render(); updateThumbs();
}

/* ---------------- adjustments ---------------- */
const ADJ = { adjB: 'b', adjC: 'c', adjS: 's', adjH: 'h', adjBl: 'bl' };
Object.keys(ADJ).forEach(id => $(id).addEventListener('input', () => { E.adj[ADJ[id]] = +$(id).value; render(); }));
function resetAdj(silent) {
  E.adj = { b: 100, c: 100, s: 100, h: 0, bl: 0, extra: '' };
  $('adjB').value = 100; $('adjC').value = 100; $('adjS').value = 100; $('adjH').value = 0; $('adjBl').value = 0;
  if (!silent) render();
}
$('adjReset').onclick = () => resetAdj();
$('adjApply').onclick = () => {
  if (!E.doc) return;
  const f = adjFilter(); if (f === 'none') return toast('Move a slider or pick a preset first');
  pushHistory();
  const l = L(), c = newCanvas(E.doc.w, E.doc.h), x = c.getContext('2d');
  x.filter = f; x.drawImage(l.canvas, 0, 0); x.filter = 'none';
  if (E.sel) {
    const keep = cloneCanvas(l.canvas), kx = keep.getContext('2d');
    kx.save(); selPath(kx, E.sel); kx.clip(); kx.clearRect(0, 0, E.doc.w, E.doc.h); kx.drawImage(c, 0, 0); kx.restore();
    l.ctx.clearRect(0, 0, E.doc.w, E.doc.h); l.ctx.drawImage(keep, 0, 0);
  } else { l.ctx.clearRect(0, 0, E.doc.w, E.doc.h); l.ctx.drawImage(c, 0, 0); }
  resetAdj(); updateThumbs(); toast('Applied to ' + l.name);
};
const PRESETS = {
  bw: { s: 100, extra: 'grayscale(1)', c: 110 }, sepia: { extra: 'sepia(.85)', c: 105 }, vivid: { s: 150, c: 115, b: 103 },
  warm: { extra: 'sepia(.22)', s: 120, b: 103 }, cool: { h: 12, s: 92, b: 102, extra: 'saturate(1.05)' }, fade: { c: 80, b: 112, s: 75 },
  invert: { extra: 'invert(1)' },
};
$('presets').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b || !E.doc) return;
  if (b.dataset.p === 'sharpen') return sharpenLayer();
  resetAdj(true); Object.assign(E.adj, PRESETS[b.dataset.p]);
  $('adjB').value = E.adj.b; $('adjC').value = E.adj.c; $('adjS').value = E.adj.s; $('adjH').value = E.adj.h; render();
  toast('Preview only - press Apply to keep it');
});
function sharpenLayer() {
  pushHistory();
  const l = L(), w = E.doc.w, h = E.doc.h, id = l.ctx.getImageData(0, 0, w, h), s = id.data, o = new Uint8ClampedArray(s);
  const k = 0.6;
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = (y * w + x) * 4;
    for (let c = 0; c < 3; c++) {
      const v = s[i + c] * (1 + 4 * k) - k * (s[i + c - 4] + s[i + c + 4] + s[i + c - w * 4] + s[i + c + w * 4]);
      o[i + c] = v;
    }
  }
  id.data.set(o); l.ctx.putImageData(id, 0, 0); render(); updateThumbs(); toast('Sharpened');
}

/* ---------------- layers panel ---------------- */
function renderLayers() {
  const ul = $('layerList'); ul.innerHTML = '';
  if (!E.doc) return;
  for (let i = E.doc.layers.length - 1; i >= 0; i--) {
    const l = E.doc.layers[i];
    const li = document.createElement('li'); if (i === E.doc.active) li.className = 'on';
    const eye = document.createElement('button'); eye.className = 'eye' + (l.visible ? '' : ' off'); eye.textContent = '👁'; eye.title = 'Show / hide';
    eye.onclick = ev => { ev.stopPropagation(); pushHistory(); l.visible = !l.visible; render(); renderLayers(); };
    const th = newCanvas(80, 60); th.className = 'checker'; l.thumb = th;
    const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = l.name;
    nm.ondblclick = () => renameLayer(l);
    li.append(eye, th, nm);
    li.onclick = () => { E.doc.active = i; renderLayers(); };
    ul.appendChild(li);
  }
  updateThumbs(true);
  $('layerOpacity').value = Math.round(L().opacity * 100); $('layerBlend').value = L().blend;
}
let thumbT;
function updateThumbs(now) {
  clearTimeout(thumbT);
  thumbT = setTimeout(() => {
    if (!E.doc) return;
    E.doc.layers.forEach(l => {
      if (!l.thumb) return;
      const t = l.thumb, x = t.getContext('2d'), s = Math.min(t.width / E.doc.w, t.height / E.doc.h);
      x.clearRect(0, 0, t.width, t.height);
      x.drawImage(l.canvas, (t.width - E.doc.w * s) / 2, (t.height - E.doc.h * s) / 2, E.doc.w * s, E.doc.h * s);
    });
  }, now ? 0 : 150);
}
function renameLayer(l) {
  const inp = document.createElement('input'); inp.value = l.name;
  modal('Rename layer', inp, [{ label: 'Cancel' }, { label: 'Rename', primary: true, onClick: () => { l.name = inp.value || l.name; renderLayers(); } }]);
  inp.onkeydown = e => { if (e.key === 'Enter') { l.name = inp.value || l.name; renderLayers(); closeModal(); } };
}
let opHist = false;
$('layerOpacity').addEventListener('input', () => { if (!E.doc) return; if (!opHist) { pushHistory(); opHist = true; } L().opacity = $('layerOpacity').value / 100; render(); });
$('layerOpacity').addEventListener('change', () => opHist = false);
$('layerBlend').onchange = () => { if (!E.doc) return; pushHistory(); L().blend = $('layerBlend').value; render(); };
$('lAdd').onclick = () => { if (!E.doc) return; pushHistory(); const l = makeLayer('Layer ' + (E.doc.layers.length + 1)); E.doc.layers.splice(E.doc.active + 1, 0, l); E.doc.active++; renderLayers(); };
$('lDup').onclick = dupLayer;
function dupLayer() {
  if (!E.doc) return; pushHistory();
  const s = L(), l = makeLayer(s.name + ' copy'); l.ctx.drawImage(s.canvas, 0, 0); l.opacity = s.opacity; l.blend = s.blend;
  E.doc.layers.splice(E.doc.active + 1, 0, l); E.doc.active++; render(); renderLayers();
}
$('lDel').onclick = () => {
  if (!E.doc) return;
  if (E.doc.layers.length === 1) return toast('An image needs at least one layer');
  pushHistory(); E.doc.layers.splice(E.doc.active, 1); E.doc.active = Math.max(0, E.doc.active - 1); render(); renderLayers();
};
$('lUp').onclick = () => { const d = E.doc; if (!d || d.active >= d.layers.length - 1) return; pushHistory(); const a = d.active; [d.layers[a], d.layers[a + 1]] = [d.layers[a + 1], d.layers[a]]; d.active++; render(); renderLayers(); };
$('lDown').onclick = () => { const d = E.doc; if (!d || d.active === 0) return; pushHistory(); const a = d.active; [d.layers[a], d.layers[a - 1]] = [d.layers[a - 1], d.layers[a]]; d.active--; render(); renderLayers(); };
$('lMerge').onclick = () => {
  const d = E.doc; if (!d || d.active === 0) return toast('Nothing below to merge into');
  pushHistory(); const top = d.layers[d.active], below = d.layers[d.active - 1];
  below.ctx.save(); below.ctx.globalAlpha = top.opacity; below.ctx.globalCompositeOperation = top.blend; if (top.visible) below.ctx.drawImage(top.canvas, 0, 0); below.ctx.restore();
  d.layers.splice(d.active, 1); d.active--; render(); renderLayers();
};
$('lFlat').onclick = flatten;
function flatten() {
  const d = E.doc; if (!d) return; pushHistory();
  const c = compositeCanvas(); const l = makeLayer('Flattened'); l.ctx.drawImage(c, 0, 0);
  d.layers = [l]; d.active = 0; render(); renderLayers();
}

/* ---------------- image menu ---------------- */
function transformAll(nw, nh, fn) {
  pushHistory();
  E.doc.layers.forEach(l => { const c = newCanvas(nw, nh), x = c.getContext('2d'); x.imageSmoothingQuality = 'high'; fn(x, l.canvas); l.canvas = c; l.ctx = x; });
  E.doc.w = nw; E.doc.h = nh; view.width = nw; view.height = nh; E.sel = null;
  fitZoom(); render(); renderLayers();
}
$('imgMenu').onchange = () => {
  const v = $('imgMenu').value; $('imgMenu').value = '';
  if (!E.doc) return toast('Open an image first');
  const { w, h } = E.doc;
  if (v === 'rotL') transformAll(h, w, (x, c) => { x.translate(0, w); x.rotate(-Math.PI / 2); x.drawImage(c, 0, 0); });
  if (v === 'rotR') transformAll(h, w, (x, c) => { x.translate(h, 0); x.rotate(Math.PI / 2); x.drawImage(c, 0, 0); });
  if (v === 'flipH') transformAll(w, h, (x, c) => { x.translate(w, 0); x.scale(-1, 1); x.drawImage(c, 0, 0); });
  if (v === 'flipV') transformAll(w, h, (x, c) => { x.translate(0, h); x.scale(1, -1); x.drawImage(c, 0, 0); });
  if (v === 'crop') cropToSel();
  if (v === 'trim') {
    const d = compositeCanvas().getContext('2d').getImageData(0, 0, w, h).data;
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (d[(y * w + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    if (x1 < 0) return toast('The image is empty');
    cropRect({ x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 });
  }
  if (v === 'resize') {
    const box = document.createElement('div');
    box.innerHTML = `<div class="row2"><label>Width <input type="number" id="rzW" value="${w}"></label><label>Height <input type="number" id="rzH" value="${h}"></label></div>
      <label class="check"><input type="checkbox" id="rzLock" checked> Keep proportions</label>
      <div class="presets">${[25, 50, 75, 150, 200].map(p => `<button data-p="${p}">${p}%</button>`).join('')}</div>`;
    const W = box.querySelector('#rzW'), H = box.querySelector('#rzH'), lock = box.querySelector('#rzLock');
    W.oninput = () => { if (lock.checked) H.value = Math.round(W.value * h / w); };
    H.oninput = () => { if (lock.checked) W.value = Math.round(H.value * w / h); };
    box.querySelector('.presets').onclick = e => { const b = e.target.closest('button'); if (!b) return; W.value = Math.round(w * b.dataset.p / 100); H.value = Math.round(h * b.dataset.p / 100); };
    modal('Resize image', box, [{ label: 'Cancel' }, { label: 'Resize', primary: true, onClick: () => {
      const nw = clamp(+W.value | 0, 1, 20000), nh = clamp(+H.value | 0, 1, 20000);
      transformAll(nw, nh, (x, c) => x.drawImage(c, 0, 0, nw, nh));
    } }]);
  }
};

/* ---------------- new / open / export ---------------- */
$('btnNew').onclick = $('emptyNew').onclick = (e) => {
  e && e.preventDefault && e.preventDefault();
  const sizes = [['1920×1080', 'HD / desktop', 1920, 1080], ['1080×1080', 'Square post', 1080, 1080], ['1080×1920', 'Phone / story', 1080, 1920], ['3000×2000', 'Photo print', 3000, 2000], ['1280×720', 'YouTube thumbnail', 1280, 720], ['512×512', 'Icon / sticker', 512, 512]];
  const box = document.createElement('div');
  box.innerHTML = `<div class="sizegrid">${sizes.map((s, i) => `<button data-i="${i}" class="${i ? '' : 'on'}">${s[0]}<small>${s[1]}</small></button>`).join('')}</div>
    <div class="row2"><label>Width <input type="number" id="nW" value="1920"></label><label>Height <input type="number" id="nH" value="1080"></label></div>
    <label>Background <select id="nBg"><option value="#ffffff">White</option><option value="transparent">Transparent</option><option value="#000000">Black</option><option value="color">Current brush color</option></select></label>`;
  box.querySelector('.sizegrid').onclick = ev => { const b = ev.target.closest('button'); if (!b) return; box.querySelectorAll('.sizegrid button').forEach(x => x.classList.remove('on')); b.classList.add('on'); const s = sizes[b.dataset.i]; box.querySelector('#nW').value = s[2]; box.querySelector('#nH').value = s[3]; };
  modal('New canvas', box, [{ label: 'Cancel' }, { label: 'Create', primary: true, onClick: () => {
    let bg = box.querySelector('#nBg').value; if (bg === 'color') bg = $('color').value;
    newDoc(clamp(+box.querySelector('#nW').value | 0, 1, 20000), clamp(+box.querySelector('#nH').value | 0, 1, 20000), bg);
  } }]);
};
$('btnOpen').onclick = $('emptyOpen').onclick = (e) => { e && e.preventDefault && e.preventDefault(); $('fileOpen').click(); };
$('btnPlace').onclick = () => $('filePlace').click();
$('fileOpen').onchange = async e => { const f = e.target.files[0]; e.target.value = ''; if (f) openFile(f); };
$('filePlace').onchange = async e => { for (const f of e.target.files) { try { placeImage(await loadImageFile(f), f.name); } catch (er) { toast(f.name + ': ' + er.message, true); } } e.target.value = ''; };
async function openFile(f) {
  try { busy(true, 'Opening…'); openImage(await loadImageFile(f), f.name); } catch (er) { toast('Could not open ' + f.name + ': ' + er.message, true); } finally { busy(false); }
}
['dragenter', 'dragover'].forEach(ev => wrap.addEventListener(ev, e => { e.preventDefault(); wrap.classList.add('dragover'); }));
['dragleave', 'drop'].forEach(ev => wrap.addEventListener(ev, e => { e.preventDefault(); wrap.classList.remove('dragover'); }));
wrap.addEventListener('drop', async e => {
  const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/') || /\.(heic|heif|jxl|avif)$/i.test(f.name));
  if (!files.length) return;
  if (!E.doc) { await openFile(files.shift()); }
  for (const f of files) { try { placeImage(await loadImageFile(f), f.name); } catch (er) { toast(er.message, true); } }
});
document.addEventListener('paste', async e => {
  if (!document.querySelector('#tab-edit.on') || /INPUT|TEXTAREA/.test(document.activeElement.tagName)) return;
  for (const it of e.clipboardData.items) if (it.type.startsWith('image/')) { const img = await createImageBitmap(it.getAsFile()); E.doc ? placeImage(img, 'Pasted') : openImage(img, 'pasted'); return; }
});

$('btnExport').onclick = () => {
  if (!E.doc) return toast('Nothing to export yet');
  const names = { png: 'PNG (lossless)', jpeg: 'JPEG (photos)', webp: 'WebP', avif: 'AVIF (smallest)', jxl: 'JPEG XL', png8: 'PNG-8 (tiny, 256 colors)' };
  const box = document.createElement('div');
  box.innerHTML = `<label>File name <input id="xName" value="${E.doc.name || 'image'}"></label>
    <label>Format <select id="xFmt">${['png', ...INFO.formats.filter(f => f !== 'png')].map(f => `<option value="${f}">${names[f] || f}</option>`).join('')}</select></label>
    <label id="xQRow">Quality <output id="xQv">88</output><input type="range" id="xQ" min="10" max="100" value="88"></label>
    <label class="check"><input type="checkbox" id="xShare"> Also save to the network gallery</label>
    <p class="hint">${E.doc.w} × ${E.doc.h} px · ${E.doc.layers.length} layer(s) flattened on export.</p>`;
  const q = box.querySelector('#xQ'), fmt = box.querySelector('#xFmt');
  q.oninput = () => box.querySelector('#xQv').textContent = q.value;
  fmt.onchange = () => box.querySelector('#xQRow').classList.toggle('hidden', /png/.test(fmt.value));
  fmt.onchange();
  modal('Export', box, [{ label: 'Cancel' }, { label: 'Download', primary: true, onClick: async () => {
    const name = box.querySelector('#xName').value || 'image', f = fmt.value;
    let blob = await compositeBlob(), ext = 'png';
    if (f !== 'png') {
      const fd = new FormData(); fd.append('image', blob, 'x.png'); fd.append('format', f); fd.append('quality', q.value); fd.append('name', name);
      try { blob = await (await api('/api/convert', fd)).blob(); } catch (er) { toast(er.message, true); return; }
      ext = { jpeg: 'jpg', png8: 'png' }[f] || f;
    }
    download(blob, name + '.' + ext);
    if (box.querySelector('#xShare').checked) shareBlob(blob, name + '.' + ext);
    toast('Saved ' + name + '.' + ext + ' · ' + fmtBytes(blob.size));
  } }]);
};
async function shareBlob(blob, name) {
  const fd = new FormData(); fd.append('files', blob, name);
  try { await api('/api/shared', fd); toast('Shared on the network: ' + name); document.dispatchEvent(new Event('halo-shared')); } catch (e) { toast(e.message, true); }
}
$('sendMenu').onchange = async () => {
  const v = $('sendMenu').value; $('sendMenu').value = '';
  if (!E.doc) return toast('Open an image first');
  const blob = await compositeBlob(), name = (E.doc.name || 'image') + '.png';
  if (v === 'shared') return shareBlob(blob, name);
  window.HaloTools && window.HaloTools.receive(v, blob, name);
  showTab(v);
};

/* ---------------- AI ---------------- */
function aiUiUpdate() {
  const ok = INFO.comfy && INFO.comfy.ok;
  const tag = $('aiTag');
  tag.className = 'aitag' + (ok ? ' ok' : '');
  tag.textContent = ok ? (INFO.comfy.vram_gb ? INFO.comfy.vram_gb + ' GB GPU' : 'GPU ready') : (INFO.rembg ? 'basic' : 'setup needed');
  const sel = $('aiModel'), cur = sel.value;
  sel.innerHTML = '<option value="">Auto model</option>' + ((INFO.comfy && INFO.comfy.checkpoints) || []).map(c => `<option value="${c}">${c.replace(/\.(safetensors|ckpt)$/, '')}</option>`).join('');
  sel.value = cur;
  ['aiFill', 'aiErase', 'aiGen', 'emptyGen'].forEach(id => $(id).title = ok ? '' : 'Needs ComfyUI running on the Halo Studio PC - see the Network tab');
  $('aiHint').innerHTML = ok ? 'Select an area with the <b>Select</b> tool, type a prompt, then <b>Generative fill</b>. Everything runs on your own PC.'
    : 'Generative fill &amp; AI images need <b>ComfyUI</b> running on the server PC (it uses the Radeon GPU and its big shared memory). Auto-fix and Upscale work now' + (INFO.rembg ? ', and so does Remove background.' : '; Remove background works after running <b>Install-AI-Extras</b>.');
}
async function aiCall(path, fd, label) {
  busy(true, label);
  try { const r = await api(path, fd); const img = await createImageBitmap(await r.blob()); return { img, r }; }
  finally { busy(false); }
}
function aiErr(e) { toast(e.message, true); }
async function layerBlob(l) { return canvasBlob(l.canvas); }
$('aiRemoveBg').onclick = async () => {
  if (!E.doc) return toast('Open an image first');
  const fd = new FormData(); fd.append('image', await layerBlob(L()), 'l.png');
  try {
    const { img, r } = await aiCall('/api/ai/removebg', fd, 'Cutting out the subject…');
    pushHistory(); const l = L(); l.ctx.clearRect(0, 0, E.doc.w, E.doc.h); l.ctx.drawImage(img, 0, 0); render(); updateThumbs();
    toast('Background removed in ' + (r.headers.get('X-AI-Ms') / 1000).toFixed(1) + 's on ' + (r.headers.get('X-AI-Engine') || 'CPU').replace('ExecutionProvider', ''));
  } catch (e) { aiErr(e); }
};
$('aiFix').onclick = async () => {
  if (!E.doc) return toast('Open an image first');
  const fd = new FormData(); fd.append('image', await layerBlob(L()), 'l.png');
  try { const { img } = await aiCall('/api/enhance', fd, 'Fixing levels and color…'); pushHistory(); const l = L(); l.ctx.clearRect(0, 0, E.doc.w, E.doc.h); l.ctx.drawImage(img, 0, 0); render(); updateThumbs(); toast('Auto-fixed'); } catch (e) { aiErr(e); }
};
async function upscale(f) {
  if (!E.doc) return toast('Open an image first');
  if (E.doc.w * E.doc.h * f * f > 120e6) return toast('That would be too big - crop or shrink first', true);
  const fd = new FormData(); fd.append('image', await compositeBlob(), 'c.png'); fd.append('factor', f);
  try {
    const { img, r } = await aiCall('/api/ai/upscale', fd, 'Upscaling ' + f + '×…');
    pushHistory(); const name = E.doc.name;
    E.doc.w = img.width; E.doc.h = img.height; view.width = img.width; view.height = img.height;
    const l = makeLayer('Upscaled ' + f + '×'); l.ctx.drawImage(img, 0, 0); E.doc.layers = [l]; E.doc.active = 0; E.doc.name = name; E.sel = null;
    fitZoom(); render(); renderLayers(); toast(`Now ${img.width}×${img.height} · ${r.headers.get('X-AI-Engine')}`);
  } catch (e) { aiErr(e); }
}
$('aiUp2').onclick = () => upscale(2); $('aiUp4').onclick = () => upscale(4);
function maskBlob() {
  const c = newCanvas(E.doc.w, E.doc.h), x = c.getContext('2d');
  x.fillStyle = '#000'; x.fillRect(0, 0, c.width, c.height); x.fillStyle = '#fff'; selPath(x, E.sel); x.fill();
  return canvasBlob(c);
}
async function genFill(prompt, label) {
  if (!E.doc) return toast('Open an image first');
  if (!E.sel) { setTool('select'); return toast('First draw a selection around the area to change', true); }
  const fd = new FormData();
  fd.append('image', await compositeBlob(), 'c.png'); fd.append('mask', await maskBlob(), 'm.png');
  fd.append('prompt', prompt); fd.append('model', $('aiModel').value); fd.append('speed', $('aiSpeed').value);
  try {
    const { img, r } = await aiCall('/api/ai/fill', fd, label);
    pushHistory(); const l = makeLayer('AI: ' + (prompt || 'erase').slice(0, 20)); l.ctx.drawImage(img, 0, 0);
    E.doc.layers.splice(E.doc.active + 1, 0, l); E.doc.active++; render(); renderLayers();
    toast('Done in ' + (r.headers.get('X-AI-Ms') / 1000).toFixed(1) + 's - it is on its own layer, so you can erase or hide it');
  } catch (e) { aiErr(e); }
}
$('aiFill').onclick = () => { const p = $('aiPrompt').value.trim(); if (!p) return toast('Type what should appear in the selection', true); genFill(p, 'Painting "' + p.slice(0, 30) + '"…'); };
$('aiErase').onclick = () => genFill('', 'Erasing the selected object…');
async function generate(prompt, asNewDoc) {
  if (!prompt) return toast('Describe the image first', true);
  const [w, h] = $('aiSize').value.split('x');
  const fd = new FormData(); fd.append('prompt', prompt); fd.append('width', w); fd.append('height', h); fd.append('model', $('aiModel').value); fd.append('speed', $('aiSpeed').value);
  try {
    const { img, r } = await aiCall('/api/ai/generate', fd, 'Imagining "' + prompt.slice(0, 30) + '"…');
    if (asNewDoc || !E.doc) openImage(img, prompt.slice(0, 30)); else placeImage(img, 'AI: ' + prompt.slice(0, 20));
    toast('Made in ' + (r.headers.get('X-AI-Ms') / 1000).toFixed(1) + 's with ' + r.headers.get('X-AI-Model'));
  } catch (e) { aiErr(e); }
}
$('aiGen').onclick = () => generate($('aiPrompt').value.trim(), false);
$('emptyGen').onclick = () => generate($('emptyPrompt').value.trim(), true);
$('emptyPrompt').onkeydown = e => { if (e.key === 'Enter') $('emptyGen').click(); };

/* ---------------- keyboard ---------------- */
document.addEventListener('keydown', e => {
  if (!document.querySelector('#tab-edit.on')) return;
  const tag = document.activeElement.tagName;
  if (/INPUT|TEXTAREA|SELECT/.test(tag) && !(e.ctrlKey && /^[zy]$/i.test(e.key) && tag === 'SELECT')) return;
  if (!$('modal').classList.contains('hidden')) return;
  const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
  if (mod && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && k === 'y') { e.preventDefault(); redo(); return; }
  if (mod && k === 's') { e.preventDefault(); $('btnExport').click(); return; }
  if (mod && k === 'o') { e.preventDefault(); $('fileOpen').click(); return; }
  if (mod && k === 'a') { e.preventDefault(); selectAll(); return; }
  if (mod && k === 'd') { e.preventDefault(); deselect(); return; }
  if (mod && k === 'j') { e.preventDefault(); E.sel ? selToLayer() : dupLayer(); return; }
  if (mod) return;
  if (k === ' ') { e.preventDefault(); if (!E.spaceDown) { E.spaceDown = true; overlay.style.cursor = 'grab'; drawOverlay(); } return; }
  if (TOOL_KEYS[k]) { setTool(TOOL_KEYS[k]); return; }
  if (k === '[') { E.opts.size = Math.max(1, Math.round(E.opts.size / 1.2)); renderToolOpts(); drawOverlay(); }
  if (k === ']') { E.opts.size = Math.min(400, Math.round(E.opts.size * 1.2) + 1); renderToolOpts(); drawOverlay(); }
  if (k === 'delete' || k === 'backspace') deleteSel();
  if (k === 'escape') deselect();
  if (k === '0') fitZoom();
  if (k === '+' || k === '=') setZoom(E.zoom * 1.25);
  if (k === '-') setZoom(E.zoom / 1.25);
  if (k === 'x') { const c = $('color').value; $('color').value = c === '#ffffff' ? '#000000' : '#ffffff'; }
  if (E.tool === 'move' && E.doc && k.startsWith('arrow')) {
    e.preventDefault();
    const st = e.shiftKey ? 10 : 1, dx = k === 'arrowleft' ? -st : k === 'arrowright' ? st : 0, dy = k === 'arrowup' ? -st : k === 'arrowdown' ? st : 0;
    pushHistory(); const l = L(), c = cloneCanvas(l.canvas); l.ctx.clearRect(0, 0, E.doc.w, E.doc.h); l.ctx.drawImage(c, dx, dy); render(); updateThumbs();
  }
});
document.addEventListener('keyup', e => { if (e.key === ' ') { E.spaceDown = false; setTool(E.tool); } });
window.addEventListener('beforeunload', e => { if (E.dirty && E.hist.length) { e.preventDefault(); e.returnValue = ''; } });

/* ---------------- boot ---------------- */
window.Halo = { E, INFO: () => INFO, toast, modal, api, busy, showTab, loadImageFile, openImage, placeImage, compositeBlob, compositeCanvas, canvasBlob, newCanvas, download, shareBlob, fmtBytes, $ };
setTool('brush');
refreshInfo(); setInterval(refreshInfo, 30000);
try { const t = localStorage.getItem('halo-tab'); if (t && $('tab-' + t)) showTab(t); } catch (e) { }
