#!/usr/bin/env python3
"""
Halo Studio - an easy, AI-powered image studio that runs on your own machine
and is shared with everyone on your home network.

Features: layered editor, local AI (background removal, generative fill,
text-to-image, AI upscaling via ComfyUI), batch thumbnails + contact sheets,
GIF / animated WebP / APNG / animated AVIF maker, next-gen compression
(AVIF, JPEG XL, WebP, PNG-8) with target-size mode, and native 3D-print
output (lithophanes, relief signs, keychains, cookie cutters, multi-color
filament-swap prints) as STL and 3MF.

Run:  python server.py        (then open the printed address on any device)
"""
import base64
import io
import json
import os
import socket
import sys
import threading
import time
import uuid
import zipfile
import urllib.request
import urllib.parse
from collections import OrderedDict

import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory, abort, Response
from PIL import Image, ImageOps, ImageFilter, ImageDraw, ImageFont, ImageEnhance, features
from werkzeug.utils import secure_filename

VERSION = "1.0"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
SHARED_DIR = os.path.join(APP_DIR, "shared")
os.makedirs(SHARED_DIR, exist_ok=True)
PORT = int(os.environ.get("HALO_PORT", "8395"))
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")

Image.MAX_IMAGE_PIXELS = 400_000_000

# ---------- optional format plugins ----------
HAS_JXL = False
try:
    import pillow_jxl  # noqa: F401  (registers JPEG XL)
    HAS_JXL = True
except Exception:
    pass
HAS_HEIF = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except Exception:
    pass
HAS_AVIF = bool(features.check("avif"))
HAS_WEBP = bool(features.check("webp"))

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB uploads


# =====================================================================
# helpers
# =====================================================================
def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packets are sent
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def load_image(fs):
    """Open an uploaded file, honour camera rotation."""
    img = Image.open(fs.stream if hasattr(fs, "stream") else fs)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img


def b64img(img, fmt="PNG", **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return "data:image/%s;base64,%s" % (fmt.lower(), base64.b64encode(buf.getvalue()).decode())


def flatten(img, bg=(255, 255, 255)):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        base = Image.new("RGB", img.size, bg)
        base.paste(img, mask=img.split()[-1])
        return base
    return img.convert("RGB")


FORMAT_INFO = {
    # key: (pillow format, extension, mime, lossy)
    "jpeg": ("JPEG", "jpg", "image/jpeg", True),
    "webp": ("WEBP", "webp", "image/webp", True),
    "avif": ("AVIF", "avif", "image/avif", True),
    "jxl": ("JXL", "jxl", "image/jxl", True),
    "png": ("PNG", "png", "image/png", False),
    "png8": ("PNG", "png", "image/png", False),
}


def available_formats():
    out = ["jpeg", "png", "png8"]
    if HAS_WEBP:
        out.append("webp")
    if HAS_AVIF:
        out.append("avif")
    if HAS_JXL:
        out.append("jxl")
    return out


def encode(img, fmt, quality=80):
    """Encode a PIL image to bytes in the requested format."""
    quality = int(max(1, min(100, quality)))
    buf = io.BytesIO()
    has_alpha = img.mode in ("RGBA", "LA", "P")
    if fmt == "jpeg":
        flatten(img).save(buf, "JPEG", quality=quality, optimize=True, progressive=True,
                          subsampling=0 if quality >= 90 else 2)
    elif fmt == "webp":
        im = img.convert("RGBA") if has_alpha else img.convert("RGB")
        if quality >= 100:
            im.save(buf, "WEBP", lossless=True, method=6)
        else:
            im.save(buf, "WEBP", quality=quality, method=6)
    elif fmt == "avif":
        im = img.convert("RGBA") if has_alpha else img.convert("RGB")
        im.save(buf, "AVIF", quality=quality, speed=6)
    elif fmt == "jxl":
        im = img.convert("RGBA") if has_alpha else img.convert("RGB")
        if quality >= 100:
            im.save(buf, "JXL", lossless=True, effort=7)
        else:
            im.save(buf, "JXL", quality=quality, effort=7)
    elif fmt == "png":
        im = img if img.mode in ("RGB", "RGBA", "L", "LA", "P") else img.convert("RGBA")
        im.save(buf, "PNG", optimize=True)
    elif fmt == "png8":
        im = img.convert("RGBA")
        q = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
        q.save(buf, "PNG", optimize=True)
    else:
        raise ValueError("Unknown format " + fmt)
    return buf.getvalue()


def encode_to_target(img, fmt, target_bytes):
    """Binary-search quality so the file lands at/under the target size."""
    lo, hi, best = 5, 95, None
    for _ in range(8):
        mid = (lo + hi) // 2
        data = encode(img, fmt, mid)
        if len(data) <= target_bytes:
            best = (data, mid)
            lo = mid + 1
        else:
            hi = mid - 1
        if lo > hi:
            break
    if best is None:
        best = (encode(img, fmt, 5), 5)
    return best


def resize_max(img, max_side):
    if max_side and max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


# =====================================================================
# static + info
# =====================================================================
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/hs.js")
def probe_js():
    """Tiny script the My Apps menu loads to find a running Halo Studio (never matches another app)."""
    r = Response("(window.__halostudio=window.__halostudio||[]).push(%d);" % PORT, mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC_DIR, p)


def onnx_providers():
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except Exception:
        return []


def has_rembg():
    try:
        import rembg  # noqa: F401
        return True
    except Exception:
        return False


@app.route("/api/info")
def info():
    comfy = comfy_status()
    provs = onnx_providers()
    accel = [p for p in provs if p not in ("CPUExecutionProvider", "AzureExecutionProvider")]
    return jsonify({
        "version": VERSION,
        "host": socket.gethostname(),
        "port": PORT,
        "lan_urls": ["http://%s:%d" % (ip, PORT) for ip in lan_ips()],
        "formats": available_formats(),
        "heif": HAS_HEIF,
        "rembg": has_rembg(),
        "onnx_providers": provs,
        "onnx_accel": accel,
        "cpu_threads": os.cpu_count(),
        "comfy": comfy,
    })


@app.route("/api/qr")
def qr():
    text = request.args.get("text", "")
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), mimetype="image/svg+xml")
    except Exception:
        abort(404)


