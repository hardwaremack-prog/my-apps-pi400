/* Blueprint - shape library, SVG drawing, text-to-diagram and auto-layout.
   Pure functions shared by the page viewer and the diagram editor. */
(function () {
'use strict';
const BP = window.BP = window.BP || {};
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
BP.esc = esc;
BP.uid = (p = '') => p + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const FONT = "Inter, 'Segoe UI', Roboto, 'DejaVu Sans', Arial, sans-serif";
const MONO = "'DejaVu Sans Mono', Consolas, Menlo, monospace";
const r1 = v => Math.round(v * 10) / 10;
const DOT = 'fill="currentColor" stroke="none"';

/* 40x40 line icons, drawn in currentColor */
const ICONS = {
  router: `<rect x="4" y="17" width="32" height="14" rx="3"/><path d="M11 17 9 6M29 17l2-11"/><circle cx="10.5" cy="24" r="1.6" ${DOT}/><circle cx="15.5" cy="24" r="1.6" ${DOT}/><path d="M21 24h10"/>`,
  switch: `<rect x="2" y="15" width="36" height="15" rx="2.5"/><rect x="6" y="20" width="3" height="5" ${DOT}/><rect x="11.5" y="20" width="3" height="5" ${DOT}/><rect x="17" y="20" width="3" height="5" ${DOT}/><rect x="22.5" y="20" width="3" height="5" ${DOT}/><rect x="28" y="20" width="3" height="5" ${DOT}/><path d="M9 8h9m-3-3 3 3-3 3M31 8h-9m3-3-3 3 3 3"/>`,
  firewall: `<rect x="4" y="6" width="32" height="28" rx="2"/><path d="M4 15.3h32M4 24.6h32M14 6v9.3M26 6v9.3M9 15.3v9.3M20 15.3v9.3M31 15.3v9.3M14 24.6V34M26 24.6V34"/>`,
  cloud: `<path d="M12 31a7.5 7.5 0 0 1-1.2-14.9A9.5 9.5 0 0 1 29 13.5 7 7 0 0 1 28.5 31z"/>`,
  modem: `<rect x="4" y="21" width="32" height="12" rx="2.5"/><path d="M14 15a8 8 0 0 1 12 0M10 10.5a14 14 0 0 1 20 0"/><circle cx="10" cy="27" r="1.5" ${DOT}/><circle cx="15" cy="27" r="1.5" ${DOT}/><path d="M21 27h10"/>`,
  ap: `<circle cx="20" cy="22" r="2.6" ${DOT}/><path d="M13.5 16a9 9 0 0 1 13 0M9 11.5a15.5 15.5 0 0 1 22 0M20 25v8M13 35h14"/>`,
  server: `<rect x="7" y="4" width="26" height="9" rx="2"/><rect x="7" y="15.5" width="26" height="9" rx="2"/><rect x="7" y="27" width="26" height="9" rx="2"/><circle cx="12" cy="8.5" r="1.3" ${DOT}/><circle cx="12" cy="20" r="1.3" ${DOT}/><circle cx="12" cy="31.5" r="1.3" ${DOT}/><path d="M18 8.5h10M18 20h10M18 31.5h10"/>`,
  vm: `<path d="M20 4 35 12v16L20 36 5 28V12z"/><path d="M5 12l15 8 15-8M20 20v16"/>`,
  nas: `<rect x="7" y="4" width="26" height="32" rx="2.5"/><path d="M12 11h16M12 17h16M12 23h16"/><circle cx="27" cy="30" r="1.6" ${DOT}/>`,
  database: `<ellipse cx="20" cy="9" rx="13" ry="5"/><path d="M7 9v22c0 2.8 5.8 5 13 5s13-2.2 13-5V9M7 20c0 2.8 5.8 5 13 5s13-2.2 13-5"/>`,
  pc: `<rect x="4" y="5" width="32" height="22" rx="2"/><path d="M15 34h10M20 27v7"/>`,
  laptop: `<rect x="8" y="8" width="24" height="17" rx="1.5"/><path d="M3 31h34l-3-6H6z"/>`,
  phone: `<rect x="12" y="3" width="16" height="34" rx="3"/><path d="M18 32h4"/>`,
  printer: `<path d="M11 13V4h18v9"/><rect x="4" y="13" width="32" height="14" rx="2"/><path d="M11 23h18v13H11z"/>`,
  camera: `<rect x="4" y="12" width="22" height="16" rx="2"/><path d="M26 18l10-5v14l-10-5"/>`,
  user: `<circle cx="20" cy="13" r="7"/><path d="M6 36c1.5-8 7-12 14-12s12.5 4 14 12"/>`,
  pi: `<rect x="3" y="8" width="34" height="24" rx="2.5"/><rect x="13" y="15" width="10" height="10"/><path d="M6 11.5h20" stroke-dasharray="1.5 2.2"/><rect x="28" y="14" width="9" height="7"/><rect x="28" y="23" width="9" height="7"/>`,
  mcu: `<rect x="9" y="9" width="22" height="22" rx="2"/><path d="M14 9V4M20 9V4M26 9V4M14 36v-5M20 36v-5M26 36v-5M9 14H4M9 20H4M9 26H4M36 14h-5M36 20h-5M36 26h-5"/>`,
  iot: `<circle cx="20" cy="20" r="4.5"/><path d="M13 13a10 10 0 0 0 0 14M27 13a10 10 0 0 1 0 14M8 8a17 17 0 0 0 0 24M32 8a17 17 0 0 1 0 24"/>`,
  power: `<rect x="4" y="11" width="29" height="18" rx="2.5"/><path d="M33 17h3v6h-3"/><path d="M19.5 14l-4.5 7h6l-4.5 6"/>`,
};
BP.ICONS = ICONS;

const NET = '#3b6fd8', TEAL = '#16a394', RED = '#d64545', ORANGE = '#e0892b', PURPLE = '#8a55d6', SLATE = '#475569', GOLD = '#c9a227';
const SHAPES = {
  cloud: { name: 'Internet / Cloud', cat: 'Network', icon: 'cloud', stroke: TEAL },
  modem: { name: 'Modem / ONT', cat: 'Network', icon: 'modem', stroke: NET },
  router: { name: 'Router', cat: 'Network', icon: 'router', stroke: NET },
  firewall: { name: 'Firewall', cat: 'Network', icon: 'firewall', stroke: RED },
  switch: { name: 'Switch', cat: 'Network', icon: 'switch', stroke: NET },
  ap: { name: 'Wi-Fi AP', cat: 'Network', icon: 'ap', stroke: NET },
  server: { name: 'Server', cat: 'Devices', icon: 'server', stroke: PURPLE },
  vm: { name: 'VM / Container', cat: 'Devices', icon: 'vm', stroke: PURPLE },
  nas: { name: 'NAS / Storage', cat: 'Devices', icon: 'nas', stroke: PURPLE },
  database: { name: 'Database', cat: 'Devices', icon: 'database', stroke: PURPLE },
  pc: { name: 'Desktop PC', cat: 'Devices', icon: 'pc', stroke: SLATE },
  laptop: { name: 'Laptop', cat: 'Devices', icon: 'laptop', stroke: SLATE },
  phone: { name: 'Phone / Tablet', cat: 'Devices', icon: 'phone', stroke: SLATE },
  printer: { name: 'Printer', cat: 'Devices', icon: 'printer', stroke: SLATE },
  camera: { name: 'Camera', cat: 'Devices', icon: 'camera', stroke: SLATE },
  user: { name: 'Person', cat: 'Devices', icon: 'user', stroke: SLATE },
  pi: { name: 'Raspberry Pi / SBC', cat: 'Hardware', icon: 'pi', stroke: '#c51a4a' },
  mcu: { name: 'Microcontroller', cat: 'Hardware', icon: 'mcu', stroke: TEAL },
  iot: { name: 'Sensor / IoT', cat: 'Hardware', icon: 'iot', stroke: ORANGE },
  power: { name: 'Power / Battery', cat: 'Hardware', icon: 'power', stroke: ORANGE },
  process: { name: 'Process', cat: 'Flowchart', w: 150, h: 64 },
  terminator: { name: 'Start / End', cat: 'Flowchart', w: 140, h: 56, fill: '#e3f7f4', stroke: TEAL },
  decision: { name: 'Decision', cat: 'Flowchart', w: 160, h: 100, fill: '#fff1e0', stroke: ORANGE },
  data: { name: 'Input / Output', cat: 'Flowchart', w: 160, h: 64 },
  document: { name: 'Document', cat: 'Flowchart', w: 140, h: 78 },
  storage: { name: 'Stored data', cat: 'Flowchart', w: 120, h: 86 },
  subprocess: { name: 'Sub-process', cat: 'Flowchart', w: 160, h: 64 },
  connector: { name: 'Connector', cat: 'Flowchart', w: 56, h: 56 },
  rect: { name: 'Box', cat: 'Basic', w: 150, h: 80 },
  rounded: { name: 'Rounded box', cat: 'Basic', w: 150, h: 80 },
  ellipse: { name: 'Ellipse', cat: 'Basic', w: 140, h: 90 },
  note: { name: 'Sticky note', cat: 'Basic', w: 170, h: 110, fill: '#fff6c4', stroke: GOLD },
  text: { name: 'Text', cat: 'Basic', w: 160, h: 40, fill: 'none', stroke: 'none' },
  zone: { name: 'Zone / Group', cat: 'Basic', w: 380, h: 250, fill: NET, stroke: NET },
};
BP.SHAPES = SHAPES;
BP.CATS = ['Network', 'Devices', 'Hardware', 'Flowchart', 'Basic'];

BP.newNode = function (type, x, y, extra) {
  const s = SHAPES[type] || SHAPES.process;
  if (!SHAPES[type]) type = 'process';
  const w = s.w || 104, h = s.h || 92;
  const label = type === 'text' ? 'Text' : type === 'zone' ? 'Zone' : type === 'connector' ? '' : s.name.split(' /')[0];
  return Object.assign({ id: BP.uid('n'), type, x: Math.round(x - w / 2), y: Math.round(y - h / 2), w, h, label, sub: '', notes: '',
    fill: s.fill || '#ffffff', stroke: s.stroke || NET, color: '#1c2433', fs: type === 'text' ? 16 : 13 }, extra || {});
};

function wrap(text, maxW, fs) {
  const maxC = Math.max(3, Math.floor(maxW / (fs * 0.56)));
  const out = [];
  String(text == null ? '' : text).split('\n').forEach(par => {
    let line = '';
    par.split(/\s+/).filter(Boolean).forEach(w => {
      if (!line) line = w;
      else if ((line + ' ' + w).length <= maxC) line += ' ' + w;
      else { out.push(line); line = w; }
      while (line.length > maxC) { out.push(line.slice(0, maxC)); line = line.slice(maxC); }
    });
    out.push(line);
  });
  return out;
}
BP.wrap = wrap;
const textW = n => n.type === 'decision' ? n.w * 0.62 : n.type === 'data' ? n.w - 40 : n.w - 16;

BP.fitNode = function (n) {
  const s = SHAPES[n.type] || SHAPES.process, fs = n.fs || 13;
  if (n.type === 'zone' || n.type === 'text' || n.type === 'connector') return n;
  if (s.icon) {
    const lines = Math.min(3, wrap(n.label, n.w - 12, fs).length);
    const need = 53 + lines * fs * 1.2 + (n.sub ? fs * 1.2 : 0) + 10;
    n.h = Math.max(n.h, Math.ceil(need / 2) * 2);
  } else {
    const lines = wrap(n.label, textW(n), fs).length;
    const need = lines * fs * 1.25 + (n.sub ? fs * 1.2 : 0) + (n.type === 'decision' ? 46 : 24);
    if (need > n.h) n.h = Math.ceil(need / 10) * 10;
  }
  return n;
};

function tx(x, y, s, fs, fill, extra) {
  return `<text x="${r1(x)}" y="${r1(y)}" font-family="${FONT}" font-size="${fs}" fill="${fill}" text-anchor="middle" dominant-baseline="central" ${extra || ''}>${esc(s)}</text>`;
}

function shapePath(n) {
  const { x, y, w, h } = n;
  switch (n.type) {
    case 'decision': return `<polygon points="${x + w / 2},${y} ${x + w},${y + h / 2} ${x + w / 2},${y + h} ${x},${y + h / 2}"/>`;
    case 'terminator': return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${h / 2}"/>`;
    case 'rounded': return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="16"/>`;
    case 'ellipse': case 'connector': return `<ellipse cx="${x + w / 2}" cy="${y + h / 2}" rx="${w / 2}" ry="${h / 2}"/>`;
    case 'data': { const k = Math.min(20, w / 5); return `<polygon points="${x + k},${y} ${x + w},${y} ${x + w - k},${y + h} ${x},${y + h}"/>`; }
    case 'document': { const a = 9; return `<path d="M${x},${y} H${x + w} V${y + h - a} Q${x + w * 0.75},${y + h - a * 2.4} ${x + w / 2},${y + h - a} T${x},${y + h - a} Z"/>`; }
    case 'storage': { const ry = Math.min(11, h / 5); return `<path d="M${x},${y + ry} a${w / 2},${ry} 0 0 1 ${w},0 v${h - 2 * ry} a${w / 2},${ry} 0 0 1 ${-w},0 z"/><path d="M${x},${y + ry} a${w / 2},${ry} 0 0 0 ${w},0" fill="none"/>`; }
    case 'subprocess': return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="3"/><path d="M${x + 12},${y}v${h}M${x + w - 12},${y}v${h}" fill="none"/>`;
    case 'note': { const f = 16; return `<path d="M${x},${y} h${w - f} l${f},${f} v${h - f} h${-w} z"/><path d="M${x + w - f},${y} v${f} h${f}" fill="none"/>`; }
    default: return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="5"/>`;
  }
}

function drawNode(n) {
  const s = SHAPES[n.type] || SHAPES.process;
  const fs = n.fs || 13, col = n.color || '#1c2433', cx = n.x + n.w / 2;
  let h = '';
  if (n.type === 'zone') {
    h += `<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="14" fill="${n.fill}" fill-opacity="0.07" stroke="${n.stroke}" stroke-width="1.6" stroke-dasharray="8 6"/>`;
    h += `<text x="${n.x + 14}" y="${n.y + 22}" font-family="${FONT}" font-size="${fs}" font-weight="700" fill="${n.stroke}">${esc(n.label)}</text>`;
    if (n.sub) h += `<text x="${n.x + 14}" y="${r1(n.y + 22 + fs * 1.3)}" font-family="${MONO}" font-size="${fs - 2}" fill="${n.stroke}" opacity="0.8">${esc(n.sub)}</text>`;
  } else if (s.icon) {
    h += `<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="12" fill="${n.fill}" stroke="${n.stroke}" stroke-width="1.6"/>`;
    h += `<g transform="translate(${r1(cx - 18)} ${n.y + 9}) scale(0.9)" color="${n.stroke}" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ICONS[s.icon]}</g>`;
    const lh = fs * 1.2; let y = n.y + 53 + lh / 2;
    wrap(n.label, n.w - 12, fs).slice(0, 3).forEach(l => { h += tx(cx, y, l, fs, col, 'font-weight="600"'); y += lh; });
    if (n.sub) h += `<text x="${r1(cx)}" y="${r1(y + 1)}" font-family="${MONO}" font-size="${fs - 2.5}" fill="#5b6780" text-anchor="middle" dominant-baseline="central">${esc(n.sub)}</text>`;
  } else {
    if (n.type === 'text') h += `<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" fill="#000" fill-opacity="0"/>`;
    else h += `<g fill="${n.fill}" stroke="${n.stroke}" stroke-width="1.6">${shapePath(n)}</g>`;
    const lines = wrap(n.label, textW(n), fs);
    const lh = fs * 1.25, subH = n.sub ? fs * 1.2 : 0;
    let y = n.y + n.h / 2 - ((lines.length - 1) * lh + subH) / 2 + (n.type === 'storage' ? 5 : n.type === 'document' ? -4 : 0);
    lines.forEach(l => { h += tx(cx, y, l, fs, col, n.type === 'text' ? '' : 'font-weight="550"'); y += lh; });
    if (n.sub) h += `<text x="${r1(cx)}" y="${r1(y - lh / 2 + subH / 2 + 1)}" font-family="${MONO}" font-size="${fs - 2.5}" fill="${col}" opacity="0.7" text-anchor="middle" dominant-baseline="central">${esc(n.sub)}</text>`;
  }
  const tip = (n.notes || n.sub) ? `<title>${esc([n.label, n.sub, n.notes].filter(Boolean).join('\n'))}</title>` : '';
  return `<g class="node" data-id="${esc(n.id)}">${tip}${h}</g>`;
}
BP.drawNode = drawNode;

/* ---------- connections */
const DIRS = { t: [0, -1], b: [0, 1], l: [-1, 0], r: [1, 0] };
function port(n, side) { const d = DIRS[side] || DIRS.b; return { x: n.x + n.w / 2 + d[0] * n.w / 2, y: n.y + n.h / 2 + d[1] * n.h / 2, dx: d[0], dy: d[1] }; }
BP.port = port;
function autoSides(a, b, dir) {
  const dx = (b.x + b.w / 2) - (a.x + a.w / 2), dy = (b.y + b.h / 2) - (a.y + a.h / 2);
  const gv = Math.max(b.y - (a.y + a.h), a.y - (b.y + b.h)), gh = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w));
  if (dir === 'LR') { if (gh >= 20) return dx > 0 ? ['r', 'l'] : ['l', 'r']; }
  else if (gv >= 20 && !(gh > 0 && gh > gv * 4)) return dy > 0 ? ['b', 't'] : ['t', 'b'];
  if (Math.abs(dx) / (a.w + b.w) > Math.abs(dy) / (a.h + b.h)) return dx > 0 ? ['r', 'l'] : ['l', 'r'];
  return dy > 0 ? ['b', 't'] : ['t', 'b'];
}
function polyMid(pts) {
  const seg = []; let tot = 0;
  for (let i = 1; i < pts.length; i++) { const l = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y); seg.push(l); tot += l; }
  let half = tot / 2;
  for (let i = 1; i < pts.length; i++) {
    const l = seg[i - 1];
    if (half <= l) { const t = l ? half / l : 0; return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t }; }
    half -= l;
  }
  return pts[0];
}
function edgeGeom(e, map, dir) {
  const a = map[e.from], b = map[e.to];
  if (!a || !b) return null;
  const au = autoSides(a, b, dir);
  const p1 = port(a, e.fromSide || au[0]), p2 = port(b, e.toSide || au[1]);
  if (e.style === 'curve') {
    const k = Math.max(30, Math.hypot(p2.x - p1.x, p2.y - p1.y) * 0.4);
    const c1 = { x: p1.x + p1.dx * k, y: p1.y + p1.dy * k }, c2 = { x: p2.x + p2.dx * k, y: p2.y + p2.dy * k };
    const u = 0.5;
    const mid = { x: 0.125 * p1.x + 0.375 * c1.x + 0.375 * c2.x + 0.125 * p2.x, y: 0.125 * p1.y + 0.375 * c1.y + 0.375 * c2.y + 0.125 * p2.y };
    void u;
    return { d: `M${r1(p1.x)},${r1(p1.y)} C${r1(c1.x)},${r1(c1.y)} ${r1(c2.x)},${r1(c2.y)} ${r1(p2.x)},${r1(p2.y)}`, mid,
      end: p2, endDir: { x: p2.x - c2.x, y: p2.y - c2.y }, start: p1, startDir: { x: p1.x - c1.x, y: p1.y - c1.y } };
  }
  let pts;
  if (e.style === 'straight') pts = [p1, p2];
  else {
    const s = 18;
    const A = { x: p1.x + p1.dx * s, y: p1.y + p1.dy * s }, B = { x: p2.x + p2.dx * s, y: p2.y + p2.dy * s };
    pts = [p1, A];
    if (p1.dx !== 0) {
      if (p2.dx !== 0) { const mx = (A.x + B.x) / 2; pts.push({ x: mx, y: A.y }, { x: mx, y: B.y }); }
      else pts.push({ x: B.x, y: A.y });
    } else {
      if (p2.dy !== 0) { const my = (A.y + B.y) / 2; pts.push({ x: A.x, y: my }, { x: B.x, y: my }); }
      else pts.push({ x: A.x, y: B.y });
    }
    pts.push(B, p2);
  }
  const raw = pts;
  pts = raw.filter((p, i) => i === 0 || Math.abs(p.x - raw[i - 1].x) > 0.5 || Math.abs(p.y - raw[i - 1].y) > 0.5);
  if (pts.length < 2) pts = [p1, p2];
  const L = pts.length;
  return { d: 'M' + pts.map(p => r1(p.x) + ',' + r1(p.y)).join(' L'), mid: polyMid(pts),
    end: p2, endDir: { x: p2.x - pts[L - 2].x, y: p2.y - pts[L - 2].y }, start: p1, startDir: { x: p1.x - pts[1].x, y: p1.y - pts[1].y } };
}
BP.edgeGeom = edgeGeom;
function arrow(p, dir, color) {
  const L = Math.hypot(dir.x, dir.y) || 1, ux = dir.x / L, uy = dir.y / L, s = 11, w = 5.5;
  const bx = p.x - ux * s, by = p.y - uy * s;
  return `<polygon points="${r1(p.x)},${r1(p.y)} ${r1(bx - uy * w)},${r1(by + ux * w)} ${r1(bx + uy * w)},${r1(by - ux * w)}" fill="${color}"/>`;
}
function drawEdge(e, map, o) {
  o = o || {};
  const g = edgeGeom(e, map, o.dir);
  if (!g) return '';
  const c = e.color || '#7a8aa5', w = e.width || 2;
  let h = `<g class="edge" data-id="${esc(e.id)}"><path d="${g.d}" fill="none" stroke="transparent" stroke-width="14" class="edge-hit"/>`;
  if (o.sel) h += `<path d="${g.d}" fill="none" stroke="#4da3ff" stroke-opacity="0.4" stroke-width="${w + 7}" stroke-linejoin="round"/>`;
  h += `<path d="${g.d}" fill="none" stroke="${c}" stroke-width="${w}"${e.dash ? ' stroke-dasharray="7 5"' : ''} stroke-linejoin="round"/>`;
  const ar = e.arrow || 'end';
  if (ar === 'end' || ar === 'both') h += arrow(g.end, g.endDir, c);
  if (ar === 'start' || ar === 'both') h += arrow(g.start, g.startDir, c);
  if (e.label) {
    const fs = 12, tw = String(e.label).length * fs * 0.58 + 14;
    h += `<rect x="${r1(g.mid.x - tw / 2)}" y="${r1(g.mid.y - 10)}" width="${r1(tw)}" height="20" rx="6" fill="#ffffff" stroke="${c}" stroke-width="1"/>`;
    h += tx(g.mid.x, g.mid.y + 0.5, e.label, fs, '#2b3546', 'font-weight="600"');
  }
  return h + '</g>';
}
BP.drawEdge = drawEdge;

