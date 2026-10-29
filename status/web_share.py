"""Web access for My Apps: open the menu from a phone or another computer, behind a password.

Started by pi-status.py. It is off until you switch it on in the menu
("🌐 Web access"), and it needs a password before it will turn on.

  * listens on port 8400 on your home network (MYAPPS_WEB_PORT changes it)
  * everything needs the password first; a wrong guess 5 times locks that
    device out for a while, getting longer each time
  * the password is stored only as a salted PBKDF2 hash in
    ~/.config/myapps-status/web.json (readable by you only)
  * other devices see the server lights but can't start or stop anything,
    and never get the key the Pi's own menu uses for that
  * it's plain http, so it's for your home Wi-Fi: don't forward this port
    on your router
"""
import hashlib, hmac, json, mimetypes, os, re, secrets, shutil, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs, quote

WEB_PORT = int(os.environ.get("MYAPPS_WEB_PORT", "8400"))
ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # the My Apps folder
CONF_DIR = os.path.join(os.path.expanduser("~"), ".config", "myapps-status")
WEB_FILE = os.path.join(CONF_DIR, "web.json")
MENU = "My Apps Menu.html"
SESSION_DAYS = 30
MIN_PW = 8
ITER = 200000

for ext, typ in ((".wasm", "application/wasm"), (".mjs", "text/javascript"), (".js", "text/javascript"),
                 (".glb", "model/gltf-binary"), (".gltf", "model/gltf+json"), (".webp", "image/webp"),
                 (".avif", "image/avif"), (".woff2", "font/woff2"), (".json", "application/json"),
                 (".svg", "image/svg+xml"), (".mp3", "audio/mpeg"), (".ogg", "audio/ogg"), (".wav", "audio/wav")):
    mimetypes.add_type(typ, ext)

_lock = threading.RLock()
_srv = {"srv": None, "err": ""}
_fails = {}          # ip -> [wrong guesses in a row, locked until]
_shared = {"state": None, "lock": None}


# ------------------------------------------------------------------ settings file
def _load():
    try:
        d = json.load(open(WEB_FILE))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = WEB_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, WEB_FILE)


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), ITER).hex()


def _sid_key(token):
    return hashlib.sha256(token.encode()).hexdigest()     # only a fingerprint of each sign-in is kept on disk


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _live_sessions(d):
    now = time.time()
    return {k: v for k, v in (d.get("sessions") or {}).items() if isinstance(v, (int, float)) and v > now}


# ------------------------------------------------------------------ what the Pi's own menu sees and does
def info():
    with _lock:
        d = _load()
        running = _srv["srv"] is not None
        return dict(on=running, want=bool(d.get("on")), port=WEB_PORT, hasPassword=bool(d.get("hash")),
                    url="http://%s:%d/" % (_lan_ip(), WEB_PORT), host=socket.gethostname(),
                    err=_srv["err"], devices=len(_live_sessions(d)))


def control(body):
    """POST /web from the Pi's own menu: {on: true/false, password: "...", signout: true}."""
    with _lock:
        d = _load()
        msgs = []
        pw = body.get("password")
        if pw is not None:
            pw = str(pw)
            if len(pw) < MIN_PW:
                return dict(ok=False, msg="Use at least %d characters for the password." % MIN_PW, web=info())
            if len(pw) > 128:
                return dict(ok=False, msg="That password is too long (128 characters at most).", web=info())
            had = bool(d.get("hash"))
            d["salt"] = secrets.token_hex(16)
            d["hash"] = _hash(pw, d["salt"])
            d["sessions"] = {}
            msgs.append("Password changed. Everyone signs in again with the new one." if had else "Password saved.")
        if body.get("signout"):
            d["sessions"] = {}
            msgs.append("Signed out every other device.")
        if body.get("on") is not None:
            want = bool(body.get("on"))
            if want and not d.get("hash"):
                return dict(ok=False, msg="Pick a password first, then turn web access on.", web=info())
            d["on"] = want
            if not want:
                d["sessions"] = {}
        _save(d)
        ok = apply()
        w = info()
        if body.get("on") is not None:
            if w["on"]:
                msgs.append("Web access is on: open %s on your phone or PC." % w["url"])
            elif not ok:
                msgs.append("Couldn't turn web access on. %s" % w["err"])
            else:
                msgs.append("Web access is off.")
        return dict(ok=ok, msg=" ".join(msgs) or "Saved.", web=w)