# =====================================================================
# compression / convert
# =====================================================================
@app.route("/api/compress", methods=["POST"])
def compress():
    f = request.files.get("image")
    if not f:
        return jsonify(error="No image uploaded"), 400
    raw = f.read()
    img = load_image(io.BytesIO(raw))
    img = resize_max(img, int(request.form.get("max_side", 0) or 0))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.mode or img.mode == "P" else "RGB")
    fmts = [x for x in request.form.get("formats", "avif,webp,jpeg").split(",") if x in available_formats()]
    quality = int(request.form.get("quality", 75))
    target_kb = float(request.form.get("target_kb", 0) or 0)
    results = []
    for fmt in fmts:
        t0 = time.time()
        try:
            if target_kb > 0 and FORMAT_INFO[fmt][3]:
                data, q = encode_to_target(img, fmt, int(target_kb * 1024))
            else:
                data, q = encode(img, fmt, quality), quality
        except Exception as e:
            results.append({"format": fmt, "error": str(e)})
            continue
        ms = int((time.time() - t0) * 1000)
        _, ext, mime, lossy = FORMAT_INFO[fmt]
        results.append({
            "format": fmt, "ext": ext, "bytes": len(data), "quality": q if lossy else None, "ms": ms,
            "data": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode()),
        })
    results.sort(key=lambda r: r.get("bytes", 1e18))
    return jsonify(original_bytes=len(raw), width=img.width, height=img.height, results=results)


@app.route("/api/convert", methods=["POST"])
def convert():
    f = request.files.get("image")
    if not f:
        return jsonify(error="No image uploaded"), 400
    fmt = request.form.get("format", "png")
    if fmt not in available_formats():
        return jsonify(error="Format not available: " + fmt), 400
    img = load_image(f)
    data = encode(img, fmt, int(request.form.get("quality", 85)))
    _, ext, mime, _ = FORMAT_INFO[fmt]
    name = secure_filename(request.form.get("name", "image")) or "image"
    return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name="%s.%s" % (name, ext))


# =====================================================================
# batch thumbnails
# =====================================================================
def get_font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def watermark(img, text):
    if not text:
        return img
    img = img.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = max(10, int(min(img.size) / 14))
    font = get_font(size)
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x, y = img.width - tw - size // 2, img.height - th - size // 2 - box[1]
    d.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 110))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 170))
    return Image.alpha_composite(img, layer)


