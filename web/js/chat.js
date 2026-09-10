/* ============================================================
   chat.js —— 聊天主逻辑
   ------------------------------------------------------------
   会话管理 / 消息渲染（Markdown）/ 发送 / 编辑重生成 /
   分支删除 / 历史列表 / 窗口控制 / 设置接入。

   依赖：api.js, util.js, markdown.js, theme.js, settings.js
   ============================================================ */
(function () {
  'use strict';

  var U = window.U;
  var S = {};
  var state = null;    // AppState
  var busy = false;    // AI 请求中
  var ctrlDownAt = null;
  var pendingImages = [];   // 待发送图片 [{url(dataURL), name}]
  var IMG_LIMIT = 6;        // 单次最多图片数
  var IMG_MAX_BYTES = 8 * 1024 * 1024; // 单张上限 8MB

  /* ---------------- AppState ---------------- */
  function makeState() {
    return {
      settings: null,
      providers: [],
      themes: [],
      conversations: [],
      prompts: { groups: [] },
      conv: null, // {id, title, messages}
      sending: false
    };
  }

  /* ---------------- 工具 ---------------- */
  function composeSystemPrompt() {
    var ids = (state.settings && state.settings.selected_prompt_groups) || [];
    var byId = {};
    ((state.prompts && state.prompts.groups) || []).forEach(function (g) { byId[g.id] = g; });
    var parts = [];
    ids.forEach(function (id) {
      var g = byId[id];
      if (!g) return;
      // 每个分组内可含多条提示词，按顺序逐条拼接
      var groupParts = [];
      (g.prompts || []).forEach(function (p) {
        if (p && p.content && String(p.content).trim()) groupParts.push(String(p.content).trim());
      });
      if (groupParts.length) parts.push(groupParts.join('\n\n'));
    });
    return parts.join('\n\n');
  }

  function avatarInfo(role) {
    var who = role === 'user' ? state.settings.user : state.settings.assistant;
    who = who || {};
    return { name: who.name || (role === 'user' ? '我' : 'AI 助手'),
             avatar: who.avatar || null };
  }

  function copyText(text) {
    function legacy() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (e) {}
      ta.remove();
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () {
        U.showToast('已复制', 'success', 1400);
      }, legacy);
    } else legacy();
  }

  /* ---------------- 渲染：头像 ---------------- */
  function avatarNode(role) {
    var info = avatarInfo(role);
    var img = document.createElement('div');
    img.className = 'avatar';
    if (info.avatar && info.avatar.indexOf('data:') === 0) {
      img.innerHTML = '<img src="' + info.avatar + '" alt="">';
    } else {
      img.textContent = (info.name || '?').charAt(0).toUpperCase();
    }
    return img;
  }

  /* 把头像画到一个圆形元素上：有 data URL 就显示图片，否则保持默认渐变圆 */
  function setAvatarVisual(el, avatar) {
    if (!el) return;
    if (avatar && avatar.indexOf('data:') === 0) {
      var im = el.querySelector('img');
      if (im) { im.src = avatar; }
      else { el.innerHTML = '<img src="' + avatar + '" alt="">'; }
    } else {
      el.innerHTML = '';
    }
  }

  /* ---------------- 渲染：单条消息 ---------------- */
  function buildMsgEl(msg, idx) {
    var role = msg.role === 'user' ? 'user' : 'ai';
    var info = avatarInfo(role === 'user' ? 'user' : 'assistant');
    var wrap = document.createElement('div');
    wrap.className = 'msg ' + role;
    wrap.dataset.idx = idx;

    var avatar = avatarNode(role === 'user' ? 'user' : 'assistant');
    var col = document.createElement('div');
    col.className = 'bubble-col';

    var head = document.createElement('div');
    head.className = 'msg-head';
    var who = document.createElement('span');
    who.className = 'who';
    who.textContent = info.name;
    var t = document.createElement('span');
    t.textContent = U.fmtTime(msg.timestamp);
    head.appendChild(who); head.appendChild(t);
    col.appendChild(head);

    var bubble = document.createElement('div');
    bubble.className = 'bubble';
    var contentEl = document.createElement('div');
    contentEl.className = 'content';
    if (role === 'user') {
      contentEl.style.whiteSpace = 'pre-wrap';
      contentEl.textContent = msg.content;
    } else {
      contentEl.innerHTML = window.MD.render(msg.content);
      // 若含错误标记（手动标记）显示为错误色
      if (msg.error) bubble.classList.add('err-bubble');
    }
    bubble.appendChild(contentEl);

    // ---- 悬停操作栏（消息下方图标）----
    // 最新一条 user 消息的判断：其是该会话中最后一条 user 消息
    var isLatestUser = false;
    if (role === 'user' && state.conv && state.conv.messages) {
      for (var j = state.conv.messages.length - 1; j >= 0; j--) {
        if (state.conv.messages[j].role === 'user') {
          isLatestUser = (j === idx);
          break;
        }
      }
    }

    function makeOpBtn(icon, label, cls) {
      var b = document.createElement('button');
      b.textContent = icon;
      b.title = label;
      if (cls) b.className = cls;
      return b;
    }

    var ops = document.createElement('div');
    ops.className = 'msg-ops';
    if (role === 'ai') {
      ops.appendChild(makeOpBtn('⧉', '复制'));
    } else {
      if (isLatestUser) ops.appendChild(makeOpBtn('↻', '重新生成'));
      ops.appendChild(makeOpBtn('⧉', '复制'));
    }
    ops.appendChild(makeOpBtn('✎', '编辑'));
    ops.appendChild(makeOpBtn('🗑', '删除', 'del'));

    col.appendChild(bubble);
    col.appendChild(ops);
    wrap.appendChild(avatar);
    wrap.appendChild(col);

    // 事件
    var btns = ops.children;
    btns[0].addEventListener('click', function () {
      // 用户消息且为最新 → 重新生成；否则复制
      if (role === 'user' && isLatestUser) {
        regenerateAt(idx);
      } else {
        copyText(msg.content);
      }
    });
    btns[btns.length - 2].addEventListener('click', function () { startEdit(wrap, msg, idx); });
    btns[btns.length - 1].addEventListener('click', function () { deleteMsg(idx); });
    return wrap;
  }

  /* ---------------- 渲染：消息区 ---------------- */
  function renderMessages() {
    var box = S.messages;
    box.innerHTML = '';
    var conv = state.conv;
    if (!conv || !conv.messages || conv.messages.length === 0) {
      box.appendChild(buildWelcome());
      return;
    }
    conv.messages.forEach(function (m, i) {
      box.appendChild(buildMsgEl(m, i));
    });
    scrollBottom(true);
    // 委托：代码块复制
  }

  function buildWelcome() {
    var w = document.createElement('div');
    w.className = 'welcome';
    // 大圆 = AI 头像：有自定义头像时显示图片，否则保持默认“蓝球”渐变
    var a = (state.settings && state.settings.assistant) || {};
    var bigInner = (a.avatar && a.avatar.indexOf('data:') === 0)
      ? '<img src="' + a.avatar + '" alt="">' : '';
    w.innerHTML =
      '<div class="big">' + bigInner + '</div><h2>你好，我是你的 AI 助手</h2>' +
      '<p>· 拖拽文件到悬浮球，AI 处理并写回原文件<br>' +
      '· 消息可编辑：修改最新提问将自动重新生成<br>' +
      '· 消息可删除：其后内容一并截断<br>' +
      '· 支持 Markdown 与代码高亮</p>' +
      '<div class="chips">' +
      '<button class="btn" data-tab="ai">选择 AI 接口</button>' +
      '<button class="btn" data-tab="prompts">管理系统提示词</button>' +
      '</div>';
    U.$$('button', w).forEach(function (b) {
      b.addEventListener('click', function () { openSettingsTab(b.getAttribute('data-tab')); });
    });
    return w;
  }

  /* ---------------- 渲染：会话列表 ---------------- */
  function renderConvList() {
    var box = S.convList;
    box.innerHTML = '';
    var list = state.conversations || [];
    if (!list.length) {
      var empty = document.createElement('div');
      empty.className = 'empty-list';
      empty.innerHTML = '暂无历史对话<br>开始聊天后会自动保存';
      box.appendChild(empty);
      return;
    }
    list.forEach(function (c) {
      var item = document.createElement('div');
      item.className = 'conv-item' + (state.conv && state.conv.id === c.id ? ' active' : '');
      item.innerHTML =
        '<div class="t">' + U.esc(c.title || '新对话') + '</div>' +
        '<div class="p">' + U.esc(c.preview || '（空）') + ' · ' + U.fmtRel(c.updated_at) + '</div>' +
        '<div class="ops">' +
        '<button class="rn" title="重命名">✎</button>' +
        '<button class="del" title="删除">🗑</button>' +
        '</div>';
      var bodyClick = function () { openConversation(c.id); };
      item.addEventListener('click', function (e) {
        if (e.target.closest('.ops')) return;
        bodyClick();
      });
      item.querySelector('.rn').addEventListener('click', function () {
        renameFlow(item, c);
      });
      item.querySelector('.del').addEventListener('click', function () {
        U.confirm('删除对话「' + (c.title || '') + '」？\n该对话的历史消息将一并删除，此操作不可恢复。',
                  { title: '删除对话', okText: '删除' }).then(function (ok) {
          if (!ok) return;
          AI.call('delete_conversation', c.id).then(function () {
            if (state.conv && state.conv.id === c.id) {
              state.conv = null;
              renderMessages();
              renderHeadInfo();
            }
            return refreshConversations();
          }).catch(function (err) { U.showToast(err.message, 'error'); });
        });
      });
      box.appendChild(item);
    });
  }

  function renameFlow(item, c) {
    item.querySelector('.t').style.display = 'none';
    var oldOps = item.querySelector('.ops');
    if (oldOps) oldOps.style.display = 'none';
    var input = document.createElement('input');
    input.className = 'rename-input';
    input.value = c.title || '';
    item.insertBefore(input, item.firstChild);
    input.focus(); input.select();
    var done = function (save) {
      var v = input.value.trim();
      input.remove();
      item.querySelector('.t').style.display = '';
      if (oldOps) oldOps.style.display = '';
      if (save && v && v !== c.title) {
        AI.call('rename_conversation', c.id, v).then(function () {
          return refreshConversations();
        }).catch(function (err) { U.showToast(err.message, 'error'); });
      } else if (save && v === c.title) { /* 无变化 */ }
    };
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') done(true);
      if (e.key === 'Escape') { e.stopPropagation(); done(false); }
    });
    input.addEventListener('blur', function () { done(true); });
  }

  /* ---------------- 数据刷新 ---------------- */
  function refreshConversations() {
    return AI.call('get_conversations').then(function (list) {
      state.conversations = list || [];
      renderConvList();
    }).catch(function (err) { console.error(err); });
  }

  function openConversation(id) {
    AI.call('get_conversation', id).then(function (conv) {
      state.conv = {
        id: conv.id,
        title: conv.title || '新对话',
        messages: conv.messages || []
      };
      renderHeadInfo();
      renderConvList();
      renderMessages();
    }).catch(function (err) { U.showToast(err.message, 'error'); });
  }

  function newChat() {
    state.conv = { id: null, title: '新对话', messages: [] };
    renderHeadInfo();
    renderConvList();
    renderMessages();
    S.input.focus();
  }

  function renderHeadInfo() {
    var conv = state.conv;
    S.chatTitle.textContent = conv && conv.title ? conv.title : '新对话';
    var provider = providerName(state.settings && state.settings.provider);
    var sub = '【' + provider + '】';
    if (conv && conv.messages) {
      sub += ' · 共 ' + conv.messages.length + ' 条消息 · Enter 发送';
    } else {
      sub += ' · 输入消息开始聊天';
    }
    S.chatSub.textContent = sub;
  }

  function providerName(id) {
    var p = (state.providers || []).find(function (x) { return x.id === id; });
    return p ? p.name : (id || '未选择');
  }

  function renderProviderSelect() {
    var sel = S.providerSelect;
    if (!state.settings) return;
    sel.innerHTML = '';
    (state.providers || []).forEach(function (p) {
      var opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name;
      sel.appendChild(opt);
    });
    sel.value = state.settings.provider || (state.providers[0] && state.providers[0].id) || '';
    applyComposerMode();
  }

  function currentProvider() {
    var id = state.settings && state.settings.provider;
    return (state.providers || []).find(function (p) { return p.id === id; });
  }
  /* 当前接口是否允许图片输入（多模态） */
  function isMultimodal() {
    var p = currentProvider();
    return !!(p && p.multimodal === true);
  }

  /* 输入框多模态模式：按钮显隐、占位提示；切换后清空未发送图片 */
  function applyComposerMode() {
    if (!S.btnAddImage) return;
    var on = isMultimodal();
    S.btnAddImage.hidden = !on;
    S.input.placeholder = on
      ? '输入消息… 可拖拽 / 粘贴 / 🖼 上传图片'
      : '输入消息，Enter 发送，Shift+Enter 换行';
    if (!on && pendingImages.length) {
      pendingImages = [];
      renderImgTray();
    }
  }

  /* ---------------- 多模态图片 ---------------- */
  function readImageAsDataURL(file) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () { resolve(fr.result); };
      fr.onerror = function () { reject(new Error('读取图片失败')); };
      fr.readAsDataURL(file);
    });
  }

  function addImageFiles(fileList) {
    var files = Array.prototype.filter.call(fileList || [], function (f) {
      return f && /^image\//.test(f.type);
    });
    if (!files.length) return;
    var space = IMG_LIMIT - pendingImages.length;
    if (files.length > space) {
      U.showToast('最多一次携带 ' + IMG_LIMIT + ' 张图片', 'error');
      files = files.slice(0, space);
    }
    var chain = Promise.resolve();
    files.forEach(function (f) {
      chain = chain.then(function () {
        if (f.size > IMG_MAX_BYTES) {
          U.showToast('图片「' + f.name + '」超过 8MB，已跳过', 'error');
          return;
        }
        return readImageAsDataURL(f).then(function (url) {
          pendingImages.push({ url: url, name: f.name });
        });
      });
    });
    chain.then(renderImgTray).catch(function (err) { U.showToast(err.message, 'error'); });
  }

  function renderImgTray() {
    if (!S.imgTray) return;
    S.imgTray.hidden = !pendingImages.length;
    S.imgTray.innerHTML = '';
    pendingImages.forEach(function (img, i) {
      var chip = document.createElement('div');
      chip.className = 'img-chip';
      chip.title = img.name || ('图片 ' + (i + 1));
      chip.innerHTML = '<img src="' + img.url + '" alt="">' +
        '<span class="rm" data-i="' + i + '">✕</span>';
      chip.querySelector('.rm').addEventListener('click', function () {
        pendingImages.splice(i, 1);
        renderImgTray();
      });
      S.imgTray.appendChild(chip);
    });
  }

  function scrollBottom(smooth) {
    var box = S.messages;
    requestAnimationFrame(function () {
      box.scrollTo({ top: box.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    });
  }

  /* ---------------- 发送 / AI 对话 ---------------- */
  function payloadMessages(msgs) {
    var system = composeSystemPrompt();
    var attachImages = isMultimodal() && pendingImages.length > 0;
    var arr = msgs.map(function (m, i) {
      var content = m.content;
      // 多模态：给最后一条 user 消息附带图片（OpenAI parts 格式，base64 data URL）
      if (attachImages && i === msgs.length - 1 && m.role === 'user') {
        content = [{ type: 'text', text: String(content || '') }].concat(
          pendingImages.map(function (p) {
            return { type: 'image_url', image_url: { url: p.url } };
          })
        );
      }
      return { role: m.role, content: content };
    });
    if (system) arr.unshift({ role: 'system', content: system });
    return arr;
  }

  /* 带超时的 chat 调用：即使后端线程卡死也不会让界面永远“思考中” */
  function callChat(providerId, msgs, temperature, timeoutMs) {
    timeoutMs = timeoutMs || 120000;
    var call = AI.call('chat', providerId, msgs, temperature);
    return Promise.race([
      call,
      new Promise(function (_, reject) {
        setTimeout(function () {
          reject(new Error('AI 请求超时（超过 ' + Math.round(timeoutMs / 1000) + ' 秒），请重试'));
        }, timeoutMs);
      })
    ]);
  }

  async function send() {
    if (busy) return;
    var text = S.input.value.trim();
    // 纯图片（无文字）在多模态下也允许发送
    if (!text && !(isMultimodal() && pendingImages.length)) return;

    if (!state.conv) newChat();
    var conv = state.conv;
    if (!conv.id) conv.id = 'c' + U.uuid();
    // 若编辑流程遗留（防御）
    if (!conv.messages) conv.messages = [];

    var userMsg = { role: 'user', content: text, timestamp: U.nowIso() };
    conv.messages.push(userMsg);
    S.input.value = '';
    autoGrow();
    renderMessages();

    busy = true;
    setBusyUI(true);
    var providerId = state.settings.provider;

    try {
      var reply = await callChat(providerId, payloadMessages(conv.messages),
                                 state.settings.temperature);
      var aiMsg = { role: 'assistant', content: reply, timestamp: U.nowIso() };
      conv.messages.push(aiMsg);
      await AI.call('save_conversation', conv.id, conv.messages);
      await refreshConversations();
      renderHeadInfo();
      renderMessages();
      scrollBottom(false);
    } catch (err) {
      // 保存用户消息（避免丢失），提示错误
      try { await AI.call('save_conversation', conv.id, conv.messages); } catch (e2) {}
      await refreshConversations();
      renderMessages();
      U.showToast('请求失败：' + err.message, 'error', 5200);
      console.error('[chat]', err);
    } finally {
      busy = false;
      setBusyUI(false);
      // 图片只随本次请求发送，完成后清空待发队列
      if (pendingImages.length) {
        pendingImages = [];
        renderImgTray();
      }
      S.input.focus();
    }
  }

  function setBusyUI(on) {
    S.typingRow.hidden = !on;
    S.btnSend.disabled = on;
    S.btnSend.textContent = on ? '…' : '发送';
  }

  /* ---------------- 编辑消息 ---------------- */
  function startEdit(wrap, msg, idx) {
    var contentEl = wrap.querySelector('.content');
    if (!contentEl || contentEl.querySelector('textarea')) return;

    var editBox = document.createElement('div');
    editBox.className = 'edit-box';
    editBox.innerHTML = '<textarea></textarea><div class="edit-actions">' +
      '<button class="btn" data-act="cancel">取消</button>' +
      '<button class="btn btn-primary" data-act="save">保存</button></div>';
    var ta = editBox.querySelector('textarea');
    ta.value = msg.content;
    contentEl.replaceWith(editBox);
    wrap.querySelector('.msg-ops').style.display = 'none';
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    function finish(save) {
      if (save && ta.value !== msg.content) {
        applyEdit(idx, msg, ta.value).catch(function (err) {
          U.showToast('保存失败：' + err.message, 'error');
        });
        return; // 保存成功后 renderMessages 会整体重建，无需 teardown
      }
      teardown();
    }
    function teardown() {
      if (editBox.parentNode) editBox.replaceWith(contentEl);
      var opsEl = wrap.querySelector('.msg-ops');
      if (opsEl) opsEl.style.display = '';
    }
    editBox.querySelector('[data-act=save]').addEventListener('click', function () { finish(true); });
    editBox.querySelector('[data-act=cancel]').addEventListener('click', function () { finish(false); });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.stopPropagation(); finish(false); }
    });
  }

  async function applyEdit(idx, msg, newContent) {
    var conv = state.conv;
    var msgs = conv.messages;
    var isLatestUser =
      msg.role === 'user' &&
      idx === msgs.length - 2 &&
      msgs[msgs.length - 1] && msgs[msgs.length - 1].role === 'assistant';

    msg.content = newContent;
    msg.timestamp = U.nowIso();
    msg.edited = true;

    if (isLatestUser) {
      // 覆盖旧回复并重新生成
      msgs = msgs.slice(0, idx + 1);
      conv.messages = msgs;
      renderMessages();
      busy = true;
      setBusyUI(true);
      try {
        var providerId = state.settings.provider;
        var reply = await callChat(providerId, payloadMessages(msgs),
                                   state.settings.temperature);
        conv.messages.push({ role: 'assistant', content: reply, timestamp: U.nowIso() });
        await AI.call('save_conversation', conv.id, conv.messages);
      } catch (err) {
        try { await AI.call('save_conversation', conv.id, conv.messages); } catch (e2) {}
        U.showToast('重新生成失败：' + err.message, 'error', 5200);
      } finally {
        busy = false;
        setBusyUI(false);
      }
    } else {
      // 非“最新用户消息”：本地编辑，仅持久化修改，不触发重生成
      var updated = await AI.call('update_message', conv.id, idx, newContent);
      if (updated) {
        conv.messages = updated.messages || conv.messages;
        conv.title = updated.title || conv.title;
      }
    }
    await refreshConversations();
    renderMessages();
  }

  /* ---------------- 重新生成：重跑某条最新用户提问 ---------------- */
  async function regenerateAt(idx) {
    var conv = state.conv;
    if (!conv || !conv.id || busy) return;
    var msg = conv.messages[idx];
    if (!msg || msg.role !== 'user') return;

    // 丢弃该提问之后的旧回复
    conv.messages = conv.messages.slice(0, idx + 1);
    renderMessages();
    busy = true;
    setBusyUI(true);
    try {
      var providerId = state.settings.provider;
      var reply = await callChat(providerId, payloadMessages(conv.messages),
                                 state.settings.temperature);
      conv.messages.push({ role: 'assistant', content: reply, timestamp: U.nowIso() });
      await AI.call('save_conversation', conv.id, conv.messages);
    } catch (err) {
      try { await AI.call('save_conversation', conv.id, conv.messages); } catch (e2) {}
      U.showToast('重新生成失败：' + err.message, 'error', 5200);
    } finally {
      busy = false;
      setBusyUI(false);
      await refreshConversations();
      renderMessages();
      scrollBottom(false);
      S.input.focus();
    }
  }

  /* ---------------- 删除消息（分支截断） ---------------- */
  function deleteMsg(idx) {
    var conv = state.conv;
    if (!conv || !conv.id) return;
    U.confirm('删除这条消息？\n它之后的所有消息内容将一并删除（分支截断），此操作不可恢复。',
              { title: '删除消息', okText: '删除' }).then(function (ok) {
      if (!ok) return;
      AI.call('delete_message', conv.id, idx).then(function (result) {
        if (result === null) {
          state.conv = null;
        } else {
          state.conv.messages = result.messages || [];
          state.conv.title = result.title || state.conv.title;
        }
        renderHeadInfo();
        return refreshConversations();
      }).then(function () {
        renderMessages();
      }).catch(function (err) { U.showToast(err.message, 'error'); });
    });
  }

  /* ---------------- 输入框 ---------------- */
  function autoGrow() {
    S.input.style.height = 'auto';
    S.input.style.height = Math.min(S.input.scrollHeight, 160) + 'px';
  }

  /* ---------------- 设置弹层 ---------------- */
  function openSettingsTab(tab) {
    if (!window.SettingsUI) return;
    window.SettingsUI.open();
    if (tab) {
      var btn = U.$('#settingsTabs button[data-tab="' + tab + '"]');
      if (btn) btn.click();
    }
  }

  /* ---------------- 窗口：尺寸调节 ---------------- */
  function bindResize() {
    function startResize(e, mode) {
      e.preventDefault();
      var minW = 500, minH = 600;
      if (state.settings && state.settings.chat) {
        minW = state.settings.chat.min_width || 500;
        minH = state.settings.chat.min_height || 600;
      }
      var startX = e.screenX, startY = e.screenY;
      var startW = window.outerWidth, startH = window.outerHeight;
      // 高分屏换算：screenX 为物理像素，窗口尺寸参数为逻辑像素
      var dpr = window.devicePixelRatio || 1;

      var moved = false;
      function onMove(ev) {
        var w = Math.max(minW, startW + (ev.screenX - startX) / dpr);
        var h = Math.max(minH, startH + (ev.screenY - startY) / dpr);
        if (mode.indexOf('h') >= 0) w = startW;
        if (mode.indexOf('v') >= 0) h = startH;
        AI.call('resize_window', 'chat', Math.round(w), Math.round(h))
          .catch(function (err) { console.error(err); });
        moved = true;
      }
      function onUp() {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        if (!moved) return; // 仅点击未拖动：不需要恢复
        // 拖动结束：直接通知后端做一次“隐藏→重显”恢复透明背景。
        // 不依赖 resize 事件，确保每次手松都触发。
        AI.call('refresh_window_transparency', 'chat')
          .catch(function (err) { console.error('[chat] 透明恢复失败', err); });
      }
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    }
    S.rzRight.addEventListener('mousedown', function (e) { startResize(e, 'w'); });
    S.rzBottom.addEventListener('mousedown', function (e) { startResize(e, 'h'); });
    S.rzCorner.addEventListener('mousedown', function (e) { startResize(e, 'wh'); });
    // 说明：系统边缘拖拽等其它 resize 由后端 resized 事件兜底自动恢复。
  }

  /* ---------------- 多模态：上传 / 粘贴 / 拖放 ---------------- */
  function bindMultimodal() {
    if (!S.btnAddImage) return;

    S.btnAddImage.addEventListener('click', function () { S.imgFile.click(); });
    S.imgFile.addEventListener('change', function () {
      addImageFiles(S.imgFile.files);
      S.imgFile.value = '';
    });

    // 粘贴剪贴板里的图片
    S.input.addEventListener('paste', function (e) {
      if (!isMultimodal()) return;
      var items = (e.clipboardData && e.clipboardData.items) || [];
      var files = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.kind === 'file' && /^image\//.test(it.type || '')) {
          var f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length) {
        e.preventDefault();
        addImageFiles(files);
      }
    });

    // 拖放图片到输入区
    var dragDepth = 0;
    function dragHasFiles(e) {
      return e.dataTransfer && Array.prototype.some.call(e.dataTransfer.types || [], function (t) {
        return t === 'Files';
      });
    }
    S.composer.addEventListener('dragenter', function (e) {
      if (!isMultimodal() || !dragHasFiles(e)) return;
      e.preventDefault();
      dragDepth++;
      S.dropMask.hidden = false;
    });
    S.composer.addEventListener('dragover', function (e) {
      if (isMultimodal() && dragHasFiles(e)) e.preventDefault();
    });
    S.composer.addEventListener('dragleave', function () {
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) S.dropMask.hidden = true;
    });
    S.composer.addEventListener('drop', function (e) {
      dragDepth = 0;
      S.dropMask.hidden = true;
      if (!isMultimodal() || !dragHasFiles(e)) return;
      e.preventDefault();
      var files = Array.prototype.filter.call(e.dataTransfer.files || [], function (f) {
        return /^image\//.test(f.type || '');
      });
      if (files.length) addImageFiles(files);
    });
  }

  /* ---------------- 事件：后端推送 ---------------- */
  function bindBackendEvents() {
    window.addEventListener('backend-toast', function (ev) {
      var d = ev.detail || {};
      U.showToast(d.text || '', d.type || 'info', 5000);
    });
    window.addEventListener('provider-changed', function (ev) {
      var d = ev.detail || {};
      if (d.id && state.settings) {
        state.settings.provider = d.id;
        renderProviderSelect();
        renderHeadInfo();
      }
    });
  }

  /* ---------------- UI 信息刷新 ---------------- */
  /* 用户与 AI 的名称/头像变更后，统一刷新所有展示位：
     侧边栏底部（用户）、侧边栏左上角品牌（AI logo+名称）、消息气泡与欢迎页 */
  function refreshUserInfo() {
    if (!state.settings) return;
    var u = state.settings.user || {};
    var a = state.settings.assistant || {};
    // 用户：侧边栏底部
    S.miniName.textContent = u.name || '我';
    if (u.avatar && u.avatar.indexOf('data:') === 0) {
      S.miniAvatar.innerHTML = '<img src="' + u.avatar + '" alt="">';
    } else {
      S.miniAvatar.innerHTML = '';
      S.miniAvatar.textContent = (u.name || '我').charAt(0).toUpperCase();
    }
    // AI：侧边栏左上角品牌（蓝球 → AI 头像，Alice AI → AI 名称）
    if (S.brandName) S.brandName.textContent = a.name || 'AI 助手';
    setAvatarVisual(S.brandLogo, a.avatar);
    renderMessages(); // 消息气泡头像/名称 + 欢迎页大球随之刷新
  }

  /* ---------------- 初始化 ---------------- */
  function attemptInit(tries) {
    if (!AI.available()) {
      if (tries > 0) { setTimeout(function () { attemptInit(tries - 1); }, 300); return; }
    }
    init();
  }

  async function init() {
    cacheEls();
    bindResize();
    bindBackendEvents();
    bindButtons();
    bindMultimodal();

    state = makeState();
    window.AppState = {
      state: state,
      onSettings: function (settings) {
        state.settings = settings;
        renderProviderSelect();
        renderHeadInfo();
        refreshUserInfo();
        applySidebar(settings.sidebar_visible !== false);
      },
      updateProviders: function (providers) {
        state.providers = providers || state.providers;
        renderProviderSelect();
        renderHeadInfo();
      },
      refreshUserInfo: refreshUserInfo
    };

    try {
      var boot = await AI.call('bootstrap');
      state.settings = boot.settings;
      state.providers = boot.providers || [];
      state.themes = boot.themes || [];
      state.conversations = boot.conversations || [];
      state.prompts = boot.prompts || { groups: [] };
      if (window.Theme) Theme.init(boot);

      applySidebar(state.settings.sidebar_visible !== false);
      renderProviderSelect();
      renderConvList();
      renderHeadInfo();
      refreshUserInfo();   // 用户/AI 名称头像 + 消息区（含欢迎页）一并渲染
      if (window.SettingsModule) window.SettingsModule.init(state);

      if (AI.available()) {
        console.log('[chat] pywebview 后端连接成功');
      }
    } catch (err) {
      console.error('初始化失败', err);
      U.showToast('初始化失败：' + err.message, 'error');
    }
  }

  function cacheEls() {
    S.messages = U.$('#messages');
    S.convList = U.$('#convList');
    S.input = U.$('#input');
    S.btnSend = U.$('#btnSend');
    S.typingRow = U.$('#typingRow');
    S.chatTitle = U.$('#chatTitle');
    S.chatSub = U.$('#chatSub');
    S.providerSelect = U.$('#providerSelect');
    S.miniAvatar = U.$('#miniAvatar');
    S.miniName = U.$('#miniName');
    S.brandLogo = U.$('#brandLogo');
    S.brandName = U.$('#brandName');
    S.rzRight = U.$('#rzRight');
    S.rzBottom = U.$('#rzBottom');
    S.rzCorner = U.$('#rzCorner');
    S.appRoot = U.$('#app');
    S.sidebarToggle = U.$('#btnSidebarToggle');
    S.imgTray = U.$('#imgTray');
    S.btnAddImage = U.$('#btnAddImage');
    S.imgFile = U.$('#imgFile');
    S.dropMask = U.$('#dropMask');
    S.composer = U.$('#composer');
  }

  /* 侧边栏收起/展开：同时更新图标与文档类名 */
  function applySidebar(visible) {
    if (!S.appRoot || !S.sidebarToggle) return;
    S.appRoot.classList.toggle('collapsed', !visible);
    S.sidebarToggle.textContent = visible ? '☰' : '≫';
    S.sidebarToggle.title = visible ? '收起侧边栏' : '展开侧边栏';
  }

  function bindButtons() {
    S.btnSend.addEventListener('click', send);
    S.input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    S.input.addEventListener('input', autoGrow);

    U.$('#btnNewChat').addEventListener('click', newChat);
    U.$('#btnSettings').addEventListener('click', function () { openSettingsTab(null); });
    U.$('#btnSettings2').addEventListener('click', function () { openSettingsTab(null); });
    U.$('#btnHideChat').addEventListener('click', function () {
      AI.call('hide_window', 'chat').catch(function (err) { console.error(err); });
    });
    U.$('#btnHideChat2').addEventListener('click', function () {
      AI.call('hide_window', 'chat').catch(function (err) { console.error(err); });
    });
    S.sidebarToggle.addEventListener('click', function () {
      var cur = !(state.settings && state.settings.sidebar_visible === false);
      var next = !cur;
      applySidebar(next);
      AI.call('save_settings', { sidebar_visible: next }).then(function (all) {
        if (state && AppState && AppState.onSettings) {
          state.settings = all;
          AppState.onSettings(all);
        }
      }).catch(function (err) {
        console.error(err);
        U.showToast('保存失败：' + err.message, 'error');
      });
    });

    // 头部 AI 接口下拉
    S.providerSelect.addEventListener('change', function () {
      var id = S.providerSelect.value;
      if (!id) return;
      AI.call('save_settings', { provider: id }).then(function (all) {
        state.settings = all;
        renderHeadInfo();
        applyComposerMode();   // 接口能力可能变化：刷新多模态输入区并清理图片
        U.showToast('AI 接口：' + providerName(id), 'success', 1500);
      }).catch(function (err) { U.showToast(err.message, 'error'); });
    });

    // 消息区：代码块复制（事件委托）
    S.messages.addEventListener('click', function (e) {
      var btn = e.target.closest('.code-copy');
      if (!btn) return;
      var codeEl = btn.closest('pre').querySelector('code');
      if (codeEl) copyText(codeEl.textContent);
    });

    // 窗口状态恢复后确保（后端已推送事件）
    document.addEventListener('keydown', function (e) {
      // 聊天窗内 Esc 隐藏窗口
      if (e.key === 'Escape') {
        var modal = U.$('#settingsModal');
        var cmodal = U.$('#confirmModal');
        if (cmodal && cmodal.classList.contains('show')) return; // 确认框自己处理 Esc
        if (!modal || !modal.classList.contains('show')) {
          AI.call('hide_window', 'chat').catch(function () {});
        }
      }
    });

    // 窗口获得焦点时把光标放回输入框，便于直接打字
    window.addEventListener('focus', function () {
      if (busy) return;
      setTimeout(function () {
        var modal = U.$('#settingsModal');
        if (S.input && (!modal || !modal.classList.contains('show'))) {
          S.input.focus({ preventScroll: true });
        }
      }, 120);
    });
  }

  // 就绪后启动（等待 pywebview API 注入）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { attemptInit(10); });
  } else {
    attemptInit(10);
  }
})();
