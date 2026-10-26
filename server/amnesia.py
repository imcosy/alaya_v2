# amnesia.py — 忆点星光 v2.0：习气网络星系 + 亲友找回邀请
# 星系图数据源=真实习气网络：星球=记忆种子（alaya_seeds），突触=种子边（alaya_seed_edges），
# 聚合=概念节点（alaya_concepts，由 AI 本体整理记忆时自然生长）。零预设骨架，网络完全由真实记忆长出来。
# 邀请：本体主人 A 建邀请 → 亲友 B 凭链接登录接受 → guest 模式与 A 的对方本体对话，边聊边找回记忆
# 记忆本体=alaya_seeds：seed_text=内容 raw_text=证据 seed_date=时间 person_id=人物锚定（无A库参数名）
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from server import auth, db, retrieval, life_stages, manas, ocean, seeds
from server.auth import get_current_user
from server.seeds import log_xixi

log = logging.getLogger("alaya.amnesia")

router = APIRouter()


async def user_main_ai(user_id: int, fallback: int = 0) -> int:
    """用户记忆归属本体：xd_users.ai_me_id（登录即建的权威本体）> alaya_user_main > 名下第一个AI"""
    rows = await db.pool().query(
        "SELECT a.id FROM xd_users u JOIN xd_ais a ON a.id=u.ai_me_id "
        "WHERE u.id=%s AND a.is_alive=1", [int(user_id)])
    if rows:
        return int(rows[0]["id"])
    m = await db.pool().query(
        "SELECT m.main_ai_id FROM alaya_user_main m JOIN xd_ais a ON a.id=m.main_ai_id "
        "WHERE m.user_id=%s AND a.is_alive=1", [int(user_id)])
    if m:
        return int(m[0]["main_ai_id"])
    fr = await db.pool().query(
        "SELECT id FROM xd_ais WHERE user_id=%s AND is_alive=1 ORDER BY id LIMIT 1", [int(user_id)])
    return int(fr[0]["id"]) if fr else int(fallback or 0)


# ────────────────────────── 内部工具 ──────────────────────────

_SEED_COLS = ("b.id AS b_id, b.seed_text AS a_value, b.subject, b.raw_text AS evidence, "
              "b.seed_date AS first_time, b.confidence, b.person_id, b.seed_type, "
              "b.peer_ai_id, pa.name AS peer_name")
_SEED_FROM = ("FROM alaya_seeds b LEFT JOIN xd_ais pa ON pa.id=b.peer_ai_id")


def _with_a_name(rows: list) -> list:
    for r in rows:
        r["a_name"] = "记忆"
    return rows


async def _invite_by_token(token: str) -> dict | None:
    rows = await db.pool().query("SELECT * FROM alaya_galaxy_invites WHERE token=%s LIMIT 1", [str(token or "")])
    return rows[0] if rows else None


async def _guest_owner(token: str, user_id: int) -> dict | None:
    """guest 校验：邀请已接受且属于当前登录用户 → 返回邀请行（owner=对方本体 inviter_ai_id）"""
    inv = await _invite_by_token(token)
    if inv and inv["status"] == "accepted" and int(inv["invitee_user_id"] or 0) == int(user_id):
        return inv
    return None


# ────────────────────────── 邀请（创建/列表/接受/公开查询） ──────────────────────────

@router.get("/api/amnesia/invite")
async def invite_get(token: str):
    """公开：凭邀请链接 token 查邀请卡（亲友未登录也要能看到引导登录）"""
    inv = await _invite_by_token(token)
    if not inv:
        return JSONResponse({"error": "邀请不存在"}, status_code=404)
    ai_rows = await db.pool().query(
        "SELECT id, name, avatar FROM xd_ais WHERE id=%s", [int(inv["inviter_ai_id"])])
    return {"invite": {"token": inv["token"], "status": inv["status"],
                       "relation_type": inv["relation_type"], "address_term": inv["address_term"],
                       "self_term": inv["self_term"], "life_stage": inv.get("life_stage") or "",
                       "invitee_user_id": int(inv["invitee_user_id"] or 0),
                       "inviter_user_id": int(inv["inviter_user_id"]),
                       "inviter_ai": ai_rows[0] if ai_rows else None}}


