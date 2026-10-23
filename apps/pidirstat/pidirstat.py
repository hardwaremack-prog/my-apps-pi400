#!/usr/bin/env python3
"""
PiDirStat - a WinDirStat-style disk usage viewer for Raspberry Pi, Linux, macOS and Windows.

Single file, standard library only. Scans a directory tree and serves an
interactive viewer (directory tree + extension list + cushion treemap) that
you open in a browser - on the Pi itself or from any machine on your network.

Usage:
    python3 pidirstat.py                 # scan / and serve on port 8088
    sudo python3 pidirstat.py /          # scan everything, including root-only folders
    python3 pidirstat.py ~ --port 9000   # scan your home folder
    python3 pidirstat.py / --html report.html   # write a standalone HTML report and exit

On Windows (Python from python.org or the Microsoft Store):
    py pidirstat.py C:\\                  # scan the C: drive; your browser opens automatically
    (run the terminal "as administrator" to include protected folders)

Options:
    --apparent      count file sizes (st_size) instead of space used on disk
    --cross-fs      descend into other mounted filesystems (USB drives, /boot/firmware, ...)
    --host HOST     address to listen on (default 0.0.0.0 = reachable from your LAN;
                    use 127.0.0.1 to keep it local to the Pi)
    --open          open a browser on the Pi when the server starts
"""
import argparse
import gzip
import json
import os
import shutil
import socket
import stat
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.setrecursionlimit(20000)

IS_WIN = os.name == "nt"
# Windows: junctions, symlinks and mounted volumes are "reparse points"; skipping
# them avoids counting things twice (e.g. "Documents and Settings") and keeps the
# scan on one drive, like --one-file-system does on Linux.
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# Files/folders smaller than TOTAL / DETAIL are grouped so the browser stays fast
# even on a card with a million files.
DETAIL = 40000
MAX_EXTS = 300


# --------------------------------------------------------------------------- scan

def ext_of(name):
    i = name.rfind(".")
    return name[i:].lower() if i > 0 else ""


