/* PiPortal client */
(() => {
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const wsBase = (location.protocol === "https:" ? "wss://" : "ws://") + location.host;
const store = {
  get(k, d) { try { const v = localStorage.getItem("pp." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("pp." + k, JSON.stringify(v)); } catch {} },
};

async function api(path, opts = {}) {
  const o = { credentials: "same-origin", ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(o.json); }
  const r = await fetch(path, o);
  if (r.status === 401) { showLogin(); throw new Error("Signed out"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

let toastT;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.classList.toggle("bad", bad); t.classList.add("show");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 3200);
}

const fmtBytes = (b) => {
  if (b == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (b >= 100 || i === 0 ? b.toFixed(0) : b.toFixed(1)) + " " + u[i];
};
const fmtRate = (b) => fmtBytes(b) + "/s";
const fmtUptime = (s) => {
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------------------------------------------ login */
function showLogin() {
  $("#app").hidden = true; $("#login").hidden = false;
  setTimeout(() => $("#pw").focus(), 50);
}
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#loginErr").textContent = "";
  try {
    await api("/api/login", { json: { password: $("#pw").value } });
    $("#pw").value = "";
    start();
  } catch (err) { $("#loginErr").textContent = err.message; }
});
$("#logoutBtn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  disconnectDesktop(); audio.stop(); location.reload();
});

/* ------------------------------------------------------------------ navigation */
let currentView = "desktop";
function show(view) {
  currentView = view;
  $$(".nav[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("on", v.id === "v-" + view));
  if (view === "overview") refreshStats();
  if (view === "processes") refreshProcs();
  if (view === "services") refreshServices();
  if (view === "terminal") openTerminal();
  if (view === "files") loadFiles(filesPath);
  if (view === "fleet") renderFleet();
  location.hash = view;
}
$$(".nav[data-view]").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

/* ------------------------------------------------------------------ info */
let INFO = {};
async function loadInfo() {
  INFO = await api("/api/info");
  document.title = INFO.hostname + " · PiPortal";
  $("#hostName").textContent = INFO.hostname;
  $("#hostModel").textContent = INFO.model;
  $("#ovHost").textContent = INFO.hostname;
  $("#ovSub").textContent = `${INFO.model} — ${INFO.os || "Linux"}, kernel ${INFO.kernel}`;
  $("#ipList").textContent = INFO.ips.map((i) => `${i.if}: ${i.ip}`).join("   ");
  // Pi 4 / 400 have a slower encoder path — start them on the lighter picture setting.
  if (!store.get("quality") && (INFO.family === "pi4" || INFO.family === "pi400")) $("#quality").value = "3";
}

/* ------------------------------------------------------------------ desktop (noVNC) */
let rfb = null;
const screenEl = $("#screen");

function connectDesktop() {
  if (rfb) return;
  $("#deskErr").textContent = "";
  $("#screenIdle").hidden = true;
  setConnectBtn("connecting");
  try {
    rfb = new RFB(screenEl, wsBase + "/ws/vnc", { wsProtocols: ["binary"] });
  } catch (e) {
    $("#deskErr").textContent = e.message; $("#screenIdle").hidden = false; setConnectBtn("off"); return;
  }
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.showDotCursor = true;
  rfb.focusOnClick = true;
  applyQuality();
  rfb.addEventListener("connect", () => {
    setConnectBtn("on"); rfb.focus();
    if (store.get("autoSound", false) && !audio.on) audio.start();
  });
  rfb.addEventListener("disconnect", (e) => {
    const clean = e.detail.clean;
    rfb = null; setConnectBtn("off");
    $("#screenIdle").hidden = false;
    if (!clean) {
      $("#deskErr").textContent = "Couldn't reach the desktop. Make sure the Pi has booted to its desktop, then try again.";
      api("/api/vnc").then((s) => { if (s.log && s.log.length) $("#deskErr").textContent += " (" + s.log.slice(-1)[0] + ")"; }).catch(() => {});
    }
  });
  rfb.addEventListener("clipboard", (e) => { $("#clipText").value = e.detail.text; });
}
function disconnectDesktop() { if (rfb) { rfb.disconnect(); rfb = null; } }
function setConnectBtn(state) {
  const b = $("#connectBtn");
  b.classList.toggle("primary", state !== "on");
  b.innerHTML = state === "on" ? '<svg><use href="#i-stop"/></svg>Disconnect'
    : state === "connecting" ? '<svg><use href="#i-play"/></svg>Connecting…' : '<svg><use href="#i-play"/></svg>Connect';
}
function applyQuality() {
  if (!rfb) return;
  const q = +$("#quality").value;
  rfb.qualityLevel = q;
  rfb.compressionLevel = q >= 9 ? 1 : q >= 6 ? 2 : 6;
}
$("#connectBtn").addEventListener("click", () => (rfb ? disconnectDesktop() : connectDesktop()));
$("#connectBtn2").addEventListener("click", connectDesktop);
$("#quality").addEventListener("change", () => { store.set("quality", $("#quality").value); applyQuality(); });
$("#cadBtn").addEventListener("click", () => rfb && rfb.sendCtrlAltDel());
$("#fsBtn").addEventListener("click", () => {
  const el = $("#v-desktop");
  if (document.fullscreenElement) document.exitFullscreen();
  else (el.requestFullscreen || el.webkitRequestFullscreen).call(el);
});
$("#clipBtn").addEventListener("click", () => $("#clipDlg").showModal());
$("#clipSend").addEventListener("click", () => { if (rfb) { rfb.clipboardPasteFrom($("#clipText").value); toast("Sent to the Pi's clipboard"); } });

// On-screen keyboard for phones/tablets: a hidden input that forwards keys.
const mk = $("#mobileKbd");
$("#kbdBtn").addEventListener("click", () => { mk.style.left = "0"; mk.focus(); });
mk.addEventListener("blur", () => { mk.style.left = "-9999px"; });
mk.addEventListener("input", (e) => {
  if (!rfb) return;
  for (const ch of mk.value) {
    const cp = ch.codePointAt(0);
    const keysym = cp < 0x100 ? cp : 0x01000000 + cp;
    rfb.sendKey(keysym);
  }
  mk.value = "";
});
mk.addEventListener("keydown", (e) => {
  if (!rfb) return;
  const map = { Backspace: 0xff08, Enter: 0xff0d, Tab: 0xff09, Escape: 0xff1b, ArrowLeft: 0xff51, ArrowUp: 0xff52, ArrowRight: 0xff53, ArrowDown: 0xff54 };
  if (map[e.key]) { e.preventDefault(); rfb.sendKey(map[e.key]); }
});

/* ------------------------------------------------------------------ sound */
const audio = {
  on: false, ws: null, ctx: null, gain: null, analyser: null, next: 0, rate: 32000, ch: 2,
  async start() {
    if (this.on) return;
    const [rate, ch] = $("#audioQ").value.split("/").map(Number);
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AC({ latencyHint: "interactive" });
      this.gain = this.ctx.createGain();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      this.gain.connect(this.analyser); this.analyser.connect(this.ctx.destination);
    }
    await this.ctx.resume();
    // iOS: play a silent buffer inside the tap so the speaker route unlocks.
    const unlock = this.ctx.createBufferSource(); unlock.buffer = this.ctx.createBuffer(1, 1, 22050); unlock.connect(this.ctx.destination); unlock.start();
    this.gain.gain.value = $("#vol").value / 100;
    this.on = true; this.next = 0;
    setSoundBtn(true);
    this.ws = new WebSocket(`${wsBase}/ws/audio?rate=${rate}&ch=${ch}`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const m = JSON.parse(ev.data);
        if (m.error) { toast(m.error, true); this.stop(); }
        else { this.rate = m.rate; this.ch = m.ch; }
        return;
      }
      this.play(ev.data);
    };
    this.ws.onclose = () => {
      if (this.on) { // dropped — retry quietly
        this.ws = null; this.on = false; setSoundBtn(false);
        setTimeout(() => { if (store.get("soundWanted", false)) this.start(); }, 1500);
      }
    };
    store.set("soundWanted", true);
    drawVU();
  },
  play(buf) {
    const ctx = this.ctx, ch = this.ch;
    const pcm = new Int16Array(buf);
    const frames = pcm.length / ch | 0;
    if (!frames) return;
    const ab = ctx.createBuffer(ch, frames, this.rate);
    for (let c = 0; c < ch; c++) {
      const out = ab.getChannelData(c);
      for (let i = 0, j = c; i < frames; i++, j += ch) out[i] = pcm[j] / 32768;
    }
    const target = +$("#audioLat").value;
    const now = ctx.currentTime;
    if (this.next < now + 0.005) this.next = now + target;          // ran dry: rebuild the cushion
    else if (this.next > now + target + 0.35) return;                // too far behind: drop to catch up
    const src = ctx.createBufferSource();
    src.buffer = ab; src.connect(this.gain); src.start(this.next);
    this.next += ab.duration;
  },
  stop() {
    this.on = false; store.set("soundWanted", false);
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
    setSoundBtn(false);
  },
  restart() { if (this.on) { this.stop(); this.start(); } },
};
function setSoundBtn(on) {
  $("#soundBtn").setAttribute("aria-pressed", on ? "true" : "false");
  $("#soundBtn").title = on ? "Stop the Pi's sound" : "Stream the Pi's sound to this device";
}
$("#soundBtn").addEventListener("click", () => (audio.on ? audio.stop() : audio.start()));
$("#vol").addEventListener("input", () => { if (audio.gain) audio.gain.gain.value = $("#vol").value / 100; store.set("vol", $("#vol").value); });
$("#audioQ").addEventListener("change", () => { store.set("audioQ", $("#audioQ").value); audio.restart(); });
$("#audioLat").addEventListener("change", () => { store.set("audioLat", $("#audioLat").value); audio.next = 0; });

// Level meter: 12 LEDs, copper then raspberry near clipping.
const vu = $("#vu"), vctx = vu.getContext("2d");
let vuLevel = 0, vuRunning = false;
function drawVU() {
  if (vuRunning) return; vuRunning = true;
  const data = new Float32Array(512);
  const dpr = window.devicePixelRatio || 1;
  vu.width = 96 * dpr; vu.height = 22 * dpr; vctx.scale(dpr, dpr);
  (function frame() {
    let lvl = 0;
    if (audio.on && audio.analyser) {
      audio.analyser.getFloatTimeDomainData(data);
      let s = 0; for (const v of data) s += v * v;
      const rms = Math.sqrt(s / data.length);
      lvl = Math.min(1, Math.max(0, (20 * Math.log10(rms + 1e-6) + 48) / 48));
    }
    vuLevel = lvl > vuLevel ? lvl : vuLevel * 0.9;
    vctx.clearRect(0, 0, 96, 22);
    const n = 12, w = 6, gap = 2;
    for (let i = 0; i < n; i++) {
      const lit = vuLevel * n > i;
      vctx.fillStyle = lit ? (i >= 10 ? "#e0466f" : i >= 8 ? "#f0b867" : "#d39a4f") : "#24443a";
      vctx.fillRect(i * (w + gap), 4, w, 14);
    }
    if (audio.on || vuLevel > 0.01) requestAnimationFrame(frame); else { vuRunning = false; vctx.setTransform(1, 0, 0, 1, 0, 0); drawVUIdle(); }
  })();
}
function drawVUIdle() {
  const dpr = window.devicePixelRatio || 1;
  vu.width = 96 * dpr; vu.height = 22 * dpr; vctx.scale(dpr, dpr);
  vctx.fillStyle = "#24443a";
  for (let i = 0; i < 12; i++) vctx.fillRect(i * 8, 4, 6, 14);
}

/* ------------------------------------------------------------------ overview */
const cpuHist = [];
async function refreshStats() {
  let s;
  try { s = await api("/api/stats"); } catch { return; }
  $("#cpuPct").textContent = Math.round(s.cpu);
  $("#cores").innerHTML = s.cores.map((c, i) => `<div class="core" title="Core ${i}: ${c}%"><i style="height:${c}%"></i><b>${Math.round(c)}</b></div>`).join("");
  $("#cpuSub").textContent = `${INFO.cpus || s.cores.length} cores${s.freq_mhz ? ` at ${(s.freq_mhz / 1000).toFixed(2)} GHz` : ""} — load ${s.load.map((l) => l.toFixed(2)).join(" / ")} — up ${fmtUptime(s.uptime)}`;
  cpuHist.push(s.cpu); if (cpuHist.length > 90) cpuHist.shift();
  drawSpark();
  if (s.temp != null) {
    $("#temp").textContent = s.temp.toFixed(1);
    const pct = Math.min(100, Math.max(0, (s.temp - 30) / (85 - 30) * 100));
    $("#thermoFill").style.width = pct + "%";
    $("#thermoFill").style.backgroundPosition = `-${pct * 2}px 0`;
    $("#tempSub").textContent = (s.fan != null ? `Fan ${s.fan} rpm — ` : "") + (s.temp >= 80 ? "Throttling range" : s.temp >= 70 ? "Running warm" : "Comfortable");
  } else { $("#temp").textContent = "–"; $("#tempSub").textContent = "No sensor found"; }
  const memUsed = s.mem.total - s.mem.avail;
  $("#memPct").textContent = Math.round(memUsed / s.mem.total * 100);
  $("#memBar").style.width = memUsed / s.mem.total * 100 + "%";
  $("#memSub").textContent = `${fmtBytes(memUsed)} of ${fmtBytes(s.mem.total)}` + (s.swap.total ? ` — swap ${fmtBytes(s.swap.total - s.swap.free)} used` : "");
  $("#diskPct").textContent = Math.round(s.disk.used / s.disk.total * 100);
  $("#diskBar").style.width = s.disk.used / s.disk.total * 100 + "%";
  $("#diskSub").textContent = `${fmtBytes(s.disk.free)} free of ${fmtBytes(s.disk.total)}`;
  $("#netRx").textContent = fmtRate(s.net.rx);
  $("#netTx").textContent = fmtRate(s.net.tx);

  const h = [];
  const t = s.throttled;
  if (t) {
    h.push(t.undervolt_now ? ["bad", "Power supply too weak right now"] : t.undervolt_ever ? ["warn", "Power dipped since boot — check the supply"] : ["", "Power supply is steady"]);
    h.push(t.throttled_now ? ["bad", "Slowed down to protect itself"] : t.throttled_ever ? ["warn", "Was slowed down earlier since boot"] : ["", "Running at full speed"]);
  }
  if (s.temp != null) h.push(s.temp >= 80 ? ["bad", `Hot at ${s.temp.toFixed(0)}°C`] : s.temp >= 70 ? ["warn", `Warm at ${s.temp.toFixed(0)}°C`] : ["", `Cool at ${s.temp.toFixed(0)}°C`]);
  h.push(s.disk.free < 2e9 ? ["warn", "Storage nearly full"] : ["", "Plenty of storage"]);
  h.push(["", `${s.streams.desktop} desktop and ${s.streams.audio} sound stream${s.streams.audio === 1 ? "" : "s"} open`]);
  $("#health").innerHTML = h.map(([c, t]) => `<li class="${c}">${esc(t)}</li>`).join("");
}
function drawSpark() {
  const c = $("#cpuSpark"), x = c.getContext("2d");
  const w = c.clientWidth, hgt = 56, dpr = window.devicePixelRatio || 1;
  if (!w) return;
  c.width = w * dpr; c.height = hgt * dpr; x.scale(dpr, dpr);
  x.clearRect(0, 0, w, hgt);
  if (cpuHist.length < 2) return;
  const step = w / Math.max(cpuHist.length - 1, 30);
  x.beginPath();
  cpuHist.forEach((v, i) => { const px = w - (cpuHist.length - 1 - i) * step, py = hgt - 2 - v / 100 * (hgt - 4); i ? x.lineTo(px, py) : x.moveTo(px, py); });
  x.strokeStyle = "#d39a4f"; x.lineWidth = 1.6; x.stroke();
  x.lineTo(w, hgt); x.lineTo(w - (cpuHist.length - 1) * step, hgt); x.closePath();
  x.fillStyle = "rgba(211,154,79,.14)"; x.fill();
}
$$("[data-power]").forEach((b) => b.addEventListener("click", async () => {
  const a = b.dataset.power;
  if (!confirm(a === "reboot" ? `Restart ${INFO.hostname}? Open programs will close.` : `Shut down ${INFO.hostname}? You'll need to power it on by hand.`)) return;
  try { await api("/api/power", { json: { action: a } }); toast(a === "reboot" ? "Restarting…" : "Shutting down…"); disconnectDesktop(); audio.stop(); }
  catch (e) { toast(e.message, true); }
}));

// Pi's own speaker volume
async function loadPiVol(data) {
  try {
    const v = data || await api("/api/pivolume");
    if (v.volume != null) { $("#piVol").value = v.volume; $("#piVolVal").textContent = v.volume + "%"; }
    else $("#piVolVal").textContent = "–";
    $("#piMute").classList.toggle("muted", !!v.muted);
    $("#piMute").innerHTML = `<svg><use href="#${v.muted ? "i-mute" : "i-speaker"}"/></svg>`;
    $("#piSink").textContent = v.error ? "Sound server not reachable — log in to the Pi's desktop first." : (v.muted ? "Muted on the Pi — you can still stream sound here" : "Output: " + (v.sink || "default"));
  } catch {}
}
let volT;
$("#piVol").addEventListener("input", () => {
  $("#piVolVal").textContent = $("#piVol").value + "%";
  clearTimeout(volT); volT = setTimeout(() => api("/api/pivolume", { json: { volume: +$("#piVol").value } }).then(loadPiVol), 150);
});
$("#piMute").addEventListener("click", () => api("/api/pivolume", { json: { muted: !$("#piMute").classList.contains("muted") } }).then(loadPiVol));

/* ------------------------------------------------------------------ processes */
let procs = [];
async function refreshProcs() {
  try { procs = await api("/api/processes"); } catch { return; }
  renderProcs();
}
function renderProcs() {
  const f = $("#procFilter").value.toLowerCase();
  const rows = procs.filter((p) => !f || p.name.toLowerCase().includes(f) || p.user.includes(f) || p.cmd.toLowerCase().includes(f)).slice(0, 150);
  $("#procBody").innerHTML = rows.map((p) => `<tr>
    <td><div class="pname">${esc(p.name)}</div><div class="pcmd" title="${esc(p.cmd)}">${esc(p.cmd)}</div></td>
    <td>${esc(p.user)}</td><td class="num">${p.cpu.toFixed(1)}%</td><td class="num">${fmtBytes(p.rss)}</td><td class="num">${p.pid}</td>
    <td class="acts"><button class="btn small" data-kill="${p.pid}" data-name="${esc(p.name)}">End</button></td></tr>`).join("");
}
$("#procFilter").addEventListener("input", renderProcs);
$("#procBody").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-kill]"); if (!b) return;
  const force = e.shiftKey;
  if (!confirm(`End ${b.dataset.name} (${b.dataset.kill})?${force ? " (force)" : "\nHold Shift while clicking to force it."}`)) return;
  try { await api(`/api/processes/${b.dataset.kill}/kill`, { json: { signal: force ? "KILL" : "TERM" } }); toast(`Ended ${b.dataset.name}`); setTimeout(refreshProcs, 400); }
  catch (err) { toast(err.message, true); }
});