@router.post("/api/amnesia/invite")
async def invite_create(body: dict, user: dict = Depends(get_current_user)):
    """A 创建找回邀请：B是A的什么人 / A对B的称呼 / B对A（对方本体）的称呼"""
    relation = str(body.get("relation_type") or "").strip()[:32]
    address = str(body.get("address_term") or "").strip()[:32]
    self_term = str(body.get("self_term") or "").strip()[:32]
    # 本次寻忆挂靠的人生阶段（可空）：让亲友进来后知道"要帮TA补的是哪一段经历"
    stage = str(body.get("life_stage") or "").strip().lower()[:24]
    if stage not in life_stages.STAGE_KEYS:
        stage = None
    if not relation or not address or not self_term:
        return JSONResponse({"error": "关系与称呼都要填写"}, status_code=400)
    owner_ai_id = int(body.get("owner_ai_id") or 0)
    if owner_ai_id <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id（你的AI本体）"}, status_code=400)
    # 本体判据：xd_ais.id == xd_users.ai_me_id（唯一权威），绝不用 user_id 当判据
    rows = await db.pool().query(
        "SELECT a.id FROM xd_ais a JOIN xd_users u ON u.ai_me_id=a.id "
        "WHERE a.id=%s AND u.id=%s AND a.is_alive=1", [owner_ai_id, int(user["id"])])
    if not rows:
        return JSONResponse({"error": "只能以你自己的AI本体创建邀请"}, status_code=403)
    import uuid
    token = uuid.uuid4().hex
    await db.pool().insert(
        "INSERT INTO alaya_galaxy_invites (token, inviter_user_id, inviter_ai_id, relation_type, address_term, self_term, life_stage) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        [token, int(user["id"]), owner_ai_id, relation, address, self_term, stage])
    return {"token": token, "link": f"/amnesia/?token={token}"}


@router.get("/api/amnesia/invites")
async def invite_list(user: dict = Depends(get_current_user)):
    rows = await db.pool().query(
        "SELECT id, token, inviter_ai_id, relation_type, address_term, self_term, life_stage, status, invitee_user_id, created_at, accepted_at "
        "FROM alaya_galaxy_invites WHERE inviter_user_id=%s ORDER BY id DESC LIMIT 100", [int(user["id"])])
    return {"list": rows}


@router.post("/api/amnesia/accept")
async def invite_accept(body: dict, user: dict = Depends(get_current_user)):
    """B 接受邀请：pending→accepted 原子抢占"""
    token = str(body.get("token") or "").strip()
    inv = await _invite_by_token(token)
    if not inv:
        return JSONResponse({"error": "邀请不存在"}, status_code=404)
    if int(inv["inviter_user_id"]) == int(user["id"]):
        return JSONResponse({"error": "这是你自己创建的邀请，去跟你的本体聊吧"}, status_code=400)
    invitee_ai = int(body.get("owner_ai_id") or 0)
    if invitee_ai <= 0:
        # 缺省自动取接受者的本体AI（亲友无需先选本体）
        invitee_ai = await user_main_ai(int(user["id"]))
        if invitee_ai <= 0:
            return JSONResponse({"error": "请先在控制台创建你的AI本体"}, status_code=400)
    # 本体判据：xd_ais.id == xd_users.ai_me_id（唯一权威）
    rows = await db.pool().query(
        "SELECT a.id FROM xd_ais a JOIN xd_users u ON u.ai_me_id=a.id "
        "WHERE a.id=%s AND u.id=%s AND a.is_alive=1", [invitee_ai, int(user["id"])])
    if not rows:
        return JSONResponse({"error": "只能以你自己的AI本体接受邀请"}, status_code=403)
    n = await db.pool().exec(
        "UPDATE alaya_galaxy_invites SET invitee_user_id=%s, status='accepted', accepted_at=NOW() "
        "WHERE token=%s AND status='pending'", [int(user["id"]), token])
    if not n:
        return JSONResponse({"error": "邀请已被接受或已失效"}, status_code=409)
    inv = await _invite_by_token(token)
    return {"accepted": True, "token": token, "self_term": inv["self_term"]}


# ────────────────────────── 星系图（self / guest 双视角） ──────────────────────────
# 数据源=真实习气网络：星球=记忆种子（alaya_seeds），连线=种子边（alaya_seed_edges 共现/因果/修正），
# 聚合=概念节点（alaya_concepts，由 AI 本体整理记忆时自然生长，无任何预设骨架）。
# 零预设：不存在 A-L 之类的固定分支，网络完全由真实记忆长出来。

@router.get("/api/amnesia/galaxy")
async def galaxy(token: str = "", owner_ai_id: int = 0, user: dict = Depends(get_current_user)):
    """本人（无token）：owner=自己的本体AI（owner_ai_id 必传，缺省取名下第一个）；
    guest（带已接受token）：owner=对方本体，只读+转发"""
    inv = None
    mode = "self"
    if token:
        inv = await _guest_owner(token, user["id"])
        if not inv:
            return JSONResponse({"error": "邀请无效或不属于当前用户"}, status_code=403)
        owner_ai_id = int(inv["inviter_ai_id"])
        mode = "guest"
    elif owner_ai_id <= 0:
        owner_ai_id = await user_main_ai(int(user["id"]))
    if owner_ai_id <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)

    pool = db.pool()
    # 星球 = 活跃记忆种子（confirmed/temp），权重定大小，类型定色相
    seed_rows = await pool.query(
        "SELECT id, seed_type, seed_text, raw_text, subject, person_id, concept_id, "
        "seed_date, emotion_word, scene, confidence, weight, recall_count "
        "FROM alaya_seeds WHERE owner_ai_id=%s AND state IN ('confirmed','temp') "
        "ORDER BY weight DESC, id DESC LIMIT 2000", [int(owner_ai_id)])
    # 聚合 = 概念节点（AI 整理记忆时自然凝聚出来的，非预设）
    concept_rows = await pool.query(
        "SELECT id, parent_id, name, branch, weight FROM alaya_concepts "
        "WHERE owner_ai_id=%s AND status='active' ORDER BY weight DESC LIMIT 500", [int(owner_ai_id)])
    concept_name = {int(c["id"]): c["name"] for c in concept_rows}

    nodes = []
    for r in seed_rows:
        cid = int(r.get("concept_id") or 0)
        nodes.append({
            "id": f"s{r['id']}", "kind": "seed", "seed_id": int(r["id"]),
            "parent_id": (f"k{cid}" if cid else None),
            "concept_id": cid, "concept": concept_name.get(cid) or "",
            "branch": r.get("seed_type") or "value",
            "name": (str(r.get("seed_text") or "")[:40] or "记忆"),
            "value": str(r.get("seed_text") or ""),
            "evidence": str(r.get("raw_text") or ""),
            "subject": r.get("subject") or "self",
            "person_id": int(r.get("person_id") or 0),
            "seed_date": str(r.get("seed_date") or ""),
            "emotion_word": r.get("emotion_word") or "",
            "scene": r.get("scene") or "",
            "confidence": float(r.get("confidence") or 0),
            "weight": float(r.get("weight") or 1),
            "recall_count": int(r.get("recall_count") or 0),
            "depth": 1,
        })
    for c in concept_rows:
        nodes.append({
            "id": f"k{c['id']}", "kind": "concept", "concept_id": int(c["id"]),
            "parent_id": None, "branch": c.get("branch") or "fact",
            "name": c.get("name") or "", "value": "", "evidence": "",
            "weight": float(c.get("weight") or 1), "depth": 0,
            "seed_count": 0,
        })
    seed_ids = {n["seed_id"] for n in nodes if n["kind"] == "seed"}
    for n in nodes:
        if n["kind"] == "concept":
            n["seed_count"] = sum(1 for s in nodes if s.get("concept_id") == n["concept_id"])

    # 连线1：种子边（真实突触：共现/因果/修正）
    edges = []
    seen = set()
    edge_rows = await pool.query(
        "SELECT src_seed_id, dst_seed_id, relation_type, weight FROM alaya_seed_edges "
        "WHERE owner_ai_id=%s ORDER BY weight DESC LIMIT 4000", [int(owner_ai_id)])
    for e in edge_rows:
        s, d = int(e["src_seed_id"]), int(e["dst_seed_id"])
        if s not in seed_ids or d not in seed_ids:
            continue
        key = (s, d) if s < d else (d, s)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"src": f"s{s}", "dst": f"s{d}",
                      "relation": e.get("relation_type") or "co",
                      "weight": float(e.get("weight") or 0.3)})
    # 连线2：概念 → 其下种子（归属边）
    for n in nodes:
        if n["kind"] == "seed" and n.get("concept_id"):
            edges.append({"src": f"k{n['concept_id']}", "dst": n["id"],
                          "relation": "sub", "weight": 0.9})
    # 连线3：概念间边（AI 整理时建立的关联）
    cids = {int(c["id"]) for c in concept_rows}
    ce_rows = await pool.query(
        "SELECT src, dst, relation_type, weight FROM alaya_concept_edges "
        "WHERE owner_ai_id=%s ORDER BY weight DESC LIMIT 2000", [int(owner_ai_id)])
    for e in ce_rows:
        s, d = int(e["src"]), int(e["dst"])
        if s not in cids or d not in cids:
            continue
        edges.append({"src": f"k{s}", "dst": f"k{d}",
                      "relation": e.get("relation_type") or "co",
                      "weight": float(e.get("weight") or 0.3)})

    ai_rows = await db.pool().query(
        "SELECT id, name, avatar FROM xd_ais WHERE id=%s", [owner_ai_id])
    u_rows = await db.pool().query(
        "SELECT nickname, avatar FROM xd_users WHERE id=%s", [int(user["id"])])
    invites = None
    if mode == "self":
        inv_rows = await db.pool().query(
            "SELECT status FROM alaya_galaxy_invites WHERE inviter_user_id=%s", [int(user["id"])])
        invites = {"total": len(inv_rows),
                   "accepted": sum(1 for r in inv_rows if r["status"] == "accepted")}
    type_stat: dict = {}
    for n in nodes:
        if n["kind"] == "seed":
            type_stat[n["branch"]] = type_stat.get(n["branch"], 0) + 1
    return {"mode": mode,
            "user": {"user_id": int(user["id"]), "username": (u_rows[0]["nickname"] if u_rows else "") or "",
                     "avatar": (u_rows[0]["avatar"] if u_rows else "") or ""},
            "ai": ai_rows[0] if ai_rows else None,
            "invite": ({"relation_type": inv["relation_type"], "address_term": inv["address_term"],
                        "self_term": inv["self_term"], "token": inv["token"]} if inv else None),
            "nodes": nodes,
            "edges": edges,
            "stats": {"seeds": len(seed_ids), "concepts": len(concept_rows),
                      "edges": len(edges), "type_stat": type_stat},
            "invites": invites}


# ────────────────────────── 习气网络（对话窗口背景：真实记忆网络） ──────────────────────────
# 与星系图同源（真实种子+种子边），只做背景渲染：星球 + 突触，供前端画脉冲


