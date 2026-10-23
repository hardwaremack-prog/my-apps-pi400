#!/usr/bin/env python3
"""
Blueprint - a locally hosted technical wiki.

Run:   python3 server.py              then open http://localhost:8080
       python3 server.py --port 9000 --open
Needs only Python 3.8+ (nothing to install).

Everything you create lives in ./data as plain files:
  data/sections.json   your left-hand tabs
  data/pages/*.json    wiki pages
  data/diagrams/*.json diagrams
  data/files/          uploaded pictures, PDFs and other files
  data/history/        older versions of pages
  data/trash/          deleted things (nothing is ever hard-deleted)
"""
import argparse
import html as htmlmod
import io
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import time
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

VERSION = "1.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.abspath(os.environ.get("BLUEPRINT_DATA", os.path.join(ROOT, "data")))
D = {k: os.path.join(DATA, k) for k in ("pages", "diagrams", "files", "history", "trash")}
SECTIONS = os.path.join(DATA, "sections.json")
MAX_HISTORY = 40
HISTORY_GAP_MS = 5 * 60 * 1000          # a new snapshot when you come back after 5+ minutes
MAX_UPLOAD = 1024 * 1024 * 1024          # 1 GB
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
LOCK = threading.RLock()

for ext, typ in ((".js", "application/javascript"), (".svg", "image/svg+xml"), (".webp", "image/webp"),
                 (".md", "text/markdown"), (".json", "application/json"), (".pdf", "application/pdf")):
    mimetypes.add_type(typ, ext)

DEFAULT_SECTIONS = [
    {"id": "projects", "name": "Projects", "icon": "folder", "color": "#4da3ff"},
    {"id": "network", "name": "Network", "icon": "globe", "color": "#2ee6c5"},
    {"id": "processes", "name": "Processes", "icon": "gear", "color": "#f5a524"},
    {"id": "prototypes", "name": "Prototypes", "icon": "flask", "color": "#b57bff"},
    {"id": "reference", "name": "Reference", "icon": "book", "color": "#ff7a90"},
]

WELCOME_HTML = """<p>Blueprint is your own wiki for projects, networks, processes and prototypes. It runs on this computer and everything is kept as plain files in the <code>data</code> folder next to the app.</p>
<div class="callout tip"><p><b>Quick start:</b> pick a section on the left, press <b>New page</b>, choose a template and start typing. Pages save by themselves.</p></div>
<h2>What you can do</h2>
<ul class="checklist">
<li data-done="true">Write pages with headings, tables, checklists, code blocks and callouts</li>
<li>Drag pictures and PDFs straight into a page while you are editing it</li>
<li>Draw network maps and flowcharts with the drag-and-drop diagram editor</li>
<li>Type a few lines like <code>Router -&gt; Switch -&gt; PC</code> and press <b>Generate</b> to have the diagram drawn for you</li>
<li>Link pages together by typing <code>[[</code></li>
<li>Bring back older versions with the history button</li>
</ul>
<h2>Example diagram</h2>
<div class="bp-embed" data-type="diagram" data-id="sample-network"></div>
<p>Press <b>Edit diagram</b> above to try the editor. Hover over a device and drag from one of its blue dots to connect it to something else.</p>
<h2>Keyboard shortcuts</h2>
<table><thead><tr><th>Keys</th><th>What it does</th></tr></thead><tbody>
<tr><td><kbd>Ctrl</kbd> + <kbd>K</kbd></td><td>Search everything</td></tr>
<tr><td><kbd>Ctrl</kbd> + <kbd>E</kbd></td><td>Edit the page / finish editing</td></tr>
<tr><td><kbd>Ctrl</kbd> + <kbd>S</kbd></td><td>Save now</td></tr>
<tr><td><kbd>[[</kbd></td><td>Link to another page</td></tr>
<tr><td><kbd>Ctrl</kbd> + <kbd>Z</kbd> / <kbd>Ctrl</kbd> + <kbd>Y</kbd></td><td>Undo / redo</td></tr>
</tbody></table>
<h2>Open it from other devices</h2>
<p>Any phone, tablet or computer on the same network can open Blueprint. The address is shown in <a href="#/settings">Settings</a>.</p>"""


