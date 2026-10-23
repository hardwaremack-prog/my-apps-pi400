/* Halo Studio - 3D print tab (three.js preview) */
import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/OrbitControls.js';
import { STLLoader } from '/static/vendor/STLLoader.js';

const { toast, api, $ } = window.Halo;

const MODES = {
  lithophane: [
    { k: 'width_mm', l: 'Width', min: 30, max: 250, step: 5, d: 100, u: 'mm' },
    { k: 'curve_deg', l: 'Shape', type: 'seg', d: '0', opts: [['0', 'Flat'], ['120', 'Curved'], ['180', 'Half round'], ['360', 'Lamp']] },
    { k: 'min_mm', l: 'Thinnest (bright)', min: 0.4, max: 2, step: 0.1, d: 0.8, u: 'mm' },
    { k: 'max_mm', l: 'Thickest (dark)', min: 1.5, max: 6, step: 0.1, d: 3.2, u: 'mm' },
    { k: 'frame_mm', l: 'Frame', min: 0, max: 10, step: 0.5, d: 3, u: 'mm' },
  ],
  relief: [
    { k: 'width_mm', l: 'Width', min: 30, max: 300, step: 5, d: 100, u: 'mm' },
    { k: 'base_mm', l: 'Base thickness', min: 0.8, max: 8, step: 0.2, d: 2, u: 'mm' },
    { k: 'depth_mm', l: 'Relief depth', min: 0.5, max: 15, step: 0.5, d: 3, u: 'mm' },
  ],
  colorlayers: [
    { k: 'width_mm', l: 'Width', min: 30, max: 250, step: 5, d: 100, u: 'mm' },
    { k: 'colors', l: 'Filament colors', min: 2, max: 8, step: 1, d: 4, u: '' },
    { k: 'layer_mm', l: 'Layer height', type: 'select', d: '0.2', opts: [['0.08', '0.08 mm'], ['0.12', '0.12 mm'], ['0.16', '0.16 mm'], ['0.2', '0.20 mm'], ['0.28', '0.28 mm']] },
    { k: 'layers_per_color', l: 'Layers per color', min: 1, max: 10, step: 1, d: 3, u: '' },
    { k: 'base_mm', l: 'Base', min: 0.2, max: 3, step: 0.2, d: 0.6, u: 'mm' },
  ],
  keychain: [
    { k: 'width_mm', l: 'Width', min: 20, max: 150, step: 5, d: 50, u: 'mm' },
    { k: 'threshold', l: 'Shape threshold', min: 5, max: 95, step: 1, d: 50, u: '%' },
    { k: 'outline', l: 'Backing plate', type: 'check', d: true },
    { k: 'hole', l: 'Keyring hole', type: 'check', d: true },
    { k: 'base_mm', l: 'Plate thickness', min: 1, max: 6, step: 0.2, d: 2, u: 'mm' },
    { k: 'thickness_mm', l: 'Raised design', min: 0.4, max: 6, step: 0.2, d: 1.4, u: 'mm' },
    { k: 'border_mm', l: 'Plate border', min: 1, max: 8, step: 0.5, d: 2.5, u: 'mm' },
  ],
  cutter: [
    { k: 'width_mm', l: 'Width', min: 30, max: 200, step: 5, d: 80, u: 'mm' },
    { k: 'threshold', l: 'Shape threshold', min: 5, max: 95, step: 1, d: 50, u: '%' },
    { k: 'height_mm', l: 'Cutter depth', min: 6, max: 30, step: 1, d: 15, u: 'mm' },
    { k: 'wall_mm', l: 'Blade thickness', min: 0.6, max: 3, step: 0.1, d: 1.0, u: 'mm' },
    { k: 'lip_mm', l: 'Grip lip width', min: 1, max: 8, step: 0.5, d: 3, u: 'mm' },
    { k: 'lip_height_mm', l: 'Grip lip height', min: 1, max: 5, step: 0.5, d: 2, u: 'mm' },
  ],
};
const TIPS = {
  lithophane: 'Photos with good contrast work best. Thin = bright, thick = dark.',
  relief: 'Logos and high-contrast art look great. Bright = raised.',
  colorlayers: 'HueForge-style prints: each height band is a filament swap. Dark colors go down first.',
  keychain: 'Use a PNG with a transparent background, or a dark shape on white.',
  cutter: 'Use a simple silhouette. Transparent PNGs work best.',
};
let mode = 'lithophane', file = null, values = {}, lastResult = null, regenT = null, busyGen = false;

