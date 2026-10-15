/* 茲茲 OCR 更新公告：在 index.html / label.html 載入本檔後即可使用。
 * 頁首按鈕：window.TzutzuPatchNotes.mountButton(document.querySelector('.your-header'))
 * 轉檔期間可設定：window.TzutzuBusy = true
 */
(function (root) {
  'use strict';

  var options = { url: 'changelog.json', storageKey: 'tzutzu-patch-notes-last-read', title: '近期功能更新' };
  var CHECK_INTERVAL = 60 * 1000;
  var state = { notes: null, shownAutomatically: false, initialVersion: null, timer: null, dialog: null };

  var icons = {
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.2 2"/></svg>',
    gift: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10h16v10H4zM3 6h18v4H3zM12 6v14M12 6H8.2a2.1 2.1 0 1 1 2.1-2.1C10.3 5.1 12 6 12 6zm0 0h3.8a2.1 2.1 0 1 0-2.1-2.1C13.7 5.1 12 6 12 6z"/></svg>',
    edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17.5V20h2.5L18.2 8.3l-2.5-2.5L4 17.5zM14.8 6.7l2.5 2.5M4 4h7"/></svg>',
    spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3zM19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15z"/></svg>',
    shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 19 6v5c0 4.7-3 8-7 10-4-2-7-5.3-7-10V6l7-3z"/><path d="m8.8 12 2.1 2.1 4.3-4.4"/></svg>',
    image: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9" r="1.5"/><path d="m5.5 17 4.4-4.3 3.1 2.8 2.2-2.1 3.3 3.6"/></svg>'
  };

  function injectStyles() {
    if (document.getElementById('tzutzu-patch-notes-css')) return;
    var style = document.createElement('style');
    style.id = 'tzutzu-patch-notes-css';
    style.textContent = `
      :root { --tz-wine:#701b35; --tz-wine-dark:#4f1027; --tz-orange:#ee7b32; --tz-cream:#fffaf6; --tz-ink:#3b2430; }
      .tz-patch-button{display:inline-grid;place-items:center;width:40px;height:40px;border:0;border-radius:12px;background:#fff3ec;color:var(--tz-wine);cursor:pointer;box-shadow:0 3px 10px #701b3520;transition:transform .18s,background .18s}.tz-patch-button:hover{transform:translateY(-2px);background:#ffe4d4}.tz-patch-button svg{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
      .tz-patch-backdrop{position:fixed;z-index:9998;inset:0;display:grid;place-items:center;padding:20px;background:#25101888;backdrop-filter:blur(4px)}.tz-patch-dialog{width:min(650px,100%);max-height:min(760px,calc(100vh - 40px));display:flex;flex-direction:column;overflow:hidden;border-radius:22px;background:var(--tz-cream);color:var(--tz-ink);box-shadow:0 24px 70px #23091670}.tz-patch-hero{position:relative;flex:none;padding:28px 76px 25px 30px;color:white;background:linear-gradient(125deg,var(--tz-wine-dark),var(--tz-wine) 54%,var(--tz-orange));border-radius:22px 22px 0 0}.tz-patch-kicker{margin:0 0 7px;font:700 11px/1.1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.16em;opacity:.8}.tz-patch-hero h2{margin:0;font:700 27px/1.15 ui-sans-serif,system-ui,sans-serif}.tz-patch-close{position:absolute;right:16px;top:16px;width:44px;height:44px;border:0;border-radius:50%;background:#ffffff28;color:#fff;font-size:28px;line-height:1;cursor:pointer}.tz-patch-date{display:inline-block;margin-top:15px;padding:6px 11px;border-radius:999px;background:#ffffff25;font:600 12px/1 ui-sans-serif,system-ui,sans-serif;white-space:nowrap}.tz-patch-body{min-height:0;overflow:auto;padding:20px 22px 8px}.tz-patch-card{display:grid;grid-template-columns:43px 1fr;gap:12px;padding:15px 13px;margin:0 0 12px;border:1px solid #eeded8;border-radius:15px;background:#fff}.tz-patch-icon{display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:#fff0e9;color:var(--tz-wine)}.tz-patch-icon svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.tz-patch-card h3{display:inline;margin:0 7px 0 0;font:700 16px/1.4 ui-sans-serif,system-ui,sans-serif}.tz-patch-tag{display:inline-block;vertical-align:2px;padding:3px 7px;border-radius:999px;background:#f9e4d8;color:var(--tz-wine);font:700 11px/1 ui-sans-serif,system-ui,sans-serif}.tz-patch-card p{margin:7px 0 0;color:#684c57;font:400 14px/1.6 ui-sans-serif,system-ui,sans-serif;white-space:pre-line}.tz-patch-points{margin:8px 0 0;padding-left:19px;color:#684c57;font:400 14px/1.6 ui-sans-serif,system-ui,sans-serif}.tz-patch-footer{flex:none;padding:10px 22px 23px}.tz-patch-ok{width:100%;padding:14px;border:0;border-radius:13px;background:linear-gradient(100deg,var(--tz-wine),var(--tz-orange));color:#fff;font:700 16px/1 ui-sans-serif,system-ui,sans-serif;cursor:pointer;box-shadow:0 7px 17px #701b3535}.tz-version-toast{position:fixed;z-index:9999;left:50%;bottom:18px;width:min(560px,calc(100% - 28px));transform:translateX(-50%);display:flex;align-items:center;gap:12px;padding:13px 15px;border:1px solid #ffffff66;border-radius:14px;background:var(--tz-wine-dark);color:white;box-shadow:0 12px 35px #25071566;font:600 14px/1.35 ui-sans-serif,system-ui,sans-serif}.tz-version-toast span{flex:1}.tz-version-toast button{flex:0 0 auto;border:0;border-radius:9px;padding:9px 12px;background:#fff;color:var(--tz-wine);font:700 13px/1 ui-sans-serif,system-ui,sans-serif;cursor:pointer}
      @media(max-width:375px){.tz-patch-backdrop{align-items:end;padding:12px}.tz-patch-dialog{width:calc(100vw - 24px);max-height:85vh;border-radius:22px 22px 0 0}.tz-patch-hero{padding:24px 68px 20px 20px;border-radius:22px 22px 0 0}.tz-patch-hero h2{font-size:23px}.tz-patch-close{right:12px;top:12px}.tz-patch-body{padding:14px 12px 5px}.tz-patch-card{grid-template-columns:36px 1fr;gap:9px;padding:12px 10px}.tz-patch-icon{width:34px;height:34px}.tz-patch-footer{padding:8px 12px 14px}.tz-version-toast{bottom:10px;padding:11px 12px;font-size:13px}}
    `;
    document.head.appendChild(style);
  }

  function safeGet(key) { try { return root.localStorage.getItem(key); } catch (_) { return null; } }
  function safeSet(key, value) { try { root.localStorage.setItem(key, value); } catch (_) {} }
  function compareVersions(a, b) {
    var aa = String(a || '').match(/\d+|[a-zA-Z]+/g) || [];
    var bb = String(b || '').match(/\d+|[a-zA-Z]+/g) || [];
    for (var i = 0; i < Math.max(aa.length, bb.length); i++) {
      var x = aa[i] || '0', y = bb[i] || '0', xn = /^\d+$/.test(x), yn = /^\d+$/.test(y);
      if (xn && yn && Number(x) !== Number(y)) return Number(x) > Number(y) ? 1 : -1;
      if (x !== y) return x > y ? 1 : -1;
    }
    return 0;
  }
  function esc(value) { var d = document.createElement('div'); d.textContent = value || ''; return d.innerHTML; }
  function fetchJson(path) { return fetch(path + '?t=' + Date.now(), { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  function latestNoteVersion(notes) { return notes.reduce(function (latest, note) { return compareVersions(note.version, latest) > 0 ? note.version : latest; }, ''); }

  function loadNotes() {
    if (state.notes) return Promise.resolve(state.notes);
    return fetchJson(options.url).then(function (data) {
      state.notes = Array.isArray(data) ? data : (data.entries || []);
      return state.notes;
    });
  }
  function closeDialog() {
    var backdrop = state.dialog || document.querySelector('.tz-patch-backdrop');
    if (backdrop) {
      if (backdrop._tzKeyHandler) document.removeEventListener('keydown', backdrop._tzKeyHandler);
      backdrop.remove();
    }
    state.dialog = null;
  }
  function openNotes(notes) {
    closeDialog();
      var newest = latestNoteVersion(notes);
      var date = notes[0] && notes[0].date ? notes[0].date : '';
      var cards = notes.map(function (note) {
        var points = Array.isArray(note.points) ? '<ul class="tz-patch-points">' + note.points.map(function (p) { return '<li>' + esc(p) + '</li>'; }).join('') + '</ul>' : '';
        return '<article class="tz-patch-card"><div class="tz-patch-icon">' + (icons[note.icon] || icons.spark) + '</div><div><h3>' + esc(note.title) + '</h3><span class="tz-patch-tag">' + esc(note.tag) + '</span><p>' + esc(note.body) + '</p>' + points + '</div></article>';
      }).join('');
      var backdrop = document.createElement('div');
      backdrop.className = 'tz-patch-backdrop';
      backdrop.innerHTML = '<section class="tz-patch-dialog" role="dialog" aria-modal="true" aria-label="' + esc(options.title) + '"><header class="tz-patch-hero"><button class="tz-patch-close" aria-label="關閉">×</button><p class="tz-patch-kicker">PATCH NOTES</p><h2>' + esc(options.title) + '</h2><span class="tz-patch-date">更新至 ' + esc(date) + '</span></header><main class="tz-patch-body">' + cards + '</main><footer class="tz-patch-footer"><button class="tz-patch-ok">好的</button></footer></section>';
      backdrop.addEventListener('click', function (event) { if (event.target === backdrop) closeDialog(); });
      backdrop._tzKeyHandler = function (event) { if (event.key === 'Escape') closeDialog(); };
      document.addEventListener('keydown', backdrop._tzKeyHandler);
      backdrop.querySelector('.tz-patch-close').addEventListener('click', closeDialog);
      backdrop.querySelector('.tz-patch-ok').addEventListener('click', closeDialog);
      document.body.appendChild(backdrop);
      state.dialog = backdrop;
      if (newest) safeSet(options.storageKey, newest);
      return backdrop;
  }
  function showNotes() {
    injectStyles();
    return loadNotes().then(function (notes) {
      // 按鈕 click 尚在冒泡時先不要加入遮罩；即使頁面另有 document click
      // 處理器，也不會把剛開啟的對話框立刻關掉。
      return new Promise(function (resolve) {
        root.setTimeout(function () { resolve(openNotes(notes)); }, 0);
      });
    }).catch(function () { /* 網路暫時不可用時不打斷操作 */ });
  }
  function showUpdateToast() {
    var existing = document.querySelector('.tz-version-toast');
    if (existing) { existing.querySelector('span').textContent = root.TzutzuBusy ? '工作完成後即可更新' : '有新版本可以更新'; existing.querySelector('button').textContent = root.TzutzuBusy ? '稍後更新' : '立即更新'; return; }
    injectStyles();
    var toast = document.createElement('div');
    toast.className = 'tz-version-toast';
    toast.innerHTML = '<span>' + (root.TzutzuBusy ? '轉檔完成後再更新' : '有新版本可以更新 ✨') + '</span><button>' + (root.TzutzuBusy ? '稍後更新' : '立即更新') + '</button>';
    toast.querySelector('button').addEventListener('click', function () { if (!root.TzutzuBusy) root.location.reload(); });
    document.body.appendChild(toast);
  }
  function checkVersion() {
    return fetchJson('version.json').then(function (data) {
      if (!data || !data.version) return;
      if (state.initialVersion === null) { state.initialVersion = data.version; return; }
      if (data.version !== state.initialVersion) showUpdateToast();
    }).catch(function () { /* 離線時安靜略過，下一輪會再檢查 */ });
  }
  // 在 DOMContentLoaded、自動讀取公告之前設定；每頁使用一組公告設定。
  function configure(settings) {
    settings = settings || {};
    ['url', 'storageKey', 'title', 'buttonText'].forEach(function (key) {
      if (typeof settings[key] === 'string' && settings[key]) options[key] = settings[key];
    });
    state.notes = null;
    state.shownAutomatically = false;
  }
  function mountButton(element, settings) {
    if (settings) configure(settings);
    injectStyles();
    if (!element || !element.appendChild) return null;
    var button = document.createElement('button');
    button.className = 'tz-patch-button';
    button.type = 'button';
    button.title = options.title;
    button.setAttribute('aria-label', '開啟' + options.title);
    button.innerHTML = icons.gift;
    if (options.buttonText) button.appendChild(document.createTextNode(options.buttonText));
    button.addEventListener('click', function (event) { event.stopPropagation(); showNotes(); });
    element.appendChild(button);
    return button;
  }
  function start() {
    checkVersion();
    loadNotes().then(function (notes) {
      var newest = latestNoteVersion(notes), lastRead = safeGet(options.storageKey);
      var notesDisabled = /(?:[?&])notes=0(?:&|$)/.test(root.location.search || '');
      if (!notesDisabled && !state.shownAutomatically && newest && (!lastRead || compareVersions(newest, lastRead) > 0)) { state.shownAutomatically = true; showNotes(); }
    }).catch(function () {});
    state.timer = root.setInterval(checkVersion, CHECK_INTERVAL);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) checkVersion(); });
  }

  root.TzutzuPatchNotes = { configure: configure, mountButton: mountButton, show: showNotes, checkVersion: checkVersion };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true }); else start();
})(window);
