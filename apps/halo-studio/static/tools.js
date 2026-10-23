/* Halo Studio - Batch, Animate, Compress, Network tabs */
'use strict';
(() => {
  const { toast, api, fmtBytes, download, loadImageFile, newCanvas, canvasBlob, shareBlob, $ } = window.Halo;
  const FMT_NAMES = { avif: 'AVIF', jxl: 'JPEG XL', webp: 'WebP', jpeg: 'JPEG', png: 'PNG', png8: 'PNG-8' };
  const isImg = f => f.type.startsWith('image/') || /\.(heic|heif|jxl|avif|webp)$/i.test(f.name);

  function dropzone(el, input, onFiles) {
    ['dragenter', 'dragover'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.remove('dragover'); }));
    el.addEventListener('drop', async e => {
      const items = [...(e.dataTransfer.items || [])];
      // folders: walk them
      const entries = items.map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
      if (entries.some(en => en.isDirectory)) {
        const files = [];
        const walk = en => new Promise(res => {
          if (en.isFile) en.file(f => { files.push(f); res(); }, res);
          else { const r = en.createReader(); const all = []; const read = () => r.readEntries(async ents => { if (!ents.length) { await Promise.all(all.map(walk)); res(); } else { all.push(...ents); read(); } }, res); read(); }
        });
        await Promise.all(entries.map(walk));
        onFiles(files);
      } else onFiles([...e.dataTransfer.files]);
    });
    input.addEventListener('change', () => { onFiles([...input.files]); input.value = ''; });
  }
  function seg(id, cb) {
    const el = $(id);
    el.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; el.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); cb && cb(b.dataset.v); });
    return () => el.querySelector('button.on').dataset.v;
  }
  function bindOut(rangeId, outId) { const r = $(rangeId), o = $(outId); const f = () => o.textContent = r.value; r.addEventListener('input', f); f(); }
  function xhrPost(url, fd, onProgress) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest(); x.open('POST', url); x.responseType = 'blob';
      x.upload.onprogress = e => e.lengthComputable && onProgress(e.loaded / e.total);
      x.onload = async () => {
        if (x.status >= 200 && x.status < 300) resolve(x);
        else { let m = x.statusText; try { m = JSON.parse(await x.response.text()).error || m; } catch (e) { } reject(new Error(m)); }
      };
      x.onerror = () => reject(new Error('Network error'));
      x.send(fd);
    });
  }

  /* ======================= BATCH ======================= */
  let batchFiles = [];
  function batchRender() {
    $('batchCount').textContent = batchFiles.length ? `${batchFiles.length} photo${batchFiles.length > 1 ? 's' : ''} · ${fmtBytes(batchFiles.reduce((a, f) => a + f.size, 0))}` : 'No photos yet';
    const g = $('batchGrid'); g.innerHTML = '';
    batchFiles.slice(0, 120).forEach(f => {
      const d = document.createElement('div');
      if (/^image\/(jpeg|png|webp|gif|avif|bmp)/.test(f.type)) { const u = URL.createObjectURL(f); d.style.backgroundImage = `url(${u})`; }
      d.innerHTML = `<span>${f.name}</span>`; g.appendChild(d);
    });
    if (batchFiles.length > 120) { const d = document.createElement('div'); d.innerHTML = `<span>+${batchFiles.length - 120} more</span>`; g.appendChild(d); }
  }
  dropzone($('batchDrop'), $('batchFiles'), files => { batchFiles.push(...files.filter(isImg)); batchRender(); });
  $('batchClear').onclick = () => { batchFiles = []; batchRender(); };
  $('bSizePresets').onclick = e => { const b = e.target.closest('button'); if (b) $('bSizes').value = b.dataset.v; };
  const bMode = seg('bMode', v => $('bPadRow').classList.toggle('hidden', v !== 'pad'));
  bindOut('bQ', 'bQv');
  $('bGo').onclick = async () => {
    if (!batchFiles.length) return toast('Add some photos first', true);
    const fd = new FormData();
    batchFiles.forEach(f => fd.append('images', f, f.name));
    fd.append('sizes', $('bSizes').value); fd.append('mode', bMode()); fd.append('format', $('bFormat').value);
    fd.append('quality', $('bQ').value); fd.append('pattern', $('bPattern').value); fd.append('watermark', $('bWm').value);
    fd.append('sharpen', $('bSharp').checked ? '1' : '0'); fd.append('sheet', $('bSheet').checked ? '1' : '0'); fd.append('bg', $('bBg').value);
    const prog = $('bProg'), bar = prog.firstElementChild; prog.classList.remove('hidden'); bar.style.width = '0';
    $('bGo').disabled = true; $('bResult').className = 'result'; $('bResult').textContent = 'Uploading…';
    const t0 = Date.now();
    try {
      const x = await xhrPost('/api/batch', fd, p => { bar.style.width = (p * 70) + '%'; if (p >= 1) $('bResult').textContent = 'Resizing on the server…'; });
      bar.style.width = '100%';
      download(x.response, 'thumbnails.zip');
      const n = x.getResponseHeader('X-Thumb-Count'), errs = +x.getResponseHeader('X-Error-Count');
      $('bResult').textContent = `✓ ${n} thumbnails in ${((Date.now() - t0) / 1000).toFixed(1)}s · ${fmtBytes(x.response.size)} zip${errs ? ` · ${errs} file(s) skipped (see _errors.txt)` : ''}`;
    } catch (e) { $('bResult').className = 'result err'; $('bResult').textContent = e.message; }
    finally { $('bGo').disabled = false; setTimeout(() => prog.classList.add('hidden'), 1500); }
  };

  /* ======================= ANIMATE ======================= */
  let frames = []; // {canvas, url}
  const aLoop = seg('aLoop');
  ['aFps:aFpsV', 'aW:aWV', 'aCol:aColV', 'aVfps:aVfpsV'].forEach(p => { const [a, b] = p.split(':'); bindOut(a, b); });
  $('aFormat').onchange = () => $('gifOnly').classList.toggle('hidden', $('aFormat').value !== 'gif');
  function frameFromSource(src, w, h) {
    const max = 1280, s = Math.min(1, max / Math.max(w, h));
    const c = newCanvas(Math.round(w * s), Math.round(h * s)); c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
    return c;
  }
  function addFrameCanvas(c) { frames.push({ canvas: c, url: c.toDataURL('image/jpeg', 0.6) }); }
  function renderFrames() {
    $('frameCount').textContent = frames.length + ' frame' + (frames.length === 1 ? '' : 's');
    const s = $('frameStrip'); s.innerHTML = '';
    frames.forEach((f, i) => {
      const d = document.createElement('div'); d.className = 'frame';
      d.innerHTML = `<span class="n">${i + 1}</span><img src="${f.url}"><div class="fb"><button data-a="l" title="Move left">◀</button><button data-a="d" title="Duplicate">⧉</button><button data-a="x" title="Remove">✕</button><button data-a="r" title="Move right">▶</button></div>`;
      d.querySelector('.fb').onclick = e => {
        const a = e.target.dataset.a; if (!a) return;
        if (a === 'x') frames.splice(i, 1);
        if (a === 'd') frames.splice(i + 1, 0, { ...f });
        if (a === 'l' && i > 0) [frames[i - 1], frames[i]] = [frames[i], frames[i - 1]];
        if (a === 'r' && i < frames.length - 1) [frames[i + 1], frames[i]] = [frames[i], frames[i + 1]];
        renderFrames();
      };
      s.appendChild(d);
    });
  }
  const video = $('animVideo');
  dropzone($('animDrop'), $('animFiles'), async files => {
    const vid = files.find(f => f.type.startsWith('video/'));
    if (vid) {
      video.src = URL.createObjectURL(vid); $('videoBox').classList.remove('hidden');
      video.onloadedmetadata = () => { $('vStart').value = 0; $('vEnd').value = Math.min(3, video.duration).toFixed(1); };
      return;
    }
    for (const f of files.filter(isImg).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))) {
      try { const b = await loadImageFile(f); addFrameCanvas(frameFromSource(b, b.width, b.height)); } catch (e) { toast(f.name + ': ' + e.message, true); }
    }
    renderFrames();
  });
  $('vMarkIn').onclick = () => $('vStart').value = video.currentTime.toFixed(1);
  $('vMarkOut').onclick = () => $('vEnd').value = video.currentTime.toFixed(1);
  $('vGrab').onclick = async () => {
    const st = +$('vStart').value, en = Math.min(+$('vEnd').value, video.duration), fps = +$('aVfps').value;
    if (!(en > st)) return toast('End must be after start', true);
    const n = Math.min(300, Math.round((en - st) * fps));
    if ((en - st) * fps > 300) toast('Capped at 300 frames - lower the sample rate for longer clips');
    video.pause(); $('vGrab').disabled = true;
    for (let i = 0; i < n; i++) {
      await new Promise(r => { video.onseeked = r; video.currentTime = st + i / fps; });
      addFrameCanvas(frameFromSource(video, video.videoWidth, video.videoHeight));
      $('vGrab').textContent = `Grabbing ${i + 1}/${n}`;
    }
    $('vGrab').disabled = false; $('vGrab').textContent = 'Grab frames';
    $('aFps').value = Math.min(30, fps); $('aFps').dispatchEvent(new Event('input'));
    renderFrames(); toast(n + ' frames grabbed');
  };
  $('fromLayers').onclick = () => {
    const E = window.Halo.E;
    if (!E.doc) return toast('Open an image with layers in the editor first', true);
    E.doc.layers.forEach(l => { const c = newCanvas(E.doc.w, E.doc.h), x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height); x.drawImage(l.canvas, 0, 0); addFrameCanvas(frameFromSource(c, c.width, c.height)); });
    renderFrames(); toast('Each layer is now a frame');
  };
  $('framesReverse').onclick = () => { frames.reverse(); renderFrames(); };
  $('framesClear').onclick = () => { frames = []; renderFrames(); };

  function captioned(c) {
    const top = $('aCapTop').value.trim().toUpperCase(), bot = $('aCapBot').value.trim().toUpperCase();
    if (!top && !bot) return c;
    const o = newCanvas(c.width, c.height), x = o.getContext('2d'); x.drawImage(c, 0, 0);
    const fs = Math.round(c.width / 11);
    x.font = `900 ${fs}px Impact,Haettenschweiler,"Arial Black",sans-serif`; x.textAlign = 'center';
    x.lineWidth = Math.max(2, fs / 8); x.strokeStyle = '#000'; x.fillStyle = '#fff'; x.lineJoin = 'round';
    const put = (t, y, base) => { x.textBaseline = base; x.strokeText(t, c.width / 2, y, c.width * 0.94); x.fillText(t, c.width / 2, y, c.width * 0.94); };
    if (top) put(top, fs * 0.25, 'top');
    if (bot) put(bot, c.height - fs * 0.25, 'bottom');
    return o;
  }
  // live preview loop
  let pf = 0, pt = 0;
  (function loop(ts) {
    requestAnimationFrame(loop);
    if (!frames.length || !document.querySelector('#tab-anim.on')) return;
    const fps = +$('aFps').value;
    if (ts - pt < 1000 / fps) return; pt = ts;
    const seq = $('aBoom').checked && frames.length > 2 ? [...frames.keys(), ...[...frames.keys()].slice(1, -1).reverse()] : [...frames.keys()];
    pf = (pf + 1) % seq.length;
    const f = frames[seq[pf]] || frames[0], c = $('animPreview'), w = +$('aW').value;
    const h = Math.round(f.canvas.height * w / f.canvas.width);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    c.getContext('2d').drawImage(captioned(f.canvas), 0, 0, w, h);
  })(0);
  $('aGo').onclick = async () => {
    if (!frames.length) return toast('Add some frames first', true);
    const fd = new FormData();
    for (let i = 0; i < frames.length; i++) fd.append('frames', await canvasBlob(captioned(frames[i].canvas)), `f${i}.png`);
    fd.append('format', $('aFormat').value); fd.append('fps', $('aFps').value); fd.append('width', $('aW').value);
    fd.append('loop', aLoop()); fd.append('colors', $('aCol').value); fd.append('dither', $('aDither').checked ? '1' : '0');
    fd.append('boomerang', $('aBoom').checked ? '1' : '0'); fd.append('quality', 75);
    $('aGo').disabled = true; $('aResult').className = 'result'; $('aResult').textContent = 'Building…';
    try {
      const j = await (await api('/api/animate', fd)).json();
      $('animOut').classList.remove('hidden'); $('animOutImg').src = j.data;
      $('animOutInfo').textContent = `· ${fmtBytes(j.bytes)} · ${j.frames} frames · ${j.width}×${j.height}`;
      const blob = await (await fetch(j.data)).blob();
      const name = 'halo-animation.' + j.ext;
      $('animDl').href = URL.createObjectURL(blob); $('animDl').download = name;
      $('animShare').onclick = () => shareBlob(blob, name);
      $('aResult').textContent = '✓ ' + fmtBytes(j.bytes);
    } catch (e) { $('aResult').className = 'result err'; $('aResult').textContent = e.message; }
    finally { $('aGo').disabled = false; }
  };

  /* ======================= COMPRESS ======================= */
  let cFile = null, cOrigUrl = null;
  const cGoal = seg('cGoal', v => { $('cQRow').classList.toggle('hidden', v !== 'q'); $('cKbRow').classList.toggle('hidden', v === 'q'); });
  bindOut('cQ', 'cQv');
  async function setCompress(file) {
    cFile = file; $('cResults').innerHTML = '';
    let url;
    try { url = URL.createObjectURL(file); await new Promise((r, j) => { const i = new Image(); i.onload = r; i.onerror = j; i.src = url; }); }
    catch (e) { const b = await loadImageFile(file); const c = newCanvas(b.width, b.height); c.getContext('2d').drawImage(b, 0, 0); url = URL.createObjectURL(await canvasBlob(c)); }
    cOrigUrl = url; $('cmpA').src = url; $('cmpB').src = url; $('compareBox').classList.remove('hidden');
    $('cDrop').querySelector('b').textContent = file.name + ' · ' + fmtBytes(file.size);
    $('cGo').click();
  }
  dropzone($('cDrop'), $('cFile'), files => { const f = files.find(isImg); if (f) setCompress(f); });
  $('cmpSlider').oninput = () => { const v = $('cmpSlider').value; $('cmpBWrap').style.clipPath = `inset(0 0 0 ${v}%)`; $('cmpWrap').style.setProperty('--split', v + '%'); };
  $('cGo').onclick = async () => {
    if (!cFile) return toast('Drop an image first', true);
    const fmts = [...document.querySelectorAll('#cFormats input:checked')].map(i => i.value);
    if (!fmts.length) return toast('Pick at least one format', true);
    const fd = new FormData(); fd.append('image', cFile, cFile.name); fd.append('formats', fmts.join(','));
    fd.append('quality', $('cQ').value); fd.append('max_side', $('cMax').value);
    if (cGoal() === 'size') fd.append('target_kb', $('cKb').value);
    $('cGo').disabled = true; $('cGo').textContent = 'Encoding…';
    try {
      const j = await (await api('/api/compress', fd)).json();
      const box = $('cResults'); box.innerHTML = '';
      j.results.forEach((r, i) => {
        const d = document.createElement('div'); d.className = 'rcard';
        if (r.error) { d.innerHTML = `<div class="fmt">${FMT_NAMES[r.format]}</div><div class="meta">${r.error}</div>`; box.appendChild(d); return; }
        const pct = Math.round((1 - r.bytes / j.original_bytes) * 100);
        d.innerHTML = `${i === 0 ? '<span class="best">Smallest</span>' : ''}<div class="fmt">${FMT_NAMES[r.format]}</div>
          <div class="sz">${fmtBytes(r.bytes)}</div>
          <div class="save ${pct < 0 ? 'worse' : ''}">${pct >= 0 ? pct + '% smaller' : -pct + '% bigger'}</div>
          <div class="meta">${r.quality ? 'quality ' + r.quality + ' · ' : ''}${j.width}×${j.height} · ${r.ms} ms</div>
          <div class="row2"><button class="btn" data-a="cmp">Compare</button><button class="btn primary" data-a="dl">Download</button></div>`;
        const name = cFile.name.replace(/\.[^.]+$/, '') + '.' + r.ext;
        d.onclick = async e => {
          const a = e.target.dataset.a;
          const blob = await (await fetch(r.data)).blob();
          if (a === 'dl') return download(blob, name);
          box.querySelectorAll('.rcard').forEach(x => x.classList.remove('on')); d.classList.add('on');
          $('cmpB').onerror = () => toast('This browser cannot preview ' + FMT_NAMES[r.format] + ' - the file is still fine to download');
          $('cmpB').src = r.data; $('cmpLabel').textContent = FMT_NAMES[r.format] + ' · ' + fmtBytes(r.bytes);
        };
        box.appendChild(d);
      });
      const best = box.querySelector('.rcard'); if (best) best.querySelector('[data-a=cmp]').click();
      toast(`Original ${fmtBytes(j.original_bytes)} → best ${j.results[0] ? fmtBytes(j.results[0].bytes) : '?'}`);
    } catch (e) { toast(e.message, true); }
    finally { $('cGo').disabled = false; $('cGo').textContent = 'Compress'; }
  };

  /* ======================= NETWORK ======================= */
  async function loadGallery() {
    try {
      const j = await (await api('/api/shared')).json();
      const g = $('gallery'); g.innerHTML = '';
      if (!j.items.length) g.innerHTML = '<p class="hint">Nothing shared yet. Use "Send to → Network gallery" in the editor, or drop files above.</p>';
      j.items.forEach(it => {
        const d = document.createElement('div'); d.className = 'gitem';
        const isI = /\.(png|jpe?g|gif|webp|avif|bmp|svg)$/i.test(it.name);
        const u = '/shared/' + encodeURIComponent(it.name);
        d.innerHTML = `<div class="im" style="${isI ? `background-image:url('${u}')` : ''}">${isI ? '' : (it.name.split('.').pop() || '').toUpperCase()}</div>
          <div class="nm" title="${it.name}">${it.name}</div><div class="mt">${fmtBytes(it.bytes)} · ${new Date(it.mtime * 1000).toLocaleString()}</div>
          <div class="acts">${isI ? '<button class="btn" data-a="edit">Edit</button>' : ''}<a class="btn" href="${u}?dl=1">Get</a><button class="btn" data-a="del">✕</button></div>`;
        d.onclick = async e => {
          const a = e.target.dataset.a;
          if (a === 'del') { if (confirm('Delete ' + it.name + ' for everyone?')) { await fetch('/api/shared/' + encodeURIComponent(it.name), { method: 'DELETE' }); loadGallery(); } }
          if (a === 'edit') { const b = await (await fetch(u)).blob(); window.Halo.openImage(await createImageBitmap(b), it.name); window.Halo.showTab('edit'); }
        };
        g.appendChild(d);
      });
    } catch (e) { }
  }
  dropzone($('sDrop'), $('sFiles'), async files => {
    const fd = new FormData(); files.forEach(f => fd.append('files', f, f.name));
    try { await api('/api/shared', fd); toast('Shared ' + files.length + ' file(s)'); loadGallery(); } catch (e) { toast(e.message, true); }
  });
  document.addEventListener('halo-shared', loadGallery);
  document.addEventListener('halo-tab', e => { if (e.detail === 'shared') loadGallery(); });
  setInterval(() => { if (document.querySelector('#tab-shared.on')) loadGallery(); }, 10000);

  let qrFor = '';
  document.addEventListener('halo-info', e => {
    const I = e.detail;
    // formats
    const order = ['avif', 'jxl', 'webp', 'jpeg', 'png8', 'png'];
    const fsel = $('bFormat'), cur = fsel.value;
    fsel.innerHTML = order.filter(f => I.formats.includes(f)).map(f => `<option value="${f}">${{ avif: 'AVIF - smallest, modern', jxl: 'JPEG XL - next-gen', webp: 'WebP - small, works everywhere', jpeg: 'JPEG - classic', png8: 'PNG-8 - tiny graphics', png: 'PNG - lossless' }[f]}</option>`).join('');
    fsel.value = cur || 'jpeg';
    const cf = $('cFormats');
    if (!cf.children.length) cf.innerHTML = order.filter(f => I.formats.includes(f)).map(f => `<label class="check"><input type="checkbox" value="${f}" ${f !== 'png' ? 'checked' : ''}> ${FMT_NAMES[f]}</label>`).join('');
    // network panel
    const urls = I.lan_urls.length ? I.lan_urls : [location.origin];
    $('lanBox').innerHTML = urls.map(u => `<a class="lan" href="${u}" target="_blank">${u}</a>`).join('');
    if (qrFor !== urls[0]) { qrFor = urls[0]; fetch('/api/qr?text=' + encodeURIComponent(urls[0])).then(r => r.ok ? r.text() : '').then(t => { $('qrBox').innerHTML = t ? t + '<p class="hint">Scan with a phone camera</p>' : ''; }); }
    const c = I.comfy || {};
    const accel = (I.onnx_accel || []).map(p => p.replace('ExecutionProvider', '')).join(', ');
    $('serverInfo').innerHTML = [
      ['Computer', I.host], ['CPU threads', I.cpu_threads],
      ['Formats', I.formats.map(f => FMT_NAMES[f]).join(', ') + (I.heif ? ', HEIC in' : '')],
      ['Background removal', I.rembg ? 'Ready' + (accel ? ' (' + accel + ')' : ' (CPU)') : 'Not installed - run Install-AI-Extras'],
      ['Gen-AI engine', c.ok ? `ComfyUI · ${c.device || ''}` : 'ComfyUI not running'],
      ['AI memory', c.ok && c.vram_gb ? c.vram_gb + ' GB' : '-'],
      ['Image models', c.ok ? (c.checkpoints || []).length : '-'],
      ['Upscale models', c.ok ? (c.upscalers || []).length : '-'],
    ].map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join('');
  });

  /* ======================= receive from editor ======================= */
  window.HaloTools = {
    receive: async (target, blob, name) => {
      const file = new File([blob], name, { type: blob.type || 'image/png' });
      if (target === 'anim') { const b = await createImageBitmap(blob); addFrameCanvas(frameFromSource(b, b.width, b.height)); renderFrames(); toast('Added as frame ' + frames.length); }
      if (target === 'compress') setCompress(file);
      if (target === 'batch') { batchFiles.push(file); batchRender(); }
      if (target === 'print' && window.HaloTools.setPrintImage) window.HaloTools.setPrintImage(file);
    },
  };
})();
