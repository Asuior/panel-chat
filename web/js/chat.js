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

  /* ---------------- 渲染：消息内图片 ---------------- */
  /* 图片条目可能来自三处：
       {data: dataURL}   刚发送、尚未落盘
       {file, url}       已落盘（后端返回的相对路径）
       "data:..."        旧格式兜底                                        */
  function imgSrc(im) {
    if (!im) return '';
    if (typeof im === 'string') return im.indexOf('data:') === 0 || im.indexOf('assets/') === 0 ? im : '';
    var src = im.url || im.file || im.data;
    return typeof src === 'string' ? src : '';
  }

  function lightbox() {
    if (!S.lightbox) {
      var box = document.createElement('div');
      box.className = 'img-lightbox';
      box.innerHTML = '<img alt=""><div class="lb-foot"><span class="lb-name"></span>' +
        '<span class="lb-tip">点击任意处或按 Esc 关闭</span></div>';
      box.addEventListener('click', closeLightbox);
      // 挂到 .app 卡片内：圆角内裁剪，不会在透明边缘出现直角遮罩
      (S.appRoot || document.body).appendChild(box);
      S.lightbox = box;
    }
    return S.lightbox;
  }

  function openLightbox(src, name) {
    if (!src) return;
    var box = lightbox();
    box.querySelector('img').src = src;
    box.querySelector('.lb-name').textContent = name || '';
    box.classList.add('show');
  }

  function closeLightbox() {
    if (S.lightbox) S.lightbox.classList.remove('show');
  }

  function lightboxOpen() {
    return !!(S.lightbox && S.lightbox.classList.contains('show'));
  }

  /* 气泡里的图片缩略图（点击放大查看） */
  function buildMsgImages(msg) {
    var list = (msg.images || []).map(function (im) {
      return { im: im, src: imgSrc(im) };
    }).filter(function (it) { return !!it.src; });
    if (!list.length) return null;

    var box = document.createElement('div');
    box.className = 'bubble-images n' + Math.min(list.length, 3);
    var single = list.length === 1;   // 单图按原比例显示，多图统一裁成方格
    list.forEach(function (it, i) {
      var im = it.im;
      var src = it.src;
      var thumb = document.createElement('button');
      thumb.type = 'button';
      thumb.className = 'bubble-thumb';
      var name = (im && im.name) || ('图片 ' + (i + 1));
      thumb.title = name + '（点击放大）';
      thumb.dataset.src = src;
      // 已知宽高时先按比例占位：图片解码完成后不会再撑高消息区
      if (single && im && im.w > 0 && im.h > 0) {
        var ratio = im.w / im.h;
        thumb.style.aspectRatio = String(Math.min(Math.max(ratio, 0.6), 2.2));
      }
      thumb.innerHTML = '<img alt="">';
      var el = thumb.querySelector('img');
      el.src = src;
      // 兜底：相对路径加载失败时（本地子资源受限）改走后端 data URL 通道
      el.addEventListener('error', function () {
        var rel = (im && (im.file || im.url)) || '';
        if (el.dataset.fallback || !rel || rel.indexOf('data:') === 0) return;
        el.dataset.fallback = '1';
        AI.call('get_image_data_url', rel).then(function (url) {
          if (!url) return;
          el.src = url;
          thumb.dataset.src = url;
        }).catch(function (err) { console.warn('[chat] 图片兜底加载失败', err); });
      });
      thumb.addEventListener('click', function () {
        openLightbox(thumb.dataset.src || src, name);
      });
      box.appendChild(thumb);
    });
    return box;
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
    // 图片（本地历史同样保留渲染；发送接口前会由后端过滤掉历史图片）
    if (msg.images && msg.images.length) {
      var imgBox = buildMsgImages(msg);
      if (imgBox) {
        bubble.classList.add('has-images');
        bubble.appendChild(imgBox);
      }
    }
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
    if (!msg.content) contentEl.hidden = true;   // 纯图片消息不显示空文本块
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
    closeLightbox();   // 重绘时关掉图片预览，避免显示已失效的图
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

  /* 欢迎页的「头像位」——始终由程序渲染，跟随 设置 → 外观 里的 AI 头像。
     它**不属于 welcome_html 的范围**：插件自定义欢迎页只能替换头像以下的内容，
     既不能替换、也不能去掉这个头像位（否则插件 HTML 一改，头像就丢了）。 */
  function buildWelcomeAvatar() {
    // 大圆 = AI 头像：有自定义头像时显示图片，否则保持默认“蓝球”渐变
    var a = (state.settings && state.settings.assistant) || {};
    var big = document.createElement('div');
    big.className = 'big';
    if (a.avatar && a.avatar.indexOf('data:') === 0) {
      big.innerHTML = '<img src="' + a.avatar + '" alt="">';
    }
    return big;
  }

  /* 内置默认欢迎内容（不含头像位） */
  function defaultWelcomeContentHtml() {
    return '<h2>你好，我是你的 AI 助手</h2>' +
      '<p>· 支持图片输入：粘贴、拖拽或点击上传<br>' +
      '· 消息可编辑：修改最新提问将自动重新生成<br>' +
      '· 消息可删除：其后内容一并截断<br>' +
      '· 支持 Markdown 与代码高亮</p>' +
      '<div class="chips">' +
      '<button class="btn" data-tab="ai">选择 AI 接口</button>' +
      '<button class="btn" data-tab="prompts">管理系统提示词</button>' +
      '</div>';
  }

  /* 当前接口自定义的欢迎词；未提供（或只有空白）时返回空串表示“用默认” */
  function providerWelcomeHtml() {
    var p = currentProvider();
    var html = p && p.welcome_html;
    return (typeof html === 'string' && html.trim()) ? html : '';
  }

  function buildWelcome() {
    var w = document.createElement('div');
    // 容器始终是 .welcome（保留居中布局与主题变量）；
    // 用接口自定义欢迎词时额外加 .custom，便于主题单独定制 .welcome.custom
    w.className = 'welcome';
    // 1) 先放头像位（程序渲染，与 welcome_html 无关）
    w.appendChild(buildWelcomeAvatar());
    // 2) 再放内容：接口提供了自定义欢迎词就用它，否则回落到内置默认
    var custom = providerWelcomeHtml();
    var holder = document.createElement('div');
    if (custom) {
      holder.innerHTML = custom;
      w.classList.add('custom');
    } else {
      holder.innerHTML = defaultWelcomeContentHtml();
    }
    // 把内容节点逐个搬进 .welcome（保持 h2 / p / .chips 与头像同级，
    // 这样 .welcome 的 flex + gap 布局不用改），holder 本身不入 DOM
    while (holder.firstChild) w.appendChild(holder.firstChild);
    // 约定：带 data-tab="ai|prompts|appearance|..." 的元素 = 跳转对应设置页签。
    // 默认欢迎词与插件自定义欢迎词共用这套绑定，插件无需自己写事件。
    U.$$('[data-tab]', w).forEach(function (b) {
      b.addEventListener('click', function () { openSettingsTab(b.getAttribute('data-tab')); });
    });
    return w;
  }

  /* 切换 AI 接口后同步欢迎词。
     只做“原地替换欢迎页节点”：
       · 当前会话有消息（非欢迎态）→ 什么都不做，不重绘已有消息、不重置滚动位置；
       · 当前是欢迎态 → 用新接口的欢迎页节点换掉旧的，其余 DOM 一概不碰。 */
  function refreshWelcome() {
    if (!S.messages || !state) return;
    var conv = state.conv;
    if (conv && conv.messages && conv.messages.length) return;
    var old = S.messages.querySelector('.welcome');
    if (!old) return;   // 消息区里没有欢迎页节点，说明当前不是欢迎态
    S.messages.replaceChild(buildWelcome(), old);
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

  /* 读取图片宽高：用于渲染时提前占位，避免图片解码后把消息区撑高（滚动会停在半路） */
  function measureImage(url) {
    return new Promise(function (resolve) {
      var im = new Image();
      im.onload = function () { resolve({ w: im.naturalWidth || 0, h: im.naturalHeight || 0 }); };
      im.onerror = function () { resolve({ w: 0, h: 0 }); };
      im.src = url;
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
          return measureImage(url).then(function (size) {
            pendingImages.push({ url: url, name: f.name, w: size.w, h: size.h });
          });
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

  /* 把消息区吸到底部。
     注意：气泡里的图片是异步解码的，解码完成前缩略图高度为 0，
     只滚一次等于「滚到当时的内容底部」，图片撑高后就停在半路了。
     因此滚完后再补一次吸附（图片加载完成 / 短延时兜底）。 */
  var smoothScrollUntil = 0;   // 平滑滚动窗口：窗口内不强行跳到底，避免打断动画

  function scrollBottom(smooth) {
    var box = S.messages;
    smoothScrollUntil = smooth ? Date.now() + 450 : 0;
    requestAnimationFrame(function () {
      box.scrollTo({ top: box.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    });
    settleScroll(box);
  }

  /* 内容仍在长高时补一次吸底（已经在底部就不动） */
  function pinToBottom(retry) {
    retry = retry || 0;
    if (Date.now() < smoothScrollUntil && retry < 4) {
      setTimeout(function () {
        requestAnimationFrame(function () { pinToBottom(retry + 1); });
      }, 180);
      return;
    }
    var box = S.messages;
    if (box.scrollHeight - box.scrollTop - box.clientHeight > 2) {
      box.scrollTo({ top: box.scrollHeight, behavior: 'auto' });
    }
  }

  /* 是否基本贴底（用于「迟到的高度变化」：只有本来就在底部才继续贴底，
     用户在往上翻历史时不会被强行拉回） */
  function nearBottom() {
    var box = S.messages;
    return box.scrollHeight - box.scrollTop - box.clientHeight <= 120;
  }

  /* 等图片解码完（或超时兜底）再补一次吸底 */
  function settleScroll(box) {
    var pending = Array.prototype.filter.call(box.querySelectorAll('img'), function (im) {
      return !im.complete;
    });
    var timer = null;
    function done() {
      if (timer) { clearTimeout(timer); timer = null; }
      requestAnimationFrame(function () { pinToBottom(0); });
    }
    if (pending.length) {
      var left = pending.length;
      pending.forEach(function (im) {
        var one = function () { if (--left <= 0) done(); };
        im.addEventListener('load', one, { once: true });
        im.addEventListener('error', one, { once: true });
      });
      timer = setTimeout(done, 1500);   // 兜底：个别图片卡住也要吸底
    } else {
      setTimeout(done, 60);
    }
  }

  /* ---------------- 发送 / AI 对话 ---------------- */
  /* 组装请求消息：只带 role/content/images，
     “哪条消息的图片真正发给 AI”由后端统一过滤
     （仅最后一条用户消息带图，历史图片降级为纯文本）。 */
  function payloadMessages(msgs) {
    var system = composeSystemPrompt();
    var arr = msgs.map(function (m) {
      var out = { role: m.role, content: m.content };
      if (m.images && m.images.length) out.images = m.images;
      return out;
    });
    if (system) arr.unshift({ role: 'system', content: system });
    return arr;
  }

  /* 保存会话并采用后端返回的消息（图片条目会换成落盘后的相对路径） */
  async function persistConversation(conv) {
    var saved = await AI.call('save_conversation', conv.id, conv.messages);
    // 仅在返回了有效消息时回填，避免异常返回把本地消息清空
    if (saved && Array.isArray(saved.messages) && saved.messages.length) {
      conv.messages = saved.messages;
      if (saved.title) conv.title = saved.title;
    }
    return saved;
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

    // 立刻把待发图片移入消息并清空托盘（不等 AI 回复）
    var sentImages = pendingImages.slice();
    pendingImages = [];
    renderImgTray();
    S.input.value = '';
    autoGrow();

    var userMsg = { role: 'user', content: text, timestamp: U.nowIso() };
    if (sentImages.length) {
      userMsg.images = sentImages.map(function (p) {
        return { data: p.url, name: p.name, w: p.w, h: p.h };
      });
    }
    conv.messages.push(userMsg);
    renderMessages();

    busy = true;
    setBusyUI(true);
    var providerId = state.settings.provider;

    try {
      var reply = await callChat(providerId, payloadMessages(conv.messages),
                                 state.settings.temperature);
      var aiMsg = { role: 'assistant', content: reply, timestamp: U.nowIso() };
      conv.messages.push(aiMsg);
      await persistConversation(conv);
      await refreshConversations();
      renderHeadInfo();
      renderMessages();
      scrollBottom(false);
    } catch (err) {
      // 保存用户消息（避免丢失），提示错误
      try { await persistConversation(conv); } catch (e2) {}
      await refreshConversations();
      renderMessages();
      U.showToast('请求失败：' + err.message, 'error', 5200);
      console.error('[chat]', err);
    } finally {
      busy = false;
      setBusyUI(false);
      S.input.focus();
    }
  }

  function setBusyUI(on) {
    S.typingRow.hidden = !on;
    S.btnSend.disabled = on;
    // 发送键是圆形图标按钮：空闲「↑」，生成中「…」
    S.btnSend.textContent = on ? '…' : '↑';
    S.btnSend.title = on ? '生成中…' : '发送';
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
        await persistConversation(conv);
      } catch (err) {
        try { await persistConversation(conv); } catch (e2) {}
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
      await persistConversation(conv);
    } catch (err) {
      try { await persistConversation(conv); } catch (e2) {}
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

      function onMove(ev) {
        var w = Math.max(minW, startW + (ev.screenX - startX) / dpr);
        var h = Math.max(minH, startH + (ev.screenY - startY) / dpr);
        if (mode.indexOf('h') >= 0) w = startW;
        if (mode.indexOf('v') >= 0) h = startH;
        AI.call('resize_window', 'chat', Math.round(w), Math.round(h))
          .catch(function (err) { console.error(err); });
      }
      function onUp() {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        // 拖动结束无需任何善后：窗口不透明，缩放不会丢失合成
      }
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    }
    S.rzRight.addEventListener('mousedown', function (e) { startResize(e, 'w'); });
    S.rzBottom.addEventListener('mousedown', function (e) { startResize(e, 'h'); });
    S.rzCorner.addEventListener('mousedown', function (e) { startResize(e, 'wh'); });
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
        refreshWelcome();   // 托盘切换接口：欢迎态下原地换欢迎页
      }
    });
  }

  /* ---------------- UI 信息刷新 ---------------- */
  /* 消息区 DOM 只依赖「消息列表 + 用户/AI 的名称与头像」。
     记下上一次渲染用的这组身份信息，用来判断某次设置变更是否真的需要重绘消息：
     只有身份变了才整体重绘（气泡头像/名称要跟着变），
     其余设置（例如切换 AI 接口）只原地换欢迎页，不重绘消息、不动滚动位置。 */
  var lastIdentity = '';

  function identityKey(settings) {
    var u = (settings && settings.user) || {};
    var a = (settings && settings.assistant) || {};
    return [u.name, u.avatar, a.name, a.avatar].join('\u0001');
  }

  /* 用户与 AI 的名称/头像变更后，统一刷新所有展示位：
     侧边栏底部（用户）、侧边栏左上角品牌（AI logo+名称）、消息气泡与欢迎页。
     :param skipMessages: true 时不重绘消息区，只原地刷新欢迎页
       （用于切换 AI 接口等与消息内容无关的设置变更）。 */
  function refreshUserInfo(skipMessages) {
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
    if (skipMessages) {
      refreshWelcome();            // 只换欢迎页节点，消息区与滚动位置不动
    } else {
      renderMessages();            // 消息气泡头像/名称 + 欢迎页大球随之刷新
    }
    lastIdentity = identityKey(state.settings);
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
        // 用户/AI 名称或头像变了 → 整体重绘消息；否则（例如切换 AI 接口）
        // 只原地替换欢迎页，不重绘已有消息、不重置滚动位置。
        var identityChanged = identityKey(settings) !== lastIdentity;
        refreshUserInfo(!identityChanged);
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

  /* 侧边栏收起/展开：同时更新图标与文档类名。
     收起后不是没有侧边栏，而是留一条窄边栏，这个按钮就落在窄边栏顶部。 */
  function applySidebar(visible) {
    if (!S.appRoot || !S.sidebarToggle) return;
    S.appRoot.classList.toggle('collapsed', !visible);
    S.sidebarToggle.textContent = visible ? '«' : '»';
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
        refreshWelcome();      // 头部下拉切换接口：欢迎态下原地换欢迎页
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

    // 图片解码完成（load 不冒泡，用捕获阶段在容器上统一接住）后，
    // 若本来就在底部，就继续贴底 —— 兜住任何「迟到的高度变化」
    S.messages.addEventListener('load', function () {
      if (nearBottom()) pinToBottom(0);
    }, true);

    // 窗口状态恢复后确保（后端已推送事件）
    document.addEventListener('keydown', function (e) {
      // 聊天窗内 Esc 隐藏窗口
      if (e.key === 'Escape') {
        // 图片放大预览优先：Esc 只关闭预览，不隐藏窗口
        if (lightboxOpen()) { closeLightbox(); return; }
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