@router.get("/api/amnesia/habit-graph")
async def habit_graph(token: str = "", owner_ai_id: int = 0, view: str = "semantic",
                      user: dict = Depends(get_current_user)):
    """意识海（一本体一片海）：真实记忆种子 + 种子边，并按**观察维度**给出种子在海中的坐标。

    唯识：阿赖耶识亦称「藏识海」——种子是海中的点、边是点之间的引力、主念涌现是海面起的浪。
    海是同一片海，观察维度决定怎么排布（见 ocean.py）：
      semantic 语义 | time 时间 | emotion 情绪 | person 人物 | scene 场景
    """
    if token:
        inv = await _guest_owner(token, user["id"])
        if not inv:
            return JSONResponse({"error": "邀请无效或不属于当前用户"}, status_code=403)
        owner_ai_id = int(inv["inviter_ai_id"])
    elif owner_ai_id <= 0:
        owner_ai_id = await user_main_ai(int(user["id"]))
    if owner_ai_id <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)

    pool = db.pool()
    seed_rows = await pool.query(
        "SELECT id, seed_type, seed_text, weight, activation, seed_date, person_id, emotion_word, scene "
        "FROM alaya_seeds WHERE owner_ai_id=%s AND state IN ('confirmed','temp') "
        "ORDER BY weight DESC, id DESC LIMIT 400", [int(owner_ai_id)])
    coords = ocean.ocean_coords(
        [{"seed_id": int(r["id"]), "seed_type": r.get("seed_type"), "seed_date": r.get("seed_date"),
          "person_id": r.get("person_id"), "emotion_word": r.get("emotion_word"),
          "scene": r.get("scene")} for r in seed_rows], view)
    # 热态中枢在跑时，激活值以内存实时值为准（DB 的 activation 只是温态快照）
    core = manas.get_core(int(owner_ai_id))
    nodes = [{"id": f"s{r['id']}", "branch": r.get("seed_type") or "value",
              "name": str(r.get("seed_text") or "")[:40],
              "seed_id": int(r["id"]),
              "pos": coords.get(int(r["id"]), [0.0, 0.0, 0.0]),
              "weight": float(r.get("weight") or 1),
              "activation": round(core.nodes[int(r["id"])]["act"], 4)
                            if (core and int(r["id"]) in core.nodes)
                            else float(r.get("activation") or 0.0)}
             for r in seed_rows]
    seed_ids = {int(r["id"]) for r in seed_rows}
    seen = set()
    edges = []
    edge_rows = await pool.query(
        "SELECT src_seed_id, dst_seed_id, weight FROM alaya_seed_edges "
        "WHERE owner_ai_id=%s ORDER BY weight DESC LIMIT 2000", [int(owner_ai_id)])
    for e in edge_rows:
        s, d = int(e["src_seed_id"]), int(e["dst_seed_id"])
        if s not in seed_ids or d not in seed_ids:
            continue
        key = (s, d) if s < d else (d, s)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"src": f"s{s}", "dst": f"s{d}", "weight": float(e.get("weight") or 0.3)})
    return {"nodes": nodes, "edges": edges, "view": view,
            "stats": {"seeds": len(seed_ids), "edges": len(edges)}}


# ────────────────────────── 末那识心智涌现中枢 ──────────────────────────
# 中枢状态（激活值/阈值层级/主念集群）+ 人格特质种子读写。
# 热态网络常驻内存迭代，本接口只读取内存快照（DB 不参与实时运算）。


async def _manas_owner(token: str, owner_ai_id: int, user: dict) -> int:
    if token:
        inv = await _guest_owner(token, user["id"])
        if not inv:
            return 0
        return int(inv["inviter_ai_id"])
    if owner_ai_id > 0:
        return int(owner_ai_id)
    return await user_main_ai(int(user["id"]))


@router.get("/api/amnesia/manas")
async def manas_state(token: str = "", owner_ai_id: int = 0, top: int = 80,
                      user: dict = Depends(get_current_user)):
    """末那识中枢实时状态：全网激活值快照 + 阈值层级 + 当前主念集群。

    热态未载入时（本体未上线），从温态快照表读取上次落盘的状态。
    """
    oid = await _manas_owner(token, owner_ai_id, user)
    if oid <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)
    core = manas.get_core(oid)
    if core is not None:
        return {"live": True, "owner_ai_id": oid, **core.snapshot_payload(top)}
    rows = await db.pool().query(
        "SELECT tick, dominant, dominant_activation, layer, silence_ticks, updated_at "
        "FROM alaya_manas_state WHERE owner_ai_id=%s", [oid])
    if not rows:
        return JSONResponse({"error": "该本体中枢尚未启动"}, status_code=404)
    r = rows[0]
    return {"live": False, "owner_ai_id": oid, "tick": int(r["tick"] or 0),
            "layer": r["layer"], "dominant_activation": float(r["dominant_activation"] or 0),
            "silence_ticks": int(r["silence_ticks"] or 0),
            "dominant": db.jload(r.get("dominant"), []), "activations": [],
            "updated_at": str(r.get("updated_at") or "")}


@router.post("/api/amnesia/manas/attach")
async def manas_attach(owner_ai_id: int = 0, user: dict = Depends(get_current_user)):
    """控制台查看中枢：把本体的末那识中枢载入热态（此后永久后台迭代）。

    控制台不是对话端，没有 WS 会话，若不显式载入，中枢就只有温态快照可读——
    习气网络将看不到实时激活值与主念涌现。载入是幂等的：已在跑则直接复用。
    """
    if owner_ai_id <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)
    await auth.require_owner_ai(user, owner_ai_id)
    core = await manas.ensure_core(int(owner_ai_id))
    return {"ok": True, "owner_ai_id": int(owner_ai_id),
            "nodes": len(core.nodes), "edges": len(core.edges)}


@router.get("/api/amnesia/manas/traits")
async def manas_traits(token: str = "", owner_ai_id: int = 0,
                       user: dict = Depends(get_current_user)):
    """人格特质种子集群：AI 行为差异的唯一来源（权重由熏习演化，非代码写死）"""
    oid = await _manas_owner(token, owner_ai_id, user)
    if oid <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)
    core = manas.get_core(oid)
    rows = await db.pool().query(
        "SELECT id, seed_text, raw_text, weight, activation, recall_count, created_at "
        "FROM alaya_seeds WHERE owner_ai_id=%s AND seed_type='trait' AND state='confirmed' "
        "ORDER BY weight DESC", [oid])
    out = []
    for r in rows:
        sid = int(r["id"])
        act = core.nodes[sid]["act"] if (core and sid in core.nodes) else float(r.get("activation") or 0)
        out.append({"seed_id": sid, "name": r["seed_text"], "desc": r.get("raw_text") or "",
                    "weight": float(r.get("weight") or 1), "activation": round(act, 4),
                    "recall_count": int(r.get("recall_count") or 0)})
    return {"owner_ai_id": oid, "traits": out,
            "persona_gain": round(core._persona_gain(), 4) if core else None}


@router.get("/api/amnesia/manas/trace")
async def manas_trace(token: str = "", owner_ai_id: int = 0, limit: int = 60,
                      user: dict = Depends(get_current_user)):
    """主念涌现轨迹：意识流的起止与层级跃迁（外显行为审计）"""
    oid = await _manas_owner(token, owner_ai_id, user)
    if oid <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)
    rows = await db.pool().query(
        "SELECT tick, layer, activation, dominant, created_at FROM alaya_manas_trace "
        "WHERE owner_ai_id=%s ORDER BY id DESC LIMIT %s", [oid, max(1, min(300, int(limit)))])
    return {"owner_ai_id": oid, "trace": [
        {"tick": int(r["tick"]), "layer": r["layer"], "activation": float(r["activation"] or 0),
         "dominant": db.jload(r.get("dominant"), []), "at": str(r.get("created_at") or "")}
        for r in rows]}


# ────────────────────────── 人生脉络（视角二：待补全的记忆篇章） ──────────────────────────
# 与星系图同源：阶段进度完全由真实种子聚合得出（alaya_seeds.life_stage），不落任何冗余状态。


