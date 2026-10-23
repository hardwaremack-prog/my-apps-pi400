#!/usr/bin/env python3
"""
PiSnip - a Snipping Tool for Raspberry Pi (Raspberry Pi OS: labwc / Wayfire / X11)

  pisnip                 open the PiSnip window
  pisnip --snip          snip now (what Print Screen / Raspberry+Shift+S run)
  pisnip --full          capture the whole screen immediately
  pisnip --record        record an area of the screen to video
  pisnip FILE            open an image in the editor
  pisnip --settings      open settings
  pisnip --setup-hotkeys add Print Screen, Raspberry+Shift+S and Raspberry+Shift+R shortcuts
  pisnip --setup-shortcuts add the menu entry, desktop icon and taskbar button
"""
import argparse
import datetime
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, colorchooser
except ImportError:
    sys.stderr.write("PiSnip needs Tk:  sudo apt install python3-tk\n")
    sys.exit(1)
try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageChops
except ImportError:
    sys.stderr.write("PiSnip needs Pillow:  sudo apt install python3-pil python3-pil.imagetk\n")
    sys.exit(1)

APP_NAME = "PiSnip"
VERSION = "1.0"
HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "pisnip")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

_R = getattr(Image, "Resampling", Image)
LANCZOS, BILINEAR = _R.LANCZOS, _R.BILINEAR
SS = 2  # supersampling factor for smooth ink

MODES = [("rectangle", "Rectangle"), ("window", "Window"), ("freeform", "Freeform"), ("full", "Full screen")]
MODE_LABEL = dict(MODES)
MODE_KEY = {v: k for k, v in MODES}
DELAYS = [(0, "No delay"), (3, "3 seconds"), (5, "5 seconds"), (10, "10 seconds")]
DELAY_LABEL = dict(DELAYS)
DELAY_KEY = {v: k for k, v in DELAYS}
PALETTE = ["#000000", "#ffffff", "#e81123", "#ff8c00", "#ffeb3b", "#16c60c", "#0078d4", "#886ce4"]
ACCENT = "#0078d4"


# ----------------------------------------------------------------- helpers
def have(cmd):
    return shutil.which(cmd) is not None


def run_out(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def is_wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def xdg_user_dir(kind, fallback):
    out = run_out(["xdg-user-dir", kind]).strip() if have("xdg-user-dir") else ""
    if out and os.path.normpath(out) != os.path.normpath(HOME):
        return out
    return os.path.join(HOME, fallback)


DEFAULTS = {
    "save_dir": os.path.join(xdg_user_dir("PICTURES", "Pictures"), "Screenshots"),
    "video_dir": os.path.join(xdg_user_dir("VIDEOS", "Videos"), "Screen Recordings"),
    "auto_save": True,
    "auto_copy": True,
    "format": "png",
    "ask_save_edits": True,
    "open_editor_after_hotkey": False,
    "multiple_windows": False,
    "add_border": False,
    "border_color": "#000000",
    "border_width": 2,
    "mode": "rectangle",
    "delay": 0,
    "record_fps": 24,
    "crosshair_guides": True,
    "pen_color": "#e81123",
    "hl_color": "#ffeb3b",
    "pen_size": 4,
    "hl_size": 20,
    "text_size": 28,
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        sys.stderr.write(f"Could not save settings: {e}\n")


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H%M%S")


def unique_path(folder, stem, ext):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, stem + ext)
    i = 1
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem} ({i}){ext}")
        i += 1
    return p


