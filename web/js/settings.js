/* ============================================================
   settings.js —— 设置弹层（外观 / AI 接口 / 提示词 / 通用）
   ------------------------------------------------------------
   依赖 window.AppState（由 chat.js 提供）与 window.AI / window.U。
   所有修改即时落盘（后端 save_settings / save_prompts / …）。

   · 提示词模型 v2：分组(groups) → 分组内多条提示词(prompts[])
     启用粒度仍是分组；启用顺序 = 拼接顺序。
   · AI 接口自定义参数：由插件注册时声明的 params 字典驱动渲染，
     填写值保存到 settings.provider_params，后端以 extra_body 传给 chat。
   ============================================================ */
(function () {
  'use strict';

  var U = window.U;
  var S = {};       // 元素缓存
  var state = null; // AppState

  /* ---------------- 工具 ---------------- */
  function persist(patch) {
    return AI.call('save_settings', patch).then(function (all) {
      if (all && window.AppState && AppState.onSettings) AppState.onSettings(all);
      return all;
    }).catch(function (err) {
      U.showToast('保存失败：' + err.message, 'error');
      throw err;
    });
  }

  function readFileAsDataURL(file) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () { resolve(fr.result); };
      fr.onerror = function () { reject(new Error('读取文件失败')); };
      fr.readAsDataURL(file);
    });
  }

  function avatarHtml(avatar, name) {
    if (avatar && avatar.indexOf('data:') === 0) {
      return '<img src="' + avatar + '" alt="">';
    }
    return U.esc((name || '?').charAt(0).toUpperCase());
  }

  function uid(prefix) {
    return prefix + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  /* ---------------- 渲染：主题 ---------------- */
  function renderThemes() {
    var grid = S.themeGrid;
    if (!grid) return;
    grid.innerHTML = '';
    (state.themes || []).forEach(function (t) {
      var div = document.createElement('div');
      div.className = 'tcard' + (t.id === state.settings.theme ? ' sel' : '');
      div.innerHTML =
        '<div class="swatch" style="background:linear-gradient(135deg,var(--accent-1),var(--accent-2))"></div>' +
        '<div class="tinfo"><div class="nm">' + U.esc(t.name || t.id) + '</div>' +
        '<div class="au">' + U.esc(t.author || '') + '</div></div>';
      div.addEventListener('click', function () {
        AI.call('apply_theme', t.id).then(function (theme) {
          state.settings.theme = t.id;
          if (window.AppState) AppState.onSettings(state.settings);
          if (window.Theme) Theme.applyTheme(theme);
          renderThemes();
          U.showToast('已切换到主题「' + (t.name || t.id) + '」', 'success');
        }).catch(function (err) { U.showToast(err.message, 'error'); });
      });
      grid.appendChild(div);
    });
  }

  /* ---------------- 渲染：AI 接口与自定义参数 ---------------- */
  function renderProviders() {
    var wrap = S.providerCards;
    if (!wrap) return;
    wrap.innerHTML = '<label>选择当前使用的 AI 接口</label>';
    var current = state.settings.provider;
    (state.providers || []).forEach(function (p) {
      var card = document.createElement('div');
      card.className = 'pcard' + (p.id === current ? ' sel' : '');
      card.innerHTML = '<span class="radio"></span><div style="flex:1;min-width:0">' +
        '<div class="pname">' + U.esc(p.name) +
        ' <code style="font-size:11px;opacity:.6">' + U.esc(p.id) + '</code></div>' +
        (p.description ? '<div class="pdesc">' + U.esc(p.description) + '</div>' : '') +
        '</div>';
      card.addEventListener('click', function () {
        persist({ provider: p.id }).then(function () {
          state.settings.provider = p.id;
          if (window.AppState) AppState.onSettings(state.settings);
          renderProviders();
          U.showToast('已切换 AI 接口：' + p.name, 'success');
        }).catch(function () {});
      });
      wrap.appendChild(card);
    });
    renderParams();
    renderMultimodal();
    if (S.providerHelp) {
      S.providerHelp.innerHTML = '各接口的自定义参数由插件声明（注册时的 params 字典）；' +
        '填写后将以 extra_body 传给该接口的 chat(messages, temperature, extra_body)。';
    }
  }

  /* 当前 provider 的自定义参数表单 */
  function renderParams() {
    var box = S.paramForm;
    if (!box) return;
    var pid = state.settings.provider;
    var provider = (state.providers || []).find(function (p) { return p.id === pid; });
    var schema = (provider && provider.params) || {};
    var keys = Object.keys(schema);
    box.innerHTML = '<div class="param-form"><label>当前接口自定义参数（可选）</label>' +
      (keys.length ? '' : '<div class="muted">该接口无需额外参数。</div>') + '</div>';
    if (!keys.length) return;

    var saved = (state.settings.provider_params &&
                 state.settings.provider_params[pid]) || {};
    var form = box.querySelector('.param-form');

    function saveVal(key, val) {
      var paramsAll = JSON.parse(JSON.stringify(state.settings.provider_params || {}));
      if (!paramsAll[pid]) paramsAll[pid] = {};
      paramsAll[pid][key] = val;
      state.settings.provider_params = paramsAll;
      persist({ provider_params: paramsAll });
    }

    keys.forEach(function (key) {
      var spec = schema[key] || {};
      var type = spec.type || 'string';
      var isSensitive = !!spec.sensitive;
      var cur;
      if (type === 'boolean') {
        // false 是合法取值，不能像字符串那样被空值判断吞掉
        cur = (saved[key] !== undefined && saved[key] !== null)
          ? !!saved[key]
          : (spec.default !== undefined ? !!spec.default : false);
      } else {
        cur = (saved[key] !== undefined && saved[key] !== null && saved[key] !== '')
          ? saved[key] : (spec.default !== undefined ? spec.default : '');
      }

      var item = document.createElement('div');
      item.className = 'param-item';
      item.innerHTML =
        '<label>' + U.esc(spec.name || key) + ' <small>' + U.esc(key) + '</small></label>' +
        (spec.description ? '<div class="pdesc">' + U.esc(spec.description) + '</div>' : '') +
        '<div class="pp-ctl"></div>';
      var ctl = item.querySelector('.pp-ctl');

      if (type === 'boolean') {
        // 复用设置页的开关控件（.switch 样式见 settings.css），不需要额外 JS 状态
        var sw = document.createElement('span');
        sw.className = 'switch';
        sw.innerHTML = '<input type="checkbox"' + (cur ? ' checked' : '') +
          '><span class="track"></span>';
        ctl.appendChild(sw);
        var swBox = sw.querySelector('input');
        swBox.addEventListener('change', function () { saveVal(key, swBox.checked); });
      } else if (type === 'number') {
        var num = document.createElement('input');
        num.type = 'number';
        num.step = 'any';
        num.value = cur;
        num.addEventListener('change', function () {
          var v = num.value;
          saveVal(key, v === '' ? '' : Number(v));
        });
        ctl.appendChild(num);
      } else if (type === 'json') {
        // 结构化参数（如 extra_body）：多行文本框便于核对括号与引号；
        // 不是合法 JSON 时由后端报错并阻止本次请求，前端不给假阳性校验
        var ta = document.createElement('textarea');
        ta.rows = 3;
        ta.spellcheck = false;
        ta.placeholder = spec.placeholder || '{}';
        ta.value = cur;
        ta.addEventListener('change', function () { saveVal(key, ta.value); });
        ctl.appendChild(ta);
      } else {
        var input = document.createElement('input');
        // 敏感字段（声明了 sensitive 或字段名含 key/secret/token/password）用密码框
        input.type = (isSensitive || /key|secret|token|password/i.test(key))
          ? 'password' : 'text';
        input.value = cur;
        input.addEventListener('change', function () { saveVal(key, input.value); });
        ctl.appendChild(input);
      }
      form.appendChild(item);
    });
  }

  /* 当前 provider 的多模态（图片输入）开关 */
  function renderMultimodal() {
    var box = S.multimodalBox;
    if (!box) return;
    var pid = state.settings.provider;
    var provider = (state.providers || []).find(function (p) { return p.id === pid; });
    box.innerHTML = '';
    if (!provider || !provider.multimodal_supported) {
      box.innerHTML = '<div class="muted">该接口不支持图片输入（插件未声明 supports_images）。</div>';
      return;
    }
    var inner = document.createElement('div');
    inner.style.cssText = 'border-top:1px dashed var(--glass-border);padding-top:12px;';
    inner.innerHTML =
      '<div style="display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap">' +
      '<div style="min-width:0"><div style="font-size:12.5px;font-weight:600">🖼 多模态图片输入</div>' +
      '<div class="pdesc" style="font-size:11px;color:var(--text-secondary);margin-top:3px">' +
      '开启后输入框支持拖拽 / 粘贴 / 按钮上传图片，图片以 base64 随多模态消息发送。</div></div>' +
      '<span class="switch"><input type="checkbox" id="swMultimodal"' +
      (provider.multimodal ? ' checked' : '') + '><span class="track"></span></span></div>';
    box.appendChild(inner);
    var sw = inner.querySelector('#swMultimodal');
    sw.addEventListener('change', function () {
      AI.call('set_provider_multimodal', pid, sw.checked).then(function () {
        return AI.call('get_providers');
      }).then(function (list) {
        state.providers = list || [];
        if (window.AppState && AppState.updateProviders) AppState.updateProviders(state.providers);
        renderMultimodal();
        U.showToast(sw.checked ? '已开启图片输入' : '已关闭图片输入', 'success');
      }).catch(function (err) { U.showToast(err.message, 'error'); });
    });
  }

  function renderTemp() {
    if (!S.tempRange) return;
    S.tempRange.value = state.settings.temperature != null ? state.settings.temperature : 0.7;
    S.tempVal.textContent = S.tempRange.value;
  }

  /* ================ 提示词（分组内多条） ================ */
  function groups() { return (state.prompts && state.prompts.groups) || []; }
  function order()  { return (state.settings.selected_prompt_groups || []).slice(); }

  function saveGroups() {
    return AI.call('save_prompts', { groups: groups() }).catch(function (err) {
      U.showToast('提示词保存失败：' + err.message, 'error');
    });
  }
  function saveOrder() {
    return AI.call('set_selected_prompt_groups', order()).then(function () {
      state.settings.selected_prompt_groups = order();
    }).catch(function (err) {
      U.showToast('顺序保存失败：' + err.message, 'error');
    });
  }

  function renderPrompts() {
    var box = S.promptGroups, ord = S.enabledOrder;
    if (!box || !ord) return;
    box.innerHTML = '';
    var gs = groups();
    var orderIds = order();

    gs.forEach(function (g) {
      var enabledPos = orderIds.indexOf(g.id);
      var prompts = g.prompts || [];
      var item = document.createElement('div');
      item.className = 'pg-item';
      var promptsHtml = '';
      if (!prompts.length) {
        promptsHtml = '<div class="pg-empty">该分组还没有提示词，点下方“＋ 添加提示词”。</div>';
      } else {
        promptsHtml = '<div class="pg-prompts">' + prompts.map(function (p, i) {
          return '<div class="pg-prompt">' +
            '<div class="pp-head">' +
            '<span class="pp-idx">#' + (i + 1) + '</span><span class="grow"></span>' +
            '<button class="pgp-del" data-gid="' + g.id + '" data-pid="' + p.id +
            '" title="删除该条提示词">删除</button></div>' +
            '<textarea data-gid="' + g.id + '" data-pid="' + p.id +
            '" placeholder="第 ' + (i + 1) + ' 条提示词内容…">' +
            U.esc(p.content || '') + '</textarea>' +
            '</div>';
        }).join('') + '</div>';
      }
      item.innerHTML =
        '<div class="pg-head">' +
        '  <span class="switch"><input type="checkbox" data-gid="' + g.id + '"' +
        (enabledPos >= 0 ? ' checked' : '') + '><span class="track"></span></span>' +
        '  <input class="pg-name" data-gid="' + g.id + '" value="' + U.esc(g.name || '') + '">' +
        '  <span class="pg-count">' +
        (enabledPos >= 0 ? '启用序 #' + (enabledPos + 1) : '未启用') +
        ' · ' + prompts.length + ' 条</span>' +
        '  <button class="btn-icon pg-del" data-gid="' + g.id + '" title="删除整个分组">🗑</button>' +
        '</div>' +
        promptsHtml +
        '<button class="btn pg-add-prompt" data-gid="' + g.id + '">＋ 添加提示词</button>';
      box.appendChild(item);
    });

    // ---- 事件绑定 ----
    U.$$('input[type=checkbox][data-gid]', box).forEach(function (cb) {
      cb.addEventListener('change', function () {
        var gid = cb.getAttribute('data-gid');
        var ordNow = order();
        if (cb.checked) {
          if (ordNow.indexOf(gid) < 0) ordNow.push(gid);
        } else {
          ordNow = ordNow.filter(function (x) { return x !== gid; });
        }
        state.settings.selected_prompt_groups = ordNow;
        saveOrder().then(renderPrompts);
      });
    });
    U.$$('.pg-name', box).forEach(function (inp) {
      inp.addEventListener('change', function () {
        var g = groups().find(function (x) { return x.id === inp.getAttribute('data-gid'); });
        if (g) { g.name = inp.value; saveGroups(); }
      });
    });
    var debouncedSave = U.debounce(saveGroups, 500);
    U.$$('textarea[data-pid]', box).forEach(function (ta) {
      ta.addEventListener('input', function () {
        var gid = ta.getAttribute('data-gid'), pid = ta.getAttribute('data-pid');
        var g = groups().find(function (x) { return x.id === gid; });
        if (!g) return;
        var p = (g.prompts || []).find(function (x) { return x.id === pid; });
        if (p) { p.content = ta.value; debouncedSave(); }
      });
    });
    U.$$('.pgp-del', box).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var gid = btn.getAttribute('data-gid'), pid = btn.getAttribute('data-pid');
        var g = groups().find(function (x) { return x.id === gid; });
        if (!g) return;
        var idx = (g.prompts || []).findIndex(function (x) { return x.id === pid; });
        if (idx < 0) return;
        U.confirm('删除第 ' + (idx + 1) + ' 条提示词？', { title: '删除提示词', okText: '删除' })
          .then(function (ok) {
            if (!ok) return;
            g.prompts.splice(idx, 1);
            saveGroups().then(renderPrompts);
          });
      });
    });
    U.$$('.pg-del', box).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var gid = btn.getAttribute('data-gid');
        var g = groups().find(function (x) { return x.id === gid; });
        if (!g) return;
        var count = (g.prompts || []).length;
        U.confirm('删除分组「' + g.name + '」？' +
          (count ? '该分组下的 ' + count + ' 条提示词将一并删除，此操作不可恢复。'
                 : '此操作不可恢复。'),
          { title: '删除分组', okText: '删除' }).then(function (ok) {
          if (!ok) return;
          state.prompts.groups = groups().filter(function (x) { return x.id !== gid; });
          state.settings.selected_prompt_groups = order().filter(function (x) { return x !== gid; });
          saveGroups().then(function () { return saveOrder(); }).then(renderPrompts);
        });
      });
    });
    U.$$('.pg-add-prompt', box).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var gid = btn.getAttribute('data-gid');
        var g = groups().find(function (x) { return x.id === gid; });
        if (!g) return;
        g.prompts = g.prompts || [];
        g.prompts.push({ id: uid('p'), content: '', created_at: new Date().toISOString() });
        saveGroups().then(function () {
          renderPrompts();
          var tas = U.$$('#promptGroups textarea[data-pid]');
          if (tas.length) tas[tas.length - 1].focus();
        });
      });
    });

    // ---- 启用顺序面板 + 统计 ----
    renderOrderPanel(orderIds, gs);
    var stat = U.$('#promptStat');
    if (stat) {
      var total = 0;
      orderIds.forEach(function (gid) {
        var g = gs.find(function (x) { return x.id === gid; });
        if (g) total += (g.prompts || []).filter(function (p) {
          return p.content && String(p.content).trim();
        }).length;
      });
      stat.textContent = '共 ' + gs.length + ' 个分组 · 已启用 ' + orderIds.length +
        ' 个分组 · 将拼接 ' + total + ' 条提示词';
    }
  }

  function renderOrderPanel(orderIds, gs) {
    var ord = S.enabledOrder;
    ord.innerHTML = '';
    if (!orderIds.length) {
      ord.innerHTML = '<div class="muted">还没有启用的分组 —— 在上方卡片打开“开关”即可加入，顺序即拼接顺序。</div>';
      return;
    }
    orderIds.forEach(function (gid, i) {
      var g = gs.find(function (x) { return x.id === gid; });
      if (!g) return;
      var count = (g.prompts || []).length;
      var it = document.createElement('div');
      it.className = 'eo-item';
      it.innerHTML = '<span class="idx">' + (i + 1) + '</span>' +
        '<span class="eo-name">' + U.esc(g.name || '未命名') +
        ' <small>(' + count + ' 条)</small></span>' +
        '<button class="eo-up" title="上移">▲</button>' +
        '<button class="eo-down" title="下移">▼</button>' +
        '<button class="eo-del" title="从启用列表移除（不删除分组）">✕</button>';
      it.querySelector('.eo-up').addEventListener('click', function () {
        if (i === 0) return;
        var a = orderIds; var t = a[i]; a[i] = a[i - 1]; a[i - 1] = t;
        saveOrder().then(renderPrompts);
      });
      it.querySelector('.eo-down').addEventListener('click', function () {
        if (i === orderIds.length - 1) return;
        var a = orderIds; var t = a[i]; a[i] = a[i + 1]; a[i + 1] = t;
        saveOrder().then(renderPrompts);
      });
      it.querySelector('.eo-del').addEventListener('click', function () {
        state.settings.selected_prompt_groups = orderIds.filter(function (x) { return x !== gid; });
        saveOrder().then(renderPrompts);
      });
      ord.appendChild(it);
    });
  }

  /* ---------------- 通用面板 ---------------- */
  function renderGeneral() {
    var s = state.settings;
    S.swStartHidden.checked = s.start_hidden !== false;
    var hk = s.hotkeys || {};
    S.hotkeyBox.textContent =
      '显示/隐藏窗口：' + (hk.toggle_chat || '未设置');
  }

  /* ---------------- 外观面板 ---------------- */
  function renderAppearance() {
    var s = state.settings;
    S.userName.value = (s.user && s.user.name) || '我';
    S.aiName.value = (s.assistant && s.assistant.name) || 'AI 助手';
    S.userAvatarLg.innerHTML = avatarHtml(s.user && s.user.avatar, S.userName.value);
    S.aiAvatarLg.innerHTML = avatarHtml(s.assistant && s.assistant.avatar, S.aiName.value);
  }

  /* ---------------- 整体刷新 ---------------- */
  function refreshAll() {
    renderThemes(); renderProviders(); renderTemp();
    renderPrompts(); renderGeneral(); renderAppearance();
  }

  /* ---------------- 弹层开关 ---------------- */
  function open() {
    S.mask.classList.add('show');
    refreshAll();
  }
  function close() { S.mask.classList.remove('show'); }

  /* ---------------- 头像上传 ---------------- */
  function bindAvatarUpload(fileInput, lgEl, kind, resetBtn) {
    lgEl.addEventListener('click', function () { fileInput.click(); });
    fileInput.addEventListener('change', function () {
      var f = fileInput.files && fileInput.files[0];
      if (!f) return;
      readFileAsDataURL(f).then(function (dataUrl) {
        return AI.call('upload_avatar', kind, dataUrl).then(function () { return dataUrl; });
      }).then(function (dataUrl) {
        // 同步本地状态：否则预览与聊天区仍读旧头像，重启前不生效
        var key = kind === 'user' ? 'user' : 'assistant';
        state.settings[key] = state.settings[key] || {};
        state.settings[key].avatar = dataUrl;
        U.showToast('头像已更新', 'success');
        if (window.AppState && AppState.refreshUserInfo) AppState.refreshUserInfo();
        renderAppearance();
      }).catch(function (err) { U.showToast(err.message, 'error'); })
        .finally(function () { fileInput.value = ''; });
    });
    if (resetBtn) resetBtn.addEventListener('click', function () {
      persist(kind === 'user' ? { user: { avatar: null } } : { assistant: { avatar: null } })
        .then(function () {
          if (window.AppState && AppState.refreshUserInfo) AppState.refreshUserInfo();
          renderAppearance();
        }).catch(function () {});
    });
  }

  /* ---------------- 初始化 ---------------- */
  function init(st) {
    state = st;
    S.mask = U.$('#settingsModal');
    S.themeGrid = U.$('#themeGrid');
    S.providerCards = U.$('#providerCards');
    S.paramForm = U.$('#paramForm');
    S.multimodalBox = U.$('#multimodalBox');
    S.providerHelp = U.$('#providerHelp');
    S.tempRange = U.$('#tempRange');
    S.tempVal = U.$('#tempVal');
    S.promptGroups = U.$('#promptGroups');
    S.enabledOrder = U.$('#enabledOrder');
    S.swStartHidden = U.$('#swStartHidden');
    S.hotkeyBox = U.$('#hotkeyBox');
    S.userName = U.$('#userName');
    S.aiName = U.$('#aiName');
    S.userAvatarLg = U.$('#userAvatarLg');
    S.aiAvatarLg = U.$('#aiAvatarLg');
    S.bgFile = U.$('#bgFile');
    S.panels = U.$('#settingsPanels');

    // 关闭：点遮罩 / Esc
    S.mask.addEventListener('click', function (e) {
      if (e.target === S.mask) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var cmodal = U.$('#confirmModal');
      if (cmodal && cmodal.classList.contains('show')) return; // 确认框优先处理 Esc
      if (S.mask.classList.contains('show')) close();
    });

    // Tab 切换
    U.$$('#settingsTabs button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        U.$$('#settingsTabs button').forEach(function (b) { b.classList.remove('active'); });
        U.$$('#settingsPanels .panel').forEach(function (p) { p.classList.remove('active'); });
        btn.classList.add('active');
        var pn = U.$('#settingsPanels .panel[data-panel="' + btn.getAttribute('data-tab') + '"]');
        if (pn) pn.classList.add('active');
        // 弹层高度固定后各 tab 共用同一个滚动容器，切换时回到顶部，
        // 否则从长面板滚到底再切到短面板会看到一个莫名其妙的空档。
        if (S.panels) S.panels.scrollTop = 0;
      });
    });

    // 背景上传 / 移除
    U.$('#btnBgUpload').addEventListener('click', function () { S.bgFile.click(); });
    S.bgFile.addEventListener('change', function () {
      var f = S.bgFile.files && S.bgFile.files[0];
      if (!f) return;
      readFileAsDataURL(f).then(function (dataUrl) {
        return AI.call('upload_background', f.name, dataUrl);
      }).then(function (rel) {
        U.showToast('背景图已更新', 'success');
        if (window.Theme) Theme.applyBackground(rel);
        state.settings.background_image = rel;
      }).catch(function (err) { U.showToast(err.message, 'error'); })
        .finally(function () { S.bgFile.value = ''; });
    });
    U.$('#btnBgRemove').addEventListener('click', function () {
      AI.call('remove_background').then(function () {
        if (window.Theme) Theme.applyBackground(null);
        state.settings.background_image = null;
        U.showToast('已移除背景图', 'success');
      }).catch(function (err) { U.showToast(err.message, 'error'); });
    });

    // 名称即时保存
    S.userName.addEventListener('change', function () {
      persist({ user: { name: S.userName.value } }).then(function () {
        if (window.AppState) AppState.refreshUserInfo();
        renderAppearance();
      }).catch(function () {});
    });
    S.aiName.addEventListener('change', function () {
      persist({ assistant: { name: S.aiName.value } }).then(function () {
        if (window.AppState) AppState.refreshUserInfo();
        renderAppearance();
      }).catch(function () {});
    });

    // 头像上传（用户 / AI）
    bindAvatarUpload(U.$('#userAvatarFile'), S.userAvatarLg, 'user', U.$('#btnUserAvatarReset'));
    bindAvatarUpload(U.$('#aiAvatarFile'), S.aiAvatarLg, 'assistant', U.$('#btnAiAvatarReset'));

    // 温度
    S.tempRange.addEventListener('input', function () {
      S.tempVal.textContent = S.tempRange.value;
    });
    S.tempRange.addEventListener('change', function () {
      persist({ temperature: parseFloat(S.tempRange.value) }).then(function () {
        state.settings.temperature = parseFloat(S.tempRange.value);
        renderTemp();
      }).catch(function () {});
    });

    // 新建分组
    U.$('#btnAddPrompt').addEventListener('click', function () {
      var g = {
        id: uid('g'),
        name: '新分组',
        created_at: new Date().toISOString(),
        prompts: [{ id: uid('p'), content: '', created_at: new Date().toISOString() }]
      };
      state.prompts.groups.push(g);
      saveGroups().then(function () {
        U.showToast('已创建分组（含 1 条空提示词），输入内容即可', 'success');
        renderPrompts();
        var inputs = U.$$('#promptGroups .pg-name');
        if (inputs.length) inputs[inputs.length - 1].focus();
      });
    });

    // 通用开关：静默启动
    S.swStartHidden.addEventListener('change', function () {
      var on = S.swStartHidden.checked;
      persist({ start_hidden: on }).then(function () {
        state.settings.start_hidden = on;
        U.showToast(on ? '已开启静默启动（下次启动生效）'
                       : '已关闭静默启动（下次启动生效）', 'success');
      }).catch(function () {});
    });

    U.$('#dataDirText').textContent = 'data/（项目根目录）';

    window.SettingsUI = { open: open, close: close, refreshAll: refreshAll };
  }

  window.SettingsModule = { init: init };
})();