/* ------------------------------------------------------------------ services */
let svcs = [];
async function refreshServices() {
  try { svcs = await api("/api/services"); } catch (e) { return; }
  renderServices();
}
function renderServices() {
  const f = $("#svcFilter").value.toLowerCase(), st = $("#svcState").value;
  const rows = svcs.filter((s) => (st === "all" || (st === "running" ? s.sub === "running" : s.active === "failed")) &&
    (!f || s.name.toLowerCase().includes(f) || s.desc.toLowerCase().includes(f)));
  $("#svcBody").innerHTML = rows.length ? rows.map((s) => {
    const running = s.active === "active";
    return `<tr><td><div class="pname">${esc(s.name.replace(/\.service$/, ""))}</div><div class="pcmd">${esc(s.desc)}</div></td>
    <td><span class="state ${s.active === "failed" ? "failed" : s.sub}">${esc(s.sub)}</span></td>
    <td>${esc(s.enabled || "—")}</td>
    <td class="acts">
      <button class="btn small" data-svc="${esc(s.name)}" data-act="logs">Log</button>
      ${running ? `<button class="btn small" data-svc="${esc(s.name)}" data-act="restart">Restart</button><button class="btn small" data-svc="${esc(s.name)}" data-act="stop">Stop</button>`
        : `<button class="btn small" data-svc="${esc(s.name)}" data-act="start">Start</button>`}
    </td></tr>`;
  }).join("") : `<tr><td colspan="4" class="muted">No services match. Try “All”.</td></tr>`;
}
$("#svcFilter").addEventListener("input", renderServices);
$("#svcState").addEventListener("change", renderServices);
$("#svcBody").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-svc]"); if (!b) return;
  const { svc, act } = b.dataset;
  if (act === "logs") {
    $("#logTitle").textContent = svc; $("#logText").textContent = "Loading…"; $("#logDlg").showModal();
    try { const r = await api(`/api/services/${encodeURIComponent(svc)}/logs`); $("#logText").textContent = r.log || "(empty)"; $("#logText").scrollTop = 1e9; }
    catch (err) { $("#logText").textContent = err.message; }
    return;
  }
  b.disabled = true;
  try { await api(`/api/services/${encodeURIComponent(svc)}/${act}`, { json: {} }); toast(`${act === "stop" ? "Stopped" : act === "start" ? "Started" : "Restarted"} ${svc.replace(/\.service$/, "")}`); }
  catch (err) { toast(err.message, true); }
  refreshServices();
});