def hex_rgba(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def is_light(h):
    r, g, b, _ = hex_rgba(h)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 180


def flatten(img, bg=(255, 255, 255)):
    if img.mode in ("RGBA", "LA"):
        out = Image.new("RGB", img.size, bg)
        out.paste(img, mask=img.getchannel("A"))
        return out
    return img.convert("RGB")


def has_transparency(img):
    return img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 255


def save_image(img, path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        flatten(img).save(path, "JPEG", quality=95)
    elif ext in (".gif", ".bmp"):
        flatten(img).save(path)
    else:
        if img.mode == "RGBA" and not has_transparency(img):
            img = img.convert("RGB")
        img.save(path, "PNG")


def add_border(img, color, width):
    width = max(1, int(width))
    mode = "RGBA" if img.mode == "RGBA" else "RGB"
    out = Image.new(mode, (img.width + 2 * width, img.height + 2 * width), hex_rgba(color) if mode == "RGBA" else hex_rgba(color)[:3])
    if mode == "RGBA":
        out.paste(img, (width, width), img)
    else:
        out.paste(img, (width, width))
    return out


def open_path(path):
    for cmd in ("xdg-open", "pcmanfm", "gio"):
        if have(cmd):
            args = [cmd, "open", path] if cmd == "gio" else [cmd, path]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False


# ------------------------------------------------------------- clipboard
def _clip_cmd(mime):
    if is_wayland() and have("wl-copy"):
        return ["wl-copy", "--type", mime]
    if have("xclip"):
        return ["xclip", "-selection", "clipboard", "-t", mime, "-i"]
    if have("wl-copy"):
        return ["wl-copy", "--type", mime]
    return None


def clipboard_image(img):
    cmd = _clip_cmd("image/png")
    if not cmd:
        return False
    buf = io.BytesIO()
    (img if img.mode in ("RGB", "RGBA") else img.convert("RGBA")).save(buf, "PNG")
    try:
        subprocess.run(cmd, input=buf.getvalue(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return True
    except Exception:
        return False


def clipboard_text(text, tkroot=None):
    cmd = _clip_cmd("text/plain;charset=utf-8")
    if cmd and "xclip" in cmd[0]:
        cmd = ["xclip", "-selection", "clipboard", "-i"]
    ok = False
    if cmd:
        try:
            subprocess.run(cmd, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            ok = True
        except Exception:
            pass
    if not ok and tkroot is not None:
        tkroot.clipboard_clear()
        tkroot.clipboard_append(text)
        ok = True
    return ok


# ---------------------------------------------------------------- capture
def grab_screen():
    """Return a PIL RGB image of the whole desktop, or None."""
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if is_wayland() and have("grim"):
            r = subprocess.run(["grim", tmp], capture_output=True, timeout=20)
            if r.returncode == 0:
                with Image.open(tmp) as im:
                    return im.convert("RGB")
        if not is_wayland():
            try:
                from PIL import ImageGrab
                return ImageGrab.grab().convert("RGB")
            except Exception:
                pass
        for cmd in (["grim", tmp], ["scrot", "-o", tmp], ["import", "-window", "root", tmp]):
            if have(cmd[0]):
                r = subprocess.run(cmd, capture_output=True, timeout=20)
                if r.returncode == 0 and os.path.getsize(tmp) > 0:
                    with Image.open(tmp) as im:
                        return im.convert("RGB")
    except Exception as e:
        sys.stderr.write(f"Screen capture failed: {e}\n")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None


def x11_windows(exclude_ids=()):
    """Visible top-level windows (x1,y1,x2,y2) bottom-to-top, frames included. None if unavailable."""
    if is_wayland() or not (have("xprop") and have("xwininfo")):
        return None
    out = run_out(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
    if "#" not in out:
        return None
    ids = re.findall(r"0x[0-9a-fA-F]+", out.split("#", 1)[1])
    excl = {int(i) for i in exclude_ids}
    wins = []
    for wid in ids:
        if int(wid, 16) in excl:
            continue
        info = run_out(["xwininfo", "-id", wid])
        if "IsViewable" not in info:
            continue
        try:
            ax = int(re.search(r"Absolute upper-left X:\s+(-?\d+)", info).group(1))
            ay = int(re.search(r"Absolute upper-left Y:\s+(-?\d+)", info).group(1))
            w = int(re.search(r"Width:\s+(\d+)", info).group(1))
            h = int(re.search(r"Height:\s+(\d+)", info).group(1))
        except AttributeError:
            continue
        props = run_out(["xprop", "-id", wid, "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE", "_NET_FRAME_EXTENTS"])
        if "_NET_WM_WINDOW_TYPE_DOCK" in props or "_NET_WM_WINDOW_TYPE_DESKTOP" in props:
            continue
        if "_NET_WM_STATE_HIDDEN" in props:
            continue
        m = re.search(r"_NET_FRAME_EXTENTS\(CARDINAL\) = (\d+), (\d+), (\d+), (\d+)", props)
        if m:
            l, r, t, b = map(int, m.groups())
            ax, ay, w, h = ax - l, ay - t, w + l + r, h + t + b
        wins.append((ax, ay, ax + w, ay + h))
    return wins


# ------------------------------------------------------------ annotations
_FONTS = {}
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


def get_font(size):
    size = max(6, int(size))
    if size not in _FONTS:
        f = None
        for p in FONT_PATHS:
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    break
                except Exception:
                    pass
        if f is None:
            try:
                f = ImageFont.load_default(size=size)
            except TypeError:
                f = ImageFont.load_default()
        _FONTS[size] = f
    return _FONTS[size]


def _ann_points(a):
    return a["pts"] if a["type"] in ("pen", "hl") else [a["p1"], a["p2"]]


def _build_mask(a):
    t, w = a["type"], a["width"]
    pts = _ann_points(a)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = w + 3 + (w * 4 + 14 if t == "arrow" else 0)
    x0, y0 = int(math.floor(min(xs) - pad)), int(math.floor(min(ys) - pad))
    x1, y1 = int(math.ceil(max(xs) + pad)), int(math.ceil(max(ys) + pad))
    W, H = max(1, x1 - x0), max(1, y1 - y0)
    m = Image.new("L", (W * SS, H * SS), 0)
    d = ImageDraw.Draw(m)

    def T(p):
        return ((p[0] - x0) * SS, (p[1] - y0) * SS)

    sw = max(1, round(w * SS))
    r = sw / 2

    def dot(p):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=255)

    if t in ("pen", "hl"):
        sp = [T(p) for p in pts]
        if len(sp) > 1:
            d.line(sp, fill=255, width=sw)
        for p in (sp if sw >= 5 else [sp[0], sp[-1]]):
            dot(p)
    elif t in ("line", "arrow"):
        a1, a2 = T(a["p1"]), T(a["p2"])
        if t == "arrow":
            ang = math.atan2(a2[1] - a1[1], a2[0] - a1[0])
            L = max(10, w * 3.5) * SS
            spread = 0.45
            head = [a2,
                    (a2[0] - L * math.cos(ang - spread), a2[1] - L * math.sin(ang - spread)),
                    (a2[0] - L * math.cos(ang + spread), a2[1] - L * math.sin(ang + spread))]
            seglen = math.hypot(a2[0] - a1[0], a2[1] - a1[1])
            cut = min(seglen, L * 0.7)
            end = (a2[0] - cut * math.cos(ang), a2[1] - cut * math.sin(ang))
            d.line([a1, end], fill=255, width=sw)
            dot(a1)
            d.polygon(head, fill=255)
        else:
            d.line([a1, a2], fill=255, width=sw)
            dot(a1)
            dot(a2)
    elif t in ("rect", "ellipse"):
        (ax, ay), (bx, by) = T(a["p1"]), T(a["p2"])
        box = [min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r, max(ay, by) + r]
        if t == "rect":
            d.rectangle(box, outline=255, width=sw)
        else:
            d.ellipse(box, outline=255, width=sw)
    m = m.resize((W, H), LANCZOS)
    return (x0, y0), m


def render_ann(img, a):
    t = a["type"]
    if t == "text":
        ImageDraw.Draw(img).text(a["pos"], a["text"], fill=hex_rgba(a["color"]), font=get_font(a["size"]))
        return
    if t == "redact":
        ImageDraw.Draw(img).rectangle(a["box"], fill=(0, 0, 0, 255))
        return
    if "_m" not in a:
        a["_m"] = _build_mask(a)
    (x0, y0), m = a["_m"]
    if t == "hl":
        region = img.crop((x0, y0, x0 + m.width, y0 + m.height))
        colored = ImageChops.multiply(region, Image.new("RGBA", m.size, hex_rgba(a["color"])))
        img.paste(Image.composite(colored, region, m), (x0, y0))
    else:
        img.paste(Image.new("RGBA", m.size, hex_rgba(a["color"])), (x0, y0), m)


def shift_ann(a, dx, dy):
    b = {k: v for k, v in a.items() if k != "_m"}
    t = a["type"]
    if t in ("pen", "hl"):
        b["pts"] = [(x + dx, y + dy) for x, y in a["pts"]]
    elif t == "text":
        b["pos"] = (a["pos"][0] + dx, a["pos"][1] + dy)
    elif t == "redact":
        x0, y0, x1, y1 = a["box"]
        b["box"] = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    else:
        b["p1"] = (a["p1"][0] + dx, a["p1"][1] + dy)
        b["p2"] = (a["p2"][0] + dx, a["p2"][1] + dy)
    return b


def _seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def ann_hit(a, x, y, tol):
    t = a["type"]
    if t in ("pen", "hl", "line", "arrow"):
        pts = _ann_points(a)
        lim = a["width"] / 2 + tol
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1]) <= lim
        return any(_seg_dist((x, y), pts[i], pts[i + 1]) <= lim for i in range(len(pts) - 1))
    if t in ("rect", "ellipse"):
        (ax, ay), (bx, by) = a["p1"], a["p2"]
        pad = a["width"] / 2 + tol
        return min(ax, bx) - pad <= x <= max(ax, bx) + pad and min(ay, by) - pad <= y <= max(ay, by) + pad
    if t == "redact":
        x0, y0, x1, y1 = a["box"]
        return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
    if t == "text":
        l, tp, r, b = get_font(a["size"]).getbbox(a["text"])
        px, py = a["pos"]
        return px + l - tol <= x <= px + r + tol and py + tp - tol <= y <= py + b + tol
    return False


# -------------------------------------------------------------------- icon
def make_icon_image(size=128):
    s = size / 128
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4 * s, 4 * s, 124 * s, 124 * s], radius=26 * s, fill=hex_rgba(ACCENT))
    w = max(2, round(6 * s))
    # selection corners
    for (cx, cy, dx, dy) in ((26, 30, 1, 1), (102, 30, -1, 1), (26, 98, 1, -1), (102, 98, -1, -1)):
        d.line([(cx * s, cy * s), ((cx + 22 * dx) * s, cy * s)], fill="white", width=w)
        d.line([(cx * s, cy * s), (cx * s, (cy + 20 * dy) * s)], fill="white", width=w)
    # raspberry-red pen stroke
    d.line([(40 * s, 78 * s), (56 * s, 62 * s), (72 * s, 72 * s), (90 * s, 50 * s)], fill=(197, 26, 74, 255), width=max(3, round(9 * s)), joint="curve")
    return im


def draw_icon(name, px=20, color="#1f1f1f"):
    """Small line icons for the main window (drawn, so no icon theme is needed)."""
    S = 4
    N = px * S
    k = N / 20.0
    m = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(m)
    w = max(1, round(1.5 * k))

    def P(*pts):
        return [(x * k, y * k) for x, y in pts]

    def box(x0, y0, x1, y1):
        return [x0 * k, y0 * k, x1 * k, y1 * k]

    if name == "camera":
        d.rounded_rectangle(box(2, 6, 18, 16.5), radius=2.5 * k, outline=255, width=w)
        d.line(P((7, 6), (8, 3.8), (12, 3.8), (13, 6)), fill=255, width=w, joint="curve")
        d.ellipse(box(7, 8, 13, 14), outline=255, width=w)
    elif name == "video":
        d.rounded_rectangle(box(1.5, 5.5, 13.5, 15), radius=2.5 * k, outline=255, width=w)
        d.polygon(P((14.5, 9.2), (18.5, 6.5), (18.5, 14), (14.5, 11.3)), fill=255)
    elif name == "rectangle":
        d.rectangle(box(3, 4.5, 17, 15.5), outline=255, width=w)
        for (x, y) in ((3, 4.5), (17, 4.5), (3, 15.5), (17, 15.5)):
            d.rectangle(box(x - 1.3, y - 1.3, x + 1.3, y + 1.3), fill=255)
    elif name == "window":
        d.rounded_rectangle(box(2.5, 3.5, 17.5, 16.5), radius=1.5 * k, outline=255, width=w)
        d.rectangle(box(2.5, 3.5, 17.5, 7), fill=255)
    elif name == "freeform":
        pts = []
        for i in range(0, 361, 12):
            a = math.radians(i)
            rr = 6.5 + 1.4 * math.sin(3 * a) + 0.8 * math.cos(5 * a)
            pts.append((10 + rr * math.cos(a), 10 + rr * 0.85 * math.sin(a)))
        d.line(P(*pts), fill=255, width=w, joint="curve")
    elif name == "full":
        d.rounded_rectangle(box(2, 3, 18, 14), radius=1.2 * k, outline=255, width=w)
        d.line(P((10, 14), (10, 17)), fill=255, width=w)
        d.line(P((6, 17.2), (14, 17.2)), fill=255, width=w)
    elif name == "clock":
        d.ellipse(box(2.5, 2.5, 17.5, 17.5), outline=255, width=w)
        d.line(P((10, 5.5), (10, 10), (13.5, 12)), fill=255, width=w, joint="curve")
    elif name == "more":
        for x in (4.5, 10, 15.5):
            d.ellipse(box(x - 1.4, 8.6, x + 1.4, 11.4), fill=255)
    elif name == "plus":
        d.line(P((10, 3.5), (10, 16.5)), fill=255, width=round(w * 1.3))
        d.line(P((3.5, 10), (16.5, 10)), fill=255, width=round(w * 1.3))
    elif name == "folder":
        d.polygon(P((2.5, 5), (8, 5), (9.5, 6.8), (17.5, 6.8), (17.5, 15.5), (2.5, 15.5)), outline=255, width=w)
    elif name == "open":
        d.polygon(P((5, 2.5), (12, 2.5), (15.5, 6), (15.5, 17.5), (5, 17.5)), outline=255, width=w)
        d.line(P((11.5, 2.5), (11.5, 6.5), (15.5, 6.5)), fill=255, width=w)
    elif name == "gear":
        for i in range(8):
            a = math.radians(i * 45)
            d.line(P((10 + 5.5 * math.cos(a), 10 + 5.5 * math.sin(a)), (10 + 8 * math.cos(a), 10 + 8 * math.sin(a))), fill=255, width=round(w * 1.6))
        d.ellipse(box(4.5, 4.5, 15.5, 15.5), outline=255, width=w)
        d.ellipse(box(8, 8, 12, 12), outline=255, width=w)
    elif name == "keyboard":
        d.rounded_rectangle(box(1.5, 5, 18.5, 15), radius=1.5 * k, outline=255, width=w)
        for x in (5, 8, 11, 14):
            d.rectangle(box(x - 0.7, 7.8, x + 0.7, 9.2), fill=255)
        d.line(P((6, 12), (14, 12)), fill=255, width=w)
    elif name == "pin":
        d.polygon(P((7, 2.5), (13, 2.5), (12, 8), (15, 11), (5, 11), (8, 8)), outline=255, width=w)
        d.line(P((10, 11), (10, 17.5)), fill=255, width=w)
    elif name == "info":
        d.ellipse(box(2.5, 2.5, 17.5, 17.5), outline=255, width=w)
        d.line(P((10, 9), (10, 14)), fill=255, width=w)
        d.ellipse(box(9, 5.3, 11, 7.3), fill=255)
    m = m.resize((px, px), LANCZOS)
    out = Image.new("RGBA", (px, px), hex_rgba(color, 0))
    out.putalpha(m)
    solid = Image.new("RGBA", (px, px), hex_rgba(color))
    solid.putalpha(m)
    return solid


class Tooltip:
    def __init__(self, widget, text):
        self.w, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, e=None):
        self.job = self.w.after(600, self._show)

    def _show(self):
        if self.tip:
            return
        t = self.tip = tk.Toplevel(self.w)
        t.overrideredirect(True)
        tk.Label(t, text=self.text, bg="#2b2b2b", fg="white", padx=8, pady=3).pack()
        t.geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 6}")

    def _hide(self, e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---------------------------------------------------------------- hotkeys
def _labwc_merge_mode():
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                parts = f.read().split(b"\0")
        except OSError:
            continue
        if parts and os.path.basename(parts[0].decode(errors="ignore")) == "labwc":
            return any(p in (b"-m", b"--merge-config") for p in parts)
    return None


def _patch_xml_keybinds(src, dst, binds, flavour, add_default_if_empty=False):
    import xml.etree.ElementTree as ET
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    if src and os.path.exists(src):
        tree = ET.parse(src, parser)
        root = tree.getroot()
    else:
        root = ET.Element("labwc_config" if flavour == "labwc" else "{http://openbox.org/3.4/rc}openbox_config")
        tree = ET.ElementTree(root)
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    ET.register_namespace("xi", "http://www.w3.org/2001/XInclude")
    if ns:
        ET.register_namespace("", ns)

    def q(tag):
        return f"{{{ns}}}{tag}" if ns else tag

    kb = root.find(q("keyboard"))
    if kb is None:
        kb = ET.SubElement(root, q("keyboard"))
    existing = kb.findall(q("keybind"))
    if add_default_if_empty and not existing and kb.find(q("default")) is None:
        kb.insert(0, ET.Element(q("default")))
    keys = {k.lower() for k, _ in binds}
    for el in existing:
        if el.get("key", "").lower() in keys:
            kb.remove(el)
    for key, cmd in binds:
        el = ET.SubElement(kb, q("keybind"), {"key": key})
        if flavour == "labwc":
            ET.SubElement(el, q("action"), {"name": "Execute", "command": cmd})
        else:
            act = ET.SubElement(el, q("action"), {"name": "Execute"})
            ET.SubElement(act, q("command")).text = cmd
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and not os.path.exists(dst + ".pisnip-backup"):
        shutil.copy2(dst, dst + ".pisnip-backup")
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tree.write(dst, encoding="utf-8", xml_declaration=True)


def setup_hotkeys(exe=None):
    exe = exe or os.path.realpath(sys.argv[0])
    if exe.endswith(".py") and not os.access(exe, os.X_OK):
        exe = f"python3 {exe}"
    snip, rec = f"{exe} --snip", f"{exe} --record"
    report = []
    cfg = os.path.join(HOME, ".config")

    # labwc (Raspberry Pi OS default Wayland desktop)
    labwc_user = os.path.join(cfg, "labwc", "rc.xml")
    labwc_sys = next((p for p in ("/etc/xdg/labwc/rc.xml",) if os.path.exists(p)), None)
    if have("labwc") or os.path.isdir(os.path.dirname(labwc_user)):
        try:
            merge = _labwc_merge_mode()
            binds = [("Print", snip), ("W-S-s", snip), ("W-S-r", rec)]
            if os.path.exists(labwc_user):
                _patch_xml_keybinds(labwc_user, labwc_user, binds, "labwc", add_default_if_empty=(merge is not True))
            else:
                _patch_xml_keybinds(labwc_sys, labwc_user, binds, "labwc", add_default_if_empty=True)
            if have("labwc"):
                subprocess.run(["labwc", "--reconfigure"], capture_output=True, timeout=5)
            report.append("labwc: Print, Raspberry+Shift+S, Raspberry+Shift+R set")
        except Exception as e:
            report.append(f"labwc: failed ({e})")

    # Openbox (Raspberry Pi OS X11 desktop)
    ob_user = os.path.join(cfg, "openbox", "lxde-pi-rc.xml")
    ob_sys = next((p for p in ("/etc/xdg/openbox/lxde-pi-rc.xml", "/etc/xdg/openbox/rc.xml") if os.path.exists(p)), None)
    if os.path.exists(ob_user) or ob_sys:
        try:
            binds = [("Print", snip), ("W-S-s", snip), ("W-S-r", rec)]
            _patch_xml_keybinds(ob_user if os.path.exists(ob_user) else ob_sys, ob_user, binds, "openbox")
            if have("openbox"):
                subprocess.run(["openbox", "--reconfigure"], capture_output=True, timeout=5)
            report.append("Openbox (X11): Print, Raspberry+Shift+S, Raspberry+Shift+R set")
        except Exception as e:
            report.append(f"Openbox: failed ({e})")

    # Wayfire (older Raspberry Pi OS Bookworm)
    wf = os.path.join(cfg, "wayfire.ini")
    if os.path.exists(wf):
        try:
            with open(wf) as f:
                lines = f.read().splitlines()
            if not os.path.exists(wf + ".pisnip-backup"):
                shutil.copy2(wf, wf + ".pisnip-backup")
            lines = [ln for ln in lines if not re.match(r"\s*(binding|command)_pisnip", ln)]
            lines = [("# " + ln if re.match(r"\s*binding_\w+\s*=.*KEY_SYSRQ", ln) else ln) for ln in lines]
            new = ["binding_pisnip = KEY_SYSRQ", f"command_pisnip = {snip}",
                   "binding_pisnip2 = <super> <shift> KEY_S", f"command_pisnip2 = {snip}",
                   "binding_pisniprec = <super> <shift> KEY_R", f"command_pisniprec = {rec}"]
            idx = next((i for i, ln in enumerate(lines) if ln.strip() == "[command]"), None)
            if idx is None:
                lines += ["", "[command]"] + new
            else:
                end = idx + 1
                while end < len(lines) and not lines[end].strip().startswith("["):
                    end += 1
                while end > idx + 1 and not lines[end - 1].strip():
                    end -= 1
                lines[end:end] = new
            with open(wf, "w") as f:
                f.write("\n".join(lines) + "\n")
            report.append("Wayfire: Print, Raspberry+Shift+S, Raspberry+Shift+R set")
        except Exception as e:
            report.append(f"Wayfire: failed ({e})")

    if not report:
        report.append("No supported desktop config found (labwc, Openbox or Wayfire). "
                      f"Bind your Print key to:  {snip}")
    return report


# ---------------------------------------------------- desktop + taskbar icon
DESKTOP_ID = "pisnip.desktop"


def desktop_entry_text(exe):
    return f"""[Desktop Entry]
Type=Application
Name=Snipping Tool
GenericName=Snipping Tool
Comment=Take, mark up and save screenshots and screen recordings (PiSnip)
Exec={exe} %f
Icon=pisnip
Terminal=false
StartupWMClass=pisnip
Categories=Utility;Graphics;
Keywords=screenshot;snip;snipping;capture;record;pisnip;
MimeType=image/png;image/jpeg;
Actions=Snip;Record;

[Desktop Action Snip]
Name=New snip
Exec={exe} --snip

[Desktop Action Record]
Name=Record screen
Exec={exe} --record
"""


def _backup(path):
    if os.path.exists(path) and not os.path.exists(path + ".pisnip-backup"):
        shutil.copy2(path, path + ".pisnip-backup")


def _restart_detached(name, cmd):
    """Restart a running panel process so it picks up a new launcher."""
    if not have(name) or subprocess.run(["pgrep", "-x", name], capture_output=True).returncode != 0:
        return False
    subprocess.run(["pkill", "-x", name], capture_output=True)
    time.sleep(0.8)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
    return True


def _pin_wf_panel(cfg):
    """Wayland taskbar (wf-panel-pi): add launcher_NNNNNN = pisnip.desktop to [panel]."""
    user_paths = [os.path.join(cfg, "wf-panel-pi", "wf-panel-pi.ini"), os.path.join(cfg, "wf-panel-pi.ini")]
    sys_paths = ["/etc/xdg/wf-panel-pi/wf-panel-pi.ini", "/etc/xdg/wf-panel-pi.ini",
                 "/usr/share/wf-panel-pi/wf-panel-pi.ini", "/etc/wf-panel-pi.ini"]
    path = next((p for p in user_paths if os.path.exists(p)), None)
    if path is None:
        src = next((p for p in sys_paths if os.path.exists(p)), None)
        if src is None and have("wf-panel-pi"):
            # no config anywhere yet: the panel is using its built-in defaults, so write them out
            # with our launcher added (these are Raspberry Pi OS's standard three launchers)
            src_text = ("[panel]\nlauncher_000001 = lxde-x-www-browser.desktop\n"
                        "launcher_000002 = pcmanfm.desktop\nlauncher_000003 = lxterminal.desktop\n")
            path = user_paths[1]
            with open(path, "w") as f:
                f.write(src_text)
        elif src:
            up = user_paths[0] if "/wf-panel-pi/" in src else user_paths[1]
            os.makedirs(os.path.dirname(up), exist_ok=True)
            shutil.copy2(src, up)
            path = up
    if path is None:
        return None
    with open(path) as f:
        lines = f.read().splitlines()
    if any(re.match(r"\s*launcher_\w+\s*=\s*" + re.escape(DESKTOP_ID), ln) for ln in lines):
        _restart_detached("wf-panel-pi", ["wf-panel-pi"])
        return f"already listed in {path}"
    _backup(path)
    nums = [int(m.group(1)) for ln in lines for m in [re.match(r"\s*launcher_(\d+)\s*=", ln)] if m]
    new = f"launcher_{(max(nums) + 1 if nums else 1):06d} = {DESKTOP_ID}"
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == "[panel]"), None)
    if idx is None:
        lines += ["", "[panel]", new]
    else:
        last = max([i for i, ln in enumerate(lines) if re.match(r"\s*launcher_\w+\s*=", ln)] or [idx])
        lines.insert(last + 1, new)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    restarted = _restart_detached("wf-panel-pi", ["wf-panel-pi"])
    return f"pinned in {path}" + ("" if restarted else " (log out and back in to see it)")