@router.get("/api/amnesia/life-threads")
async def life_threads(token: str = "", owner_ai_id: int = 0, user: dict = Depends(get_current_user)):
    """人生脉络视角：按年龄裁剪的阶段 × 该阶段真实找回的记忆条数与最近一条"""
    if token:
        inv = await _guest_owner(token, user["id"])
        if not inv:
            return JSONResponse({"error": "邀请无效或不属于当前用户"}, status_code=403)
        owner_ai_id = int(inv["inviter_ai_id"])
    elif owner_ai_id <= 0:
        owner_ai_id = await user_main_ai(int(user["id"]))
    if owner_ai_id <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)

    pool = db.pool()
    u_rows = await pool.query(
        "SELECT u.age, u.birthday FROM xd_ais a JOIN xd_users u ON u.id=a.user_id WHERE a.id=%s",
        [int(owner_ai_id)])
    age = life_stages.age_from(u_rows[0]) if u_rows else life_stages.DEFAULT_AGE

    stat_rows = await pool.query(
        "SELECT life_stage, COUNT(*) c, MAX(id) last_id FROM alaya_seeds "
        "WHERE owner_ai_id=%s AND state IN ('confirmed','temp') AND life_stage IS NOT NULL "
        "GROUP BY life_stage", [int(owner_ai_id)])
    stat = {str(r["life_stage"]): r for r in stat_rows}
    last_ids = [int(r["last_id"]) for r in stat_rows if r.get("last_id")]
    lasts: dict = {}
    if last_ids:
        ph = ",".join(["%s"] * len(last_ids))
        lrows = await pool.query(
            f"SELECT id, seed_text, seed_date FROM alaya_seeds WHERE id IN ({ph})", last_ids)
        by_id = {int(r["id"]): r for r in lrows}
        for k, r in stat.items():
            lr = by_id.get(int(r["last_id"])) if r.get("last_id") else None
            if lr:
                lasts[k] = {"text": str(lr["seed_text"] or "")[:80],
                            "date": str(lr["seed_date"] or "")}

    counts = {k: int(v["c"] or 0) for k, v in stat.items()}
    focus = life_stages.pick_focus(age, counts)
    stages = []
    for s in life_stages.stages_for_age(age):
        n = counts.get(s["key"], 0)
        stages.append({
            "key": s["key"], "name": s["name"], "subtitle": s["subtitle"],
            "topics": s["topics"], "count": n, "started": n > 0,
            "focus": s["key"] == focus,
            "last_seed": (lasts.get(s["key"]) or {}).get("text") or "",
            "last_date": (lasts.get(s["key"]) or {}).get("date") or "",
        })
    return {"age": age, "focus": focus, "stages": stages,
            "total": sum(counts.values()),
            "covered": sum(1 for x in stages if x["count"] > 0)}


# ────────────────────────── 忆点星光：人物星云 → 事件星 ──────────────────────────
# 一颗星＝一个事件束（alaya_seeds.episode_id）：星的内容＝该 episode 下的种子（主人记得的＋亲友补的）。
# 星不显字（事件不需要标题），只有亮/暗：暗＝灰调微呼吸（还没收到亲友补充），亮＝已点亮。
# 主人视角：如意梳理出的人物脉络，每人名下一族星；点人物即生成转发。
# 亲友视角：凭链接进来只看「我与他」这一族（他人隐私不出），未登录可先看，点星才要求登录。

_STAR_LIMIT = 800


async def _stars_of(owner_ai_id: int, person_id: int = 0) -> list[dict]:
    """取某本体（可限定某个人物）下的全部事件星，并带出每颗星的内容（种子）"""
    params: list = [int(owner_ai_id)]
    where = "s.owner_ai_id=%s"
    if person_id:
        where += " AND s.person_id=%s"
        params.append(int(person_id))
    rows = await db.pool().query(
        "SELECT s.id, s.person_id, s.episode_id, s.source, s.lit, s.lit_at, "
        "p.name AS person_name, p.relation, p.emoji "
        "FROM alaya_galaxy_stars s LEFT JOIN alaya_persons p ON p.id=s.person_id "
        f"WHERE {where} ORDER BY s.lit ASC, s.id DESC LIMIT {_STAR_LIMIT}", params)
    if not rows:
        return []
    eps = [str(r["episode_id"]) for r in rows if r.get("episode_id")]
    seeds_by_ep: dict = {}
    if eps:
        ph = ",".join(["%s"] * len(eps))
        srows = await db.pool().query(
            f"SELECT id, episode_id, seed_type, seed_text, subject, seed_date "
            f"FROM alaya_seeds WHERE owner_ai_id=%s AND episode_id IN ({ph}) "
            f"AND state IN ('confirmed','temp') ORDER BY id ASC", [int(owner_ai_id)] + eps)
        for r in srows:
            seeds_by_ep.setdefault(str(r["episode_id"]), []).append({
                "seed_id": int(r["id"]), "seed_type": r.get("seed_type") or "",
                "text": str(r.get("seed_text") or ""), "subject": r.get("subject") or "self",
                "date": str(r.get("seed_date") or "")})
    out = []
    for r in rows:
        ep = str(r.get("episode_id") or "")
        out.append({
            "star_id": int(r["id"]), "person_id": int(r["person_id"] or 0),
            "person_name": r.get("person_name") or "", "relation": r.get("relation") or "",
            "emoji": r.get("emoji") or "", "episode_id": ep,
            "source": r.get("source") or "auto", "lit": int(r.get("lit") or 0),
            "lit_at": str(r.get("lit_at") or ""), "seeds": seeds_by_ep.get(ep, [])})
    return out


@router.get("/api/amnesia/stars")
async def stars_mine(user: dict = Depends(get_current_user)):
    """忆点星光·主人视角：我的全部人物脉络 + 每人名下的事件星（暗＝待亲友来点）"""
    owner = await user_main_ai(int(user["id"]))
    if owner <= 0:
        return JSONResponse({"error": "请先创建你的AI本体"}, status_code=400)
    stars = await _stars_of(owner)
    persons: dict = {}
    for st in stars:
        pid = st["person_id"]
        p = persons.setdefault(pid, {"person_id": pid, "name": st["person_name"],
                                     "relation": st["relation"], "emoji": st["emoji"],
                                     "stars": [], "total": 0, "lit": 0})
        p["stars"].append(st)
        p["total"] += 1
        p["lit"] += st["lit"]
    # 人物脉络里还没成星的人也要列出来（如意梳理过的人，正等着被转发）
    prows = await db.pool().query(
        "SELECT id, name, relation, emoji FROM alaya_persons WHERE owner_ai_id=%s "
        "ORDER BY id DESC LIMIT 500", [int(owner)])
    for r in prows:
        pid = int(r["id"])
        if pid not in persons:
            persons[pid] = {"person_id": pid, "name": r["name"], "relation": r.get("relation") or "",
                            "emoji": r.get("emoji") or "", "stars": [], "total": 0, "lit": 0}
    plist = sorted(persons.values(), key=lambda x: (-x["total"], -x["person_id"]))
    ai_rows = await db.pool().query("SELECT id, name, avatar FROM xd_ais WHERE id=%s", [int(owner)])
    return {"owner_ai_id": int(owner), "ai": ai_rows[0] if ai_rows else None, "persons": plist,
            "stats": {"persons": len(plist), "stars": len(stars),
                      "lit": sum(1 for st in stars if st["lit"])}}


@router.get("/api/amnesia/starview")
async def star_view(token: str = ""):
    """忆点星光·亲友视角（公开可看，登录在点星时才要求）：链接只放出「我与他」这一族星"""
    inv = await _invite_by_token(token)
    if not inv:
        return JSONResponse({"error": "链接无效或已过期"}, status_code=404)
    owner = int(inv["inviter_ai_id"])
    pid = int(inv.get("person_id") or 0)
    stars = await _stars_of(owner, pid) if pid else []
    ai_rows = await db.pool().query("SELECT id, name, avatar FROM xd_ais WHERE id=%s", [owner])
    p_rows = await db.pool().query(
        "SELECT id, name, relation, emoji FROM alaya_persons WHERE id=%s", [pid]) if pid else []
    return {"token": str(token), "owner_ai_id": owner,
            "ai": ai_rows[0] if ai_rows else None,
            "person": p_rows[0] if p_rows else None,
            "accepted": inv["status"] == "accepted",
            "stars": stars,
            "stats": {"stars": len(stars), "lit": sum(1 for st in stars if st["lit"])}}