/* ------------------------------------------------------------------ terminal */
let term = null, termWs = null, fit = null;
function openTerminal() {
  if (!term) {
    term = new Terminal({ fontFamily: '"JetBrains Mono","DejaVu Sans Mono",monospace', fontSize: 14, cursorBlink: true,
      theme: { background: "#0b1712", foreground: "#e8eee6", cursor: "#f0b867", selectionBackground: "#2f5447" } });
    fit = new FitAddon.FitAddon(); term.loadAddon(fit);
    term.open($("#term"));
    term.onData((d) => termWs && termWs.readyState === 1 && termWs.send(JSON.stringify({ i: d })));
    term.onResize(({ cols, rows }) => termWs && termWs.readyState === 1 && termWs.send(JSON.stringify({ r: [cols, rows] })));
    new ResizeObserver(() => currentView === "terminal" && fit.fit()).observe($("#term"));
  }
  setTimeout(() => fit.fit(), 0);
  if (!termWs || termWs.readyState > 1) {
    termWs = new WebSocket(wsBase + "/ws/term");
    termWs.binaryType = "arraybuffer";
    termWs.onopen = () => { fit.fit(); termWs.send(JSON.stringify({ r: [term.cols, term.rows] })); };
    termWs.onmessage = (e) => term.write(new Uint8Array(e.data));
    termWs.onclose = () => term.write("\r\n\x1b[33m[session ended — open Terminal again for a new one]\x1b[0m\r\n");
  }
  term.focus();
}