function paramsUI() {
  const box = $('pParams');
  box.innerHTML = '<h3>Settings</h3>' + MODES[mode].map(p => {
    const v = values[mode][p.k];
    if (p.type === 'seg') return `<label>${p.l}</label><div class="seg" data-k="${p.k}">${p.opts.map(([ov, on]) => `<button data-v="${ov}" class="${String(v) === ov ? 'on' : ''}">${on}</button>`).join('')}</div>`;
    if (p.type === 'select') return `<label>${p.l} <select data-k="${p.k}">${p.opts.map(([ov, on]) => `<option value="${ov}" ${String(v) === ov ? 'selected' : ''}>${on}</option>`).join('')}</select></label>`;
    if (p.type === 'check') return `<label class="check"><input type="checkbox" data-k="${p.k}" ${v ? 'checked' : ''}> ${p.l}</label>`;
    return `<label>${p.l} <output>${v}${p.u ? ' ' + p.u : ''}</output><input type="range" data-k="${p.k}" min="${p.min}" max="${p.max}" step="${p.step}" value="${v}"></label>`;
  }).join('') + `<p class="hint">${TIPS[mode]}</p>`;
  box.querySelectorAll('input[type=range]').forEach(r => r.oninput = () => { values[mode][r.dataset.k] = +r.value; const p = MODES[mode].find(x => x.k === r.dataset.k); r.previousElementSibling.textContent = r.value + (p.u ? ' ' + p.u : ''); schedule(); });
  box.querySelectorAll('select').forEach(s => s.onchange = () => { values[mode][s.dataset.k] = s.value; schedule(); });
  box.querySelectorAll('input[type=checkbox]').forEach(c => c.onchange = () => { values[mode][c.dataset.k] = c.checked; schedule(); });
  box.querySelectorAll('.seg').forEach(sg => sg.onclick = e => { const b = e.target.closest('button'); if (!b) return; sg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); values[mode][sg.dataset.k] = b.dataset.v; schedule(); });
}
Object.keys(MODES).forEach(m => { values[m] = {}; MODES[m].forEach(p => values[m][p.k] = p.d); });
paramsUI();
$('pModes').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.m; $('pModes').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
  paramsUI(); schedule(true);
};
$('pDet').oninput = () => { $('pDetV').textContent = +$('pDet').value === 60 ? 'low' : $('pDet').value + ' px'; schedule(); };
$('pDetV').textContent = 'auto'; $('pDet').value = 60; $('pDet').dataset.auto = '1';
$('pDet').addEventListener('input', () => delete $('pDet').dataset.auto);
$('pInv').onchange = () => schedule();
function schedule(now) { if (!file || !lastResult) return; clearTimeout(regenT); regenT = setTimeout(generate, now ? 10 : 700); }

async function setImage(f) {
  file = f;
  const img = $('pSrc');
  try { const b = await window.Halo.loadImageFile(f); const c = window.Halo.newCanvas(b.width, b.height); c.getContext('2d').drawImage(b, 0, 0); img.src = c.toDataURL(); }
  catch (e) { img.removeAttribute('src'); }
  img.classList.remove('hidden'); $('pDropTxt').textContent = f.name;
  generate();
}
window.HaloTools.setPrintImage = setImage;
const drop = $('pDrop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('dragover'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('dragover'); }));
drop.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) setImage(f); });
$('pFile').onchange = e => { const f = e.target.files[0]; e.target.value = ''; if (f) setImage(f); };
$('pGo').onclick = () => file ? generate() : toast('Drop a picture first', true);
['pStl', 'p3mf'].forEach(id => $(id).classList.add('off'));

async function generate() {
  if (!file) return;
  if (busyGen) { schedule(); return; }
  busyGen = true; $('pGo').disabled = true; $('pGo').textContent = 'Building model…';
  const fd = new FormData(); fd.append('image', file, file.name); fd.append('mode', mode);
  Object.entries(values[mode]).forEach(([k, v]) => fd.append(k, typeof v === 'boolean' ? (v ? '1' : '0') : v));
  if (!$('pDet').dataset.auto) fd.append('detail', $('pDet').value);
  fd.append('invert', $('pInv').checked ? '1' : '0');
  try {
    const t0 = performance.now();
    const j = await (await api('/api/3d', fd)).json();
    lastResult = j;
    const base = (file.name.replace(/\.[^.]+$/, '') || 'print') + '_' + mode;
    $('pStl').href = `/api/3d/${j.id}.stl?name=${encodeURIComponent(base)}`; $('pStl').download = base + '.stl';
    $('p3mf').href = `/api/3d/${j.id}.3mf?name=${encodeURIComponent(base)}`; $('p3mf').download = base + '.3mf';
    ['pStl', 'p3mf'].forEach(id => $(id).classList.remove('off'));
    $('pTip').textContent = j.tip || '';
    showInfo(j, performance.now() - t0);
    await loadModel(j);
  } catch (e) { toast(e.message, true); }
  finally { busyGen = false; $('pGo').disabled = false; $('pGo').textContent = 'Make 3D model'; }
}
function showInfo(j, ms) {
  const box = $('pInfo'); box.classList.remove('hidden');
  const [x, y, z] = j.size_mm;
  let h = `<div class="stat"><b>${x} × ${y} × ${z} mm</b><span>size on the bed</span></div>
    <div class="stat"><b>~${j.grams_pla} g</b><span>PLA (100% infill)</span></div>
    <div class="stat"><b>${(j.triangles / 1000).toFixed(0)}k</b><span>triangles · ${(ms / 1000).toFixed(1)} s</span></div>`;
  if (j.swaps) h += `<div class="swaps"><table>${j.swaps.map(s => `<tr><td><span class="sw" style="background:${s.color}"></span></td><td>${s.note}</td><td>${s.height_mm} mm</td><td>layer ${s.layer ?? ''}</td></tr>`).join('')}</table></div>`;
  if (j.preview) h += `<div><img class="qprev" src="${j.preview}" alt="color preview"><div class="hint">How the colors will land</div></div>`;
  box.innerHTML = h;
}