def _pin_lxpanel(cfg):
    """X11 taskbar (lxpanel / lxpanel-pi): add a Button to the launchbar plugin."""
    pairs = [(os.path.join(cfg, "lxpanel-pi", "panels", "panel"), "/etc/xdg/lxpanel-pi/panels/panel", "lxpanel-pi"),
             (os.path.join(cfg, "lxpanel", "LXDE-pi", "panels", "panel"), "/etc/xdg/lxpanel/LXDE-pi/panels/panel", "lxpanel")]
    done = None
    for user, system, proc in pairs:
        path = user if os.path.exists(user) else None
        if path is None and os.path.exists(system):
            os.makedirs(os.path.dirname(user), exist_ok=True)
            shutil.copy2(system, user)
            path = user
        if path is None:
            continue
        with open(path) as f:
            text = f.read()
        if f"id={DESKTOP_ID}" in text:
            done = done or "already on the taskbar"
            continue
        m = re.search(r"type\s*=\s*launchbar\s*\n\s*Config\s*\{\s*\n", text)
        if not m:
            continue
        indent = re.match(r"[ \t]*", text[m.end():]).group(0) or "    "
        button = f"{indent}Button {{\n{indent}  id={DESKTOP_ID}\n{indent}}}\n"
        _backup(path)
        with open(path, "w") as f:
            f.write(text[:m.end()] + button + text[m.end():])
        if have("lxpanelctl") and subprocess.run(["pgrep", "-x", proc], capture_output=True).returncode == 0:
            subprocess.run(["lxpanelctl", "restart"], capture_output=True, timeout=10)
        done = "pinned"
    return done


def setup_shortcuts(exe=None, desktop=True, taskbar=True):
    """Menu entry + desktop icon + taskbar launcher, like Snipping Tool on Windows."""
    exe = exe or os.path.realpath(sys.argv[0])
    report = []
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
    cfg = os.path.join(HOME, ".config")
    text = desktop_entry_text(exe)

    icon = os.path.join(data, "icons", "hicolor", "128x128", "apps", "pisnip.png")
    os.makedirs(os.path.dirname(icon), exist_ok=True)
    make_icon_image(128).save(icon)
    for size in (48, 64, 256):
        p = os.path.join(data, "icons", "hicolor", f"{size}x{size}", "apps", "pisnip.png")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        make_icon_image(size).save(p)
    app_file = os.path.join(data, "applications", DESKTOP_ID)
    os.makedirs(os.path.dirname(app_file), exist_ok=True)
    with open(app_file, "w") as f:
        f.write(text)
    for cmd in (["update-desktop-database", os.path.dirname(app_file)], ["gtk-update-icon-cache", "-q", "-t", os.path.join(data, "icons", "hicolor")]):
        if have(cmd[0]):
            subprocess.run(cmd, capture_output=True)
    report.append("Menu: Accessories → Snipping Tool")

    if desktop:
        ddir = xdg_user_dir("DESKTOP", "Desktop")
        os.makedirs(ddir, exist_ok=True)
        dfile = os.path.join(ddir, DESKTOP_ID)
        with open(dfile, "w") as f:
            f.write(text)
        os.chmod(dfile, 0o755)
        if have("gio"):
            subprocess.run(["gio", "set", dfile, "metadata::trusted", "true"], capture_output=True)
        # stop the file manager asking "Execute / Open?" when the icon is double-clicked
        libfm = os.path.join(cfg, "libfm", "libfm.conf")
        try:
            content = open(libfm).read() if os.path.exists(libfm) else ""
            if not re.search(r"^\s*quick_exec\s*=\s*1", content, re.M):
                _backup(libfm)
                if re.search(r"^\s*quick_exec\s*=", content, re.M):
                    content = re.sub(r"^\s*quick_exec\s*=.*$", "quick_exec=1", content, flags=re.M)
                elif "[config]" in content:
                    content = content.replace("[config]", "[config]\nquick_exec=1", 1)
                else:
                    content = "[config]\nquick_exec=1\n" + content
                os.makedirs(os.path.dirname(libfm), exist_ok=True)
                with open(libfm, "w") as f:
                    f.write(content)
        except OSError:
            pass
        report.append(f"Desktop icon: {dfile}")

    if taskbar:
        results = []
        try:
            r = _pin_wf_panel(cfg)
            if r:
                results.append(f"Wayland taskbar {r}")
        except Exception as e:
            results.append(f"Wayland taskbar failed ({e})")
        try:
            r = _pin_lxpanel(cfg)
            if r:
                results.append(f"X11 taskbar {r}")
        except Exception as e:
            results.append(f"X11 taskbar failed ({e})")
        report.append("Taskbar: " + ("; ".join(results) if results else
                      "couldn't find the panel config - right-click the taskbar → Add/Remove Plugins… → Launcher and add Snipping Tool"))
    return report


