// Frostfall Cabins — LAN multiplayer client
import * as THREE from './three.module.min.js';

// ------------------------------------------------------------------ layout
// (must match server.py)
const CABIN_W = 10, CABIN_D = 8;
const STOVE = [-3.2, -2.7], WOODBOX = [-4.0, 0.6], PANTRY = [3.9, -2.9];
const CANDLES = [[1.5, 0.55, 1.27], [2.5, 0.55, 1.27], [4.2, 1.6, 1.55]];
const WORLD = 640, SEGS = 192, CELL = WORLD / SEGS;
const LAKE = { x: -222, z: -8, r: 40 };
const SPAWN = { x: 0, z: 30 };
const REGION_COL = { summit: '#b8c7dc', lake: '#8fd0ff', birch: '#e9e1c8', mill: '#d6a46a', hollow: '#9b8cd8' };

const qs = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

// ------------------------------------------------------------------- noise
let SEED = 1;
function hash(ix, iz) {
  let h = Math.imul(ix, 374761393) + Math.imul(iz, 668265263) + Math.imul(SEED, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177); h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
function vnoise(x, z) {
  const ix = Math.floor(x), iz = Math.floor(z), fx = x - ix, fz = z - iz;
  const ux = fx * fx * (3 - 2 * fx), uz = fz * fz * (3 - 2 * fz);
  const a = hash(ix, iz), b = hash(ix + 1, iz), c = hash(ix, iz + 1), d = hash(ix + 1, iz + 1);
  return (a + (b - a) * ux + (c - a) * uz + (a - b - c + d) * ux * uz) * 2 - 1;
}
function fbm(x, z, o) { let s = 0, a = 1, f = 1, n = 0; for (let i = 0; i < o; i++) { s += a * vnoise(x * f, z * f); n += a; a *= 0.5; f *= 2.03; } return s / n; }
function mulberry(seed) { let s = seed >>> 0; return () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

let cabins = [];        // world info + live state + meshes
let ICE_Y = 0;
function H0(x, z) {
  let h = fbm(x * 0.0055, z * 0.0055, 4) * 24 + fbm(x * 0.022 + 7.3, z * 0.022 - 3.1, 2) * 3.5;
  h += 95 * Math.exp(-(x * x + (z + 275) ** 2) / (2 * 85 * 85));                 // summit
  h -= 12 * Math.exp(-((x - 146) ** 2 + (z - 158) ** 2) / (2 * 55 * 55));        // the hollow
  h += 10 * Math.exp(-((x - 200) ** 2 + (z + 160) ** 2) / (2 * 60 * 60));        // birch ridge
  const r = Math.hypot(x, z); if (r > 262) h += Math.min((r - 262) ** 2 * 0.04, 70);           // valley walls
  return h;
}
function H1(x, z) {
  let h = H0(x, z);
  const d = Math.hypot(x - LAKE.x, z - LAKE.z);
  return lerp(h, ICE_Y - 2.2, smooth(LAKE.r + 24, LAKE.r - 4, d));
}
function heightFn(x, z) {
  let h = H1(x, z);
  for (const c of cabins) { const d = Math.hypot(x - c.x, z - c.z); if (d < 18) h = lerp(h, c.base, smooth(18, 9.5, d)); }
  const ds = Math.hypot(x - SPAWN.x, z - SPAWN.z); if (ds < 16) h = lerp(h, spawnBase, smooth(16, 6, ds));
  return h;
}
let spawnBase = 0;

// mesh-exact ground sampling
let grid = null;
function terrainAt(x, z) {
  const gx = (x + WORLD / 2) / CELL, gz = (z + WORLD / 2) / CELL;
  const ix = clamp(Math.floor(gx), 0, SEGS - 1), iz = clamp(Math.floor(gz), 0, SEGS - 1);
  const fx = clamp(gx - ix, 0, 1), fz = clamp(gz - iz, 0, 1), W = SEGS + 1;
  const a = grid[iz * W + ix], b = grid[(iz + 1) * W + ix], c = grid[(iz + 1) * W + ix + 1], d = grid[iz * W + ix + 1];
  return fx + fz <= 1 ? a + (d - a) * fx + (b - a) * fz : c + (b - c) * (1 - fx) + (d - c) * (1 - fz);
}
function onIce(x, z) { return Math.hypot(x - LAKE.x, z - LAKE.z) < LAKE.r + 3; }
function groundAt(x, z) {
  let h = terrainAt(x, z);
  if (onIce(x, z)) h = Math.max(h, ICE_Y);
  for (const c of cabins) {
    const [lx, lz] = toLocal(c, x, z);
    if (Math.abs(lx) < CABIN_W / 2 + 0.35 && Math.abs(lz) < CABIN_D / 2 + 0.35) return c.base + 0.3;
  }
  return h;
}
function toWorld(c, lx, lz) { const co = Math.cos(c.rot), s = Math.sin(c.rot); return [c.x + lx * co + lz * s, c.z - lx * s + lz * co]; }
function toLocal(c, x, z) { const dx = x - c.x, dz = z - c.z, co = Math.cos(c.rot), s = Math.sin(c.rot); return [dx * co - dz * s, dx * s + dz * co]; }

// ---------------------------------------------------------------- renderer
let quality = store('ffc.quality') || (/aarch64|arm|linux arm/i.test(navigator.platform) ? 'pi' : 'pc');
const canvas = $('view');
let renderer, scene, camera;
const frostU = { value: 0.3 };
const clockU = { value: 0 };

function makeRenderer() {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: quality === 'pc', powerPreference: 'high-performance' });
  renderer.setPixelRatio(quality === 'pi' ? 0.85 : Math.min(devicePixelRatio, 1.5));
  renderer.setSize(innerWidth, innerHeight, false);
  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0b1424, 0.006);
  camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.1, 1400);
  scene.add(camera);
  addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight, false); });
}

function frostMaterial(opts) {
  const m = new THREE.MeshLambertMaterial(opts);
  m.onBeforeCompile = (sh) => {
    sh.uniforms.uFrost = frostU;
    sh.vertexShader = 'varying float vUp;\n' + sh.vertexShader.replace('#include <beginnormal_vertex>', '#include <beginnormal_vertex>\n vUp = objectNormal.y;');
    sh.fragmentShader = 'uniform float uFrost; varying float vUp;\n' + sh.fragmentShader.replace('#include <color_fragment>',
      '#include <color_fragment>\n diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.88,0.93,1.0), clamp(uFrost*(0.2+0.95*max(vUp,0.0)),0.0,1.0));');
  };
  return m;
}

function canvasTex(w, h, draw, repeat) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; }
  t.anisotropy = 4;
  return t;
}
function glowTex(inner, outer) {
  return canvasTex(128, 128, (g, w) => {
    const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
    gr.addColorStop(0, inner); gr.addColorStop(0.35, outer); gr.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = gr; g.fillRect(0, 0, w, w);
  });
}

