// habit-graph.js — 习气网络（对话窗口背景 + 可交互 3D 视图）
// 数据源：/api/amnesia/habit-graph（阿赖耶引擎真实习气网络：记忆种子 + 种子共现/因果/修正边）
// 视觉：真实 3D —— 3D 力导向世界坐标 + 透视投影 + 轨道球相机 + 景深排序，
//       与星系图（amnesia.js）同一套空间语言；星球呼吸、突触弧线发光、信号脉冲沿边流动。
// 交互：拖拽旋转 / 滚轮·双指缩放 / 点击星球看记忆详情（记忆种子本身不拖动改位——
//       位置由习气关联强度算定，拖动改位会破坏「网络即人格」的语义）。
// 实时：末那识中枢每 200ms 迭代一次，激活值经 WS/REST 下发给 setLive()，
//       星球按激活值放大发光、按阈值分层着色（潜意识/显意识/外显）。
// 用法：HabitGraph.mount(canvas) / load(qs) / setActive(on) / setInteractive(on) /
//       setLive(payload) / pulse(ids) / focusSeed(seedId) / stats() / filledIds()
(function () {
  'use strict';

  // 种子类型配色（对齐阿赖耶记忆种子模型：event/value/style/thought/person，
  // 以及 AI 的自主认知 opinion/insight/imagination/curiosity，非 A-L 预设分支）
  const SEED_COLORS = {
    event: '#60a5fa', value: '#fbbf24', style: '#34d399',
    thought: '#c084fc', person: '#f472b6',
    opinion: '#fb923c', insight: '#22d3ee', imagination: '#e879f9', curiosity: '#a3e635',
    trait: '#94a3b8',
  };
  const ROOT_COLOR = '#c9d2ff';
  const MAX_PULSES = 90;
  const FOV = 620;                       // 焦距：屏幕半径 = 世界半径 × FOV / 景深
  const MIN_DIST = 240, MAX_DIST = 3200;
  // 分层配色（与后端 manas.py 相对统治力分层一致）：潜意识冷灰 / 显意识暖黄 / 外显炽红
  const LAYER_COLORS = { sub: '#7c8bb8', conscious: '#fbbf24', manifest: '#f87171' };

  function seedColor(t) {
    const k = String(t || '');
    if (SEED_COLORS[k]) return SEED_COLORS[k];
    let h = 0;
    for (let i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0;
    return `hsl(${h % 360},68%,64%)`;
  }

  let cv = null, ctx = null, W = 0, H = 0, DPR = 1;
  // 部署根路径：与 amnesia.js 同源推导（可带 URL 前缀；直连端口为空）
  const BASE = location.pathname.replace(/\/amnesia\/.*$/, '').replace(/\/$/, '');
  let nodes = [], byId = new Map(), edges = [], lastVis = [];
  let pulses = [];
  let active = false;          // 是否在对话视图（隐藏时停画）
  let interactive = false;     // 是否接管指针（习气网络视图）
  let raf = 0, time = 0, lastTs = 0;
  let autoPulseAt = 0;         // 下次自发脉冲时间
  let onSelect = null;         // 点击星球回调（宿主弹记忆详情）
  // 末那识中枢实时态（setLive 写入）：激活值 + 相对统治力分层 + 主念集群
  let live = { tick: 0, layer: 'sub', dominant: [], dominantIds: new Set(),
               silence_ticks: 0, dominant_activation: 0, dominance: 0, at: 0 };
  // 意识海观察维度：同一片海，换一个维度就换一种排布（semantic/time/emotion/person/scene）
  let view = 'semantic';
  // 安静模式：作为对话页背景时开启——只显圈点与光效，不显任何文字标签。
  // 背景的职责是"氛围"，文字会让它和聊天气泡抢注意力（看清"TA 此刻在想什么"请去中枢视角）。
  let quiet = false;

  // 轨道球相机：相机位于以视点(fx,fy,fz)为心、dist 为半径的球面上；(yaw,pitch)=方位/俯仰
  const orbit = { yaw: .42, tyaw: .42, pitch: .2, tpitch: .2, dist: 1200, tdist: 1200,
                  fx: 0, fy: 0, fz: 0, tfx: 0, tfy: 0, tfz: 0 };

  function hexA(hex, a) {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function hash(str) { let h = 0; for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0; return h; }
  function hash01(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 10000) / 10000;
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function randUnit(seed) {                     // 确定性随机单位向量（布局初值）
    const a = hash01(seed + 'a') * Math.PI * 2;
    const z = hash01(seed + 'z') * 2 - 1;
    const r = Math.sqrt(Math.max(0, 1 - z * z));
    return { x: r * Math.cos(a), y: z, z: r * Math.sin(a) };
  }

  function resize() {
    if (!cv) return;
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    // 全屏固定层：以 CSS 盒尺寸为准，兜底视口尺寸（canvas 是替换元素，无显式 width/height 时不会撑满）
    W = cv.clientWidth || innerWidth;
    H = cv.clientHeight || innerHeight;
    cv.width = W * DPR; cv.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  // 视口自适应距离：窄栏（控制台三栏中栏）下限 480，宽屏（全屏星系）上限 1500
  function baseDist() { return Math.round(clamp(Math.min(W, H) * 1.05, 480, 1500)); }
  function setDist(d) { orbit.tdist = clamp(d, MIN_DIST, MAX_DIST); }
  function resetOrbit() {
    orbit.yaw = orbit.tyaw = .42;
    orbit.pitch = orbit.tpitch = .2;
    orbit.dist = orbit.tdist = baseDist();
    orbit.fx = orbit.tfx = 0; orbit.fy = orbit.tfy = 0; orbit.fz = orbit.tfz = 0;
  }

  // ─── 意识海坐标（后端按观察维度算定）：种子在海中的位置来自它的多维指纹，
  //     前端不再做力导向——「位置即记忆本身」，不是临时摆出来的。
  //     语义散布始终是基底，切换维度只是换一层更大的结构（分层/分簇）。
  function layout() {
    byId = new Map();
    nodes.forEach(n => byId.set(n.id, n));
    edges = edges.filter(e => byId.has(e.src) && byId.has(e.dst));
    const R = 300 + Math.sqrt(Math.max(nodes.length, 1)) * 62;
    nodes.forEach(n => {
      const p = n.pos;
      if (p && p.length === 3) {
        n._p3 = { x: p[0], y: p[1], z: p[2] };
      } else {                                   // 后端未给坐标（异常）：散在球内，不塌成一点
        const u = randUnit('p0' + n.id);
        n._p3 = { x: u.x * R * .5, y: u.y * R * .5, z: u.z * R * .5 };
      }
      n._color = seedColor(n.branch);
      n._phase = hash('p' + n.id) % 628 / 100;
      n._glow = 0; n._act = 0;
    });
    lastVis = [];
  }

  // ─── 渲染 ───
  function frame(ts) {
    raf = requestAnimationFrame(frame);
    if (!active || !ctx) return;
    const dt = lastTs ? Math.min(0.05, (ts - lastTs) / 1000) : 0.016;
    lastTs = ts;
    time += dt;

    if (cv.clientWidth !== W || cv.clientHeight !== H) { resize(); if (nodes.length) layout(); }

    ctx.clearRect(0, 0, W, H);

    // 相机缓动（视角/距离/视点）
    orbit.yaw += (orbit.tyaw - orbit.yaw) * .12;
    orbit.pitch += (orbit.tpitch - orbit.pitch) * .12;
    orbit.dist += (orbit.tdist - orbit.dist) * .1;
    orbit.fx += (orbit.tfx - orbit.fx) * .12;
    orbit.fy += (orbit.tfy - orbit.fy) * .12;
    orbit.fz += (orbit.tfz - orbit.fz) * .12;

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

    // 自转：让 3D 结构持续透视旋转，念头涌现的立体感不依赖用户操作。
    // 但用户正在拖拽/缩放时必须让位——否则自转与拖拽叠加，用户拖不动、转不动（实测 2026-09-30 反馈）。
    if (!dragging && !pinching) {
      orbit.yaw += .0016;
      orbit.tyaw += .0016;
    }

    // 位置投影 + 辉光衰减
    for (const n of nodes) {
      n._pr = proj(n._p3);
      n._glow *= 0.975;
      n._act *= 0.995;
    }
    lastVis = nodes;

    // — 关联边（种子共现/因果/修正弧线，弯向网络中心；权重决定粗细/亮度）★ 反射链路 ★ —
    for (const e of edges) {
      const s = byId.get(e.src), d = byId.get(e.dst);
      if (!s || !d || !s._pr || !d._pr) continue;
      const p1 = s._pr, p2 = d._pr;
      const mx = (p1.sx + p2.sx) / 2, my = (p1.sy + p2.sy) / 2;
      const cxx = mx + (W / 2 - mx) * 0.55, cyy = my + (H / 2 - my) * 0.55;
      const hot = Math.max(s._glow, d._glow);
      const actHot = Math.max(s._act, d._act);
      const f = (p1.f + p2.f) / 2;
      const a = (0.06 + e.weight * 0.14 + hot * 0.45 + actHot * 0.4) * clamp(f * 1.6, .25, 1);
      ctx.strokeStyle = hexA(s._color, clamp(a, 0, 1));
      ctx.lineWidth = clamp((0.5 + e.weight * 1.2 + hot * 1.2 + actHot * 1.0) * f * 1.4, .4, 3.4);
      ctx.beginPath(); ctx.moveTo(p1.sx, p1.sy); ctx.quadraticCurveTo(cxx, cyy, p2.sx, p2.sy); ctx.stroke();
      if (actHot > 0.2) {   // 激活边叠加发光
        ctx.strokeStyle = hexA(s._color, actHot * 0.35);
        ctx.lineWidth = 2 + actHot * 2;
        ctx.shadowColor = s._color; ctx.shadowBlur = 10 * actHot;
        ctx.beginPath(); ctx.moveTo(p1.sx, p1.sy); ctx.quadraticCurveTo(cxx, cyy, p2.sx, p2.sy); ctx.stroke();
        ctx.shadowBlur = 0;
      }
    }

    // — 突触脉冲（沿边流动的光点）★ 仿神经信号 ★ —
    pulses = pulses.filter(pu => {
      pu.t += dt / (pu.dur || 1.1);
      if (pu.t > 1) return false;
      const s = byId.get(pu.from), d = byId.get(pu.to);
      if (!s || !d || !s._pr || !d._pr) return false;
      const p1 = s._pr, p2 = d._pr;
      const mx = (p1.sx + p2.sx) / 2, my = (p1.sy + p2.sy) / 2;
      const cxx = mx + (W / 2 - mx) * 0.55, cyy = my + (H / 2 - my) * 0.55;
      const t = pu.t;
      const qx = (1 - t) * (1 - t) * p1.sx + 2 * (1 - t) * t * cxx + t * t * p2.sx;
      const qy = (1 - t) * (1 - t) * p1.sy + 2 * (1 - t) * t * cyy + t * t * p2.sy;
      const fade = Math.sin(t * Math.PI);
      const col = (byId.get(pu.from) || {})._color || ROOT_COLOR;
      ctx.fillStyle = hexA(col, fade);
      ctx.shadowColor = col; ctx.shadowBlur = 16 * fade;
      ctx.beginPath(); ctx.arc(qx, qy, 2.6 * fade + 1, 0, 6.29); ctx.fill();
      ctx.shadowBlur = 0;
      return true;
    });

    // — 星球（按景深排序：远的先画，近的覆盖在上；权重定大小、激活值定亮度与分层色） —
    const items = nodes.filter(n => n._pr);
    items.sort((a, b) => b._pr.depth - a._pr.depth);
    for (const n of items) {
      const f = n._pr.f, sx = n._pr.sx, sy2 = n._pr.sy;
      const w = clamp((n.weight || 1) - 1, 0, 4) / 4;
      const rc = clamp((n.recall_count || 0) / 6, 0, 1);
      const act = clamp(n._liveAct || 0, 0, 1);              // 末那识实时激活值
      const pulse = 0.5 + 0.5 * Math.sin(time * 1.5 + n._phase);
      // 大小：权重/回忆次数为底，激活值进一步放大（念头越强，星球越亮越大）
      const worldR = (7 + Math.max(w, rc) * 11) * (1 + act * 0.9);
      const sr = Math.max(.8, worldR * f);
      if (sr < .7) continue;
      const fade = clamp(1.5 - n._pr.depth / 1500, .16, 1);     // 推远暗化
      // 颜色：主念集群按中枢「相对统治力分层」着色（潜意识冷灰 / 显意识暖黄 / 外显炽红），
      // 非主念星球保持类型色——是否分层由中枢竞争结果决定，前端不做绝对阈值判断
      // skill（对话基本技能：等待说完/话中回应/停顿/情绪标签音色）是所有 AI 本体共有的
      // 通识能力，恒定掌握、不是"此刻在想的事"——不参与显化（点选仍可查详情）。
      const isSkill = n.branch === 'skill';
      const isDom = !isSkill && live.dominantIds.has(n.id);
      const layerCol = isDom ? (LAYER_COLORS[live.layer] || null) : null;
      const col = layerCol || n._color;
      const alpha = clamp((0.5 + Math.max(w, rc) * 0.3 + n._glow * 0.4 + pulse * 0.12
                           + act * 0.5) * fade, 0, 1);
      ctx.shadowColor = hexA(col, 0.9);
      ctx.shadowBlur = 8 + Math.max(w, rc) * 8 + n._glow * 14 + act * 26;
      ctx.fillStyle = hexA(col, alpha);
      ctx.beginPath(); ctx.arc(sx, sy2, sr, 0, 6.29); ctx.fill();
      ctx.shadowBlur = 0;
      // 主念集群：外圈炽白光环（当下意识焦点）
      if (isDom) {
        ctx.strokeStyle = hexA('#ffffff', .35 + .3 * Math.sin(time * 5));
        ctx.lineWidth = 1.8;
        ctx.beginPath(); ctx.arc(sx, sy2, sr + 5 + 2 * Math.sin(time * 5), 0, 6.29); ctx.stroke();
      }
      // 高权重/高频回忆节点：外圈光晕环，像突触末梢
      if (w > 0.5 || rc > 0.5) {
        ctx.strokeStyle = hexA(col, 0.16 + pulse * 0.12);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(sx, sy2, sr * 2.1, 0, 6.29); ctx.stroke();
      }
      // 选中/聚焦：脉冲圈
      if (focusId === n.id) {
        ctx.strokeStyle = hexA(col, .5 + .3 * Math.sin(time * 4));
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(sx, sy2, sr + 7 + 2 * Math.sin(time * 4), 0, 6.29); ctx.stroke();
      }
      // — 记忆内容显化：默认只显圈点；唯有「当下主念」（末那识主念集群，激活值最大的那几颗）
      //   或用户聚焦的星球才亮出文本——这样一眼就能看到 AI 本体此刻在想什么，不再满屏文字。
      //   安静模式（作对话页背景）下完全不显文字：背景只做氛围，不与气泡抢注意力。
      if (!quiet && (isDom || focusId === n.id) && n.name) {
        const fs = clamp(13 * f / .5, 11, 20);
        const ly = sy2 + sr + 5;
        ctx.font = `${fs}px sans-serif`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        const label = n.name;
        const tw = ctx.measureText(label).width;
        // 半透明底衬：星云背景上保证文字清晰可读
        ctx.fillStyle = 'rgba(6,10,22,.6)';
        ctx.fillRect(sx - tw / 2 - 5, ly - 3, tw + 10, fs + 6);
        ctx.fillStyle = isDom ? hexA(layerCol || '#ffffff', .96) : 'rgba(215,222,240,.94)';
        ctx.fillText(label, sx, ly);
        if (isDom && act >= 0.02) {
          ctx.font = `${clamp(11 * f / .5, 10, 15)}px sans-serif`;
          ctx.fillStyle = hexA(col, clamp(act * 2.2, .3, .95));
          ctx.fillText(act.toFixed(3), sx, ly + fs + 1);
        }
      }
    }

    // — 自发脉冲：随机挑一条边发信号，保持画面"活着" —
    if (time > autoPulseAt && edges.length) {
      autoPulseAt = time + 0.28 + Math.random() * 0.5;
      const e = edges[(Math.random() * edges.length) | 0];
      const s = byId.get(e.src);
      if (s) { s._glow = Math.max(s._glow, 0.9); s._act = Math.max(s._act, 0.8); }
      pulses.push({ from: e.src, to: e.dst, t: 0, dur: 0.9 + Math.random() * 0.6 });
      if (pulses.length > MAX_PULSES) pulses = pulses.slice(-MAX_PULSES);
    }
  }

  // ─── 指针交互（拖拽旋转 / 滚轮·双指缩放 / 点击选中） ───
  let focusId = null;
  let pinchD = 0, moved = 0, downAt = 0;
  // 交互中标志：frame() 的自转必须让位于用户操作（否则拖拽/缩放被自转抵消，手感"拖不动"）
  let dragging = false, pinching = false;

  function hitTest(px, py) {
    let best = null, bestD = 1e9;
    for (const n of lastVis) {
      if (!n._pr) continue;
      const w = clamp((n.weight || 1) - 1, 0, 4) / 4;
      const rc = clamp((n.recall_count || 0) / 6, 0, 1);
      const sr = Math.max(4, (7 + Math.max(w, rc) * 11) * n._pr.f);
      const d = Math.hypot(px - n._pr.sx, py - n._pr.sy);
      if (d <= sr + 8 && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  function bindPointer() {
    if (!cv) return;
    cv.addEventListener('pointerdown', (e) => {
      if (!interactive) return;
      dragging = true; moved = 0; downAt = performance.now();
      cv.setPointerCapture(e.pointerId);
    });
    cv.addEventListener('pointermove', (e) => {
      if (!interactive || !dragging) return;
      const dx = e.movementX || 0, dy = e.movementY || 0;
      moved += Math.abs(dx) + Math.abs(dy);
      orbit.tyaw += dx * 0.0052;
      orbit.tpitch = clamp(orbit.tpitch + dy * 0.0044, -1.15, 1.15);
    });
    const endDrag = (e) => {
      if (!interactive || !dragging) return;
      dragging = false;
      // 判定为点击（位移小、时间短）：命中星球 → 交给宿主弹记忆详情
      if (moved < 6 && performance.now() - downAt < 420) {
        const hit = hitTest(e.clientX, e.clientY);
        if (hit) {
          focusId = hit.id;
          orbit.tfx = hit._p3.x; orbit.tfy = hit._p3.y; orbit.tfz = hit._p3.z;
          setDist(Math.min(orbit.tdist, 560));
          if (onSelect) onSelect({ id: hit.id, seed_id: hit.seed_id, name: hit.name,
                                   branch: hit.branch, weight: hit.weight });
        }
      }
    };
    cv.addEventListener('pointerup', endDrag);
    cv.addEventListener('pointercancel', () => { dragging = false; });
    cv.addEventListener('wheel', (e) => {
      if (!interactive) return;
      e.preventDefault();
      setDist(orbit.tdist * Math.exp(-e.deltaY * 0.0009));
    }, { passive: false });
    // 双指捏合缩放（手机端）
    cv.addEventListener('touchmove', (e) => {
      if (!interactive) return;
      if (e.touches.length === 2) {
        e.preventDefault();
        pinching = true;
        const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                             e.touches[0].clientY - e.touches[1].clientY);
        if (pinchD > 0 && d > 0) setDist(orbit.tdist * pinchD / d);
        pinchD = d;
      }
    }, { passive: false });
    cv.addEventListener('touchend', () => { pinchD = 0; pinching = false; });
    cv.addEventListener('touchcancel', () => { pinchD = 0; pinching = false; });
  }

  // ─── 对外接口 ───
  const api = {
    mount(canvas) {
      cv = canvas; ctx = cv.getContext('2d');
      resize();
      resetOrbit();
      bindPointer();
      addEventListener('resize', () => { resize(); if (nodes.length) layout(); });
      if (!raf) raf = requestAnimationFrame(frame);
    },
    setActive(on) {
      active = !!on;
      if (on) { lastTs = 0; autoPulseAt = 0; }
      // 用 class 而非内联 opacity：明暗交给宿主 CSS 决定——对话态可整体淡化，查看态全亮
      if (cv) cv.classList.toggle('on', !!on);
    },
    // 习气网络视图：接管指针（可拖拽/缩放/点击）；对话态交还指针（纯背景）
    setInteractive(on) {
      interactive = !!on;
      if (!on) { focusId = null; }
    },
    // 意识海观察维度：换一个维度看同一片海（宿主切换后重新 load 即生效）
    setView(v) { if (v) view = String(v); },
    currentView() { return view; },
    // 安静模式：作对话页背景时开启——只显圈点与光效，不显任何文字标签
    setQuiet(on) { quiet = !!on; },
    onSelect(fn) { onSelect = fn; },
    // 末那识中枢实时态注入（WS manas_state / REST 快照）：
    //   payload = {tick, layer, dominant_activation, silence_ticks, dominant:[{seed_id,activation}], activations:[...]}
    // 激活值落到节点 _liveAct，主念集群落到 live.dominantIds，渲染层据此放大发光与分层着色。
    setLive(payload) {
      if (!payload) return;
      live.tick = payload.tick || live.tick;
      live.layer = payload.layer || live.layer;
      live.silence_ticks = payload.silence_ticks || 0;
      live.dominant_activation = payload.dominant_activation || 0;
      live.dominance = payload.dominance || 0;
      live.at = performance.now();
      const acts = payload.activations || payload.dominant || [];
      const domIds = new Set();
      for (const a of (payload.dominant || [])) domIds.add('s' + a.seed_id);
      live.dominantIds = domIds;
      live.dominant = payload.dominant || [];
      // 激活值按 seed_id 落到节点（未在本次快照中的节点平滑衰减到 0，保持画面连续）
      const seen = new Set();
      for (const a of acts) {
        const n = byId.get('s' + a.seed_id);
        if (!n) continue;
        n._liveAct = a.activation || 0;
        seen.add(n.id);
      }
      for (const n of nodes) if (!seen.has(n.id)) n._liveAct = (n._liveAct || 0) * 0.92;
    },
    liveStats() {
      return { tick: live.tick, layer: live.layer, dominant_activation: live.dominant_activation,
               dominance: live.dominance, silence_ticks: live.silence_ticks, dominant: live.dominant };
    },
    // 宿主（记忆详情弹窗）关闭时调用：解除聚焦，相机回到全景
    clearFocus() {
      focusId = null;
      orbit.tfx = orbit.tfy = orbit.tfz = 0;
      setDist(baseDist());
    },
    // 从星系图进入：聚焦到某颗种子（seed_id 为后端 alaya_seeds.id）
    focusSeed(seedId) {
      const n = byId.get('s' + seedId);
      if (!n || !n._p3) return false;
      focusId = n.id;
      orbit.tfx = n._p3.x; orbit.tfy = n._p3.y; orbit.tfz = n._p3.z;
      setDist(560);
      return true;
    },
    async load(qs) {
      try {
        const r = await fetch(`${BASE}/api/amnesia/habit-graph?${qs}&view=${view}`, { headers: window.__authHeaders ? window.__authHeaders() : {} });
        const j = await r.json();
        if (j.error) return j;
        const first = !nodes.length;
        nodes = (j.nodes || []).map(n => ({ ...n, _liveAct: clamp(n.activation || 0, 0, 1) }));
        edges = (j.edges || []).map(e => ({ ...e }));
        layout();
        // 首次载入：全部种子给一个入场辉光（后续刷新靠 pulse 点亮新增节点）
        if (first) nodes.forEach(n => { n._glow = 1; });
        return j;
      } catch (e) { return { error: e.message }; }
    },
    // 对话写入记忆后：点亮指定节点（或随机）并发射脉冲
    pulse(ids) {
      const list = (ids && ids.length) ? ids.map(String) : [];
      if (list.length) {
        for (const id of list) {
          const n = byId.get(id);
          if (n) { n._glow = 1.4; n._act = 1; }
        }
        // 命中节点沿关联边扩散
        for (const e of edges) {
          const as = (byId.get(e.src) || {})._act || 0, ad = (byId.get(e.dst) || {})._act || 0;
          if (as > 0.5 || ad > 0.5) {
            pulses.push({ from: e.src, to: e.dst, t: 0, dur: 1.0 });
          }
        }
      } else if (edges.length) {
        const e = edges[(Math.random() * edges.length) | 0];
        pulses.push({ from: e.src, to: e.dst, t: 0, dur: 1.0 });
      }
      if (pulses.length > MAX_PULSES) pulses = pulses.slice(-MAX_PULSES);
    },
    stats() { return { nodes: nodes.length, edges: edges.length }; },
    filledIds() { return nodes.map(n => n.id); },
  };

  window.HabitGraph = api;
})();
