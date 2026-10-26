# organize.py — 记忆整理算子（AI 本体对自己的记忆做整理，模拟真人脑的运行原理）
#
# 铁律：整理绝不删记忆、绝不改种子指纹。只做三件事：
#   1. 散乱 → 凝聚：语义相近的散种子某公司一个概念节点（alaya_concepts），种子挂上 concept_id
#   2. 重复 → 合并：内容重复的种子，保留权重最高的一颗，其余 state=superseded（走修正链）
#   3. 冲突 → 修正：新事实与旧记忆矛盾，旧种子 supersede + corr 边（seeds.supersede 已有）
#
# 触发：每轮对话写回后由 AI 本体自主判断（_memory_pipeline 末尾调用）。
# 概念层五支 fact/habit/relation/emotion/ability 是概念的「自然属性」，由整理时按内容判定，
# 不是预设骨架——没有任何预置节点，网络完全由真实记忆生长出来。
from __future__ import annotations

import logging

from . import db
from .seeds import log_xixi

log = logging.getLogger("alaya.organize")

# 凝聚门槛：同一批种子至少 2 颗才值得成概念；单颗散种子不成概念
MIN_CLUSTER = 2
# 重复判定：向量余弦 ≥ 该值视为同一件事的重复表述
DUP_SIM = 0.93
# 每次整理最多处理的新种子数（只整理本轮新写入的，避免全量重算）
MAX_BATCH = 12

BRANCHES = ("fact", "habit", "relation", "emotion", "ability")

# 概念内建边阈值：同一概念下两颗种子的语义相似度 ≥ 此值才建突触。
# 概念只保证"大致同类"，突触必须由语义相似度决定——否则概念内全连接会把
# 无关记忆（同批次被 LLM 误聚的）连成噪声团，污染主念集群（"乱回复"的根因）。
CONCEPT_EDGE_SIM = 0.55


async def organize_after_turn(owner_ai_id: int, new_seed_ids: list, llm_chat_fn) -> dict:
    """一轮对话写回后的自主整理。

    new_seed_ids：本轮新写入的种子（只整理新种子与既有记忆的关系，不全量重算）。
    返回 {concepts_created, seeds_merged, seeds_linked}
    """
    ids = [int(s) for s in (new_seed_ids or []) if s]
    if not ids:
        return {"concepts_created": 0, "seeds_merged": 0, "seeds_linked": 0}

    pool = db.pool()
    rows = await pool.query(
        "SELECT id, seed_text, seed_type, subject, person_id, concept_id, weight, confidence, "
        "emotion_word, scene FROM alaya_seeds "
        "WHERE owner_ai_id=%s AND id IN (" + ",".join(["%s"] * len(ids)) + ") "
        "AND state IN ('confirmed','temp')",
        [int(owner_ai_id), *ids])
    if not rows:
        return {"concepts_created": 0, "seeds_merged": 0, "seeds_linked": 0}

    merged = await _merge_duplicates(owner_ai_id, rows)
    linked, created = await _consolidate_concepts(owner_ai_id, rows, llm_chat_fn)
    return {"concepts_created": created, "seeds_merged": merged, "seeds_linked": linked}


async def _merge_duplicates(owner_ai_id: int, rows: list) -> int:
    """重复合并：与既有记忆向量高度重合的新种子 → 并入旧的（旧的加权，新的 superseded）。

    只处理「新种子 vs 既有种子」的重复，不互删既有记忆。
    """
    from . import retrieval
    pool = db.pool()
    merged = 0
    for r in rows:
        sid = int(r["id"])
        if sid not in retrieval._vecs:
            continue
        import numpy as np
        q = retrieval._vecs[sid]
        hits = retrieval._cosine_topk(q, 5)
        for other_id, sim in hits:
            if other_id == sid or sim < DUP_SIM:
                continue
            other = await pool.query(
                "SELECT id, weight, state FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s",
                [int(other_id), int(owner_ai_id)])
            if not other or other[0]["state"] not in ("confirmed", "temp"):
                continue
            # 既有记忆加权（被反复确认），新种子并入并退出活跃集
            await pool.exec(
                "UPDATE alaya_seeds SET weight=LEAST(2.0,weight+0.1), recall_count=recall_count+1, "
                "last_recalled_at=NOW() WHERE id=%s", [int(other_id)])
            await pool.exec(
                "UPDATE alaya_seeds SET state='superseded', superseded_by=%s, weight=weight*0.5 "
                "WHERE id=%s AND state IN ('confirmed','temp')", [int(other_id), sid])
            await pool.insert(
                "INSERT IGNORE INTO alaya_seed_edges "
                "(owner_ai_id, src_seed_id, dst_seed_id, relation_type, weight) "
                "VALUES (%s,%s,%s,'corr',0.9)", [int(owner_ai_id), sid, int(other_id)])
            await log_xixi(sid, "supersede", -0.5, f"整理·重复并入 #{other_id}（相似度{sim:.2f}）")
            merged += 1
            break
    if merged:
        log.info("[organize] 重复合并 %s 颗", merged)
    return merged