function mergeColored(parts) {
  // parts: [{geo, color:THREE.Color | fn(vertexIndex,pos)}]
  const pos = [], nor = [], col = [];
  for (const p of parts) {
    const g = p.geo.index ? p.geo.toNonIndexed() : p.geo;
    const P = g.attributes.position, N = g.attributes.normal;
    for (let i = 0; i < P.count; i++) {
      pos.push(P.getX(i), P.getY(i), P.getZ(i)); nor.push(N.getX(i), N.getY(i), N.getZ(i));
      const c = typeof p.color === 'function' ? p.color(i, P.getX(i), P.getY(i), P.getZ(i)) : p.color;
      col.push(c.r, c.g, c.b);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  return g;
}

// ------------------------------------------------------------------- world
let sky, stars, moon, hemi, moonLight, lantern, terrainMat, iceMat, snow, mast;
const treeCells = new Map();
const cabinTags = [];

function buildWorld(world) {
  SEED = world.seed;
  cabins = world.cabins.map((c) => ({ ...c, state: null }));
  ICE_Y = H0(LAKE.x, LAKE.z) - 4;
  for (const c of cabins) c.base = H1(c.x, c.z);
  spawnBase = H1(SPAWN.x, SPAWN.z);

  // sky dome
  const skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: new THREE.Color() }, bot: { value: new THREE.Color() } },
    vertexShader: 'varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader: 'uniform vec3 top; uniform vec3 bot; varying vec3 vP; void main(){ float t = smoothstep(-0.05, 0.45, vP.y); gl_FragColor = vec4(mix(bot, top, t), 1.0); }',
  });
  sky = new THREE.Mesh(new THREE.SphereGeometry(1000, 24, 12), skyMat); sky.renderOrder = -2; scene.add(sky);

  const sp = [], rnd = mulberry(7);
  for (let i = 0; i < 1600; i++) {
    const u = rnd() * 2 - 1, th = rnd() * Math.PI * 2, y = Math.abs(u) * 0.95 + 0.05, r = Math.sqrt(1 - y * y);
    sp.push(Math.cos(th) * r * 900, y * 900, Math.sin(th) * r * 900);
  }
  const sg = new THREE.BufferGeometry(); sg.setAttribute('position', new THREE.Float32BufferAttribute(sp, 3));
  stars = new THREE.Points(sg, new THREE.PointsMaterial({ color: 0xdfe8ff, size: 1.6, sizeAttenuation: false, fog: false, transparent: true, depthWrite: false }));
  scene.add(stars);
  moon = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex('rgba(255,255,245,1)', 'rgba(200,215,255,0.35)'), fog: false, depthWrite: false, transparent: true }));
  moon.scale.set(110, 110, 1); scene.add(moon);

  hemi = new THREE.HemisphereLight(0x8aa4d6, 0xe8eefa, 0.5); scene.add(hemi);
  moonLight = new THREE.DirectionalLight(0xbcd0ff, 0.4); moonLight.position.set(-300, 400, -200); scene.add(moonLight);
  lantern = new THREE.PointLight(0xffc98a, 3.2, 26, 1); lantern.position.set(0.25, -0.35, -0.3); camera.add(lantern);

  // terrain
  const geo = new THREE.PlaneGeometry(WORLD, WORLD, SEGS, SEGS); geo.rotateX(-Math.PI / 2);
  const P = geo.attributes.position; grid = new Float32Array(P.count);
  for (let i = 0; i < P.count; i++) { const h = heightFn(P.getX(i), P.getZ(i)); P.setY(i, h); grid[i] = h; }
  geo.computeVertexNormals();
  const N = geo.attributes.normal, cols = new Float32Array(P.count * 3);
  const snowC = new THREE.Color(0xeef3fb), shadeC = new THREE.Color(0xc9d6ec), rockC = new THREE.Color(0x5b5f6a), tmp = new THREE.Color();
  for (let i = 0; i < P.count; i++) {
    const x = P.getX(i), z = P.getZ(i), y = P.getY(i), ny = N.getY(i);
    tmp.copy(snowC).lerp(shadeC, clamp(vnoise(x * 0.08, z * 0.08) * 0.5 + 0.35, 0, 1));
    const rock = smooth(0.86, 0.72, ny) + (y > 70 ? smooth(70, 95, y) * 0.35 * (vnoise(x * 0.2, z * 0.2) > 0.1 ? 1 : 0) : 0);
    tmp.lerp(rockC, clamp(rock, 0, 0.85));
    const hol = Math.exp(-((x - 146) ** 2 + (z - 158) ** 2) / (2 * 60 * 60)); tmp.multiplyScalar(1 - hol * 0.25);
    cols[i * 3] = tmp.r; cols[i * 3 + 1] = tmp.g; cols[i * 3 + 2] = tmp.b;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(cols, 3));
  terrainMat = new THREE.MeshLambertMaterial({ vertexColors: true });
  scene.add(new THREE.Mesh(geo, terrainMat));

  // frozen lake + dock
  iceMat = new THREE.MeshPhongMaterial({ color: 0x9cc8e8, specular: 0xffffff, shininess: 90, transparent: true, opacity: 0.92 });
  const ice = new THREE.Mesh(new THREE.CircleGeometry(LAKE.r + 4, 64), iceMat);
  ice.rotation.x = -Math.PI / 2; ice.position.set(LAKE.x, ICE_Y + 0.02, LAKE.z); scene.add(ice);
  const lc = cabins.find((c) => c.region === 'lake');
  if (lc) {
    const dir = new THREE.Vector2(lc.x - LAKE.x, lc.z - LAKE.z).normalize();
    const plank = new THREE.MeshLambertMaterial({ color: 0x6b4a32 });
    const dock = new THREE.Group();
    const deck = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.2, 12), plank); deck.position.y = 0.45; dock.add(deck);
    for (let i = 0; i < 4; i++) for (const s of [-1, 1]) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 2.2), plank); post.position.set(s * 1.1, -0.2, -5 + i * 3.3); dock.add(post);
    }
    const sx = LAKE.x + dir.x * (LAKE.r - 2), sz = LAKE.z + dir.y * (LAKE.r - 2);
    dock.position.set(sx, ICE_Y, sz); dock.rotation.y = Math.atan2(dir.x, dir.y); scene.add(dock);
  }

  buildTrees();
  buildRocks();
  for (const c of cabins) buildCabin(c);
  buildLandmarks();
  buildSnow();

  // floating cabin tags
  for (const c of cabins) {
    const el = document.createElement('div'); el.className = 'ctag'; $('tags').appendChild(el); cabinTags.push(el);
  }
}

function regionAt(x, z) {
  let best = null, bd = 1e9;
  for (const c of cabins) {
    const cx = c.region === 'summit' ? 0 : c.region === 'lake' ? -200 : c.x, cz = c.region === 'summit' ? -235 : c.region === 'lake' ? -25 : c.z;
    const d = Math.hypot(x - cx, z - cz);
    if (d < bd) { bd = d; best = c.region; }
  }
  return bd < 120 ? best : 'meadow';
}

function buildTrees() {
  const rnd = mulberry(SEED * 13 + 5);
  const pines = [], birches = [];
  for (let gx = -WORLD / 2 + 4; gx < WORLD / 2; gx += 6.5) for (let gz = -WORLD / 2 + 4; gz < WORLD / 2; gz += 6.5) {
    const x = gx + (rnd() - 0.5) * 5.5, z = gz + (rnd() - 0.5) * 5.5;
    const r = Math.hypot(x, z); if (r > 305) continue;
    if (cabins.some((c) => Math.hypot(x - c.x, z - c.z) < 15)) continue;
    if (Math.hypot(x - SPAWN.x, z - SPAWN.z) < 15) continue;
    if (Math.hypot(x - LAKE.x, z - LAKE.z) < LAKE.r + 7) continue;
    const reg = regionAt(x, z), h = heightFn(x, z), n = vnoise(x * 0.03 + 11, z * 0.03);
    let p = { summit: 0.28, lake: 0.36, birch: 0.5, mill: 0.42, hollow: 0.9, meadow: 0.2 }[reg] + n * 0.25;
    if (h > 62) p *= 0.25;
    if (rnd() > p) continue;
    const s = 0.75 + rnd() * 0.65 * (reg === 'hollow' ? 1.3 : 1) * (h > 55 ? 0.6 : 1);
    const t = { x, z, y: heightFn(x, z) - 0.2, s, ry: rnd() * 6.28 };
    const birch = reg === 'birch' ? rnd() < 0.8 : reg === 'mill' ? rnd() < 0.25 : false;
    (birch ? birches : pines).push(t);
    const key = Math.floor(x / 10) + ',' + Math.floor(z / 10);
    if (!treeCells.has(key)) treeCells.set(key, []);
    treeCells.get(key).push([x, z, birch ? 0.35 : 0.55 * s]);
  }
  const darkG = new THREE.Color(0x1f3a2c), midG = new THREE.Color(0x2c4a35), bark = new THREE.Color(0x4a3426);
  const pineGeo = mergeColored([
    { geo: new THREE.CylinderGeometry(0.22, 0.32, 2.2, 6).translate(0, 1.1, 0), color: bark },
    { geo: new THREE.ConeGeometry(2.3, 3.2, 7).translate(0, 3.0, 0), color: darkG },
    { geo: new THREE.ConeGeometry(1.8, 2.8, 7).translate(0, 4.7, 0), color: midG },
    { geo: new THREE.ConeGeometry(1.15, 2.4, 7).translate(0, 6.3, 0), color: darkG },
  ]);
  const white = new THREE.Color(0xe8e4dc), mark = new THREE.Color(0x2b2622), leaf = new THREE.Color(0x8a7a4e);
  const birchGeo = mergeColored([
    { geo: new THREE.CylinderGeometry(0.16, 0.22, 7, 7, 10).translate(0, 3.5, 0), color: (i, x, y) => (Math.sin(y * 9.1 + x * 40) > 0.75 ? mark : white) },
    { geo: new THREE.IcosahedronGeometry(1.5, 0).translate(0.2, 6.6, 0), color: leaf },
    { geo: new THREE.IcosahedronGeometry(1.1, 0).translate(-0.6, 5.6, 0.4), color: leaf },
    { geo: new THREE.IcosahedronGeometry(1.0, 0).translate(0.5, 7.6, -0.3), color: leaf },
  ]);
  const mat = frostMaterial({ vertexColors: true });
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), v = new THREE.Vector3(), sc = new THREE.Vector3();
  for (const [list, g] of [[pines, pineGeo], [birches, birchGeo]]) {
    const im = new THREE.InstancedMesh(g, mat, list.length);
    list.forEach((t, i) => { q.setFromEuler(e.set(0, t.ry, 0)); m4.compose(v.set(t.x, t.y, t.z), q, sc.set(t.s, t.s * (0.9 + (i % 7) * 0.04), t.s)); im.setMatrixAt(i, m4); });
    im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere(); scene.add(im);
  }
}

function buildRocks() {
  const rnd = mulberry(SEED + 99), list = [];
  for (let i = 0; i < 260; i++) {
    const x = (rnd() - 0.5) * 560, z = (rnd() - 0.5) * 560, h = heightFn(x, z);
    if (Math.hypot(x, z) > 300 || cabins.some((c) => Math.hypot(x - c.x, z - c.z) < 14)) continue;
    if (h < 45 && rnd() > 0.15) continue;
    list.push([x, h, z, 0.6 + rnd() * 2.2, rnd() * 6]);
  }
  const im = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(1, 0), frostMaterial({ color: 0x6d717c }), list.length);
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
  list.forEach(([x, y, z, s, r], i) => { q.setFromEuler(e.set(r, r * 2, r * 0.5)); m4.compose(new THREE.Vector3(x, y - s * 0.3, z), q, new THREE.Vector3(s * 1.3, s * 0.8, s)); im.setMatrixAt(i, m4); });
  scene.add(im);
}

