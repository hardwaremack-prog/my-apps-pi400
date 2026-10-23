/* Tidy Suite people picker.
   Loaded first by every app. Keeps each person's saved work separate in this browser by
   giving their storage keys a private prefix, and shows a small "who's using this" menu.
   The first person keeps the suite's original keys, so work saved before this existed stays theirs. */
(function () {
  'use strict';
  var LS;
  try { LS = window.localStorage; LS.getItem('suite:user'); } catch (e) { return; }
  var P = Storage.prototype;
  if (P.__tidyUsers) return;
  var raw = {
    get: P.getItem, set: P.setItem, rm: P.removeItem, key: P.key, clear: P.clear,
    len: Object.getOwnPropertyDescriptor(P, 'length').get
  };
  var USERS = 'suite:users', CUR = 'suite:user';
  function rget(k) { return raw.get.call(LS, k); }
  function rset(k, v) { raw.set.call(LS, k, v); }
  function allKeys() { var out = [], n = raw.len.call(LS); for (var i = 0; i < n; i++) out.push(raw.key.call(LS, i)); return out; }

  /* ---------- server mode ----------
     When the suite is opened through Start Tidy Suite, each person's work is kept in the
     "Saved work" folder on that computer. This browser keeps a copy so the apps stay quick,
     loads the latest from the folder when an app opens, and sends every change back. */
  var server = null;
  function xhr(method, url, body) {
    try { var x = new XMLHttpRequest(); x.open(method, url, false); if (body != null) x.setRequestHeader('Content-Type', 'text/plain;charset=utf-8'); x.send(body == null ? null : body); return x; } catch (e) { return null; }
  }
  if (/^https?:$/.test(location.protocol)) {
    var hello = xhr('GET', '/api/hello');
    if (hello && hello.status === 200) { try { var hj = JSON.parse(hello.responseText); if (hj && hj.suite === 'tidy') server = hj; } catch (e) {} }
  }

  var users, fromServer = false;
  if (server) { var ur = xhr('GET', '/api/users'); if (ur && ur.status === 200) { try { users = JSON.parse(ur.responseText); fromServer = Array.isArray(users) && users.length > 0; } catch (e) {} } }
  if (!fromServer) { try { users = JSON.parse(rget(USERS)); } catch (e) {} }
  if (!Array.isArray(users) || !users.length) users = [{ id: 'main', name: 'Me', color: 0 }];
  if (server && !fromServer) xhr('PUT', '/api/users', JSON.stringify(users));
  if (server) rset(USERS, JSON.stringify(users));
  var cur = rget(CUR) || 'main';
  if (!users.some(function (u) { return u.id === cur; })) cur = users[0].id;
  var prefix = cur === 'main' ? '' : 'u:' + cur + ':';
  function mine(k) { return prefix ? k.indexOf(prefix) === 0 : !(k.indexOf('u:') === 0 || k.indexOf('suite:') === 0); }
  function myKeys() { return allKeys().filter(mine).map(function (k) { return k.slice(prefix.length); }); }
  function isLS(s) { return s === LS; }

  var PEND = 'suite:pending:' + cur, syncState = server ? 'ok' : 'local';
  function pending() { try { return JSON.parse(rget(PEND)) || {}; } catch (e) { return {}; } }
  function setPending(p) { if (Object.keys(p).length) rset(PEND, JSON.stringify(p)); else raw.rm.call(LS, PEND); }
  function iurl(k) { return '/api/item?user=' + encodeURIComponent(cur) + '&key=' + encodeURIComponent(k); }
  if (server) {
    var pend = pending(), failed = false;
    // 1. Send changes that didn't reach the folder last time (the computer was off, say).
    Object.keys(pend).forEach(function (k) {
      var v = rget(prefix + k), x = v == null ? xhr('DELETE', iurl(k)) : xhr('PUT', iurl(k), v);
      if (x && x.status === 200) delete pend[k]; else failed = true;
    });
    setPending(pend);
    // 2. Load this person's latest work from the folder.
    var d = xhr('GET', '/api/data?user=' + encodeURIComponent(cur));
    if (d && d.status === 200) {
      var obj = null; try { obj = JSON.parse(d.responseText); } catch (e) {}
      if (obj) {
        allKeys().filter(mine).forEach(function (k) { var kk = k.slice(prefix.length); if (!(kk in obj) && !(kk in pend)) raw.rm.call(LS, k); });
        Object.keys(obj).forEach(function (k) { if (!(k in pend)) { try { raw.set.call(LS, prefix + k, obj[k]); } catch (e) {} } });
      }
    } else if (d && d.status === 404) {
      // First time this person uses server mode: move what this browser has into the folder.
      myKeys().forEach(function (k) { var x = xhr('PUT', iurl(k), rget(prefix + k)); if (!(x && x.status === 200)) { pend[k] = 1; failed = true; } });
      setPending(pend);
    } else failed = true;
    if (failed) syncState = 'pending';
  }
  var syncTimer = null;
  function queue(k) { if (!server) return; var p = pending(); p[k] = 1; setPending(p); setSync('saving'); clearTimeout(syncTimer); syncTimer = setTimeout(flush, 500); }
  function flush(leaving) {
    var ks = Object.keys(pending()); if (!ks.length) { setSync('ok'); return; }
    Promise.all(ks.map(function (k) {
      var v = rget(prefix + k);
      return fetch(iurl(k), { method: v == null ? 'DELETE' : 'PUT', body: v == null ? undefined : v, headers: { 'Content-Type': 'text/plain;charset=utf-8' }, keepalive: !!leaving && (v == null || v.length < 60000) })
        .then(function (r) { if (!r.ok) throw new Error('not saved'); if (rget(prefix + k) === v) { var q = pending(); delete q[k]; setPending(q); } });
    })).then(function () { var left = Object.keys(pending()).length; setSync(left ? 'saving' : 'ok'); if (left) { clearTimeout(syncTimer); syncTimer = setTimeout(flush, 500); } },
      function () { setSync('pending'); clearTimeout(syncTimer); syncTimer = setTimeout(flush, 8000); });
  }
  if (server) {
    window.addEventListener('pagehide', function () { flush(true); });
    if (syncState === 'pending') setTimeout(flush, 3000);
  }
  function setSync(st) {
    syncState = st; var el = document.getElementById('tsu-sync'); if (!el) return;
    el.className = 'tsu-sync ' + st; el.title = syncText();
  }
  function syncText() {
    if (!server) return 'Saved in this browser only';
    var pc = server.computer ? ' on ' + server.computer : '';
    return { ok: 'Saved to the Tidy Suite folder' + pc, saving: 'Saving to the Tidy Suite folder' + pc + '…', pending: 'Can\'t reach the Tidy Suite computer. Your work is safe here and will be sent when it\'s back' }[syncState];
  }

  P.getItem = function (k) { return isLS(this) ? raw.get.call(this, prefix + k) : raw.get.call(this, k); };
  P.setItem = function (k, v) { if (!isLS(this)) return raw.set.call(this, k, v); raw.set.call(this, prefix + k, v); queue(String(k)); };
  P.removeItem = function (k) { if (!isLS(this)) return raw.rm.call(this, k); raw.rm.call(this, prefix + k); queue(String(k)); };
  P.key = function (i) { if (!isLS(this)) return raw.key.call(this, i); var k = myKeys()[i]; return k === undefined ? null : k; };
  P.clear = function () { if (!isLS(this)) return raw.clear.call(this); var me = this; myKeys().forEach(function (k) { raw.rm.call(me, prefix + k); queue(k); }); };
  try { Object.defineProperty(P, 'length', { configurable: true, get: function () { return isLS(this) ? myKeys().length : raw.len.call(this); } }); } catch (e) {}
  P.__tidyUsers = true;

  function saveUsers() { rset(USERS, JSON.stringify(users)); if (server) xhr('PUT', '/api/users', JSON.stringify(users)); }
  function switchTo(id) { rset(CUR, id); location.reload(); }
  // When someone switches people in another open suite tab, follow along.
  window.addEventListener('storage', function (e) { if (e.key === CUR && e.newValue !== cur) location.reload(); });

  window.TidySuiteUsers = { mode: function () { return server ? 'server' : 'standalone'; }, current: function () { return users.filter(function (u) { return u.id === cur; })[0]; }, list: function () { return users.slice(); } };

  /* ---------- the menu ---------- */
  var COLORS = ['#2B63B5', '#D9446F', '#2C8A57', '#E07B12', '#7A3E8C', '#138A8A', '#B8323A', '#4D5B70'];
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function initial(n) { return (String(n || '?').trim().charAt(0) || '?').toUpperCase(); }
  function me() { return users.filter(function (u) { return u.id === cur; })[0]; }

  var css = '' +
    '#tsu{--tsu-bg:#fff;--tsu-ink:#1b2530;--tsu-muted:#5a6876;--tsu-line:#d8e0e7;--tsu-soft:#eef2f5;--tsu-bad:#c2453a;' +
    'position:fixed;left:calc(12px + env(safe-area-inset-left,0px));bottom:calc(12px + env(safe-area-inset-bottom,0px));z-index:2147483000;' +
    'font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--tsu-ink)}' +
    '@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) #tsu{--tsu-bg:#1a222c;--tsu-ink:#e8eef4;--tsu-muted:#9aa8b6;--tsu-line:#2f3b49;--tsu-soft:#222c38;--tsu-bad:#ee7a6e}}' +
    ':root[data-theme="dark"] #tsu{--tsu-bg:#1a222c;--tsu-ink:#e8eef4;--tsu-muted:#9aa8b6;--tsu-line:#2f3b49;--tsu-soft:#222c38;--tsu-bad:#ee7a6e}' +
    '#tsu *{box-sizing:border-box;font:inherit;color:inherit}' +
    '#tsu .tsu-btn{display:inline-flex;align-items:center;gap:8px;max-width:190px;background:var(--tsu-bg);border:1px solid var(--tsu-line);border-radius:99px;padding:4px 12px 4px 4px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.14);font-weight:600}' +
    '#tsu .tsu-btn:focus-visible,#tsu button:focus-visible,#tsu input:focus-visible{outline:2px solid ' + '#E9A23B' + ';outline-offset:2px}' +
    '#tsu .tsu-av{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:800;font-size:13px;flex:none}' +
    '#tsu .tsu-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '#tsu .tsu-caret{opacity:.6;font-size:11px}' +
    '#tsu .tsu-sync{width:8px;height:8px;border-radius:50%;background:#2C8A57;flex:none}#tsu .tsu-sync.saving{background:#E39A00}#tsu .tsu-sync.pending{background:#C2453A}' +
    '#tsu .tsu-menu{position:absolute;left:0;bottom:calc(100% + 8px);width:min(290px,calc(100vw - 24px));background:var(--tsu-bg);border:1px solid var(--tsu-line);border-radius:14px;box-shadow:0 12px 34px rgba(0,0,0,.22);padding:8px;display:flex;flex-direction:column;gap:2px}' +
    '#tsu .tsu-head{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--tsu-muted);font-weight:700;padding:6px 8px 4px}' +
    '#tsu .tsu-row{display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:10px;border:0;background:none;cursor:pointer;width:100%;text-align:left}' +
    '#tsu .tsu-row:hover{background:var(--tsu-soft)}' +
    '#tsu .tsu-row[aria-current="true"]{background:var(--tsu-soft);font-weight:700}' +
    '#tsu .tsu-row small{margin-left:auto;color:var(--tsu-muted);font-size:12px}' +
    '#tsu .tsu-sep{height:1px;background:var(--tsu-line);margin:6px 2px}' +
    '#tsu .tsu-act{border:0;background:none;padding:7px 8px;border-radius:10px;cursor:pointer;text-align:left;width:100%;font-weight:600}' +
    '#tsu .tsu-act:hover{background:var(--tsu-soft)}' +
    '#tsu .tsu-act.bad{color:var(--tsu-bad)}' +
    '#tsu form{display:flex;gap:6px;padding:4px}' +
    '#tsu input{flex:1;min-width:0;border:1px solid var(--tsu-line);background:var(--tsu-soft);border-radius:9px;padding:7px 9px}' +
    '#tsu .tsu-go{border:0;border-radius:9px;padding:7px 12px;font-weight:700;cursor:pointer;background:#2B63B5;color:#fff}' +
    '#tsu .tsu-note{color:var(--tsu-muted);font-size:12.5px;padding:4px 8px 6px;margin:0}' +
    '@media print{#tsu{display:none!important}}';

  function build() {
    if (document.getElementById('tsu')) return;
    var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    var box = document.createElement('div'); box.id = 'tsu'; document.body.appendChild(box);
    var mode = 'list', open = false, armed = false;

    function render() {
      var u = me();
      var h = '<button class="tsu-btn" id="tsu-btn" aria-haspopup="true" aria-expanded="' + open + '" title="Who\'s using the Tidy Suite">' +
        '<span class="tsu-av" style="background:' + COLORS[u.color % COLORS.length] + '">' + esc(initial(u.name)) + '</span>' +
        '<span class="tsu-name">' + esc(u.name) + '</span>' + (server ? '<i id="tsu-sync" class="tsu-sync ' + syncState + '" title="' + esc(syncText()) + '"></i>' : '') + '<span class="tsu-caret" aria-hidden="true">▲</span></button>';
      if (open) {
        h += '<div class="tsu-menu" role="menu">';
        if (mode === 'list') {
          h += '<div class="tsu-head">Who\'s using this?</div>';
          users.forEach(function (x) {
            h += '<button class="tsu-row" role="menuitem" data-u="' + x.id + '" aria-current="' + (x.id === cur) + '"><span class="tsu-av" style="background:' + COLORS[x.color % COLORS.length] + '">' + esc(initial(x.name)) + '</span>' + esc(x.name) + (x.id === cur ? '<small>Using now</small>' : '') + '</button>';
          });
          h += '<div class="tsu-sep"></div><button class="tsu-act" data-act="add">Add a person</button><button class="tsu-act" data-act="rename">Rename ' + esc(u.name) + '</button>';
          if (users.length > 1) h += '<button class="tsu-act bad" data-act="remove">' + (armed ? 'Tap again to delete ' + esc(u.name) + '\'s work' : 'Remove ' + esc(u.name)) + '</button>';
          h += '<p class="tsu-note">' + (server ? '<b>Server mode.</b> ' + esc(syncText()) + '. Everyone can pick up their work on any device that opens the suite.' : '<b>Standalone.</b> Each person\'s work is saved separately in this browser. Open the suite with Start Tidy Suite to use it from any device.') + '</p>';
        } else {
          h += '<div class="tsu-head">' + (mode === 'add' ? 'Add a person' : 'Rename') + '</div><form id="tsu-form"><input id="tsu-in" maxlength="24" placeholder="Name" aria-label="Name" value="' + (mode === 'rename' ? esc(u.name) : '') + '" required><button class="tsu-go" type="submit">' + (mode === 'add' ? 'Add' : 'Save') + '</button></form>' +
            '<button class="tsu-act" data-act="back">Back</button>';
        }
        h += '</div>';
      }
      box.innerHTML = h;
      var inp = document.getElementById('tsu-in'); if (inp) { inp.focus(); inp.select(); }
    }
    box.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('button') : null; if (!t) return;
      if (t.id === 'tsu-btn') { open = !open; mode = 'list'; armed = false; render(); return; }
      if (t.dataset.u) { if (t.dataset.u !== cur) switchTo(t.dataset.u); else { open = false; render(); } return; }
      var a = t.dataset.act;
      if (a === 'add' || a === 'rename') { mode = a; render(); }
      else if (a === 'back') { mode = 'list'; render(); }
      else if (a === 'remove') {
        if (!armed) { armed = true; render(); setTimeout(function () { if (armed) { armed = false; render(); } }, 4000); return; }
        var gone = cur, pre = gone === 'main' ? null : 'u:' + gone + ':';
        allKeys().forEach(function (k) { if (pre ? k.indexOf(pre) === 0 : !(k.indexOf('u:') === 0 || k.indexOf('suite:') === 0)) raw.rm.call(LS, k); });
        raw.rm.call(LS, 'suite:pending:' + gone); if (server) xhr('DELETE', '/api/data?user=' + encodeURIComponent(gone));
        users = users.filter(function (x) { return x.id !== gone; }); saveUsers(); switchTo(users[0].id);
      }
    });
    box.addEventListener('submit', function (e) {
      e.preventDefault(); var n = document.getElementById('tsu-in').value.trim(); if (!n) return;
      if (mode === 'add') {
        var id = 'p' + Math.random().toString(36).slice(2, 8);
        var used = users.map(function (x) { return x.color; }), c = 0; while (used.indexOf(c) >= 0 && c < COLORS.length - 1) c++;
        users.push({ id: id, name: n, color: c }); saveUsers(); switchTo(id);
      } else { me().name = n; saveUsers(); mode = 'list'; render(); }
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && open) { open = false; render(); document.getElementById('tsu-btn').focus(); } });
    document.addEventListener('click', function (e) { if (open && (e.composedPath ? e.composedPath().indexOf(box) < 0 : !box.contains(e.target))) { open = false; render(); } });
    render();
  }
  if (document.body) build(); else document.addEventListener('DOMContentLoaded', build);
})();
