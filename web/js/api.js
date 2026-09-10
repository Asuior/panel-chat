/* ============================================================
   api.js —— pywebview.api 调用封装
   ------------------------------------------------------------
   统一入口 window.AI.call('方法名', ...args)。
   在普通浏览器中打开页面时自动降级为本地 Mock，
   方便脱离 Python 调试界面样式。
   ============================================================ */
(function () {
  'use strict';

  function bridgeAvailable() {
    return !!(window.pywebview && window.pywebview.api);
  }

  /* ---------------- 本地 Mock（纯浏览器预览用） ---------------- */
  var Mock = {
    bootstrap: function () {
      return Promise.resolve({
        settings: {
          provider: 'mock',
          temperature: 0.7,
          theme: 'glassmorphism',
          selected_prompt_groups: [],
          user: { name: '我', avatar: null },
          assistant: { name: 'AI 助手', avatar: null },
          chat: { width: 420, height: 700, min_width: 500, min_height: 600 },
          ball: { size: 70 },
          ball_light_effect: true,
          file_processing: { mode: 'overwrite', instruction: '', max_chars: 80000 },
          hotkeys: { toggle_ball: 'ctrl+alt+q', toggle_chat: 'ctrl+alt+w' }
        },
        providers: [
          { id: 'mock', name: '本地模拟（离线测试）', description: '浏览器预览用假数据',
            params: {}, multimodal_supported: true, multimodal: true }
        ],
        themes: [{ id: 'glassmorphism', name: '毛玻璃 · 默认', css_url: '', description: '' }],
        conversations: [],
        prompts: { groups: [] },
        active_theme: { id: 'glassmorphism', name: '毛玻璃 · 默认', css_url: '' },
        server_time: Date.now() / 1000
      });
    },
    chat: function (providerId, messages) {
      var last = messages.filter(function (m) { return m.role === 'user'; }).pop();
      return Promise.resolve('【浏览器预览模式】这是模拟回复。\n\n收到：' +
        (last ? String(last.content).slice(0, 80) : ''));
    },
    get_providers: function () { return Mock.bootstrap().then(function (b) { return b.providers; }); },
    get_themes: function () { return Mock.bootstrap().then(function (b) { return b.themes; }); },
    get_conversations: function () { return Mock.bootstrap().then(function (b) { return b.conversations; }); },
    get_prompts: function () { return Mock.bootstrap().then(function (b) { return b.prompts; }); },
    get_settings: function () { return Mock.bootstrap().then(function (b) { return b.settings; }); },
    get_active_theme: function () { return Mock.bootstrap().then(function (b) { return b.active_theme; }); },
    compose_system_prompt: function () { return Promise.resolve(''); },
    set_selected_prompt_groups: function () { return Promise.resolve(true); },
    save_settings: function (s) { console.log('[mock] save_settings', s); return Promise.resolve(s); },
    save_prompts: function () { return Promise.resolve(true); },
    save_conversation: function () { return Promise.resolve({ messages: [] }); },
    process_file: function () { return Promise.resolve({ ok: false, error: '浏览器预览模式不处理文件' }); }
  };

  /* ---------------- 调用入口 ---------------- */
  async function call(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    if (!bridgeAvailable()) {
      var mockFn = Mock[name];
      if (!mockFn) {
        console.warn('[api] 非 pywebview 环境，无方法 ' + name);
        return null;
      }
      return await mockFn.apply(null, args);
    }
    var fn = window.pywebview.api[name];
    if (typeof fn !== 'function') {
      throw new Error('后端未暴露方法：' + name);
    }
    try {
      return await fn.apply(null, args);
    } catch (err) {
      // pywebview 的 rejection 是 Error，message 即 Python 端异常文本
      var msg = err && err.message ? err.message : String(err);
      var e = new Error(msg);
      e.pyStack = err && err.stack;
      throw e;
    }
  }

  window.AI = {
    call: call,
    available: bridgeAvailable,
    providers: function () { return call('get_providers'); }
  };
})();