def make_thumb(img, size, mode, bg):
    if mode == "crop":
        return ImageOps.fit(img, (size, size), Image.LANCZOS, centering=(0.5, 0.45))
    t = img.copy()
    t.thumbnail((size, size), Image.LANCZOS)
    if mode == "pad":
        canvas = Image.new("RGBA", (size, size), bg)
        canvas.paste(t, ((size - t.width) // 2, (size - t.height) // 2), t if t.mode == "RGBA" else None)
        return canvas
    return t


def contact_sheet(items, title):
    """items: list of (name, PIL image). Returns an RGB contact sheet."""
    cell, pad, label = 220, 14, 28
    cols = min(6, max(1, len(items)))
    rows = (len(items) + cols - 1) // cols
    head = 60
    W = cols * (cell + pad) + pad
    H = head + rows * (cell + label + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 22, 20))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 18), title, font=get_font(22), fill=(255, 196, 92))
    small = get_font(13)
    for i, (name, im) in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = head + r * (cell + label + pad)
        t = im.copy()
        t.thumbnail((cell, cell), Image.LANCZOS)
        d.rectangle([x, y, x + cell, y + cell], fill=(38, 35, 32))
        t = t.convert("RGBA")
        sheet.paste(t, (x + (cell - t.width) // 2, y + (cell - t.height) // 2), t)
        nm = name if len(name) <= 28 else name[:25] + "..."
        d.text((x, y + cell + 6), nm, font=small, fill=(220, 214, 205))
    return sheet


@app.route("/api/batch", methods=["POST"])
def batch():
    files = request.files.getlist("images")
    if not files:
        return jsonify(error="No images uploaded"), 400
    raw_sizes = request.form.get("sizes", "256").replace(" ", "").lower().split(",")
    sizes = sorted({max(16, min(8000, int(s))) for s in raw_sizes if s.isdigit()})
    if "full" in raw_sizes:
        sizes.append(0)  # 0 = keep original size (just convert / compress)
    if not sizes:
        sizes = [256]
    mode = request.form.get("mode", "fit")
    fmt = request.form.get("format", "jpeg")
    if fmt not in available_formats():
        fmt = "jpeg"
    quality = int(request.form.get("quality", 82))
    pattern = request.form.get("pattern", "{name}_{size}") or "{name}_{size}"
    wm = request.form.get("watermark", "").strip()
    sharpen = request.form.get("sharpen", "1") == "1"
    sheet_on = request.form.get("sheet", "1") == "1"
    bg = request.form.get("bg", "#ffffff")
    try:
        bgc = tuple(int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    except Exception:
        bgc = (255, 255, 255, 255)
    _, ext, _, _ = FORMAT_INFO[fmt]

    zbuf = io.BytesIO()
    sheet_items, errors, count = [], [], 0
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_STORED) as z:
        for idx, f in enumerate(files):
            base = os.path.splitext(secure_filename(f.filename or "image%d" % idx))[0] or "image%d" % idx
            try:
                img = load_image(f)
                img = img.convert("RGBA") if (img.mode in ("RGBA", "LA", "P")) else img.convert("RGB")
            except Exception as e:
                errors.append("%s: %s" % (f.filename, e))
                continue
            for s in sizes:
                if s == 0:
                    t = img.copy()
                    t = watermark(t, wm)
                    name = pattern.replace("{name}", base).replace("{size}", "full").replace("{n}", "%03d" % (idx + 1))
                    name = secure_filename(name) or base
                    z.writestr("%s.%s" % (name, ext), encode(t, fmt, quality))
                    count += 1
                    continue
                t = make_thumb(img, s, mode, bgc)
                if sharpen:
                    t = t.filter(ImageFilter.UnsharpMask(radius=0.8, percent=70, threshold=2))
                t = watermark(t, wm)
                name = pattern.replace("{name}", base).replace("{size}", str(s)).replace("{n}", "%03d" % (idx + 1))
                name = secure_filename(name) or "%s_%d" % (base, s)
                z.writestr("%s.%s" % (name, ext), encode(t, fmt, quality))
                count += 1
            if sheet_on:
                sheet_items.append((f.filename or base, img))
        if sheet_on and sheet_items:
            sheet = contact_sheet(sheet_items, "Contact sheet - %d images" % len(sheet_items))
            z.writestr("_contact_sheet.jpg", encode(sheet, "jpeg", 88))
        if errors:
            z.writestr("_errors.txt", "\n".join(errors))
    zbuf.seek(0)
    resp = send_file(zbuf, mimetype="application/zip", as_attachment=True, download_name="thumbnails.zip")
    resp.headers["X-Thumb-Count"] = str(count)
    resp.headers["X-Error-Count"] = str(len(errors))
    return resp


# =====================================================================
# animation maker
# =====================================================================
@app.route("/api/animate", methods=["POST"])
def animate():
    files = request.files.getlist("frames")
    if len(files) < 1:
        return jsonify(error="Add at least one frame"), 400
    fmt = request.form.get("format", "gif")
    fps = max(0.5, min(60.0, float(request.form.get("fps", 10))))
    width = int(request.form.get("width", 480) or 480)
    loop = int(request.form.get("loop", 0))
    colors = max(2, min(256, int(request.form.get("colors", 256))))
    dither = request.form.get("dither", "1") == "1"
    boomerang = request.form.get("boomerang", "0") == "1"
    quality = int(request.form.get("quality", 75))
    delays = request.form.get("delays", "")

    frames = []
    for f in files:
        im = load_image(f).convert("RGBA")
        frames.append(im)
    w0, h0 = frames[0].size
    width = max(16, min(width, 2000))
    height = max(1, round(h0 * width / w0))
    out = []
    for im in frames:
        canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        t = ImageOps.contain(im, (width, height), Image.LANCZOS)
        canvas.alpha_composite(t, ((width - t.width) // 2, (height - t.height) // 2))
        out.append(canvas)
    base_ms = int(round(1000 / fps))
    durs = [base_ms] * len(out)
    if delays:
        try:
            d = [int(x) for x in delays.split(",")]
            if len(d) == len(out):
                durs = [max(10, x) for x in d]
        except Exception:
            pass
    if boomerang and len(out) > 2:
        out = out + out[-2:0:-1]
        durs = durs + durs[-2:0:-1]

    buf = io.BytesIO()
    if fmt == "gif":
        # shared global palette = smaller files + no flicker
        strip = Image.new("RGB", (width, height * min(len(out), 12)))
        step = max(1, len(out) // 12)
        for i, im in enumerate(out[::step][:12]):
            strip.paste(im.convert("RGB"), (0, i * height))
        pal = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        dm = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
        pframes = [im.convert("RGB").quantize(palette=pal, dither=dm) for im in out]
        pframes[0].save(buf, "GIF", save_all=True, append_images=pframes[1:], duration=durs,
                        loop=loop, optimize=True, disposal=1)
        mime, ext = "image/gif", "gif"
    elif fmt == "webp":
        out[0].save(buf, "WEBP", save_all=True, append_images=out[1:], duration=durs, loop=loop,
                    quality=quality, method=4)
        mime, ext = "image/webp", "webp"
    elif fmt == "apng":
        out[0].save(buf, "PNG", save_all=True, append_images=out[1:], duration=durs, loop=loop, optimize=True)
        mime, ext = "image/apng", "png"
    elif fmt == "avif" and HAS_AVIF:
        rgb = [im.convert("RGB") for im in out]
        rgb[0].save(buf, "AVIF", save_all=True, append_images=rgb[1:], duration=durs, loop=loop,
                    quality=quality, speed=7)
        mime, ext = "image/avif", "avif"
    else:
        return jsonify(error="Format not supported: " + fmt), 400
    data = buf.getvalue()
    return jsonify(bytes=len(data), frames=len(out), width=width, height=height, ext=ext,
                   data="data:%s;base64,%s" % (mime, base64.b64encode(data).decode()))


# =====================================================================
# 3D printing
# =====================================================================
MESH_CACHE = OrderedDict()
MESH_LOCK = threading.Lock()


def quads_to_tris(quads, outward):
    """quads (N,4,3) corners in cycle order, outward (N,3). Returns oriented (2N,3,3)."""
    t1 = quads[:, [0, 1, 2]]
    t2 = quads[:, [0, 2, 3]]
    tris = np.concatenate([t1, t2], axis=0)
    out = np.concatenate([outward, outward], axis=0)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    flip = np.einsum("ij,ij->i", n, out) < 0
    tris[flip] = tris[flip][:, [0, 2, 1]]
    return tris


def heightmap_solid(Z, px):
    """Smooth closed solid: top surface = Z (mm), flat bottom at 0."""
    h, w = Z.shape
    xs = np.arange(w) * px
    ys = (h - 1 - np.arange(h)) * px
    X, Y = np.meshgrid(xs, ys)
    T = np.stack([X, Y, Z], axis=-1).astype(np.float64)
    B = np.stack([X, Y, np.zeros_like(Z)], axis=-1).astype(np.float64)

    def grid_quads(P):
        return np.stack([P[:-1, :-1], P[:-1, 1:], P[1:, 1:], P[1:, :-1]], axis=2).reshape(-1, 4, 3)

    parts = []
    q = grid_quads(T)
    parts.append(quads_to_tris(q, np.tile([0, 0, 1.0], (len(q), 1))))
    q = grid_quads(B)
    parts.append(quads_to_tris(q, np.tile([0, 0, -1.0], (len(q), 1))))
    # walls
    def wall(top_edge, bot_edge, direction):
        q = np.stack([top_edge[:-1], top_edge[1:], bot_edge[1:], bot_edge[:-1]], axis=1)
        return quads_to_tris(q, np.tile(direction, (len(q), 1)))
    parts.append(wall(T[0, :], B[0, :], [0, 1.0, 0]))
    parts.append(wall(T[-1, :], B[-1, :], [0, -1.0, 0]))
    parts.append(wall(T[:, 0], B[:, 0], [-1.0, 0, 0]))
    parts.append(wall(T[:, -1], B[:, -1], [1.0, 0, 0]))
    return np.concatenate(parts, axis=0)


def column_solid(H, px):
    """Blocky extrusion: each pixel is a column of height H[i,j] (0 = empty)."""
    H = H.astype(np.float64).copy()
    # remove diagonal-only pixel contacts (they make non-manifold edges slicers complain about)
    for _ in range(20):
        changed = False
        for L in np.unique(H[H > 0])[::-1]:
            O = H >= L
            a, b, c, d = O[:-1, :-1], O[:-1, 1:], O[1:, :-1], O[1:, 1:]
            m1 = a & d & ~b & ~c
            m2 = b & c & ~a & ~d
            if m1.any():
                r, q = np.nonzero(m1)
                H[r, q + 1] = np.maximum(H[r, q + 1], L)
                changed = True
            if m2.any():
                r, q = np.nonzero(m2)
                H[r, q] = np.maximum(H[r, q], L)
                changed = True
        if not changed:
            break
    h, w = H.shape
    Hp = np.pad(H, 1)
    ii, jj = np.nonzero(H > 0)
    if len(ii) == 0:
        raise ValueError("Nothing to print - the shape is empty. Try changing the threshold or invert.")
    hz = H[ii, jj].astype(np.float64)
    x0, x1 = jj * px, (jj + 1) * px
    y1, y0 = (h - ii) * px, (h - ii - 1) * px
    z0 = np.zeros_like(hz)

    def mk(a, b, c, d):
        return np.stack([np.stack(a, -1), np.stack(b, -1), np.stack(c, -1), np.stack(d, -1)], axis=1)

    parts = []
    q = mk((x0, y1, hz), (x1, y1, hz), (x1, y0, hz), (x0, y0, hz))
    parts.append(quads_to_tris(q, np.tile([0, 0, 1.0], (len(q), 1))))
    q = mk((x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0))
    parts.append(quads_to_tris(q, np.tile([0, 0, -1.0], (len(q), 1))))
    # side walls, split at every height level so neighbouring walls share edges (watertight)
    levels = np.unique(np.concatenate([[0.0], H[H > 0].ravel()]))
    for di, dj, d in ((-1, 0, [0, 1.0, 0]), (1, 0, [0, -1.0, 0]), (0, -1, [-1.0, 0, 0]), (0, 1, [1.0, 0, 0])):
      hn_all = Hp[ii + 1 + di, jj + 1 + dj].astype(np.float64)
      for la, lb in zip(levels[:-1], levels[1:]):
        m = (hn_all <= la) & (hz >= lb)
        if not m.any():
            continue
        a_x0, a_x1, a_y0, a_y1 = x0[m], x1[m], y0[m], y1[m]
        a_n = np.full(m.sum(), la)
        a_h = np.full(m.sum(), lb)
        if di == -1:
            q = mk((a_x0, a_y1, a_n), (a_x1, a_y1, a_n), (a_x1, a_y1, a_h), (a_x0, a_y1, a_h))
        elif di == 1:
            q = mk((a_x0, a_y0, a_n), (a_x1, a_y0, a_n), (a_x1, a_y0, a_h), (a_x0, a_y0, a_h))
        elif dj == -1:
            q = mk((a_x0, a_y0, a_n), (a_x0, a_y1, a_n), (a_x0, a_y1, a_h), (a_x0, a_y0, a_h))
        else:
            q = mk((a_x1, a_y0, a_n), (a_x1, a_y1, a_n), (a_x1, a_y1, a_h), (a_x1, a_y0, a_h))
        parts.append(quads_to_tris(q, np.tile(d, (len(q), 1))))
    return np.concatenate(parts, axis=0)


def bend(tris, width_mm, angle_deg):
    """Wrap a flat lithophane around a cylinder (curved lithophane)."""
    ang = np.radians(max(10, min(359, angle_deg)))
    R = width_mm / ang
    t = tris.copy()
    x = t[..., 0] - width_mm / 2
    z = t[..., 2]
    th = x / R
    t[..., 0] = (R + z) * np.sin(th)
    t[..., 2] = (R + z) * np.cos(th) - R
    return t


def dilate(m, k):
    m = m.copy()
    for _ in range(int(k)):
        p = np.pad(m, 1)
        m = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
             | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
    return m


def erode(m, k):
    return ~dilate(~m, k)


def fill_holes(mask):
    try:
        from scipy.ndimage import binary_fill_holes
        return binary_fill_holes(mask)
    except Exception:
        pass
    outside = np.zeros_like(mask)
    outside[0, :] = ~mask[0, :]
    outside[-1, :] = ~mask[-1, :]
    outside[:, 0] |= ~mask[:, 0]
    outside[:, -1] |= ~mask[:, -1]
    for _ in range(4000):
        p = np.pad(outside, 1)
        grown = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]) & ~mask
        if (grown == outside).all():
            break
        outside = grown
    return ~outside


def tris_bounds(t):
    mn = t.reshape(-1, 3).min(0)
    mx = t.reshape(-1, 3).max(0)
    return mn, mx


def to_stl(tris):
    tris = tris.astype(np.float32)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1
    n = n / ln
    dt = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec = np.zeros(len(tris), dtype=dt)
    rec["n"] = n
    rec["v"] = tris
    header = b"Halo Studio STL".ljust(80, b" ")
    return header + np.uint32(len(tris)).tobytes() + rec.tobytes()


def to_3mf(tris, name="Halo Studio print"):
    allv = np.round(tris.reshape(-1, 3), 4)
    uniq, inv = np.unique(allv, axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3)
    vtxt = "".join('<vertex x="%.4f" y="%.4f" z="%.4f"/>' % tuple(v) for v in uniq)
    ftxt = "".join('<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(f) for f in faces if len(set(f)) == 3)
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Title">%s</metadata><metadata name="Application">Halo Studio</metadata>'
             '<resources><object id="1" type="model"><mesh><vertices>%s</vertices><triangles>%s</triangles></mesh></object></resources>'
             '<build><item objectid="1"/></build></model>') % (name, vtxt, ftxt)
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


def prep_grid(img, detail):
    """Return (RGBA image resized to grid, alpha_present)."""
    img = img.convert("RGBA")
    detail = max(40, min(600, int(detail)))
    w, h = img.size
    s = detail / max(w, h)
    gw, gh = max(2, round(w * s)), max(2, round(h * s))
    small = img.resize((gw, gh), Image.LANCZOS)
    alpha = np.asarray(small.split()[-1])
    return small, bool((alpha < 250).any())


def lum_of(small, autocontrast=True):
    rgb = flatten(small)
    g = rgb.convert("L")
    if autocontrast:
        g = ImageOps.autocontrast(g, cutoff=1)
    return np.asarray(g).astype(np.float64) / 255.0


@app.route("/api/3d", methods=["POST"])
def make_3d():
    f = request.files.get("image")
    if not f:
        return jsonify(error="No image uploaded"), 400
    P = request.form
    mode = P.get("mode", "lithophane")
    width_mm = max(10.0, min(400.0, float(P.get("width_mm", 100))))
    invert = P.get("invert", "0") == "1"
    img = load_image(f)
    default_detail = 260 if mode in ("lithophane", "relief") else 180
    small, has_alpha = prep_grid(img, int(P.get("detail", default_detail) or default_detail))
    gh, gw = small.height, small.width
    px = width_mm / gw
    extra = {}
    preview = None

    try:
        if mode == "lithophane":
            tmin = float(P.get("min_mm", 0.8))
            tmax = float(P.get("max_mm", 3.2))
            frame = float(P.get("frame_mm", 3))
            L = lum_of(small)
            if invert:
                L = 1 - L
            Z = tmax - L * (tmax - tmin)
            fp = int(round(frame / px)) if frame > 0 else 0
            if fp:
                Z = np.pad(Z, fp, constant_values=tmax + 0.4)
            tris = heightmap_solid(Z, px)
            curve = float(P.get("curve_deg", 0) or 0)
            if curve >= 10:
                tris = bend(tris, Z.shape[1] * px, curve)
            extra["tip"] = "Print upright in white PLA, 100% infill, 0.12-0.16 mm layers. Shine a light behind it."
        elif mode == "relief":
            base = float(P.get("base_mm", 2.0))
            depth = float(P.get("depth_mm", 3.0))
            L = lum_of(small)
            if invert:
                L = 1 - L
            L = np.asarray(Image.fromarray((L * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))) / 255.0
            Z = base + L * depth
            tris = heightmap_solid(Z, px)
            extra["tip"] = "Bright areas stand up, dark areas sink. Great for signs, coasters and wall art."
        elif mode in ("keychain", "cutter"):
            if has_alpha:
                mask = np.asarray(small.split()[-1]) > 128
            else:
                thr = float(P.get("threshold", 50)) / 100.0
                mask = lum_of(small, autocontrast=False) < thr
            if invert:
                mask = ~mask
            if mask.sum() == 0:
                raise ValueError("Nothing to print - try the threshold slider or Invert.")
            if mode == "keychain":
                th = float(P.get("thickness_mm", 1.5))
                H = np.where(mask, th, 0.0)
                if P.get("outline", "1") == "1":
                    border = max(1, int(round(float(P.get("border_mm", 2)) / px)))
                    plate = dilate(fill_holes(mask), border)
                    base = float(P.get("base_mm", 2))
                    H = np.where(plate, base, 0.0)
                    H = np.where(mask, base + th, H)
                    if P.get("hole", "1") == "1":
                        # keyring tab at top-left of the plate
                        rows, cols = np.nonzero(plate)
                        top = rows.min()
                        c = int(np.median(cols[rows <= top + 2]))
                        rad = max(2, int(round(3.5 / px)))
                        inner = max(1, int(round(2.0 / px)))
                        yy, xx = np.ogrid[:H.shape[0], :H.shape[1]]
                        cy = top + 0  # tab centre sits on top edge
                        pad_top = rad + 1
                        H = np.pad(H, ((pad_top, 0), (0, 0)))
                        yy, xx = np.ogrid[:H.shape[0], :H.shape[1]]
                        cy = top + pad_top
                        ring = (yy - cy) ** 2 + (xx - c) ** 2 <= rad ** 2
                        hole = (yy - cy) ** 2 + (xx - c) ** 2 <= inner ** 2
                        H = np.where(ring & (H == 0), base, H)
                        H = np.where(hole, 0.0, H)
                tris = column_solid(H, px)
                extra["tip"] = "Print flat. Swap filament color at the layer where the raised design starts for a two-tone keychain."
                if P.get("outline", "1") == "1":
                    lh = float(P.get("layer_mm", 0.2))
                    b = float(P.get("base_mm", 2))
                    extra["swaps"] = [
                        {"height_mm": 0.0, "layer": 1, "color": "#ff8a3d", "note": "Start with your base color"},
                        {"height_mm": round(b, 2), "layer": int(round(b / lh)) + 1, "color": "#ffffff",
                         "note": "Swap here so the raised design pops"}]
            else:
                filled = fill_holes(mask)
                wall = max(1, int(round(float(P.get("wall_mm", 1.0)) / px)))
                lip = max(1, int(round(float(P.get("lip_mm", 3.0)) / px)))
                blade_h = float(P.get("height_mm", 15))
                lip_h = float(P.get("lip_height_mm", 2))
                pad = wall + lip + 2
                filled = np.pad(filled, pad)
                ring = dilate(filled, wall) & ~filled
                lipring = dilate(filled, wall + lip) & ~erode(filled, lip)
                H = np.where(ring, blade_h, 0.0)
                H = np.maximum(H, np.where(lipring, lip_h, 0.0))
                tris = column_solid(H, px)
                extra["tip"] = "Print with the wide lip on the bed. 0.2 mm layers, 3 walls. Food-safe PLA recommended."
        elif mode == "colorlayers":
            ncol = max(2, min(8, int(P.get("colors", 4))))
            layer_h = float(P.get("layer_mm", 0.2))
            per = max(1, int(P.get("layers_per_color", 3)))
            base = float(P.get("base_mm", 0.6))
            rgb = flatten(small)
            q = rgb.quantize(colors=ncol, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            pal = np.array(q.getpalette()[:ncol * 3]).reshape(-1, 3)
            idx = np.asarray(q.filter(ImageFilter.ModeFilter(3)))  # remove speckles
            lum = pal @ np.array([0.299, 0.587, 0.114])
            order = np.argsort(lum) if not invert else np.argsort(-lum)
            rank = np.empty_like(order)
            rank[order] = np.arange(len(order))
            band = per * layer_h
            H = base + (rank[idx] + 1) * band
            tris = column_solid(H, px)
            swaps = []
            for k, ci in enumerate(order):
                col = "#%02x%02x%02x" % tuple(int(v) for v in pal[ci])
                start = 0.0 if k == 0 else base + k * band
                swaps.append({"height_mm": round(start, 3), "layer": int(round(start / layer_h)) + 1, "color": col,
                              "note": "Start with this color" if k == 0 else "Swap to this color"})
            extra["swaps"] = swaps
            qprev = Image.fromarray(pal[idx].astype(np.uint8), "RGB")
            preview = b64img(qprev.resize((qprev.width * 2, qprev.height * 2), Image.NEAREST))
            extra["tip"] = "Use your slicer's 'color change at layer' (M600) at each height listed. Layer height must be %.2f mm." % layer_h
        else:
            return jsonify(error="Unknown mode"), 400
    except ValueError as e:
        return jsonify(error=str(e)), 400

    # centre on the bed
    mn, mx = tris_bounds(tris)
    tris = tris - np.array([(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, mn[2]])
    mn, mx = tris_bounds(tris)
    mid = uuid.uuid4().hex[:12]
    with MESH_LOCK:
        MESH_CACHE[mid] = (tris, mode)
        while len(MESH_CACHE) > 12:
            MESH_CACHE.popitem(last=False)
    size = (mx - mn).round(1).tolist()
    # rough filament estimate: volume via divergence theorem
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    vol = abs(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0) / 1000.0  # cm^3
    return jsonify(id=mid, triangles=int(len(tris)), size_mm=size, volume_cm3=round(vol, 1),
                   grams_pla=round(vol * 1.24, 1), preview=preview, **extra)


@app.route("/api/3d/<mid>.<ext>")
def get_3d(mid, ext):
    with MESH_LOCK:
        item = MESH_CACHE.get(mid)
    if not item:
        abort(404)
    tris, mode = item
    name = request.args.get("name", "halo_" + mode)
    name = secure_filename(name) or "halo_print"
    if ext == "stl":
        return send_file(io.BytesIO(to_stl(tris)), mimetype="model/stl", as_attachment=True, download_name=name + ".stl")
    if ext == "3mf":
        return send_file(io.BytesIO(to_3mf(tris, name)), mimetype="model/3mf", as_attachment=True, download_name=name + ".3mf")
    abort(404)


# =====================================================================
# AI - local
# =====================================================================
_REMBG = {}
_REMBG_LOCK = threading.Lock()


def rembg_session(model):
    with _REMBG_LOCK:
        if model in _REMBG:
            return _REMBG[model]
        from rembg import new_session
        prefer = ["DmlExecutionProvider", "ROCMExecutionProvider", "MIGraphXExecutionProvider",
                  "VitisAIExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
        avail = onnx_providers()
        provs = [p for p in prefer if p in avail and p != "VitisAIExecutionProvider"] or ["CPUExecutionProvider"]
        try:
            s = new_session(model, providers=provs)
        except TypeError:
            s = new_session(model)
        _REMBG[model] = (s, provs[0])
        return _REMBG[model]


@app.route("/api/ai/removebg", methods=["POST"])
def ai_removebg():
    f = request.files.get("image")
    if not f:
        return jsonify(error="No image"), 400
    if not has_rembg():
        return jsonify(error="Background removal isn't installed yet. Run 'Install AI Extras' on the server PC, then restart Halo Studio."), 501
    from rembg import remove
    img = load_image(f).convert("RGBA")
    model = request.form.get("model", "isnet-general-use")
    t0 = time.time()
    try:
        sess, prov = rembg_session(model)
    except Exception:
        sess, prov = rembg_session("u2net")
    out = remove(img, session=sess, post_process_mask=True)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    resp = send_file(io.BytesIO(buf.getvalue()), mimetype="image/png")
    resp.headers["X-AI-Engine"] = prov
    resp.headers["X-AI-Ms"] = str(int((time.time() - t0) * 1000))
    return resp


@app.route("/api/enhance", methods=["POST"])
def enhance():
    f = request.files.get("image")
    if not f:
        return jsonify(error="No image"), 400
    img = load_image(f).convert("RGBA")
    a = img.split()[-1]
    rgb = img.convert("RGB")
    rgb = ImageOps.autocontrast(rgb, cutoff=0.5, preserve_tone=True)
    rgb = ImageEnhance.Color(rgb).enhance(1.12)
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    rgb.putalpha(a)
    buf = io.BytesIO()
    rgb.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# =====================================================================
# AI - ComfyUI bridge (uses the GPU / unified memory of this PC)
# =====================================================================
_COMFY_CACHE = {"t": 0, "v": None}


def comfy_req(path, data=None, timeout=10, raw=False, headers=None):
    url = COMFY_URL + path
    if data is not None and not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data).encode()
        headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return body if raw else json.loads(body.decode() or "null")


def comfy_status(force=False):
    if not force and time.time() - _COMFY_CACHE["t"] < 15 and _COMFY_CACHE["v"] is not None:
        return _COMFY_CACHE["v"]
    st = {"ok": False, "url": COMFY_URL}
    try:
        stats = comfy_req("/system_stats", timeout=1.5)
        st["ok"] = True
        devs = stats.get("devices") or []
        if devs:
            d = devs[0]
            st["device"] = d.get("name", "")
            st["vram_gb"] = round((d.get("vram_total") or 0) / 1024 ** 3, 1)
        try:
            oi = comfy_req("/object_info/CheckpointLoaderSimple", timeout=4)
            st["checkpoints"] = oi["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        except Exception:
            st["checkpoints"] = []
        try:
            oi = comfy_req("/object_info/UpscaleModelLoader", timeout=4)
            st["upscalers"] = oi["UpscaleModelLoader"]["input"]["required"]["model_name"][0]
        except Exception:
            st["upscalers"] = []
    except Exception:
        pass
    _COMFY_CACHE.update(t=time.time(), v=st)
    return st


def comfy_upload(img, name):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    boundary = "----halo" + uuid.uuid4().hex
    body = io.BytesIO()
    def part(h, content):
        body.write(("--%s\r\n%s\r\n\r\n" % (boundary, h)).encode())
        body.write(content)
        body.write(b"\r\n")
    part('Content-Disposition: form-data; name="image"; filename="%s"\r\nContent-Type: image/png' % name, buf.getvalue())
    part('Content-Disposition: form-data; name="overwrite"', b"true")
    body.write(("--%s--\r\n" % boundary).encode())
    res = comfy_req("/upload/image", data=body.getvalue(), timeout=60,
                    headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    return res["name"] if not res.get("subfolder") else res["subfolder"] + "/" + res["name"]


def comfy_run(workflow, timeout=900):
    cid = uuid.uuid4().hex
    res = comfy_req("/prompt", {"prompt": workflow, "client_id": cid}, timeout=30)
    if "prompt_id" not in res:
        raise RuntimeError("ComfyUI rejected the job: %s" % res)
    pid = res["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.6)
        hist = comfy_req("/history/" + pid, timeout=10)
        if hist and pid in hist:
            h = hist[pid]
            status = h.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError("ComfyUI error - check the ComfyUI window for details.")
            for node in h.get("outputs", {}).values():
                for im in node.get("images", []):
                    q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                "type": im.get("type", "output")})
                    data = comfy_req("/view?" + q, raw=True, timeout=60)
                    return Image.open(io.BytesIO(data)).convert("RGBA")
    raise RuntimeError("Timed out waiting for ComfyUI")


def sampler_settings(ckpt, speed):
    n = (ckpt or "").lower()
    fast = speed == "fast" or (speed == "auto" and any(k in n for k in ("turbo", "lightning", "hyper", "lcm", "schnell")))
    if fast:
        return dict(steps=6, cfg=1.6, sampler_name="euler_ancestral", scheduler="sgm_uniform")
    return dict(steps=28, cfg=6.0, sampler_name="dpmpp_2m", scheduler="karras")


def pick_ckpt(name):
    st = comfy_status(force=True)
    if not st.get("ok"):
        raise RuntimeError("AI engine (ComfyUI) isn't running. Start ComfyUI on the server PC, then try again.")
    ck = st.get("checkpoints") or []
    if not ck:
        raise RuntimeError("ComfyUI has no models yet. Put an SDXL or SD 1.5 .safetensors file in ComfyUI/models/checkpoints.")
    return name if name in ck else ck[0]


def base_workflow(ckpt, prompt, negative, settings, seed):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "5": {"class_type": "KSampler", "inputs": dict(seed=seed, denoise=1.0, model=["1", 0], positive=["2", 0],
                                                      negative=["3", 0], latent_image=["4", 0], **settings)},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "halo_studio", "images": ["6", 0]}},
    }


NEG = "blurry, lowres, watermark, text, jpeg artifacts, deformed, extra fingers"


@app.route("/api/ai/generate", methods=["POST"])
def ai_generate():
    P = request.form
    try:
        ckpt = pick_ckpt(P.get("model", ""))
        w = int(P.get("width", 1024)) // 8 * 8
        h = int(P.get("height", 1024)) // 8 * 8
        seed = int(P.get("seed") or np.random.randint(0, 2 ** 31))
        wf = base_workflow(ckpt, P.get("prompt", ""), P.get("negative", NEG), sampler_settings(ckpt, P.get("speed", "auto")), seed)
        wf["4"] = {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
        t0 = time.time()
        img = comfy_run(wf)
    except Exception as e:
        return jsonify(error=str(e)), 503
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["X-AI-Ms"] = str(int((time.time() - t0) * 1000))
    resp.headers["X-AI-Model"] = ckpt
    resp.headers["X-AI-Seed"] = str(seed)
    return resp


@app.route("/api/ai/fill", methods=["POST"])
def ai_fill():
    """Generative fill: image + mask (white = replace). Returns a transparent layer with only the new pixels."""
    P = request.form
    fi, fm = request.files.get("image"), request.files.get("mask")
    if not fi or not fm:
        return jsonify(error="Need image and selection"), 400
    try:
        ckpt = pick_ckpt(P.get("model", ""))
        img = Image.open(fi).convert("RGB")
        mask = Image.open(fm).convert("L").resize(img.size)
        m = np.asarray(mask) > 127
        if not m.any():
            return jsonify(error="Make a selection first"), 400
        ys, xs = np.nonzero(m)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        pad = int(max(x1 - x0, y1 - y0) * 0.35) + 32
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(img.width, x1 + pad), min(img.height, y1 + pad)
        crop = img.crop((x0, y0, x1, y1))
        cmask = mask.crop((x0, y0, x1, y1))
        target = 1024 if "xl" in ckpt.lower() or "sdxl" in ckpt.lower() else 768
        s = target / max(crop.size)
        cw, ch = max(64, int(crop.width * s) // 8 * 8), max(64, int(crop.height * s) // 8 * 8)
        crop_s = crop.resize((cw, ch), Image.LANCZOS)
        mask_s = cmask.resize((cw, ch), Image.LANCZOS).point(lambda v: 255 if v > 127 else 0)
        rgba = crop_s.convert("RGBA")
        rgba.putalpha(ImageOps.invert(mask_s))  # ComfyUI LoadImage: transparent = masked
        name = comfy_upload(rgba, "halo_fill_%s.png" % uuid.uuid4().hex[:8])
        seed = int(P.get("seed") or np.random.randint(0, 2 ** 31))
        prompt = P.get("prompt", "").strip() or "clean natural background, seamless, matching surroundings"
        wf = base_workflow(ckpt, prompt, P.get("negative", NEG), sampler_settings(ckpt, P.get("speed", "auto")), seed)
        wf["8"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf["4"] = {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["8", 0], "vae": ["1", 2], "mask": ["8", 1], "grow_mask_by": 8}}
        t0 = time.time()
        out = comfy_run(wf).convert("RGB").resize(crop.size, Image.LANCZOS)
        feather = cmask.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(6))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        piece = out.convert("RGBA")
        piece.putalpha(feather)
        layer.paste(piece, (x0, y0))
    except Exception as e:
        return jsonify(error=str(e)), 503
    buf = io.BytesIO()
    layer.save(buf, "PNG")
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["X-AI-Ms"] = str(int((time.time() - t0) * 1000))
    resp.headers["X-AI-Model"] = ckpt
    return resp


@app.route("/api/ai/upscale", methods=["POST"])
def ai_upscale():
    f = request.files.get("image")
    if not f:
        return jsonify(error="No image"), 400
    img = load_image(f).convert("RGBA")
    factor = max(1.5, min(4.0, float(request.form.get("factor", 2))))
    t0 = time.time()
    engine = "Classic (Lanczos + sharpen)"
    st = comfy_status(force=True)
    out = None
    if st.get("ok") and st.get("upscalers"):
        try:
            name = comfy_upload(img.convert("RGB"), "halo_up_%s.png" % uuid.uuid4().hex[:8])
            model = st["upscalers"][0]
            wf = {
                "1": {"class_type": "LoadImage", "inputs": {"image": name}},
                "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
                "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
                "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "halo_up", "images": ["3", 0]}},
            }
            up = comfy_run(wf).convert("RGB")
            tw, th = int(img.width * factor), int(img.height * factor)
            out = up.resize((tw, th), Image.LANCZOS).convert("RGBA")
            out.putalpha(img.split()[-1].resize((tw, th), Image.LANCZOS))
            engine = "AI (%s)" % model
        except Exception:
            out = None
    if out is None:
        tw, th = int(img.width * factor), int(img.height * factor)
        out = img.resize((tw, th), Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.6, percent=80, threshold=2))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["X-AI-Engine"] = engine
    resp.headers["X-AI-Ms"] = str(int((time.time() - t0) * 1000))
    return resp


# =====================================================================
# shared network gallery
# =====================================================================
@app.route("/api/shared", methods=["GET"])
def shared_list():
    items = []
    for n in os.listdir(SHARED_DIR):
        p = os.path.join(SHARED_DIR, n)
        if os.path.isfile(p):
            st = os.stat(p)
            items.append({"name": n, "bytes": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda x: -x["mtime"])
    return jsonify(items=items)


@app.route("/api/shared", methods=["POST"])
def shared_add():
    saved = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "") or "file_%s" % uuid.uuid4().hex[:6]
        base, ext = os.path.splitext(name)
        dest = os.path.join(SHARED_DIR, name)
        i = 2
        while os.path.exists(dest):
            dest = os.path.join(SHARED_DIR, "%s_%d%s" % (base, i, ext))
            i += 1
        f.save(dest)
        saved.append(os.path.basename(dest))
    return jsonify(saved=saved)


@app.route("/shared/<path:name>")
def shared_get(name):
    return send_from_directory(SHARED_DIR, secure_filename(name), as_attachment=request.args.get("dl") == "1")


@app.route("/api/shared/<path:name>", methods=["DELETE"])
def shared_del(name):
    p = os.path.join(SHARED_DIR, secure_filename(name))
    if os.path.isfile(p):
        os.remove(p)
    return jsonify(ok=True)


# =====================================================================
def main():
    print("\n  HALO STUDIO %s" % VERSION)
    print("  ------------------------------------------")
    print("  On this PC:      http://localhost:%d" % PORT)
    for ip in lan_ips():
        print("  On your network: http://%s:%d" % (ip, PORT))
    print("  AI engine:       ComfyUI at %s (%s)" % (COMFY_URL, "found" if comfy_status().get("ok") else "not running - optional"))
    print("  Formats:         %s" % ", ".join(available_formats()))
    print("  Press Ctrl+C to stop.\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT, threads=12, max_request_body_size=1024 ** 3)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT, threaded=True)


def already_running():
    """True if Halo Studio is already answering on our port (then we just open it)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/hs.js" % PORT, timeout=1.5) as r:
            return b"__halostudio" in r.read()
    except Exception:
        return False


if __name__ == "__main__":
    want_browser = "--no-browser" not in sys.argv and os.environ.get("HALO_NO_BROWSER") != "1"
    if already_running():
        print("Halo Studio is already running - opening it.")
        if want_browser:
            import webbrowser
            webbrowser.open("http://localhost:%d" % PORT)
        sys.exit(0)
    if want_browser:
        try:
            import webbrowser
            threading.Timer(1.5, lambda: webbrowser.open("http://localhost:%d" % PORT)).start()
        except Exception:
            pass
    main()