BP.bounds = function (nodes) {
  if (!nodes || !nodes.length) return { x: 0, y: 0, w: 400, h: 200 };
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  nodes.forEach(n => { x1 = Math.min(x1, n.x); y1 = Math.min(y1, n.y); x2 = Math.max(x2, n.x + n.w); y2 = Math.max(y2, n.y + n.h); });
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
};

BP.diagramSVG = function (d, o) {
  o = o || {};
  const nodes = d.nodes || [], edges = d.edges || [];
  const map = {}; nodes.forEach(n => { map[n.id] = n; });
  const b = BP.bounds(nodes), pad = o.pad == null ? 30 : o.pad;
  const vb = [b.x - pad, b.y - pad, b.w + pad * 2, b.h + pad * 2];
  let body = '';
  nodes.forEach(n => { if (n.type === 'zone') body += drawNode(n); });
  edges.forEach(e => { body += drawEdge(e, map, { dir: d.layoutDir }); });
  nodes.forEach(n => { if (n.type !== 'zone') body += drawNode(n); });
  const bg = o.bg ? `<rect x="${vb[0]}" y="${vb[1]}" width="${vb[2]}" height="${vb[3]}" fill="${o.bg}"/>` : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vb.join(' ')}" width="${Math.round(vb[2])}" height="${Math.round(vb[3])}">${bg}${body}</svg>`;
};