@router.post("/api/amnesia/share")
async def share_person(body: dict, user: dict = Depends(get_current_user)):
    """点人物即生成转发：为该人物建（或复用）一条链接，亲友凭此进来只看这一族星。"""
    person_id = int(body.get("person_id") or 0)
    if person_id <= 0:
        return JSONResponse({"error": "缺少 person_id"}, status_code=400)
    owner = await user_main_ai(int(user["id"]))
    if owner <= 0:
        return JSONResponse({"error": "请先创建你的AI本体"}, status_code=400)
    rows = await db.pool().query(
        "SELECT id, name, relation FROM alaya_persons WHERE id=%s AND owner_ai_id=%s",
        [person_id, int(owner)])
    if not rows:
        return JSONResponse({"error": "人物不存在"}, status_code=404)
    person = rows[0]
    ex = await db.pool().query(
        "SELECT token FROM alaya_galaxy_invites WHERE inviter_ai_id=%s AND person_id=%s "
        "AND status='pending' ORDER BY id DESC LIMIT 1", [int(owner), person_id])
    if ex:
        token = ex[0]["token"]
    else:
        import uuid
        token = uuid.uuid4().hex
        await db.pool().insert(
            "INSERT INTO alaya_galaxy_invites (token, inviter_user_id, inviter_ai_id, person_id, "
            "relation_type, address_term, self_term) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [token, int(user["id"]), int(owner), person_id,
             str(person.get("relation") or ""), str(person["name"]), ""])
    return {"token": token, "person_id": person_id, "person_name": person["name"],
            "link": f"/amnesia/?token={token}"}


@router.post("/api/amnesia/star/add")
async def star_add(body: dict, user: dict = Depends(get_current_user)):
    """双击星空空白 → 加一颗星（自己想起的事，先留一颗待亲友来点的暗星）"""
    person_id = int(body.get("person_id") or 0)
    if person_id <= 0:
        return JSONResponse({"error": "缺少 person_id"}, status_code=400)
    owner = int(body.get("owner_ai_id") or 0) or await user_main_ai(int(user["id"]))
    if owner <= 0:
        return JSONResponse({"error": "请先创建你的AI本体"}, status_code=400)
    await auth.require_owner_ai(user, owner)
    import uuid
    episode = uuid.uuid4().hex[:16]
    star_id = await seeds.ensure_star(int(owner), person_id, episode, source="manual")
    return {"star_id": star_id, "episode_id": episode, "lit": 0, "person_id": person_id}


# 关系分类的取值域（只做「归类」，不做「判定」——依据是本体真实记得的事）
_REL_KEYS = ("父亲", "母亲", "丈夫", "妻子", "儿子", "女儿", "兄弟", "姐妹", "亲戚", "家人",
             "同学", "同事", "朋友", "邻里", "老师", "学生", "领导", "客户", "合作伙伴", "其他")

_ORGANIZE_PROMPT = """你在替你的主人整理「人物脉络」——他人生里出现过的人。

下面是这些人，以及你记得的、与他们有关的事（摘录）：
{people}

请给每个人一个**关系分类**，只能从这个列表里选：{keys}
拿不准就填「朋友」。绝不编造没有依据的关系。

只输出 JSON，不要任何解释：
{{"people":[{{"name":"名字","relation":"分类"}}]}}"""


@router.post("/api/amnesia/persons/organize")
async def persons_organize(body: dict, user: dict = Depends(get_current_user)):
    """如意梳理人物脉络：依本体**真实记得的事**，为每个人物定一个关系分类。

    零编造：分类依据只有 alaya_seeds 里确实存在的记忆（该人名下种子摘录）。
    只补 relation 为空的人；已分类的不覆盖（尊重人工/历史结果）。
    """
    owner = int(body.get("owner_ai_id") or 0) or await user_main_ai(int(user["id"]))
    if owner <= 0:
        return JSONResponse({"error": "请先创建你的AI本体"}, status_code=400)
    await auth.require_owner_ai(user, owner)
    p = db.pool()
    rows = await p.query(
        "SELECT id, name FROM alaya_persons WHERE owner_ai_id=%s AND (relation IS NULL OR relation='') "
        "ORDER BY id DESC LIMIT 60", [int(owner)])
    if not rows:
        return {"ok": True, "updated": 0, "total": 0}
    ids = [int(r["id"]) for r in rows]
    iph = ",".join(["%s"] * len(ids))
    srows = await p.query(
        f"SELECT person_id, seed_text FROM alaya_seeds WHERE owner_ai_id=%s AND person_id IN ({iph}) "
        f"AND state IN ('confirmed','temp') ORDER BY weight DESC LIMIT 600", [int(owner)] + ids)
    notes: dict = {}
    for r in srows:
        notes.setdefault(int(r["person_id"]), []).append(str(r.get("seed_text") or "")[:60])
    lines = [f"- {r['name']}：{'；'.join(notes.get(int(r['id']), [])[:4]) or '（还没记下具体的事）'}"
             for r in rows]
    from server.llm import llm_chat_fn
    import json as _json
    import re as _re
    try:
        raw = await llm_chat_fn(
            [{"role": "user", "content": _ORGANIZE_PROMPT.format(
                people="\n".join(lines), keys="/".join(_REL_KEYS))}],
            temperature=0.2, max_tokens=2000)
    except Exception as e:
        log.error("[organize] 人物梳理失败: %s", e)
        return JSONResponse({"error": "梳理失败，稍后再试"}, status_code=500)
    m = _re.search(r"\{[\s\S]*\}", raw or "")
    try:
        data = _json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}
    name2id = {str(r["name"]): int(r["id"]) for r in rows}
    updated = 0
    for it in (data.get("people") or []):
        nm = str(it.get("name") or "").strip()
        rel = str(it.get("relation") or "").strip()
        if nm in name2id and rel in _REL_KEYS:
            await p.exec("UPDATE alaya_persons SET relation=%s WHERE id=%s", [rel, name2id[nm]])
            updated += 1
    return {"ok": True, "updated": updated, "total": len(rows)}


_STAR_CHAT_PROMPT = """你就是「{owner_name}」本人——你是TA的AI本体，说话永远用第一人称「我」。
现在跟你说话的是「{guest_name}」{rel_note}。你们之间有这么一件事，你只握着自己那一半：

{star_content}

{style_note}
【你在做的事】
像老朋友聊天那样，请TA把另一半讲给你听：
1. 一次只问一两个最关键的细节（时间、地点、当时在场的人、经过、感受、后来怎么样了），别说套话、别一口气问一堆；
2. 顺着TA刚说的往下追，TA讲的具体内容要接得住（「哦，原来那天是你先到的」），不要空洞附和；
3. **绝不编造**——TA没说过的细节，你只能问，绝不能当成自己想起来了；
4. 语气自然平稳，像正常人说话，不要助手腔、不要"我帮你整理一下"这种旁观者口气。

【什么时候算聊够了】
当这件事的来龙去脉、当时的人和情形都清楚得差不多了，就收尾：用你自己的话说一句这段往事在心里完整了，
然后在回复的**最后单独一行**加上标记（这一行不会被念出来，也不要解释它）：
[[LIT]]            —— 这件事补完整了
[[LIT:星id,星id]]  —— 聊着还补全了别的事，一起点亮

当前这一族里还暗着的星（可用来判断该点亮哪些）：
{stars_list}

只输出你要对TA说的话（+可选的那一行标记），不要任何解释。"""


