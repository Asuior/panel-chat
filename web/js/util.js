/* ============================================================
   util.js —— 通用小工具（无依赖）
   ============================================================ */
(function () {
  'use strict';

  /* HTML 转义 */
  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* 时间戳/ISO 转 “HH:MM” 或 “昨天” 等 */
  function fmtTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    function p(n) { return (n < 10 ? '0' : '') + n; }
    var now = new Date();
    var sameDay = d.toDateString() === now.toDateString();
    var hm = p(d.getHours()) + ':' + p(d.getMinutes());
    if (sameDay) return hm;
    var yest = new Date(now); yest.setDate(now.getDate() - 1);
    if (d.toDateString() === yest.toDateString()) return '昨天 ' + hm;
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hm;
  }

  /* 相对时间 */
  function fmtRel(iso) {
    if (!iso) return '';
    var t = new Date(iso).getTime();
    if (isNaN(t)) return '';
    var diff = Date.now() - t;
    if (diff < 60e3) return '刚刚';
    if (diff < 3600e3) return Math.floor(diff / 60e3) + ' 分钟前';
    if (diff < 86400e3) return Math.floor(diff / 3600e3) + ' 小时前';
    if (diff < 7 * 86400e3) return Math.floor(diff / 86400e3) + ' 天前';
    return fmtTime(iso);
  }

  /* 当前 ISO 时间戳 */
  function nowIso() { return new Date().toISOString(); }

  /* UUID（无 crypto 环境回退） */
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      var v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  /* Toast */
  function showToast(text, type, ms) {
    type = type || 'info';
    ms = ms || 3200;
    var wrap = document.getElementById('toastWrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'toast-wrap';
      wrap.id = 'toastWrap';
      document.body.appendChild(wrap);
    }
    var el = document.createElement('div');
    el.className = 'toast ' + (type === 'error' ? 'error' : type === 'success' ? 'success' : '');
    el.textContent = text;
    wrap.appendChild(el);
    setTimeout(function () {
      el.classList.add('out');
      setTimeout(function () { el.remove(); }, 350);
    }, ms);
  }

  /* 简单防抖 */
  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var ctx = this, args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, wait);
    };
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* ---------------- 自绘确认弹窗（替代 window.confirm） ---------------- */
  function confirmDialog(message, opts) {
    opts = opts || {};
    var modal = document.getElementById('confirmModal');
    // 页面没有确认弹窗（例如纯浏览器预览之外的场景）时退回原生 confirm
    if (!modal || !document.getElementById('confirmOk')) {
      return Promise.resolve(window.confirm(String(message)));
    }
    var titleEl = document.getElementById('confirmTitle');
    var textEl = document.getElementById('confirmText');
    var okBtn = document.getElementById('confirmOk');
    var cancelBtn = document.getElementById('confirmCancel');
    var okText = opts.okText || '删除';

    titleEl.textContent = opts.title || '确认操作';
    textEl.textContent = String(message == null ? '' : message);
    okBtn.textContent = okText;
    if (opts.danger === false) {
      okBtn.classList.remove('confirm-ok');
      okBtn.style.cssText = '';
    } else {
      okBtn.classList.add('confirm-ok');
    }

    modal.classList.add('show');

    return new Promise(function (resolve) {
      function done(val) {
        modal.classList.remove('show');
        modal.removeEventListener('click', onMask);
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKey);
        resolve(val);
      }
      function onOk() { done(true); }
      function onCancel() { done(false); }
      function onMask(e) { if (e.target === modal) done(false); }
      function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); done(false); } }
      modal.addEventListener('click', onMask);
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      document.addEventListener('keydown', onKey);
    });
  }

  window.U = { esc: esc, fmtTime: fmtTime, fmtRel: fmtRel, nowIso: nowIso,
               uuid: uuid, showToast: showToast, debounce: debounce, $: $, $$: $$,
               confirm: confirmDialog };
})();