async def _consolidate_concepts(owner_ai_id: int, rows: list, llm_chat_fn) -> tuple:
    """散乱凝聚：把语义相近的散种子某公司概念节点，种子挂 concept_id。

    用 LLM 判断这批种子该某公司哪些概念（概念名 + 五支归属），
    已有同名概念直接复用（uk_owner_name 唯一键），不重复建。
    """
    pool = db.pool()
    # 只处理未挂概念的种子（已挂的说明整理过）
    loose = [r for r in rows if not int(r.get("concept_id") or 0)]
    if len(loose) < MIN_CLUSTER:
        return 0, 0

    existing = await pool.query(
        "SELECT id, name, branch FROM alaya_concepts WHERE owner_ai_id=%s AND status='active'",
        [int(owner_ai_id)])
    existing_names = {r["name"]: r for r in existing}

    seed_lines = "\n".join(
        f"#{r['id']} [{r['seed_type']}] {str(r['seed_text'])[:70]}" for r in loose[:MAX_BATCH])
    exist_hint = "、".join(list(existing_names.keys())[:30]) or "（暂无）"
    prompt = (
        "你是记忆整理器。以下是这个AI本体刚写入的记忆种子，以及已有的概念节点。\n"
        f"【已有概念】{exist_hint}\n"
        f"【新种子】\n{seed_lines}\n"
        "任务：把语义相近、指向同一件事/同一个人的种子某公司概念节点。规则：\n"
        "1. 能归入已有概念的，直接复用那个概念名，不要另造新名；\n"
        "2. 确实无法归入的，才新建概念（名字精炼，如「饮食习惯」「陈总」「童年住所」）；\n"
        "3. 孤立的、无法与任何种子聚合的单颗种子，不要强行成概念；\n"
        "4. branch 只能是 fact事实|habit习惯|relation关系|emotion情绪|ability能力 之一。\n"
        "只输出JSON：{\"clusters\":[{\"concept\":\"概念名\",\"branch\":\"fact\",\"seed_ids\":[1,2]}]}"
    )
    out_text = await llm_chat_fn([{"role": "system", "content": prompt}], 0.3, 500)
    data = _safe_json(out_text) or {}
    clusters = data.get("clusters") or []
    if not clusters:
        return 0, 0

    valid_ids = {int(r["id"]) for r in loose}
    linked = 0
    created = 0
    for c in clusters:
        name = str(c.get("concept") or "").strip()[:64]
        branch = str(c.get("branch") or "fact").strip()
        if branch not in BRANCHES:
            branch = "fact"
        sids = [int(x) for x in (c.get("seed_ids") or []) if int(x) in valid_ids]
        if not name or len(sids) < MIN_CLUSTER:
            continue
        row = existing_names.get(name)
        if row:
            cid = int(row["id"])
        else:
            cid = await pool.insert(
                "INSERT INTO alaya_concepts (owner_ai_id, parent_id, name, branch, weight, status) "
                "VALUES (%s,0,%s,%s,1.0,'active')", [int(owner_ai_id), name, branch])
            existing_names[name] = {"id": cid, "name": name, "branch": branch}
            created += 1
            await log_xixi(None, "consolidate", None, f"整理·凝聚新概念「{name}」({branch})", concept_id=cid)
        for sid in sids:
            await pool.exec(
                "UPDATE alaya_seeds SET concept_id=%s WHERE id=%s AND owner_ai_id=%s AND concept_id=0",
                [cid, sid, int(owner_ai_id)])
            linked += 1
        # 概念内种子建边：只连「语义真正相近」的对，绝不两两全连接。
        # 旧实现把同一概念内所有种子无脑两两连边（weight 0.3），而 LLM 凝聚时
        # 常把不相关的种子塞进同一概念（如「老陈童年经历」混进打招呼/重复开场白，
        # 批次里还有「形象照」「奶奶去世」）——于是这些语义无关的记忆被强行连边，
        # 主念集群按 CLUSTER_EDGE 归并时把无关记忆全拉进上下文，表现为"乱回复"。
        # 概念只说明"大致同类"，突触必须由语义相似度决定：相似度即边权。
        from . import retrieval
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                a, b = sorted((sids[i], sids[j]))
                va, vb = retrieval._vecs.get(int(a)), retrieval._vecs.get(int(b))
                if va is None or vb is None:
                    continue
                sim = float(va @ vb)
                if sim < CONCEPT_EDGE_SIM:
                    continue
                await pool.exec(
                    "INSERT IGNORE INTO alaya_seed_edges "
                    "(owner_ai_id, src_seed_id, dst_seed_id, relation_type, weight) "
                    "VALUES (%s,%s,%s,'co',%s)", [int(owner_ai_id), a, b, round(sim, 4)])
    if created or linked:
        log.info("[organize] 凝聚：新建概念 %s 个，挂载种子 %s 颗", created, linked)
    return linked, created


def _safe_json(text: str) -> dict | None:
    """LLM 输出容错解析（剥 ```json 包裹/截取首个大括号块）"""
    import json
    if not text:
        return None
    t = str(text).strip()
    if t.startswith("```"):
        t = t.strip("`").lstrip("json").strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except ValueError:
                    return None
    return None