def _n(i, t, x, y, label, sub="", stroke="#3b6fd8", w=104, h=92, fill="#ffffff"):
    return {"id": i, "type": t, "x": x, "y": y, "w": w, "h": h, "label": label, "sub": sub, "notes": "",
            "fill": fill, "stroke": stroke, "color": "#1c2433", "fs": 13}


def _e(i, a, b, label=""):
    return {"id": i, "from": a, "to": b, "label": label, "style": "orth", "arrow": "none",
            "dash": False, "color": "", "width": 2}


SAMPLE_DIAGRAM = {
    "id": "sample-network", "title": "Example home network", "edgeStyle": "orth",
    "nodes": [
        _n("z1", "zone", 20, 276, "Home LAN", "192.168.1.0/24", w=540, h=270, fill="#3b6fd8"),
        _n("n1", "cloud", 238, 20, "Internet", stroke="#16a394"),
        _n("n2", "router", 238, 160, "Router", "192.168.1.1", h=96),
        _n("n3", "switch", 238, 300, "Switch"),
        _n("n4", "pc", 60, 430, "Desktop PC", "192.168.1.20", stroke="#475569", h=96),
        _n("n5", "pi", 238, 430, "Raspberry Pi", "192.168.1.30", stroke="#c51a4a", h=96),
        _n("n6", "nas", 416, 430, "NAS", "192.168.1.40", stroke="#8a55d6", h=96),
    ],
    "edges": [_e("e1", "n1", "n2", "WAN"), _e("e2", "n2", "n3"), _e("e3", "n3", "n4"),
              _e("e4", "n3", "n5"), _e("e5", "n3", "n6")],
}


def now_ms():
    return int(time.time() * 1000)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


TAG_RE = re.compile(r"<[^>]+>")


def text_of(html):
    return re.sub(r"\s+", " ", htmlmod.unescape(TAG_RE.sub(" ", html or ""))).strip()


def setup():
    for p in D.values():
        os.makedirs(p, exist_ok=True)
    if not os.path.exists(SECTIONS):
        write_json(SECTIONS, DEFAULT_SECTIONS)
        t = now_ms()
        write_json(os.path.join(D["pages"], "welcome.json"), {
            "id": "welcome", "title": "Welcome to Blueprint", "section": "projects", "tags": ["start-here"],
            "pinned": True, "html": WELCOME_HTML, "created": t, "updated": t})
        d = dict(SAMPLE_DIAGRAM, created=t, updated=t)
        write_json(os.path.join(D["diagrams"], "sample-network.json"), d)


def list_json(folder):
    out = []
    for fn in os.listdir(folder):
        if fn.endswith(".json"):
            obj = read_json(os.path.join(folder, fn))
            if isinstance(obj, dict) and obj.get("id"):
                out.append(obj)
    return out


def page_summary(p):
    s = {k: p.get(k) for k in ("id", "title", "section", "tags", "pinned", "updated", "created")}
    s["excerpt"] = text_of(p.get("html", ""))[:180]
    return s


def diagram_summary(d):
    return {"id": d["id"], "title": d.get("title") or "Untitled diagram", "updated": d.get("updated"),
            "count": len(d.get("nodes") or [])}


def list_files():
    out = []
    for fn in sorted(os.listdir(D["files"])):
        p = os.path.join(D["files"], fn)
        if os.path.isfile(p) and not fn.startswith("."):
            st = os.stat(p)
            out.append({"name": fn, "url": "/files/" + quote(fn), "size": st.st_size,
                        "mtime": int(st.st_mtime * 1000),
                        "type": mimetypes.guess_type(fn)[0] or "application/octet-stream"})
    return out


def safe_name(name):
    name = os.path.basename((name or "").replace("\\", "/")).strip() or "file"
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "file"
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:12].lower()
    stem = stem[:80]
    cand, i = stem + ext, 1
    while os.path.exists(os.path.join(D["files"], cand)):
        cand = "%s-%d%s" % (stem, i, ext)
        i += 1
    return cand


def obj_path(kind, oid):
    if not ID_RE.match(oid or ""):
        raise ValueError("bad id")
    return os.path.join(D["pages" if kind == "page" else "diagrams"], oid + ".json")


def save_history(oid, old):
    hd = os.path.join(D["history"], oid)
    os.makedirs(hd, exist_ok=True)
    write_json(os.path.join(hd, "%d.json" % now_ms()), old)
    snaps = sorted(f for f in os.listdir(hd) if f.endswith(".json"))
    for f in snaps[:-MAX_HISTORY]:
        os.remove(os.path.join(hd, f))