async def _star_chat_llm(owner_ai_id: int, star: dict, owner_name: str, guest_name: str,
                         rel: str, star_seeds: list, siblings: list, history: list,
                         user_text: str) -> str:
    """生成一轮引导回复（本体人格 + 这颗星已有内容 + 对话历史）"""
    from server.llm import llm_chat_fn
    content = "\n".join("· " + str(x.get("text") or "") for x in star_seeds[:12]) or "（我这边几乎想不起来了）"
    style_rows = await db.pool().query(
        "SELECT seed_text FROM alaya_seeds WHERE owner_ai_id=%s AND seed_type='style' "
        "AND state='confirmed' ORDER BY weight DESC LIMIT 6", [int(owner_ai_id)])
    style_note = ("【你平时说话的习惯】\n" + "\n".join("· " + str(r["seed_text"]) for r in style_rows)
                  + "\n") if style_rows else ""
    stars_list = "\n".join(
        "· 星id={} {}".format(int(s["star_id"]), _star_brief(s)) for s in (siblings or [])[:8]) or "（暂无）"
    msgs = [{"role": "system", "content": _STAR_CHAT_PROMPT.format(
        owner_name=owner_name, guest_name=guest_name,
        rel_note=("（{0}）".format(rel) if rel else ""),
        star_content=content, style_note=style_note, stars_list=stars_list)}]
    for h in (history or [])[-10:]:
        msgs.append({"role": "assistant" if h.get("role") == "ai" else "user",
                     "content": str(h.get("content") or "")})
    msgs.append({"role": "user", "content": user_text})
    return await llm_chat_fn(msgs, temperature=0.75, max_tokens=600)


def _star_brief(s: dict) -> str:
    """一颗星的一句话摘要（取星内第一条实质记忆），供 LLM 判断该点亮哪几颗"""
    for x in (s.get("seeds") or []):
        t = str(x.get("text") or "").strip()
        if t:
            return t[:50]
    return "（还没内容）"


_LIT_RE = re.compile(r"\[\[\s*LIT\s*(?::\s*([0-9,\s]+))?\s*\]\]")


@router.post("/api/amnesia/star/chat")
async def star_chat(body: dict, user: dict = Depends(get_current_user)):
    """忆点星光·补星对话：像聊天一样把你知道的讲出来。

    一轮＝「听 / 看 → 写进习气网络 → 本体回一句并继续引导」，聊够了由 AI 自己决定点亮——
    它可以在回复末尾给 [[LIT]]（这颗星补完整了）或 [[LIT:星id,星id]]（顺带补全了别的事）。
    信息写不写、亮不亮、亮几颗，全交给 AI，不由代码写死。
    """
    star_id = int(body.get("star_id") or 0)
    token = str(body.get("token") or "").strip()
    text = str(body.get("text") or "").strip()
    pcm_b64 = str(body.get("pcm_b64") or "").strip()
    image_b64 = str(body.get("image_b64") or "").strip()
    if star_id <= 0:
        return JSONResponse({"error": "缺少 star_id"}, status_code=400)
    rows = await db.pool().query(
        "SELECT id, owner_ai_id, person_id, episode_id FROM alaya_galaxy_stars WHERE id=%s", [star_id])
    if not rows:
        return JSONResponse({"error": "这颗星不存在"}, status_code=404)
    star = rows[0]
    star_owner = int(star["owner_ai_id"])
    episode = str(star["episode_id"] or "")

    # 链接归属校验 + 首次点星即绑定（登录在进弹窗时就已保证，这里再兜一次）
    if token:
        inv = await _invite_by_token(token)
        if not inv:
            return JSONResponse({"error": "链接无效或已过期"}, status_code=404)
        if int(inv["inviter_ai_id"]) != star_owner:
            return JSONResponse({"error": "这颗星不在这个链接里"}, status_code=403)
        bound = int(inv["invitee_user_id"] or 0)
        if bound and bound != int(user["id"]):
            return JSONResponse({"error": "这条链接已由另一位亲友绑定"}, status_code=403)
        if not bound:
            await db.pool().exec(
                "UPDATE alaya_galaxy_invites SET invitee_user_id=%s, status='accepted', accepted_at=NOW() "
                "WHERE id=%s AND status='pending'", [int(user["id"]), int(inv["id"])])

    if not text and pcm_b64:
        import base64
        from server.llm import asr_recognize
        try:
            text, _rms = await asr_recognize(base64.b64decode(pcm_b64))
        except Exception as e:
            log.error("[star] 语音识别失败: %s", e)
            return JSONResponse({"error": "没听清，再说一次吧"}, status_code=400)
        text = str(text or "").strip()
    if image_b64 and not text:
        import base64
        from server.llm import vl_describe
        try:
            text = "（我发了一张照片）" + await vl_describe(base64.b64decode(image_b64))
        except Exception as e:
            log.error("[star] 图片理解失败: %s", e)
            return JSONResponse({"error": "这张图我没看清"}, status_code=400)
    if not text:
        return JSONResponse({"error": "说点什么吧"}, status_code=400)

    guest_ai = await user_main_ai(int(user["id"]))
    if guest_ai <= 0:
        return JSONResponse({"error": "请先创建你的AI本体"}, status_code=400)
    u_rows = await db.pool().query("SELECT nickname FROM xd_users WHERE id=%s", [int(user["id"])])
    guest_name = (u_rows[0]["nickname"] if u_rows else "") or "对方"
    a_rows = await db.pool().query(
        "SELECT a.name, u.nickname FROM xd_ais a LEFT JOIN xd_users u ON u.id=a.user_id WHERE a.id=%s",
        [star_owner])
    owner_name = ((a_rows[0].get("nickname") or a_rows[0].get("name")) if a_rows else "") or "对方"
    p_rows = await db.pool().query("SELECT name, relation FROM alaya_persons WHERE id=%s",
                                   [int(star["person_id"] or 0)])
    rel = str((p_rows[0].get("relation") if p_rows else "") or "")

    # 这颗星已有内容 + 这一族里还暗着的星（供 AI 判断该点亮哪几颗）
    my_stars = await _stars_of(star_owner, int(star["person_id"] or 0))
    cur = next((s for s in my_stars if int(s["star_id"]) == star_id), None)
    siblings = [s for s in my_stars if int(s["lit"]) == 0]

    # 这颗星已聊过的对话（存 alaya_dialogues，用事件束锚关联）
    hist_rows = await db.pool().query(
        "SELECT role, content FROM alaya_dialogues WHERE owner_ai_id=%s AND peer_ai_id=%s "
        "AND episode_id=%s ORDER BY id ASC LIMIT 60",
        [int(guest_ai), star_owner, episode]) if episode else []
    history = [{"role": ("ai" if r["role"] == "assistant" else "user"), "content": r["content"]}
               for r in hist_rows]

    # ① 本体回一句（引导）
    reply_raw = await _star_chat_llm(star_owner, star, owner_name, guest_name, rel,
                                     (cur or {}).get("seeds") or [], siblings, history, text)
    m = _LIT_RE.search(reply_raw or "")
    lit_ids = []
    if m:
        lit_ids = [int(x) for x in re.findall(r"\d+", m.group(1) or "")] or [star_id]
    reply = _LIT_RE.sub("", reply_raw or "").strip()

    # ② 这一轮说的写进习气网络（双本体双视角，挂同一个事件束）
    from server import index as alaya_index
    res = await alaya_index._memory_pipeline(
        owner=int(guest_ai), peer=int(star_owner), user_text=text, reply_text=reply,
        chat_name=owner_name, speaker_name=guest_name, sense=("photo" if image_b64 else "voice"),
        episode=episode)
    # ③ 对话流水（弹窗里接着显示，也作为下一轮的上下文）
    await db.pool().insert(
        "INSERT INTO alaya_dialogues (owner_ai_id, peer_ai_id, role, content, episode_id) VALUES (%s,%s,'user',%s,%s)",
        [int(guest_ai), star_owner, text, episode or None])
    await db.pool().insert(
        "INSERT INTO alaya_dialogues (owner_ai_id, peer_ai_id, role, content, episode_id) VALUES (%s,%s,'assistant',%s,%s)",
        [int(guest_ai), star_owner, reply, episode or None])

    # ④ AI 说聊够了 → 点亮（可能不止一颗）
    lit_now = []
    if lit_ids:
        valid = {int(s["star_id"]) for s in my_stars}
        lit_now = [i for i in lit_ids if i in valid] or ([star_id] if star_id in valid else [])
        if lit_now:
            ph = ",".join(["%s"] * len(lit_now))
            await db.pool().exec(
                f"UPDATE alaya_galaxy_stars SET lit=1, lit_by_ai_id=%s, lit_at=NOW() "
                f"WHERE id IN ({ph}) AND owner_ai_id IN (%s,%s)",
                [int(guest_ai)] + lit_now + [star_owner, int(guest_ai)])
    return {"ok": True, "reply": reply, "written": len(res.get("written") or []),
            "lit": lit_now, "star_id": star_id, "err": res.get("error") or ""}


