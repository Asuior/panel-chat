/* ============================================================
   theme.js —— 主题加载与背景应用
   ------------------------------------------------------------
   · 后端 bootstrap/apply_theme 返回主题 {css_url,...}，
     以 file:///…/themes/<名>/theme.css 动态注入 <link>；
   · 监听自定义事件 theme-changed（托盘切换主题时后端推送）。
   ============================================================ */
(function () {
  'use strict';

  function applyTheme(theme) {
    if (!theme || !theme.css_url) return;
    var id = 'alice-theme-link';
    var link = document.getElementById(id);
    if (!link) {
      link = document.createElement('link');
      link.rel = 'stylesheet';
      link.id = id;
      document.head.appendChild(link);
    }
    if (link.href === theme.css_url) return;
    link.href = theme.css_url;
    console.log('[theme] 应用主题', theme.name || theme.id);
  }

  function applyBackground(bgPath) {
    var body = document.body;
    if (!bgPath) {
      body.classList.remove('bg-image');
      body.style.removeProperty('--bg-url');
      return;
    }
    // 路径为 web/ 相对路径（assets/backgrounds/xxx.png）或绝对路径。
    //
    // 必须转成**绝对 URL**：--bg-url 的 var() 消费点在 web/css/base.css 里，
    // 而 CSS 中的相对 url() 是相对**样式表自身**解析的，不是相对文档 ——
    // 直接塞 "assets/backgrounds/x.png" 会被解析成 web/css/assets/... 从而
    // 404：背景层什么也不画（连内置渐变也不会退回，因为 background-image
    // 已经被替换掉了），表现就是「设置了背景图却毫无变化」。
    var url = String(bgPath).replace(/\\/g, '/');
    try {
      url = new URL(url, document.baseURI).href;
    } catch (e) {
      console.warn('[theme] 背景图 URL 解析失败，按原样使用：', url);
    }
    body.style.setProperty('--bg-url', 'url("' + url + '")');
    body.classList.add('bg-image');
  }

  function init(boot) {
    if (boot) {
      if (boot.active_theme) applyTheme(boot.active_theme);
      if (boot.settings) applyBackground(boot.settings.background_image);
    }
    window.addEventListener('theme-changed', function (ev) {
      applyTheme(ev.detail || {});
    });
  }

  window.Theme = { applyTheme: applyTheme, applyBackground: applyBackground, init: init };
})();