// ------------------------------------------------------------------ cabins
const logTex = () => canvasTex(256, 256, (g, w, h) => {
  const n = 8, lh = h / n;
  for (let i = 0; i < n; i++) {
    const gr = g.createLinearGradient(0, i * lh, 0, (i + 1) * lh);
    gr.addColorStop(0, '#3a2516'); gr.addColorStop(0.18, '#7a5233'); gr.addColorStop(0.55, '#8b6040'); gr.addColorStop(0.9, '#4a2f1c'); gr.addColorStop(1, '#24170d');
    g.fillStyle = gr; g.fillRect(0, i * lh, w, lh);
    g.strokeStyle = 'rgba(30,18,10,.35)'; g.lineWidth = 1;
    for (let k = 0; k < 5; k++) { g.beginPath(); const y = i * lh + 6 + Math.random() * (lh - 12); g.moveTo(0, y); g.bezierCurveTo(w * .3, y + 2, w * .6, y - 2, w, y + 1); g.stroke(); }
  }
}, true);
let LOGTEX = null;
const winOff = new THREE.MeshBasicMaterial({ color: 0x1a2232 });
const winOn = new THREE.MeshBasicMaterial({ color: 0xffb455, fog: false });
const glowSprite = () => glowTex('rgba(255,200,120,0.95)', 'rgba(255,140,50,0.35)');
let GLOWTEX = null;

function signTex(text) {
  return canvasTex(512, 96, (g, w, h) => {
    g.fillStyle = '#4b301c'; g.fillRect(0, 0, w, h); g.strokeStyle = '#2a190d'; g.lineWidth = 8; g.strokeRect(4, 4, w - 8, h - 8);
    g.fillStyle = '#f3e3c3'; g.font = 'bold 54px Georgia, serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, w / 2, h / 2 + 3);
  });
}

function buildCabin(c) {
  LOGTEX = LOGTEX || logTex(); GLOWTEX = GLOWTEX || glowSprite();
  const g = new THREE.Group(); g.position.set(c.x, c.base, c.z); g.rotation.y = c.rot; scene.add(g); c.group = g;
  const wall = new THREE.MeshLambertMaterial({ map: LOGTEX });
  const plank = new THREE.MeshLambertMaterial({ color: 0x7b5a3c });
  const dark = new THREE.MeshLambertMaterial({ color: 0x23242a });
  const snowM = new THREE.MeshLambertMaterial({ color: 0xf2f6fc });
  const stone = new THREE.MeshLambertMaterial({ color: 0x7a7670 });
  const box = (w, h, d, m, x, y, z) => { const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); o.position.set(x, y, z); g.add(o); return o; };
  const T = 0.35, H = 3, Y0 = 0.3, hw = CABIN_W / 2, hd = CABIN_D / 2;
  box(CABIN_W + 0.6, 0.3, CABIN_D + 0.6, plank, 0, 0.15, 0);
  box(CABIN_W + 2 * T, H, T, wall, 0, Y0 + H / 2, -hd - T / 2);
  box(T, H, CABIN_D + 2 * T, wall, -hw - T / 2, Y0 + H / 2, 0);
  box(T, H, CABIN_D + 2 * T, wall, hw + T / 2, Y0 + H / 2, 0);
  const fw = hw + T - 0.8;
  box(fw, H, T, wall, -(0.8 + fw / 2), Y0 + H / 2, hd + T / 2);
  box(fw, H, T, wall, 0.8 + fw / 2, Y0 + H / 2, hd + T / 2);
  box(1.6, 0.8, T, wall, 0, Y0 + 2.6, hd + T / 2);
  const door = box(0.08, 2.2, 1.5, plank, 0.8, Y0 + 1.1, hd + 0.75); door.rotation.y = 0; door.position.set(1.5, Y0 + 1.1, hd + 0.8); door.rotation.y = Math.PI / 2 - 0.3;
  // gables + roof
  for (const sz of [-1, 1]) box(CABIN_W + 2 * T, 0.32, T, wall, 0, Y0 + H + 0.16, sz * (hd + T / 2));
  const span = hd + T + 0.3, rise = span * Math.tan(0.5);
  const tri = new THREE.Shape([new THREE.Vector2(-hd - T, 0), new THREE.Vector2(hd + T, 0), new THREE.Vector2(0, rise - 0.1)]);
  for (const s of [-1, 1]) { const gm = new THREE.Mesh(new THREE.ShapeGeometry(tri), new THREE.MeshLambertMaterial({ map: LOGTEX, side: THREE.DoubleSide })); gm.rotation.y = Math.PI / 2; gm.position.set(s * (hw + T / 2), Y0 + H, 0); g.add(gm); }
  const slabL = span / Math.cos(0.5) + 0.4;
  for (const s of [-1, 1]) {
    const roof = new THREE.Group(); roof.position.set(0, Y0 + H + rise / 2, s * span / 2); roof.rotation.x = s * 0.5; g.add(roof);
    const r1 = new THREE.Mesh(new THREE.BoxGeometry(CABIN_W + 1.8, 0.25, slabL), plank); roof.add(r1);
    const r2 = new THREE.Mesh(new THREE.BoxGeometry(CABIN_W + 1.9, 0.22, slabL + 0.05), snowM); r2.position.y = 0.22; roof.add(r2);
  }
  // windows (both faces) on side walls + front right
  c.windows = [];
  const win = (x, z, ry) => { for (const off of [-0.2, 0.2]) { const w = new THREE.Mesh(new THREE.PlaneGeometry(1.3, 1.0), winOff); w.position.set(x + Math.sin(ry) * off, Y0 + 1.75, z + Math.cos(ry) * off); w.rotation.y = ry + (off < 0 ? Math.PI : 0); g.add(w); c.windows.push(w); }
    const fr = box(Math.abs(Math.sin(ry)) > 0.5 ? 0.5 : 1.5, 0.12, Math.abs(Math.sin(ry)) > 0.5 ? 1.5 : 0.5, plank, x, Y0 + 1.18, z); fr.material = snowM; };
  win(-hw - T / 2, 0.5, -Math.PI / 2); win(hw + T / 2, 0.5, Math.PI / 2); win(3.1, hd + T / 2, 0);
  // chimney + stove
  box(0.9, 3.6, 0.9, stone, STOVE[0], Y0 + H + 1.6, STOVE[1] - 0.2);
  const [chx, chz] = toWorld(c, STOVE[0], STOVE[1] - 0.2);
  c.chimneyTop = new THREE.Vector3(chx, c.base + Y0 + H + 3.5, chz);
  box(1.1, 0.9, 0.8, dark, STOVE[0], Y0 + 0.5, STOVE[1]);
  box(1.2, 0.08, 0.9, dark, STOVE[0], Y0 + 0.97, STOVE[1]);
  const pipe = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.13, 2.3), dark); pipe.position.set(STOVE[0], Y0 + 2.1, STOVE[1] - 0.2); g.add(pipe);
  c.stoveGlow = new THREE.MeshBasicMaterial({ color: 0x111111 });
  const sd = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.4), c.stoveGlow); sd.position.set(STOVE[0], Y0 + 0.5, STOVE[1] + 0.41); g.add(sd);
  c.pot = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.22, 10), new THREE.MeshLambertMaterial({ color: 0xb7b9c0 }));
  c.pot.position.set(STOVE[0] + 0.25, Y0 + 1.12, STOVE[1]); g.add(c.pot);
  c.steam = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOWTEX, color: 0xffffff, transparent: true, opacity: 0.35, depthWrite: false }));
  c.steam.scale.set(0.5, 0.8, 1); c.steam.position.set(STOVE[0] + 0.25, Y0 + 1.6, STOVE[1]); g.add(c.steam);
  // woodbox with stack
  const [wx, wz] = WOODBOX;
  box(0.9, 0.08, 1.5, plank, wx, Y0 + 0.05, wz); box(0.06, 0.6, 1.5, plank, wx - 0.42, Y0 + 0.32, wz); box(0.06, 0.6, 1.5, plank, wx + 0.42, Y0 + 0.32, wz);
  box(0.9, 0.6, 0.06, plank, wx, Y0 + 0.32, wz - 0.72); box(0.9, 0.6, 0.06, plank, wx, Y0 + 0.32, wz + 0.72);
  c.woodLogs = [];
  const logM = new THREE.MeshLambertMaterial({ color: 0x7a5132 }), logGeo = new THREE.CylinderGeometry(0.12, 0.12, 1.3, 7).rotateX(Math.PI / 2);
  for (let i = 0; i < 12; i++) { const l = new THREE.Mesh(logGeo, logM); l.position.set(wx - 0.27 + (i % 3) * 0.27, Y0 + 0.2 + Math.floor(i / 3) * 0.2, wz); g.add(l); c.woodLogs.push(l); }
  // table, candles, shelf
  box(1.7, 0.08, 1.1, plank, 2, Y0 + 0.85, 0.55);
  for (const [x, z] of [[1.25, 0.1], [2.75, 0.1], [1.25, 1.0], [2.75, 1.0]]) box(0.08, 0.85, 0.08, plank, x, Y0 + 0.42, z);
  box(0.6, 0.06, 1.0, plank, 4.68, 1.48, 1.6);
  c.candleFlames = [];
  const wax = new THREE.MeshLambertMaterial({ color: 0xf1e6cc });
  for (const [x, z, y] of CANDLES) {
    const cd = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.055, 0.24, 8), wax); cd.position.set(x, y - 0.0, z); g.add(cd);
    const fl = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOWTEX, color: 0xffd08a, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    fl.scale.set(0.35, 0.5, 1); fl.position.set(x, y + 0.2, z); g.add(fl); c.candleFlames.push(fl);
  }
  // pantry
  const [px, pz] = PANTRY;
  box(0.45, 1.9, 1.5, plank, px + 0.6, Y0 + 0.95, pz); for (const y of [0.6, 1.2]) box(0.6, 0.05, 1.5, plank, px + 0.5, Y0 + y, pz);
  c.cans = [];
  const canM = new THREE.MeshLambertMaterial({ color: 0xc23b2b });
  for (let i = 0; i < 3; i++) { const cn = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.2, 10), canM); cn.position.set(px + 0.45, Y0 + 0.72, pz - 0.45 + i * 0.45); g.add(cn); c.cans.push(cn); }
  // bed + rug
  box(2.2, 0.45, 1.3, plank, -2.8, Y0 + 0.23, 2.9);
  box(1.9, 0.12, 1.22, new THREE.MeshLambertMaterial({ color: 0x8c2f2a }), -2.65, Y0 + 0.5, 2.9);
  box(0.45, 0.12, 0.9, new THREE.MeshLambertMaterial({ color: 0xece4d4 }), -3.6, Y0 + 0.52, 2.9);
  box(3.2, 0.02, 2.2, new THREE.MeshLambertMaterial({ color: 0x5b3a52 }), 0, Y0 + 0.01, 0.2);
  // name sign over door
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 0.64), (() => { const t = signTex(c.name); return new THREE.MeshLambertMaterial({ map: t, emissiveMap: t, emissive: 0xffffff, emissiveIntensity: 0.35 }); })());
  sign.position.set(0, Y0 + H - 0.05, hd + T + 0.03); g.add(sign);
  // interior light, beacon glow, chimney smoke
  c.light = new THREE.PointLight(0xff9a4a, 0, 15, 1); c.light.position.set(-1.6, Y0 + 2.2, -1.2); g.add(c.light);
  c.beacon = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOWTEX, transparent: true, depthWrite: false, fog: false, blending: THREE.AdditiveBlending, opacity: 0 }));
  c.beacon.scale.set(22, 22, 1); c.beacon.position.set(0, Y0 + H + 3, 0); g.add(c.beacon);
  c.smoke = makeSmoke(c.chimneyTop); scene.add(c.smoke);

  // collision boxes in local space [minX, maxX, minZ, maxZ]
  const e = hw + T;
  c.solids = [
    [-e, e, -hd - T, -hd], [-e, -hw, -hd - T, hd + T], [hw, e, -hd - T, hd + T],
    [-e, -0.8, hd, hd + T], [0.8, e, hd, hd + T],
    [STOVE[0] - 0.6, STOVE[0] + 0.6, STOVE[1] - 0.45, STOVE[1] + 0.45],
    [1.15, 2.85, 0.0, 1.1], [px + 0.2, e, pz - 0.75, pz + 0.75], [-3.9, -1.7, 2.25, 3.55], [wx - 0.45, wx + 0.45, wz - 0.75, wz + 0.75],
  ];

  // regional props
  if (c.region === 'mill') {
    const wheel = new THREE.Group(); const wm = new THREE.MeshLambertMaterial({ color: 0x5a3e28 });
    const rim = new THREE.Mesh(new THREE.TorusGeometry(2.6, 0.16, 6, 24), wm); wheel.add(rim);
    for (let i = 0; i < 8; i++) { const sp = new THREE.Mesh(new THREE.BoxGeometry(0.14, 5.2, 0.14), wm); sp.rotation.z = (i / 8) * Math.PI; wheel.add(sp);
      const pad = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.08, 0.9), snowM); pad.position.set(Math.cos(i / 8 * 6.283) * 2.6, Math.sin(i / 8 * 6.283) * 2.6, 0); pad.rotation.z = i / 8 * 6.283; wheel.add(pad); }
    wheel.rotation.y = Math.PI / 2; wheel.position.set(hw + 1.2, 2.4, -1); g.add(wheel); c.wheel = wheel;
    const ice = new THREE.Mesh(new THREE.PlaneGeometry(4, 40), iceMat); ice.rotation.x = -Math.PI / 2; ice.position.set(hw + 1.2, 0.04, -1); g.add(ice);
  }
  if (c.region === 'hollow') {
    c.wisps = [];
    for (let i = 0; i < 9; i++) {
      const w = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOWTEX, color: 0x9bf0ff, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.6 }));
      w.scale.set(0.9, 0.9, 1); w.userData = { a: i * 0.7, r: 14 + (i * 7) % 18, y: 1.2 + (i % 3) * 0.6 }; scene.add(w); c.wisps.push(w);
    }
  }
  // woodpile outside the door so the first fire is easy to start
  for (let i = 0; i < 3; i++) { const l = new THREE.Mesh(logGeo, logM); l.rotation.y = Math.PI / 2; l.position.set(-2.2, 0.3 + 0.13 + i * 0.0, hd + 1.3 + i * 0.3); l.scale.set(1, 1, 1.4); g.add(l); }
}