/* ---------- text -> diagram */
const ALIAS = {
  router: 'router', gateway: 'router', switch: 'switch', firewall: 'firewall', fw: 'firewall', cloud: 'cloud', internet: 'cloud', wan: 'cloud',
  modem: 'modem', ont: 'modem', ap: 'ap', wifi: 'ap', 'wi-fi': 'ap', wireless: 'ap', server: 'server', vm: 'vm', container: 'vm', docker: 'vm',
  nas: 'nas', storage: 'nas', database: 'database', db: 'database', pc: 'pc', desktop: 'pc', computer: 'pc', laptop: 'laptop', phone: 'phone',
  tablet: 'phone', mobile: 'phone', printer: 'printer', camera: 'camera', cam: 'camera', user: 'user', person: 'user', pi: 'pi', raspberry: 'pi',
  sbc: 'pi', mcu: 'mcu', arduino: 'mcu', esp32: 'mcu', microcontroller: 'mcu', iot: 'iot', sensor: 'iot', power: 'power', battery: 'power',
  ups: 'power', solar: 'power', process: 'process', step: 'process', start: 'terminator', end: 'terminator', terminator: 'terminator',
  decision: 'decision', question: 'decision', data: 'data', io: 'data', input: 'data', output: 'data', document: 'document', doc: 'document',
  stored: 'storage', subprocess: 'subprocess', note: 'note', text: 'text', box: 'rect', rect: 'rect', rounded: 'rounded', ellipse: 'ellipse',
  circle: 'ellipse', connector: 'connector', zone: 'zone', group: 'zone',
};
const NET_RULES = [
  [/\b(internet|cloud|wan|isp)\b/i, 'cloud'], [/\b(firewall|fw|pfsense|opnsense)\b/i, 'firewall'], [/\b(modem|ont)\b/i, 'modem'],
  [/\b(router|gateway|gw)\b/i, 'router'], [/\bswitch\b/i, 'switch'], [/\b(ap|access point|wi-?fi|wireless|wap|mesh)\b/i, 'ap'],
  [/\b(nas|backup)\b/i, 'nas'], [/\b(db|database|sql|mysql|postgres)\b/i, 'database'], [/\b(vm|container|docker|lxc)\b/i, 'vm'],
  [/\b(server|srv|proxmox|esxi|host)\b/i, 'server'], [/\b(printer|scanner)\b/i, 'printer'], [/\b(laptop|notebook|macbook|chromebook)\b/i, 'laptop'],
  [/\b(phone|mobile|iphone|android|tablet|ipad)\b/i, 'phone'], [/\b(camera|cam|nvr|cctv|doorbell)\b/i, 'camera'],
  [/\b(raspberry|pi|rpi|pi\d+)\b/i, 'pi'], [/\b(arduino|esp32|esp8266|mcu|microcontroller|pico)\b/i, 'mcu'],
  [/\b(sensor|iot|thermostat|smart|plug)\b/i, 'iot'], [/\b(ups|battery|power|solar|psu|inverter)\b/i, 'power'],
  [/\b(pc|desktop|workstation|computer|tv|console|xbox|playstation)\b/i, 'pc'], [/\b(user|person|admin|customer|client)\b/i, 'user'],
];
function infer(name, mode) {
  if (/\?\s*$/.test(name)) return 'decision';
  if (mode === 'flow') {
    if (name.split(/\s+/).length <= 2 && /^(start|begin|end|finish|finished|stop|done|complete)\b/i.test(name)) return 'terminator';
    return 'process';
  }
  for (const [re, t] of NET_RULES) if (re.test(name)) return t;
  return 'process';
}
function splitTop(s, sep) {
  const out = []; let depth = 0, cur = '';
  for (const ch of s) {
    if (ch === '(' || ch === '[') depth++;
    else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);
    if (ch === sep && depth === 0) { out.push(cur); cur = ''; } else cur += ch;
  }
  out.push(cur);
  return out;
}
BP.parseDSL = function (text, mode) {
  mode = mode || 'auto';
  const lines = String(text || '').split(/\r?\n/).map(l => l.trim()).filter(l => l && !/^(#|\/\/)/.test(l));
  const ARROW = /\s*(?:->|→|=>)\s*/;
  const hasArrow = lines.some(l => ARROW.test(l));
  if (mode === 'auto') {
    const flowish = !hasArrow || lines.some(l => /\?\s*($|->|→|=>|:)/.test(l) || /(^|->|→)\s*(start|end|done|begin|finish)\s*($|->|→|:)/i.test(l));
    mode = flowish ? 'flow' : 'network';
  }
  const nodes = new Map(), edges = [];
  const node = raw => {
    let name = raw.trim().replace(/^(\d+[.)]|[-*•])\s+/, ''), type = null, sub = '';
    name = name.replace(/\[([^\]]+)\]/, (m, t) => { type = ALIAS[t.trim().toLowerCase()] || null; return ''; }).trim();
    const pm = name.match(/\(([^()]*)\)\s*$/);
    if (pm) { sub = pm[1].trim(); name = name.slice(0, pm.index).trim(); }
    if (!name) name = sub || 'Untitled';
    const key = name.toLowerCase();
    let n = nodes.get(key);
    if (!n) { n = { key, name, type: type || infer(name, mode), sub }; nodes.set(key, n); }
    else { if (type) n.type = type; if (sub && !n.sub) n.sub = sub; }
    return key;
  };
  const addEdge = (a, b, label) => { if (a !== b && !edges.some(e => e.from === a && e.to === b)) edges.push({ from: a, to: b, label: label || '' }); };
  if (hasArrow) {
    lines.forEach(line => {
      if (!ARROW.test(line)) { splitTop(line, ',').map(s => s.trim()).filter(Boolean).forEach(node); return; }
      const m = [...line.matchAll(/->|→|=>/g)], last = m[m.length - 1];
      const tailStart = last.index + last[0].length;
      let body = line, label = '', depth = 0;
      for (let i = tailStart; i < line.length; i++) {
        const ch = line[i];
        if (ch === '(' || ch === '[') depth++; else if (ch === ')' || ch === ']') depth--;
        else if (ch === ':' && depth === 0 && (i + 1 >= line.length || /\s/.test(line[i + 1]))) { body = line.slice(0, i); label = line.slice(i + 1).trim(); break; }
      }
      const segs = body.split(ARROW).map(seg => splitTop(seg, ',').map(x => x.trim()).filter(Boolean));
      const keys = segs.map(seg => seg.map(node));
      for (let i = 0; i + 1 < keys.length; i++) keys[i].forEach(a => keys[i + 1].forEach(b => addEdge(a, b, label)));
    });
  } else {
    const ks = lines.map(node);
    if (mode === 'flow' && ks.length) {
      if (nodes.get(ks[0]).type !== 'terminator') ks.unshift(node('Start'));
      if (nodes.get(ks[ks.length - 1]).type !== 'terminator') ks.push(node('Done'));
    }
    for (let i = 0; i + 1 < ks.length; i++) {
      const dec = nodes.get(ks[i]).type === 'decision';
      addEdge(ks[i], ks[i + 1], dec ? 'Yes' : '');
      if (dec) addEdge(ks[i], ks[Math.max(1, i - 2)], 'No');
    }
  }
  edges.forEach(e => { if (nodes.get(e.from).type === 'ap') e.dash = true; });
  return { mode, nodes: [...nodes.values()], edges };
};