def scan(root, apparent=False, xdev=True, progress=None):
    root = os.path.abspath(os.path.expanduser(root))
    st = os.stat(root)
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError(f"not a directory: {root}")
    dev = st.st_dev
    if IS_WIN:
        apparent = True   # Windows doesn't report allocated blocks
    t0 = time.time()
    exts = {}
    seen = set()          # hard links are counted once
    nfiles = ndirs = nerr = tick = 0

    top = {"n": root, "c": [], "fl": []}
    stack = [(root, top)]
    order = []
    while stack:
        path, node = stack.pop()
        order.append(node)
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        nerr += 1
                        continue
                    if IS_WIN and getattr(s, "st_file_attributes", 0) & REPARSE:
                        if stat.S_ISDIR(s.st_mode) or e.is_dir():
                            continue
                    if stat.S_ISDIR(s.st_mode):
                        if xdev and not IS_WIN and s.st_dev != dev:
                            continue
                        child = {"n": e.name, "c": [], "fl": []}
                        node["c"].append(child)
                        stack.append((e.path, child))
                        ndirs += 1
                    else:
                        if s.st_nlink > 1 and s.st_ino:
                            key = (s.st_dev, s.st_ino)
                            if key in seen:
                                continue
                            seen.add(key)
                        blocks = getattr(s, "st_blocks", None)
                        sz = s.st_size if (apparent or blocks is None) else blocks * 512
                        node["fl"].append((e.name, sz))
                        x = ext_of(e.name)
                        a = exts.get(x)
                        if a is None:
                            exts[x] = [sz, 1]
                        else:
                            a[0] += sz
                            a[1] += 1
                        nfiles += 1
                    tick += 1
                    if progress and tick % 2000 == 0:
                        progress(nfiles, ndirs, nerr)
        except OSError:
            nerr += 1
            node["e"] = 1

    # subtree sizes, children before parents
    for node in reversed(order):
        s = sum(sz for _, sz in node["fl"])
        f = len(node["fl"])
        for c in node["c"]:
            s += c["s"]
            f += c["f"]
        node["s"] = s
        node["f"] = f

    total = top["s"]
    min_size = max(total / DETAIL, 1)

    def build(d):
        kids = []
        small_n = small_s = 0
        for name, sz in d["fl"]:
            if sz >= min_size:
                kids.append({"n": name, "s": sz})
            else:
                small_n += 1
                small_s += sz
        for sd in d["c"]:
            if sd["s"] >= min_size:
                kids.append(build(sd))
            else:
                k = {"n": sd["n"], "s": sd["s"], "f": sd["f"], "d": 1}
                if sd.get("e"):
                    k["e"] = 1
                kids.append(k)
        if small_n == 1 and d["fl"]:
            # a single small file: just show it
            for name, sz in d["fl"]:
                if sz < min_size:
                    kids.append({"n": name, "s": sz})
                    break
        elif small_n:
            kids.append({"n": f"<{small_n:,} smaller files>", "s": small_s, "m": small_n})
        kids.sort(key=lambda k: k["s"], reverse=True)
        out = {"n": d["n"], "s": d["s"], "f": d["f"], "c": kids}
        if d.get("e"):
            out["e"] = 1
        return out

    ext_list = sorted(([k, v[0], v[1]] for k, v in exts.items()), key=lambda e: e[1], reverse=True)
    if len(ext_list) > MAX_EXTS:
        rest = ext_list[MAX_EXTS:]
        ext_list = ext_list[:MAX_EXTS] + [["(other)", sum(e[1] for e in rest), sum(e[2] for e in rest)]]

    try:
        du = shutil.disk_usage(root)
        disk = [du.total, du.used, du.free]
    except OSError:
        disk = None

    return {
        "root": build(top),
        "ext": ext_list,
        "path": root,
        "total": total,
        "files": nfiles,
        "dirs": ndirs,
        "errors": nerr,
        "apparent": apparent,
        "xdev": xdev,
        "disk": disk,
        "host": socket.gethostname(),
        "win": IS_WIN,
        "elapsed": round(time.time() - t0, 2),
    }


# --------------------------------------------------------------------------- server state

class State:
    lock = threading.Lock()
    scanning = False
    path = "/"
    files = dirs = errors = 0
    started = 0.0
    payload = None      # gzipped JSON of the last scan
    id = 0
    error = None