function makeSmoke(origin) {
  const n = 28, seeds = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) { seeds[i * 2] = i / n; seeds[i * 2 + 1] = Math.random(); }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3)); g.setAttribute('seed', new THREE.BufferAttribute(seeds, 2));
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uT: clockU, uO: { value: origin }, uA: { value: 0 }, uWind: { value: new THREE.Vector2(1, 0) }, uCol: { value: new THREE.Color(0x8a8f99) } },
    vertexShader: `uniform float uT; uniform vec3 uO; uniform vec2 uWind; attribute vec2 seed; varying float vA;
      void main(){ float t = fract(uT*0.13 + seed.x); vec3 p = uO + vec3(uWind.x*t*t*9. + sin(seed.y*30.+uT)*0.4*t, t*10., uWind.y*t*t*9. + cos(seed.y*20.+uT)*0.4*t);
        vec4 mv = viewMatrix*vec4(p,1.); gl_Position = projectionMatrix*mv; gl_PointSize = (90. + t*260.)/-mv.z; vA = (1.-t)*smoothstep(0.,0.08,t); }`,
    fragmentShader: `uniform float uA; uniform vec3 uCol; varying float vA; void main(){ float d = length(gl_PointCoord-0.5); if(d>0.5) discard; gl_FragColor = vec4(uCol, (1.-d*2.)*vA*uA*0.55); }`,
  });
  const pts = new THREE.Points(g, mat); pts.frustumCulled = false; return pts;
}

function buildLandmarks() {
  // trailhead signpost pointing to every cabin
  const g = new THREE.Group(); g.position.set(SPAWN.x, spawnBase, SPAWN.z); scene.add(g);
  const wood = new THREE.MeshLambertMaterial({ color: 0x5a3e28 });
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.16, 4.4), wood); post.position.y = 2.2; g.add(post);
  cabins.forEach((c, i) => {
    const a = Math.atan2(c.x - SPAWN.x, c.z - SPAWN.z);
    const arm = new THREE.Group(); arm.position.y = 3.8 - i * 0.55; arm.rotation.y = a - Math.PI / 2; g.add(arm);
    const tex = canvasTex(512, 96, (gg, w, h) => { gg.fillStyle = '#6b4a2e'; gg.beginPath(); gg.moveTo(0, 8); gg.lineTo(w - 50, 8); gg.lineTo(w - 4, h / 2); gg.lineTo(w - 50, h - 8); gg.lineTo(0, h - 8); gg.fill();
      gg.fillStyle = '#f3e3c3'; gg.font = 'bold 46px Georgia, serif'; gg.textBaseline = 'middle'; gg.fillText(c.name, 18, h / 2 + 2); });
    const board = new THREE.Mesh(new THREE.PlaneGeometry(2.2, 0.42), new THREE.MeshLambertMaterial({ map: tex, transparent: true, side: THREE.DoubleSide }));
    board.position.x = 1.15; arm.add(board);
  });
  // stone fire ring
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1.1, 0.25, 6, 14), new THREE.MeshLambertMaterial({ color: 0x6f6b66 }));
  ring.rotation.x = -Math.PI / 2; ring.position.set(3.5, 0.15, -2); g.add(ring);
  // summit radio mast with a blinking light, visible from everywhere
  const sc = cabins.find((c) => c.region === 'summit');
  if (sc) {
    const [mx, mz] = toWorld(sc, 0, -14), mb = heightFn(mx, mz);
    const steel = new THREE.MeshLambertMaterial({ color: 0x8a8f99 });
    const m = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.35, 22, 6), steel); m.position.set(mx, mb + 11, mz); scene.add(m);
    mast = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex('rgba(255,90,80,1)', 'rgba(255,40,30,0.3)'), fog: false, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    mast.scale.set(5, 5, 1); mast.position.set(mx, mb + 22.4, mz); scene.add(mast);
  }
}

function buildSnow() {
  const n = quality === 'pi' ? 2200 : 5000;
  const seeds = new Float32Array(n * 4); for (let i = 0; i < n * 4; i++) seeds[i] = Math.random();
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3)); g.setAttribute('seed', new THREE.BufferAttribute(seeds, 4));
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uT: clockU, uCam: { value: new THREE.Vector3() }, uBox: { value: new THREE.Vector3(70, 36, 70) }, uWind: { value: new THREE.Vector2() }, uFall: { value: 2.2 }, uDen: { value: 0.35 }, uCol: { value: new THREE.Color(0xffffff) } },
    vertexShader: `uniform float uT; uniform vec3 uCam; uniform vec3 uBox; uniform vec2 uWind; uniform float uFall; uniform float uDen; attribute vec4 seed; varying float vA;
      void main(){ vec3 p = seed.xyz*uBox; float s = 0.6 + seed.w*0.8;
        p.y -= uT*uFall*s; p.x += uT*uWind.x*s + sin(uT*0.7 + seed.w*20.)*0.7; p.z += uT*uWind.y*s + cos(uT*0.6 + seed.w*17.)*0.7;
        vec3 rel = mod(p - uCam + uBox*0.5, uBox) - uBox*0.5; vec4 mv = viewMatrix*vec4(uCam + rel, 1.);
        gl_Position = projectionMatrix*mv; gl_PointSize = clamp((1.5 + seed.w*2.5) * (40. / max(-mv.z, 0.5)), 1.0, 7.0);
        vA = step(seed.w, uDen) * smoothstep(uBox.x*0.5, uBox.x*0.3, length(rel.xz)) * smoothstep(0.6, 2.5, -mv.z); }`,
    fragmentShader: `uniform vec3 uCol; varying float vA; void main(){ float d = length(gl_PointCoord-0.5); if(d>0.5 || vA<0.01) discard; gl_FragColor = vec4(uCol, vA*(1.-d*2.)*0.9); }`,
  });
  snow = new THREE.Points(g, mat); snow.frustumCulled = false; scene.add(snow);
}