BP.DGM_TEMPLATES = [
  { name: 'Home network', mode: 'network', dir: 'TB', text:
`# Home network - edit the names and addresses to match yours
Internet -> Modem (ISP) -> Router (192.168.1.1)
Router -> Main Switch
Router -> Wi-Fi AP
Main Switch -> Desktop PC (192.168.1.20), Raspberry Pi (192.168.1.30), NAS (192.168.1.40), Printer
Wi-Fi AP -> Laptop, Phone, Smart Thermostat [iot]` },
  { name: 'Small office network', mode: 'network', dir: 'TB', text:
`Internet -> Firewall (WAN) -> Core Switch
Core Switch -> Server Switch [switch], Office Switch [switch], Wi-Fi AP
Server Switch -> File Server [server] (10.0.10.5), Database Server [database] (10.0.10.6), Backup NAS (10.0.10.9)
Office Switch -> Workstation 1 [pc], Workstation 2 [pc], Workstation 3 [pc], Printer
Wi-Fi AP -> Staff Laptops [laptop], Guest Phones [phone]` },
  { name: 'Pi hardware project', mode: 'network', dir: 'LR', text:
`Battery Pack [power] -> Raspberry Pi : 5V
Raspberry Pi -> Temperature Sensor [iot] : I2C
Raspberry Pi -> Camera : CSI
Raspberry Pi -> Microcontroller [mcu] : UART
Raspberry Pi -> Home Router : Wi-Fi
Home Router -> Internet` },
  { name: 'Troubleshooting flow', mode: 'flow', dir: 'TB', text:
`Start -> Device powers on?
Device powers on? -> Check power supply and cable : No
Check power supply and cable -> Device powers on?
Device powers on? -> Network light on? : Yes
Network light on? -> Swap cable or port : No
Swap cable or port -> Network light on?
Network light on? -> Can ping gateway? : Yes
Can ping gateway? -> Check IP settings : No
Check IP settings -> Can ping gateway?
Can ping gateway? -> Fixed - write it up : Yes
Fixed - write it up -> End` },
  { name: 'Project process (steps list)', mode: 'flow', dir: 'TB', text:
`Idea / problem statement
Research and requirements
Sketch and design
Build prototype
Test it
Does it meet the requirements?
Write it up and release` },
  { name: 'Safe update / release', mode: 'flow', dir: 'TB', text:
`Start -> Make the change -> Test on spare Pi -> Tests pass?
Tests pass? -> Fix issues : No
Fix issues -> Test on spare Pi
Tests pass? -> Back up live system : Yes
Back up live system -> Deploy update -> Working?
Working? -> Restore backup : No
Working? -> Done : Yes` },
];