# ================================================================ UI bits
class Toast:
    """Small notification in the bottom-right corner, like Windows' snip notification."""

    def __init__(self, app, title, subtitle="", img=None, on_click=None, buttons=(), timeout=7000):
        self.app, self.on_click = app, on_click
        app.toasts.append(self)
        t = self.top = tk.Toplevel(app.root)
        t.overrideredirect(True)
        try:
            t.attributes("-topmost", True)
        except tk.TclError:
            pass
        BG = "#2b2b2b"
        outer = tk.Frame(t, bg="#4a4a4a", padx=1, pady=1)
        outer.pack()
        f = tk.Frame(outer, bg=BG, padx=14, pady=12, cursor="hand2" if on_click else "")
        f.pack()
        head = tk.Frame(f, bg=BG)
        head.pack(fill="x")
        try:
            self.app_icon = ImageTk.PhotoImage(make_icon_image(16))
            tk.Label(head, image=self.app_icon, bg=BG).pack(side="left", padx=(0, 6))
        except Exception:
            pass
        tk.Label(head, text="Snipping Tool", bg=BG, fg="#cccccc", font=("TkDefaultFont", 9)).pack(side="left")
        x = tk.Label(head, text="✕", bg=BG, fg="#cccccc", cursor="hand2", padx=4)
        x.pack(side="right")
        x.bind("<Button-1>", lambda e: (self.close(), "break")[1])
        x.bind("<Enter>", lambda e: x.configure(fg="white"))
        x.bind("<Leave>", lambda e: x.configure(fg="#cccccc"))
        if img is not None:
            th = img.copy()
            th.thumbnail((360, 210), LANCZOS)
            self.photo = ImageTk.PhotoImage(flatten(th) if th.mode == "RGBA" else th.convert("RGB"))
            frame = tk.Frame(f, bg="#5a5a5a", padx=1, pady=1)
            frame.pack(pady=(10, 0))
            tk.Label(frame, image=self.photo, bg=BG, bd=0).pack()
        tk.Label(f, text=title, bg=BG, fg="white", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x", pady=(10, 0))
        if subtitle:
            tk.Label(f, text=subtitle, bg=BG, fg="#d0d0d0", anchor="w", justify="left", wraplength=360).pack(fill="x", pady=(2, 0))
        if buttons:
            bf = tk.Frame(f, bg=BG)
            bf.pack(fill="x", pady=(10, 0))
            for label, fn in buttons:
                tk.Button(bf, text=label, command=lambda fn=fn: (self.close(), fn()), bg="#3d3d3d", fg="white",
                          activebackground="#4d4d4d", activeforeground="white", relief="flat", padx=12, pady=3,
                          highlightthickness=0, bd=0).pack(side="left", padx=(0, 6))
        if on_click:
            def bind_all(w):
                if w is not x and isinstance(w, (tk.Label, tk.Frame)):
                    w.bind("<Button-1>", self._clicked)
                for ch in w.winfo_children():
                    bind_all(ch)
            bind_all(f)
        t.update_idletasks()
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        self.x_end = sw - t.winfo_reqwidth() - 16
        self.y = sh - t.winfo_reqheight() - 56
        self._slide(sw, self.x_end)
        self.hover = False
        t.bind("<Enter>", lambda e: setattr(self, "hover", True))
        t.bind("<Leave>", lambda e: setattr(self, "hover", False))
        self.deadline = time.time() + timeout / 1000
        t.after(500, self._tick)

    def _slide(self, x, end):
        """Slide in from the right edge, like a Windows notification."""
        try:
            self.top.geometry(f"+{int(x)}+{self.y}")
            if x > end:
                self.top.after(15, lambda: self._slide(max(end, x - max(8, (x - end) * 0.25)), end))
        except tk.TclError:
            pass

    def _tick(self):
        if not self.top.winfo_exists():
            return
        if self.hover:
            self.deadline = max(self.deadline, time.time() + 2)
        if time.time() >= self.deadline:
            self.close()
        else:
            self.top.after(500, self._tick)

    def _clicked(self, e=None):
        cb = self.on_click
        self.close()
        if cb:
            cb()

    def close(self):
        if self in self.app.toasts:
            self.app.toasts.remove(self)
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        self.app.check_exit()


class Countdown:
    def __init__(self, app, seconds, then):
        self.app, self.left, self.then = app, seconds, then
        t = self.top = tk.Toplevel(app.root)
        t.overrideredirect(True)
        try:
            t.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.lbl = tk.Label(t, text=str(seconds), font=("TkDefaultFont", 40, "bold"), fg="white", bg="#202020", padx=34, pady=10)
        self.lbl.pack()
        tk.Label(t, text="Snipping in…", fg="#cccccc", bg="#202020").pack(fill="x")
        t.update_idletasks()
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry(f"+{(sw - t.winfo_reqwidth()) // 2}+{(sh - t.winfo_reqheight()) // 2}")
        t.after(1000, self._tick)

    def _tick(self):
        self.left -= 1
        if self.left <= 0:
            self.top.destroy()
            self.app.root.after(350, self.then)
        else:
            self.lbl.configure(text=str(self.left))
            self.top.after(1000, self._tick)


# ============================================================ snip overlay
class SnipOverlay:
    """Freezes the screen and lets the user pick an area, window, freeform shape or full screen."""

    def __init__(self, app, shot, mode, on_done, on_cancel, record=False):
        self.app, self.shot, self.record = app, shot, record
        self.mode = mode if not record or mode in ("rectangle", "full") else "rectangle"
        self.on_done, self.on_cancel = on_done, on_cancel
        self.finished = False
        self.wins = None
        self.sel = None
        self.pts = []
        self.start = None
        self.ox = self.oy = 0

        t = self.top = tk.Toplevel(app.root)
        t.title("PiSnip")
        t.configure(bg="black", cursor="crosshair")
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry(f"{sw}x{sh}+0+0")
        try:
            t.attributes("-fullscreen", True)
        except tk.TclError:
            t.overrideredirect(True)
        try:
            t.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.scale = shot.width / sw if abs(shot.width - sw) > 1 else 1.0
        disp = shot if self.scale == 1.0 else shot.resize((round(shot.width / self.scale), round(shot.height / self.scale)), BILINEAR)
        self.disp_size = disp.size
        dim = Image.blend(disp, Image.new("RGB", disp.size, (0, 0, 0)), 0.45)
        self.bright = ImageTk.PhotoImage(disp, master=t)
        self.dim = ImageTk.PhotoImage(dim, master=t)
        self.region = tk.PhotoImage(master=t)

        c = self.c = tk.Canvas(t, highlightthickness=0, bd=0, bg="black", cursor="crosshair")
        c.pack(fill="both", expand=True)
        self.bg_id = c.create_image(0, 0, image=self.dim, anchor="nw")
        self.reg_id = c.create_image(0, 0, image=self.region, anchor="nw", state="hidden")
        self.out_id = c.create_rectangle(0, 0, 0, 0, outline="white", width=1, state="hidden")
        self.lbl_bg = c.create_rectangle(0, 0, 0, 0, fill="#202020", outline="", state="hidden")
        self.lbl_id = c.create_text(0, 0, text="", fill="white", anchor="nw", state="hidden")
        self.free_id = c.create_line(0, 0, 0, 0, fill="white", width=2, state="hidden")
        # full-screen crosshair guide lines that follow the pointer
        self.guides = app.settings.get("crosshair_guides", True)
        self.guide_ids = [c.create_line(0, 0, 0, 0, fill="black", width=1, state="hidden"),
                          c.create_line(0, 0, 0, 0, fill="black", width=1, state="hidden"),
                          c.create_line(0, 0, 0, 0, fill="white", width=1, dash=(4, 4), state="hidden"),
                          c.create_line(0, 0, 0, 0, fill="white", width=1, dash=(4, 4), state="hidden")]

        bar = tk.Frame(c, bg="#2b2b2b", padx=4, pady=4)
        self.mode_btns = {}
        modes = [("rectangle", "Rectangle"), ("full", "Full screen")] if record else MODES
        for key, label in modes:
            b = tk.Button(bar, text=label, relief="flat", bd=0, padx=12, pady=6, fg="white", bg="#2b2b2b", cursor="hand2",
                          activebackground="#454545", activeforeground="white", highlightthickness=0,
                          command=lambda k=key: self.set_mode(k))
            b.pack(side="left", padx=2)
            self.mode_btns[key] = b
        tk.Frame(bar, width=1, bg="#555555").pack(side="left", fill="y", padx=6)
        tk.Button(bar, text="✕  Cancel", relief="flat", bd=0, padx=12, pady=6, fg="white", bg="#2b2b2b", cursor="hand2",
                  activebackground="#c42b1c", activeforeground="white", highlightthickness=0,
                  command=self.cancel).pack(side="left", padx=2)
        c.create_window(sw // 2, 14, window=bar, anchor="n")
        self.hint_id = c.create_text(sw // 2, 74, text="", fill="white", font=("TkDefaultFont", 11), anchor="n")

        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", lambda e: self._hide_guides())
        c.bind("<Enter>", self._guides)
        c.bind("<Button-3>", lambda e: self.cancel())
        t.bind("<Escape>", lambda e: self.cancel())
        t.protocol("WM_DELETE_WINDOW", self.cancel)
        self.set_mode(self.mode, initial=True)
        t.after(60, self._settle)
        t.after(400, self._settle)

    # coordinate helpers: canvas -> shot pixels
    def to_shot(self, x, y):
        return (x + self.ox) * self.scale, (y + self.oy) * self.scale

    def _settle(self):
        if self.finished:
            return
        try:
            self.top.lift()
            self.top.focus_force()
            self.c.focus_set()
            self.ox, self.oy = self.top.winfo_rootx(), self.top.winfo_rooty()
            self.c.coords(self.bg_id, -self.ox, -self.oy)
            # make sure the pointer shows the crosshair straight away, without moving the mouse
            self.top.configure(cursor="crosshair")
            self.c.configure(cursor="crosshair")
            px, py = self.top.winfo_pointerxy()
            if px >= 0:
                self._guides(type("E", (), {"x": px - self.ox, "y": py - self.oy})())
        except tk.TclError:
            pass

    def _guides(self, e):
        if not self.guides:
            return
        W, H = self.top.winfo_width(), self.top.winfo_height()
        for i, gid in enumerate(self.guide_ids):
            if i % 2 == 0:
                self.c.coords(gid, 0, e.y, W, e.y)
            else:
                self.c.coords(gid, e.x, 0, e.x, H)
            self.c.itemconfigure(gid, state="normal")
            self.c.tag_raise(gid)

    def _hide_guides(self):
        for gid in self.guide_ids:
            self.c.itemconfigure(gid, state="hidden")

    def set_mode(self, mode, initial=False):
        if mode == "full":
            if self.record:
                self._finish((0, 0, self.shot.width, self.shot.height, self.scale))
            else:
                self._finish(self.shot)
            return
        self.mode = mode
        if not self.record and not initial:
            self.app.settings["mode"] = mode
            save_settings(self.app.settings)
        for k, b in self.mode_btns.items():
            b.configure(bg=ACCENT if k == mode else "#2b2b2b")
        self._clear_sel()
        hint = {"rectangle": "Drag to select an area", "freeform": "Draw around the area you want",
                "window": "Click a window to snip it"}[mode]
        if self.record:
            hint = "Drag to select the area to record"
        if mode == "window":
            if self.wins is None:
                excl = []
                try:
                    excl = [int(self.top.wm_frame(), 16), self.top.winfo_id()]
                except (tk.TclError, ValueError):
                    pass
                self.wins = x11_windows(excl) or []
            if not self.wins:
                hint = "Window snips need the X11 desktop — drag around the window instead"
        self.c.itemconfigure(self.hint_id, text=hint + "   ·   Esc to cancel")

    def _clear_sel(self):
        self.sel = None
        for i in (self.reg_id, self.out_id, self.lbl_bg, self.lbl_id, self.free_id):
            self.c.itemconfigure(i, state="hidden")

    def _show_sel(self, x1, y1, x2, y2):
        W, H = self.disp_size
        dx1, dy1 = max(0, int(x1 + self.ox)), max(0, int(y1 + self.oy))
        dx2, dy2 = min(W, int(x2 + self.ox)), min(H, int(y2 + self.oy))
        if dx2 - dx1 < 1 or dy2 - dy1 < 1:
            self._clear_sel()
            return
        self.sel = (x1, y1, x2, y2)
        self.region.blank()
        self.region.tk.call(self.region, "copy", self.bright, "-from", dx1, dy1, dx2, dy2, "-to", 0, 0, "-shrink")
        self.c.coords(self.reg_id, dx1 - self.ox, dy1 - self.oy)
        self.c.coords(self.out_id, x1, y1, x2, y2)
        w, h = round((x2 - x1) * self.scale), round((y2 - y1) * self.scale)
        self.c.itemconfigure(self.lbl_id, text=f" {w} × {h} ")
        ly = y2 + 6 if y2 + 28 < self.top.winfo_height() else y1 - 26
        self.c.coords(self.lbl_id, x1, ly)
        bb = self.c.bbox(self.lbl_id)
        if bb:
            self.c.coords(self.lbl_bg, bb[0] - 2, bb[1] - 2, bb[2] + 2, bb[3] + 2)
        for i in (self.reg_id, self.out_id, self.lbl_bg, self.lbl_id):
            self.c.itemconfigure(i, state="normal")
        self.c.tag_raise(self.lbl_bg)
        self.c.tag_raise(self.lbl_id)

    def _win_at(self, x, y):
        sx, sy = self.to_shot(x, y)
        for (a, b, c, d) in reversed(self.wins or []):
            if a <= sx < c and b <= sy < d:
                return (a, b, c, d)
        return None

    def _rect_like(self):
        return self.mode == "rectangle" or (self.mode == "window" and not self.wins)

    def _press(self, e):
        if self._rect_like():
            self.start = (e.x, e.y)
            self._clear_sel()
        elif self.mode == "freeform":
            self.pts = [(e.x, e.y)]
            self.c.coords(self.free_id, e.x, e.y, e.x, e.y)
            self.c.itemconfigure(self.free_id, state="normal")

    def _drag(self, e):
        self._guides(e)
        if self._rect_like() and self.start:
            x0, y0 = self.start
            self._show_sel(min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y))
        elif self.mode == "freeform" and self.pts:
            lx, ly = self.pts[-1]
            if abs(e.x - lx) + abs(e.y - ly) >= 2:
                self.pts.append((e.x, e.y))
                self.c.coords(self.free_id, *[v for p in self.pts + [self.pts[0]] for v in p]) if len(self.pts) > 2 else \
                    self.c.coords(self.free_id, *[v for p in self.pts for v in p])

    def _motion(self, e):
        self._guides(e)
        if self.mode == "window" and self.wins:
            r = self._win_at(e.x, e.y)
            if r:
                a, b, c, d = r
                self._show_sel(a / self.scale - self.ox, b / self.scale - self.oy, c / self.scale - self.ox, d / self.scale - self.oy)
            else:
                self._clear_sel()

    def _release(self, e):
        if self._rect_like() and self.start:
            self.start = None
            if self.sel:
                x1, y1, x2, y2 = self.sel
                if x2 - x1 >= 4 and y2 - y1 >= 4:
                    self._finish_rect(x1, y1, x2, y2)
                    return
            self._clear_sel()
        elif self.mode == "freeform" and self.pts:
            pts, self.pts = self.pts, []
            if len(pts) >= 3:
                sp = [self.to_shot(x, y) for x, y in pts]
                mask = Image.new("L", self.shot.size, 0)
                ImageDraw.Draw(mask).polygon(sp, fill=255)
                bbox = mask.getbbox()
                if bbox and bbox[2] - bbox[0] > 3 and bbox[3] - bbox[1] > 3:
                    img = self.shot.convert("RGBA").crop(bbox)
                    img.putalpha(mask.crop(bbox))
                    self._finish(img)
                    return
            self._clear_sel()
        elif self.mode == "window" and self.wins:
            r = self._win_at(e.x, e.y)
            if r:
                a, b, c, d = r
                a, b = max(0, a), max(0, b)
                c, d = min(self.shot.width, c), min(self.shot.height, d)
                if c - a > 1 and d - b > 1:
                    self._finish(self.shot.crop((a, b, c, d)))

    def _finish_rect(self, x1, y1, x2, y2):
        a, b = self.to_shot(x1, y1)
        c, d = self.to_shot(x2, y2)
        a, b = max(0, round(a)), max(0, round(b))
        c, d = min(self.shot.width, round(c)), min(self.shot.height, round(d))
        if self.record:
            self._finish((a, b, c, d, self.scale))
        else:
            self._finish(self.shot.crop((a, b, c, d)))

    def _close(self):
        self.finished = True
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()

    def _finish(self, result):
        if self.finished:
            return
        self._close()
        self.app.root.after(30, lambda: self.on_done(result))

    def cancel(self):
        if self.finished:
            return
        self._close()
        self.app.root.after(30, self.on_cancel)


# ================================================================ recorder
class Recorder:
    def __init__(self, app, rect, on_finish):
        self.app, self.on_finish = app, on_finish
        self.proc = None
        self.state = "ready"
        x1, y1, x2, y2, scale = rect
        self.scale = scale
        w, h = (x2 - x1) // 2 * 2, (y2 - y1) // 2 * 2
        self.rect = (int(x1), int(y1), int(w), int(h))
        self.path = None
        self.windows = []
        self.cmd = self._command()
        if self.cmd is None:
            need = "wf-recorder" if is_wayland() else "ffmpeg"
            messagebox.showerror(APP_NAME, f"Screen recording needs {need}.\n\nInstall it with:\n  sudo apt install {need}")
            self.on_finish(None)
            return
        if w < 16 or h < 16:
            self.on_finish(None)
            return
        app.recorder = self
        self._build()

    def _command(self):
        x, y, w, h = self.rect
        fps = str(int(self.app.settings.get("record_fps", 24)))
        self.path = unique_path(self.app.settings["video_dir"], f"Screen Recording {stamp()}", ".mp4")
        if is_wayland() and have("wf-recorder"):
            s = self.scale
            g = f"{round(x / s)},{round(y / s)} {round(w / s)}x{round(h / s)}"
            return ["wf-recorder", "-g", g, "-f", self.path, "-c", "libx264", "-p", "preset=ultrafast", "-p", "crf=26"]
        if not is_wayland() and have("ffmpeg") and os.environ.get("DISPLAY"):
            return ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", fps, "-video_size", f"{w}x{h}",
                    "-i", f"{os.environ['DISPLAY']}+{x},{y}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-pix_fmt", "yuv420p", self.path]
        return None

    def _build(self):
        root = self.app.root
        s = self.scale
        x, y, w, h = [v / s for v in self.rect]
        b = 3
        for gx, gy, gw, gh in ((x - b, y - b, w + 2 * b, b), (x - b, y + h, w + 2 * b, b), (x - b, y, b, h), (x + w, y, b, h)):
            t = tk.Toplevel(root, bg="#e81123")
            t.overrideredirect(True)
            try:
                t.attributes("-topmost", True)
            except tk.TclError:
                pass
            t.geometry(f"{max(1, int(gw))}x{max(1, int(gh))}+{int(gx)}+{int(gy)}")
            self.windows.append(t)
        c = self.ctrl = tk.Toplevel(root, bg="#262626")
        c.overrideredirect(True)
        try:
            c.attributes("-topmost", True)
        except tk.TclError:
            pass
        f = tk.Frame(c, bg="#262626", padx=8, pady=6, highlightbackground="#5a5a5a", highlightthickness=1)
        f.pack()
        self.dot = tk.Label(f, text="●", fg="#777777", bg="#262626", font=("TkDefaultFont", 12))
        self.dot.pack(side="left")
        self.time_lbl = tk.Label(f, text="00:00", fg="white", bg="#262626", font=("TkDefaultFont", 11), width=6)
        self.time_lbl.pack(side="left", padx=(2, 8))
        btn = dict(relief="flat", bd=0, padx=12, pady=4, fg="white", highlightthickness=0, activeforeground="white")
        self.go_btn = tk.Button(f, text="Start", bg=ACCENT, activebackground="#1a86d9", command=self.start, **btn)
        self.go_btn.pack(side="left", padx=2)
        tk.Button(f, text="Discard", bg="#3a3a3a", activebackground="#4a4a4a", command=self.discard, **btn).pack(side="left", padx=2)
        c.update_idletasks()
        cw, ch = c.winfo_reqwidth(), c.winfo_reqheight()
        sw, sh = c.winfo_screenwidth(), c.winfo_screenheight()
        cx = int(min(max(0, x + w / 2 - cw / 2), sw - cw))
        if y - ch - 12 >= 0:
            cy = int(y - ch - 12)
        elif y + h + ch + 12 <= sh:
            cy = int(y + h + 12)
        else:
            cx, cy = sw - cw - 10, 10
        c.geometry(f"+{cx}+{cy}")
        self.windows.append(c)

    def start(self):
        if self.state != "ready":
            return
        self.state = "countdown"
        self.go_btn.configure(state="disabled")
        self._count(3)

    def _count(self, n):
        if self.state != "countdown":
            return
        if n > 0:
            self.time_lbl.configure(text=f"  {n}…")
            self.ctrl.after(1000, lambda: self._count(n - 1))
            return
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            self.log = open(os.path.join(CONFIG_DIR, "record.log"), "w")
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not start recording:\n{e}")
            self._teardown(None)
            return
        self.state = "recording"
        self.t0 = time.time()
        self.go_btn.configure(text="Stop", bg="#c42b1c", activebackground="#d83b2b", state="normal", command=self.stop)
        self._tick()

    def _tick(self):
        if self.state != "recording":
            return
        if self.proc.poll() is not None:
            self._failed()
            return
        el = int(time.time() - self.t0)
        self.time_lbl.configure(text=f"{el // 60:02d}:{el % 60:02d}")
        self.dot.configure(fg="#e81123" if el % 2 == 0 else "#7a1a1a")
        self.ctrl.after(500, self._tick)

    def _failed(self):
        self.state = "done"
        tail = ""
        try:
            self.log.close()
            with open(os.path.join(CONFIG_DIR, "record.log")) as f:
                tail = f.read()[-600:]
        except Exception:
            pass
        messagebox.showerror(APP_NAME, "The recorder stopped unexpectedly.\n\n" + tail)
        self._teardown(None)

    def stop(self):
        if self.state != "recording":
            return
        self.state = "stopping"
        self.go_btn.configure(text="Saving…", state="disabled")
        try:
            self.proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        self._wait(time.time() + 15)

    def _wait(self, deadline):
        if self.proc.poll() is None and time.time() < deadline:
            self.ctrl.after(200, lambda: self._wait(deadline))
            return
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.log.close()
        except Exception:
            pass
        ok = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        self._teardown(self.path if ok else None)

    def discard(self):
        if self.state == "recording" and self.proc:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
            try:
                os.unlink(self.path)
            except OSError:
                pass
        self.state = "done"
        self._teardown(None)

    def _teardown(self, path):
        for w in self.windows:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self.windows = []
        self.app.recorder = None
        self.on_finish(path)


# =================================================================== editor
class Editor:
    TOOLS = [("pen", "Pen"), ("hl", "Highlighter"), ("eraser", "Eraser"), (None, None),
             ("line", "Line"), ("arrow", "Arrow"), ("rect", "Rectangle"), ("ellipse", "Ellipse"), ("text", "Text"),
             (None, None), ("crop", "Crop")]

    def __init__(self, app, img, path=None, dirty=None):
        self.app, self.s = app, app.settings
        self.zoom = 1.0
        self.base = None
        self.anns = []
        self.undo_stack, self.redo_stack = [], []
        self.live = None
        self.cur_pts = []
        self.text_edit = None
        self.crop_rect = None
        self.erase_snapshot = None
        self.photo = None
        app.editors.append(self)
        w = self.win = tk.Toplevel(app.root)
        w.minsize(900, 420)
        w.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self.load(img, path, dirty, first=True)

    # ---------- UI
    def _build_ui(self):
        w, s = self.win, self.s
        bar1 = ttk.Frame(w, padding=(8, 6, 8, 2))
        bar1.pack(fill="x")
        more = ttk.Menubutton(bar1, text="⋯", width=3)
        m = tk.Menu(more, tearoff=False)
        m.add_command(label="Open file…            Ctrl+O", command=self.open_file)
        m.add_command(label="Open Screenshots folder", command=self.open_folder)
        m.add_command(label="Print…                   Ctrl+P", command=self.print_img)
        m.add_separator()
        m.add_command(label="Erase all ink", command=self.erase_all)
        m.add_command(label="Revert to original", command=self.revert)
        m.add_separator()
        m.add_command(label="Record screen…", command=self.app.start_record)
        m.add_command(label="Settings", command=lambda: self.app.open_settings(self.win))
        more["menu"] = m
        more.pack(side="right")
        ttk.Button(bar1, text="Save as", command=self.save_as).pack(side="right", padx=2)
        ttk.Button(bar1, text="Copy", command=self.copy).pack(side="right", padx=2)
        self.ocr_btn = ttk.Button(bar1, text="Text actions", command=self.text_actions)
        self.ocr_btn.pack(side="right", padx=2)

        ttk.Button(bar1, text="+ New", style="Accent.TButton", command=self.new_snip).pack(side="left")
        self.mode_cb, self.delay_cb = self.app.capture_controls(bar1)
        ttk.Separator(bar1, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar1, text="↶ Undo", width=7, command=self.undo).pack(side="left")
        ttk.Button(bar1, text="↷ Redo", width=7, command=self.redo).pack(side="left", padx=(2, 0))

        bar2 = ttk.Frame(w, padding=(8, 2, 8, 6))
        bar2.pack(fill="x")
        self.tool = tk.StringVar(value="pen")
        for key, label in self.TOOLS:
            if key is None:
                ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y", padx=6)
                continue
            ttk.Radiobutton(bar2, text=label, value=key, variable=self.tool, style="Toolbutton",
                            command=self._tool_changed).pack(side="left", padx=1)
        ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y", padx=6)
        self.swatches = []
        for col in PALETTE:
            sw = tk.Label(bar2, bg=col, width=2, relief="flat", bd=0, highlightthickness=2, highlightbackground="#d0d0d0", cursor="hand2")
            sw.pack(side="left", padx=1, ipady=1)
            sw.bind("<Button-1>", lambda e, c=col: self.set_color(c))
            self.swatches.append((col, sw))
        self.custom_sw = tk.Label(bar2, text="+", width=2, relief="flat", bd=0, highlightthickness=2, highlightbackground="#d0d0d0", cursor="hand2")
        self.custom_sw.pack(side="left", padx=1, ipady=1)
        self.custom_sw.bind("<Button-1>", lambda e: self.pick_color())
        ttk.Label(bar2, text="  Size").pack(side="left")
        self.size_var = tk.DoubleVar(value=s["pen_size"])
        self.size_scale = ttk.Scale(bar2, from_=1, to=60, variable=self.size_var, length=110, command=self._size_changed)
        self.size_scale.pack(side="left", padx=4)
        self.size_lbl = ttk.Label(bar2, text="4", width=3)
        self.size_lbl.pack(side="left")

        self.crop_bar = ttk.Frame(w, padding=(8, 4))
        ttk.Label(self.crop_bar, text="Drag over the image to choose the area to keep.").pack(side="left")
        ttk.Button(self.crop_bar, text="Cancel", command=self.cancel_crop_tool).pack(side="right", padx=2)
        ttk.Button(self.crop_bar, text="✓ Apply crop", style="Accent.TButton", command=self.apply_crop).pack(side="right", padx=2)

        sbar = ttk.Frame(w, style="Status.TFrame")
        sbar.pack(side="bottom", fill="x")
        # zoom controls live in the status bar (bottom-right), so the toolbar never runs out of room
        zf = ttk.Frame(sbar, style="Status.TFrame")
        zf.pack(side="right", padx=6)
        zb = dict(style="Zoom.TButton", width=3)
        ttk.Button(zf, text="-", command=lambda: self.set_zoom(self.zoom / 1.25), **zb).pack(side="left")
        self.zoom_lbl = ttk.Label(zf, text="100%", width=6, anchor="center", cursor="hand2", style="Status.TLabel")
        self.zoom_lbl.pack(side="left")
        self.zoom_lbl.bind("<Button-1>", lambda e: self.set_zoom(1.0))
        ttk.Button(zf, text="+", command=lambda: self.set_zoom(self.zoom * 1.25), **zb).pack(side="left")
        ttk.Button(zf, text="Fit", style="Zoom.TButton", width=4, command=self.fit_zoom).pack(side="left", padx=(4, 0))
        self.status = ttk.Label(sbar, text="", padding=(8, 2), anchor="w", style="Status.TLabel")
        self.status.pack(side="left", fill="x", expand=True)

        area = ttk.Frame(w)
        area.pack(fill="both", expand=True)
        self.cv = tk.Canvas(area, bg="#d6d6d6", highlightthickness=0, cursor="crosshair")
        vs = ttk.Scrollbar(area, orient="vertical", command=self.cv.yview)
        hs = ttk.Scrollbar(area, orient="horizontal", command=self.cv.xview)
        self.cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.cv.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.img_item = self.cv.create_image(0, 0, anchor="nw")

        cv = self.cv
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_release)
        cv.bind("<Button-4>", lambda e: cv.yview_scroll(-3, "units"))
        cv.bind("<Button-5>", lambda e: cv.yview_scroll(3, "units"))
        cv.bind("<Shift-Button-4>", lambda e: cv.xview_scroll(-3, "units"))
        cv.bind("<Shift-Button-5>", lambda e: cv.xview_scroll(3, "units"))
        cv.bind("<Control-Button-4>", lambda e: self.set_zoom(self.zoom * 1.1))
        cv.bind("<Control-Button-5>", lambda e: self.set_zoom(self.zoom / 1.1))
        cv.bind("<MouseWheel>", lambda e: (self.set_zoom(self.zoom * (1.1 if e.delta > 0 else 1 / 1.1)) if e.state & 4
                                          else cv.yview_scroll(-1 if e.delta > 0 else 1, "units")))

        keys = {
            "<Control-z>": self.undo, "<Control-y>": self.redo, "<Control-Z>": self.redo,
            "<Control-s>": self.save_as, "<Control-n>": self.new_snip, "<Control-o>": self.open_file,
            "<Control-p>": self.print_img, "<Control-plus>": lambda: self.set_zoom(self.zoom * 1.25),
            "<Control-equal>": lambda: self.set_zoom(self.zoom * 1.25), "<Control-minus>": lambda: self.set_zoom(self.zoom / 1.25),
            "<Control-0>": lambda: self.set_zoom(1.0), "<Control-w>": self.close,
        }
        for k, fn in keys.items():
            w.bind(k, lambda e, fn=fn: None if self._typing(e) else (fn(), "break")[1])
        w.bind("<Control-c>", lambda e: None if self._typing(e) else (self.copy(), "break")[1])
        w.bind("<Escape>", lambda e: self._escape())
        w.bind("<Return>", lambda e: self.apply_crop() if self.tool.get() == "crop" and not self._typing(e) else None)
        self._tool_changed()

    def _typing(self, e):
        return isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text))

    def _escape(self):
        if self.text_edit:
            self.finish_text(commit=False)
        elif self.tool.get() == "crop":
            self.cancel_crop_tool()

    # ---------- state
    def load(self, img, path=None, dirty=None, first=False):
        self.finish_text(commit=False)
        self._clear_crop()
        self.base = img.convert("RGBA")
        self.original = self.base
        self.anns, self.undo_stack, self.redo_stack = [], [], []
        self.path = path
        self.dirty = (path is None) if dirty is None else dirty
        self.recompose(refresh=False)
        W, H = self.base.size
        if first:
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            z = min(1.0, (sw * 0.85 - 30) / W, (sh * 0.82 - 150) / H)
            self.zoom = max(0.1, z)
            ww = max(1020, min(int(sw * 0.9), int(W * self.zoom) + 40))
            wh = max(460, min(int(sh * 0.9), int(H * self.zoom) + 150))
            self.win.geometry(f"{ww}x{wh}")
            self.refresh()
        else:
            self.win.after(50, self.fit_zoom)
        self._update_title()
        self.win.deiconify()
        self.win.lift()

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled snip"
        self.win.title(f"{'● ' if self.dirty else ''}{name} — {APP_NAME}")
        W, H = self.base.size
        where = f"Saved: {self.path}" if self.path and not self.dirty else ("Unsaved changes" if self.dirty else "")
        self.status.configure(text=f"{W} × {H} px     {where}")

    def mark_dirty(self):
        self.dirty = True
        self._update_title()

    def push_undo(self):
        self.undo_stack.append((self.base, list(self.anns)))
        del self.undo_stack[:-60]
        self.redo_stack.clear()

    def undo(self):
        self.finish_text()
        if not self.undo_stack:
            return
        self.redo_stack.append((self.base, list(self.anns)))
        self.base, self.anns = self.undo_stack.pop()
        self.recompose()
        self.mark_dirty()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append((self.base, list(self.anns)))
        self.base, self.anns = self.redo_stack.pop()
        self.recompose()
        self.mark_dirty()

    def recompose(self, refresh=True):
        self.comp = self.base.copy()
        for a in self.anns:
            render_ann(self.comp, a)
        self.transparent = has_transparency(self.base)
        if refresh:
            self.refresh()

    def commit(self, ann):
        self.push_undo()
        self.anns.append(ann)
        render_ann(self.comp, ann)
        self.mark_dirty()
        self.refresh()

    def result(self):
        self.finish_text()
        return self.comp

    # ---------- view
    def refresh(self):
        W, H = self.comp.size
        z = self.zoom
        disp = self.comp
        if abs(z - 1.0) > 1e-3:
            disp = disp.resize((max(1, round(W * z)), max(1, round(H * z))), BILINEAR if z < 1 else Image.NEAREST)
        if self.transparent:
            bg = Image.new("RGBA", disp.size, (255, 255, 255, 255))
            bg.alpha_composite(disp)
            disp = bg
        self.photo = ImageTk.PhotoImage(disp, master=self.win)
        self.cv.itemconfigure(self.img_item, image=self.photo)
        self.cv.configure(scrollregion=(0, 0, disp.width, disp.height))
        self.zoom_lbl.configure(text=f"{round(z * 100)}%")
        if self.crop_rect:
            self._draw_crop()

    def set_zoom(self, z):
        self.finish_text()
        self.zoom = max(0.1, min(8.0, z))
        self.refresh()

    def fit_zoom(self):
        self.win.update_idletasks()
        cw, ch = max(100, self.cv.winfo_width() - 4), max(100, self.cv.winfo_height() - 4)
        W, H = self.comp.size
        self.set_zoom(min(1.0, cw / W, ch / H))

    def to_img(self, e):
        return self.cv.canvasx(e.x) / self.zoom, self.cv.canvasy(e.y) / self.zoom

    # ---------- tools
    def _tool_color(self, t=None):
        return self.s["hl_color"] if (t or self.tool.get()) == "hl" else self.s["pen_color"]

    def _size_key(self, t=None):
        t = t or self.tool.get()
        return {"hl": "hl_size", "text": "text_size"}.get(t, "pen_size")

    def _tool_changed(self):
        self.finish_text()
        t = self.tool.get()
        if t == "crop":
            self.crop_bar.pack(side="bottom", fill="x", after=self.status.master)
        else:
            self._clear_crop()
            self.crop_bar.pack_forget()
        self.cv.configure(cursor={"eraser": "dotbox", "text": "xterm", "crop": "tcross"}.get(t, "crosshair"))
        self.size_var.set(self.s[self._size_key()])
        self.size_lbl.configure(text=str(int(self.size_var.get())))
        self._show_color()

    def _show_color(self):
        cur = self._tool_color().lower()
        for col, sw in self.swatches:
            sw.configure(highlightbackground="#202020" if col == cur else "#d0d0d0")
        custom = cur not in PALETTE
        self.custom_sw.configure(bg=cur if custom else self.win.cget("bg"),
                                 highlightbackground="#202020" if custom else "#d0d0d0")

    def set_color(self, col):
        key = "hl_color" if self.tool.get() == "hl" else "pen_color"
        if self.tool.get() in ("eraser", "crop"):
            self.tool.set("pen")
            self._tool_changed()
        self.s[key] = col.lower()
        save_settings(self.s)
        self._show_color()
        if self.text_edit:
            self.text_edit["color"] = col
            self.text_edit["entry"].configure(fg=col, insertbackground=col)

    def pick_color(self):
        c = colorchooser.askcolor(color=self._tool_color(), parent=self.win, title="Custom colour")
        if c and c[1]:
            self.set_color(c[1])

    def _size_changed(self, _=None):
        v = int(round(self.size_var.get()))
        self.size_lbl.configure(text=str(v))
        self.s[self._size_key()] = v
        save_settings(self.s)

    # ---------- mouse
    def on_press(self, e):
        t = self.tool.get()
        x, y = self.to_img(e)
        cx, cy = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        z = self.zoom
        if t != "text":
            self.finish_text()
        col, size = self._tool_color(), self.s[self._size_key()]
        if t in ("pen", "hl"):
            self.cur_pts = [(x, y)]
            opts = dict(fill=col, width=max(1, size * z), capstyle="round", joinstyle="round")
            if t == "hl":
                opts.update(stipple="gray50", capstyle="projecting")
            self.live = self.cv.create_line(cx, cy, cx + 0.01, cy + 0.01, **opts)
        elif t in ("line", "arrow", "rect", "ellipse"):
            self.start = (x, y)
            wd = max(1, size * z)
            if t in ("line", "arrow"):
                kw = dict(fill=col, width=wd, capstyle="round")
                if t == "arrow":
                    L = max(10, size * 3.5) * z
                    kw.update(arrow="last", arrowshape=(L, L, L * 0.45))
                self.live = self.cv.create_line(cx, cy, cx, cy, **kw)
            elif t == "rect":
                self.live = self.cv.create_rectangle(cx, cy, cx, cy, outline=col, width=wd)
            else:
                self.live = self.cv.create_oval(cx, cy, cx, cy, outline=col, width=wd)
        elif t == "eraser":
            self.erase_snapshot = (self.base, list(self.anns))
            self._erase_at(x, y)
        elif t == "text":
            if self.text_edit:
                self.finish_text()
                return
            self.start_text(x, y)
        elif t == "crop":
            self.start = (x, y)
            self.crop_rect = None

    def on_drag(self, e):
        t = self.tool.get()
        x, y = self.to_img(e)
        z = self.zoom
        if t in ("pen", "hl") and self.live:
            lx, ly = self.cur_pts[-1]
            if math.hypot(x - lx, y - ly) * z >= 1.5:
                self.cur_pts.append((x, y))
                self.cv.coords(self.live, *[v * z for p in self.cur_pts for v in p])
        elif t in ("line", "arrow", "rect", "ellipse") and self.live:
            x0, y0 = self.start
            self.cv.coords(self.live, x0 * z, y0 * z, x * z, y * z)
        elif t == "eraser":
            self._erase_at(x, y)
        elif t == "crop" and getattr(self, "start", None):
            W, H = self.base.size
            x0, y0 = self.start
            x, y = max(0, min(W, x)), max(0, min(H, y))
            self.crop_rect = (min(x0, x), min(y0, y), max(x0, x), max(y0, y))
            self._draw_crop()

    def on_release(self, e):
        t = self.tool.get()
        x, y = self.to_img(e)
        if t in ("pen", "hl") and self.live:
            self.cv.delete(self.live)
            self.live = None
            pts = self.cur_pts
            self.cur_pts = []
            self.commit({"type": t, "pts": pts, "color": self._tool_color(t), "width": self.s[self._size_key(t)]})
        elif t in ("line", "arrow", "rect", "ellipse") and self.live:
            self.cv.delete(self.live)
            self.live = None
            x0, y0 = self.start
            if math.hypot(x - x0, y - y0) * self.zoom >= 3:
                self.commit({"type": t, "p1": (x0, y0), "p2": (x, y), "color": self.s["pen_color"], "width": self.s["pen_size"]})
        elif t == "eraser" and self.erase_snapshot:
            snap, self.erase_snapshot = self.erase_snapshot, None
            if snap[1] != self.anns:
                self.undo_stack.append(snap)
                self.redo_stack.clear()
                self.mark_dirty()
        elif t == "crop":
            self.start = None

    def _erase_at(self, x, y):
        tol = 6 / self.zoom
        for i in range(len(self.anns) - 1, -1, -1):
            if ann_hit(self.anns[i], x, y, tol):
                self.anns = self.anns[:i] + self.anns[i + 1:]
                self.recompose()
                return

    def erase_all(self):
        if self.anns:
            self.push_undo()
            self.anns = []
            self.recompose()
            self.mark_dirty()

    def revert(self):
        if self.base is not self.original or self.anns:
            self.push_undo()
            self.base, self.anns = self.original, []
            self.recompose()
            self.mark_dirty()

    # ---------- text
    def start_text(self, x, y):
        z = self.zoom
        size = self.s["text_size"]
        col = self.s["pen_color"]
        ent = tk.Entry(self.cv, font=("DejaVu Sans", -max(8, int(size * z))), fg=col, insertbackground=col,
                       bg="#3a3a3a" if is_light(col) else "#ffffff", relief="solid", bd=1, width=18)
        item = self.cv.create_window(x * z - 2, y * z - 2, window=ent, anchor="nw")
        self.text_edit = {"entry": ent, "item": item, "pos": (x, y), "color": col, "size": size}
        ent.focus_set()
        ent.bind("<Return>", lambda e: (self.finish_text(), "break")[1])
        ent.bind("<Escape>", lambda e: (self.finish_text(commit=False), "break")[1])
        ent.bind("<KeyRelease>", lambda e: ent.configure(width=max(18, len(ent.get()) + 2)))

    def finish_text(self, commit=True):
        te = self.text_edit
        if not te:
            return
        self.text_edit = None
        txt = te["entry"].get()
        self.cv.delete(te["item"])
        te["entry"].destroy()
        if commit and txt.strip():
            self.commit({"type": "text", "pos": te["pos"], "text": txt, "color": te["color"], "size": te["size"]})

    # ---------- crop
    def _draw_crop(self):
        self.cv.delete("crop")
        if not self.crop_rect:
            return
        z = self.zoom
        x0, y0, x1, y1 = [v * z for v in self.crop_rect]
        W, H = self.base.width * z, self.base.height * z
        for r in ((0, 0, W, y0), (0, y1, W, H), (0, y0, x0, y1), (x1, y0, W, y1)):
            self.cv.create_rectangle(*r, fill="black", stipple="gray50", outline="", tags="crop")
        self.cv.create_rectangle(x0, y0, x1, y1, outline="white", width=2, dash=(6, 4), tags="crop")
        self.cv.create_rectangle(x0, y0, x1, y1, outline="black", width=1, tags="crop")

    def _clear_crop(self):
        self.crop_rect = None
        if hasattr(self, "cv"):
            self.cv.delete("crop")

    def cancel_crop_tool(self):
        self._clear_crop()
        self.tool.set("pen")
        self._tool_changed()

    def apply_crop(self):
        if not self.crop_rect:
            return
        x0, y0, x1, y1 = [int(round(v)) for v in self.crop_rect]
        W, H = self.base.size
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        self.push_undo()
        self.base = self.base.crop((x0, y0, x1, y1))
        self.anns = [shift_ann(a, -x0, -y0) for a in self.anns]
        self.cancel_crop_tool()
        self.recompose()
        self.mark_dirty()
        self.fit_zoom()

    # ---------- actions
    def copy(self):
        ok = clipboard_image(self.result())
        self.status.configure(text="Copied to clipboard" if ok else "Clipboard tool missing — install wl-clipboard / xclip")
        self.win.after(2500, self._update_title)

    def save_as(self):
        img = self.result()
        fmt = self.s.get("format", "png")
        initdir = os.path.dirname(self.path) if self.path else self.s["save_dir"]
        os.makedirs(initdir, exist_ok=True)
        initfile = os.path.basename(self.path) if self.path else f"Screenshot {stamp()}.{fmt}"
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Save As", initialdir=initdir, initialfile=initfile, defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg"), ("GIF", "*.gif"), ("All files", "*")])
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += ".png"
        try:
            save_image(img, path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not save:\n{e}", parent=self.win)
            return False
        self.path = path
        self.dirty = False
        self._update_title()
        return True

    def confirm_discard(self):
        self.finish_text()
        if not self.dirty or not self.s.get("ask_save_edits", True):
            return True
        r = messagebox.askyesnocancel(APP_NAME, "Do you want to save changes to this snip?", parent=self.win)
        if r is None:
            return False
        if r:
            return self.save_as()
        return True

    def new_snip(self):
        target = None if self.s.get("multiple_windows") else self
        if target and not self.confirm_discard():
            return
        self.app.start_snip(target=target)

    def open_file(self):
        if not self.s.get("multiple_windows") and not self.confirm_discard():
            return
        path = filedialog.askopenfilename(parent=self.win, title="Open image", initialdir=self.s["save_dir"],
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*")])
        if not path:
            return
        try:
            img = Image.open(path)
            img.load()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not open image:\n{e}", parent=self.win)
            return
        if self.s.get("multiple_windows"):
            Editor(self.app, img, path, dirty=False)
        else:
            self.load(img, path, dirty=False)

    def open_folder(self):
        folder = os.path.dirname(self.path) if self.path else self.s["save_dir"]
        os.makedirs(folder, exist_ok=True)
        open_path(folder)

    def print_img(self):
        if not have("lp"):
            messagebox.showinfo(APP_NAME, "Printing needs CUPS:\n  sudo apt install cups-client\nand a printer set up.", parent=self.win)
            return
        if not messagebox.askokcancel(APP_NAME, "Send this snip to the default printer?", parent=self.win):
            return
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        save_image(flatten(self.result()), tmp)
        r = subprocess.run(["lp", "-o", "fit-to-page", tmp], capture_output=True, text=True)
        messagebox.showinfo(APP_NAME, r.stdout.strip() or r.stderr.strip() or "Sent to printer.", parent=self.win)

    def text_actions(self):
        if not have("tesseract"):
            messagebox.showinfo(APP_NAME, "Text actions use Tesseract OCR.\n\nInstall it with:\n  sudo apt install tesseract-ocr", parent=self.win)
            return
        TextActions(self)

    def close(self):
        if not self.confirm_discard():
            return
        if self in self.app.editors:
            self.app.editors.remove(self)
        self.win.destroy()
        self.app.check_exit()


# ============================================================ text actions
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\(?\d[\d\s().-]{5,}\d")


def ocr_words(img):
    """Run tesseract, return list of words with boxes (image coordinates)."""
    up = 2 if img.width * img.height < 3_000_000 else 1
    src = flatten(img)
    if up > 1:
        src = src.resize((img.width * up, img.height * up), LANCZOS)
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        src.save(tmp)
        out = subprocess.run(["tesseract", tmp, "stdout", "tsv"], capture_output=True, text=True, timeout=300).stdout
    finally:
        os.unlink(tmp)
    words = []
    for line in out.splitlines()[1:]:
        p = line.split("\t")
        if len(p) < 12 or p[0] != "5" or not p[11].strip():
            continue
        l, t, w, h = (int(v) / up for v in p[6:10])
        words.append({"key": (int(p[1]), int(p[2]), int(p[3]), int(p[4])), "text": p[11].strip(), "box": (l, t, l + w, t + h)})
    return words


def words_to_lines(words):
    lines, order = {}, []
    for w in words:
        if w["key"] not in lines:
            lines[w["key"]] = []
            order.append(w["key"])
        lines[w["key"]].append(w)
    return [lines[k] for k in order]


class TextActions:
    def __init__(self, ed):
        self.ed = ed
        ed.finish_text()
        self.img = ed.base.copy()  # read the screenshot itself, not the ink on top of it
        t = self.top = tk.Toplevel(ed.win)
        t.title(f"Text actions — {APP_NAME}")
        t.transient(ed.win)
        t.geometry("560x420")
        f = ttk.Frame(t, padding=10)
        f.pack(fill="both", expand=True)
        self.info = ttk.Label(f, text="Reading text… (this can take a little while on a Pi)")
        self.info.pack(anchor="w")
        self.txt = tk.Text(f, wrap="word", height=14, relief="solid", bd=1, font=("TkDefaultFont", 10))
        self.txt.pack(fill="both", expand=True, pady=6)
        bf = ttk.Frame(f)
        bf.pack(fill="x")
        self.b_copy = ttk.Button(bf, text="Copy all text", style="Accent.TButton", command=self.copy_all, state="disabled")
        self.b_copy.pack(side="left")
        self.b_redact = ttk.Button(bf, text="Quick redact (emails & phone numbers)", command=self.quick_redact, state="disabled")
        self.b_redact.pack(side="left", padx=6)
        self.b_redact_all = ttk.Button(bf, text="Redact all text", command=self.redact_all, state="disabled")
        self.b_redact_all.pack(side="left")
        ttk.Button(bf, text="Close", command=t.destroy).pack(side="right")
        self.words = None
        self.error = None
        threading.Thread(target=self._work, daemon=True).start()
        t.after(200, self._poll)

    def _work(self):
        try:
            self.words = ocr_words(self.img)
        except Exception as e:
            self.error = str(e)
            self.words = []

    def _poll(self):
        if not self.top.winfo_exists():
            return
        if self.words is None:
            self.top.after(200, self._poll)
            return
        if self.error:
            self.info.configure(text=f"OCR failed: {self.error}")
            return
        lines = words_to_lines(self.words)
        text, prev_block = [], None
        for ln in lines:
            blk = ln[0]["key"][:2]
            if prev_block is not None and blk != prev_block:
                text.append("")
            prev_block = blk
            text.append(" ".join(w["text"] for w in ln))
        self.text = "\n".join(text).strip()
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", self.text or "(No text found)")
        self.info.configure(text=f"Found {len(self.words)} words. You can edit the text below before copying.")
        state = "normal" if self.words else "disabled"
        for b in (self.b_copy, self.b_redact, self.b_redact_all):
            b.configure(state=state)

    def copy_all(self):
        clipboard_text(self.txt.get("1.0", "end").strip(), self.top)
        self.info.configure(text="Text copied to clipboard.")

    def _redact(self, boxes):
        if not boxes:
            self.info.configure(text="Nothing to redact.")
            return
        ed = self.ed
        ed.push_undo()
        for (x0, y0, x1, y1) in boxes:
            ed.anns.append({"type": "redact", "box": (x0 - 2, y0 - 2, x1 + 2, y1 + 2)})
        ed.recompose()
        ed.mark_dirty()
        self.info.configure(text=f"Redacted {len(boxes)} item(s). Use Undo in the editor to reverse.")

    def quick_redact(self):
        boxes = []
        for ln in words_to_lines(self.words):
            s, spans, pos = "", [], 0
            for w in ln:
                if s:
                    s += " "
                spans.append((len(s), len(s) + len(w["text"]), w["box"]))
                s += w["text"]
            for rx in (EMAIL_RE, PHONE_RE):
                for m in rx.finditer(s):
                    if rx is PHONE_RE and len(re.sub(r"\D", "", m.group())) < 7:
                        continue
                    hit = [b for a, e, b in spans if a < m.end() and e > m.start()]
                    if hit:
                        boxes.append((min(b[0] for b in hit), min(b[1] for b in hit), max(b[2] for b in hit), max(b[3] for b in hit)))
        self._redact(boxes)

    def redact_all(self):
        self._redact([w["box"] for w in self.words])


# ================================================================ settings
class SettingsDialog:
    def __init__(self, app, parent=None):
        self.app = app
        s = app.settings
        app.dialogs += 1
        t = self.top = tk.Toplevel(parent or app.root)
        t.title(f"Settings — {APP_NAME}")
        t.resizable(False, False)
        t.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(t, padding=14)
        f.pack(fill="both", expand=True)
        self.v = {}
        r = 0

        def section(text):
            nonlocal r
            ttk.Label(f, text=text, font=("TkDefaultFont", 11, "bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(10 if r else 0, 4))
            r += 1

        def check(key, text):
            nonlocal r
            self.v[key] = tk.BooleanVar(value=bool(s[key]))
            ttk.Checkbutton(f, text=text, variable=self.v[key]).grid(row=r, column=0, columnspan=3, sticky="w", pady=1)
            r += 1

        def folder(key, text):
            nonlocal r
            self.v[key] = tk.StringVar(value=s[key])
            ttk.Label(f, text=text).grid(row=r, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.v[key], width=42).grid(row=r, column=1, sticky="we", padx=4)
            ttk.Button(f, text="Browse…", command=lambda: self._browse(key)).grid(row=r, column=2)
            r += 1

        section("Snipping")
        folder("save_dir", "Save screenshots to")
        check("auto_save", "Automatically save screenshots")
        check("auto_copy", "Automatically copy changes to the clipboard")
        self.v["format"] = tk.StringVar(value=s["format"])
        ttk.Label(f, text="Auto-save format").grid(row=r, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.v["format"], values=["png", "jpg"], state="readonly", width=6).grid(row=r, column=1, sticky="w", padx=4)
        r += 1
        check("ask_save_edits", "Ask to save edited screenshots when closing")
        check("open_editor_after_hotkey", "Open the editor straight away after a keyboard-shortcut snip (instead of the preview pop-up)")
        check("multiple_windows", "Multiple windows (each new snip opens in its own window)")
        check("crosshair_guides", "Show crosshair guide lines while snipping")
        check("add_border", "Add border to each screenshot")
        bf = ttk.Frame(f)
        bf.grid(row=r, column=0, columnspan=3, sticky="w", padx=22)
        r += 1
        self.border_color = s["border_color"]
        ttk.Label(bf, text="Colour").pack(side="left")
        self.bc_sw = tk.Label(bf, bg=self.border_color, width=3, cursor="hand2", relief="solid", bd=1)
        self.bc_sw.pack(side="left", padx=4)
        self.bc_sw.bind("<Button-1>", lambda e: self._pick_border())
        ttk.Label(bf, text="   Thickness").pack(side="left")
        self.v["border_width"] = tk.IntVar(value=int(s["border_width"]))
        ttk.Spinbox(bf, from_=1, to=20, textvariable=self.v["border_width"], width=4).pack(side="left", padx=4)

        section("Screen recording")
        folder("video_dir", "Save recordings to")
        self.v["record_fps"] = tk.IntVar(value=int(s["record_fps"]))
        ttk.Label(f, text="Frame rate (X11)").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(f, from_=5, to=60, textvariable=self.v["record_fps"], width=5).grid(row=r, column=1, sticky="w", padx=4)
        r += 1

        section("Keyboard shortcuts")
        ttk.Label(f, text="Print Screen or Raspberry+Shift+S = snip,  Raspberry+Shift+R = record", foreground="#555").grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Button(f, text="Set up shortcuts", command=self._hotkeys).grid(row=r, column=2)
        r += 1
        ttk.Label(f, text="Snipping Tool icon on the desktop and taskbar", foreground="#555").grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Button(f, text="Add icons", command=self._icons).grid(row=r, column=2, pady=(4, 0))
        r += 1

        section("System check")
        ttk.Label(f, text=system_report(), foreground="#555", justify="left").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        bb = ttk.Frame(f)
        bb.grid(row=r, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(bb, text="Cancel", command=self.close).pack(side="right", padx=(6, 0))
        ttk.Button(bb, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        f.columnconfigure(1, weight=1)

    def _browse(self, key):
        d = filedialog.askdirectory(parent=self.top, initialdir=self.v[key].get())
        if d:
            self.v[key].set(d)

    def _pick_border(self):
        c = colorchooser.askcolor(color=self.border_color, parent=self.top)
        if c and c[1]:
            self.border_color = c[1]
            self.bc_sw.configure(bg=c[1])

    def _icons(self):
        messagebox.showinfo(APP_NAME, "\n".join(setup_shortcuts()), parent=self.top)

    def _hotkeys(self):
        messagebox.showinfo(APP_NAME, "\n".join(setup_hotkeys()) + "\n\nIf a shortcut doesn't work yet, log out and back in.", parent=self.top)

    def save(self):
        s = self.app.settings
        for k, var in self.v.items():
            try:
                s[k] = var.get()
            except tk.TclError:
                pass
        s["border_color"] = self.border_color
        save_settings(s)
        self.close()

    def close(self):
        self.app.dialogs -= 1
        self.top.destroy()
        self.app.check_exit()


def system_report():
    wl = is_wayland()
    desk = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "?"
    items = [("grim", wl), ("wl-copy", wl), ("xclip", not wl), ("tesseract", True),
             ("wf-recorder", wl), ("ffmpeg", not wl), ("xwininfo", not wl)]
    parts = [f"{n} {'✓' if have(n) else '✗'}" for n, rel in items if rel]
    return f"Session: {'Wayland' if wl else 'X11'} ({desk})\n" + "   ".join(parts)


# ===================================================================== app
class App:
    def __init__(self, args, shot=None):
        self.args = args
        self.settings = load_settings()
        self.editors, self.toasts = [], []
        self.recorder = None
        self.capturing = False
        self.dialogs = 0
        self.launcher_visible = False
        root = self.root = tk.Tk(className="pisnip")
        root.withdraw()
        root.title(APP_NAME)
        self._style()
        try:
            self.icon = ImageTk.PhotoImage(make_icon_image(64))
            root.iconphoto(True, self.icon)
        except Exception:
            pass

        if args.snip:
            self.start_snip(mode=args.mode, delay=args.delay, hotkey=True, shot=shot)
        elif args.full:
            self.start_snip(mode="full", delay=args.delay or 0, hotkey=True, shot=shot)
        elif args.record:
            self.start_record()
        elif args.settings:
            self.open_settings()
        elif args.file:
            try:
                img = Image.open(args.file)
                img.load()
                Editor(self, img, os.path.abspath(args.file), dirty=False)
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Could not open {args.file}:\n{e}")
                self.show_launcher()
        else:
            self.show_launcher()

    def _style(self):
        st = ttk.Style(self.root)
        if "clam" in st.theme_names():
            st.theme_use("clam")
        bg = "#f3f3f3"
        self.root.configure(bg=bg)
        st.configure(".", background=bg)
        st.configure("TButton", padding=(10, 4))
        st.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT)
        st.map("Accent.TButton", background=[("active", "#1a86d9"), ("disabled", "#9cc3e6")])
        st.configure("Toolbutton", padding=(8, 4))
        st.map("Toolbutton", background=[("selected", "#cfe4f7"), ("active", "#e5e5e5")])
        st.configure("Status.TLabel", background="#e9e9e9", foreground="#444444")
        st.configure("Status.TFrame", background="#e9e9e9")
        st.configure("Zoom.TButton", padding=(2, 0))
        st.configure("Big.Accent.TButton", font=("TkDefaultFont", 11, "bold"), padding=(18, 8))

    def capture_controls(self, parent):
        s = self.settings
        mv = tk.StringVar(value=MODE_LABEL.get(s["mode"], "Rectangle"))
        dv = tk.StringVar(value=DELAY_LABEL.get(int(s["delay"]), "No delay"))
        mcb = ttk.Combobox(parent, textvariable=mv, values=[l for _, l in MODES], state="readonly", width=11)
        dcb = ttk.Combobox(parent, textvariable=dv, values=[l for _, l in DELAYS], state="readonly", width=10)
        mcb.pack(side="left", padx=(6, 2))
        dcb.pack(side="left", padx=2)

        def changed(_=None):
            s["mode"] = MODE_KEY.get(mv.get(), "rectangle")
            s["delay"] = DELAY_KEY.get(dv.get(), 0)
            save_settings(s)
        mcb.bind("<<ComboboxSelected>>", changed)
        dcb.bind("<<ComboboxSelected>>", changed)
        mcb.bind("<FocusIn>", lambda e: mv.set(MODE_LABEL.get(s["mode"], "Rectangle")))
        return mcb, dcb

    # ---------- launcher
    def show_launcher(self):
        r = self.root
        if not getattr(self, "_launcher_built", False):
            self._launcher_built = True
            self._build_launcher()
        self.launcher_visible = True
        self._sync_launcher()
        r.deiconify()
        r.lift()

    def _build_launcher(self):
        """Compact main window modelled on the Windows 11 Snipping Tool."""
        r, s = self.root, self.settings
        r.title("Snipping Tool")
        r.resizable(True, False)
        r.protocol("WM_DELETE_WINDOW", self.close_launcher)
        BG, HOVER, SEL, FG = "#f3f3f3", "#e5e5e5", "#dcebf9", "#1f1f1f"
        r.configure(bg=BG)
        ic = self._icons = {n: ImageTk.PhotoImage(draw_icon(n, 20, FG)) for n in
                            ("camera", "video", "rectangle", "window", "freeform", "full", "clock", "more",
                             "folder", "open", "gear", "keyboard", "pin", "info")}
        ic["plus"] = ImageTk.PhotoImage(draw_icon("plus", 18, "#ffffff"))
        ic["camera_on"] = ImageTk.PhotoImage(draw_icon("camera", 20, ACCENT))
        ic["video_on"] = ImageTk.PhotoImage(draw_icon("video", 20, ACCENT))

        def flat(parent, **kw):
            b = tk.Button(parent, relief="flat", bd=0, highlightthickness=0, bg=BG, fg=FG,
                          activebackground=HOVER, activeforeground=FG, cursor="hand2", **kw)
            b.bind("<Enter>", lambda e: b.configure(bg=HOVER) if b.cget("bg") == BG else None)
            b.bind("<Leave>", lambda e: b.configure(bg=BG) if b.cget("bg") == HOVER else None)
            return b

        def menubtn(parent, **kw):
            b = tk.Menubutton(parent, relief="flat", bd=0, highlightthickness=0, bg=BG, fg=FG,
                              activebackground=HOVER, activeforeground=FG, cursor="hand2", **kw)
            return b

        top = tk.Frame(r, bg=BG, padx=10, pady=10)
        top.pack(fill="x")

        # Snip / Record toggle (camera / video camera), like Windows
        seg = tk.Frame(top, bg="#d6d6d6", padx=1, pady=1)
        seg.grid(row=0, column=0, padx=(0, 10))
        self.kind = tk.StringVar(value="snip")
        self.kind_btns = {}
        for col, (key, tip) in enumerate((("snip", "Snip"), ("record", "Record"))):
            b = tk.Button(seg, relief="flat", bd=0, highlightthickness=0, width=40, height=30, cursor="hand2",
                          command=lambda k=key: self._set_kind(k))
            b.grid(row=0, column=col)
            self.kind_btns[key] = b
            Tooltip(b, tip)

        new = tk.Button(top, text=" New", image=ic["plus"], compound="left", font=("TkDefaultFont", 11, "bold"),
                        bg=ACCENT, fg="white", activebackground="#1a86d9", activeforeground="white",
                        relief="flat", bd=0, highlightthickness=0, padx=16, pady=6, cursor="hand2",
                        command=self._launcher_new)
        new.grid(row=0, column=1, padx=(0, 10))
        Tooltip(new, "New snip (Ctrl+N)")

        # snip options (hidden in record mode, as in Windows)
        self.opts = tk.Frame(top, bg=BG)
        self.opts.grid(row=0, column=2)
        self.mode_var = tk.StringVar(value=s["mode"])
        self.delay_var = tk.IntVar(value=int(s["delay"]))
        self.mode_btn = menubtn(self.opts, compound="left", padx=8, pady=6, width=128, anchor="w")
        mm = tk.Menu(self.mode_btn, tearoff=False)
        for key, label in MODES:
            mm.add_radiobutton(label=f"  {label}", image=ic[key], compound="left", variable=self.mode_var,
                               value=key, command=self._launcher_opts_changed)
        self.mode_btn["menu"] = mm
        self.mode_btn.pack(side="left", padx=(0, 4))
        Tooltip(self.mode_btn, "Snipping mode")
        self.delay_btn = menubtn(self.opts, image=ic["clock"], compound="left", padx=8, pady=6, width=110, anchor="w")
        dm = tk.Menu(self.delay_btn, tearoff=False)
        for secs, label in DELAYS:
            dm.add_radiobutton(label=label, variable=self.delay_var, value=secs, command=self._launcher_opts_changed)
        self.delay_btn["menu"] = dm
        self.delay_btn.pack(side="left")
        Tooltip(self.delay_btn, "Time delay")

        more = menubtn(top, image=ic["more"], padx=8, pady=8)
        m = tk.Menu(more, tearoff=False)
        m.add_command(label="  Settings", image=ic["gear"], compound="left", command=lambda: self.open_settings(self.root))
        m.add_command(label="  Open file…", image=ic["open"], compound="left", command=self._launcher_open)
        m.add_command(label="  Open Screenshots folder", image=ic["folder"], compound="left",
                      command=lambda: (os.makedirs(self.settings["save_dir"], exist_ok=True), open_path(self.settings["save_dir"])))
        m.add_command(label="  Open Recordings folder", image=ic["folder"], compound="left",
                      command=lambda: (os.makedirs(self.settings["video_dir"], exist_ok=True), open_path(self.settings["video_dir"])))
        m.add_separator()
        m.add_command(label="  Add desktop & taskbar icons", image=ic["pin"], compound="left",
                      command=lambda: messagebox.showinfo(APP_NAME, "\n".join(setup_shortcuts()), parent=self.root))
        m.add_command(label="  Set up keyboard shortcuts", image=ic["keyboard"], compound="left",
                      command=lambda: messagebox.showinfo(APP_NAME, "\n".join(setup_hotkeys()) +
                                                          "\n\nIf a shortcut doesn't work yet, log out and back in.", parent=self.root))
        m.add_separator()
        m.add_command(label="  About", image=ic["info"], compound="left",
                      command=lambda: messagebox.showinfo(APP_NAME, f"{APP_NAME} {VERSION}\nA Snipping Tool for Raspberry Pi.", parent=self.root))
        more["menu"] = m
        more.grid(row=0, column=3, padx=(10, 0))
        Tooltip(more, "See more")

        # our own minimise / close buttons, so the window can always be closed
        # even when the desktop doesn't draw a title bar for it
        wbtns = tk.Frame(top, bg=BG)
        wbtns.grid(row=0, column=4, padx=(8, 0))
        mini = tk.Label(wbtns, text="\u2014", bg=BG, fg=FG, font=("TkDefaultFont", 11), width=3, pady=4, cursor="hand2")
        mini.pack(side="left")
        mini.bind("<Enter>", lambda e: mini.configure(bg=HOVER))
        mini.bind("<Leave>", lambda e: mini.configure(bg=BG))
        mini.bind("<Button-1>", lambda e: self.root.iconify())
        Tooltip(mini, "Minimise")
        close = tk.Label(wbtns, text="\u2715", bg=BG, fg=FG, font=("TkDefaultFont", 12), width=3, pady=3, cursor="hand2")
        close.pack(side="left")
        close.bind("<Enter>", lambda e: close.configure(bg="#c42b1c", fg="white"))
        close.bind("<Leave>", lambda e: close.configure(bg=BG, fg=FG))
        close.bind("<ButtonRelease-1>", lambda e: self.close_launcher())
        Tooltip(close, "Close")
        top.columnconfigure(2, weight=1)

        tk.Frame(r, bg="#e0e0e0", height=1).pack(fill="x")
        self.hint = tk.Label(r, bg=BG, fg="#5f5f5f", font=("TkDefaultFont", 10), pady=22, padx=20)
        self.hint.pack(fill="x")

        r.bind("<Control-n>", lambda e: self._launcher_new())
        r.bind("<Control-w>", lambda e: self.close_launcher())
        r.bind("<Control-q>", lambda e: self.close_launcher())
        r.bind("<FocusIn>", lambda e: self._sync_launcher() if e.widget is r else None)
        self._launcher_colors = (BG, SEL)
        self._set_kind("snip")

    def _set_kind(self, kind):
        self.kind.set(kind)
        BG, SEL = self._launcher_colors
        for key, b in self.kind_btns.items():
            on = key == kind
            b.configure(image=self._icons[("camera" if key == "snip" else "video") + ("_on" if on else "")],
                        bg=SEL if on else BG, activebackground=SEL if on else "#e5e5e5")
        if kind == "record":
            self.opts.grid_remove()
            self.hint.configure(text="Select New or press Raspberry + Shift + R to start a screen recording.")
        else:
            self.opts.grid()
            self.hint.configure(text="Press Print Screen or Raspberry + Shift + S to start a snip.")

    def _sync_launcher(self):
        if not getattr(self, "_launcher_built", False):
            return
        s = self.settings
        self.mode_var.set(s["mode"])
        self.delay_var.set(int(s["delay"]))
        self.mode_btn.configure(image=self._icons.get(s["mode"], self._icons["rectangle"]),
                                text=f"  {MODE_LABEL.get(s['mode'], 'Rectangle')}  \u25be")
        self.delay_btn.configure(text=f"  {DELAY_LABEL.get(int(s['delay']), 'No delay')}  \u25be")

    def _launcher_opts_changed(self):
        self.settings["mode"] = self.mode_var.get()
        self.settings["delay"] = int(self.delay_var.get())
        save_settings(self.settings)
        self._sync_launcher()

    def _launcher_new(self):
        if self.kind.get() == "record":
            self.start_record()
        else:
            self.start_snip()

    def _launcher_open(self):
        path = filedialog.askopenfilename(parent=self.root, title="Open image", initialdir=self.settings["save_dir"],
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*")])
        if path:
            try:
                img = Image.open(path)
                img.load()
                Editor(self, img, path, dirty=False)
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Could not open image:\n{e}")

    def close_launcher(self):
        self.launcher_visible = False
        self.root.withdraw()
        self.check_exit()

    def open_settings(self, parent=None):
        SettingsDialog(self, parent)

    # ---------- window hiding during capture
    def hide_windows(self):
        hidden = []
        for w in [self.root] + [e.win for e in self.editors]:
            try:
                if w.winfo_exists() and w.state() != "withdrawn":
                    w.withdraw()
                    hidden.append(w)
            except tk.TclError:
                pass
        for t in list(self.toasts):
            t.close()
        self.root.update()
        return hidden

    def restore(self, hidden):
        for w in hidden:
            try:
                if w.winfo_exists():
                    w.deiconify()
            except tk.TclError:
                pass

    # ---------- snipping
    def start_snip(self, mode=None, delay=None, hotkey=False, target=None, shot=None):
        if self.capturing or self.recorder:
            return
        s = self.settings
        mode = mode or s["mode"]
        delay = int(s["delay"] if delay is None else delay)
        self.capturing = True
        hidden = self.hide_windows()

        def done(img):
            self.capturing = False
            self.restore(hidden)
            self.finish_capture(img, hotkey, target)

        def cancel():
            self.capturing = False
            self.restore(hidden)
            self.check_exit()

        def capture(img=None):
            img = img or grab_screen()
            if img is None:
                self.capturing = False
                self.restore(hidden)
                messagebox.showerror(APP_NAME, "Couldn't capture the screen.\n\nOn Wayland install grim:\n  sudo apt install grim\n"
                                               "On X11 install scrot:\n  sudo apt install scrot")
                self.check_exit()
                return
            if mode == "full":
                done(img)
            else:
                SnipOverlay(self, img, mode, done, cancel)

        if shot is not None and delay == 0:
            capture(shot)
        elif delay > 0:
            self.root.after(250, lambda: Countdown(self, delay, capture))
        else:
            self.root.after(350 if hidden else 50, capture)

    def finish_capture(self, img, hotkey=False, target=None):
        s = self.settings
        if s.get("add_border"):
            img = add_border(img, s.get("border_color", "#000000"), s.get("border_width", 2))
        path = None
        if s.get("auto_save", True):
            try:
                path = unique_path(s["save_dir"], f"Screenshot {stamp()}", "." + s.get("format", "png"))
                save_image(img, path)
            except Exception as e:
                path = None
                messagebox.showerror(APP_NAME, f"Couldn't auto-save the screenshot:\n{e}")
        copied = clipboard_image(img) if s.get("auto_copy", True) else False
        if hotkey and not s.get("open_editor_after_hotkey"):
            title = "Snip copied to clipboard" if copied else ("Snip saved" if path else "Snip captured")
            sub = (f"Automatically saved to the {os.path.basename(os.path.dirname(path))} folder. " if path else "") + \
                "Select here to mark up and share the image."
            Toast(self, title, sub, img=img, on_click=lambda: self.open_editor(img, path), timeout=8000)
        else:
            self.open_editor(img, path, target)

    def open_editor(self, img, path, target=None):
        if target is not None and target in self.editors and not self.settings.get("multiple_windows"):
            target.load(img, path)
        else:
            Editor(self, img, path)

    # ---------- recording
    def start_record(self):
        if self.capturing or self.recorder:
            return
        self.capturing = True
        hidden = self.hide_windows()

        def finished(path):
            self.restore(hidden)
            if path:
                Toast(self, "Screen recording saved", os.path.basename(path),
                      buttons=[("Play", lambda: open_path(path)), ("Open folder", lambda: open_path(os.path.dirname(path)))],
                      timeout=10000)
            self.check_exit()

        def done(rect):
            self.capturing = False
            Recorder(self, rect, finished)

        def cancel():
            self.capturing = False
            self.restore(hidden)
            self.check_exit()

        def go():
            img = grab_screen()
            if img is None:
                cancel()
                messagebox.showerror(APP_NAME, "Couldn't capture the screen to choose a recording area.")
                return
            SnipOverlay(self, img, "rectangle", done, cancel, record=True)

        self.root.after(350 if hidden else 50, go)

    # ---------- lifetime
    def check_exit(self):
        def later():
            if self.launcher_visible or self.editors or self.toasts or self.recorder or self.capturing or self.dialogs > 0:
                return
            self.root.quit()
        try:
            self.root.after(300, later)
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    ap = argparse.ArgumentParser(prog="pisnip", description="Snipping Tool for Raspberry Pi")
    ap.add_argument("file", nargs="?", help="image to open in the editor")
    ap.add_argument("--snip", action="store_true", help="start a snip right away (for keyboard shortcuts)")
    ap.add_argument("--full", action="store_true", help="capture the full screen right away")
    ap.add_argument("--record", action="store_true", help="record an area of the screen")
    ap.add_argument("--mode", choices=[k for k, _ in MODES], help="snip mode for --snip")
    ap.add_argument("--delay", type=int, choices=[0, 3, 5, 10], help="delay in seconds")
    ap.add_argument("--settings", action="store_true", help="open settings")
    ap.add_argument("--setup-hotkeys", action="store_true", help="add keyboard shortcuts to the desktop config")
    ap.add_argument("--setup-shortcuts", action="store_true", help="add menu entry, desktop icon and taskbar button")
    ap.add_argument("--no-desktop-icon", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-taskbar", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--make-icon", metavar="PNG", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    args = ap.parse_args()

    if args.make_icon:
        make_icon_image(128).save(args.make_icon)
        return
    if args.setup_hotkeys:
        print("\n".join(setup_hotkeys()))
        return
    if args.setup_shortcuts:
        print("\n".join(setup_shortcuts(desktop=not args.no_desktop_icon, taskbar=not args.no_taskbar)))
        return
    shot = None
    if (args.snip or args.full) and not args.delay:
        shot = grab_screen()  # grab before Tk starts so the snip matches the moment the key was pressed
    App(args, shot).run()


if __name__ == "__main__":
    main()