// ------------------------------------------------------------- logs (pool)
const logMeshes = new Map();
let logGeoW, logMatW, logRingMat;
function syncLogs(list) {
  if (!logGeoW) {
    logGeoW = new THREE.CylinderGeometry(0.2, 0.24, 1.6, 8).rotateZ(Math.PI / 2);
    logMatW = new THREE.MeshLambertMaterial({ color: 0x7a5132, emissive: 0x24140a });
    logRingMat = new THREE.MeshBasicMaterial({ color: 0xffc070, transparent: true, opacity: 0.0, depthWrite: false });
  }
  const seen = new Set();
  for (const [id, x, z, r] of list) {
    seen.add(id);
    if (!logMeshes.has(id)) {
      const m = new THREE.Mesh(logGeoW, logMatW); m.position.set(x, groundAt(x, z) + 0.22, z); m.rotation.y = r; scene.add(m);
      m.userData = { id, x, z }; logMeshes.set(id, m);
    }
  }
  for (const [id, m] of logMeshes) if (!seen.has(id)) { scene.remove(m); logMeshes.delete(id); }
}

// ------------------------------------------------------------------ avatars
const avatars = new Map();
function nameTex(name, color) {
  return canvasTex(256, 64, (g, w, h) => {
    g.font = 'bold 30px system-ui, sans-serif'; const tw = g.measureText(name).width + 34;
    g.fillStyle = 'rgba(8,12,22,0.7)'; g.beginPath(); g.roundRect((w - tw) / 2, 8, tw, 46, 23); g.fill();
    g.fillStyle = color; g.beginPath(); g.arc((w - tw) / 2 + 18, 31, 7, 0, 7); g.fill();
    g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(name, w / 2 + 8, 32);
  });
}
function makeAvatar(p) {
  const g = new THREE.Group();
  const coat = new THREE.MeshLambertMaterial({ color: p.color });
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.34, 0.8, 4, 10), coat); body.position.y = 0.78; g.add(body);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.24, 12, 10), new THREE.MeshLambertMaterial({ color: 0xe9c4a2 })); head.position.y = 1.5; g.add(head);
  const hat = new THREE.Mesh(new THREE.SphereGeometry(0.25, 12, 8, 0, 6.283, 0, 1.6), coat); hat.position.y = 1.55; g.add(hat);
  const bob = new THREE.Mesh(new THREE.SphereGeometry(0.09, 8, 6), new THREE.MeshLambertMaterial({ color: 0xffffff })); bob.position.y = 1.82; g.add(bob);
  const scarf = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.07, 6, 14), new THREE.MeshLambertMaterial({ color: 0xf0e6d2 })); scarf.rotation.x = Math.PI / 2; scarf.position.y = 1.28; g.add(scarf);
  const eyeM = new THREE.MeshBasicMaterial({ color: 0x111111 });
  for (const s of [-1, 1]) { const e = new THREE.Mesh(new THREE.SphereGeometry(0.03, 6, 4), eyeM); e.position.set(s * 0.08, 1.53, -0.22); g.add(e); }
  const lamp = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOWTEX || (GLOWTEX = glowSprite()), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
  lamp.scale.set(1.1, 1.1, 1); lamp.position.set(0.42, 0.85, -0.25); g.add(lamp);
  const back = []; const logM = new THREE.MeshLambertMaterial({ color: 0x7a5132 });
  for (let i = 0; i < 4; i++) { const l = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.9, 6), logM); l.rotation.z = Math.PI / 2; l.position.set(0, 0.7 + i * 0.2, 0.38); g.add(l); back.push(l); }
  const tag = new THREE.Sprite(new THREE.SpriteMaterial({ map: nameTex(p.name, p.color), depthTest: false, transparent: true }));
  tag.scale.set(2.2, 0.55, 1); tag.position.y = 2.2; tag.renderOrder = 5; g.add(tag);
  scene.add(g);
  return { g, coat, back, base: new THREE.Color(p.color), t: { x: p.x, y: p.y, z: p.z, ry: p.ry } };
}

// ---------------------------------------------------------------- network
let ws, myId = null, world = null, S = null, nightSeconds = 720, localM = 0;
const me = { x: 0, y: 0, z: 30, vx: 0, vz: 0, vy: 0, yaw: 0, pitch: 0, onGround: true };
let joined = false;

function connect(name, color) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => ws.send(JSON.stringify({ t: 'join', name, color }));
  ws.onerror = () => { $('joinErr').textContent = "Can't reach the server. Is server.py running on that computer?"; $('go').disabled = false; };
  ws.onclose = () => { if (joined) toast('Lost connection to the server. Refresh to rejoin.', 60000); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.t === 'welcome') onWelcome(m);
    else if (m.t === 'state') onState(m);
    else if (m.t === 'chat') addChat(m.from, m.color, m.text);
  };
}
function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }

function onWelcome(m) {
  myId = m.id; world = m.world; nightSeconds = world.nightSeconds;
  makeRenderer();
  buildWorld(world);
  me.x = SPAWN.x; me.z = SPAWN.z; me.y = groundAt(me.x, me.z);
  me.yaw = Math.atan2(-(cabins[0].x - me.x), -(cabins[0].z - me.z));
  const gb = $('galeBand'); gb.style.left = (world.galeStart / world.dawn * 100) + '%'; gb.style.width = ((world.galeEnd - world.galeStart) / world.dawn * 100) + '%';
  $('join').classList.add('hidden'); $('hud').classList.remove('hidden');
  joined = true;
  buildCabinList();
  toast('Find a cabin, haul firewood, and keep the stoves lit through the 2:00 AM gale.', 9000);
  toast('Glowing windows and chimney smoke mean a fire is burning.', 9000);
  startAudio();
  if (!qs.has('auto')) $('clickOv').classList.remove('hidden');
  requestAnimationFrame(frame);
}

function onState(m) {
  S = m;
  if (Math.abs(localM - m.m) > 1.5) localM = m.m;
  m.cabins.forEach((cs, i) => { if (cabins[i]) cabins[i].state = cs; });
  syncLogs(m.logs);
  const ids = new Set();
  for (const p of m.players) {
    ids.add(p.id);
    if (p.id === myId) {
      if (p.tp) { me.x = p.x; me.z = p.z; me.y = groundAt(p.x, p.z); me.vx = me.vz = me.vy = 0; }
      continue;
    }
    let a = avatars.get(p.id);
    if (!a) { a = makeAvatar(p); avatars.set(p.id, a); a.g.position.set(p.x, p.y, p.z); }
    a.t = p; if (p.tp) a.g.position.set(p.x, p.y, p.z);
  }
  for (const [id, a] of avatars) if (!ids.has(id)) { scene.remove(a.g); avatars.delete(id); }
  for (const e of m.events) toast(e);
  updateHud();
}

// ------------------------------------------------------------------- input
const keys = {};
let locked = false, chatting = false, bigMap = false;
addEventListener('keydown', (e) => {
  if (!joined) return;
  if (chatting) {
    if (e.key === 'Enter') { const v = $('chatIn').value.trim(); if (v) send({ t: 'chat', text: v }); closeChat(); }
    else if (e.key === 'Escape') closeChat();
    return;
  }
  keys[e.code] = true;
  if (e.code === 'KeyE') interact();
  if (e.code === 'KeyT' || e.code === 'Enter') { e.preventDefault(); openChat(); }
  if (e.code === 'KeyM') { bigMap = !bigMap; resizeMap(); }
  if (e.code === 'Tab') { e.preventDefault(); $('roster').classList.remove('hidden'); renderRoster(); }
});
addEventListener('keyup', (e) => { keys[e.code] = false; if (e.code === 'Tab') $('roster').classList.add('hidden'); });
addEventListener('blur', () => { for (const k in keys) keys[k] = false; });
function openChat() { chatting = true; const ci = $('chatIn'); ci.classList.remove('hidden'); ci.value = ''; ci.focus(); if (document.pointerLockElement) document.exitPointerLock(); for (const k in keys) keys[k] = false; }
function closeChat() { chatting = false; $('chatIn').classList.add('hidden'); $('chatIn').blur(); canvas.requestPointerLock?.(); }
$('clickOv').addEventListener('click', () => canvas.requestPointerLock?.());
canvas.addEventListener('click', () => { if (joined && !locked) canvas.requestPointerLock?.(); });
document.addEventListener('pointerlockchange', () => {
  locked = document.pointerLockElement === canvas;
  $('clickOv').classList.toggle('hidden', locked || chatting || qs.has('auto'));
});
addEventListener('mousemove', (e) => {
  if (!locked) return;
  me.yaw -= e.movementX * 0.0022; me.pitch = clamp(me.pitch - e.movementY * 0.0022, -1.45, 1.45);
});