/* ---------- layered auto-layout */
BP.layout = function (nodes, edges, dir, o) {
  o = o || {};
  const TB = dir !== 'LR';
  const x0 = o.x0 || 0, y0 = o.y0 || 0;
  const ids = nodes.map(n => n.id), N = new Map(nodes.map(n => [n.id, n]));
  if (!ids.length) return;
  const out = new Map(ids.map(i => [i, []])), inc = new Map(ids.map(i => [i, []]));
  edges.forEach(e => { if (N.has(e.from) && N.has(e.to) && e.from !== e.to) { out.get(e.from).push(e.to); inc.get(e.to).push(e.from); } });
  const st = new Map(), back = new Set(), K = (a, b) => a + '\u0000' + b;
  const dfs = u => { st.set(u, 1); for (const v of out.get(u)) { if (st.get(v) === 1) back.add(K(u, v)); else if (!st.get(v)) dfs(v); } st.set(u, 2); };
  ids.filter(i => !inc.get(i).length).forEach(i => { if (!st.get(i)) dfs(i); });
  ids.forEach(i => { if (!st.get(i)) dfs(i); });
  const fwd = u => out.get(u).filter(v => !back.has(K(u, v)));
  const pre = v => inc.get(v).filter(u => !back.has(K(u, v)));
  const indeg = new Map(ids.map(i => [i, 0]));
  ids.forEach(u => fwd(u).forEach(v => indeg.set(v, indeg.get(v) + 1)));
  const layer = new Map(), qu = ids.filter(i => indeg.get(i) === 0);
  qu.forEach(i => layer.set(i, 0));
  for (let h = 0; h < qu.length; h++) {
    const u = qu[h];
    for (const v of fwd(u)) { layer.set(v, Math.max(layer.get(v) || 0, layer.get(u) + 1)); indeg.set(v, indeg.get(v) - 1); if (indeg.get(v) === 0) qu.push(v); }
  }
  ids.forEach(i => { if (!layer.has(i)) layer.set(i, 0); });
  const L = [];
  ids.forEach(i => { const l = layer.get(i); (L[l] = L[l] || []).push(i); });
  for (let i = 0; i < L.length; i++) L[i] = L[i] || [];
  const pos = new Map();
  L.forEach(row => row.forEach((id, i) => pos.set(id, i)));
  for (let it = 0; it < 8; it++) {
    const down = it % 2 === 0;
    const order = down ? L.map((_, i) => i).slice(1) : L.map((_, i) => i).reverse().slice(1);
    order.forEach(li => {
      const row = L[li];
      const bc = new Map(row.map(id => {
        const ns = (down ? pre(id) : fwd(id));
        return [id, ns.length ? ns.reduce((s, x) => s + pos.get(x), 0) / ns.length : pos.get(id)];
      }));
      row.sort((a, b) => bc.get(a) - bc.get(b));
      row.forEach((id, i) => pos.set(id, i));
    });
  }
  const gapC = o.gapC || (TB ? 46 : 36), gapM = o.gapM || (TB ? 76 : 100);
  const S = n => TB ? n.w : n.h, T = n => TB ? n.h : n.w;
  const C = n => TB ? n.x + n.w / 2 : n.y + n.h / 2;
  const setS = (n, v) => { if (TB) n.x = v; else n.y = v; };
  const getS = n => TB ? n.x : n.y;
  const rows = L.map(row => row.map(id => N.get(id)));
  let main = 0;
  rows.forEach(row => {
    const thick = Math.max(...row.map(T));
    let c = -(row.reduce((s, n) => s + S(n), 0) + gapC * (row.length - 1)) / 2;
    row.forEach(n => {
      setS(n, c); c += S(n) + gapC;
      const m = main + (thick - T(n)) / 2;
      if (TB) n.y = m; else n.x = m;
    });
    main += thick + gapM;
  });
  const place = (row, des) => {
    let prev = -Infinity;
    row.forEach((n, i) => { let s = des[i] - S(n) / 2; if (s < prev + gapC) s = prev + gapC; setS(n, s); prev = s + S(n); });
    const shift = row.reduce((a, n, i) => a + (des[i] - C(n)), 0) / row.length;
    row.forEach(n => setS(n, getS(n) + shift));
  };
  for (let li = 1; li < rows.length; li++) {
    const row = rows[li];
    place(row, row.map(n => { const ps = pre(n.id).map(id => N.get(id)); return ps.length ? ps.reduce((a, p) => a + C(p), 0) / ps.length : C(n); }));
  }
  if (rows.length > 1) {
    const row = rows[0];
    place(row, row.map(n => { const cs = fwd(n.id).map(id => N.get(id)); return cs.length ? cs.reduce((a, c) => a + C(c), 0) / cs.length : C(n); }));
  }
  const b = BP.bounds(nodes);
  nodes.forEach(n => { n.x = Math.round((n.x - b.x + x0) / 10) * 10; n.y = Math.round((n.y - b.y + y0) / 10) * 10; });
};
})();
