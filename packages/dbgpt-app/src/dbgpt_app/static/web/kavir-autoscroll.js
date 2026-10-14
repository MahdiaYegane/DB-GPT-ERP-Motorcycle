/*
 * Auto-scroll for the agent step timeline (Kavir Motor).
 *
 * Any element marked with `data-kavir-autoscroll` follows new content while
 * the model works: when a new step / thought / answer text is added to the
 * LAST round, the container scrolls to the bottom — but only while the user
 * is already near the bottom. Scrolling up pauses it; scrolling back down
 * (or sending a new question) resumes it. Expanding an older round never
 * moves the view.
 */
(function () {
  if (typeof window === 'undefined' || !window.MutationObserver) return;
  var NEAR_BOTTOM_PX = 120;
  var attached = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

  function attach(el) {
    if (!attached || attached.has(el)) return;
    attached.add(el);
    var stick = true;
    var pending = false;

    el.addEventListener(
      'scroll',
      function () {
        var distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        stick = distance < NEAR_BOTTOM_PX;
      },
      { passive: true },
    );

    function scrollToBottom() {
      if (pending) return;
      pending = true;
      window.requestAnimationFrame(function () {
        pending = false;
        if (stick) el.scrollTop = el.scrollHeight;
      });
    }

    new MutationObserver(function (records) {
      var last = el.lastElementChild;
      var newRound = false;
      var inLastRound = false;
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.target === el && r.addedNodes && r.addedNodes.length) newRound = true;
        if (last && (r.target === last || last.contains(r.target))) inLastRound = true;
      }
      if (newRound) stick = true; // a new question was sent
      if (stick && (newRound || inLastRound)) scrollToBottom();
    }).observe(el, { childList: true, subtree: true, characterData: true });

    scrollToBottom();
  }

  var scanQueued = false;
  function scan() {
    scanQueued = false;
    var nodes = document.querySelectorAll('[data-kavir-autoscroll]');
    for (var i = 0; i < nodes.length; i++) attach(nodes[i]);
  }
  function queueScan() {
    if (scanQueued) return;
    scanQueued = true;
    window.requestAnimationFrame(scan);
  }
  function start() {
    scan();
    new MutationObserver(queueScan).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
