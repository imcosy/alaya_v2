# retrieval.py — 检索感知：向量反射（外部 embedding 接口）+ 种子召回 + 激活扩散
# 快：向量内存索引（seed_id→vec），落盘 data/vectors.npz，增量追加
# 准：三路召回（向量反射/时间近因/高频权重）融合去重
# 读即写：召回即复熏（xixi.reinforce_on_recall）
from __future__ import annotations

import logging
import os

import numpy as np

from . import db, settings
from . import llm
from .seeds import fmt_date_annotated

log = logging.getLogger("alaya.retrieval")

VEC_PATH = os.getenv("ALAYA_VEC_PATH",
                     os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "data", "vectors.npz"))

_vecs: dict[int, np.ndarray] = {}   # seed_id -> L2归一化向量
_dim = 0                            # 当前模型维度（0=未知）


async def _encode(texts: list) -> np.ndarray:
    """文本 → L2 归一化向量（外部 /embeddings）"""
    return await llm.embed([str(t) for t in texts])


async def probe() -> bool:
    """启动自检：确认向量模型可用，并在维度变化时丢弃旧索引。

    换 embedding 模型是配置变更，旧向量与新模型不在同一空间，必须整体重建——
    这里的"丢弃"不是兜底，而是配置变更后的正确处置：随后 load_vectors_once
    会遍历库中所有长期种子重新编码。返回 False 表示当前不可用（配置缺失/接口不通）。
    """
    global _dim, _loaded
    if not settings.is_configured("gateway"):
        log.warning("[retrieval] 未配置统一接口（我的资料 → 模型设置），向量召回不可用")
        return False
    v = await _encode(["ping"])
    dim = int(v.shape[1])
    old = int(settings.get("gateway.embedding_dim") or 0)
    if old and old != dim:
        log.warning("[retrieval] 向量维度 %s → %s（模型已更换），丢弃旧索引并重建", old, dim)
        _vecs.clear()
        _loaded = False          # 允许 load_vectors_once 重新走一遍全量回填
        if os.path.exists(VEC_PATH):
            os.remove(VEC_PATH)
    if old != dim:
        settings.persist_value("gateway.embedding_dim", dim)
    _dim = dim
    log.info("[retrieval] 向量模型就绪 dim=%s", dim)
    return True


async def load_vectors():
    """启动时落盘向量载入内存"""
    global _vecs, _dim
    if os.path.exists(VEC_PATH):
        try:
            data = np.load(VEC_PATH, allow_pickle=True)
            ids = data["ids"]
            mat = data["vecs"]
            _vecs = {int(i): mat[k] for k, i in enumerate(ids)}
            _dim = int(mat.shape[1]) if mat.size else 0
            log.info("[retrieval] 向量载入 %s 颗 dim=%s", len(_vecs), _dim)
        except (OSError, ValueError, KeyError) as e:
            log.warning("[retrieval] 向量载入失败（将重建）: %s", e)


_loaded = False