def run_scan(path, apparent, xdev):
    with State.lock:
        if State.scanning:
            return False
        State.scanning = True
        State.path = os.path.abspath(os.path.expanduser(path))
        State.files = State.dirs = State.errors = 0
        State.started = time.time()
        State.error = None

    def prog(f, d, e):
        State.files, State.dirs, State.errors = f, d, e

    def work():
        try:
            data = scan(path, apparent=apparent, xdev=xdev, progress=prog)
            raw = json.dumps(data, separators=(",", ":")).encode()
            State.payload = gzip.compress(raw, 5)
            State.files, State.dirs, State.errors = data["files"], data["dirs"], data["errors"]
            State.id += 1
        except Exception as ex:  # noqa: BLE001
            State.error = str(ex)
        finally:
            State.scanning = False

    threading.Thread(target=work, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype, gz=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if gz:
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                self.send_header("Content-Encoding", "gzip")
            else:
                body = gzip.decompress(body)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self.send(200, PAGE.replace("/*__EMBED__*/", "").encode(), "text/html; charset=utf-8")
        elif p == "/api/status":
            st = {
                "scanning": State.scanning, "path": State.path, "files": State.files,
                "dirs": State.dirs, "errors": State.errors, "id": State.id,
                "ready": State.payload is not None, "error": State.error,
                "elapsed": round(time.time() - State.started, 1) if State.scanning else None,
            }
            self.send(200, json.dumps(st).encode(), "application/json")
        elif p == "/pds.js":
            # lets the My Apps menu find a running PiDirStat (a script tag works from file:// pages)
            port = self.server.server_address[1]
            self.send(200, f"(window.__pidirstat=window.__pidirstat||[]).push({port});".encode(), "application/javascript")
        elif p == "/api/tree" and State.payload:
            self.send(200, State.payload, "application/json", gz=True)
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/scan":
            return self.send(404, b"not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            path = req.get("path") or State.path
            if not os.path.isdir(os.path.expanduser(path)):
                return self.send(400, json.dumps({"error": f"Not a folder: {path}"}).encode(), "application/json")
            ok = run_scan(path, bool(req.get("apparent")), bool(req.get("xdev", True)))
            self.send(200 if ok else 409, json.dumps({"ok": ok}).encode(), "application/json")
        except Exception as ex:  # noqa: BLE001
            self.send(400, json.dumps({"error": str(ex)}).encode(), "application/json")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="WinDirStat-style disk usage viewer for Raspberry Pi")
    ap.add_argument("path", nargs="?", default=(os.path.splitdrive(os.getcwd())[0] + "\\") if IS_WIN else "/")
    ap.add_argument("--port", type=int, default=8088)
    # On Windows, stay local by default (avoids the firewall prompt); use --host 0.0.0.0 to share on your network.
    ap.add_argument("--host", default="127.0.0.1" if IS_WIN else "0.0.0.0")
    ap.add_argument("--apparent", action="store_true", help="use file sizes instead of disk usage")
    ap.add_argument("--cross-fs", action="store_true", help="descend into other mounted filesystems")
    ap.add_argument("--html", metavar="FILE", help="write a standalone HTML report and exit")
    ap.add_argument("--open", action="store_true", help="open a browser when ready")
    a = ap.parse_args()

    if a.html:
        print(f"Scanning {os.path.abspath(a.path)} ...", flush=True)
        data = scan(a.path, apparent=a.apparent, xdev=not a.cross_fs,
                    progress=lambda f, d, e: print(f"\r  {f:,} files, {d:,} folders   ", end="", flush=True))
        embed = "window.EMBED=" + json.dumps(data, separators=(",", ":")).replace("</", "<\\/") + ";"
        with open(a.html, "w", encoding="utf-8") as fh:
            fh.write(PAGE.replace("/*__EMBED__*/", embed))
        print(f"\rDone: {data['files']:,} files in {data['elapsed']}s -> {a.html}" + " " * 20)
        return

    if IS_WIN:
        print("Tip: run the terminal as administrator to include protected folders.")
    elif os.geteuid() != 0 and os.path.abspath(a.path) == "/":
        print("Tip: run with sudo to include folders only root can read.")
    run_scan(a.path, a.apparent, not a.cross_fs)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"PiDirStat is running. Open one of these in a browser:")
    print(f"  http://localhost:{a.port}")
    if a.host in ("0.0.0.0", ""):
        ip = lan_ip()
        if ip:
            print(f"  http://{ip}:{a.port}")
        if not IS_WIN:
            print(f"  http://{socket.gethostname()}.local:{a.port}")
    print("Press Ctrl+C to stop.")
    if a.open or IS_WIN:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")


