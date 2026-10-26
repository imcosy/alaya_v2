// amnesia.js — 忆点星光（H5）：星系脑图首页 / A邀请管理 / B接受邀请 / 找回对话
// 星系图：3D透视星系——大脑居中为恒星，记忆星球按倾斜轨道静止环绕（一级=四类记忆种子
//         event/value/style/person，与记忆种子模型同构；子节点LLM延展并继承种子类型）；
//         轨道球相机：拖动旋转 · 滚轮/双指缩放 · 双击星球进入主星球视角（卫星逐个入场） · 亮度=已找回记忆数；
//         本人点击=关联记忆+编辑双tab，亲友点击=转发表单
// 协议为 alaya 原生 WS（/ws）：init{user_id,token,owner_ai_id|invite_token} → ready；
//   start / 二进制16kPCM / stop{frame_id} / interrupt / chat{text,scene}；
//   服务端：stt / reply_delta / reply_done / memory / turn_done + 二进制48k PCM
(function () {
  // 登录态与主控制台共享：alaya_uid + alaya_token（一次登录全应用互通）
  const $ = (id) => document.getElementById(id);
  const TOKEN = new URLSearchParams(location.search).get('token') || '';
  const FOCUS_NODE = Number(new URLSearchParams(location.search).get('node') || 0);

  let USER = null;          // { user_id, username }
  let INVITE = null;        // 邀请详情
  let ws = null;
  let mic = null;
  let streaming = false;
  let ttsPlaying = false;
  let aiBubble = null;
let recoveredCount = 0;
let relSelected = '';

let faceImg = null;         // 星系中心用户照片（形象照；无则🧠）
let brainHit = null;        // 大脑屏幕位置/半径（点击拖拽判定用）
// ─── 摄像头模式 ──
let camActive = false, camStream = null, camFacing = 'user';

  // ─── toast（居中半透明） ───────────────────────────────
  let toastTimer = null;
  function toast(text) {
    const t = $('toast');
    t.textContent = text;
    t.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add('hidden'), 2600);
  }

  // ─── 登录态（与主控制台共享 alaya_uid/alaya_token） ──
  function sessionUid() { return localStorage.getItem('alaya_uid') || ''; }
  function sessionToken() { return localStorage.getItem('alaya_token') || ''; }
  function authQ(extra) {
    const p = new URLSearchParams({ user_id: sessionUid(), token: sessionToken() });
    Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
    return p.toString();
  }
  // ─── 部署根路径 ──
  // 本应用可以挂在任意 URL 前缀下（例如 /alaya/），直连端口时前缀为空。
  // API/WS 一律基于它拼接，绝不写死根绝对路径（否则带前缀部署时全 404）。
  const BASE = location.pathname.replace(/\/amnesia\/.*$/, '').replace(/\/$/, '');

  async function api(path, opts = {}) {
    const r = await fetch(`${BASE}/api${path}`, {
      method: opts.method || 'GET',
      headers: { 'Content-Type': 'application/json', 'X-User-Id': sessionUid(), 'X-Token': sessionToken() },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const j = await r.json().catch(() => ({ error: '网络错误' }));
    if (r.status === 401) throw new Error(j.error || j.detail || '未登录');
    return j;
  }

  // ─── TTS 播放（48k int16 二进制流，顺序调度防噼啪防断档；与主控制台同款） ──
  let aCtx = null, playQ = [], playAt = 0, playing = false;
  function playCtx() {
    if (!aCtx) aCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
    if (aCtx.state === 'suspended') aCtx.resume();
    return aCtx;
  }
  function playPcm(buf) {
    const ctx = playCtx();
    const i16 = new Int16Array(buf);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const aBuf = ctx.createBuffer(1, f32.length, 48000);
    aBuf.getChannelData(0).set(f32);
    playQ.push(aBuf);
    if (!ttsPlaying) {
      ttsPlaying = true;
      setMicState('speaking');
      if (mic) mic.setEchoMode(true);
    }
    if (!playing) drainPlay();
  }
  function drainPlay() {
    const ctx = playCtx();
    if (!playQ.length) {
      playing = false;
      if (ttsPlaying) {
        ttsPlaying = false;
        setMicState('idle');
        if (mic) mic.setEchoMode(false);
      }
      return;
    }
    playing = true;
    const aBuf = playQ.shift();
    const src = ctx.createBufferSource();
    src.buffer = aBuf; src.connect(ctx.destination);
    const now = Math.max(ctx.currentTime + 0.05, playAt);
    src.start(now);
    playAt = now + aBuf.duration;
    src.onended = () => drainPlay();
  }
  function stopPlay() {
    playQ = []; playAt = 0; playing = false;
    if (ttsPlaying) {
      ttsPlaying = false;
      setMicState('idle');
      if (mic) mic.setEchoMode(false);
    }
    if (aCtx) { try { aCtx.close(); } catch (e) {} aCtx = null; }
  }

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  // 记忆归属本体（galaxy 响应的 ai = owner：self=自己的本体AI / guest=对方本体）
  // 记忆归属的AI本体 id：星系已加载取星系下发值；未加载（登录即对话）取后端下发的 ai_me_id
  function ownerAid() { return (GAL && GAL.ai && GAL.ai.id) || MY_AI_ID || 0; }

  // ═══════════════════════════════════════════════════════
  // 星系脑图（Canvas 3D 透视星系：大脑为恒星，记忆星球沿倾斜轨道静止分布——不公转，
  //   保证鼠标/手指可稳定点中；轨道球相机仍可拖动旋转、滚轮/双指缩放、双击进主星球视角）
  // ═══════════════════════════════════════════════════════
  // 零预设：星球=真实记忆种子（alaya_seeds），聚合星团=AI 本体整理记忆时自然长出的概念
  // （alaya_concepts），突触=种子边（alaya_seed_edges）。不存在 A-L 之类的固定骨架。
  // 星球色相只按记忆种子类型（event/value/style/…，引擎下发的多元枚举）取色，前端不硬编码分支标签。
  const SEED_HUE = { event: 200, value: 45, style: 168, person: 340 };
  function seedHue(t) {
    const k = String(t || '');
    if (SEED_HUE[k] != null) return SEED_HUE[k];
    let h = 0;
    for (let i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) % 360;
    return h;
  }
  function nodeHue(n) {
    if (n.kind === 'concept') return 275;
    return seedHue(n.branch);
  }
  const cv = $('bg-canvas');
  const ctx = cv.getContext('2d');
  const FOV = 620;                  // 焦距：屏幕半径 = 世界半径 × FOV / 景深
  const FOCUS_DIST = 250, MIN_DIST = 230, MAX_DIST = 3000;
  let W = 0, H = 0, DPR = 1;

  // 背景星尘（随视角缓慢视差）
  const stars = Array.from({ length: 90 }, () => ({
    x: Math.random(), y: Math.random(),
    r: Math.random() * 1.4 + .3, a: Math.random() * .5 + .12,
    z: Math.random() * .8 + .2,
  }));

  let GAL = null;            // { mode, user, ai, invite, nodes, _byId, focus, sel }
  let lastVis = [];          // 最近一帧可见节点（命中检测用）

  // 轨道球相机：相机位于以视点(fx,fy,fz)为心、dist 为半径的球面上；(yaw,pitch)=方位/俯仰
  const orbit = { yaw: .42, tyaw: .42, pitch: .2, tpitch: .2, dist: 1200, tdist: 1200, fx: 0, fy: 0, fz: 0, tfx: 0, tfy: 0, tfz: 0 };

  function hash01(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 10000) / 10000;
  }

  function resizeCanvas() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = cv.clientWidth = innerWidth; H = cv.clientHeight = innerHeight;
    cv.width = W * DPR; cv.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  resizeCanvas();
  addEventListener('resize', resizeCanvas);

  // ─── 向量工具 ────────────────────────────────────────
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function cross(a, b) { return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }; }
  function vnorm(v) { const l = Math.hypot(v.x, v.y, v.z) || 1; return { x: v.x / l, y: v.y / l, z: v.z / l }; }
  function randUnit(seed) {                            // 确定性随机单位向量
    const a = hash01(seed + 'a') * Math.PI * 2;
    const z = hash01(seed + 'z') * 2 - 1;
    const r = Math.sqrt(Math.max(0, 1 - z * z));
    return { x: r * Math.cos(a), y: z, z: r * Math.sin(a) };
  }
  function orthoBasis(nrm) {                           // 由轨道法线构造面内正交基
    const up = Math.abs(nrm.y) > .9 ? { x: 1, y: 0, z: 0 } : { x: 0, y: 1, z: 0 };
    const u = vnorm(cross(up, nrm));
    return [u, cross(nrm, u)];
  }

  // ─── 布局：3D 力导向（真实习气网络自然聚合，零预设） ──
  //   星球=记忆种子，聚合星团=概念节点（AI 整理记忆时长出），突触=种子边。
  //   斥力铺开 + 突触/归属弹簧聚拢 + 向心收束，迭代后按固定相位一次算定世界坐标（不每帧抖动）。
  function layoutGalaxy() {
    if (!GAL) return;
    const byId = {};
    GAL.nodes.forEach(n => { byId[n.id] = n; });
    GAL._byId = byId;

    const nodes = GAL.nodes;
    const N = Math.max(nodes.length, 1);
    const R = 300 + Math.sqrt(N) * 62;                 // 网络半径随规模增长
    const idx = new Map();
    nodes.forEach((n, i) => { idx.set(n.id, i); n._p3 = randUnit('p0' + n.id); });
    // 概念星团中心：黄金角螺旋铺满球面（互不重叠），种子初始落在其概念中心附近
    const concepts = nodes.filter(n => n.kind === 'concept');
    const CN = Math.max(concepts.length, 1);
    concepts.forEach((c, i) => {
      const ga = i * 2.399963;
      const ny = 1 - 2 * (i + .5) / CN;
      const nxz = Math.sqrt(Math.max(0, 1 - ny * ny));
      const d = R * .42;
      c._p3 = { x: d * nxz * Math.cos(ga), y: d * ny, z: d * nxz * Math.sin(ga) };
    });
    nodes.forEach(n => {
      if (n.kind === 'seed' && n.parent_id && byId[n.parent_id]) {
        const c = byId[n.parent_id]._p3;
        n._p3 = { x: c.x + randUnit('c' + n.id).x * 60, y: c.y + randUnit('c' + n.id).y * 60, z: c.z + randUnit('c' + n.id).z * 60 };
      }
    });
    const E = (GAL.edges || []).map(e => ({ a: idx.get(e.src), b: idx.get(e.dst), w: clamp(e.weight || .3, .1, 1) }))
      .filter(e => e.a != null && e.b != null);
    const link = (i, j, rest, k) => {
      const a = nodes[i]._p3, b = nodes[j]._p3;
      let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
      const d = Math.hypot(dx, dy, dz) || .01;
      const f = (d - rest) * k / d;                    // 弹簧：拉向静止长度
      dx *= f; dy *= f; dz *= f;
      a.x += dx; a.y += dy; a.z += dz;
      b.x -= dx; b.y -= dy; b.z -= dz;
    };
    for (let it = 0; it < 320; it++) {
      // 斥力（全对，规模小、迭代有限，开销可控）
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i]._p3, b = nodes[j]._p3;
          let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
          let d2 = dx * dx + dy * dy + dz * dz;
          if (d2 < 1) { d2 = 1; dx = (hash01('j' + i + j) - .5); dy = (hash01('j2' + i + j) - .5); dz = (hash01('j3' + i + j) - .5); }
          const d = Math.sqrt(d2);
          const rep = 26000 / d2;
          const ux = dx / d, uy = dy / d, uz = dz / d;
          a.x += ux * rep; a.y += uy * rep; a.z += uz * rep;
          b.x -= ux * rep; b.y -= uy * rep; b.z -= uz * rep;
        }
      }
      // 突触弹簧 + 归属弹簧
      for (const e of E) link(e.a, e.b, 130 + (1 - e.w) * 150, .05);
      for (const n of nodes) {
        if (n.kind === 'seed' && n.parent_id && byId[n.parent_id]) {
          link(idx.get(n.id), idx.get(n.parent_id), 105, .07);
        }
      }
      // 向心收束（防飞散）+ 概念星团轻微互斥
      for (const n of nodes) {
        const p = n._p3;
        const d = Math.hypot(p.x, p.y, p.z) || .01;
        const pull = n.kind === 'concept' ? .006 : .014;
        const k = Math.max(0, d - R) * pull;
        p.x -= p.x / d * k; p.y -= p.y / d * k; p.z -= p.z / d * k;
      }
    }
    // 概念星团与种子重合时轻微外推，避免完全叠死（保留聚合观感）
    nodes.forEach(n => {
      if (n.kind === 'seed' && n.parent_id && byId[n.parent_id]) {
        const c = byId[n.parent_id]._p3, p = n._p3;
        let dx = p.x - c.x, dy = p.y - c.y, dz = p.z - c.z;
        const d = Math.hypot(dx, dy, dz) || .01;
        const want = 62;
        if (d < want) {
          const f = (want - d) / d;
          p.x += dx * f; p.y += dy * f; p.z += dz * f;
        }
      }
    });
    nodes.forEach(n => { n._hue = nodeHue(n); });
  }

  function baseDist() { return Math.round(clamp(Math.min(W, H) * 1.05, 820, 1500)); }
  function setDist(d) { orbit.tdist = clamp(d, MIN_DIST, MAX_DIST); }

  function resetOrbit() {
    orbit.yaw = orbit.tyaw = .42;
    orbit.pitch = orbit.tpitch = .2;
    orbit.dist = orbit.tdist = baseDist();
    orbit.fx = orbit.tfx = 0; orbit.fy = orbit.tfy = 0; orbit.fz = orbit.tfz = 0;
  }

  // ─── 渲染主循环（深空底 + 星尘视差 + 星系） ──────────────
  (function frame() {
    requestAnimationFrame(frame);
    ctx.fillStyle = '#070b16';
    ctx.fillRect(0, 0, W, H);
    let g = ctx.createRadialGradient(W / 2, H * .38, 0, W / 2, H * .38, Math.max(W, H) * .8);
    g.addColorStop(0, 'rgba(46,38,96,.35)'); g.addColorStop(1, 'rgba(7,11,22,0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    // 星尘（视差：随星系相机偏移）
    const par = (GAL && !$('view-galaxy').classList.contains('hidden')) ? 1 : 0;
    for (const st of stars) {
      const ox = par ? orbit.yaw * 26 * st.z : 0;    // 视角视差
      const oy = par ? orbit.pitch * 18 * st.z : 0;
      const sx = (st.x * W + ox + W) % W, sy = (st.y * H + oy + H) % H;
      ctx.beginPath(); ctx.arc(sx, sy, st.r, 0, 6.29);
      ctx.fillStyle = `rgba(170,200,255,${st.a})`; ctx.fill();
    }
    if (par) drawGalaxy(performance.now());
  })();

  // ─── 可见性：习气网络无层级遮蔽，全部星球常显（入场各带一次弹出动画） ──
  function visibleNodes(now) {
    for (const n of GAL.nodes) if (!n._born) n._born = now + hash01('b' + n.id) * 260;
    return GAL.nodes;
  }

  // 记忆种子星球世界半径：权重定大小（权重=被反复想起的强度），概念星团更大
  function nodeWorldR(n) {
    if (n.kind === 'concept') return 20 + Math.min(1, (n.seed_count || 0) / 6) * 16;
    const t = Math.min(1, Math.max(0, (n.weight || 1) - 1) / 4);   // weight 1→5 映射 0→1
    return 7 + t * 9;
  }
  // 星球亮度：权重 + 被回忆次数（越常想起越亮）
  function nodeLit(n) {
    if (n.kind === 'concept') return .85;
    const w = Math.min(1, Math.max(0, (n.weight || 1) - 1) / 4);
    const rc = Math.min(1, (n.recall_count || 0) / 6);
    return .45 + Math.max(w, rc) * .55;
  }

  function drawGalaxy(now) {
    // 相机缓动（视角/距离/视点）
    orbit.yaw += (orbit.tyaw - orbit.yaw) * .12;
    orbit.pitch += (orbit.tpitch - orbit.pitch) * .12;
    orbit.dist += (orbit.tdist - orbit.dist) * .1;
    if (GAL.focus && GAL._byId[GAL.focus]) {
      const p = GAL._byId[GAL.focus]._p3;    // 主星球视角：视点固定在该星球上
      orbit.fx = p.x; orbit.fy = p.y; orbit.fz = p.z;
    } else {
      orbit.fx += (orbit.tfx - orbit.fx) * .12;
      orbit.fy += (orbit.tfy - orbit.fy) * .12;
      orbit.fz += (orbit.tfz - orbit.fz) * .12;
    }
    // 透视投影：世界 → 屏幕（屏幕半径 = 世界半径 × 焦距/景深）
    const cp = Math.cos(orbit.pitch), sp = Math.sin(orbit.pitch);
    const cy = Math.cos(orbit.yaw), sy = Math.sin(orbit.yaw);
    const proj = (p3) => {
      const vx = p3.x - orbit.fx, vy = p3.y - orbit.fy, vz = p3.z - orbit.fz;
      const depth = orbit.dist - (sy * cp * vx + sp * vy + cy * cp * vz);
      if (depth < 12) return null;
      const f = FOV / depth;
      return {
        sx: W / 2 + (vx * cy - vz * sy) * f,
        sy: H / 2 - (-sp * sy * vx + cp * vy - sp * cy * vz) * f,
        depth, f,
      };
    };
    const vis = visibleNodes(now);
    // ─── 宇宙级退离：聚焦态下，非聚焦链的星球以大脑为心拉伸 ×6 退到深空远景
    // （只剩聚焦星球 + 其直接关联的突触邻居，互不遮挡）
    const chainSet = new Set();
    if (GAL.focus && GAL._byId[GAL.focus]) {
      chainSet.add(GAL.focus);
      for (const e of (GAL.edges || [])) {                 // 突触邻居：与聚焦星球直接相连的星球
        if (e.src === GAL.focus) chainSet.add(e.dst);
        else if (e.dst === GAL.focus) chainSet.add(e.src);
      }
    }
    for (const n of vis) {
      n._retired = !!GAL.focus && !chainSet.has(n.id);   // 退离星=深空背景，不可点
      n._pr = proj(n._retired
        ? { x: n._p3.x * 6, y: n._p3.y * 6, z: n._p3.z * 6 }   // 相对大脑(0,0,0)向外推远
        : n._p3);
    }
    lastVis = vis;
    // — 习气网络的突触连线：种子共现/因果/修正边 + 概念归属边 + 概念间边 —
    if (GAL.edges && GAL.edges.length) {
      for (const e of GAL.edges) {
        const a = GAL._byId[e.src], b = GAL._byId[e.dst];
        if (!a || !b || !a._pr || !b._pr || a._retired || b._retired) continue;
        const w = clamp(e.weight || .3, .1, 1);
        if (e.relation === 'sub') {
          ctx.strokeStyle = `hsla(275,72%,74%,${.06 + w * .1})`;
          ctx.lineWidth = clamp(1.2 * a._pr.f, .5, 1.8);
        } else {
          ctx.strokeStyle = `hsla(${(a._hue + b._hue) / 2},72%,72%,${.05 + w * .16})`;
          ctx.lineWidth = clamp(1.5 * a._pr.f, .5, 2);
        }
        ctx.beginPath(); ctx.moveTo(a._pr.sx, a._pr.sy); ctx.lineTo(b._pr.sx, b._pr.sy); ctx.stroke();
      }
    }
    // — 星球 + 大脑按景深排序（远的先画，近的覆盖在上） —
    const items = [];
    const bp = proj({ x: 0, y: 0, z: 0 });
    if (bp) items.push({ _pr: bp, depth: 0, _hue: 265, _brain: true });
    for (const n of vis) if (n._pr) items.push(n);
    items.sort((a, b) => b._pr.depth - a._pr.depth);
    for (const n of items) {
      const f = n._pr.f, sx = n._pr.sx, sy2 = n._pr.sy;
      let worldR = n._brain ? 82 : nodeWorldR(n);
      if (n._retired) worldR *= .35;                             // 退离星：缩成深空远景小球
      const pop = n._born ? Math.min(1, (now - n._born) / 420) : 1;
      if (pop <= 0) continue;
      const ease = pop < 1 ? 1 - Math.pow(1 - pop, 3) : 1;
      const sr = Math.max(.8, worldR * f * ease);
      if (sr < .75) { if (n._brain) brainHit = null; continue; }  // 太远 → 暗到消失
      const hue = n._hue || 265;
      if (n._brain) {
        const glow = ctx.createRadialGradient(sx, sy2, 0, sx, sy2, sr * 2.2);
        glow.addColorStop(0, 'rgba(124,108,255,.30)'); glow.addColorStop(1, 'rgba(124,108,255,0)');
        ctx.fillStyle = glow;
        ctx.beginPath(); ctx.arc(sx, sy2, sr * 2.2, 0, 6.29); ctx.fill();
        for (let i = 0; i < 3; i++) {
          const rr = sr * (.9 + i * .3) * (1 + .03 * Math.sin(now / 1400 + i * 1.7));
          ctx.strokeStyle = `rgba(143,124,255,${.26 - i * .07})`;
          ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.arc(sx, sy2, rr, 0, 6.29); ctx.stroke();
        }
        ctx.font = `${clamp(sr * .78, 16, 58)}px serif`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        if (faceImg && faceImg.complete && faceImg.naturalWidth) {
          // 用户照片（圆形裁切）：先存屏幕位置供点击拖拽判定
          brainHit = { sx, sy2, r: sr };
          ctx.save();
          ctx.beginPath(); ctx.arc(sx, sy2, sr * .92, 0, 6.29); ctx.clip();
          ctx.drawImage(faceImg, sx - sr * .92, sy2 - sr * .92, sr * 1.84, sr * 1.84);
          ctx.restore();
          ctx.strokeStyle = 'rgba(143,124,255,.55)';
          ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(sx, sy2, sr * .92, 0, 6.29); ctx.stroke();
        } else {
          brainHit = null;
          ctx.fillText('🧠', sx, sy2);
        }
        if (sr > 26 && GAL.ai) {
          ctx.font = `${clamp(13 * f / .5, 11, 15)}px sans-serif`;
          ctx.fillStyle = 'rgba(200,208,235,.85)';
          ctx.fillText(`${GAL.ai.name || '我'}的星系`, sx, sy2 + sr + 16);
        }
        continue;
      }
      const t = nodeLit(n);                                      // 亮度：权重 + 被回忆次数
      const fade = clamp(1.5 - n._pr.depth / 1500, .16, 1);      // 推远暗化
      const tw = .86 + .14 * Math.sin(now / 900 + hash01('t' + n.id) * 6.28);
      let alpha = (.32 + t * .68) * fade * tw;
      if (n._retired) alpha *= .18;                              // 退离星：压暗成背景
      ctx.shadowColor = `hsla(${hue},90%,72%,${alpha})`;
      ctx.shadowBlur = 13 * f * (n.kind === 'concept' ? 1.6 : 1);
      ctx.fillStyle = `hsla(${hue},88%,${58 + t * 14}%,${alpha})`;
      ctx.beginPath(); ctx.arc(sx, sy2, sr, 0, 6.29); ctx.fill();
      ctx.shadowBlur = 0;
      if (n.kind === 'concept') {                                // 概念星团：外圈光环（聚合感）
        ctx.strokeStyle = `hsla(${hue},80%,78%,${.14 + .12 * t})`;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(sx, sy2, sr * 1.5, 0, 6.29); ctx.stroke();
      }
      if (GAL.sel === n.id || GAL.focus === n.id) {             // 选中/主视角脉冲圈
        ctx.strokeStyle = `hsla(${hue},95%,80%,${.5 + .3 * Math.sin(now / 260)})`;
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(sx, sy2, sr + 7 + 2 * Math.sin(now / 260), 0, 6.29); ctx.stroke();
      }
      if (!n._retired && grabId === n.id) {                    // 按住抓取：高亮十字准星
        ctx.strokeStyle = 'rgba(255,255,255,.75)';
        ctx.lineWidth = 1;
        const gr = sr + 14;
        ctx.beginPath(); ctx.arc(sx, sy2, gr, 0, 6.29); ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(sx - gr - 4, sy2); ctx.lineTo(sx - gr + 5, sy2);
        ctx.moveTo(sx + gr - 5, sy2); ctx.lineTo(sx + gr + 4, sy2);
        ctx.stroke();
      }
      if (worldR * f >= 7) {                                    // 拉近显化标签
        ctx.font = `${clamp(13 * f / .5, 11, 22)}px sans-serif`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillStyle = `rgba(215,222,240,${clamp((worldR * f - 7) / 8, .3, .92)})`;
        ctx.fillText(n.name, sx, sy2 + sr + 5);
      }
    }
  }

  // ─── 主视角（聚焦）：双击/点击星球进入，相机拉近该星球，其余退离深空 ──
  function focusOn(n, now) {
    if (!n) return;
    GAL.focus = n.id;
    orbit.tdist = FOCUS_DIST;
    orbit.tfx = n._p3.x; orbit.tfy = n._p3.y; orbit.tfz = n._p3.z;
    $('btn-galaxy-back').classList.remove('hidden');
    $('galaxy-tip').textContent = `主视角「${n.name}」· 按住拖拽旋转 · 点中心星球看记忆 · 点空白返回全景`;
  }
  function unfocus(now) {
    if (!GAL) return;
    GAL.focus = null;
    orbit.tdist = baseDist();
    orbit.tfx = orbit.tfy = orbit.tfz = 0;
    $('btn-galaxy-back').classList.add('hidden');
    $('galaxy-tip').textContent = GAL.mode === 'self'
      ? '拖拽旋转 · 滚轮/双指缩放 · 点星球看记忆 · 点空白返回全景'
      : '拖拽旋转 · 滚轮/双指缩放 · 点星球可转发给亲友';
  }

  // ─── 交互：按住拖拽=旋转（抓住星球）/ 滚轮·双指缩放 / 点击=主视角 / 点主星球=详情 ──
  const pointers = new Map();
  let pinchD = 0, moved = 0, downAt = 0, grabId = 0;
  let brainDrag = false;      // 抓住大脑（用户照片）拖拽 = 平移整个星系

  function inGalaxy() { return GAL && !$('view-galaxy').classList.contains('hidden'); }
  // 顶栏按钮/表单/弹窗上的手势不进入星系控制
  function onUIEl(e) { return e.target.closest && e.target.closest('#topbar, button, input, textarea, select, a, .mask, .sheet'); }
  // 点中大脑（用户照片）：允许按住拖拽移动；点击（未拖动）不触发退焦
  function hitBrain(px, py) {
    if (!brainHit) return false;
    return Math.hypot(px - brainHit.sx, py - brainHit.sy2) <= Math.max(30, brainHit.r + 10);
  }

  document.addEventListener('pointerdown', (e) => {
    if (!inGalaxy() || onUIEl(e)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = 0; downAt = performance.now();
    brainDrag = pointers.size === 1 && hitBrain(e.clientX, e.clientY);
    grabId = (hitTest(e.clientX, e.clientY) || {}).id || 0;   // 抓住星球拖拽（视觉反馈）
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchD = Math.hypot(a.x - b.x, a.y - b.y);
    }
  });
  document.addEventListener('pointermove', (e) => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId);
    const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved += Math.abs(dx) + Math.abs(dy);
    if (pointers.size === 1) {                       // 单指拖动 = 旋转（3D 环绕视角）
      if (brainDrag) {                               // 抓住大脑拖拽 = 平移星系（视点跟随）
        orbit.tfx -= dx * orbit.dist / FOV;
        orbit.tfy += dy * orbit.dist / FOV;
      } else {
        orbit.tyaw += dx * 0.0052;
        orbit.tpitch = clamp(orbit.tpitch + dy * 0.0044, -1.15, 1.15);
      }
    } else if (pointers.size === 2) {                // 双指捏合 = 缩放
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchD > 0 && d > 0) setDist(orbit.tdist * pinchD / d);
      pinchD = d;
    }
  });
  function endPointer(e) {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    const grabbed = grabId; grabId = 0;
    const wasBrainDrag = brainDrag; brainDrag = false;
    if (pointers.size < 2) pinchD = 0;
    if (pointers.size !== 0 || !inGalaxy() || moved >= 10 || performance.now() - downAt >= 500) return;
    if (wasBrainDrag) return;                    // 拖过大脑 = 平移，不再触发点击逻辑
    const hit = hitTest(e.clientX, e.clientY);
    // 点击星球：已是主视角中心的 → 打开详情弹窗；否则 → 切入该星主视角（其余星球退离）
    if (hit) {
      if (GAL.focus === hit.id) selectNode(hit);
      else focusOn(hit, performance.now());
    } else if (GAL.focus && !hitBrain(e.clientX, e.clientY)) {
      unfocus(performance.now());                     // 主视角态点空白 = 返回全景
    }
  }
  document.addEventListener('pointerup', endPointer);
  document.addEventListener('pointercancel', endPointer);
  document.addEventListener('wheel', (e) => {       // 滚轮缩放（以指针位置为锚）
    if (!inGalaxy()) return;
    e.preventDefault();
    setDist(orbit.tdist * Math.exp(-e.deltaY * 0.0009));
  }, { passive: false });

  function hitTest(px, py) {
    let best = null, bestD = 1e18;
    for (const n of lastVis) {
      if (!n._pr || n._retired) continue;          // 退离背景星不可点
      const d = Math.hypot(px - n._pr.sx, py - n._pr.sy);
      const sr = nodeWorldR(n) * n._pr.f;
      if (d < Math.max(28, sr + 12) && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  // ─── 多视角：忆点星光（人物脉络+事件星） / 习气网络 / 人生脉络 / 末那识中枢 ───
  //   同一批底层种子（alaya_seeds）的不同投影：忆点星光按「人物」组织，看的是「与谁的事还暗着」。
  const LIFE_STAGE_CN = { childhood: '童年', school: '求学', career: '工作',
                          love: '婚恋', family: '家庭', midlife: '中年' };
  let GAL_VIEW = 'stars';
  let LIFE = null;

  function lifeStageName(key) {
    const s = ((LIFE && LIFE.stages) || []).find(x => x.key === key);
    return (s && s.name) || LIFE_STAGE_CN[key] || key;
  }

  // 只同步视角的 DOM 状态（不取数、不重排），供 showGalaxy 复用
  function applyGalView() {
    const threads = GAL_VIEW === 'threads';
    const manas = GAL_VIEW === 'manas';
    const starsView = GAL_VIEW === 'stars';
    document.querySelectorAll('[data-gview]').forEach(el =>
      el.classList.toggle('on', el.dataset.gview === GAL_VIEW));
    document.body.classList.toggle('life-view', threads);
    document.body.classList.toggle('manas-view', manas);
    document.body.classList.toggle('stars-view', starsView);
    $('life-threads').classList.toggle('hidden', !threads);
    $('manas-hud').classList.toggle('hidden', !manas);
    $('stars-hud').classList.toggle('hidden', !starsView);
    $('galaxy-stats').classList.toggle('hidden', threads || manas || starsView);
    $('galaxy-tip').classList.toggle('hidden', threads || manas || starsView);
    // 末那识中枢视角：习气网络接管指针（可拖拽/缩放/点击星球看记忆）
    if (window.HabitGraph) HabitGraph.setInteractive(manas);
    // 从对话/其它视图回到中枢视角：重新拉起实时轮询（幂等）
    if (manas) startManasLive(); else stopManasLive();
    // 忆点星光：自己的星图自己画（暗星微呼吸、亮星闪烁，需要持续重绘）
    if (starsView) { if (STARS === null) loadStars(); else startStarsLoop(); } else stopStarsLoop();
  }

  function setGalView(v) {
    GAL_VIEW = (v === 'threads' || v === 'manas' || v === 'stars') ? v : 'network';
    applyGalView();
    if (GAL_VIEW === 'threads') loadLifeThreads();
    else if (GAL_VIEW === 'manas') startManasLive();
    else if (GAL_VIEW === 'stars') loadStars();
    else { stopManasLive(); if (GAL) { layoutGalaxy(); updateGalaxyHud(); } }
  }

  // ─── 忆点星光：人物星云 → 事件星 ─────────────────────────
  // 一颗星＝一件你们之间的事（后端 alaya_galaxy_stars；星的内容＝该事件束下的记忆种子）。
  // 暗星＝灰调 + 微呼吸（还没收到亲友补充）；亮星＝金色 + 闪烁（已被点亮）。
  // 主人：点人物→生成转发；点星→看内容并补充；双击空白→加一颗星。
  // 亲友：凭链接进来只见「你和他」这一族星；未登录可先看，点星时才要求登录。
  let STARS = null;              // 主人视角 { persons, stats }
  let STAR_GUEST = null;         // 亲友视角（带 token 进来）
  let STAR_SCOPE = 0;            // 当前进入的人物 person_id（0＝人物星云层）
  let STAR_RAF = 0, STAR_HIT = [];
  let STAR_REC = null, STAR_CHUNKS = [];

  function starsData() {
    if (TOKEN) {
      const p = (STAR_GUEST && STAR_GUEST.person) || {};
      const ss = (STAR_GUEST && STAR_GUEST.stars) || [];
      return {
        persons: p.id ? [{ person_id: p.id, name: p.name || 'TA', relation: p.relation || '',
                           emoji: p.emoji || '', stars: ss, total: ss.length,
                           lit: ss.filter(s => s.lit).length }] : [],
        stats: (STAR_GUEST && STAR_GUEST.stats) || {}
      };
    }
    return STARS || { persons: [], stats: {} };
  }

  function starsScopePerson() {
    return starsData().persons.find(p => p.person_id === STAR_SCOPE) || null;
  }

  async function loadStars(keepScope) {
    let scope = keepScope ? STAR_SCOPE : 0;
    try {
      if (TOKEN) {
        STAR_GUEST = await api('/amnesia/starview?token=' + encodeURIComponent(TOKEN));
        // 亲友视角只有「我与他」这一族星：直接进这片星空（不必先看只有一个人物节点的人物层）
        scope = Number((STAR_GUEST.person || {}).id || 0);
      } else {
        STARS = await api('/amnesia/stars');
      }
    } catch (e) {
      if (TOKEN) toast(e.message || '链接无效');
      STARS = { persons: [], stats: {} };
    }
    STAR_SCOPE = scope;
    updateStarsHud();
    startStarsLoop();
  }

  function updateStarsHud() {
    const st = starsData().stats || {};
    const p = starsScopePerson();
    $('stars-scope').textContent = STAR_SCOPE ? ('「' + (p ? p.name : '') + '」的星空') : '人物星云';
    $('stars-total').textContent = st.stars || 0;
    $('stars-lit').textContent = st.lit || 0;
    $('btn-stars-organize').classList.toggle('hidden', !!TOKEN);
    $('btn-galaxy-back').classList.toggle('hidden', !STAR_SCOPE || !!TOKEN);
    const tip = $('galaxy-tip');
    tip.textContent = STAR_SCOPE
      ? (TOKEN ? '点一颗星，把你知道的讲出来' : '点一颗星看内容 · 双击空白加一颗星')
      : (TOKEN ? '这是TA与你的星空' : '点人物 → 把这一族星发给他');
    tip.classList.remove('hidden');
  }

  function startStarsLoop() {
    $('stars-canvas').classList.remove('hidden');
    updateStarsHud();
    if (!STAR_RAF) STAR_RAF = requestAnimationFrame(starsLoop);
  }

  function stopStarsLoop() {
    if (STAR_RAF) cancelAnimationFrame(STAR_RAF);
    STAR_RAF = 0;
    $('stars-canvas').classList.add('hidden');
  }

  function starsLoop(now) {
    if (GAL_VIEW !== 'stars') { STAR_RAF = 0; return; }
    drawStars(now);
    STAR_RAF = requestAnimationFrame(starsLoop);
  }

  // ─── 3D 星云：实时透视投影（可拖拽旋转 / 滚轮或双指缩放）──────────
  // 星不再钉在平面上：同一片星云换个角度看，前后景有大小差、明暗差与穿插遮挡，
  // 与习气网络/末那识中枢的手感一致（拖拽旋转 · 缩放 · 点选）。
  const STAR_FOV = 880;                 // 焦距：越小透视越夸张
  let STAR_ROT = { x: -0.10, y: 0.35 }; // 当前视角（水平拖改 y、垂直拖改 x）
  let STAR_ZOOM = 1;
  let STAR_C = { x: 0, y: 0 };          // 视口中心（每帧刷新）

  // Fibonacci 球面：把 n 个点均匀铺在球面上（转起来没有"排线感"）
  function starSphere(i, n, R) {
    const ga = Math.PI * (3 - Math.sqrt(5));
    const y = 1 - (i + 0.5) * 2 / Math.max(1, n);
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = ga * i;
    return [Math.cos(th) * r * R, y * R, Math.sin(th) * r * R];
  }

  // 旋转（水平拖绕 Y、垂直拖绕 X）→ 透视投影
  function starProject(p, cx, cy) {
    const cyy = Math.cos(STAR_ROT.y), syy = Math.sin(STAR_ROT.y);
    const cxx = Math.cos(STAR_ROT.x), sxx = Math.sin(STAR_ROT.x);
    const x1 = p[0] * cyy - p[2] * syy;
    const z1 = p[0] * syy + p[2] * cyy;
    const y1 = p[1] * cxx - z1 * sxx;
    const z2 = p[1] * sxx + z1 * cxx;
    const k = STAR_FOV / (STAR_FOV + z2) * STAR_ZOOM;
    return { x: cx + x1 * k, y: cy + y1 * k, z: z2, k: k };
  }

  function drawStars(now) {
    const c = $('stars-canvas');
    const r = c.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = Math.round(r.width * dpr), H = Math.round(r.height * dpr);
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, r.width, r.height);
    STAR_HIT = [];
    STAR_C = { x: r.width / 2, y: r.height / 2 };
    const cx = STAR_C.x, cy = STAR_C.y, t = now / 1000;
    g.textAlign = 'center';
    const R = Math.min(r.width, r.height) * 0.34;
    if (STAR_SCOPE) {
      const p = starsScopePerson();
      const stars = (p && p.stars) || [];
      starCore(g, starProject([0, 0, 0], cx, cy), (p && p.name) || 'TA', t, 0.72);
      if (!stars.length) {
        g.font = '500 13px system-ui,sans-serif';
        g.fillStyle = 'rgba(255,255,255,.55)';
        g.fillText(TOKEN ? '这一族星还空着' : '还没记下你们之间的事 · 双击空白加一颗星', cx, cy + 96);
        return;
      }
      starDrawAll(g, stars.map((s, i) => ({ p3: starSphere(i, stars.length, R), kind: 'star', data: s })), t);
    } else {
      const persons = starsData().persons || [];
      starCore(g, starProject([0, 0, 0], cx, cy), '我', t, 1);
      if (!persons.length) {
        g.font = '500 13px system-ui,sans-serif';
        g.fillStyle = 'rgba(255,255,255,.6)';
        g.fillText(TOKEN ? 'TA还没记下与你的往事' : '如意还没理出人物脉络——先和TA聊聊你的过往', cx, cy + 96);
        return;
      }
      starDrawAll(g, persons.map((p, i) => ({ p3: starSphere(i, persons.length, R), kind: 'person', data: p })), t);
    }
  }

  // 中心光核（"我" / 某个人的星空中心）
  function starCore(g, pt, label, t, scale) {
    const k = scale * (1 + 0.055 * Math.sin(t * 1.6)) * Math.min(1.5, pt.k);
    const rg = g.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, 74 * k);
    rg.addColorStop(0, 'rgba(255,255,255,.92)');
    rg.addColorStop(.35, 'rgba(150,200,255,.42)');
    rg.addColorStop(1, 'rgba(120,170,255,0)');
    g.fillStyle = rg;
    g.beginPath(); g.arc(pt.x, pt.y, 74 * k, 0, Math.PI * 2); g.fill();
    g.font = '600 13px system-ui,-apple-system,"PingFang SC",sans-serif';
    g.fillStyle = '#fff';
    g.fillText(label, pt.x, pt.y + 5);
  }

  // 按深度排序绘制（远的先画，近的后画盖住它——3D 的穿插感就来自这里），顺带登记命中区
  function starDrawAll(g, items, t) {
    const cx = STAR_C.x, cy = STAR_C.y;
    const proj = items.map(it => ({ it: it, pt: starProject(it.p3, cx, cy) }));
    proj.sort((a, b) => b.pt.z - a.pt.z);
    for (const o of proj) {
      const depth = Math.max(0.34, Math.min(1.3, o.pt.k));   // 近大远小、近亮远暗
      if (o.it.kind === 'star') starDrawOne(g, o.pt, o.it.data, t, depth);
      else starDrawPerson(g, o.pt, o.it.data, t, depth);
    }
  }

  // 人物节点（星多的靠近"正面"感觉更大；亮得多偏金，全暗偏灰蓝）
  function starDrawPerson(g, pt, p, t, depth) {
    const rr = (9 + Math.min(15, Math.sqrt(p.total || 0) * 2.2)) * depth;
    const ratio = p.total ? (p.lit / p.total) : 0;
    const col = ratio > 0 ? 'rgba(255,' + (190 + Math.round(40 * ratio)) + ',120,' : 'rgba(150,170,200,';
    g.strokeStyle = 'rgba(140,180,240,' + (0.04 + 0.13 * depth).toFixed(3) + ')';
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(STAR_C.x, STAR_C.y); g.lineTo(pt.x, pt.y); g.stroke();
    const bp = 1 + 0.10 * Math.sin(t * 1.2 + p.person_id * 0.3);
    const grad = g.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, rr * 2.6 * bp);
    grad.addColorStop(0, col + (0.55 + 0.4 * depth).toFixed(3) + ')');
    grad.addColorStop(.45, col + (0.10 + 0.18 * depth).toFixed(3) + ')');
    grad.addColorStop(1, col + '0)');
    g.fillStyle = grad;
    g.beginPath(); g.arc(pt.x, pt.y, rr * 2.6 * bp, 0, Math.PI * 2); g.fill();
    g.fillStyle = col + Math.min(1, 0.5 + 0.5 * depth).toFixed(3) + ')';
    g.beginPath(); g.arc(pt.x, pt.y, rr * 0.5, 0, Math.PI * 2); g.fill();
    g.font = '500 12px system-ui,-apple-system,"PingFang SC",sans-serif';
    g.fillStyle = 'rgba(255,255,255,' + (0.35 + 0.55 * Math.min(1, depth)).toFixed(3) + ')';
    g.fillText(p.name + (p.total ? ' · ' + p.total : ''), pt.x, pt.y + rr * 2.6 * bp + 13);
    STAR_HIT.push({ x: pt.x, y: pt.y, z: pt.z, r: Math.max(20, rr * 1.9) , type: 'person', data: p });
  }

  // 事件星（暗星灰调微呼吸 · 亮星金色闪烁；越近越亮越大）
  function starDrawOne(g, pt, s, t, depth) {
    const on = !!s.lit;
    const rr = (5.5 + Math.min(7, (s.seeds || []).length * 1.6)) * depth;
    let col, a;
    if (on) {                                      // 亮星：金色闪烁
      col = 'rgba(255,206,120,'; a = (0.72 + 0.28 * Math.sin(t * 3.1 + s.star_id * 0.7));
    } else {                                       // 暗星：灰调 + 微呼吸（不闪）
      col = 'rgba(150,166,196,'; a = (0.38 + 0.16 * Math.sin(t * 0.9 + s.star_id * 0.4));
    }
    a = Math.max(0.08, Math.min(1, a * (0.55 + 0.5 * depth)));      // 远处更暗
    const grad = g.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, rr * 3.4);
    grad.addColorStop(0, col + Math.min(1, a + 0.25).toFixed(3) + ')');
    grad.addColorStop(.5, col + (a * 0.5).toFixed(3) + ')');
    grad.addColorStop(1, col + '0)');
    g.fillStyle = grad;
    g.beginPath(); g.arc(pt.x, pt.y, rr * 3.4, 0, Math.PI * 2); g.fill();
    g.fillStyle = col + Math.min(1, a + 0.35).toFixed(3) + ')';
    g.beginPath(); g.arc(pt.x, pt.y, on ? rr * 0.68 : rr * 0.48, 0, Math.PI * 2); g.fill();
    STAR_HIT.push({ x: pt.x, y: pt.y, z: pt.z, r: Math.max(18, rr * 2.4), type: 'star', data: s });
  }

  function starHitTest(x, y) {
    let best = null, score = 1e9;
    for (const h of STAR_HIT) {
      const d = Math.hypot(h.x - x, h.y - y);
      if (d > h.r) continue;
      // 离光标越近越优先；重叠时取更靠前（z 更小）的那颗——与"近的盖住远的"一致
      const s = d + Math.max(0, h.z + 420) * 0.05;
      if (s < score) { score = s; best = h; }
    }
    return best;
  }
  function starPt(e) {
    const r = $('stars-canvas').getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  // 点人物 → 生成转发（用户：点击人物就生成转发）
  async function sharePerson(p) {
    if (TOKEN) return;
    try {
      const r = await api('/amnesia/share', { method: 'POST', body: { person_id: p.person_id } });
      const url = location.origin + BASE + r.link;
      const dark = (p.stars || []).find(s => !s.lit) || (p.stars || [])[0] || null;
      const clue = dark && (dark.seeds || []).length ? String(dark.seeds[0].text || '') : '';
      const me = (USER && USER.username) || '我';
      $('share-title').textContent = '把「' + p.name + '」这一族星发给他';
      $('share-text').textContent = clue
        ? p.name + '：' + clue + '…… 你还记得什么？—— ' + me
        : p.name + '，我把我记得的我们之间的事，都放进了一片星空里，等你来点亮。—— ' + me;
      $('share-link').textContent = url;
      $('share-link').dataset.url = url;
      $('dlg-share').classList.remove('hidden');
    } catch (e) { toast(e.message || '生成转发失败'); }
  }

  // ─── 补星弹窗：像聊天一样把你知道的讲出来 ─────────────────
  // 输入栏默认是长条语音（按住说话 · 松开提交 · 上滑取消），可切键盘打字、也可发照片。
  // 每轮提交都由对方本体回一句、继续引导；聊够了由 AI 自己决定点亮（可能不止一颗星）。
  // 未登录时先弹登录窗，登录成功再接着打开（不是只提示一句"未登录"）。
  let STAR_CUR = null;              // 当前弹窗里的星
  let STAR_BUF = [], STAR_CANCEL = false, STAR_VOICE = true;

  function starMsg(role, text) {
    const box = $('star-msgs');
    const d = document.createElement('div');
    d.className = 'msg ' + (role === 'ai' ? 'ai' : 'me');
    d.textContent = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
    return d;
  }

  function starTyping(on) {
    let t = $('star-typing');
    if (on) {
      if (!t) {
        t = document.createElement('div');
        t.id = 'star-typing';
        t.className = 'msg ai typing';
        t.textContent = '…';
        $('star-msgs').appendChild(t);
        $('star-msgs').scrollTop = $('star-msgs').scrollHeight;
      }
    } else if (t) t.remove();
  }

  function setStarVoice(on) {
    STAR_VOICE = on;
    $('star-mode').textContent = on ? '⌨️' : '🎤';
    $('star-voice').classList.toggle('hidden', !on);
    $('star-text').classList.toggle('hidden', on);
    $('star-send').classList.toggle('hidden', on);
  }

  async function openStarDialog(s) {
    if (!requireLogin(() => openStarDialog(s))) return;
    STAR_CUR = s;
    $('star-title').textContent = s.lit ? '这一颗星 · 已点亮' : '这一颗星 · 还暗着';
    $('star-sub').textContent = '「' + (s.person_name || 'TA') + '」只记得自己那一半——你记得的，讲给TA听';
    $('star-msgs').innerHTML = '';
    $('dlg-star').classList.remove('hidden');
    setStarVoice(true);
    try {
      const h = await api('/amnesia/star/history?star_id=' + s.star_id);
      (h.msgs || []).forEach(m => starMsg(m.role, m.content));
    } catch (e) { /* 历史拉不到不影响继续聊 */ }
    if (!$('star-msgs').children.length) {
      const seeds = s.seeds || [];
      if (seeds.length) starMsg('ai', '我记得的是：' + seeds.map(x => x.text).join('；'));
      starMsg('ai', '你还记得那时候的事吗？跟我讲讲。');
    }
  }

  async function starSubmit(payload) {
    if (!STAR_CUR) return;
    if (!payload.text && !payload.pcm_b64 && !payload.image_b64) return;
    starTyping(true);
    try {
      const r = await api('/amnesia/star/chat', {
        method: 'POST',
        body: Object.assign({ star_id: STAR_CUR.star_id, token: TOKEN }, payload)
      });
      starTyping(false);
      if (r.reply) starMsg('ai', r.reply);
      if ((r.lit || []).length) {
        toast('这段往事完整了 · 这颗星亮了' + (r.lit.length > 1 ? '（共 ' + r.lit.length + ' 颗）' : ''));
        STAR_CUR.lit = 1;
        $('star-title').textContent = '这一颗星 · 已点亮';
        await loadStars(true);
      }
    } catch (e) {
      starTyping(false);
      toast(e.message || '没听清，再说一次吧');
    }
  }

  async function starSendText() {
    const el = $('star-text');
    const t = el.value.trim();
    if (!t) return;
    el.value = '';
    starMsg('me', t);
    await starSubmit({ text: t });
  }

  // 按住说话：松开提交、上滑取消
  async function starVoiceDown(e) {
    e.preventDefault();
    if (STAR_REC) return;
    STAR_BUF = [];
    STAR_CANCEL = false;
    const m = new Voice.Mic();
    m.onStreamChunk = (b) => STAR_BUF.push(b);
    m.onVolume = () => {};
    m.onStateChange = () => {};
    STAR_REC = m;
    const v = $('star-voice');
    v.classList.add('rec');
    v.classList.remove('cancel');
    v.textContent = '松开 提交';
    try {
      await m.start();
    } catch (err) {
      STAR_REC = null;
      v.classList.remove('rec');
      v.textContent = '按住 说话';
      toast('麦克风不可用，请检查浏览器权限');
    }
  }

  async function starVoiceUp() {
    if (!STAR_REC) return;
    const m = STAR_REC;
    STAR_REC = null;
    try { m.stop(); } catch (e) {}
    const v = $('star-voice');
    v.classList.remove('rec', 'cancel');
    v.textContent = '按住 说话';
    const buf = STAR_BUF;
    STAR_BUF = [];
    if (STAR_CANCEL) { STAR_CANCEL = false; return; }
    const pcm = mergeBufs(buf);
    if (pcm.length < 3200) { toast('说得太短了，再说一次'); return; }
    starMsg('me', '（语音）');
    await starSubmit({ pcm_b64: bufB64(pcm) });
  }

  async function starPickImage(file) {
    if (!file) return;
    try {
      const b64 = await starReadImage(file);
      starMsg('me', '（照片）');
      await starSubmit({ image_b64: b64 });
    } catch (e) { toast(e.message || '这张图读不了'); }
  }

  // 读图并压缩（长边 1280、体积压到 700K 以内，与其他入口一致）
  function starReadImage(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => {
        const img = new Image();
        img.onload = () => {
          const max = 1280;
          let w = img.width, h = img.height;
          const k = Math.min(1, max / Math.max(w, h));
          w = Math.round(w * k); h = Math.round(h * k);
          const cv = document.createElement('canvas');
          cv.width = w; cv.height = h;
          cv.getContext('2d').drawImage(img, 0, 0, w, h);
          let q = 0.82, out = cv.toDataURL('image/jpeg', q);
          while (out.length > 700000 && q > 0.4) { q -= 0.12; out = cv.toDataURL('image/jpeg', q); }
          resolve(out.split(',')[1] || '');
        };
        img.onerror = () => reject(new Error('这张图读不了'));
        img.src = String(fr.result || '');
      };
      fr.onerror = () => reject(new Error('这张图读不了'));
      fr.readAsDataURL(file);
    });
  }

  function escapeText(s) {
    return String(s || '').replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function mergeBufs(list) {
    let n = 0;
    for (const b of list) n += b.byteLength;
    const out = new Uint8Array(n);
    let off = 0;
    for (const b of list) { out.set(new Uint8Array(b), off); off += b.byteLength; }
    return out;
  }

  function bufB64(u8) {
    let s = '';
    const CH = 0x8000;
    for (let i = 0; i < u8.length; i += CH) s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
    return btoa(s);
  }

  // 双击空白 → 加一颗星（主人自己想起的事，先留一颗待亲友来点的暗星）
  async function addStar() {
    if (TOKEN || !STAR_SCOPE) return;
    try {
      await api('/amnesia/star/add', { method: 'POST', body: { person_id: STAR_SCOPE } });
      toast('加了一颗星 · 点亮后就能请TA来补');
      await loadStars(true);        // 停在当前人物的星空里，不跳回人物星云
    } catch (e) { toast(e.message || '加星失败'); }
  }

  // ─── 末那识中枢实时视图 ──────────────────────────────
  // 习气网络 3D 视图 + 中枢实时激活值：轮询 /api/amnesia/manas 拿增量，
  // 交给 HabitGraph.setLive() 渲染（星球按激活值放大发光、阈值分层着色、主念集群加白环）。
  let manasTimer = 0, manasTraitsLoaded = false;

  function manasQs() { return TOKEN ? `token=${encodeURIComponent(TOKEN)}` : `owner_ai_id=${ownerAid()}`; }

  async function startManasLive() {
    if (!window.HabitGraph) return;
    // 习气网络 3D 网络先就位（末那识中枢就是在这张网络上跑动力学）
    document.body.classList.add('chat-bg-on');
    HabitGraph.setActive(true);
    HabitGraph.setQuiet(false);      // 查看视角：显化主念文字（看 TA 此刻在想什么）
    await HabitGraph.load(manasQs());
    HabitGraph.setInteractive(true);
    if (!manasTraitsLoaded) { manasTraitsLoaded = true; loadManasTraits(); }
    pollManas();
    if (!manasTimer) manasTimer = setInterval(pollManas, 500);
  }

  function stopManasLive() {
    if (manasTimer) { clearInterval(manasTimer); manasTimer = 0; }
    if (window.HabitGraph) HabitGraph.setInteractive(false);
    document.body.classList.remove('chat-bg-on');
    HabitGraph && HabitGraph.setActive(false);
  }

  async function pollManas() {
    if (GAL_VIEW !== 'manas' || !window.HabitGraph) return;
    try {
      const r = await api(`/amnesia/manas?${manasQs()}&top=80`);
      if (r.error) return;
      HabitGraph.setLive(r);
      renderManasHud(r);
    } catch (e) {}
  }

  function renderManasHud(r) {
    const layerName = { sub: '潜意识', conscious: '显意识·内思考', manifest: '外显行为' }[r.layer] || r.layer;
    $('manas-tick').textContent = r.tick != null ? r.tick : 0;
    $('manas-act').textContent = (r.dominant_activation || 0).toFixed(3);
    $('manas-dom-power').textContent = (r.dominance || 0).toFixed(2);
    $('manas-silence').textContent = r.silence_ticks || 0;
    const chip = $('manas-layer-chip');
    chip.textContent = layerName;
    chip.className = 'chip manas-layer ' + (r.layer || 'sub');
    const dom = r.dominant || [];
    $('manas-dominant').innerHTML = dom.length
      ? '<span class="manas-dom-tip">当下主念集群</span>' + dom.map(d =>
          `<span class="manas-dom-item">#${d.seed_id} <b>${(d.activation || 0).toFixed(3)}</b></span>`).join('')
      : '<span class="manas-dom-tip">此刻无主念（潜意识静默流转）</span>';
  }

  async function loadManasTraits() {
    try {
      const r = await api(`/amnesia/manas/traits?${manasQs()}`);
      if (r.error) { $('manas-traits').innerHTML = ''; return; }
      $('manas-traits').innerHTML =
        `<span class="manas-dom-tip">人格特质种子（AI 行为差异之源）· 敏感度增益 ${(r.persona_gain || 1).toFixed(3)}</span>`
        + (r.traits || []).map(t =>
            `<span class="manas-trait" title="${esc(t.desc || '')}">${esc(t.name)} <b>${(t.activation || 0).toFixed(3)}</b></span>`).join('');
    } catch (e) {}
  }

  async function loadLifeThreads() {
    const box = $('life-threads');
    box.innerHTML = '<p class="empty-tip">加载中…</p>';
    try {
      const q = TOKEN ? `token=${encodeURIComponent(TOKEN)}` : `owner_ai_id=${ownerAid()}`;
      const r = await api(`/amnesia/life-threads?${q}`);
      if (r.error) { box.innerHTML = `<p class="empty-tip">${esc(r.error)}</p>`; return; }
      LIFE = r;
      renderLifeThreads();
    } catch (e) { box.innerHTML = `<p class="empty-tip">${esc(e.message)}</p>`; }
  }

  function renderLifeThreads() {
    const box = $('life-threads');
    const r = LIFE || { stages: [], age: 0, covered: 0, total: 0 };
    // 亲友（guest）只读，不提供代发寻忆入口
    const canRecall = !TOKEN;
    box.classList.toggle('can-recall', canRecall);
    const head = `<p class="lt-head">按 ${r.age} 岁裁剪 · 已覆盖 ${r.covered}/${(r.stages || []).length} 段 · 共找回 ${r.total} 条记忆</p>`;
    box.innerHTML = head + (r.stages || []).map(s => `
      <div class="lt-card${s.started ? ' done' : ''}${s.focus ? ' focus' : ''}" data-stage="${esc(s.key)}">
        <div class="lt-top"><b>${esc(s.name)}</b><span class="lt-sub">${esc(s.subtitle)}</span>
          ${s.focus ? '<span class="lt-badge">本轮优先</span>' : ''}
          <span class="lt-n">${s.count > 0 ? `已找回 ${s.count} 条` : '未开始'}</span></div>
        <p class="lt-topics">${(s.topics || []).map(esc).join(' · ')}</p>
        ${s.last_seed ? `<p class="lt-last">${esc(s.last_seed)}${s.last_date ? '（' + esc(String(s.last_date).slice(0, 10)) + '）' : ''}</p>` : ''}
        ${canRecall ? '<p class="lt-go">点这里找位一起经历过的人来补全 →</p>' : ''}
      </div>`).join('');
    if (canRecall) {
      box.querySelectorAll('.lt-card').forEach(el => {
        el.onclick = () => openStageRecall(el.dataset.stage);
      });
    }
  }

  // 点某段人生经历 → 直接发起该段的寻忆邀请
  function openStageRecall(stageKey) {
    const s = ((LIFE && LIFE.stages) || []).find(x => x.key === stageKey);
    openRecallDialog({ stage: stageKey });
    if (s) {
      $('recall-hint').textContent =
        `「${s.name}」这段经历（${(s.topics || []).join(' · ')}）——找一位一起经历过的人来帮你补全，`
        + '填好下面几项，生成寻忆链接转发给TA。';
    }
  }

  // ─── 打开星系 ────────────────────────────────────────
  // 顶栏对话入口按钮：文案随所处视图切换（星系中=进入对话 / 对话中=回到星系）
  function setChatBtn(label) {
    const b = $('btn-chat');
    b.textContent = label;
    b.classList.remove('hidden');
  }

  async function showGalaxy(focusId) {
    hideHabitBg();
    document.body.classList.remove('chat-on');   // 离开对话：星系恢复常态亮度
    $('view-home').classList.add('hidden');
    $('view-accept').classList.add('hidden');
    $('view-chat').classList.add('hidden');
    $('view-galaxy').classList.remove('hidden');
    // 忆点星光视角不依赖星系数据（人物脉络+事件星另走 /amnesia/stars 或 /amnesia/starview）：
    // 未登录的亲友凭链接进来时 /amnesia/galaxy 必然 401，绝不能因此掐断整个视图的初始化。
    if (GAL_VIEW === 'stars') { applyGalView(); return; }
    try {
      const r = await api('/amnesia/galaxy' + (TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : ''));
      if (r.error) { toast(r.error); return; }
      GAL = {
        mode: r.mode, user: r.user, ai: r.ai, invite: r.invite,
        nodes: (r.nodes || []).map(n => ({ ...n, _born: performance.now() })),
        edges: r.edges || [], stats: r.stats || null,
        focus: null, sel: null,
      };
      resetOrbit();
      $('btn-galaxy-back').classList.add('hidden');
      layoutGalaxy();
      updateGalaxyHud();
      if (r.mode === 'self') {
        $('tb-sub').textContent = `${r.user.username || '你'}，这是你的忆点星光`;
        $('btn-invites').classList.remove('hidden');
        $('btn-recovered').classList.remove('hidden');
        // 本人：与系统AI如意对话（如意作访谈者，引导用户梳理自己本体的记忆脉络并触发转发寻忆）
        setChatBtn('💬 与如意对话');
        $('btn-ais').classList.remove('hidden');
      } else {
        $('tb-sub').textContent = `你正在帮「${(r.ai && r.ai.name) || 'TA'}」找回记忆`;
        $('btn-invites').classList.add('hidden');
        $('btn-recovered').classList.add('hidden');
        setChatBtn('💬 找回对话');
        $('btn-ais').classList.remove('hidden');
      }
      $('galaxy-tip').textContent = GAL.mode === 'self'
      ? '拖拽旋转 · 滚轮/双指缩放 · 点星球看记忆 · 点空白返回全景'
      : '拖拽旋转 · 滚轮/双指缩放 · 点星球可转发给亲友';
      // 本体记忆来源铁律：系统AI如意访谈 + 亲友邀请对话（本人不与自己的本体对话，只与如意对话）
      if (r.mode === 'self') loadSelfFace();
      applyGalView();   // 保持用户上次选的视角（习气网络 / 人生脉络）
      if (focusId) {
        const n = GAL._byId[focusId];
        if (n && n._p3) setTimeout(() => { focusOn(n, performance.now()); selectNode(n); }, 620);
      }
    } catch (e) { toast(e.message); }
  }

  function updateGalaxyHud() {
    const st = GAL.stats || {};
    const seeds = st.seeds != null ? st.seeds : GAL.nodes.filter(n => n.kind === 'seed').length;
    const concepts = st.concepts != null ? st.concepts : GAL.nodes.filter(n => n.kind === 'concept').length;
    let html = `<span class="chip">记忆种子 <b>${seeds}</b> 颗</span>`
      + `<span class="chip">概念聚合 <b>${concepts}</b> 团</span>`
      + `<span class="chip">突触关联 <b>${st.edges != null ? st.edges : (GAL.edges || []).length}</b> 条</span>`;
    if (GAL.mode === 'self' && GAL.user) html += `<span class="chip">${esc(GAL.user.username)}</span>`;
    $('galaxy-stats').innerHTML = html;
  }

  // ─── 星球详情 ────────────────────────────────────────
  //   星球=记忆种子本体（一条真实记忆）：展示内容/证据/来向/时间 + 修正/删除；
  //   聚合星团=概念节点：展示概念名 + 其下种子清单（AI 整理记忆时自然长出，不可手工增删）。
  let selNode = null;
  async function selectNode(n) {
    selNode = n;
    GAL.sel = n.id;
    const hue = n._hue || 265;
    $('node-title').textContent = n.name;
    const sub = [];
    if (n.kind === 'concept') {
      sub.push(`概念聚合 · ${n.seed_count || 0} 颗记忆种子`);
    } else {
      sub.push(`记忆种子 · ${n.branch || 'value'}`);
      if (n.weight != null) sub.push(`权重 ${Number(n.weight).toFixed(2)}`);
      if (n.recall_count) sub.push(`被想起 ${n.recall_count} 次`);
    }
    $('node-sub').textContent = sub.join(' · ');
    $('node-dot').style.background = `hsl(${hue},88%,66%)`;
    $('node-dot').style.color = `hsl(${hue},88%,66%)`;

    if (GAL.mode === 'self') {
      $('node-forward').classList.add('hidden');
      $('node-self').classList.remove('hidden');
      $('dlg-node').classList.remove('hidden');
      renderNodeMem(n);
    } else {
      $('node-self').classList.add('hidden');
      $('node-forward').classList.remove('hidden');
      $('fwd-message').value = '';
      $('fwd-to').value = '';
      $('dlg-node').classList.remove('hidden');
    }
  }

  // 本人视角：种子=单条记忆卡片；概念=其下种子清单（只读，聚合由 AI 整理产生）
  function renderNodeMem(n) {
    const box = $('node-mem-list');
    if (n.kind === 'concept') {
      const kids = GAL.nodes.filter(x => x.kind === 'seed' && x.concept_id === n.concept_id);
      box.innerHTML = `<p class="empty-tip">「${esc(n.name)}」是 AI 本体整理记忆时自然凝聚出的概念，`
        + `下含 ${kids.length} 颗记忆种子。点任一颗查看、修正或删除。</p>`;
      kids.forEach(s => {
        const div = document.createElement('div');
        div.className = 'mem-item';
        div.innerHTML = `<p class="mv">${esc(s.value || s.name)}</p>`
          + `<p class="mm"><span>${esc(s.branch || '')}</span><span>${esc((s.seed_date || '').slice(0, 10))}</span></p>`;
        div.onclick = () => { closeDialog('dlg-node'); focusOn(s, performance.now()); selectNode(s); };
        box.appendChild(div);
      });
      return;
    }
    renderMemList(box, [{
      id: n.seed_id, a_value: n.value, evidence: n.evidence, subject: n.subject,
      a_name: n.branch, first_time: n.seed_date,
    }]);
  }

  // 记忆卡片：完整元信息（内容/来向/时间/证据）+ 修正 / 删除（不提供任何节点挂载操作）
  function renderMemList(box, list) {
    box.innerHTML = '';
    if (!list.length) { box.innerHTML = '<p class="empty-tip">暂无内容</p>'; return; }
    // 本人/亲友的判据＝是否凭邀请 token 进入（guest 只读转发，本人可修正/删除），
    // 不能依赖 GAL——本人登录是直接进对话的，此时星系数据尚未加载
    const selfMode = !TOKEN;
    list.forEach(raw => {
      const m = { ...raw, id: raw.id ?? raw.b_id };
      const div = document.createElement('div');
      div.className = 'mem-item';
      div.dataset.bid = m.id;
      const who = m.subject && m.subject !== 'self' ? `关于「${esc(m.subject)}」` : '本人的记忆';
      const when = (m.first_time || '').slice(0, 10);
      const tag = m.a_name ? `<span class="who">${esc(m.a_name)}</span>` : '';
      const acts = [];
      if (selfMode) {
        acts.push(`<a class="mem-act edit" href="javascript:void(0)">修正</a>`);
        acts.push(`<a class="mem-act del" href="javascript:void(0)">删除</a>`);
      }
      div.innerHTML = `<p class="mv">${esc(m.a_value || '')}</p>`
        + `<p class="mm">${tag}<span>${who}</span><span>${esc(when)}</span>`
        + (acts.length ? `<span class="acts">${acts.join('')}</span>` : '') + `</p>`
        + (m.evidence ? `<p class="mm ev">原话：「${esc(String(m.evidence).slice(0, 80))}」</p>` : '');
      const q = (s) => div.querySelector(s);
      if (q('.mem-act.edit')) q('.mem-act.edit').onclick = () => startMemEdit(m, div);
      if (q('.mem-act.del')) q('.mem-act.del').onclick = () => askDeleteMem(m);
      box.appendChild(div);
    });
  }

  // 记忆内联编辑：事实修正入口（如生日 1991→1987）
  function startMemEdit(m, card) {
    card.innerHTML = `<p class="field-label">修正记忆内容（例：生日 1991 → 1987）</p>
      <textarea class="mem-edit-val" rows="2" maxlength="6000">${esc(m.a_value || '')}</textarea>
      <p class="field-label">关于谁（留空=本人）</p>
      <input class="mem-edit-subject" maxlength="64" value="${esc((m.subject && m.subject !== 'self') ? m.subject : '')}" placeholder="如：张艳">
      <div class="mem-edit-btns">
        <button class="ghost-btn mem-edit-ok">保存修改</button>
        <button class="ghost-btn mem-edit-cancel">取消</button>
      </div>`;
    card.querySelector('.mem-edit-ok').onclick = () => saveMemEdit(m.id, card);
    card.querySelector('.mem-edit-cancel').onclick = () => { if (selNode) renderNodeMem(selNode); };
  }
  async function saveMemEdit(bId, card) {
    const val = card.querySelector('.mem-edit-val').value.trim();
    const subj = card.querySelector('.mem-edit-subject').value.trim();
    if (!val) { toast('记忆内容不能为空'); return; }
    try {
      const r = await api('/amnesia/mem/update', {
        method: 'POST', body: { b_id: bId, a_value: val, subject: subj || 'self', owner_ai_id: ownerAid() },
      });
      if (r.error) { toast(r.error); return; }
      toast('已修正该记忆');
      if (selNode) {
        if (selNode.kind === 'seed') { selNode.value = val; selNode.name = val.slice(0, 40); }
        renderNodeMem(selNode);
      }
    } catch (e) { toast(e.message); }
  }

  // 删除记忆：自定义确认弹窗（禁用 confirm），讲清影响
  let confirmAction = null;
  // 星系已加载时同步摘掉这颗星球；本人从对话中直接删记忆时星系未加载，无需同步
  function dropGalaxySeed(seedId) {
    if (!GAL) return;
    const dead = String(seedId);
    GAL.nodes = GAL.nodes.filter(x => !(x.kind === 'seed' && String(x.seed_id) === dead));
    GAL.edges = (GAL.edges || []).filter(e => GAL._byId[e.src] && GAL._byId[e.dst]);
    layoutGalaxy();
    updateGalaxyHud();
  }
  function askDeleteMem(m) {
    $('confirm-title').textContent = '删除这条记忆？';
    $('confirm-desc').innerHTML = `「${esc(String(m.a_value || '').slice(0, 70))}」将从记忆库<b>彻底删除</b>，`
      + `它长出的突触关联也随之消失，<b>不可恢复</b>。`;
    confirmAction = async () => {
      try {
        const r = await api('/amnesia/mem/delete', { method: 'POST', body: { b_id: m.id, owner_ai_id: ownerAid() } });
        if (r.error) { toast(r.error); return; }
        closeDialog('dlg-confirm');
        dropGalaxySeed(m.id);
        closeDialog('dlg-node');
        toast('已彻底删除');
      } catch (e) { toast(e.message); }
    };
    $('dlg-confirm').classList.remove('hidden');
  }
  $('btn-confirm-ok').onclick = () => { const f = confirmAction; confirmAction = null; if (f) f(); };
  $('btn-confirm-cancel').onclick = () => { confirmAction = null; closeDialog('dlg-confirm'); };

  async function forwardNode() {
    if (!selNode) return;
    const message = $('fwd-message').value.trim();
    if (!message) { toast('写几句想说的话吧'); return; }
    try {
      const r = await api('/amnesia/node/forward', {
        method: 'POST', body: {
          token: TOKEN, seed_id: selNode.seed_id, message, to_name: $('fwd-to').value.trim(),
        },
      });
      if (r.error) { toast(r.error); return; }
      closeDialog('dlg-node');
      copyLink(r.link);
    } catch (e) { toast(e.message); }
  }

  // ─── 登录控制器（home / accept / 弹窗三处复用；本地账号：用户名 + 密码） ─────────────
  function bindLogin(userId, passId, btnCodeId, btnLoginId, onOk) {
    const btnCode = $(btnCodeId);
    if (btnCode) btnCode.style.display = 'none';   // 本地账号没有验证码
    $(btnLoginId).onclick = async () => {
      const username = $(userId).value.trim(), password = $(passId).value;
      if (username.length < 3) { toast('用户名至少 3 位'); return; }
      if (password.length < 6) { toast('密码至少 6 位'); return; }
      try {
        const r = await api('/auth/login', { method: 'POST', body: { username, password } });
        if (r && r.user_id) {
          localStorage.setItem('alaya_uid', String(r.user_id));
          localStorage.setItem('alaya_token', r.token || '');
          USER = { user_id: r.user_id, username: r.username || r.nickname || '用户',
                   nickname: r.nickname || '', call_me: r.call_me || r.nickname || '' };
          toast('登录成功');
          // 登录后必须先备好 AI 清单：对话要拿它解析当前对话者（owner_ai_id）
          await warmAis();
          onOk();
        } else toast((r && (r.detail || r.error)) || '登录失败');
      } catch (e) { toast(e.message); }
    };
  }

  // ─── 登录弹窗（未登录时的统一入口）──────────────────────
  // 需要身份才能做的事（补星提交 / 看自己的资料 / 点亮…）都从这里登录，
  // 而不是只弹一句"未登录"然后什么也不做。
  let loginCb = null;
  function requireLogin(cb) {
    if (USER) { if (cb) cb(); return true; }
    loginCb = cb || null;
    $('dlg-login').classList.remove('hidden');
    return false;
  }

  async function restoreUser() {
    if (!sessionToken()) return null;
    try {
      const r = await api('/me');
      const u = r && r.user;
      return (u && u.id) ? {
        user_id: u.id, username: u.nickname || u.phone || '用户',
        nickname: u.nickname || '', call_me: u.call_me || u.nickname || '',
      } : null;
    } catch (e) { return null; }
  }

  // ─── A视图：登录后进入星系；邀请管理走底部弹窗 ──────────
  function showHome() {
    if (!USER) { $('view-home').classList.remove('hidden'); return; }
    // 带 ?node= 锚点（转发/分享链接）：直达该星球；否则本人登录即与如意对话
    if (FOCUS_NODE) showGalaxy(FOCUS_NODE);
    else enterChat();
  }

  async function loadInvites() {
    try {
      const r = await api('/amnesia/invites');
      const list = $('invite-list');
      list.innerHTML = '';
      (r.list || []).forEach(inv => {
        const div = document.createElement('div');
        div.className = 'invite-item';
        div.innerHTML = `<div class="who"><b>${esc(inv.address_term || '好友')}</b>
          <p>${esc(inv.relation_type || '')} · ${inv.status === 'accepted' ? '已接受' : '待接受'}</p></div>
          <button class="copy-btn">复制链接</button>`;
        div.querySelector('.copy-btn').onclick = () => copyLink(inv.token ? `/amnesia/?token=${inv.token}` : '');
        list.appendChild(div);
      });
      if (!(r.list || []).length) list.innerHTML = '<p class="empty-tip">还没有邀请，创建一个吧</p>';
    } catch (e) { toast(e.message); }
  }

  function copyLink(path) {
    const link = `${location.origin}${path}`;
    const done = () => toast('链接已复制，快去分享给TA吧');
    if (navigator.clipboard) navigator.clipboard.writeText(link).then(done).catch(() => fallbackCopy(link, done));
    else fallbackCopy(link, done);
  }
  function fallbackCopy(text, done) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { toast('复制失败，请长按链接复制：' + text); }
    document.body.removeChild(ta);
  }

  function openInviteDialog() {
    const dlg = $('dlg-invite');
    dlg.classList.remove('hidden');
    const tags = ['家人', '亲戚', '朋友', '同学', '同事', '恋人', '自定义'];
    const row = $('rel-tags');
    row.innerHTML = '';
    relSelected = '';
    $('rel-custom').classList.add('hidden');
    tags.forEach(t => {
      const s = document.createElement('span');
      s.className = 'tag'; s.textContent = t;
      s.onclick = () => {
        row.querySelectorAll('.tag').forEach(x => x.classList.remove('on'));
        s.classList.add('on');
        if (t === '自定义') { $('rel-custom').classList.remove('hidden'); $('rel-custom').focus(); relSelected = ''; }
        else { $('rel-custom').classList.add('hidden'); relSelected = t; }
      };
      row.appendChild(s);
    });
    $('rel-custom').oninput = () => { relSelected = $('rel-custom').value.trim(); };
  }

  async function createInvite() {
    const addr = $('addr-term').value.trim();
    const self = $('self-term').value.trim();
    if (!relSelected) { toast('请点选你们的关系'); return; }
    if (!addr) { toast('请填写你怎么称呼TA'); return; }
    if (!self) { toast('请填写TA怎么称呼你'); return; }
    try {
      const r = await api('/amnesia/invite', { method: 'POST', body: {
        relation_type: relSelected, address_term: addr, self_term: self, owner_ai_id: ownerAid() } });
      closeDialog('dlg-invite');
      copyLink(`/amnesia/?token=${r.token}`);
      loadInvites();
    } catch (e) { toast(e.message); }
  }

  // ─── 寻忆邀请（系统AI如意提到某个人物 → 用户同意 → 底窗填表转发） ───
  //   默认回填AI已确认的称呼；关系点选（2-20个用文本标签，不落下拉框）
  let recallRel = '';
  let recallStage = '';   // 本次寻忆挂靠的人生阶段（人生脉络视角点某段进来时带上）

  function openRecallDialog(m) {
    m = m || {};
    const name = String(m.person || '').trim();
    const rel = String(m.relation || '').trim();
    recallStage = String(m.stage || '').trim();
    $('dlg-recall').classList.remove('hidden');
    $('recall-hint').textContent = name
      ? `如意建议：请「${name}」来帮你找回这段记忆。填好下面几项，生成寻忆链接转发给TA。`
      : (recallStage
        ? `「${lifeStageName(recallStage)}」这段经历，找一位一起经历过的人来帮你补全——填好下面几项，生成寻忆链接转发给TA。`
        : '填好下面几项，生成一条寻忆链接转发给TA——TA打开后就能陪你聊起那些共同的往事。');
    $('recall-addr').value = name;
    $('recall-self').value = (USER && (USER.call_me || USER.nickname)) || '';
    // 关系标签：AI已确认的关系优先点亮，其余供点选
    const tags = ['家人', '亲戚', '朋友', '同学', '同事', '恋人', '自定义'];
    const row = $('recall-rel-tags');
    row.innerHTML = '';
    recallRel = rel || '';
    $('recall-rel-custom').classList.add('hidden');
    let matched = false;
    tags.forEach(t => {
      const s = document.createElement('span');
      s.className = 'tag' + (t === rel ? ' on' : '');
      if (t === rel) matched = true;
      s.textContent = t;
      s.onclick = () => {
        row.querySelectorAll('.tag').forEach(x => x.classList.remove('on'));
        s.classList.add('on');
        if (t === '自定义') { $('recall-rel-custom').classList.remove('hidden'); $('recall-rel-custom').focus(); recallRel = ''; }
        else { $('recall-rel-custom').classList.add('hidden'); recallRel = t; }
      };
      row.appendChild(s);
    });
    if (rel && !matched) { recallRel = rel; $('recall-rel-custom').classList.remove('hidden'); $('recall-rel-custom').value = rel; }
    $('recall-rel-custom').oninput = () => { recallRel = $('recall-rel-custom').value.trim(); };
  }

  async function createRecallInvite() {
    const addr = $('recall-addr').value.trim();
    const self = $('recall-self').value.trim();
    if (!recallRel) { toast('请点选TA是你的什么人'); return; }
    if (!addr) { toast('请填写你怎么称呼TA'); return; }
    if (!self) { toast('请填写TA怎么称呼你'); return; }
    try {
      const r = await api('/amnesia/invite', { method: 'POST', body: {
        relation_type: recallRel, address_term: addr, self_term: self,
        owner_ai_id: ownerAid(), life_stage: recallStage || '' } });
      closeDialog('dlg-recall');
      copyLink(`/amnesia/?token=${r.token}`);
    } catch (e) { toast(e.message); }
  }

  // ─── B视图：接受邀请 ─────────────────────────────────
  async function showAccept() {
    $('view-galaxy').classList.add('hidden');
    $('view-accept').classList.remove('hidden');
    try {
      const r = await api(`/amnesia/invite?token=${encodeURIComponent(TOKEN)}`);
      if (r.error) { toast(r.error); return; }
      INVITE = r.invite;
      const ai = INVITE.inviter_ai || {};
      $('inv-ai-avatar').textContent = ai.avatar || '🧠';
      $('inv-ai-name').textContent = ai.name || '忆点星光';
      $('inv-desc').innerHTML =
        `我是 <b>${esc(ai.name || 'TA')}</b> 的AI本体。<br>` +
        `${esc(ai.name || 'TA')}说你是TA的 <b>${esc(INVITE.relation_type || '亲友')}</b>，` +
        `TA平时叫你「<b>${esc(INVITE.address_term || '好友')}</b>」。<br>` +
        `你们之间的事，TA只记得自己那一半——你记得的那一半，才让那段往事完整。<br>` +
        `愿意陪TA把它一颗颗点亮吗？`;
      enterAcceptFlow();
    } catch (e) { toast(e.message); }
  }

  function enterAcceptFlow() {
    const inv = INVITE;
    if (!inv) return;
    // 忆点星光：凭链接进来**直接看星图**——未登录也能先看（用户：点击链接进入可以直接查看；
    // 点星星弹窗要补充时才判断登录，登录后自动绑定当前事件，见 openStarDialog / star/chat）。
    // 所以这里不再用登录页把人挡在门外，也不再走"先接受邀请再对话"的老流程。
    showGalaxy(FOCUS_NODE);
    if (USER && Number(inv.inviter_user_id) !== Number(USER.user_id) && inv.status === 'pending') {
      // 已登录的受邀亲友：静默接受，之后也能用「找回对话」与本体的 guest 通道
      api('/amnesia/accept', { method: 'POST', body: { token: TOKEN } }).catch(() => {});
    }
  }

  async function acceptInvite() {
    try {
      const r = await api('/amnesia/accept', { method: 'POST', body: { token: TOKEN } });
      if (r.error) { toast(r.error); return; }
      toast('已接受，正在进入…');
      $('accept-box').classList.add('hidden');
      $('accept-ok').classList.remove('hidden');
      if (FOCUS_NODE) showGalaxy(FOCUS_NODE);
      else setTimeout(enterChat, 600);
    } catch (e) { toast(e.message); }
  }

  // ─── 找回对话 ────────────────────────────────────────
  function enterChat() {
    $('view-accept').classList.add('hidden');
    $('view-home').classList.add('hidden');
    $('view-galaxy').classList.add('hidden');
    $('view-chat').classList.remove('hidden');
    $('btn-recovered').classList.remove('hidden');
    $('btn-invites').classList.add('hidden');
    // 顶栏按钮转为「返回星系」：对话与星系随时互切
    setChatBtn('🌌 星系');
    // 本人（无邀请 token）：默认与系统AI如意对话（guide 模式）——如意是访谈者，
    // 记忆归属仍是用户自己的AI本体（后端按所选 ai_id 的 user_id 判定 guide/persona）
    if (!TOKEN && !CHAT_AI_ID) {
      const sysAi = AIS_CACHE.find(x => Number(x.is_system) === 1);
      if (sysAi) CHAT_AI_ID = Number(sysAi.id) || 0;
    }
    // 副标题指向「正在和你对话的这个人」：
    //   亲友找回 → 邀请方本体名（TA只记得一半，你帮TA补全）
    //   自己的本体 → 本体名（TA以第一人称和你一起回忆）
    //   系统AI如意 → 如意（访谈梳理）
    // 副标题指向「正在和你对话的这个人」——判据全部来自后端下发：
    //   系统AI如意（is_system）→ 如意访谈，帮你梳理「记忆归属本体」的经历
    //   我的AI本体（id==MY_AI_ID）→ 本体名，TA以第一人称和你一起回忆
    //   亲友的AI本体 → 该本体名，你帮TA找回记忆
    const ownerName = (INVITE && INVITE.inviter_ai && INVITE.inviter_ai.name)
      || (GAL && GAL.ai && GAL.ai.name)
      || (AIS_CACHE.find(x => Number(x.is_my_ai) === 1) || {}).name || 'TA';
    const curAi = AIS_CACHE.find(x => Number(x.id) === Number(CHAT_AI_ID || ownerAid())) || {};
    const curName = curAi.name || ownerName;
    const isSys = Number(curAi.is_system) === 1;
    const isMyAi = Number(curAi.is_my_ai) === 1 || Number(CHAT_AI_ID || ownerAid()) === Number(MY_AI_ID);
    $('tb-sub').textContent = isSys
      ? `如意帮你梳理「${ownerName}」的经历`
      : (isMyAi ? `和「${curName}」一起回忆` : `帮「${curName}」找回记忆`);
    document.body.classList.add('chat-on');   // 对话态：星系节点淡化，气泡半透明浮动于画面之上
    // 离开星系查看态：复位背景明暗与指针（否则 manas-view 会让背景保持全亮且可拖拽）
    document.body.classList.remove('manas-view', 'life-view');
    showHabitBg();
    connectWs();
  }

  function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}${BASE}/ws`);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => ws.send(JSON.stringify({
      type: 'init', user_id: sessionUid(), token: sessionToken(), invite_token: TOKEN,
      // 默认与自己的AI本体（系统AI如意访谈，guide 模式）；切换角色后为亲友的AI本体（persona）
      owner_ai_id: CHAT_AI_ID || ownerAid() || undefined,
    }));
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) { playPcm(new Int16Array(ev.data)); return; }
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      handleWs(m);
    };
    ws.onclose = () => {
      setStatus('连接断开');
      setTimeout(() => { if ($('view-chat').classList.contains('hidden')) return; connectWs(); }, 3000);
    };
    ws.onerror = () => {};
  }

  function handleWs(m) {
    switch (m.type) {
      case 'ready': {
        // 系统提示点明「正在和你说话的这个人是谁」，语气与身份严格对应：
        //   guide（如意访谈）→ 如意是助手，帮TA梳理「owner_name」的经历
        //   persona（本体出场）→ 本体本人格以第一人称和你一起回忆（本体的「我」）
        const who = m.owner_name || m.ai_name || 'TA';
        const talker = m.mode === 'guide' ? (m.ai_name || '如意') : who;
        addSys(m.mode === 'guide'
          ? `「${talker}」会帮你梳理「${who}」的经历，TA想起来的会记在星星里`
          : `这是「${talker}」与你的一段往事，TA想起来什么都会记在星星里`);
        setStatus('聆听中');
        if (!mic) startMic();
        break;
      }
      case 'stt':
        if (m.text) addUser(m.text);
        break;
      case 'reply_delta':
        if (!aiBubble) aiBubble = addBubble('ai', '');
        aiBubble.textContent += (m.text || '');
        scrollBottom();
        break;
      case 'reply_done':
        // 服务端下发的 final 文本是权威值（已剥离 [RECALL] 等系统标记）：
        // 流式过程中若曾漏出标记半截，这里一次性校正回来
        if (aiBubble && m.text) aiBubble.textContent = m.text;
        else if (aiBubble && !aiBubble.textContent && m.text) aiBubble.textContent = m.text;
        aiBubble = null;
        break;
      case 'memory':
        // 记忆写入：习气网络刷新（新填充的概念节点会亮起并发射突触脉冲）
        if (window.HabitGraph && m.written && m.written.length) refreshHabitBg();
        break;
      case 'ask_recall':
        // 系统AI如意提到某个人物，建议转发寻忆链接：弹底窗，默认回填已确认的称呼
        openRecallDialog(m);
        break;
      case 'turn_done':
        refreshRecovered();
        setMicState('idle');
        break;
      case 'manas_state':
        // 末那识中枢实时态：WS 增量推送（比 REST 轮询更即时）。
        // 无论当前在哪个视图都喂给 HabitGraph，切到中枢视角即刻看到最新激活值。
        if (window.HabitGraph) {
          HabitGraph.setLive(m);
          if (GAL_VIEW === 'manas') renderManasHud(m);
        }
        break;
      case 'error':
        toast(m.message || '出错了'); setStatus('出错了');
        setMicState('idle');
        break;
    }
  }

  // ─── 状态机（复刻 ai_me：idle/listening/thinking/speaking） ──
  let micState = 'idle';
  function setMicState(state) {
    micState = state;
    const tips = {
      idle: '点麦克风开始对话，也可以直接说话；左侧相册、右侧摄像头',
      listening: '聆听中…',
      thinking: '思考中…',
      speaking: '说话中，随时可以打断我',
    };
    const fbtn = $('btn-mic');
    if (fbtn) { fbtn.className = 'mic-main ' + state; $('chat-status').textContent = tips[state] || ''; }
  }

  // 麦克风按钮点击：按当前状态切换（idle=开启 / listening=停止并提交 / speaking=打断 / thinking=忽略）
  function onMicClick() {
    if (micState === 'thinking') { toast('正在识别思考中，请稍候…'); return; }
    if (micState === 'speaking') {
      // 打断AI播报，立即转聆听
      stopPlay();
      const S = activeWs();
      if (S) S.send(JSON.stringify({ type: 'interrupt' }));
      if (aiBubble && !aiBubble.textContent) aiBubble.remove();
      aiBubble = null;
      if (mic) mic.setEchoMode(false);
      setMicState('listening');
      return;
    }
    if (micState === 'listening') {
      // 用户主动结束本轮：立即提交已录内容
      if (mic) mic.stop();
      setMicState('thinking');
      return;
    }
    // idle：开启对话（麦克风全时聆听）
    if (!mic) { startMic(); return; }
    setMicState('listening');
  }
  // 当前应接收音频的 WS：亲友找回通道
  function activeWs() {
    return (ws && ws.readyState === 1) ? ws : null;
  }

  // ─── 麦克风（全时 VAD：说话即上送，静音800ms自动提交） ──
  async function startMic() {
    mic = new Voice.Mic();
    mic.onVolume = () => {};
    mic.onStateChange = (st) => {
      if (st === 'speech-start') {
        if (ttsPlaying) {
          // 用户插话打断AI播报
          stopPlay();
          const S = activeWs();
          if (S) S.send(JSON.stringify({ type: 'interrupt' }));
          if (aiBubble && !aiBubble.textContent) aiBubble.remove();
          aiBubble = null;
          if (mic) mic.setEchoMode(false);
        }
        setMicState('listening');
      }
    };
    mic.onStreamStart = () => {
      streaming = true;
      const S = activeWs();
      if (S) S.send(JSON.stringify({ type: 'start' }));
      setMicState('listening');
    };
    mic.onStreamChunk = (buf) => {
      const S = activeWs();
      if (streaming && S) S.send(buf);
    };
    mic.onStreamEnd = async () => {
      streaming = false;
      const S = activeWs();
      if (S) {
        // 摄像头模式：说完话先上传截帧（内存暂存 frame_id），随语音同一轮送出（AI结合画面回应）
        let frameId = '';
        if (camActive) {
          try {
            frameId = await uploadFrame();
          } catch (e) { toast(e.message || '画面上传失败'); }
        }
        S.send(JSON.stringify(frameId ? { type: 'stop', frame_id: frameId } : { type: 'stop' }));
      }
      setMicState('thinking');
    };
    try {
      await mic.start();
      setMicState('idle');
    } catch (e) {
      toast('麦克风不可用，请检查浏览器权限');
      setMicState('idle');
    }
  }

  // ─── 相册：照片 / 文件（pdf/docs/ppt/txt） ──────────────
  // 照片：canvas 压缩到 500k 以内再上传（走 /api/upload，VLM 出画面描述，随 chat 入对话轮）
  // 文档：原文件上传（走 /api/upload，服务端提取文本，随 chat 入对话轮）
  const PHOTO_CAP = 500 * 1024;   // 照片上限 500k

  function compressPhoto(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const maxSide = 1600;
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
        const c = document.createElement('canvas');
        c.width = Math.max(1, Math.round(img.width * scale));
        c.height = Math.max(1, Math.round(img.height * scale));
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        let q = 0.82, blob = null, guard = 0;
        do {
          const dataUrl = c.toDataURL('image/jpeg', q);
          const bin = atob(dataUrl.split(',')[1] || '');
          const arr = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
          blob = new Blob([arr], { type: 'image/jpeg' });
          q -= 0.12;
        } while (blob.size > PHOTO_CAP && q > 0.3 && guard++ < 10);
        resolve(new File([blob], (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' }));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('照片读取失败')); };
      img.src = url;
    });
  }

  async function uploadAsset(f) {
    const fd = new FormData();
    fd.append('file', f, f.name || 'file');
    const r = await fetch(`${BASE}/api/upload`, {
      method: 'POST', headers: { 'X-User-Id': sessionUid(), 'X-Token': sessionToken() }, body: fd,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || j.detail || '上传失败');
    return j;
  }

  async function sendFile(inputEl) {
    const f = inputEl.files && inputEl.files[0];
    inputEl.value = '';
    if (!f || !ws || ws.readyState !== 1) return;
    setMicState('thinking');
    try {
      if (f.type.startsWith('image/')) {
        const photo = await compressPhoto(f);
        if (photo.size > PHOTO_CAP) { toast('照片压缩后仍超过 500k，换一张试试'); setMicState('idle'); return; }
        const j = await uploadAsset(photo);
        addUser('📷 发送了一张照片');
        ws.send(JSON.stringify({ type: 'chat', scene: j.desc || '', text: '（发来一张照片）' }));
      } else {
        if (f.size > 15 * 1024 * 1024) { toast('文件太大，请小于 15MB'); setMicState('idle'); return; }
        const j = await uploadAsset(f);
        addUser(`📄 发送了文件「${j.name || f.name}」`);
        ws.send(JSON.stringify({ type: 'chat', text: `（发来文件「${j.name || f.name}」）\n${j.text || ''}` }));
      }
    } catch (e) { toast(e.message); setMicState('idle'); }
  }

  // ─── 粘贴文字：聊天记录 / 日记（最长20000字）→ 与语音同一轮 chat 消息送给本体 ──
  async function sendText() {
    const box = $('text-input');
    const text = box.value.trim();
    if (!text) { toast('先把内容粘贴进来吧'); return; }
    if (text.length > 20000) { toast('内容过长，请控制在 20000 字以内'); return; }
    if (!ws || ws.readyState !== 1) { toast('还没连上，稍等一下再发'); return; }
    setMicState('thinking');
    try {
      const brief = text.length > 40 ? text.slice(0, 40) + '…' : text;
      addUser(`📝 发来一段文字（${text.length} 字）：${brief}`);
      ws.send(JSON.stringify({ type: 'chat', text: `（我发来一段文字，是我们过去的记录，你读一读）\n${text}` }));
      closeDialog('dlg-text');
      box.value = '';
      $('text-count').textContent = '0 / 20000';
    } catch (e) { toast(e.message); setMicState('idle'); }
  }

  // ─── 摄像头：画面铺满页面背景；双击切换前后置；说完话自动截帧随语音送出 ──
  // 摄像头开关状态标记在底部栏右侧「📹」按钮上
  function camBtn() { return $('btn-cam'); }

  async function toggleCamera() {
    if (camActive) { stopCamera(); return; }
    try {
      camStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: camFacing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
      });
      $('cam-video').srcObject = camStream;
      $('cam-layer').classList.remove('hidden');
      document.body.classList.add('cam-on');
      camActive = true;
      camBtn().classList.add('on');
      setStatus('摄像头已开启 · 双击画面切换前后置 · 直接说话，我会看到你看到的');
    } catch (e) {
      toast('摄像头打不开：' + (e.message || '请检查权限'));
    }
  }

  function stopCamera() {
    if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; }
    camActive = false;
    $('cam-layer').classList.add('hidden');
    document.body.classList.remove('cam-on');
    camBtn().classList.remove('on');
  }

  async function switchCamera() {
    if (!camActive) return;
    camFacing = camFacing === 'user' ? 'environment' : 'user';
    stopCamera();
    await toggleCamera();
  }

  // 截取当前摄像头画面 → canvas 压缩到 500k 以内 → base64（与语音同一轮上送）
  function captureFrame() {
    const v = $('cam-video');
    if (!v || !v.videoWidth) return '';
    const c = document.createElement('canvas');
    const scale = Math.min(1, 720 / Math.max(v.videoWidth, v.videoHeight));
    c.width = Math.max(1, Math.round(v.videoWidth * scale));
    c.height = Math.max(1, Math.round(v.videoHeight * scale));
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
    let q = 0.7, url = c.toDataURL('image/jpeg', q);
    const cap = 500 * 1024 * 1.37;
    let guard = 0;
    while (url.length > cap && q > 0.3 && guard++ < 12) {
      q -= 0.12;
      url = c.toDataURL('image/jpeg', Math.max(0.3, q));
    }
    return String(url).split(',')[1] || '';
  }

  // 摄像头截帧上传：/api/frame 内存暂存 → frame_id（语音轮 stop 携带，消费即删）
  async function uploadFrame() {
    const b64 = captureFrame();
    if (!b64) return '';
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    const fd = new FormData();
    fd.append('file', new Blob([arr], { type: 'image/jpeg' }), 'frame.jpg');
    const r = await fetch(`${BASE}/api/frame`, {
      method: 'POST', headers: { 'X-User-Id': sessionUid(), 'X-Token': sessionToken() }, body: fd,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || j.detail || '画面上传失败');
    return j.frame_id || '';
  }

  // ─── 星系中心用户照片：形象照列表最新一张；无则保持🧠 ──
  async function loadSelfFace() {
    faceImg = null;
    try {
      const r = await api(`/faces?owner_ai_id=${ownerAid()}`);
      const latest = (r.faces || [])[0];
      const imgEl = $('user-face');
      if (latest) {
        const url = `${BASE}/api/faces/${latest.id}/file?${authQ()}`;
        const img = new Image();
        img.onload = () => { faceImg = img; };
        img.src = url;
        imgEl.src = url;
        imgEl.classList.remove('hidden');
        $('user-face-fallback').classList.add('hidden');
      } else {
        imgEl.classList.add('hidden');
        $('user-face-fallback').classList.remove('hidden');
      }
    } catch (e) { /* 未登录或暂无形象照：保持🧠 */ }
  }

  // ─── 气泡 / 状态 ────────────────────────────────────
  function addBubble(kind, text) {
    const list = $('chat-list');
    const div = document.createElement('div');
    div.className = `bubble ${kind}`;
    div.textContent = text;
    list.appendChild(div);
    scrollBottom();
    return div;
  }
  const addUser = (t) => addBubble('me', t);
  const addSys = (t) => addBubble('sys', t);
  function scrollBottom() {
    const list = $('chat-list');
    requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
  }
  function setStatus(t) { $('chat-status').textContent = t || ''; }

  // ─── 找回的记忆面板：直接读真实习气网络的全部记忆种子（本人带 owner_ai_id、guest 带 token） ──
  function renderRecovered(list) {
    recoveredCount = list.length;
    $('recovered-count').textContent = recoveredCount;
    renderMemList($('recovered-list'), list);
  }
  async function refreshRecovered() {
    try {
      const q = TOKEN ? `token=${encodeURIComponent(TOKEN)}` : `owner_ai_id=${ownerAid()}`;
      const r = await api(`/amnesia/galaxy?${q}`);
      if (r.error) { renderRecovered([]); return; }
      const list = (r.nodes || []).filter(n => n.kind === 'seed').map(n => ({
        id: n.seed_id, a_value: n.value, evidence: n.evidence, subject: n.subject,
        a_name: n.branch, first_time: n.seed_date,
      }));
      renderRecovered(list);
    } catch (e) { console.error('[找回的记忆] 加载失败', e); }
  }

  // ─── 弹窗通用 ───────────────────────────────────────
  function closeDialog(id) { $(id).classList.add('hidden'); }
  document.querySelectorAll('.sheet-close').forEach(btn => {
    btn.onclick = () => btn.closest('.mask').classList.add('hidden');
  });
  // 点遮罩收起（保留右上角关闭按钮）
  document.querySelectorAll('.mask').forEach(m => {
    m.addEventListener('click', (e) => { if (e.target === m) m.classList.add('hidden'); });
  });

  // ═══════════════════════════════════════════════════
  // 用户资料弹窗（资料 / 我的声音 / 我的形象）
  // ═══════════════════════════════════════════════════

  function askConfirm(title, descHtml, action) {
    $('confirm-title').textContent = title;
    $('confirm-desc').innerHTML = descHtml;
    confirmAction = action;
    $('dlg-confirm').classList.remove('hidden');
  }

  // ─── 切换对话角色：自己的AI本体（第一人称）/ 亲友的AI本体 / 系统AI如意（访谈） ───
  //   切换后 WS 以新 owner_ai_id 重连；该AI本体知道怎么称呼当前用户，围绕「帮TA恢复记忆」展开
  let CHAT_AI_ID = 0;   // 当前对话的AI本体（0=默认：自己的本体）
  let AIS_CACHE = [];   // /ais 清单缓存（enterChat 同步解析当前对话者名字/类型用）
  let MY_AI_ID = 0;     // 我的AI本体 id（后端按 xd_users.ai_me_id 下发，唯一权威判据）

  // 预载 /ais：enterChat 需同步解析「当前对话者」名字与类型（本体/如意/亲友）
  async function warmAis() {
    try {
      const r = await api('/ais');
      AIS_CACHE = (r && r.ais) || [];
      MY_AI_ID = Number((r && r.my_ai_id) || 0);
    } catch (e) {}
  }

  async function openAiSwitcher() {
    $('dlg-ais').classList.remove('hidden');
    const box = $('ais-list');
    box.innerHTML = '<p class="empty-tip">加载中…</p>';
    try {
      const r = await api('/ais');
      const mine = (r && r.ais) || [];
      AIS_CACHE = mine;
      MY_AI_ID = Number((r && r.my_ai_id) || 0);
      if (!mine.length) { box.innerHTML = '<p class="empty-tip">暂无可对话的AI本体</p>'; return; }
      box.innerHTML = '';
      mine.forEach(a => {
        // 本体判据由后端下发（is_my_ai = ai_me_id 命中），前端绝不用 user_id/is_user_voice 推断
        const isMine = Number(a.is_my_ai) === 1;
        const isSys = Number(a.is_system) === 1;
        const cur = Number(a.id) === Number(CHAT_AI_ID || ownerAid());
        const item = document.createElement('div');
        item.className = 'recover-item ai-pick' + (cur ? ' on' : '');
        // 标签即语气承诺：本体=第一人称「我」（本体本人）；系统AI=如意访谈（助手）；亲友本体=帮TA补全
        const label = isMine ? '我的AI本体 · TA以第一人称和你一起回忆'
          : (isSys ? '系统AI如意 · 访谈梳理你的经历' : '亲友的AI本体 · 帮TA找回记忆');
        item.innerHTML = `<b>${esc(a.avatar || '🧠')} ${esc(a.name)}</b>`
          + `<p>${label}${cur ? ' · 当前对话中' : ''}</p>`;
        item.onclick = () => switchChatAi(a, isMine, isSys);
        box.appendChild(item);
      });
    } catch (e) { box.innerHTML = `<p class="empty-tip">${esc(e.message)}</p>`; }
  }

  function switchChatAi(a, isMine, isSys) {
    closeDialog('dlg-ais');
    CHAT_AI_ID = Number(a.id) || 0;
    // 自己的AI本体：本体本人格以第一人称出场（本体的「我」）——可切回来，但记忆归属不变
    toast(isMine ? `已切回「${a.name}」，TA会以第一人称和你一起回忆`
      : (isSys ? '已切换到系统AI如意，TA会帮你梳理经历' : `已切换到「${a.name}」，TA会围绕帮你恢复记忆展开`));
    if (!$('view-chat').classList.contains('hidden')) { reconnectChat(); }
    else { enterChat(); }
  }

  function reconnectChat() {
    if (ws) { try { ws.onclose = null; ws.close(); } catch (e) {} ws = null; }
    $('chat-list').innerHTML = '';
    aiBubble = null;
    connectWs();
  }

  // ─── 我的资料弹窗：姓名 / 手机号 / 相关资料 + 我的AI本体 ───
  const GENDERS = ['男', '女', '保密'];
  let userGender = '';

  function renderGenderTags() {
    const box = $('user-gender-tags');
    box.innerHTML = '';
    GENDERS.forEach(g => {
      const el = document.createElement('span');
      el.className = 'tag' + (g === userGender ? ' on' : '');
      el.textContent = g;
      el.onclick = () => { userGender = g; renderGenderTags(); };
      box.appendChild(el);
    });
  }

  async function openUserDialog() {
    $('dlg-user').classList.remove('hidden');
    switchUserTab('info');
    try {
      const r = await api('/me');
      const u = (r && r.user) || {};
      USER = { user_id: u.id, username: u.username || u.nickname || '用户',
               nickname: u.nickname || '', call_me: u.call_me || u.nickname || '' };
      $('user-name').textContent = u.nickname || '未设置昵称';
      $('user-phone').textContent = u.username || '未设置账号';
      $('user-name-edit').value = u.nickname || '';
      $('user-phone-edit').value = u.username || '';
      $('user-city-edit').value = u.city || '';
      $('user-profession-edit').value = u.profession || '';
      $('user-interests-edit').value = u.interests || '';
      userGender = u.gender || '';
      renderGenderTags();
      $('user-ai-line').textContent = u.ai_me_id ? `本体 #${u.ai_me_id} · 已就绪` : '正在创建…';
    } catch (e) { toast(e.message); }
    loadVoices();
    loadFaces();
  }

  function switchUserTab(tab) {
    document.querySelectorAll('[data-utab]').forEach(el =>
      el.classList.toggle('on', el.dataset.utab === tab));
    $('utab-info').classList.toggle('hidden', tab !== 'info');
    $('utab-voices').classList.toggle('hidden', tab !== 'voices');
    $('utab-faces').classList.toggle('hidden', tab !== 'faces');
  }

  async function saveUserProfile() {
    const name = $('user-name-edit').value.trim();
    if (!name) { toast('姓名不能为空'); return; }
    try {
      await api('/user/profile', { method: 'POST', body: {
        nickname: name,
        gender: userGender,
        city: $('user-city-edit').value.trim(),
        profession: $('user-profession-edit').value.trim(),
        interests: $('user-interests-edit').value.trim(),
      } });
      const r = await api('/me');
      const u = (r && r.user) || {};
      $('user-name').textContent = u.nickname || '未设置昵称';
      $('user-phone').textContent = u.username || '未设置账号';
      toast('已保存');
    } catch (e) { toast(e.message); }
  }

  // ─── 音色：控制台配置的外部 TTS 音色清单（可切换本体说话音色） ───
  async function loadVoices() {
    const box = $('voice-list');
    box.innerHTML = '<p class="empty-tip">加载中…</p>';
    try {
      const r = await api(`/voices?owner_ai_id=${ownerAid()}`);
      const list = r.voices || [];
      if (!list.length) { box.innerHTML = '<p class="empty-tip">还没有配置音色：资料页 → 模型设置 → TTS 音色清单</p>'; return; }
      box.innerHTML = list.map(v => `
        <div class="voice-item" data-voice="${esc(v.id)}">
          <span class="v-emoji">${esc(v.emoji || '🔊')}</span>
          <div class="v-info">
            <div class="v-name">${esc(v.name || v.id)}${v.in_use ? ' <i class="v-inuse">使用中</i>' : ''}</div>
            <div class="v-tag">${esc(v.id)}</div>
          </div>
          <div class="v-acts">
            ${v.in_use ? '' : `<button class="ghost-btn" data-act="use" data-voice-id="${esc(v.id)}">设为音色</button>`}
          </div>
        </div>`).join('');
      box.querySelectorAll('[data-act="use"]').forEach(b => {
        b.onclick = async () => {
          try {
            await api(`/voices/select?owner_ai_id=${ownerAid()}`, {
              method: 'POST', body: { voice_id: b.dataset.voiceId } });
            toast('已切换音色');
            loadVoices();
          } catch (e) { toast(e.message); }
        };
      });
    } catch (e) { box.innerHTML = `<p class="empty-tip">${esc(e.message)}</p>`; }
  }

  // ─── 我的形象：上传→压缩→VLM第一人称自述→本体记忆（本体由此知道自己长什么样） ───
  async function loadFaces() {
    const box = $('face-list');
    box.innerHTML = '<p class="empty-tip">加载中…</p>';
    try {
      const r = await api(`/faces?owner_ai_id=${ownerAid()}`);
      const list = r.faces || [];
      if (!list.length) { box.innerHTML = '<p class="empty-tip">还没有形象照，上传后AI会"看到"并记住自己</p>'; return; }
      box.innerHTML = list.map(f => `
        <div class="face-item" data-face="${f.id}">
          <img src="${BASE}/api/faces/${f.id}/file?${authQ()}" alt="形象照">
          <div class="face-desc">${esc(f.descr || '')}</div>
          <button class="face-del" data-face-id="${f.id}">✕</button>
        </div>`).join('');
      box.querySelectorAll('.face-del').forEach(b => {
        b.onclick = () => {
          const faceId = Number(b.dataset.faceId);
          askConfirm('删除形象照', '删除后AI记忆中的这条自我形象描述会保留（历史事实），但照片不再展示。', async () => {
            try {
              await api(`/faces/${faceId}`, { method: 'DELETE' });
              toast('已删除');
              loadFaces();
              loadSelfFace();
            } catch (e) { toast(e.message); }
          });
        };
      });
    } catch (e) { box.innerHTML = `<p class="empty-tip">${esc(e.message)}</p>`; }
  }

  async function uploadFace(inputEl) {
    const f = inputEl.files && inputEl.files[0];
    inputEl.value = '';
    if (!f) return;
    toast('正在识别并描述照片…');
    try {
      const fd = new FormData();
      fd.append('file', f, f.name || 'face.jpg');
      const r = await fetch(`${BASE}/api/faces?owner_ai_id=${ownerAid()}`, {
        method: 'POST', headers: { 'X-User-Id': sessionUid(), 'X-Token': sessionToken() }, body: fd,
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { toast(j.error || j.detail || '上传失败'); return; }
      toast('已上传，AI记住了你的样子');
      loadFaces();
      loadSelfFace();
    } catch (e) { toast(e.message); }
  }

  // ─── 事件绑定 ───────────────────────────────────────
  $('btn-new-invite').onclick = openInviteDialog;
  $('btn-create-invite').onclick = createInvite;
  $('btn-accept').onclick = acceptInvite;
  $('btn-goto-galaxy').onclick = () => showGalaxy(FOCUS_NODE);
  // 底部栏：左「📷 相册」/ 中「🎤 录音」/ 右「📹 摄像头」
  // 左侧「📷」：相册——底部弹窗二选一（照片压缩到500k内 / PDF·Word·PPT·文本）
  $('btn-album').onclick = () => $('dlg-album').classList.remove('hidden');
  $('pick-photo').onclick = () => { closeDialog('dlg-album'); $('file-photo').click(); };
  $('pick-doc').onclick = () => { closeDialog('dlg-album'); $('file-doc').click(); };
  $('pick-text').onclick = () => {
    closeDialog('dlg-album');
    $('text-input').value = '';
    $('text-count').textContent = '0 / 20000';
    $('dlg-text').classList.remove('hidden');
    $('text-input').focus();
  };
  $('text-input').addEventListener('input', () => {
    $('text-count').textContent = `${$('text-input').value.length} / 20000`;
  });
  $('btn-text-send').onclick = sendText;
  $('file-photo').addEventListener('change', () => sendFile($('file-photo')));
  $('file-doc').addEventListener('change', () => sendFile($('file-doc')));
  // 中间「🎤」：状态机点击（idle=开启 / listening=停止并提交 / speaking=打断 / thinking=忽略）
  $('btn-mic').onclick = onMicClick;
  // 右侧「📹」：开启/关闭摄像头（双击画面切换前后置）
  $('btn-cam').onclick = toggleCamera;
  // 寻忆邀请（系统AI提到人物后弹窗填表转发）
  $('btn-recall-create').onclick = createRecallInvite;
  $('btn-invites').onclick = () => {
    $('dlg-invites').classList.remove('hidden');
    loadInvites();
  };
  // 顶栏对话入口：星系 ↔ 对话 互切
  $('btn-chat').onclick = () => {
    if ($('view-chat').classList.contains('hidden')) enterChat();
    else showGalaxy();
  };
  // 视角切换：忆点星光 ↔ 习气网络 ↔ 人生脉络 ↔ 末那识中枢（同源，都是真实的记忆种子）
  document.querySelectorAll('[data-gview]').forEach(el => {
    el.onclick = () => setGalView(el.dataset.gview);
  });
  $('btn-galaxy-back').onclick = () => {
    if (GAL_VIEW === 'stars') { STAR_SCOPE = 0; updateStarsHud(); return; }   // 从某个人的星空退回人物星云
    unfocus(performance.now());
  };

  // ─── 忆点星光交互：拖拽旋转/缩放 · 点人物→转发 · 点星→补充 · 双击空白→加星 ───
  (function bindStars() {
    const c = $('stars-canvas');
    if (!c) return;
    // 拖拽旋转（水平拖绕 Y、垂直拖绕 X）与缩放（滚轮 / 双指），手感对齐习气网络与末那识中枢
    let drag = null, suppressClick = false, pinch = 0;
    c.addEventListener('pointerdown', (e) => {
      if (GAL_VIEW !== 'stars') return;
      drag = { x: e.clientX, y: e.clientY, moved: 0 };
    });
    window.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      STAR_ROT.y += dx * 0.0085;
      STAR_ROT.x = Math.max(-1.4, Math.min(1.4, STAR_ROT.x + dy * 0.0085));
      drag.x = e.clientX; drag.y = e.clientY;
    });
    const endDrag = () => {
      if (drag && drag.moved > 8) {          // 拖过就别当成点击
        suppressClick = true;
        setTimeout(() => { suppressClick = false; }, 60);
      }
      drag = null;
    };
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
    c.addEventListener('wheel', (e) => {
      if (GAL_VIEW !== 'stars') return;
      e.preventDefault();
      STAR_ZOOM = Math.max(0.45, Math.min(2.6, STAR_ZOOM * (e.deltaY > 0 ? 0.92 : 1.08)));
    }, { passive: false });
    c.addEventListener('touchmove', (e) => {       // 手机上没滚轮：双指缩放
      if (e.touches.length !== 2 || GAL_VIEW !== 'stars') return;
      e.preventDefault();
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                           e.touches[0].clientY - e.touches[1].clientY);
      if (pinch) STAR_ZOOM = Math.max(0.45, Math.min(2.6, STAR_ZOOM * (d / pinch)));
      pinch = d;
    }, { passive: false });
    c.addEventListener('touchend', () => { pinch = 0; });

    c.addEventListener('click', async (e) => {
      if (onUIEl(e) || GAL_VIEW !== 'stars' || suppressClick) return;
      const pt = starPt(e);
      const hit = starHitTest(pt.x, pt.y);
      if (!hit) return;
      if (hit.type === 'person') {
        STAR_SCOPE = hit.data.person_id;      // 进入TA的星空
        updateStarsHud();
        await sharePerson(hit.data);          // 同时生成转发（点击人物就生成转发）
      } else if (hit.type === 'star') {
        openStarDialog(hit.data);
      }
    });
    c.addEventListener('dblclick', (e) => {
      if (onUIEl(e) || GAL_VIEW !== 'stars' || !STAR_SCOPE || suppressClick) return;
      const pt = starPt(e);
      if (starHitTest(pt.x, pt.y)) return;    // 双击落在星/人上，不算空白
      addStar();
    });
    const organize = $('btn-stars-organize');
    if (organize) organize.onclick = async () => {
      organize.disabled = true;
      const old = organize.textContent;
      organize.textContent = '如意正在梳理…';
      try {
        const r = await api('/amnesia/persons/organize', { method: 'POST', body: {} });
        toast(r.updated ? ('梳理完成：' + r.updated + ' 位人物已归类') : '人物都已归类');
        await loadStars();
      } catch (e) { toast(e.message || '梳理失败'); }
      organize.textContent = old;
      organize.disabled = false;
    };
    const copy = $('btn-share-copy');
    if (copy) copy.onclick = async () => {
      const url = $('share-link').dataset.url || '';
      const text = $('share-text').textContent || '';
      try {
        if (navigator.share) { await navigator.share({ title: '忆点星光', text, url }); return; }
        await navigator.clipboard.writeText(text + '\n' + url);
        toast('已复制，去发给TA吧');
      } catch (e) {
        try { await navigator.clipboard.writeText(url); toast('链接已复制'); }
        catch (e2) { toast('复制失败，长按链接手动复制'); }
      }
    };
    // 补星弹窗的输入栏：默认长条语音（按住说话 · 松开提交 · 上滑取消），可切键盘、可发照片
    const vBtn = $('star-voice');
    if (vBtn) {
      vBtn.addEventListener('pointerdown', starVoiceDown);
      window.addEventListener('pointerup', starVoiceUp);
      window.addEventListener('pointercancel', starVoiceUp);
      vBtn.addEventListener('pointermove', (e) => {
        if (!STAR_REC) return;
        const r = vBtn.getBoundingClientRect();
        STAR_CANCEL = (r.top - e.clientY) > 50;         // 手指/指针上滑超过 50px 即取消
        vBtn.textContent = STAR_CANCEL ? '松开 取消' : '松开 提交';
        vBtn.classList.toggle('cancel', STAR_CANCEL);
      });
      vBtn.addEventListener('contextmenu', (e) => e.preventDefault());
    }
    const modeBtn = $('star-mode');
    if (modeBtn) modeBtn.onclick = () => { setStarVoice(!STAR_VOICE); if (!STAR_VOICE) $('star-text').focus(); };
    const sendBtn = $('star-send');
    if (sendBtn) sendBtn.onclick = starSendText;
    const sText = $('star-text');
    if (sText) sText.addEventListener('keydown', (e) => { if (e.key === 'Enter') starSendText(); });
    const plusBtn = $('star-plus');
    if (plusBtn) plusBtn.onclick = () => $('star-file').click();
    const sFile = $('star-file');
    if (sFile) sFile.addEventListener('change', () => { starPickImage(sFile.files[0]); sFile.value = ''; });
  })();
  $('btn-forward').onclick = forwardNode;
  $('btn-recovered').onclick = async () => {
    $('dlg-recovered').classList.remove('hidden');
    $('recovered-list').innerHTML = '<p class="empty-tip">加载中…</p>';
    await refreshRecovered();
  };

  // ─── 摄像头（亲友找回对话中拍照发给本体） + 用户资料弹窗 ──
  // 双击画面（空白处）切换前后置摄像头（document 级监听，穿透上层视图容器）
  document.addEventListener('dblclick', (e) => {
    if (!camActive || onUIEl(e)) return;
    switchCamera();
  });
  // 用户图标：未登录先把登录弹窗摆出来，登录成功后再打开资料窗（不是直接弹资料窗）
  $('btn-user').onclick = () => requireLogin(openUserDialog);
  $('btn-ais').onclick = openAiSwitcher;
  $('btn-user-save').onclick = saveUserProfile;
  document.querySelectorAll('[data-utab]').forEach(el => {
    el.onclick = () => switchUserTab(el.dataset.utab);
  });
  $('btn-face-upload').onclick = () => $('file-face').click();
  $('file-face').addEventListener('change', () => uploadFace($('file-face')));

  // ─── 启动 ───────────────────────────────────────────
  bindLogin('login-phone', 'login-code', 'btn-code', 'btn-login', () => showHome());
  bindLogin('login-phone2', 'login-code2', 'btn-code2', 'btn-login2', () => enterAcceptFlow());
  // 登录弹窗：登录成功后关窗，并把之前被拦下的那件事接着做完
  bindLogin('login-phone3', 'login-code3', 'btn-code3', 'btn-login3', () => {
    $('dlg-login').classList.add('hidden');
    const cb = loginCb;
    loginCb = null;
    if (cb) cb();
  });

  // ─── 习气网络背景（对话窗口：真实记忆网络 + 突触脉冲） ──
  if (window.HabitGraph) {
    HabitGraph.mount($('habit-canvas'));
    // 复用 api() 的鉴权头
    window.__authHeaders = () => ({ 'X-User-Id': sessionUid(), 'X-Token': sessionToken() });
    // 末那识中枢视角点击星球 → 打开记忆详情（与星系图同一套详情面板）
    HabitGraph.onSelect((hit) => {
      if (GAL_VIEW !== 'manas' || !GAL) return;
      const n = GAL._byId['s' + hit.seed_id];
      if (n) selectNode(n);
    });
  }
  // 进入对话视图：淡入习气网络背景 —— 只作氛围（安静模式：不显文字标签），
  // 对话气泡半透明浮动于网络之上，成为画面唯一焦点
  async function showHabitBg() {
    if (!window.HabitGraph) return;
    document.body.classList.add('chat-bg-on');
    HabitGraph.setActive(true);
    HabitGraph.setQuiet(true);
    const qs = TOKEN ? `token=${encodeURIComponent(TOKEN)}` : `owner_ai_id=${ownerAid()}`;
    await HabitGraph.load(qs);
  }
  // 记忆写入后刷新：新填充的概念节点亮起 + 沿关联边发射突触脉冲
  async function refreshHabitBg() {
    if (!window.HabitGraph) return;
    const before = new Set(HabitGraph.filledIds());
    const qs = TOKEN ? `token=${encodeURIComponent(TOKEN)}` : `owner_ai_id=${ownerAid()}`;
    const j = await HabitGraph.load(qs);
    if (j && !j.error) {
      const fresh = HabitGraph.filledIds().filter(id => !before.has(id));
      HabitGraph.pulse(fresh);
    }
  }
  function hideHabitBg() {
    if (!window.HabitGraph) return;
    document.body.classList.remove('chat-bg-on');
    HabitGraph.setActive(false);
  }

  (async function init() {
    USER = await restoreUser();
    if (USER) await warmAis();
    if (TOKEN) showAccept();
    else showHome();
  })();
})();
