/* ============================================================
   markdown.js —— 轻量 Markdown 渲染（安全、零依赖）
   ------------------------------------------------------------
   支持：标题 / 段落 / 有序无序列表 / 引用 / 分隔线 / 粗体 / 斜体 /
        删除线 / 行内代码 / 代码块(带极简语法高亮与复制按钮) /
        链接 / 图片 / 自动换行。
   所有输入先 HTML 转义，再套用结构，不会注入脚本。
   ============================================================ */
(function () {
  'use strict';

  var U = window.U;

  /* ---------------- 极简语法高亮 ---------------- */
  var KEYWORDS = {
    python: 'def|class|return|import|from|as|if|elif|else|for|while|try|except|finally|with|lambda|yield|pass|break|continue|raise|and|or|not|in|is|None|True|False|async|await|global|nonlocal|del|assert',
    javascript: 'function|return|const|let|var|if|else|for|while|do|switch|case|break|continue|new|class|extends|super|this|typeof|instanceof|in|of|try|catch|finally|throw|async|await|yield|import|export|from|default|null|undefined|true|false|delete|void|static|get|set',
    typescript: 'function|return|const|let|var|if|else|for|while|switch|case|break|new|class|extends|implements|interface|type|enum|namespace|this|typeof|instanceof|try|catch|finally|throw|async|await|import|export|from|default|null|undefined|true|false|interface|readonly|public|private|protected|static|abstract',
    json: 'true|false|null',
    cpp: 'int|char|float|double|void|bool|return|if|else|for|while|switch|case|break|continue|new|delete|class|struct|public|private|protected|namespace|using|template|typename|const|static|virtual|override|auto|nullptr|true|false|include|define',
    c: 'int|char|float|double|void|return|if|else|for|while|switch|case|break|continue|struct|union|enum|const|static|typedef|sizeof|include|define|null|true|false|unsigned|short|long|extern',
    java: 'public|private|protected|class|interface|enum|extends|implements|import|package|static|final|void|int|long|float|double|boolean|char|byte|short|return|if|else|for|while|switch|case|break|continue|new|try|catch|finally|throw|throws|this|super|null|true|false|abstract|synchronized',
    css: 'color|background|background-color|border|margin|padding|display|position|flex|grid|font|font-size|width|height|top|left|right|bottom|opacity|transform|transition|animation|overflow|z-index|content|float|clear|text-align|align-items|justify-content|border-radius|box-shadow|pointer-events|user-select|filter|backdrop-filter',
    sql: 'select|from|where|insert|into|values|update|set|delete|create|table|drop|alter|join|left|right|inner|outer|on|group|by|order|having|limit|offset|and|or|not|in|is|null|like|as|distinct|count|sum|avg|min|max|primary|key|foreign|references|index|view|procedure|begin|end|commit|rollback|case|when|then|else',
    bash: 'if|then|else|elif|fi|for|while|do|done|case|esac|function|return|exit|echo|export|local|read|cd|source|sudo|grep|awk|sed|find|ls|cat|mkdir|rm|cp|mv|chmod|chown|tar|curl|wget|python|pip|git',
    html: 'div|span|html|head|body|script|style|link|meta|title|header|footer|nav|main|section|article|aside|ul|ol|li|a|img|p|br|hr|table|tr|td|th|thead|tbody|form|input|button|select|option|textarea|label|iframe|video|audio|canvas|svg|class|id|href|src|type|name|value|placeholder|style'
  };
  var ALL_KEYWORDS = 'function|return|const|let|var|if|else|for|while|switch|case|break|continue|new|class|try|catch|finally|throw|import|export|from|default|null|true|false|this|typeof|async|await|def|lambda|yield|pass|raise|and|or|not|in|is|None|static|void|public|private|delete|extends|super|with|do|of|instanceof|typeof|undefined|global';

  function keywordsFor(lang) {
    return KEYWORDS[lang] || ALL_KEYWORDS;
  }

  function highlight(code, lang) {
    var kw = keywordsFor((lang || '').toLowerCase());
    var tokenRe = new RegExp(
      '(\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/|<!--[\\s\\S]*?-->|#[^\\n]*' +
      '|"(?:\\\\.|[^"\\\\\\n])*"|\'(?:\\\\.|[^\'\\\\\\n])*\'|`(?:\\\\.|[^`\\\\])*`' +
      '|\\b\\d[\\d_]*(?:\\.\\d+)?\\b|\\b[A-Za-z_$][\\w$]*\\b)',
      'g'
    );
    var out = '';
    var last = 0, m;
    while ((m = tokenRe.exec(code)) !== null) {
      out += U.esc(code.slice(last, m.index));
      var tok = m[0], cls = '';
      if (/^(\/\/|#|<!--)/.test(tok)) cls = 'tok-com';
      else if (/^(\/\*)/.test(tok)) cls = 'tok-com';
      else if (/^("|'|`)/.test(tok)) cls = 'tok-str';
      else if (/^\d/.test(tok)) cls = 'tok-num';
      else if (new RegExp('^(?:' + kw + ')$').test(tok)) cls = 'tok-kw';
      else if (code[tokenRe.lastIndex] === '(') cls = 'tok-fn';
      out += cls ? '<span class="' + cls + '">' + U.esc(tok) + '</span>' : U.esc(tok);
      last = tokenRe.lastIndex;
    }
    out += U.esc(code.slice(last));
    return out;
  }

  /* ---------------- 行内样式 ---------------- */
  function inline(text) {
    // 先保护行内代码
    var codes = [];
    text = text.replace(/`([^`\n]+)`/g, function (_, c) {
      codes.push('<code>' + U.esc(c) + '</code>');
      return '\u0000' + (codes.length - 1) + '\u0000';
    });

    // 转义其余文本（占位符允许保留）
    text = U.esc(text).replace(/\u0000(\d+)\u0000/g, function (_, i) {
      return codes[+i];
    });

    // 图片 ![alt](url) 与链接 [text](url) —— 先生成并保护，避免被裸链接逻辑二次匹配
    var anchors = [];
    text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, function (_, alt, src) {
      var html = '<img alt="' + U.esc(alt) + '" src="' + U.esc(src) + '">';
      anchors.push(html);
      return '\u0002' + (anchors.length - 1) + '\u0002';
    });
    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, t, url) {
      var html = '<a href="' + U.esc(url) + '" target="_blank" rel="noopener">' + t + '</a>';
      anchors.push(html);
      return '\u0002' + (anchors.length - 1) + '\u0002';
    });

    // 粗体 / 斜体 / 删除线（粗体优先）
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    // 剩余裸 URL 自动链接
    text = text.replace(/(https?:\/\/[^\s<]+)/g, function (url) {
      return '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>';
    });

    // 还原链接/图片
    text = text.replace(/\u0002(\d+)\u0002/g, function (_, i) {
      return anchors[+i];
    });
    return text;
  }

  /* ---------------- 块级渲染 ---------------- */
  function renderMarkdown(text) {
    if (!text) return '';
    text = String(text).replace(/\r\n?/g, '\n');

    // 1) 抽取围栏代码块
    var fences = [];
    text = text.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, function (_, lang, body) {
      fences.push({ lang: lang || '', body: body.replace(/\n$/, '') });
      return '\u0001' + (fences.length - 1) + '\u0001';
    });

    var lines = text.split('\n');
    var html = [];
    var i = 0;

    function emitFence(m) {
      var idx = parseInt(m.slice(1, -1), 10);
      var f = fences[idx];
      var copyId = 'cc' + idx + Math.floor(Math.random() * 1e6);
      return '<pre><div class="code-head"><span class="lang">' + U.esc(f.lang || 'code') +
        '</span><button class="code-copy" data-copy="' + copyId + '">复制</button></div>' +
        '<code class="' + U.esc(f.lang) + '" id="' + copyId + '">' +
        highlight(f.body, f.lang) + '</code></pre>';
    }

    while (i < lines.length) {
      var line = lines[i];

      // 围栏占位
      if (/^\u0001\d+\u0001$/.test(line.trim())) {
        html.push(emitFence(line.trim()));
        i++; continue;
      }
      // 空行
      if (!line.trim()) { i++; continue; }
      // 标题
      var h = /^(#{1,6})\s+(.*)$/.exec(line);
      if (h) {
        var lv = h[1].length;
        html.push('<' + 'h' + lv + '>' + inline(h[2].trim()) + '</' + 'h' + lv + '>');
        i++; continue;
      }
      // 分隔线
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line) && line.trim().length >= 3) {
        html.push('<hr>'); i++; continue;
      }
      // 引用块
      if (/^\s*>\s?/.test(line)) {
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, ''));
          i++;
        }
        html.push('<blockquote>' + inline(quote.join('\n').trim()) + '</blockquote>');
        continue;
      }
      // 列表
      var ulm = /^\s*[-*+]\s+/.test(line);
      var olm = /^\s*\d+[.)]\s+/.test(line);
      if (ulm || olm) {
        var tag = ulm ? 'ul' : 'ol';
        var items = [];
        while (i < lines.length) {
          var cur = lines[i];
          var m2 = /^\s*[-*+]\s+(.*)$/.exec(cur) ||
                   (olm ? /^\s*\d+[.)]\s+(.*)$/.exec(cur) : null);
          if (!m2) break;
          items.push('<li>' + inline(m2[1].trim()) + '</li>');
          i++;
        }
        html.push('<' + tag + '>' + items.join('') + '</' + tag + '>');
        continue;
      }
      // 普通段落（合并后续非空非块行）
      var para = [line.trim()];
      i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,6})\s/.test(lines[i]) &&
             !/^\s*[-*+]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i]) &&
             !/^\s*>\s?/.test(lines[i]) && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i]) &&
             !/^\u0001\d+\u0001$/.test(lines[i].trim())) {
        para.push(lines[i].trim());
        i++;
      }
      html.push('<p>' + inline(para.join(' ')) + '</p>');
    }

    return '<div class="md">' + html.join('') + '</div>';
  }

  window.MD = { render: renderMarkdown };
})();