// ------------------------------------------------------------- interaction
let target = null;
function interactTargets() {
  const out = [];
  for (const m of logMeshes.values()) out.push({ kind: 'log', id: m.userData.id, x: m.position.x, z: m.position.z, y: m.position.y });
  cabins.forEach((c, i) => {
    if (Math.hypot(me.x - c.x, me.z - c.z) > 12) return;
    const add = (kind, [lx, lz], y, extra) => { const [x, z] = toWorld(c, lx, lz); out.push({ kind, cabin: i, x, z, y: c.base + y, ...extra }); };
    add('stove', STOVE, 1.2); add('woodbox', WOODBOX, 0.8); add('pantry', PANTRY, 1.3);
    CANDLES.forEach((cd, k) => add('candle', cd, cd[2], { i: k }));
  });
  return out;
}
function findTarget() {
  const fx = -Math.sin(me.yaw), fz = -Math.cos(me.yaw);
  const eye = me.y + 1.62, look = Math.sin(me.pitch);
  let best = null, bs = 1e9;
  for (const t of interactTargets()) {
    const dx = t.x - me.x, dz = t.z - me.z, d = Math.hypot(dx, dz);
    const reach = t.kind === 'log' ? 3.2 : 2.9;
    if (d > reach) continue;
    const facing = d < 0.01 ? 1 : (dx * fx + dz * fz) / d;
    if (facing < 0.35 && d > 1.2) continue;
    const vert = Math.abs((t.y - eye) / Math.max(d, 0.6) - look * 1.2);
    const s = d * 0.6 + (1 - facing) * 3 + vert * 0.8;
    if (s < bs) { bs = s; best = t; }
  }
  return best;
}
function myState() { return S ? S.players.find((p) => p.id === myId) : null; }
function promptFor(t) {
  const p = myState(); if (!p || !t) return null;
  if (t.kind === 'log') return p.c < 4 ? ['E', 'Pick up firewood'] : [null, 'Your arms are full (4 logs)'];
  const cs = cabins[t.cabin].state; if (!cs) return null;
  if (t.kind === 'woodbox') return p.c > 0 ? ['E', `Stack ${Math.min(p.c, 12 - cs.wood)} log${p.c > 1 ? 's' : ''} in the woodbox (${cs.wood}/12)`] : cs.wood > 0 ? ['E', `Take a log (${cs.wood} stacked)`] : [null, 'Woodbox is empty'];
  if (t.kind === 'pantry') return p.can ? [null, 'Already holding a can'] : cs.cans > 0 ? ['E', `Take a can of stew (${cs.cans} left)`] : [null, 'Pantry is empty'];
  if (t.kind === 'candle') return cs.candles[t.i] ? ['E', 'Blow out candle'] : cs.fuel > 0 ? ['E', 'Light candle from the stove'] : [null, 'Light the stove first'];
  if (t.kind === 'stove') {
    if (cs.pot >= 20) return ['E', 'Eat the hot stew'];
    if (p.can && cs.pot < 0) return ['E', 'Set the can on the stove to heat'];
    const src = p.c > 0 ? '' : cs.wood > 0 ? ' from the woodbox' : null;
    const heat = cs.pot >= 0 ? `  ·  stew ${Math.round(cs.pot / 20 * 100)}%` : '';
    if (src !== null) {
      if (cs.fuel > 91) return [null, 'Stove is roaring — full' + heat];
      return ['E', (cs.fuel <= 0 ? 'Light the stove' : 'Add a log') + src + heat];
    }
    return [null, (cs.fuel > 0 ? 'Fire burning — bring more logs' : 'Stove is cold — bring firewood') + heat];
  }
  return null;
}
function interact() {
  const t = target; if (!t || !S) return;
  if (t.kind === 'log') send({ t: 'act', a: 'pickup', id: t.id });
  else send({ t: 'act', a: t.kind, cabin: t.cabin, i: t.i });
}

// ----------------------------------------------------------------- physics
function collide() {
  const R = 0.4;
  for (const c of cabins) {
    if (Math.abs(me.x - c.x) > 12 || Math.abs(me.z - c.z) > 12) continue;
    let [lx, lz] = toLocal(c, me.x, me.z);
    for (const [x0, x1, z0, z1] of c.solids) {
      const cx = clamp(lx, x0, x1), cz = clamp(lz, z0, z1);
      const dx = lx - cx, dz = lz - cz, d = Math.hypot(dx, dz);
      if (d < R) {
        if (d > 1e-4) { lx = cx + dx / d * R; lz = cz + dz / d * R; }
        else { // inside the box: push out along the shortest axis
          const opts = [[x0 - R - lx, 0], [x1 + R - lx, 0], [0, z0 - R - lz], [0, z1 + R - lz]].sort((a, b) => Math.abs(a[0] + a[1]) - Math.abs(b[0] + b[1]));
          lx += opts[0][0]; lz += opts[0][1];
        }
      }
    }
    [me.x, me.z] = toWorld(c, lx, lz);
  }
  const k0 = Math.floor(me.x / 10), k1 = Math.floor(me.z / 10);
  for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) {
    const cell = treeCells.get((k0 + a) + ',' + (k1 + b)); if (!cell) continue;
    for (const [tx, tz, tr] of cell) {
      const dx = me.x - tx, dz = me.z - tz, d = Math.hypot(dx, dz), m = tr + R;
      if (d < m && d > 1e-4) { me.x = tx + dx / d * m; me.z = tz + dz / d * m; }
    }
  }
  const r = Math.hypot(me.x, me.z); if (r > 300) { me.x *= 300 / r; me.z *= 300 / r; }
}

function movePlayer(dt, frozen) {
  const ice = onIce(me.x, me.z);
  let ix = 0, iz = 0;
  if (!frozen && !chatting) {
    if (keys.KeyW || keys.ArrowUp) iz -= 1; if (keys.KeyS || keys.ArrowDown) iz += 1;
    if (keys.KeyA || keys.ArrowLeft) ix -= 1; if (keys.KeyD || keys.ArrowRight) ix += 1;
  }
  const len = Math.hypot(ix, iz) || 1; ix /= len; iz /= len;
  const sp = (keys.ShiftLeft || keys.ShiftRight ? 9.5 : 5.2) * (S && S.gale > 0.5 && !insideAny() ? 0.8 : 1);
  const s = Math.sin(me.yaw), c = Math.cos(me.yaw);
  const wx = (ix * c + iz * s) * sp, wz = (-ix * s + iz * c) * sp;
  const acc = ice ? 2.2 : 14;
  me.vx += (wx - me.vx) * Math.min(1, acc * dt); me.vz += (wz - me.vz) * Math.min(1, acc * dt);
  // the gale shoves you around outdoors
  if (S && S.gale > 0 && !insideAny()) { const w = windVec(); me.vx += w.x * S.gale * 0.9 * dt; me.vz += w.y * S.gale * 0.9 * dt; }
  const ox = me.x, oz = me.z;
  me.x += me.vx * dt; me.z += me.vz * dt;
  collide();
  let g = groundAt(me.x, me.z);
  // block steep climbs (valley walls / cliffs)
  if (g - me.y > 0.9 && me.onGround) { me.x = ox; me.z = oz; me.vx *= 0.2; me.vz *= 0.2; g = groundAt(me.x, me.z); }
  if (!frozen && keys.Space && me.onGround) { me.vy = 7.2; me.onGround = false; }
  me.vy -= 22 * dt; me.y += me.vy * dt;
  if (me.y <= g + 0.02 || (me.onGround && me.y - g < 0.6 && me.vy <= 0)) { me.y = g; me.vy = 0; me.onGround = true; } else me.onGround = false;
}
function insideAny() { for (const c of cabins) { const [lx, lz] = toLocal(c, me.x, me.z); if (Math.abs(lx) < CABIN_W / 2 && Math.abs(lz) < CABIN_D / 2) return c; } return null; }
function windVec() { const a = 0.6 + Math.sin(clockU.value * 0.05) * 0.4; return new THREE.Vector2(Math.cos(a) * 9, Math.sin(a) * 9); }

// --------------------------------------------------------------- atmosphere
const C = (h) => new THREE.Color(h);
const SKY = {
  dusk: [C(0x29386a), C(0xd88a68)], night: [C(0x03060f), C(0x0e1a30)], dawn: [C(0x3d5d92), C(0xf0a47a)], gale: [C(0x4a5566), C(0x6f7b8c)],
};
const tmpA = new THREE.Color(), tmpB = new THREE.Color(), fogC = new THREE.Color();
function atmosphere(dt) {
  const m = localM, gale = S ? S.gale : 0;
  const dark = m < 150 ? smooth(0, 150, m) : m > 640 ? 1 - smooth(640, 720, m) * 0.8 : 1;
  const dawnMix = smooth(640, 720, m);
  tmpA.copy(SKY.dusk[0]).lerp(SKY.night[0], dark); tmpB.copy(SKY.dusk[1]).lerp(SKY.night[1], dark);
  if (m > 640) { tmpA.lerp(SKY.dawn[0], dawnMix * 0.8); tmpB.lerp(SKY.dawn[1], dawnMix * 0.8); }
  const gn = 1 - dark * 0.55;
  tmpA.lerp(tmpC(SKY.gale[0], gn), gale); tmpB.lerp(tmpC(SKY.gale[1], gn), gale);
  sky.material.uniforms.top.value.copy(tmpA); sky.material.uniforms.bot.value.copy(tmpB);
  fogC.copy(tmpB).lerp(tmpA, 0.3);
  scene.fog.color.copy(fogC);
  const inside = insideAny();
  scene.fog.density = lerp(lerp(0.0045, 0.0075, dark), inside ? 0.02 : 0.05, gale);
  stars.material.opacity = dark * (1 - gale);
  moon.material.opacity = dark * (1 - gale) * 0.95;
  stars.visible = moon.visible = snow.visible = !inside;
  sky.position.copy(camera.position); stars.position.copy(camera.position);
  moon.position.set(camera.position.x - 380, camera.position.y + 420, camera.position.z - 520);
  hemi.intensity = lerp(1.05, 0.32, dark) * (1 - gale * 0.25);
  hemi.color.set(0x8aa4d6).lerp(C(0xaab4c4), gale);
  moonLight.intensity = 0.45 * (1 - gale * 0.7);
  frostU.value = 0.3 + gale * 0.6;
  terrainMat.color.set(0xffffff).lerp(C(0xd6e4ff), gale * 0.6);
  // snow
  const u = snow.material.uniforms, w = windVec();
  u.uCam.value.copy(camera.position);
  u.uWind.value.set(w.x * gale * 1.8 + 0.4, w.y * gale * 1.8);
  u.uFall.value = 2.2 + gale * 3;
  u.uDen.value = 0.3 + gale * 0.7;
  u.uBox.value.set(inside ? 40 : 70, 36, inside ? 40 : 70);
  // frost vignette
  const p = myState();
  const cold = p ? clamp((55 - p.w) / 55, 0, 1) : 0;
  $('frost').style.opacity = Math.max(cold * 0.95, inside ? 0 : gale * 0.35).toFixed(3);
  lantern.intensity = 3.2 * (0.9 + Math.sin(clockU.value * 13) * 0.03 + Math.sin(clockU.value * 7.3) * 0.04);
  if (mast) mast.material.opacity = Math.sin(clockU.value * 3) > 0.2 ? 1 : 0.08;
}
function tmpC(c, k) { return c.clone().multiplyScalar(k); }