/* ---------------- three.js viewer ---------------- */
const host = $('viewer');
let renderer, scene, camera, controls, mesh, grid;
function initViewer() {
  if (renderer) return;
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  host.appendChild(renderer.domElement);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(40, 1, 0.5, 5000);
  camera.position.set(0, 120, 180);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xfff4e6, 0x2a2018, 1.4));
  const d1 = new THREE.DirectionalLight(0xffffff, 2.2); d1.position.set(80, 160, 120); scene.add(d1);
  const d2 = new THREE.DirectionalLight(0xffb070, 0.9); d2.position.set(-120, 60, -90); scene.add(d2);
  const ro = new ResizeObserver(resize); ro.observe(host); resize();
  (function loop() { requestAnimationFrame(loop); if (!document.querySelector('#tab-print.on')) return; controls.update(); renderer.render(scene, camera); })();
}
function resize() {
  if (!renderer) return;
  const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}
document.addEventListener('halo-tab', e => { if (e.detail === 'print') setTimeout(resize, 20); });

async function loadModel(j) {
  initViewer();
  const buf = await (await fetch(`/api/3d/${j.id}.stl`)).arrayBuffer();
  let geo = new STLLoader().parse(buf);
  geo.rotateX(-Math.PI / 2); // slicer Z-up -> three Y-up
  let mat;
  if (j.swaps && j.swaps.length > 1) {
    const pos = geo.attributes.position, n = pos.count, cols = new Float32Array(n * 3);
    const sw = j.swaps.map(s => ({ h: s.height_mm, c: new THREE.Color(s.color) }));
    for (let t = 0; t < n; t += 3) {
      const zmax = Math.max(pos.getY(t), pos.getY(t + 1), pos.getY(t + 2)) - 1e-3;
      let c = sw[0].c; for (const s of sw) if (s.h <= zmax) c = s.c;
      for (let k = 0; k < 3; k++) { cols[(t + k) * 3] = c.r; cols[(t + k) * 3 + 1] = c.g; cols[(t + k) * 3 + 2] = c.b; }
    }
    geo.setAttribute('color', new THREE.BufferAttribute(cols, 3));
    mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6, metalness: 0.0 });
  } else {
    mat = new THREE.MeshStandardMaterial({ color: mode === 'lithophane' ? 0xf4ede2 : 0xffb347, roughness: 0.55, metalness: 0.02 });
  }
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh.material.dispose(); }
  mesh = new THREE.Mesh(geo, mat); scene.add(mesh);
  geo.computeBoundingBox(); geo.computeBoundingSphere();
  const bb = geo.boundingBox, size = new THREE.Vector3(); bb.getSize(size);
  if (grid) scene.remove(grid);
  const g = Math.ceil(Math.max(size.x, size.z) * 1.4 / 10) * 10;
  grid = new THREE.GridHelper(g, g / 10, 0x6b5a48, 0x3a3029); grid.position.y = bb.min.y - 0.05; scene.add(grid);
  const r = geo.boundingSphere.radius, c = geo.boundingSphere.center;
  controls.target.copy(c);
  const fresh = !loadModel.done; loadModel.done = true;
  if (fresh || loadModel.lastMode !== mode) {
    const dir = mode === 'lithophane' ? new THREE.Vector3(0.1, 1, 0.45) : new THREE.Vector3(0.35, 0.9, 0.9);
    camera.position.copy(c).add(dir.normalize().multiplyScalar(r * 2.6));
  }
  loadModel.lastMode = mode;
  camera.near = r / 50; camera.far = r * 50; camera.updateProjectionMatrix();
  $('vhint').classList.add('hidden');
}
