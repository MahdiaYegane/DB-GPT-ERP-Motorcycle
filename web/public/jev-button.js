/*
 * Standalone JEV button for Kavir Motor (Liara TypeSafe decision guard).
 *
 * Inserts ONE separate <button> right next to each model selector
 * (the desktop toolbar wraps it in `.model-selector-premium`; legacy
 * headers use `Select.min-w-[120px]`) — never inside the model list
 * next to a model name. Clicking the button opens a small panel with
 * the live guard status from GET /api/v1/liara/jev/status (fail-open
 * to static defaults; the status call is local and costs nothing).
 *
 * Idempotent: processed anchors are marked, rescans and SPA navigation
 * never create duplicates.
 */
(function () {
  if (typeof window === 'undefined' || !window.MutationObserver || !document.querySelectorAll) return;

  var BTN_CLASS = 'jev-standalone-btn';
  var PANEL_CLASS = 'jev-panel';
  var STYLE_ID = 'jev-standalone-style';
  var ICON_SRC = '/models/jev.svg';
  var TYPES_URL = '/api/v1/model/types';
  var STATUS_URL = '/api/v1/liara/jev/status';

  var modelSet = null; // null = not loaded yet
  var statusCache = null;
  var openFor = null;

  function insidePremium(el) {
    var n = el;
    while (n && n !== document) {
      if (n.classList && n.classList.contains('model-selector-premium')) return true;
      n = n.parentNode;
    }
    return false;
  }

  function isVisible(el) {
    try {
      if (!el || !el.getBoundingClientRect) return true;
      var r = el.getBoundingClientRect();
      if (r && (r.width === 0 || r.height === 0)) return false;
      var n = el;
      while (n && n !== document) {
        if (n.hidden) return false;
        if (n.style && (n.style.display === 'none' || n.style.visibility === 'hidden')) return false;
        n = n.parentNode;
      }
      return true;
    } catch (e) {
      return true;
    }
  }

  function visibleButtons() {
    var out = [];
    try {
      var all = document.querySelectorAll('.' + BTN_CLASS);
      for (var i = 0; i < all.length; i++) {
        if (isVisible(all[i])) out.push(all[i]);
      }
    } catch (e) {}
    return out;
  }

  // Exactly one JEV button per page: keep the first visible one, drop the
  // rest. Also cleans up duplicates left by older script versions without
  // requiring a page refresh.
  function dedupAll() {
    try {
      var vis = visibleButtons();
      for (var i = 1; i < vis.length; i++) {
        if (vis[i].parentNode) vis[i].parentNode.removeChild(vis[i]);
      }
    } catch (e) {}
  }

  function ensureStyle() {
    if (document.getElementById && document.getElementById(STYLE_ID)) return;
    var css =
      '.' + BTN_CLASS + '{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;' +
      'border-radius:8px;border:1px solid rgba(0,0,0,0.12);cursor:pointer;font:inherit;font-size:13px;font-weight:600;color:#0f172a;' +
      'background:linear-gradient(180deg,#ffffff 0%,#f9fafb 100%);' +
      'box-shadow:0 1px 2px rgba(0,0,0,0.05), inset 0 1px 0 rgba(255,255,255,1);transition:all .2s ease;white-space:nowrap;}' +
      '.' + BTN_CLASS + ':hover{border-color:rgba(0,0,0,0.2);box-shadow:0 2px 4px rgba(0,0,0,0.06);transform:translateY(-0.5px);}' +
      '.' + BTN_CLASS + ' img{width:18px;height:18px;border-radius:9999px;object-fit:contain;background:#fff;border:1px solid #e5e7eb;}' +
      '.' + BTN_CLASS + ' .jev-dot{width:8px;height:8px;border-radius:9999px;background:#9ca3af;flex-shrink:0;}' +
      '.' + BTN_CLASS + ' .jev-dot.on{background:#22c55e;}' +
      '.dark .' + BTN_CLASS + '{color:#e2e8f0;border-color:rgba(255,255,255,0.1);' +
      'background:linear-gradient(180deg,#2a2b2f 0%,#1e1f24 100%);}' +
      '.' + PANEL_CLASS + '{position:fixed;z-index:9999;width:300px;padding:12px 14px;border-radius:10px;' +
      'background:#fff;color:#0f172a;border:1px solid rgba(0,0,0,0.12);box-shadow:0 8px 24px rgba(0,0,0,0.14);' +
      'font:inherit;font-size:12.5px;line-height:1.7;}' +
      '.dark .' + PANEL_CLASS + '{background:#1e1f24;color:#e2e8f0;border-color:rgba(255,255,255,0.12);}' +
      '.' + PANEL_CLASS + ' .jev-head{display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;margin-bottom:6px;}' +
      '.' + PANEL_CLASS + ' .jev-head img{width:22px;height:22px;border-radius:9999px;}' +
      '.' + PANEL_CLASS + ' .jev-row{display:flex;justify-content:space-between;gap:8px;}' +
      '.' + PANEL_CLASS + ' .jev-row b{font-weight:600;}' +
      '.' + PANEL_CLASS + ' .jev-foot{margin-top:8px;padding-top:6px;border-top:1px solid rgba(0,0,0,0.08);}' +
      '.' + PANEL_CLASS + ' .jev-foot a{color:#7c3aed;text-decoration:none;}';
    var el = document.createElement('style');
    el.id = STYLE_ID;
    el.type = 'text/css';
    if (el.styleSheet) el.styleSheet.cssText = css;
    else el.appendChild(document.createTextNode(css));
    (document.head || document.getElementsByTagName('head')[0] || document.body).appendChild(el);
  }

  function loadModels() {
    if (modelSet !== null || typeof fetch !== 'function') return;
    modelSet = new Set();
    fetch(TYPES_URL)
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var list = (j && (j.data || j.models)) || [];
        for (var i = 0; i < list.length; i++) modelSet.add(String(list[i]));
        scan(document); // second pass: legacy selectors need the loaded model list
      })
      .catch(function () {});
  }

  function loadStatus() {
    if (statusCache || typeof fetch !== 'function') return statusCache;
    statusCache = fetch(STATUS_URL)
      .then(function (r) { return r.json(); })
      .then(function (j) { return (j && j.data) || {}; })
      .catch(function () { return {}; });
    return statusCache;
  }

  function onOff(v) {
    return v ? 'on' : 'off';
  }

  function row(label, value) {
    return '<div class="jev-row"><span>' + label + '</span><b>' + value + '</b></div>';
  }

  function fillPanel(panel, st) {
    st = st || {};
    panel.innerHTML =
      '<div class="jev-head"><img src="' + ICON_SRC + '" alt="jev"><span>Jev — TypeSafe decision guard</span></div>' +
      '<div>Automatic per-turn triage + post-loop verification for the agent. Advisory only, fail-open.</div>' +
      row('Model', st.model || 'typesafe/jev-1.13') +
      row('Auto triage', st.auto_triage_enabled === undefined ? '—' : onOff(st.auto_triage_enabled)) +
      row('Verification', st.verify_enabled === undefined ? '—' : onOff(st.verify_enabled)) +
      row('Fallback report', st.fallback_report_enabled === undefined ? '—' : onOff(st.fallback_report_enabled)) +
      '<div class="jev-foot"><a href="https://docs.liara.ir/ai/quick-start/" target="_blank" rel="noreferrer">Liara AI docs</a></div>';
  }

  function closePanel() {
    var p = document.querySelector ? document.querySelector('.' + PANEL_CLASS) : null;
    if (p && p.parentNode) p.parentNode.removeChild(p);
    openFor = null;
  }

  function togglePanel(btn) {
    if (openFor === btn) {
      closePanel();
      return;
    }
    closePanel();
    var panel = document.createElement('div');
    panel.className = PANEL_CLASS;
    fillPanel(panel, {});
    document.body.appendChild(panel);
    try {
      var r = btn.getBoundingClientRect();
      var w = 300;
      var left = Math.min(Math.max(8, r.left), (window.innerWidth || 1280) - w - 8);
      panel.style.left = left + 'px';
      panel.style.top = r.bottom + 6 + (window.scrollY || 0) + 'px';
    } catch (e) {}
    var p = loadStatus();
    if (p && p.then) {
      p.then(function (st) {
        if (document.body.contains(panel)) fillPanel(panel, st || {});
      });
    }
    openFor = btn;
  }

  function paintDot(btn) {
    var dot = btn.querySelector ? btn.querySelector('.jev-dot') : null;
    if (!dot) return;
    var p = loadStatus();
    if (p && p.then) {
      p.then(function (st) {
        if (st && st.auto_triage_enabled) dot.classList.add('on');
      });
    }
  }

  function makeButton() {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = BTN_CLASS;
    btn.title = 'Jev — TypeSafe decision guard (Liara)';
    var img = document.createElement('img');
    img.src = ICON_SRC;
    img.alt = 'jev';
    var label = document.createElement('span');
    label.textContent = 'JEV';
    var dot = document.createElement('span');
    dot.className = 'jev-dot';
    btn.appendChild(img);
    btn.appendChild(label);
    btn.appendChild(dot);
    btn.addEventListener('click', function (ev) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      togglePanel(btn);
    });
    return btn;
  }

  function placeAfter(anchor) {
    if (!anchor || !anchor.parentNode) return;
    if (anchor.dataset && anchor.dataset.jevBtn) return;
    var next = anchor.nextSibling;
    // nextSibling may be a text node; walk to the next element
    while (next && next.nodeType !== 1) next = next.nextSibling;
    if (next && next.classList && next.classList.contains(BTN_CLASS)) {
      if (anchor.dataset) anchor.dataset.jevBtn = '1';
      return;
    }
    var btn = makeButton();
    anchor.parentNode.insertBefore(btn, anchor.nextSibling);
    if (anchor.dataset) anchor.dataset.jevBtn = '1';
    paintDot(btn);
  }

  function isModelSelect(sel) {
    if (!sel || !sel.classList) return false;
    if (!sel.classList.contains('min-w-[120px]')) return false;
    // Never touch dropdown portals.
    var el = sel;
    while (el) {
      if (el.classList && el.classList.contains('ant-select-dropdown')) return false;
      el = el.parentNode;
    }
    if (modelSet === null || modelSet.size === 0) return false;
    var item = sel.querySelector ? sel.querySelector('.ant-select-selection-item') : null;
    var text = item && (item.textContent || item.innerText || '');
    return !!text && modelSet.has(String(text).trim());
  }

  function scan(root) {
    try {
      dedupAll();
      if (visibleButtons().length > 0) return; // exactly one JEV button per page
      var base = root || document;
      var premiums = base.querySelectorAll('.model-selector-premium');
      for (var i = 0; i < premiums.length; i++) {
        if (isVisible(premiums[i])) {
          placeAfter(premiums[i]);
          return;
        }
      }
      if (modelSet && modelSet.size > 0) {
        var list = base.querySelectorAll('.ant-select');
        for (var j = 0; j < list.length; j++) {
          if (!insidePremium(list[j]) && isModelSelect(list[j]) && isVisible(list[j])) {
            placeAfter(list[j]);
            return;
          }
        }
      }
    } catch (e) {}
  }

  var scanQueued = false;
  function queueScan() {
    if (scanQueued) return;
    scanQueued = true;
    var run = function () {
      scanQueued = false;
      scan(document);
    };
    if (window.requestAnimationFrame) window.requestAnimationFrame(run);
    else window.setTimeout(run, 0);
  }

  function start() {
    ensureStyle();
    loadModels();
    scan(document);
    new MutationObserver(queueScan).observe(document.body, { childList: true, subtree: true });
    if (document.addEventListener) {
      document.addEventListener('click', function (ev) {
        var t = ev && ev.target;
        var inside = false;
        while (t && t !== document) {
          if (t.classList && (t.classList.contains(PANEL_CLASS) || t.classList.contains(BTN_CLASS))) {
            inside = true;
            break;
          }
          t = t.parentNode;
        }
        if (!inside) closePanel();
      });
      document.addEventListener('keydown', function (ev) {
        if (ev && (ev.key === 'Escape' || ev.keyCode === 27)) closePanel();
      });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