function animateCabins(t) {
  const w = windVec(), gale = S ? S.gale : 0;
  for (const c of cabins) {
    const cs = c.state; if (!cs) continue;
    const f = cs.fuel / 100, lit = cs.fuel > 0, nc = cs.candles.reduce((a, b) => a + b, 0);
    const flick = 0.85 + Math.sin(t * 11 + c.i) * 0.08 + Math.sin(t * 23.7 + c.i * 3) * 0.07;
    c.light.intensity = lit ? (2.2 + f * 2.2) * flick + nc * 0.5 : nc * 0.35;
    for (const win of c.windows) win.material = lit || nc ? winOn : winOff;
    c.stoveGlow.color.setRGB(lit ? 1 * flick : 0.07, lit ? 0.45 * flick : 0.07, lit ? 0.12 : 0.07);
    c.beacon.material.opacity = lit ? (0.25 + f * 0.45) * flick : 0;
    c.smoke.material.uniforms.uA.value = lit ? 0.4 + f * 0.6 : 0;
    c.smoke.material.uniforms.uWind.value.set(0.25 + w.x / 9 * (0.3 + gale), w.y / 9 * (0.3 + gale));
    c.woodLogs.forEach((l, i) => (l.visible = i < cs.wood));
    c.cans.forEach((cn, i) => (cn.visible = i < cs.cans));
    c.candleFlames.forEach((fl, i) => { fl.visible = !!cs.candles[i]; fl.scale.set(0.3 + Math.sin(t * 17 + i) * 0.03, 0.48 + Math.sin(t * 13 + i * 2) * 0.05, 1); });
    c.pot.visible = cs.pot >= 0; c.steam.visible = cs.pot >= 20; c.steam.position.y = 1.65 + Math.sin(t * 2) * 0.08;
    if (c.wisps) c.wisps.forEach((wp) => { const d = wp.userData; d.a += 0.004; wp.position.set(c.x + Math.cos(d.a) * d.r, groundAt(c.x + Math.cos(d.a) * d.r, c.z + Math.sin(d.a * 1.3) * d.r) + d.y + Math.sin(t + d.r) * 0.3, c.z + Math.sin(d.a * 1.3) * d.r); });
  }
}

function animateAvatars(dt, t) {
  const k = 1 - Math.exp(-12 * dt);
  for (const a of avatars.values()) {
    const p = a.t, g = a.g;
    const moving = Math.hypot(p.x - g.position.x, p.z - g.position.z) > 0.03;
    g.position.x = lerp(g.position.x, p.x, k); g.position.z = lerp(g.position.z, p.z, k); g.position.y = lerp(g.position.y, p.y, k);
    let d = p.ry - g.rotation.y; d = Math.atan2(Math.sin(d), Math.cos(d)); g.rotation.y += d * k;
    g.children[0].position.y = 0.78 + (moving ? Math.abs(Math.sin(t * 9)) * 0.06 : 0);
    a.back.forEach((l, i) => (l.visible = i < p.c));
    a.coat.color.copy(a.base); if (p.f > 0) a.coat.color.lerp(C(0xcfe9ff), 0.85);
    g.rotation.z = p.f > 0 ? 0.25 : 0;
  }
}