# --------------------------------------------------------------------------- page

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PiDirStat</title>
<style>
:root{--bg:#eef0f2;--panel:#fff;--fg:#1c1d20;--muted:#6c6f76;--line:#dfe2e6;--sel:#d6e4ff;--accent:#c51a4a;--bar:#7aa0f0;--hover:#f3f5f8}
@media (prefers-color-scheme:dark){:root{--bg:#131416;--panel:#1c1d21;--fg:#e7e8ea;--muted:#999ca3;--line:#2d2f35;--sel:#243a63;--accent:#ff4f7e;--bar:#4468b0;--hover:#24262b}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:13px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;display:flex;flex-direction:column;overflow:hidden}
header{display:flex;gap:10px;align-items:center;padding:8px 12px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:15px;margin:0 6px 0 0;display:flex;align-items:center;gap:7px;letter-spacing:.2px}
.logo{width:16px;height:16px;border-radius:3px;background:conic-gradient(var(--accent) 0 40%,#3a7bd5 0 65%,#3cb371 0 85%,#e0b000 0)}
#controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#path{width:260px;max-width:60vw;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
label{display:flex;gap:4px;align-items:center;color:var(--muted);user-select:none}
button{font:inherit;padding:5px 12px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
button:hover:not(:disabled){border-color:var(--muted)}
button:disabled{opacity:.5;cursor:default}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
#status{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums}
#bar{padding:5px 12px;color:var(--muted);border-bottom:1px solid var(--line);background:var(--panel);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bar b{color:var(--fg);font-weight:600}
.disk{display:inline-block;width:120px;height:8px;border-radius:4px;background:var(--line);vertical-align:middle;margin:0 6px;overflow:hidden}
.disk i{display:block;height:100%;background:var(--accent)}
main{flex:1;display:grid;grid-template-rows:minmax(150px,42%) 1fr;min-height:0}
#top{display:grid;grid-template-columns:minmax(0,1fr) 340px;min-height:0;border-bottom:1px solid var(--line)}
#tree,#exts{overflow:auto;background:var(--panel);min-height:0}
#exts{border-left:1px solid var(--line)}
.row,.erow{display:grid;align-items:center;height:22px;padding-right:10px;white-space:nowrap;cursor:default}
.row{grid-template-columns:minmax(0,1fr) 130px 78px 80px}
.erow{grid-template-columns:24px minmax(0,1fr) 72px 44px 70px;padding-left:6px}
.row:hover:not(.hdr),.erow:hover:not(.hdr){background:var(--hover)}
.row.sel,.erow.sel{background:var(--sel)}
.hdr{position:sticky;top:0;background:var(--panel);font-weight:600;border-bottom:1px solid var(--line);z-index:1;color:var(--muted)}
.name{overflow:hidden;text-overflow:ellipsis}
.tog{display:inline-block;width:16px;text-align:center;color:var(--muted);cursor:pointer}
.ic{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.ic.dir{background:#e3b341;border-radius:1px 3px 2px 2px}
.dim{color:var(--muted);font-style:italic}
.pc{display:flex;align-items:center;gap:6px}
.pct{flex:1;height:9px;background:var(--line);border-radius:2px;overflow:hidden}
.pct i{display:block;height:100%;background:var(--bar)}
.pn{width:40px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.num{text-align:right;font-variant-numeric:tabular-nums}
.sw{width:12px;height:12px;border-radius:2px;border:1px solid rgba(0,0,0,.25)}
#bottom{display:flex;flex-direction:column;min-height:0}
#nav{display:flex;gap:8px;align-items:center;padding:5px 10px;background:var(--panel);border-bottom:1px solid var(--line);min-height:34px}
#nav button{padding:2px 10px}
#crumbs{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:40%}
#crumbs a{color:var(--fg);cursor:pointer;text-decoration:none}
#crumbs a:hover{text-decoration:underline}
#hover{margin-left:auto;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;direction:rtl;text-align:right}
#mapwrap{position:relative;flex:1;min-height:0;background:#000}
#mapwrap canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
#empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#aaa;font-size:14px}
@media (max-width:760px){#top{grid-template-columns:1fr}#exts{display:none}.row{grid-template-columns:minmax(0,1fr) 90px 70px}.row>span:nth-child(4){display:none}#path{width:100%}}
</style></head>
<body>
<header>
  <h1><span class="logo"></span>PiDirStat</h1>
  <div id="controls">
    <input id="path" type="text" spellcheck="false" placeholder="/">
    <label title="Count file sizes instead of space used on disk"><input type="checkbox" id="apparent">Apparent size</label>
    <label title="Also scan other mounted drives below this folder"><input type="checkbox" id="xfs">Cross filesystems</label>
    <button id="scan" class="primary">Scan</button>
  </div>
  <div id="status">Connecting…</div>
</header>
<div id="bar"><span id="summary">&nbsp;</span></div>
<main>
  <section id="top"><div id="tree"></div><div id="exts"></div></section>
  <section id="bottom">
    <div id="nav">
      <button id="up" title="Zoom out (or right-click the map)">↑ Up</button>
      <button id="zoom" title="Show only the selected folder (or double-click the map)">Zoom in</button>
      <span id="crumbs"></span><span id="hover"></span>
    </div>
    <div id="mapwrap"><canvas id="map"></canvas><canvas id="ov"></canvas><div id="empty">Scanning…</div></div>
  </section>
</main>
<script>/*__EMBED__*/</script>
<script>
(()=>{
const $=s=>document.querySelector(s);
const PAL=[[66,110,245],[232,62,62],[52,190,82],[35,195,215],[212,68,215],[228,202,40],[140,150,255],[255,140,105],[140,226,140],[128,216,236],[236,140,226],[236,230,140]];
const OTHER=[200,200,200],DIRC=[125,125,132],MERGED=[165,165,172];
const HGT=0.38,SCALE=0.91,IA=0.15;
const LN=Math.hypot(1,1,10),LX=-1/LN,LY=-1/LN,LZ=10/LN;
let D=null,root=null,view=null,sel=null,extFilter=null,extColor={},expanded=new Set(),flat=[],leaves=[],pix=null,rects=new Map(),W=0,H=0,dpr=1,loadedId=-1;

const fmt=b=>{const u=['B','KB','MB','GB','TB'];let i=0;while(b>=1024&&i<4){b/=1024;i++}return (i?b.toFixed(b<10?2:b<100?1:0):b)+' '+u[i]};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const extOf=n=>{const i=n.lastIndexOf('.');return i>0?n.slice(i).toLowerCase():''};
const colorOf=n=>(n.c||n.d)?DIRC:n.m?MERGED:(extColor[extOf(n.n)]||OTHER);
const isFile=n=>!n.c&&!n.d&&!n.m;
function pathOf(n){const p=[];for(let x=n;x;x=x._p)p.push(x.n);p.reverse();let s=p[0];const sep=s.includes('\\')?'\\':'/';for(let i=1;i<p.length;i++)s+=(s.endsWith(sep)?'':sep)+p[i];return s}
function within(n,a){for(let x=n;x;x=x._p)if(x===a)return true;return false}

function load(data){
  D=data;root=data.root;view=root;sel=null;extFilter=null;expanded=new Set([root]);
  const st=[root];while(st.length){const n=st.pop();if(n.c)for(const k of n.c){k._p=n;st.push(k)}}
  extColor={};data.ext.filter(e=>e[0]!=='(other)').slice(0,PAL.length).forEach((e,i)=>extColor[e[0]]=PAL[i]);
  $('#empty').style.display='none';
  if(!window.EMBED)$('#path').value=data.path;
  $('#apparent').checked=!!data.apparent;$('#apparent').disabled=!!data.win;$('#xfs').checked=!data.xdev;
  summary();renderExts();renderTree();layout();
}

function summary(){
  let s=`<b>${esc(D.path)}</b> on ${esc(D.host)} — <b>${fmt(D.total)}</b> ${D.apparent?'(file sizes)':'on disk'} in ${D.files.toLocaleString()} files, ${D.dirs.toLocaleString()} folders · scanned in ${D.elapsed}s`;
  if(D.errors)s+=` · <span title="Run with sudo to read these">${D.errors.toLocaleString()} unreadable</span>`;
  if(D.disk){const[t,u,f]=D.disk;s+=` · filesystem<span class="disk"><i style="width:${(u/t*100).toFixed(1)}%"></i></span>${fmt(u)} used, <b>${fmt(f)} free</b> of ${fmt(t)}`}
  $('#summary').innerHTML=s;
}

/* ---------- directory tree ---------- */
function renderTree(){
  flat=[];
  const out=['<div class="row hdr"><span style="padding-left:8px">Name</span><span>Subtree %</span><span class="num">Size</span><span class="num">Files</span></div>'];
  (function walk(n,depth){
    const id=flat.push(n)-1,par=n._p,pct=par&&par.s?n.s/par.s*100:100;
    const tog=n.c&&n.c.length?(expanded.has(n)?'▾':'▸'):'';
    let icon,label;
    if(n.c||n.d){icon='<span class="ic dir"></span>';label=esc(n.n)+(n.d?' <span class="dim">(small)</span>':'')+(n.e?' <span class="dim">(no access)</span>':'')}
    else if(n.m){icon=`<span class="ic" style="background:rgb(${MERGED})"></span>`;label=`<span class="dim">${esc(n.n)}</span>`}
    else{icon=`<span class="ic" style="background:rgb(${colorOf(n)})"></span>`;label=esc(n.n)}
    const cnt=(n.c||n.d)?(n.f||0).toLocaleString():n.m?n.m.toLocaleString():'';
    out.push(`<div class="row${n===sel?' sel':''}" data-i="${id}"><span class="name" style="padding-left:${depth*16+2}px"><span class="tog">${tog}</span>${icon}${label}</span><span class="pc"><span class="pct"><i style="width:${pct.toFixed(1)}%"></i></span><span class="pn">${pct.toFixed(1)}%</span></span><span class="num">${fmt(n.s)}</span><span class="num">${cnt}</span></div>`);
    if(n.c&&expanded.has(n)){
      const lim=Math.min(n.c.length,1000);
      for(let k=0;k<lim;k++)walk(n.c[k],depth+1);
      if(n.c.length>lim)out.push(`<div class="row"><span class="name dim" style="padding-left:${(depth+1)*16+18}px">… ${(n.c.length-lim).toLocaleString()} more</span></div>`);
    }
  })(root,0);
  $('#tree').innerHTML=out.join('');
}
$('#tree').addEventListener('click',e=>{
  const r=e.target.closest('.row[data-i]');if(!r)return;const n=flat[+r.dataset.i];
  if(e.target.classList.contains('tog')&&n.c){expanded.has(n)?expanded.delete(n):expanded.add(n);renderTree();return}
  select(n,false);
});
$('#tree').addEventListener('dblclick',e=>{
  const r=e.target.closest('.row[data-i]');if(!r)return;const n=flat[+r.dataset.i];
  if(n.c&&!e.target.classList.contains('tog')){expanded.has(n)?expanded.delete(n):expanded.add(n);renderTree()}
});

/* ---------- extension list ---------- */
function renderExts(){
  const tot=D.total||1;
  $('#exts').innerHTML='<div class="erow hdr"><span></span><span>Extension</span><span class="num">Size</span><span class="num">%</span><span class="num">Files</span></div>'+
    D.ext.map(e=>{const c=extColor[e[0]]||OTHER;return `<div class="erow${extFilter===e[0]?' sel':''}" data-x="${esc(e[0])}" title="Click to highlight in the treemap"><span class="sw" style="background:rgb(${c})"></span><span class="name">${e[0]?esc(e[0]):'<span class="dim">(none)</span>'}</span><span class="num">${fmt(e[1])}</span><span class="num">${(e[1]/tot*100).toFixed(1)}</span><span class="num">${e[2].toLocaleString()}</span></div>`}).join('');
}
$('#exts').addEventListener('click',e=>{
  const r=e.target.closest('.erow[data-x]');if(!r)return;const x=r.getAttribute('data-x');
  extFilter=extFilter===x?null:x;renderExts();paint();drawOverlay();
});

/* ---------- selection & zoom ---------- */
function select(n,fromMap){
  sel=n;
  for(let p=n._p;p;p=p._p)expanded.add(p);
  if(!within(n,view)){view=root;layout()}
  renderTree();drawOverlay();
  $('#hover').textContent='\u200E'+pathOf(n)+' — '+fmt(n.s)+'\u200E';
  const r=$('#tree .row.sel');if(r)r.scrollIntoView({block:'nearest'});
}
function zoomTo(n){if(n&&n.c){view=n;layout()}}
$('#up').onclick=()=>{if(view&&view._p)zoomTo(view._p)};
$('#zoom').onclick=()=>{if(!sel)return;let n=sel.c?sel:sel._p;if(n)zoomTo(n)};
function crumbs(){
  const chain=[];for(let x=view;x;x=x._p)chain.unshift(x);
  $('#crumbs').innerHTML=chain.map((n,i)=>`<a data-i="${i}">${esc(i?n.n:n.n)}</a>`).join(' / ');
  $('#crumbs').onclick=e=>{const a=e.target.closest('a');if(a)zoomTo(chain[+a.dataset.i])};
  $('#up').disabled=!view._p;
}

/* ---------- cushion treemap ---------- */
function ridge(s,x,y,w,h,ht){const h4=4*ht;const wf=h4/w;s[2]+=wf*(2*x+w);s[0]-=wf;const hf=h4/h;s[3]+=hf*(2*y+h);s[1]-=hf}
function squarify(kids,total,x,y,w,h,cb){
  let n=kids.length;while(n&&kids[n-1].s<=0)n--;
  const scale=w*h/total;let i=0;
  while(i<n&&w>0&&h>0){
    const l=Math.min(w,h),M=kids[i].s*scale;let S=0,j=i,prev=Infinity;
    while(j<n){const a=kids[j].s*scale,S2=S+a;const worst=Math.max(l*l*M/(S2*S2),(S2*S2)/(l*l*a));if(worst>prev)break;prev=worst;S=S2;j++}
    if(w>=h){const cw=S/h;let yy=y;for(let k=i;k<j;k++){const kh=kids[k].s*scale/cw;cb(kids[k],x,yy,cw,kh);yy+=kh}x+=cw;w-=cw}
    else{const rh=S/w;let xx=x;for(let k=i;k<j;k++){const kw=kids[k].s*scale/rh;cb(kids[k],xx,y,kw,rh);xx+=kw}y+=rh;h-=rh}
    i=j;
  }
}
function place(n,x,y,w,h,surf,ht){
  if(w<=0||h<=0)return;
  rects.set(n,[x,y,w,h]);
  const s=surf.slice();ridge(s,x,y,w,h,ht);
  if(n.c&&n.s>0&&w*h>=4&&w>=1&&h>=1)squarify(n.c,n.s,x,y,w,h,(k,a,b,cw,ch)=>place(k,a,b,cw,ch,s,ht*SCALE));
  else leaves.push({n,x,y,w,h,s});
}
function layout(){
  if(!view)return;
  const r=$('#mapwrap').getBoundingClientRect();
  dpr=Math.min(window.devicePixelRatio||1,2);
  W=Math.max(1,Math.floor(r.width*dpr));H=Math.max(1,Math.floor(r.height*dpr));
  for(const c of [$('#map'),$('#ov')]){c.width=W;c.height=H}
  rects=new Map();leaves=[];
  place(view,0,0,W,H,[0,0,0,0],HGT);
  paint();drawOverlay();crumbs();
}
function paint(){
  if(!W)return;
  const ctx=$('#map').getContext('2d'),img=ctx.createImageData(W,H),d=img.data;
  pix=new Int32Array(W*H).fill(-1);
  for(let li=0;li<leaves.length;li++){
    const L=leaves[li];
    const x0=Math.max(0,Math.round(L.x)),x1=Math.min(W,Math.round(L.x+L.w)),y0=Math.max(0,Math.round(L.y)),y1=Math.min(H,Math.round(L.y+L.h));
    if(x1<=x0||y1<=y0)continue;
    let [r,g,b]=colorOf(L.n);
    if(extFilter!==null&&!(isFile(L.n)&&extOf(L.n.n)===extFilter)){const v=(r+g+b)/3*.35+25;r=g=b=v}
    const s=L.s,s0=2*s[0],s1=2*s[1],s2=s[2],s3=s[3];
    for(let yy=y0;yy<y1;yy++){
      const ny=-(s1*(yy+.5)+s3);let p=yy*W+x0;
      for(let xx=x0;xx<x1;xx++,p++){
        const nx=-(s0*(xx+.5)+s2);
        let c=(nx*LX+ny*LY+LZ)/Math.sqrt(nx*nx+ny*ny+1);if(c<0)c=0;
        const v=(IA+(1-IA)*c)*1.12,q=p*4;
        d[q]=r*v;d[q+1]=g*v;d[q+2]=b*v;d[q+3]=255;pix[p]=li;
      }
    }
  }
  ctx.putImageData(img,0,0);
}
function drawOverlay(){
  const c=$('#ov').getContext('2d');c.clearRect(0,0,W,H);if(!sel)return;
  let n=sel,r=rects.get(n);while(!r&&n._p){n=n._p;r=rects.get(n)}
  if(!r)return;
  const lw=Math.max(1,Math.round(dpr));
  c.lineWidth=lw*3;c.strokeStyle='rgba(0,0,0,.75)';c.strokeRect(r[0]+lw*1.5,r[1]+lw*1.5,Math.max(0,r[2]-lw*3),Math.max(0,r[3]-lw*3));
  c.lineWidth=lw;c.strokeStyle='#fff';c.strokeRect(r[0]+lw*1.5,r[1]+lw*1.5,Math.max(0,r[2]-lw*3),Math.max(0,r[3]-lw*3));
}
function hit(e){
  if(!pix)return null;const r=$('#ov').getBoundingClientRect();
  const x=Math.floor((e.clientX-r.left)*dpr),y=Math.floor((e.clientY-r.top)*dpr);
  if(x<0||y<0||x>=W||y>=H)return null;const li=pix[y*W+x];return li>=0?leaves[li].n:null;
}
const ov=$('#ov');
ov.addEventListener('mousemove',e=>{const n=hit(e);if(n)$('#hover').textContent='\u200E'+pathOf(n)+' — '+fmt(n.s)+'\u200E'});
ov.addEventListener('click',e=>{const n=hit(e);if(n)select(n,true)});
ov.addEventListener('dblclick',e=>{const n=hit(e);if(!n)return;let x=n;while(x._p&&x._p!==view)x=x._p;if(x._p===view)zoomTo(x)});
ov.addEventListener('contextmenu',e=>{e.preventDefault();if(view&&view._p)zoomTo(view._p)});
let rt;new ResizeObserver(()=>{clearTimeout(rt);rt=setTimeout(layout,120)}).observe($('#mapwrap'));

/* ---------- server communication ---------- */
function status(st){
  $('#status').textContent=st.scanning?`Scanning ${st.path} — ${st.files.toLocaleString()} files, ${st.dirs.toLocaleString()} folders (${st.elapsed}s)`:st.error?'Error: '+st.error:'';
  $('#scan').disabled=st.scanning;
  if(st.scanning&&!D)$('#empty').textContent='Scanning '+st.path+' …';
  if(!$('#path').value)$('#path').value=st.path;
}
async function poll(){
  try{
    const st=await (await fetch('api/status',{cache:'no-store'})).json();status(st);
    if(st.scanning){setTimeout(poll,700);return}
    if(st.ready&&st.id!==loadedId){$('#status').textContent='Loading…';const d=await (await fetch('api/tree',{cache:'no-store'})).json();loadedId=st.id;load(d);status(st)}
  }catch(e){$('#status').textContent='Lost connection to PiDirStat — retrying…';setTimeout(poll,3000)}
}
async function startScan(){
  const r=await fetch('api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:$('#path').value.trim(),apparent:$('#apparent').checked,xdev:!$('#xfs').checked})});
  if(r.status===400){const j=await r.json();$('#status').textContent=j.error;return}
  poll();
}
$('#scan').onclick=startScan;
$('#path').addEventListener('keydown',e=>{if(e.key==='Enter')startScan()});

if(window.EMBED){$('#controls').style.display='none';$('#status').textContent='Saved report';load(window.EMBED)}
else poll();
})();
</script>
</body></html>
'''

if __name__ == "__main__":
    main()