/* ------------------------------------------------------------------ files */
let filesPath = store.get("filesPath", "~");
async function loadFiles(path) {
  let d;
  try { d = await api("/api/files?path=" + encodeURIComponent(path)); }
  catch (e) { toast(e.message, true); if (path !== "~") return loadFiles("~"); return; }
  filesPath = d.path; store.set("filesPath", filesPath);
  const parts = d.path.split("/").filter(Boolean);
  let acc = "";
  $("#crumbs").innerHTML = `<button data-go="/">/</button>` + parts.map((p) => { acc += "/" + p; return `<button data-go="${esc(acc)}">${esc(p)}</button><span>/</span>`; }).join("");
  const up = d.path !== "/" ? `<tr><td colspan="4"><div class="fname isdir" data-go="${esc(d.parent)}"><svg><use href="#i-folder"/></svg>..</div></td></tr>` : "";
  const showHidden = $("#showHidden").checked;
  $("#fileBody").innerHTML = up + d.items.filter((it) => showHidden || !it.name.startsWith(".")).map((it) => {
    const full = (d.path === "/" ? "" : d.path) + "/" + it.name;
    return `<tr><td><div class="fname ${it.dir ? "isdir" : ""}" ${it.dir ? `data-go="${esc(full)}"` : `data-dl="${esc(full)}"`}>
      <svg><use href="#${it.dir ? "i-folder" : "i-clip"}"/></svg>${esc(it.name)}</div></td>
      <td class="num">${it.dir ? "" : fmtBytes(it.size)}</td>
      <td class="num">${it.mtime ? new Date(it.mtime * 1000).toLocaleDateString() : ""}</td>
      <td class="acts">${it.dir ? "" : `<button class="btn small" data-dl="${esc(full)}">Download</button>`}
        <button class="btn small" data-ren="${esc(full)}" data-name="${esc(it.name)}">Rename</button>
        <button class="btn small danger" data-del="${esc(full)}" data-name="${esc(it.name)}">Delete</button></td></tr>`;
  }).join("");
}
$("#v-files").addEventListener("click", async (e) => {
  const t = e.target.closest("[data-go],[data-dl],[data-ren],[data-del]"); if (!t) return;
  if (t.dataset.go) return loadFiles(t.dataset.go);
  if (t.dataset.dl) { location.href = "/api/files/download?path=" + encodeURIComponent(t.dataset.dl); return; }
  if (t.dataset.ren) {
    const n = prompt("New name", t.dataset.name); if (!n || n === t.dataset.name) return;
    try { await api("/api/files/op", { json: { op: "rename", path: t.dataset.ren, name: n } }); loadFiles(filesPath); } catch (err) { toast(err.message, true); }
  }
  if (t.dataset.del) {
    if (!confirm(`Delete ${t.dataset.name}? This can't be undone.`)) return;
    try { await api("/api/files/op", { json: { op: "delete", path: t.dataset.del } }); toast(`Deleted ${t.dataset.name}`); loadFiles(filesPath); } catch (err) { toast(err.message, true); }
  }
});
$("#showHidden").addEventListener("change", () => loadFiles(filesPath));
$("#newFolder").addEventListener("click", async () => {
  const n = prompt("Folder name"); if (!n) return;
  try { await api("/api/files/op", { json: { op: "mkdir", path: filesPath, name: n } }); loadFiles(filesPath); } catch (e) { toast(e.message, true); }
});
async function uploadFiles(files) {
  if (!files.length) return;
  const fd = new FormData(); for (const f of files) fd.append("file", f, f.name);
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`);
  const r = await fetch("/api/files/upload?path=" + encodeURIComponent(filesPath), { method: "POST", body: fd });
  if (r.ok) { toast("Uploaded"); loadFiles(filesPath); } else toast("Upload failed", true);
}
$("#upload").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });
const drop = $("#drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", (e) => { if (!drop.contains(e.relatedTarget)) drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadFiles([...e.dataTransfer.files]); });

/* ------------------------------------------------------------------ fleet */
function fleetList() { return store.get("fleet", []); }
async function renderFleet() {
  const list = fleetList();
  const self = { host: location.host, self: true };
  const all = [self, ...list.filter((h) => h !== location.host).map((h) => ({ host: h }))];
  $("#fleet").innerHTML = all.map((p, i) => `<div class="pi ${p.self ? "this" : ""}" data-i="${i}">
      <strong>${esc(p.self ? INFO.hostname : p.host)}</strong>
      <span class="state" data-st>${p.self ? esc(INFO.model) : "Checking…"}</span>
      <div class="row">${p.self ? `<span class="muted">This Pi</span>` :
        `<a class="btn small primary" href="${location.protocol}//${esc(p.host)}/">Open</a><button class="btn small" data-rm="${esc(p.host)}">Remove</button>`}</div></div>`).join("");
  all.forEach(async (p, i) => {
    if (p.self) return;
    const el = $(`.pi[data-i="${i}"] [data-st]`);
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 3000);
      const r = await fetch(`${location.protocol}//${p.host}/api/ping`, { signal: ctl.signal });
      const d = await r.json();
      el.className = "state running"; el.textContent = `${d.hostname} — ${d.model} — up ${fmtUptime(d.uptime)}`;
    } catch { el.className = "state failed"; el.textContent = "Not answering"; }
  });
}
$("#fleetAdd").addEventListener("submit", (e) => {
  e.preventDefault();
  let h = $("#fleetHost").value.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  if (!h.includes(":")) h += ":" + (INFO.port || 6090);
  const l = fleetList(); if (!l.includes(h)) l.push(h); store.set("fleet", l);
  $("#fleetHost").value = ""; renderFleet();
});
$("#fleet").addEventListener("click", (e) => {
  const b = e.target.closest("[data-rm]"); if (!b) return;
  store.set("fleet", fleetList().filter((h) => h !== b.dataset.rm)); renderFleet();
});

/* ------------------------------------------------------------------ boot */
let ticker = null;
async function start() {
  $("#login").hidden = true; $("#app").hidden = false;
  $("#quality").value = store.get("quality", $("#quality").value);
  $("#vol").value = store.get("vol", 100);
  $("#audioQ").value = store.get("audioQ", $("#audioQ").value);
  $("#audioLat").value = store.get("audioLat", $("#audioLat").value);
  drawVUIdle();
  await loadInfo();
  loadPiVol();
  const v = location.hash.slice(1);
  show(["desktop", "overview", "processes", "services", "terminal", "files", "fleet"].includes(v) ? v : "desktop");
  clearInterval(ticker);
  ticker = setInterval(() => {
    if (document.hidden) return;
    if (currentView === "overview") refreshStats();
    if (currentView === "processes") refreshProcs();
  }, 2000);
  refreshStats(); // prime CPU deltas
}

(async () => {
  try {
    const me = await (await fetch("/api/me")).json();
    $("#loginHost").textContent = me.hostname;
    $("#loginModel").textContent = me.configured ? me.model : "No password set yet — run install.sh on the Pi.";
    if (me.authed) start(); else showLogin();
  } catch { showLogin(); }
})();
})();