def apply():
    """Start or stop the web server so it matches the settings file. True if that worked."""
    with _lock:
        d = _load()
        want = bool(d.get("on") and d.get("hash"))
        srv = _srv["srv"]
        if want and srv is None:
            try:
                s = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), ShareHandler)
            except OSError as e:
                _srv["err"] = "Port %d is already used by another program (%s)." % (WEB_PORT, e.strerror or e)
                return False
            s.daemon_threads = True
            threading.Thread(target=s.serve_forever, name="myapps-web", daemon=True).start()
            _srv["srv"], _srv["err"] = s, ""
        elif not want and srv is not None:
            _srv["srv"] = None
            def _close(s=srv):
                s.shutdown()
                s.server_close()
            threading.Thread(target=_close, daemon=True).start()
        if not want:
            _srv["err"] = ""
        return True


def start(state, lock):
    """Called once by pi-status.py: remember the server lights, and turn web access back on if it was on."""
    _shared["state"], _shared["lock"] = state, lock
    apply()


# ------------------------------------------------------------------ sign in
def _locked_for(ip):
    f = _fails.get(ip)
    return max(0, int(f[1] - time.time())) if f else 0


def _wrong(ip):
    f = _fails.setdefault(ip, [0, 0])
    f[0] += 1
    if f[0] >= 5:
        f[1] = time.time() + min(900, 30 * 2 ** (f[0] - 5))    # 30 s, 1 min, 2 min ... up to 15 min


def _check_password(pw):
    d = _load()
    if not d.get("hash") or not d.get("salt"):
        return False
    return hmac.compare_digest(_hash(pw, d["salt"]), d["hash"])


def _new_session():
    tok = secrets.token_hex(32)
    with _lock:
        d = _load()
        s = _live_sessions(d)
        s[_sid_key(tok)] = int(time.time() + SESSION_DAYS * 86400)
        d["sessions"] = dict(sorted(s.items(), key=lambda kv: kv[1])[-50:])     # 50 devices at most
        _save(d)
    return tok


def _end_session(tok):
    with _lock:
        d = _load()
        s = _live_sessions(d)
        s.pop(_sid_key(tok), None)
        d["sessions"] = s
        _save(d)