async def load_vectors_once():
    """幂等载入 + 库/索引双向对账：缺的补算、库中已不存在的幽灵向量剔除"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    await load_vectors()
    # 缺向量自愈只针对长期记忆：临时记忆（sensory）永不建向量——
    # 它们一旦进了向量索引，就会在每次召回里被当成"往事"命中（实测污染过：噪声碎片
    # "对，对，对。" 被注进 prompt）。
    from .seeds import TRANSIENT_TYPES
    ph = ",".join(["%s"] * len(TRANSIENT_TYPES))
    rows = await db.pool().query(
        f"SELECT id, seed_text FROM alaya_seeds WHERE state IN ('confirmed','temp') "
        f"AND seed_type NOT IN ({ph})", list(TRANSIENT_TYPES))
    missing = [r for r in rows if int(r["id"]) not in _vecs]
    if missing:
        log.info("[retrieval] 缺向量自愈：补算 %s 颗", len(missing))
        for r in missing:
            await ensure_seed_vector(int(r["id"]), str(r["seed_text"] or ""))
        save_vectors()
    # 反向对账：库里已不存在的 id（临时记忆被结生清掉、种子被删除）要把向量一并剔除——
    # 否则索引里长期挂着一堆"查不到实体"的幽灵 id，白占余弦检索的位次。
    live = {int(r["id"]) for r in await db.pool().query("SELECT id FROM alaya_seeds")}
    ghosts = [i for i in list(_vecs) if i not in live]
    if ghosts:
        for i in ghosts:
            _vecs.pop(i, None)
        log.info("[retrieval] 幽灵向量剔除 %s 颗（实体已不存在）", len(ghosts))
        save_vectors()


def model_ready() -> bool:
    return settings.is_configured("gateway")


def vector_count() -> int:
    return len(_vecs)


def save_vectors():
    """向量落盘（写种子后调用，低频）"""
    if not _vecs:
        return
    ids = np.array(list(_vecs.keys()), dtype=np.int64)
    mat = np.stack(list(_vecs.values()))
    os.makedirs(os.path.dirname(VEC_PATH), exist_ok=True)
    np.savez(VEC_PATH, ids=ids, vecs=mat)


def drop_vector(seed_id: int) -> bool:
    """作废一颗种子时同步剔除它的向量——否则它还会被余弦检索命中（幽灵记忆）"""
    if _vecs.pop(int(seed_id), None) is None:
        return False
    save_vectors()
    return True


async def ensure_seed_vector(seed_id: int, text: str):
    """种子向量缺失时补算（写种子后/启动回填）"""
    if seed_id in _vecs:
        return
    try:
        vec = (await _encode([text]))[0]
        _vecs[int(seed_id)] = vec
    except Exception as e:  # 外部接口异常不阻塞写入，下次补算
        log.warning("[retrieval] 向量补算失败 seed=%s: %s", seed_id, e)
        return
    if len(_vecs) % 20 == 0:
        save_vectors()


async def update_seed_vector(seed_id: int, text: str):
    """种子文本被修正后强制刷新向量（忆点星光·记忆编辑）"""
    _vecs.pop(int(seed_id), None)
    await ensure_seed_vector(int(seed_id), text)


# ── 写入仲裁用：相似度比对（同一件事识别）────────────────────
MERGE_SIM = 0.88      # ≥此值：与已有记忆是「同一件事的重复讲述」→ 建修正边、降权
RELATE_SIM = 0.72     # ≥此值：相关但非同一件 → 建共现边


async def nearest_seed(text: str, exclude: set | None = None) -> tuple[int, float]:
    """在已有记忆里找与新内容最像的一颗（写入仲裁：同一件事识别）。
    返回 (seed_id, 相似度)；无可用向量或库空时返回 (0, 0.0)。"""
    text = (text or "").strip()
    if not text or not _vecs:
        return 0, 0.0
    try:
        q = (await _encode([text]))[0]
    except Exception as e:  # 外部接口异常不阻塞写入
        log.warning("[retrieval] 仲裁编码失败: %s", e)
        return 0, 0.0
    skip = exclude or set()
    for sid, sim in _cosine_topk(q, 8):
        if sid in skip:
            continue
        return int(sid), float(sim)
    return 0, 0.0


async def expand_by_edges(owner_ai_id: int, seed_ids: list[int],
                          max_extra: int = 8) -> list[int]:
    """事件束扩容召回：命中种子后，沿 co/corr 边把「同一件事的其它记录」一并带出。

    这是修「明明说过却想不起来」的关键——同一件事在不同轮次被讲过多次，
    每次的表述不同、向量也未必撞得到，但只要其中一条命中，它的同族就该一起浮起
    （与记忆重激活的机制一致：点亮一个 engram，整族一起亮）。"""
    ids = [int(i) for i in (seed_ids or []) if i]
    if not ids:
        return []
    ph = ",".join(["%s"] * len(ids))
    rows = await db.pool().query(
        f"SELECT src_seed_id, dst_seed_id, weight FROM alaya_seed_edges "
        f"WHERE owner_ai_id=%s AND relation_type IN ('co','corr') "
        f"AND (src_seed_id IN ({ph}) OR dst_seed_id IN ({ph})) "
        f"ORDER BY weight DESC LIMIT %s",
        [int(owner_ai_id)] + ids + ids + [max_extra])
    have = set(ids)
    extra = []
    for r in rows:
        for k in ("src_seed_id", "dst_seed_id"):
            sid = int(r[k])
            if sid not in have:
                have.add(sid)
                extra.append(sid)
    return extra



def drop_seed_vector(seed_id: int):
    """种子删除后同步移除向量，防检索命中已删记忆"""
    _vecs.pop(int(seed_id), None)
    save_vectors()


def _cosine_topk(q: np.ndarray, topk: int) -> list[tuple[int, float]]:
    if not _vecs:
        return []
    ids = np.fromiter(_vecs.keys(), dtype=np.int64)
    mat = np.stack(list(_vecs.values()))
    sims = mat @ q
    order = np.argsort(-sims)[:topk]
    return [(int(ids[i]), float(sims[i])) for i in order]


async def recall(owner_ai_id: int, peer_ai_id: int, query_text: str,
                 topk: int = 12) -> dict:
    """三路召回融合：向量反射 + 时间近因 + 高权重常青种子。

    返回 {seeds: [{seed_id, seed_text, seed_type, seed_date_fmt, emotion_word, scene, score}],
          concepts: [概念名], seed_ids: [命中的种子ID]}
    """
    pool = db.pool()
    # 排除两类"不属于往事"的种子：
    #   ① skill：对话基本技能是所有 AI 本体的通识能力，由 _skill_prompt 专门注入；
    #   ② 临时记忆（sensory 现量一句 / working 现量窗口）：它们尚未结生，
    #      绝不参与长期召回——否则噪声碎片会被当成"TA 讲过的事"注入 prompt。
    from .seeds import TRANSIENT_TYPES
    excl = ("skill",) + TRANSIENT_TYPES
    ph_ex = ",".join(["%s"] * len(excl))
    cond = ("owner_ai_id=%s AND state IN ('confirmed','temp') AND (peer_ai_id=%s OR peer_ai_id=0) "
            f"AND seed_type NOT IN ({ph_ex})")
    params_base: list = [int(owner_ai_id), int(peer_ai_id or 0), *excl]

    # ── 路线1：向量反射 ──
    vec_hits: dict[int, float] = {}
    if query_text and len(query_text.strip()) >= 2:
        try:
            q = (await _encode([query_text.strip()]))[0]
            for sid, sim in _cosine_topk(q, topk * 3):
                if sim >= 0.42:
                    vec_hits[sid] = round(float(sim), 4)
        except Exception as e:
            log.warning("[retrieval] 向量反射失败: %s", e)
        # 向量索引是全库共享的（跨本体），命中后必须回到库里校验归属与状态：
        # 旧实现只剔 skill，导致①别的本体的种子被当成"我方的往事"注入 ②临时记忆的
        # 噪声碎片（实测"对，对，对。"）被当成往事。这里一次性把三关都补上。
        if vec_hits:
            ph = ",".join(["%s"] * len(vec_hits))
            srows = await pool.query(
                f"SELECT id FROM alaya_seeds WHERE id IN ({ph}) AND owner_ai_id=%s "
                f"AND state IN ('confirmed','temp') AND seed_type NOT IN ({ph_ex})",
                [*vec_hits, int(owner_ai_id), *excl])
            valid = {int(r["id"]) for r in srows}
            for sid in list(vec_hits):
                if sid not in valid:
                    vec_hits.pop(sid, None)

    # ── 路线2：时间近因（最近14天种子，经历连续性）──
    recent_rows = await pool.query(
        f"SELECT id, weight FROM alaya_seeds WHERE {cond} AND seed_date >= date('now','localtime','-14 day') "
        "ORDER BY seed_date DESC LIMIT 20", params_base)

    # ── 路线3：高权重常青（人格底色）──
    # 只取「最近30天没被召回」的种子：常青路线的本义是「很久没想起、但依然重要的底色记忆」。
    # 若不加这个约束，weight 顶格的种子每轮必进 top12 → 每轮被注入 prompt → 读即写再加权，
    # 形成「越召回越强、越强越被召回」的死循环，AI 会永远卡在同一个话题上出不来
    # （华哥 2026-09-30 反馈「如意一直在说老周」的根因之一）。
    # 近期已召回的记忆，其相关性已由路线1向量反射按当下语义精确衡量，不需要常青路线再兜一遍。
    evergreen_rows = await pool.query(
        f"SELECT id, weight FROM alaya_seeds WHERE {cond} "
        "AND (last_recalled_at IS NULL OR last_recalled_at < datetime('now','localtime','-30 day')) "
        "ORDER BY weight DESC LIMIT 20", params_base)

    fused: dict[int, float] = {}
    for sid, sim in vec_hits.items():
        fused[sid] = fused.get(sid, 0.0) + sim * 1.0
    for r in recent_rows:
        fused[int(r["id"])] = fused.get(int(r["id"]), 0.0) + 0.35
    for r in evergreen_rows:
        fused[int(r["id"])] = fused.get(int(r["id"]), 0.0) + min(0.5, float(r["weight"]) * 0.3)

    if not fused:
        return {"seeds": [], "concepts": [], "seed_ids": []}

    ranked = sorted(fused.items(), key=lambda kv: -kv[1])[:topk]
    ids = [sid for sid, _ in ranked]
    # 事件束扩容：命中种子沿 co/corr 边把「同一件事的其它记录」一并带出。
    # 同一件事多次讲述时表述各异、向量未必互相撞得上，但只要有一条命中，
    # 它的同族就该一起浮起——否则就会出现「明明说过、却想不起来、还重复记」。
    kin = await expand_by_edges(owner_ai_id, ids)
    if kin:
        ids = ids + [k for k in kin if k not in ids]
    ph = ",".join(["%s"] * len(ids))
    rows = await pool.query(f"SELECT * FROM alaya_seeds WHERE id IN ({ph})", ids)
    by_id = {int(r["id"]): r for r in rows}
    seeds_out = []
    for sid, score in ranked:
        r = by_id.get(sid)
        if not r:
            continue
        seeds_out.append({
            "seed_id": sid,
            "seed_type": r.get("seed_type"),
            "seed_text": r.get("seed_text"),
            "raw_text": r.get("raw_text") or "",
            "seed_date_fmt": fmt_date_annotated(r.get("seed_date")),
            "emotion_word": r.get("emotion_word") or "",
            "scene": r.get("scene") or "",
            "subject": r.get("subject") or "self",
            "score": round(score, 3),
            # 只有向量反射真的命中才算「直接相关」：常青/近因路线的加分是兜底，
            # 不能让它们冒充「这话勾起的记忆」被优先回应（否则高权重种子会永远霸占话题）
            "direct": sid in vec_hits,
            "kin": False,
        })
    # 同族记录（同一件事的其它讲述）附在其后：它们是「这件事的另一半」，
    # 供本体拼出完整脉络，但不与直接相关的记忆争抢主位。
    for sid in ids:
        if any(s["seed_id"] == sid for s in seeds_out):
            continue
        r = by_id.get(sid)
        if not r:
            continue
        seeds_out.append({
            "seed_id": sid,
            "seed_type": r.get("seed_type"),
            "seed_text": r.get("seed_text"),
            "raw_text": r.get("raw_text") or "",
            "seed_date_fmt": fmt_date_annotated(r.get("seed_date")),
            "emotion_word": r.get("emotion_word") or "",
            "scene": r.get("scene") or "",
            "subject": r.get("subject") or "self",
            "score": 0.3,
            "direct": False,
            "kin": True,
        })

    # 概念层召回：命中种子的挂靠概念（画像维度注入）
    concepts = []
    cids = [int(r["concept_id"]) for r in rows if r.get("concept_id")]
    if cids:
        ph = ",".join(["%s"] * len(cids))
        crows = await pool.query(f"SELECT name FROM alaya_concepts WHERE id IN ({ph})", cids)
        concepts = [c["name"] for c in crows]

    return {"seeds": seeds_out, "concepts": concepts, "seed_ids": ids}


async def fetch_seed_rows(seed_ids: list[int]) -> dict[int, dict]:
    """按 id 批量取种子行（主念集群渲染用）"""
    ids = [int(i) for i in (seed_ids or []) if int(i) > 0]
    if not ids:
        return {}
    ph = ",".join(["%s"] * len(ids))
    rows = await db.pool().query(f"SELECT * FROM alaya_seeds WHERE id IN ({ph})", ids)
    return {int(r["id"]): r for r in rows}


def _raw_part(r: dict, limit: int = 90) -> str:
    """原话附注：seed_text 是模型改写过的摘要（会润色，"陪最后一晚"可能被写成
    "守在身边直到去世"），raw_text 是当时真实说出口的话——它是语气与细节的凭据。
    不注入原话，本体就只能复述被润色过的骨架，越传越走样。"""
    raw = str(r.get("raw_text") or "").strip()
    st = str(r.get("seed_text") or "")
    if not raw or raw in st:
        return ""
    if len(raw) > limit:
        raw = raw[:limit] + "…"
    return f"（原话：{raw}）"


def _seed_line(r: dict, act: float) -> str:
    """一颗种子 → 一行带归属/日期/情绪/强度的描述（[我]/[对方]/[我们] 由 subject 决定）"""
    own = {"self": "我", "共有": "我们", "他人": "对方"}.get(str(r.get("subject") or "self"), "我")
    stype = str(r.get("seed_type") or "value")
    date_fmt = fmt_date_annotated(r.get("seed_date"))
    date_part = f" [{date_fmt}]" if date_fmt else ""
    emo = str(r.get("emotion_word") or "").strip()
    emo_part = f"（情绪:{emo}）" if emo and emo != "平静" else ""
    return (f"[{own}][{stype}]{date_part} {str(r.get('seed_text') or '')}"
            f"{_raw_part(r)}{emo_part} ⟨强度{act:.2f}⟩")


def build_conception_prompt(conception: dict, rows: dict[int, dict],
                            subject_label: str = "我", self_prompt: str = "") -> str:
    """第六意识·当下这一念 → LLM 记忆块（对话前提的唯一来源）

    唯识：种子（阿赖耶识所藏）必须「遇缘现行」才成为心念。此前把主念集群平铺成清单，
    等于把「库房目录」当成了「正在想的事」——十几条并列文本，没有主语、没有主次。
    这里把它收摄成**一念**，并让「我」在场（意言：第一人称的内语言）：
      self_prompt：末那识三层我识（本体我/关系我/当下我）——回答「是谁在想」
      受 · 心境 —— 这一念带着什么情绪
      想 · 所缘 —— 这一念落在什么事上（集群里第一颗实质记忆为主，同念余种为辅）
      思 · 倾向 —— 我打算做什么
    另有【伴念】：同时浮着、但并非当下焦点，作背景，不与主念争夺注意力。

    persona 模式传 self_prompt（出场本体自己的我识）——「我」与「这一念」合流为同一段；
    guide 模式不传（如意的我识另行注入，此处只把用户本体的记忆以第三人称档案呈现）。
    收摄由 manas.ManasCore.conceive() 完成（确定性、不走 LLM）；这里只做渲染。
    """
    if not conception:
        return ""

    def _usable(d: dict):
        # 所缘已由 manas.conceive() 收摄（保证是实质记忆：感官是"境"、技能是"能"，都不作所缘），
        # 这里只需确认种子行仍在（可能已被修正/删除）
        r = rows.get(int(d.get("seed_id") or 0))
        return (d, r) if r else None

    ordered = [conception.get("lead") or {}] + list(conception.get("companions") or [])
    items = [x for x in (_usable(d) for d in ordered) if x]
    if not items:
        return ""
    lead_d, lead_r = items[0]
    same = items[1:4]      # 同属这一念的余种（构成"意境"）
    rest = items[4:]       # 伴念

    if self_prompt:
        lines = ["【我 · 当下这一念】（第六意识·综合分别：我是谁，以及我此刻在想什么）", self_prompt]
    else:
        lines = [f"【{subject_label or 'TA'} · 当下这一念】（第六意识·综合分别：TA 此刻在想的事）"]
    lines += [
        f"· 受 · 心境：{conception.get('vedana') or '平静'}",
        f"· 想 · 所缘：{_seed_line(lead_r, float(lead_d.get('activation') or 0))}",
    ]
    for d, r in same:
        lines.append(f"　　└ 同属这一念：{_seed_line(r, float(d.get('activation') or 0))}")
    lines.append(f"· 思 · 倾向：{conception.get('intent') or '顺着自己的念头继续想'}")
    # 表达弹性：念头有厚薄，话就该有长短。
    # 绝不规定"说几句"（那是硬编码，只会把本体框死成固定长度），只如实交代这一念的分量，
    # 把长短交回给它自己——真人聊天本来就时短时长，凭的是这事儿对他有多重。
    depth = 1 + len(same)
    lines.append(
        f"· 话的长短：这一念牵动了 {depth} 件事，另有 {len(rest)} 条伴念垫底——"
        "说多说少由你定：一句话能说清就一句；碰上你真正在意的往事、对方在追问细节、"
        "或你情绪被带起来时，就把话讲透、讲到细节里去，可以连着讲好几段。"
        "不要为了稳妥每句都只说一两句——那是机器，不是人。")
    lines.append("（[我]=我自己的经历与认知、[对方]=对方告诉我的关于TA的事、[我们]=我们的共同经历；"
                 "绝不可把 [对方] 的事说成我自己的；这一念是我当下真实在想的东西，自然融入回应，不要逐条复述）")
    parts = ["\n".join(lines)]
    if rest:
        parts.append("【伴念】（同时浮着、但并非当下焦点；仅在本轮确有关联时自然提及，不要硬扯）\n"
                     + "\n".join(f"- {_seed_line(r, float(d.get('activation') or 0))}" for d, r in rest))
    return "\n\n".join(parts)


def build_memory_prompt(recall_result: dict, autonomous: bool = False) -> str:
    """召回种子 → prompt 注入。

    分两段：向量反射真正命中的（与当下这句话直接相关）在前，标「直接相关」；
    时间近因/高权重常青的兜底项在后，标「背景」。
    旧实现把两路混在一张平铺清单里，LLM 无从分辨哪条是「这话勾起的记忆」，
    于是要么全当背景忽略、要么拿无关旧事硬凑 → 答非所问（华哥 2026-09-29 反馈「太蠢」的根因之一）。

    「直接相关」的判据是**向量反射真的命中**（sim≥0.42），不能只看 score：
    常青路线给每颗种子的加分上限是 0.5，任何一颗高权重种子都能凭这一项越过 0.42，
    被误标成「优先据此回应」——这正是 AI 反复揪着同一件事不放的直接推手
    （华哥 2026-09-30 反馈「如意一直在说老周」的根因之一）。
    """
    seeds = recall_result.get("seeds") or []
    if not seeds:
        return ""

    def _line(s: dict) -> str:
        tag = s.get("seed_type") or "fact"
        # 归属标注：让 LLM 一眼分清这条记忆是「我的」还是「对方的」。
        # 旧实现不标归属，LLM 把对方的信息也当成自己的往事 → 认领对方的人生
        # （用户说「我女儿叫谢心瑜」，AI 就说「我女儿叫谢心瑜」）——华哥 2026-10-01 反馈
        # 「AI 理解力完全没法用、颠三倒四」的根因之一。
        own = {"self": "我", "共有": "我们", "他人": "对方"}.get(str(s.get("subject") or "self"), "我")
        own_part = f"[{own}]"
        date_part = f" [{s['seed_date_fmt']}]" if s.get("seed_date_fmt") else ""
        emo_part = f"（情绪:{s['emotion_word']}）" if s.get("emotion_word") else ""
        # 原话一并注入（seed_text 是被润色过的摘要，原话才是语气与细节的凭据）；
        # 同族记录标出来，让本体知道「这是同一件事的另一种讲述」，可据此拼出完整脉络、
        # 而不是当成两件不相干的事各说一遍。
        raw_part = _raw_part({"seed_text": s.get("seed_text") or "", "raw_text": s.get("raw_text") or ""})
        kin_part = "（同一件事的另一半，可与此条合并理解）" if s.get("kin") else ""
        return f"- [{tag}]{own_part}{date_part} {s['seed_text']}{raw_part}{emo_part}{kin_part}"

    direct = [s for s in seeds if s.get("direct")][:6]
    rest = [s for s in seeds if not s.get("direct")][:6]
    if not direct and not rest:
        return ""

    blocks = []
    if direct:
        if autonomous:
            # 自主开口：没有人说这句话。若仍写「当下这句话勾起的记忆…优先据此回应」，
            # 模型会把记忆当成对方刚说的话来回应——用户听到的就是"她把我没说过的事
            # 说成我刚说的"（华哥 2026-10-24 反馈的根因）。
            blocks.append("【此刻从你自己记忆里浮起来的事（不是对方刚说的，是TA没开口时你自己想起来的）】\n"
                          "（每条前的 [我]=我的经历、[对方]=对方告诉我的TA的事、[我们]=共同经历；"
                          "绝不可把 [对方] 的事说成我自己的；也不要把这些说成「你刚说」）\n"
                          + "\n".join(_line(s) for s in direct))
        else:
            blocks.append("【当下这句话勾起的记忆（直接相关，优先据此回应）】\n"
                          "（每条前的 [我]=我的经历、[对方]=对方告诉我的TA的事、[我们]=共同经历；"
                          "绝不可把 [对方] 的事说成我自己的。**这些是你记得的事，不是对方此刻说的话**——"
                          "引用时只能说「你以前跟我说过…」「我记得你提过…」，"
                          "绝不能说成「你刚才说…」「你刚提到…」）\n"
                          + "\n".join(_line(s) for s in direct))
    if rest:
        blocks.append("【你脑子里还浮着的其他记忆（背景，仅在本轮确有关联时自然提及，不要硬扯）】\n"
                      + "\n".join(_line(s) for s in rest))
    blocks.append("（以上记忆的日期以标注为准，绝不能把标注天数前的事说成「昨天」）")
    return "\n\n".join(blocks)