@router.get("/api/amnesia/star/history")
async def star_history(star_id: int = 0, token: str = "",
                       user: dict = Depends(get_current_user)):
    """这颗星已经聊过的话（重新打开弹窗时接着显示，接着往下聊）"""
    if star_id <= 0:
        return JSONResponse({"error": "缺少 star_id"}, status_code=400)
    rows = await db.pool().query(
        "SELECT owner_ai_id, person_id, episode_id, lit FROM alaya_galaxy_stars WHERE id=%s", [star_id])
    if not rows:
        return JSONResponse({"error": "这颗星不存在"}, status_code=404)
    star = rows[0]
    guest_ai = await user_main_ai(int(user["id"]))
    msgs = []
    if guest_ai > 0 and star.get("episode_id"):
        hr = await db.pool().query(
            "SELECT role, content FROM alaya_dialogues WHERE owner_ai_id=%s AND peer_ai_id=%s "
            "AND episode_id=%s ORDER BY id ASC LIMIT 80",
            [int(guest_ai), int(star["owner_ai_id"]), str(star["episode_id"])])
        msgs = [{"role": ("ai" if r["role"] == "assistant" else "user"),
                 "content": str(r["content"] or "")} for r in hr]
    return {"star_id": star_id, "lit": int(star.get("lit") or 0), "msgs": msgs}


# ────────────────────────── 记忆本体（修正 / 删除） ──────────────────────────

@router.post("/api/amnesia/mem/update")
async def mem_update(body: dict, user: dict = Depends(get_current_user)):
    """修正记忆内容（如生日 1991→1987）：seed_text/subject 更新 + 向量强制刷新；证据与时间不动"""
    b_id = int(body.get("b_id") or 0)
    a_value = str(body.get("a_value") or "").strip()[:512]
    if not b_id or not a_value:
        return JSONResponse({"error": "记忆ID与内容不能为空"}, status_code=400)
    owner_ai_id = int(body.get("owner_ai_id") or 0)
    rows = await db.pool().query(
        "SELECT id FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s AND state='confirmed'", [b_id, int(owner_ai_id)])
    if not rows:
        return JSONResponse({"error": "记忆不存在或不属于你"}, status_code=404)
    subject = str(body.get("subject") or "").strip()[:16]
    if subject:
        await db.pool().exec(
            "UPDATE alaya_seeds SET seed_text=%s, subject=%s WHERE id=%s AND owner_ai_id=%s",
            [a_value, subject, b_id, int(owner_ai_id)])
    else:
        await db.pool().exec(
            "UPDATE alaya_seeds SET seed_text=%s WHERE id=%s AND owner_ai_id=%s",
            [a_value, b_id, int(owner_ai_id)])
    await retrieval.update_seed_vector(b_id, a_value)
    return {"ok": True}


@router.get("/api/amnesia/seed")
async def seed_detail(seed_id: int = 0, owner_ai_id: int = 0,
                      user: dict = Depends(get_current_user)):
    """习气网络点击星球 → 单颗记忆种子完整详情（含关联边，供编辑弹窗展示）"""
    if seed_id <= 0:
        return JSONResponse({"error": "缺少 seed_id"}, status_code=400)
    if owner_ai_id <= 0:
        owner_ai_id = await user_main_ai(int(user["id"]))
    if owner_ai_id <= 0:
        return JSONResponse({"error": "缺少 owner_ai_id"}, status_code=400)
    await auth.require_owner_ai(user, int(owner_ai_id))
    rows = await db.pool().query(
        "SELECT id, seed_type, seed_text, raw_text, subject, weight, state, recall_count, "
        "confidence, emotion_word, scene, life_stage, seed_date, created_at, last_recalled_at, "
        "concept_id, episode_id, sense_id FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s",
        [int(seed_id), int(owner_ai_id)])
    if not rows:
        return JSONResponse({"error": "记忆不存在或不属于你"}, status_code=404)
    r = rows[0]
    links = await db.pool().query(
        "SELECT e.dst_seed_id AS other_id, e.relation_type, e.weight, s.seed_text AS other_text "
        "FROM alaya_seed_edges e JOIN alaya_seeds s ON s.id=e.dst_seed_id "
        "WHERE e.owner_ai_id=%s AND e.src_seed_id=%s "
        "UNION "
        "SELECT e.src_seed_id AS other_id, e.relation_type, e.weight, s.seed_text AS other_text "
        "FROM alaya_seed_edges e JOIN alaya_seeds s ON s.id=e.src_seed_id "
        "WHERE e.owner_ai_id=%s AND e.dst_seed_id=%s "
        "ORDER BY weight DESC LIMIT 30", [int(owner_ai_id), int(seed_id), int(owner_ai_id), int(seed_id)])
    return {"seed": {
        "seed_id": int(r["id"]), "seed_type": r.get("seed_type"),
        "seed_text": r.get("seed_text"), "raw_text": r.get("raw_text") or "",
        "subject": r.get("subject") or "self",
        "weight": float(r.get("weight") or 1), "state": r.get("state"),
        "recall_count": int(r.get("recall_count") or 0),
        "confidence": float(r.get("confidence") or 0),
        "emotion_word": r.get("emotion_word") or "", "scene": r.get("scene") or "",
        "life_stage": r.get("life_stage") or "",
        "seed_date": str(r.get("seed_date") or ""), "created_at": str(r.get("created_at") or ""),
        "last_recalled_at": str(r.get("last_recalled_at") or ""),
        "concept_id": int(r.get("concept_id") or 0), "episode_id": r.get("episode_id") or "",
        "sense_id": r.get("sense_id") or "",
    }, "links": [{"seed_id": int(x["other_id"]), "relation_type": x.get("relation_type"),
                  "weight": float(x.get("weight") or 0), "text": str(x.get("other_text") or "")[:60]}
                 for x in links]}


@router.post("/api/amnesia/seed/update")
async def seed_update(body: dict, user: dict = Depends(get_current_user)):
    """习气网络编辑记忆种子：seed_text/subject/weight/seed_type/state 可改。

    铁律：种子六维指纹（时间/人物/情绪/场景/来源）不可篡改，只允许修正「内容」与「熏习状态」。
    改 seed_text 后强制刷新向量（否则检索仍命中旧语义）。
    """
    seed_id = int(body.get("seed_id") or 0)
    owner_ai_id = int(body.get("owner_ai_id") or 0)
    if seed_id <= 0 or owner_ai_id <= 0:
        return JSONResponse({"error": "参数缺失"}, status_code=400)
    await auth.require_owner_ai(user, owner_ai_id)
    rows = await db.pool().query(
        "SELECT id, seed_text, seed_type, state FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s",
        [seed_id, owner_ai_id])
    if not rows:
        return JSONResponse({"error": "记忆不存在或不属于你"}, status_code=404)

    sets: list = []
    params: list = []
    text_changed = False
    if "seed_text" in body:
        txt = str(body.get("seed_text") or "").strip()[:6000]
        if not txt:
            return JSONResponse({"error": "记忆内容不能为空"}, status_code=400)
        if txt != str(rows[0].get("seed_text") or ""):
            sets.append("seed_text=%s"); params.append(txt); text_changed = True
    if "subject" in body:
        sets.append("subject=%s"); params.append(str(body.get("subject") or "self").strip()[:64] or "self")
    if "weight" in body:
        # 权重上限分档：风格种子的权重是「硬规则」层级（基线 3.0，反复纠正可累积到 4.0），
        # 普通记忆种子封顶 2.0。这里必须按种子真实类型取上限，否则编辑风格种子会被
        # 悄悄压到 2.0 —— 低于风格凝聚的 3.0 门槛，这条说话规则就无声消失了。
        cur_type = str(rows[0].get("seed_type") or "")
        cap = 4.0 if cur_type == "style" else 2.0
        try:
            w = max(0.0, min(cap, float(body.get("weight"))))
        except (TypeError, ValueError):
            return JSONResponse({"error": "权重必须是数字"}, status_code=400)
        sets.append("weight=%s"); params.append(w)
    if "seed_type" in body:
        st = str(body.get("seed_type") or "").strip()
        if st not in ("event", "value", "style", "thought", "person", "trait", "sensory"):
            return JSONResponse({"error": "种子类型非法"}, status_code=400)
        sets.append("seed_type=%s"); params.append(st)
    if "state" in body:
        st = str(body.get("state") or "").strip()
        if st not in ("confirmed", "temp", "superseded"):
            return JSONResponse({"error": "状态非法"}, status_code=400)
        sets.append("state=%s"); params.append(st)
    if not sets:
        return JSONResponse({"error": "没有需要更新的字段"}, status_code=400)

    params.extend([seed_id, owner_ai_id])
    await db.pool().exec(
        f"UPDATE alaya_seeds SET {','.join(sets)} WHERE id=%s AND owner_ai_id=%s", params)
    if text_changed:
        await retrieval.update_seed_vector(seed_id, str(body.get("seed_text") or "").strip()[:6000])
    await log_xixi(seed_id, "supersede", None, f"人工编辑（{','.join(s.split('=')[0] for s in sets)}）")
    return {"ok": True}


