/* ============================================================
   floatball.js —— 悬浮球交互（无文字版）
   ------------------------------------------------------------
   1) 正圆玻璃球 + 按时段自动切换的渐变光效（可开关）；
   2) 内部“潮汐水位”：波形水面由 SVG 逐帧绘制，沿正弦曲线起伏、
      缓慢流动，水位高度 = 当前时刻占一天百分比（12:00 ≈ 50%）；
   3) 单击 → 唤起悬浮窗；双击 → 预留占位；
   4) 系统文件拖拽 → 光圈反馈（路径处理在后端 DOM 监听，
      完成后以 file-drop-state 事件通知）。
   窗口移动由 pywebview easy_drag 完成（后端负责位置记忆）。
   ============================================================ */
(function () {
  'use strict';

  var U = window.U;

  /* ---------------- 时段光效配置（与规格书一致） ----------------
     每个时段额外配一套“水体配色”，保证液体与球体颜色有足够区分：
     球体浅色（上午/下午）时水体用深蓝/深紫，球体深色时水体用亮白/亮青。 */
  var PERIODS = [
    { id: 'late-night', start: 0,  end: 6,   c1: '#4A4A8A', c2: '#6B4FAB',
      glowA: 'rgba(74,74,138,.55)', glowB: 'rgba(107,79,171,.45)', breath: '3s',
      wTop: 'rgba(200,240,255,0.55)', wMid: 'rgba(120,205,255,0.25)',
      wLine: '#ffffff', wSub: 'rgba(255,255,255,0.35)' },
    { id: 'dawn',       start: 6,  end: 9,   c1: '#FF8C42', c2: '#FFB347',
      glowA: 'rgba(255,140,66,.6)', glowB: 'rgba(255,179,71,.5)', breath: '2.5s',
      wTop: 'rgba(255,255,255,0.6)', wMid: 'rgba(255,220,180,0.25)',
      wLine: '#ffffff', wSub: 'rgba(255,255,255,0.4)' },
    { id: 'morning',    start: 9,  end: 12,  c1: '#4FC3F7', c2: '#81D4FA',
      glowA: 'rgba(79,195,247,.5)', glowB: 'rgba(129,212,250,.4)', breath: '3s',
      wTop: 'rgba(13,92,200,0.52)', wMid: 'rgba(30,130,220,0.28)',
      wLine: '#eaf6ff', wSub: 'rgba(255,255,255,0.4)' },
    { id: 'afternoon',  start: 12, end: 17,  c1: '#FFFFFF', c2: '#E0E0E0',
      glowA: 'rgba(255,255,255,.65)', glowB: 'rgba(224,224,224,.5)', breath: '4s',
      wTop: 'rgba(124,77,255,0.5)', wMid: 'rgba(90,60,210,0.28)',
      wLine: '#ffffff', wSub: 'rgba(255,255,255,0.4)' },
    { id: 'dusk',       start: 17, end: 19,  c1: '#FF6F00', c2: '#FFA726',
      glowA: 'rgba(255,111,0,.6)', glowB: 'rgba(255,167,38,.5)', breath: '2.5s',
      wTop: 'rgba(255,255,255,0.6)', wMid: 'rgba(255,196,150,0.26)',
      wLine: '#ffffff', wSub: 'rgba(255,255,255,0.4)' },
    { id: 'night',      start: 19, end: 24,  c1: '#7C4DFF', c2: '#00BCD4',
      glowA: 'rgba(124,77,255,.6)', glowB: 'rgba(0,188,212,.45)', breath: '3s',
      wTop: 'rgba(190,250,255,0.5)', wMid: 'rgba(120,220,240,0.25)',
      wLine: '#ffffff', wSub: 'rgba(255,255,255,0.4)' }
  ];

  function periodFor(date) {
    var h = date.getHours();
    for (var i = 0; i < PERIODS.length; i++) {
      if (h >= PERIODS[i].start && h < PERIODS[i].end) return PERIODS[i];
    }
    return PERIODS[0];
  }

  var ball = U.$('#ball');
  var ring = U.$('#dropRing');
  var waterBox = U.$('#water');
  var fxEnabled = true;
  var busy = false;
  var curPalette = null; // 当前时段（含水体配色）

  /* ================ 时段光效 ================ */
  function applyFx(now) {
    var date = now || new Date();
    var p = periodFor(date);
    curPalette = p;
    var st = ball.style;
    st.setProperty('--c1', p.c1);
    st.setProperty('--c2', p.c2);
    st.setProperty('--glow-a', p.glowA);
    st.setProperty('--glow-b', p.glowB);
    st.setProperty('--breath', p.breath);
    ball.classList.toggle('static', !fxEnabled);
  }

  function scheduleFx() {
    applyFx();
    var now = new Date();
    var next = new Date(now);
    next.setMinutes(now.getMinutes() + 1, 0, 0);
    setTimeout(scheduleFx, next - now + 1000);
  }

  /* ================ 水位水波（SVG 波浪） ================
     思路：画一个覆盖整个球的水面 SVG——
     1) 水体：一条正弦曲线作为水面（随 phase 流动），水面以下填充半透明白；
     2) 水线：沿曲线画高亮描边，让“波浪轮廓”清晰；
     3) 次级波：错相位再叠一层淡波，增加立体感。
     水位 level（0~100）→ 水体中线大致位于 y = (1-level/100)*H。   */
  var NS = 'http://www.w3.org/2000/svg';
  var svgEl = null;
  var levelCur = 0;      // 当前显示水位（平滑过渡用）
  var levelTarget = 0;   // 目标水位（随当前时刻）

  function buildSvg() {
    svgEl = document.createElementNS(NS, 'svg');
    svgEl.setAttribute('viewBox', '0 0 100 100');
    svgEl.setAttribute('preserveAspectRatio', 'none');
    waterBox.appendChild(svgEl);
  }

  function waterTarget(date) {
    var mins = date.getHours() * 60 + date.getMinutes();
    return (mins / 1440) * 100; // 12:00 => 50
  }

  function sampleSurface(H, A, k, phase, step, fn) {
    for (var x = 0; x <= 100 + 1e-6; x += step) {
      var y = H + A * Math.sin(k * x + phase);
      fn(x, y);
    }
  }

  function renderWater(phase, dt) {
    if (!svgEl) return;
    var P = curPalette || {};
    if (levelCur !== levelTarget) {
      // 潮汐平滑升降：每秒移动约 25% 水位
      var d = Math.min(1, (dt / 1000) * 0.25);
      levelCur += (levelTarget - levelCur) * d;
      if (Math.abs(levelTarget - levelCur) < 0.05) levelCur = levelTarget;
    }
    var H = (1 - levelCur / 100) * 100;   // 水体中线（viewBox 0~100）
    if (H <= 0.5) H = 0.5;                // 满水时保留一点起伏可见
    var A = Math.max(1.6, Math.min(4.5, H * 0.06 + 1.2)); // 波幅自适应
    var k = Math.PI / 34;                 // 波长约 68（球内约一个半波）
    var step = 2;

    var body = 'M ';
    var line = '';
    var sub = '';
    var first = true;

    sampleSurface(H, A, k, phase, step, function (x, y) {
      var cmd = (first ? '' : ' L ') + x.toFixed(2) + ' ' + y.toFixed(2);
      body += cmd; line += cmd; first = false;
    });
    // 水体闭合到底部
    body += ' L 100 100 L 0 100 Z';

    // 次级波（错相 180°、更淡），仅生成一条描边增加层次
    first = true;
    sampleSurface(H, A * 0.8, k, phase + Math.PI, step * 2, function (x, y) {
      sub += (first ? 'M ' : ' L ') + x.toFixed(2) + ' ' + (y + 2.2).toFixed(2);
      first = false;
    });

    svgEl.innerHTML =
      '<defs><linearGradient id="wg" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + (P.wTop || 'rgba(255,255,255,0.42)') + '"/>' +
      '<stop offset="0.3" stop-color="' + (P.wMid || 'rgba(255,255,255,0.16)') + '"/>' +
      '<stop offset="1" stop-color="rgba(255,255,255,0)"/>' +
      '</linearGradient></defs>' +
      '<path d="' + body + '" fill="url(#wg)"/>' +
      '<path d="' + sub + '" fill="none" stroke="' + (P.wSub || 'rgba(255,255,255,0.28)') +
      '" stroke-width="1"/>' +
      '<path d="' + line + '" fill="none" stroke="' + (P.wLine || '#ffffff') +
      '" stroke-width="1.1" stroke-linecap="round"/>';
  }

  var lastTs = null;
  function waveLoop(ts) {
    if (!lastTs) lastTs = ts;
    var dt = Math.min(80, ts - lastTs);
    lastTs = ts;
    if (fxEnabled) {
      var phase = ts * 0.0009;   // 波浪水平流动速度
      renderWater(phase, dt);
    }
    requestAnimationFrame(waveLoop);
  }

  /* ================ 单击 / 双击（区分拖拽） ================ */
  var down = false, moved = false, downX = 0, downY = 0;

  ball.addEventListener('mousedown', function (e) {
    down = true; moved = false;
    downX = e.screenX; downY = e.screenY;
  });
  window.addEventListener('mousemove', function (e) {
    if (down && Math.abs(e.screenX - downX) + Math.abs(e.screenY - downY) > 6) moved = true;
  });
  window.addEventListener('mouseup', function () { down = false; });

  ball.addEventListener('click', function (e) {
    e.stopPropagation();
    if (moved) return;                       // 拖拽而非点击
    AI.call('show_window', 'chat').catch(function (err) { console.error(err); });
  });
  ball.addEventListener('dblclick', function (e) {
    e.stopPropagation();
    if (moved) return;
    // 规格：双击为预留入口（当前仅占位，无视觉文字）
    AI.call('show_window', 'chat').catch(function () {});
  });

  /* ================ 文件拖放：仅光圈反馈 ================ */
  function hasFiles(e) {
    return e.dataTransfer && Array.prototype.some.call(e.dataTransfer.types || [], function (t) {
      return t === 'Files';
    });
  }
  window.addEventListener('dragover', function (e) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (!busy) ring.classList.add('on');
  });
  window.addEventListener('dragleave', function () {
    if (!busy) ring.classList.remove('on');
  });
  window.addEventListener('drop', function (e) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    // 真正的处理由后端 DOM drop 监听完成，此处只负责反馈
    if (!busy) ring.classList.remove('on');
  });

  window.addEventListener('file-drop-state', function (ev) {
    var d = ev.detail || {};
    if (d.state === 'processing') {
      busy = true;
      ring.classList.add('on', 'busy');
    } else {
      busy = false;
      ring.classList.remove('on', 'busy');
    }
  });

  /* ================ 初始化 ================ */
  function start() {
    buildSvg();
    levelTarget = waterTarget(new Date());
    scheduleFx();
    requestAnimationFrame(waveLoop);
  }

  function loadSettings(tries) {
    if (!AI.available()) {
      if (tries > 0) { setTimeout(function () { loadSettings(tries - 1); }, 300); return; }
      start();
      return;
    }
    AI.call('get_settings').then(function (s) {
      if (s && typeof s.ball_light_effect === 'boolean') fxEnabled = s.ball_light_effect;
      if (s && s.ball && s.ball.size) {
        document.documentElement.style.setProperty('--ball-size', s.ball.size + 'px');
      }
      start();
    }).catch(function () { start(); });
  }
  loadSettings(10);
})();