def _valid(tok):
    if not tok:
        return False
    with _lock:
        exp = (_load().get("sessions") or {}).get(_sid_key(tok))
    return isinstance(exp, (int, float)) and exp > time.time()


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>My Apps · Sign in</title>
<style>
:root{--bg:#14111f;--panel:#1f1a30;--panel2:#2a2341;--ink:#f4efe6;--dim:#a79fbd;--games:#ff5d73;--apps:#4fd1c5}
*{box-sizing:border-box}html,body{margin:0;height:100%%}
body{display:grid;place-items:center;padding:16px;color:var(--ink);font-family:"DejaVu Sans","Noto Sans",system-ui,sans-serif;
 background:radial-gradient(900px 420px at 10%% -10%%,#3a2459 0%%,transparent 60%%),radial-gradient(700px 420px at 110%% 10%%,#123c4a 0%%,transparent 60%%),var(--bg)}
form{width:100%%;max-width:380px;background:rgba(31,26,48,.85);border:2px solid var(--panel2);border-radius:22px;padding:28px 24px 22px}
h1{margin:0 0 4px;font-size:40px;font-weight:900;letter-spacing:-1px}
h1 span{display:inline-block;transform:rotate(-3deg);background:var(--games);color:var(--bg);padding:0 12px;border-radius:9px;margin-left:4px}
.host{color:var(--dim);font-size:14px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin:0 0 22px}
label{display:block;font-size:14px;color:var(--dim);margin-bottom:6px}
input{width:100%%;background:var(--panel);border:3px solid var(--panel2);color:var(--ink);font:inherit;font-size:18px;padding:12px 16px;border-radius:14px}
input:focus{outline:none;border-color:var(--apps)}
button{width:100%%;margin-top:14px;appearance:none;border:0;background:var(--apps);color:var(--bg);font:inherit;font-size:18px;font-weight:800;padding:13px;border-radius:999px;cursor:pointer}
button:focus-visible{outline:3px solid #fff;outline-offset:2px}
.err{background:#5a2330;color:#ffd0d8;border-radius:12px;padding:9px 12px;font-size:14px;margin:0 0 14px}
.note{color:var(--dim);font-size:12.5px;margin:16px 0 0;line-height:1.4}
</style></head><body>
<form method="post" action="/__myapps/login" autocomplete="on">
<h1>My <span>Apps</span></h1><p class="host">🍓 %(host)s</p>
%(err)s<label for="pw">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="%(next)s">
<button type="submit">Open My Apps</button>
<p class="note">This is the web access password set in My Apps on the Pi. You stay signed in on this device for %(days)d days.</p>
</form></body></html>"""


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _safe_next(n):
    n = str(n or "/")
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n and len(n) < 2000 else "/"


# ------------------------------------------------------------------ the web server other devices talk to
class ShareHandler(BaseHTTPRequestHandler):
    server_version = "MyApps"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _token(self):
        m = re.search(r"(?:^|;\s*)myapps_s=([0-9a-f]{64})(?:;|$)", self.headers.get("Cookie", ""))
        return m.group(1) if m else None

    def _head(self, code, ctype, length, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self._head(code, ctype, len(b), (("Cache-Control", "no-store"),) + tuple(extra))
        if self.command != "HEAD":
            self.wfile.write(b)

    def _redirect(self, to, extra=()):
        self._send(303, "", extra=(("Location", to),) + tuple(extra))

    def _login_page(self, nxt="/", err="", code=200):
        e = '<p class="err">%s</p>' % _esc(err) if err else ""
        self._send(code, LOGIN_PAGE % dict(host=_esc(socket.gethostname()), err=e, next=_esc(_safe_next(nxt)),
                                           days=SESSION_DAYS))

    # ---------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/__myapps/login":
            if _valid(self._token()):
                return self._redirect(_safe_next(parse_qs(u.query).get("next", ["/"])[0]))
            return self._login_page(parse_qs(u.query).get("next", ["/"])[0])
        if path == "/__myapps/logout":
            t = self._token()
            if t:
                _end_session(t)
            return self._redirect("/__myapps/login", (("Set-Cookie", "myapps_s=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"),))
        if path == "/favicon.ico" and os.path.exists(os.path.join(ROOT, "icon.png")):
            path = "/icon.png"
        if not _valid(self._token()):
            accept = self.headers.get("Accept", "")
            if path.startswith("/__myapps/") or ("text/html" not in accept and path != "/"):
                return self._send(401, '{"ok":false,"msg":"Sign in first."}', "application/json")
            return self._redirect("/__myapps/login?next=" + quote(self.path, safe="/"))
        if path == "/__myapps/status":
            st, lk = _shared["state"], _shared["lock"]
            if st is None:
                return self._send(503, '{"ok":false}', "application/json")
            with lk:
                d = dict(st, ok=True, remote=True)
            return self._send(200, json.dumps(d), "application/json")
        if path == "/status/token.js":     # the Pi's own key never leaves the Pi
            return self._send(200, "window.MYAPPS_STATUS={remote:true};\n", "text/javascript; charset=utf-8")
        return self._file(path)

    do_HEAD = do_GET

    def _file(self, path):
        rel = unquote(path).lstrip("/") or MENU
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or p.startswith(".") for p in parts) or parts[0] == "status" or "\x00" in rel:
            return self._send(404, "Not found")
        fp = os.path.realpath(os.path.join(ROOT, *parts))
        if fp != ROOT and not fp.startswith(ROOT + os.sep):
            return self._send(404, "Not found")
        if os.path.isdir(fp):
            if not path.endswith("/"):
                return self._redirect(path + "/")
            fp = os.path.join(fp, "index.html")
        if not os.path.isfile(fp):
            return self._send(404, "Not found")
        size = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        start, end, code, extra = 0, size - 1, 200, [("Accept-Ranges", "bytes"), ("Cache-Control", "private, no-cache")]
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self._send(416, "", extra=(("Content-Range", "bytes */%d" % size),))
            code = 206
            extra.append(("Content-Range", "bytes %d-%d/%d" % (start, end, size)))
        length = end - start + 1 if size else 0
        self._head(code, ctype, length, extra)
        if self.command == "HEAD" or not length:
            return
        try:
            with open(fp, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------- POST (only signing in)
    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if u.path != "/__myapps/login" or n > 4096:
            if n <= 65536:
                self.rfile.read(n)
            return self._send(404, "Not found", extra=(("Connection", "close"),))
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        nxt = form.get("next", ["/"])[0]
        ip = self.client_address[0]
        wait = _locked_for(ip)
        if wait:
            return self._login_page(nxt, "Too many wrong tries. Wait %d seconds and try again." % wait, 429)
        if not _check_password(form.get("password", [""])[0]):
            _wrong(ip)
            time.sleep(0.6)
            wait = _locked_for(ip)
            return self._login_page(nxt, ("Wrong password. Locked for %d seconds." % wait) if wait
                                    else "That password isn't right. Try again.", 401)
        _fails.pop(ip, None)
        tok = _new_session()
        self._redirect(_safe_next(nxt), (("Set-Cookie", "myapps_s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax"
                                          % (tok, SESSION_DAYS * 86400)),))