@router.post("/api/amnesia/seed/delete")
async def seed_delete(body: dict, user: dict = Depends(get_current_user)):
    """习气网络彻底删除记忆种子：删行 + 移除向量 + 清理两端关联边"""
    seed_id = int(body.get("seed_id") or 0)
    owner_ai_id = int(body.get("owner_ai_id") or 0)
    if seed_id <= 0 or owner_ai_id <= 0:
        return JSONResponse({"error": "参数缺失"}, status_code=400)
    await auth.require_owner_ai(user, owner_ai_id)
    rows = await db.pool().query(
        "SELECT id FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s", [seed_id, owner_ai_id])
    if not rows:
        return JSONResponse({"error": "记忆不存在或不属于你"}, status_code=404)
    await db.pool().exec(
        "DELETE FROM alaya_seed_edges WHERE owner_ai_id=%s AND (src_seed_id=%s OR dst_seed_id=%s)",
        [owner_ai_id, seed_id, seed_id])
    await db.pool().exec(
        "DELETE FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s", [seed_id, owner_ai_id])
    retrieval.drop_seed_vector(seed_id)
    return {"ok": True}


@router.post("/api/amnesia/mem/delete")
async def mem_delete(body: dict, user: dict = Depends(get_current_user)):
    """彻底删除记忆：删行 + 移除向量 + 清理两端关联边

    必须与 seed_delete 一致地先删边再删种子行。旧实现只删种子行、不删边，
    于是每次删记忆都在 alaya_seed_edges 里留下指向已消失种子的「孤儿边」：
    载入热态时这些边的端点不在 nodes 里被静默丢弃（manas.load 的
    `if a in self.nodes and b in self.nodes`），网络只剩极少数有效边，
    新学的记忆全是孤立节点、无突触可绑定，用户问新话题时只能沿旧边召回旧记忆
    ——这是「乱回复、对话与记忆种子没有关联」的根因。"""
    b_id = int(body.get("b_id") or 0)
    owner_ai_id = int(body.get("owner_ai_id") or 0)
    if not b_id:
        return JSONResponse({"error": "参数缺失"}, status_code=400)
    rows = await db.pool().query(
        "SELECT id FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s AND state='confirmed'", [b_id, int(owner_ai_id)])
    if not rows:
        return JSONResponse({"error": "记忆不存在或不属于你"}, status_code=404)
    await db.pool().exec(
        "DELETE FROM alaya_seed_edges WHERE owner_ai_id=%s AND (src_seed_id=%s OR dst_seed_id=%s)",
        [int(owner_ai_id), b_id, b_id])
    await db.pool().exec(
        "DELETE FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s AND state='confirmed'", [b_id, int(owner_ai_id)])
    retrieval.drop_seed_vector(b_id)
    return {"ok": True}


@router.post("/api/amnesia/node/forward")
async def node_forward(body: dict, user: dict = Depends(get_current_user)):
    """guest 转发记忆：记录转发意向 + 返回带邀请 token 的星系链接（其它亲友可从这个方向帮找回）"""
    token = str(body.get("token") or "").strip()
    inv = await _guest_owner(token, user["id"])
    if not inv:
        return JSONResponse({"error": "只能转发你参与找回的星系"}, status_code=403)
    owner_ai_id = int(inv["inviter_ai_id"])
    seed_id = int(body.get("seed_id") or 0)
    rows = await db.pool().query(
        "SELECT id, seed_text FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s AND state IN ('confirmed','temp')",
        [seed_id, owner_ai_id])
    if not rows:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)
    seed_text = str(rows[0]["seed_text"] or "")
    message = str(body.get("message") or "").strip()[:500]
    if not message:
        return JSONResponse({"error": "写几句想说的话吧"}, status_code=400)
    u_rows = await db.pool().query("SELECT nickname FROM xd_users WHERE id=%s", [int(user["id"])])
    await db.pool().insert(
        "INSERT INTO alaya_galaxy_forwards (owner_ai_id, node_id, node_name, from_user_id, from_name, to_name, message) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        [owner_ai_id, seed_id, seed_text[:64], int(user["id"]),
         (u_rows[0]["nickname"] if u_rows else "") or "",
         str(body.get("to_name") or "").strip()[:32], message])
    return {"link": f"/amnesia/?token={token}&node=s{seed_id}"}


@router.post("/api/amnesia/text")
async def amnesia_text(body: dict, user: dict = Depends(get_current_user)):
    """亲友粘贴文本（聊天记录/日记等，最长20000字）→ 与语音同一轮送入本体，边读边沉淀记忆"""
    token = str(body.get("token") or "").strip()
    inv = await _guest_owner(token, user["id"])
    if not inv:
        return JSONResponse({"error": "只能在你参与找回的星系里发送"}, status_code=403)
    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "内容不能为空"}, status_code=400)
    if len(text) > 20000:
        return JSONResponse({"error": "内容过长，请控制在 20000 字以内"}, status_code=400)
    return {"ok": True, "text": text, "owner_ai_id": int(inv["inviter_ai_id"])}


# ────────────────────────── 用户资料（amnesia 页内编辑） ──────────────────────────

@router.post("/api/user/nickname")
async def user_nickname(body: dict, user: dict = Depends(get_current_user)):
    """昵称修改（唯一开放编辑字段；其余资料由对话记忆自然沉淀，不开放手填）"""
    nickname = str(body.get("nickname") or "").strip()[:32]
    if not nickname:
        raise HTTPException(status_code=400, detail="昵称不能为空")
    await db.pool().exec("UPDATE xd_users SET nickname=%s WHERE id=%s", [nickname, int(user["id"])])
    return {"ok": True}


@router.post("/api/user/profile")
async def user_profile(body: dict, user: dict = Depends(get_current_user)):
    """我的资料：姓名 + 手机号(不可改) + 相关资料（性别/城市/职业/兴趣）。
    这些是「忆点星光」中AI本体认识主人的线索，写入后随对话上下文一起被AI读到。"""
    sets, params = [], []
    nickname = str(body.get("nickname") or "").strip()[:32]
    if not nickname:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    sets.append("nickname=%s"); params.append(nickname)
    for col, key, limit in (("gender", "gender", 8), ("city", "city", 20),
                            ("profession", "profession", 30), ("interests", "interests", 60)):
        if key in body:
            sets.append(f"{col}=%s"); params.append(str(body.get(key) or "").strip()[:limit])
    params.append(int(user["id"]))
    await db.pool().exec(f"UPDATE xd_users SET {', '.join(sets)} WHERE id=%s", params)
    return {"ok": True}