// --------------------------------------------------------------------- hud
function fmtClock(m) { const tot = 18 * 60 + Math.floor(m); let h = Math.floor(tot / 60) % 24; const mm = tot % 60; const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12; return `${h}:${String(mm).padStart(2, '0')} ${ap}`; }
function fmtDur(gm) { const s = Math.max(0, gm * nightSeconds / 720); return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`; }
function buildCabinList() {
  $('cabList').innerHTML = cabins.map((c, i) => `<div class="cab" id="cab${i}"><div class="row"><span class="nm"><span class="dot" style="background:${REGION_COL[c.region]}"></span>${c.name}</span><span class="meta" id="cabM${i}"></span></div><div class="bar"><i id="cabB${i}"></i></div></div>`).join('');
}
function updateHud() {
  if (!S || !world) return;
  const p = myState(); if (!p) return;
  $('clockT').textContent = fmtClock(localM);
  const ph = $('clockP');
  if (S.phase === 'dawn') { ph.textContent = 'Dawn — the gale has passed'; ph.className = 'ph'; }
  else if (S.m >= world.galeStart && S.m < world.galeEnd) { ph.textContent = `ICY GALE · ends in ${fmtDur(world.galeEnd - S.m)}`; ph.className = 'ph gale'; }
  else if (S.m < world.galeStart) { const left = world.galeStart - S.m; ph.textContent = `Gale arrives in ${fmtDur(left)}`; ph.className = left < 90 ? 'ph warn' : 'ph'; }
  else { ph.textContent = `Dawn in ${fmtDur(world.dawn - S.m)}`; ph.className = 'ph'; }
  $('nightNow').style.left = clamp(S.m / world.dawn * 100, 0, 100) + '%';
  $('warmBar').style.width = p.w + '%'; $('warmN').textContent = Math.round(p.w);
  $('hungBar').style.width = p.h + '%'; $('hungN').textContent = Math.round(p.h);
  document.querySelectorAll('#carry .logdot').forEach((d, i) => d.classList.toggle('on', i < p.c));
  const ct = $('canTag'); ct.classList.toggle('hidden', !p.can); ct.textContent = 'Cold can';
  cabins.forEach((c, i) => {
    const cs = c.state; if (!cs) return;
    const here = S.players.filter((q) => q.in === i);
    const el = $('cab' + i); el.classList.toggle('here', p.in === i); el.classList.toggle('out', cs.fuel <= 0);
    $('cabB' + i).style.width = cs.fuel + '%';
    $('cabM' + i).innerHTML = `${cs.fuel > 0 ? Math.round(cs.fuel) + '%' : 'cold'} · wood ${cs.wood}` + (here.length ? `<span class="pips">${here.map((q) => `<b style="background:${q.color}"></b>`).join('')}</span>` : '');
  });
  $('frozenOv').classList.toggle('hidden', !(p.f > 0));
  if (p.f > 0) $('frozenMsg').textContent = `Thawing out by the nearest fire in ${Math.ceil(p.f)}…`;
  const dov = $('dawnOv');
  if (S.phase === 'dawn' && S.summary) {
    dov.classList.remove('hidden');
    const s = S.summary;
    $('dawnCard').innerHTML = `<div class="big">Dawn breaks</div><div style="color:var(--dim);margin-top:6px">${s.litCabins.length} of 5 cabins still burning${s.litCabins.length ? ': ' + s.litCabins.join(', ') : ''}</div>
      <table><tr><th></th><th>Hiker</th><th>Logs hauled</th><th>Times frozen</th></tr>${s.players.map((r, i) => `<tr><td>${i === 0 ? '★' : ''}</td><td><span class="dot" style="background:${r.color}"></span>${esc(r.name)}</td><td>${r.logs}</td><td>${r.freezes}</td></tr>`).join('')}</table>
      <div style="color:var(--dim);margin-top:12px">Next night begins in ${Math.ceil(S.dawnIn)}s</div>`;
  } else dov.classList.add('hidden');
}
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function renderRoster() {
  if (!S) return;
  $('roster').innerHTML = `<table><tr><th>Hiker</th><th>Warmth</th><th>Where</th></tr>${S.players.map((p) => `<tr><td><span class="dot" style="background:${p.color}"></span>${esc(p.name)}${p.id === myId ? ' (you)' : ''}</td><td>${p.f > 0 ? 'frozen' : Math.round(p.w)}</td><td>${p.in >= 0 ? cabins[p.in].name : 'outdoors'}</td></tr>`).join('')}</table>`;
}
function toast(text, ms = 4500) {
  const t = document.createElement('div'); t.className = 'toast panel'; t.textContent = text; $('toasts').appendChild(t);
  while ($('toasts').children.length > 4) $('toasts').firstChild.remove();
  setTimeout(() => { t.style.opacity = 0; setTimeout(() => t.remove(), 700); }, ms);
}
function addChat(from, color, text) {
  const d = document.createElement('div'); d.innerHTML = `<b style="color:${esc(color)}">${esc(from)}:</b> ${esc(text)}`; $('chatLog').appendChild(d);
  while ($('chatLog').children.length > 7) $('chatLog').firstChild.remove();
  setTimeout(() => { d.style.opacity = 0.25; }, 20000);
}

const proj = new THREE.Vector3();
function updateTags() {
  const inside = insideAny();
  cabins.forEach((c, i) => {
    const el = cabinTags[i], d = Math.hypot(c.x - me.x, c.z - me.z);
    proj.set(c.x, c.base + 8, c.z).project(camera);
    if (inside || d < 16 || proj.z > 1 || Math.abs(proj.x) > 1.05 || Math.abs(proj.y) > 1.05) { el.style.display = 'none'; return; }
    el.style.display = ''; el.style.left = ((proj.x + 1) / 2 * innerWidth) + 'px'; el.style.top = ((1 - proj.y) / 2 * innerHeight) + 'px';
    const lit = c.state && c.state.fuel > 0;
    el.className = 'ctag' + (lit ? ' lit' : '');
    el.innerHTML = `<b>${c.name}</b>${Math.round(d)} m · ${lit ? 'fire lit' : 'cold'}`;
  });
}

// --------------------------------------------------------------------- map
function resizeMap() { const cv = $('map'), s = bigMap ? Math.min(560, innerHeight - 80) : 190; cv.width = cv.height = s; $('mapWrap').classList.toggle('big', bigMap); }
function drawMap() {
  const cv = $('map'), g = cv.getContext('2d'), s = cv.width, k = s / 600, X = (x) => s / 2 + x * k, Z = (z) => s / 2 + z * k;
  g.fillStyle = '#121a28'; g.fillRect(0, 0, s, s);
  g.fillStyle = '#1a2436'; g.beginPath(); g.arc(s / 2, s / 2, 295 * k, 0, 7); g.fill();
  g.fillStyle = 'rgba(143,208,255,.35)'; g.beginPath(); g.arc(X(LAKE.x), Z(LAKE.z), LAKE.r * k, 0, 7); g.fill();
  g.fillStyle = 'rgba(200,215,235,.12)'; g.beginPath(); g.arc(X(0), Z(-275), 70 * k, 0, 7); g.fill();
  g.fillStyle = '#8a6a4a';
  for (const m of logMeshes.values()) { if (Math.hypot(m.position.x - me.x, m.position.z - me.z) < (bigMap ? 400 : 90)) g.fillRect(X(m.position.x) - 1, Z(m.position.z) - 1, bigMap ? 3 : 2, bigMap ? 3 : 2); }
  for (const c of cabins) {
    const lit = c.state && c.state.fuel > 0;
    if (lit) { const gr = g.createRadialGradient(X(c.x), Z(c.z), 0, X(c.x), Z(c.z), 14 * (bigMap ? 2 : 1)); gr.addColorStop(0, 'rgba(255,170,80,.8)'); gr.addColorStop(1, 'rgba(255,120,40,0)'); g.fillStyle = gr; g.fillRect(X(c.x) - 30, Z(c.z) - 30, 60, 60); }
    g.save(); g.translate(X(c.x), Z(c.z)); g.rotate(-c.rot);
    g.fillStyle = lit ? '#ffb35c' : '#64748b'; g.fillRect(-4, -3, 8, 6); g.restore();
    if (bigMap) { g.fillStyle = '#dfe7f3'; g.font = '13px system-ui, sans-serif'; g.textAlign = 'center'; g.fillText(c.name, X(c.x), Z(c.z) - 10); }
  }
  g.fillStyle = '#f3e3c3'; g.fillRect(X(SPAWN.x) - 2, Z(SPAWN.z) - 2, 4, 4);
  if (S) for (const p of S.players) { if (p.id === myId) continue; g.fillStyle = p.color; g.beginPath(); g.arc(X(p.x), Z(p.z), bigMap ? 5 : 3.5, 0, 7); g.fill(); }
  g.save(); g.translate(X(me.x), Z(me.z)); g.rotate(-me.yaw); g.fillStyle = '#fff'; g.beginPath(); g.moveTo(0, -7); g.lineTo(4.5, 5); g.lineTo(0, 2.5); g.lineTo(-4.5, 5); g.closePath(); g.fill(); g.restore();
}

// ------------------------------------------------------------------- audio
let actx, windGain, windFilter, crackleGain;
function startAudio() {
  try {
    actx = new (window.AudioContext || window.webkitAudioContext)();
    const len = actx.sampleRate * 2, buf = actx.createBuffer(1, len, actx.sampleRate), d = buf.getChannelData(0);
    let last = 0; for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = last * 3.5; }
    const src = actx.createBufferSource(); src.buffer = buf; src.loop = true;
    windFilter = actx.createBiquadFilter(); windFilter.type = 'bandpass'; windFilter.frequency.value = 500; windFilter.Q.value = 0.6;
    windGain = actx.createGain(); windGain.gain.value = 0;
    src.connect(windFilter).connect(windGain).connect(actx.destination); src.start();
    crackleGain = actx.createGain(); crackleGain.gain.value = 0; crackleGain.connect(actx.destination);
  } catch (e) { actx = null; }
}
let crackleT = 0;
function audioTick(dt, t) {
  if (!actx) return;
  if (actx.state === 'suspended') actx.resume();
  const gale = S ? S.gale : 0, inside = insideAny();
  const target = (0.05 + gale * 0.4) * (inside ? 0.3 : 1);
  windGain.gain.value += (target - windGain.gain.value) * Math.min(1, dt * 2);
  windFilter.frequency.value = 350 + gale * 500 + Math.sin(t * 0.7) * 120 + Math.sin(t * 2.3) * 60;
  // fire crackle near a lit stove
  let near = 0;
  for (const c of cabins) { if (!c.state || c.state.fuel <= 0) continue; const [x, z] = toWorld(c, ...STOVE); near = Math.max(near, 1 - Math.hypot(me.x - x, me.z - z) / 9); }
  crackleT -= dt;
  if (near > 0 && crackleT <= 0) {
    crackleT = 0.03 + Math.random() * 0.25;
    const n = actx.createBufferSource(), b = actx.createBuffer(1, 400, actx.sampleRate), dd = b.getChannelData(0);
    for (let i = 0; i < 400; i++) dd[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / 400, 3);
    n.buffer = b; const gg = actx.createGain(); gg.gain.value = near * (0.08 + Math.random() * 0.18); n.connect(gg).connect(actx.destination); n.start();
  }
}

// -------------------------------------------------------------------- loop
let last = performance.now(), sendAcc = 0;
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  clockU.value += dt;
  if (S && S.phase === 'night') localM += dt * 720 / nightSeconds;
  const p = myState(), frozen = p && p.f > 0;
  movePlayer(dt, frozen);
  const bob = me.onGround && (Math.abs(me.vx) + Math.abs(me.vz) > 1) ? Math.sin(clockU.value * 10) * 0.04 : 0;
  camera.position.set(me.x, me.y + (frozen ? 0.6 : 1.62) + bob, me.z);
  camera.rotation.set(me.pitch, me.yaw, frozen ? 0.4 : 0, 'YXZ');
  sendAcc += dt;
  if (sendAcc > 1 / 15) { sendAcc = 0; send({ t: 'pos', x: +me.x.toFixed(2), y: +me.y.toFixed(2), z: +me.z.toFixed(2), ry: +me.yaw.toFixed(3) }); }
  target = frozen ? null : findTarget();
  const pr = promptFor(target), pe = $('prompt');
  if (pr) { pe.classList.remove('hidden'); pe.innerHTML = (pr[0] ? `<kbd>${pr[0]}</kbd>` : '') + esc(pr[1]); } else pe.classList.add('hidden');
  atmosphere(dt);
  animateCabins(clockU.value);
  animateAvatars(dt, clockU.value);
  updateTags();
  if ((frameN++ & 3) === 0) { drawMap(); if (!$('roster').classList.contains('hidden')) renderRoster(); }
  if (S) $('clockT').textContent = fmtClock(localM);
  audioTick(dt, clockU.value);
  renderer.render(scene, camera);
}
let frameN = 0;

// --------------------------------------------------------------- join form
const COLORS = ['#e8a33d', '#d9534f', '#4fa3e0', '#5cb85c', '#a77bd8', '#f06fa8', '#3fc1b5', '#f1f1f1'];
let pickedColor = store('ffc.color') || COLORS[Math.floor(Math.random() * COLORS.length)];
$('swatches').innerHTML = COLORS.map((c) => `<div class="sw" data-c="${c}" style="background:${c}"></div>`).join('');
const paintSw = () => document.querySelectorAll('.sw').forEach((s) => s.classList.toggle('on', s.dataset.c === pickedColor));
document.querySelectorAll('.sw').forEach((s) => s.addEventListener('click', () => { pickedColor = s.dataset.c; paintSw(); }));
paintSw();
const paintQ = () => document.querySelectorAll('#quality button').forEach((b) => b.classList.toggle('on', b.dataset.q === quality));
document.querySelectorAll('#quality button').forEach((b) => b.addEventListener('click', () => { quality = b.dataset.q; paintQ(); }));
paintQ();
$('nameIn').value = qs.get('name') || store('ffc.name') || '';
function join() {
  const name = $('nameIn').value.trim() || 'Hiker';
  store('ffc.name', name); store('ffc.color', pickedColor); store('ffc.quality', quality);
  $('go').disabled = true; $('joinErr').textContent = '';
  connect(name, pickedColor);
}
$('go').addEventListener('click', join);
$('nameIn').addEventListener('keydown', (e) => { if (e.key === 'Enter') join(); });
if (qs.has('auto')) join();

// handy for testing / screenshots: window.ff.go(x, z, yaw, pitch)
window.ff = { me, go(x, z, yaw = me.yaw, pitch = 0) { me.x = x; me.z = z; me.y = groundAt(x, z); me.yaw = yaw; me.pitch = pitch; }, cabins: () => cabins, send, state: () => S };