def to_trash(path):
    if os.path.exists(path):
        shutil.move(path, os.path.join(D["trash"], "%d-%s" % (now_ms(), os.path.basename(path))))


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


class Handler(BaseHTTPRequestHandler):
    server_version = "Blueprint/" + VERSION

    def log_message(self, fmt, *args):
        if os.environ.get("BLUEPRINT_LOG"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---------- responses
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def err(self, code, msg):
        self.json({"error": msg}, code)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("file too large")
        buf, left = io.BytesIO(), n
        while left > 0:
            chunk = self.rfile.read(min(1 << 16, left))
            if not chunk:
                break
            buf.write(chunk)
            left -= len(chunk)
        return buf.getvalue()

    def jbody(self):
        raw = self.body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def file(self, path, download=None):
        if not os.path.isfile(path):
            return self.err(404, "not found")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        size = os.path.getsize(path)
        start, end, code = 0, max(size - 1, 0), 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if m and size > 0 and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        length = end - start + 1 if size else 0
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(path).replace('"', ""))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def parts(self):
        u = urlparse(self.path)
        return [unquote(x) for x in u.path.strip("/").split("/") if x], parse_qs(u.query)

    def _wrap(self, fn):
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except ValueError as e:
            self.err(400, str(e))
        except Exception as e:  # noqa
            try:
                self.err(500, "%s: %s" % (type(e).__name__, e))
            except Exception:
                pass

    def do_GET(self):
        self._wrap(self._get)

    def do_PUT(self):
        self._wrap(self._put)

    def do_POST(self):
        self._wrap(self._post)

    def do_DELETE(self):
        self._wrap(self._delete)

    # ---------- GET
    def _get(self):
        parts, q = self.parts()
        if not parts or parts == ["index.html"]:
            return self.file(os.path.join(STATIC, "index.html"))
        if parts[0] == "static" and len(parts) == 2:
            return self.file(os.path.join(STATIC, os.path.basename(parts[1])))
        if parts[0] == "files" and len(parts) == 2:
            return self.file(os.path.join(D["files"], os.path.basename(parts[1])), download="download" in q)
        if parts == ["bpw.js"]:
            # lets the My Apps dashboard tile find a running Blueprint (other programs won't answer this)
            return self._send(200, ("(window.__blueprint=window.__blueprint||[]).push(%d);" % self.server.server_port).encode(),
                              "application/javascript; charset=utf-8")
        if parts == ["favicon.ico"]:
            return self.file(os.path.join(STATIC, "icon.svg"))
        if parts[0] != "api":
            return self.err(404, "not found")
        r = parts[1:]
        if r == ["state"]:
            with LOCK:
                return self.json({
                    "sections": read_json(SECTIONS, DEFAULT_SECTIONS),
                    "pages": [page_summary(p) for p in list_json(D["pages"])],
                    "diagrams": [diagram_summary(d) for d in list_json(D["diagrams"])],
                    "files": list_files(), "version": VERSION})
        if r == ["info"]:
            return self.json({"host": socket.gethostname(), "ips": lan_ips(), "port": self.server.server_port,
                              "data": DATA, "version": VERSION})
        if r == ["diagrams"]:
            return self.json(list_json(D["diagrams"]))
        if len(r) == 2 and r[0] in ("page", "diagram"):
            obj = read_json(obj_path(r[0], r[1]))
            return self.json(obj) if obj else self.err(404, "not found")
        if r == ["search"]:
            return self.json(self.search((q.get("q") or [""])[0]))
        if r and r[0] == "history" and len(r) in (2, 3):
            if not ID_RE.match(r[1]):
                raise ValueError("bad id")
            hd = os.path.join(D["history"], r[1])
            if len(r) == 3:
                obj = read_json(os.path.join(hd, os.path.basename(r[2]) + ".json"))
                return self.json(obj) if obj else self.err(404, "not found")
            out = []
            if os.path.isdir(hd):
                for f in sorted(os.listdir(hd), reverse=True):
                    if f.endswith(".json"):
                        o = read_json(os.path.join(hd, f)) or {}
                        out.append({"ts": f[:-5], "title": o.get("title"), "updated": o.get("updated"),
                                    "size": len(text_of(o.get("html", "")))})
            return self.json(out)
        if r == ["export"]:
            buf = io.BytesIO()
            with LOCK, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                if os.path.exists(SECTIONS):
                    z.write(SECTIONS, "sections.json")
                for sub in ("pages", "diagrams", "files"):
                    for fn in os.listdir(D[sub]):
                        p = os.path.join(D[sub], fn)
                        if os.path.isfile(p):
                            z.write(p, "%s/%s" % (sub, fn))
            name = "blueprint-backup-%s.zip" % datetime.now().strftime("%Y%m%d-%H%M")
            return self._send(200, buf.getvalue(), "application/zip",
                              {"Content-Disposition": 'attachment; filename="%s"' % name})
        return self.err(404, "unknown api")

    def search(self, qs):
        terms = [t for t in qs.lower().split() if t]
        if not terms:
            return []
        res = []
        for p in list_json(D["pages"]):
            title = p.get("title") or ""
            tags = " ".join(p.get("tags") or [])
            text = text_of(p.get("html", ""))
            hay = (title + " " + tags + " " + text).lower()
            if all(t.lstrip("#") in hay for t in terms):
                score = sum((10 if t in title.lower() else 0) + (5 if t.lstrip("#") in tags.lower() else 0)
                            + min(hay.count(t), 10) for t in terms)
                i = text.lower().find(terms[0])
                snip = text[max(0, i - 60): i + 140] if i >= 0 else text[:160]
                res.append({"kind": "page", "id": p["id"], "title": title, "section": p.get("section"),
                            "snippet": snip, "score": score})
        for d in list_json(D["diagrams"]):
            words = [d.get("title") or ""] + [" ".join(str(n.get(k) or "") for k in ("label", "sub", "notes"))
                                              for n in d.get("nodes") or []]
            hay = " ".join(words).lower()
            if all(t in hay for t in terms):
                res.append({"kind": "diagram", "id": d["id"], "title": d.get("title") or "Untitled diagram",
                            "snippet": "Diagram - %d shapes" % len(d.get("nodes") or []),
                            "score": sum(min(hay.count(t), 10) for t in terms)})
        for f in list_files():
            if all(t in f["name"].lower() for t in terms):
                res.append({"kind": "file", "id": f["name"], "title": f["name"], "url": f["url"],
                            "snippet": f["type"], "score": 1})
        res.sort(key=lambda x: -x["score"])
        return res[:60]

    # ---------- PUT
    def _put(self):
        parts, _ = self.parts()
        r = parts[1:] if parts and parts[0] == "api" else []
        if r == ["sections"]:
            data = self.jbody()
            if not isinstance(data, list):
                raise ValueError("expected a list")
            clean = []
            for s in data:
                if isinstance(s, dict) and ID_RE.match(str(s.get("id", ""))):
                    clean.append({"id": s["id"], "name": str(s.get("name") or "Section")[:60],
                                  "icon": str(s.get("icon") or "folder")[:30],
                                  "color": str(s.get("color") or "#4da3ff")[:20]})
            with LOCK:
                write_json(SECTIONS, clean)
            return self.json(clean)
        if len(r) == 2 and r[0] == "page":
            data = self.jbody()
            path = obj_path("page", r[1])
            with LOCK:
                old = read_json(path)
                t = now_ms()
                if old and (data.get("snapshot") or t - (old.get("updated") or 0) > HISTORY_GAP_MS):
                    save_history(r[1], old)
                obj = {"id": r[1], "title": str(data.get("title") or "Untitled")[:200],
                       "section": str(data.get("section") or "")[:80],
                       "tags": [str(x)[:40] for x in (data.get("tags") or [])][:30],
                       "pinned": bool(data.get("pinned")), "html": str(data.get("html") or ""),
                       "created": (old or {}).get("created") or t, "updated": t}
                write_json(path, obj)
            return self.json(page_summary(obj))
        if len(r) == 2 and r[0] == "diagram":
            data = self.jbody()
            path = obj_path("diagram", r[1])
            with LOCK:
                old = read_json(path) or {}
                t = now_ms()
                obj = {"id": r[1], "title": str(data.get("title") or "Untitled diagram")[:200],
                       "nodes": data.get("nodes") if isinstance(data.get("nodes"), list) else [],
                       "edges": data.get("edges") if isinstance(data.get("edges"), list) else [],
                       "edgeStyle": str(data.get("edgeStyle") or "orth"),
                       "layoutDir": "LR" if data.get("layoutDir") == "LR" else "TB",
                       "created": old.get("created") or t, "updated": t}
                write_json(path, obj)
            return self.json(diagram_summary(obj))
        return self.err(404, "unknown api")

    # ---------- POST
    def _post(self):
        parts, _ = self.parts()
        r = parts[1:] if parts and parts[0] == "api" else []
        if r == ["upload"]:
            raw = self.body()
            name = safe_name(unquote(self.headers.get("X-Filename") or "file"))
            with open(os.path.join(D["files"], name), "wb") as f:
                f.write(raw)
            return self.json({"name": name, "url": "/files/" + quote(name), "size": len(raw),
                              "mtime": now_ms(), "type": mimetypes.guess_type(name)[0] or "application/octet-stream"})
        if r == ["import"]:
            raw = self.body()
            counts = {"pages": 0, "diagrams": 0, "files": 0, "sections": 0}
            with LOCK, zipfile.ZipFile(io.BytesIO(raw)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    bits = [b for b in info.filename.replace("\\", "/").split("/") if b]
                    if bits and bits[0] == "data":
                        bits = bits[1:]
                    if bits == ["sections.json"]:
                        incoming = json.loads(z.read(info).decode("utf-8"))
                        cur = read_json(SECTIONS, [])
                        have = {s["id"] for s in cur}
                        for s in incoming if isinstance(incoming, list) else []:
                            if isinstance(s, dict) and s.get("id") not in have and ID_RE.match(str(s.get("id", ""))):
                                cur.append(s)
                                counts["sections"] += 1
                        write_json(SECTIONS, cur)
                    elif len(bits) == 2 and bits[0] in ("pages", "diagrams", "files") and not bits[1].startswith("."):
                        target = os.path.join(D[bits[0]], os.path.basename(bits[1]))
                        with open(target, "wb") as f:
                            f.write(z.read(info))
                        counts[bits[0]] += 1
            return self.json(counts)
        return self.err(404, "unknown api")

    # ---------- DELETE
    def _delete(self):
        parts, _ = self.parts()
        r = parts[1:] if parts and parts[0] == "api" else []
        with LOCK:
            if len(r) == 2 and r[0] in ("page", "diagram"):
                to_trash(obj_path(r[0], r[1]))
                return self.json({"ok": True})
            if len(r) == 2 and r[0] == "file":
                to_trash(os.path.join(D["files"], os.path.basename(r[1])))
                return self.json({"ok": True})
        return self.err(404, "unknown api")


def find_running():
    """Port of a Blueprint already running on this computer (it answers /bpw.js), or None."""
    from urllib.request import urlopen
    for port in (8080, 8081, 8082, 8083):
        try:
            if b"__blueprint" in urlopen("http://127.0.0.1:%d/bpw.js" % port, timeout=0.6).read():
                return port
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser(description="Blueprint wiki server")
    ap.add_argument("--port", type=int, default=None, help="default 8080 (tries 8081-8083 if it is busy)")
    ap.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 = reachable from other devices on your network, 127.0.0.1 = this computer only")
    ap.add_argument("--open", action="store_true", help="open the wiki in your browser")
    a = ap.parse_args()
    fixed = a.port or (int(os.environ["BLUEPRINT_PORT"]) if os.environ.get("BLUEPRINT_PORT") else None)
    if not fixed:
        running = find_running()
        if running:
            url = "http://localhost:%d" % running
            print("Blueprint is already running at %s" % url)
            if a.open:
                webbrowser.open(url)
            return
    setup()
    srv = None
    for port in ([fixed] if fixed else [8080, 8081, 8082, 8083]):
        try:
            srv = ThreadingHTTPServer((a.host, port), Handler)
            break
        except OSError as e:
            print("Port %d is busy (%s)" % (port, e))
    if not srv:
        print("Could not start. Is Blueprint already running? Try:  python3 server.py --port 9090")
        sys.exit(1)
    a.port = srv.server_port
    url = "http://localhost:%d" % a.port
    print("\n  Blueprint wiki v%s is running" % VERSION)
    print("  On this computer:    %s" % url)
    if a.host == "0.0.0.0":
        for ip in lan_ips():
            print("  On your network:     http://%s:%d" % (ip, a.port))
    print("  Data folder:         %s" % DATA)
    print("  Press Ctrl+C to stop.\n")
    if a.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
