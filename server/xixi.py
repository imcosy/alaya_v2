# xixi.py — 熏习算子（读即写：每一次交互都在塑造"我"是谁）
# 五类算子，全部只作用 weight/state/recall，全部落审计日志：
#   1. strengthen_cooccurrence  写时强化：同轮种子两两共现 → 种子边 + 概念边
#   2. reinforce_on_recall      读即写：检索命中的种子复现强化（置信度越高熏得越深）
#   3. decay_half_life          半衰衰减：长期未唤醒的种子按半衰期下沉（每日调度）
#   4. supersede                修正：新种子覆盖旧偏差（seeds.py 实现，此处引出）
#   5. consolidate              我识凝聚：现行熏种子，种子成熟为我（selfmodel.py 实现）
from __future__ import annotations

import logging
import math

from . import db
from .seeds import log_xixi

log = logging.getLogger("alaya.xixi")

CO_BOOST = 0.02          # 共现强化步长
CO_EDGE_INIT = 0.3       # 新建共现边初始权重
# 读即写基础步长：一次检索命中≈一次真实回忆，必须能显著抬升权重。
# 旧值 0.01 太小（召回一次仅 +0.005~0.01），而每轮对话都会新建同主题种子（初始 weight=1），
# 结果「反复被想起的老记忆」永远追不上「刚说出口的新流水」，权重排序被新鲜度霸占 →
# 常青召回退化成「最近说过什么」，AI 记不住真正的旧事（实测 2026-09-29 反馈「太蠢」的根因之一）。
RECALL_BOOST_BASE = 0.1   # 读即写基础步长
# 读即写边际递减半衰次数：同一颗种子被反复召回时，增量按 1/(1+recall_count/K) 收缩。
# 没有它，「越被召回 → 权重越高 → 越必被召回 → 越被注入 prompt」会形成正反馈死循环：
# 一颗偶然入网的种子（如别处听来的"老李"）会被越提越强、永远霸占 top12，
# 表现为 AI 陷进一个话题出不来（实测 2026-09-30 反馈「如意一直在说老李」的根因）。
# 真实回忆是有限资源：第 1 次想起最鲜明，第 50 次几乎不再加深——这才是记忆的物理规律。
RECALL_SATURATION = 4.0
HALF_LIFE_DAYS = 30       # 半衰期
# 召回惩罚：被过度召回的种子在每日半衰时额外降温，权重按 1/(1+recall_count/PENALTY_K) 打折。
# 半衰只看 last_recalled_at，而高频种子每轮都被召回、时间戳永远最新 →
# 半衰永远打不下去。必须把 recall_count 纳入衰减，过度曝光才有代价。
RECALL_PENALTY_K = 30.0

# ── 关联熏习（新种子入网 → 全网关联）──
# 铁律：记忆不是孤岛。一颗新种子进入习气网络时，必须与「语义上真正相关的既有种子」
# 建立突触（种子边），并让被关联的种子一起被熏习（共同加权）——这才叫「熏习」。
# 旧实现只在「同一轮写入的种子之间」建边（strengthen_cooccurrence 要求 len>=2），
# 单轮只写一颗种子的对话永远不产生任何边 → 91 颗种子只有 58 条边，网络稀疏散乱
# （实测 2026-09-30 反馈「大部分种子没有连线」的根因）。
ASSOC_SIM = 0.55         # 关联建边阈值：余弦 ≥ 此值视为「相关」（高于召回阈值 0.42，边要精不要滥）
ASSOC_TOPK = 5           # 每颗新种子最多关联的既有种子数（突触有度上限，防全连接糊成一团）
ASSOC_EDGE_INIT = 0.35   # 关联边初始权重
ASSOC_EDGE_BOOST = 0.06  # 关联边命中时的加权步长
ASSOC_SEED_BOOST = 0.05  # 被关联的既有种子共同熏习的权重增量（熏习：相关记忆一起被唤醒加固）
ASSOC_MIN_WEIGHT = 0.2   # 关联边低于此权重视为失效，不再计入


async def strengthen_cooccurrence(owner_ai_id: int, seed_ids: list):
    """写时强化：同一轮写入的种子两两共现 → 种子边（新建0.3/递增封顶1.0）+ 概念边"""
    uniq = [int(s) for s in dict.fromkeys(seed_ids) if s]
    if len(uniq) < 2:
        return
    pool = db.pool()
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            # 边方向必须统一（小 id 在前）：uk_seed_edge 唯一键含 src/dst，
            # 若不排序，同一对种子可能被写成 (a,b) 与 (b,a) 两条反向边，
            # 权重被拆成两份、簇内计数翻倍（associate_seeds / link_repeat 都是排序写法）。
            a, b = sorted((uniq[i], uniq[j]))
            await pool.exec(
                "INSERT IGNORE INTO alaya_seed_edges (owner_ai_id,src_seed_id,dst_seed_id,relation_type,weight) "
                "VALUES (%s,%s,%s,'co',%s)", [owner_ai_id, a, b, CO_EDGE_INIT])
            await pool.exec(
                "UPDATE alaya_seed_edges SET weight=LEAST(1.0,weight+%s) "
                "WHERE owner_ai_id=%s AND src_seed_id=%s AND dst_seed_id=%s AND relation_type='co'",
                [CO_BOOST, owner_ai_id, a, b])
            # 概念层共现（种子挂了概念节点的，节点间也建共现边）
            rows = await pool.query(
                "SELECT id, concept_id FROM alaya_seeds WHERE id IN (%s,%s) AND concept_id>0",
                [a, b])
            cids = [int(r["concept_id"]) for r in rows if r.get("concept_id")]
            for x in range(len(cids)):
                for y in range(x + 1, len(cids)):
                    await pool.exec(
                        "INSERT IGNORE INTO alaya_concept_edges (owner_ai_id,src,dst,relation_type,weight,auto_created) "
                        "VALUES (%s,%s,%s,'co',%s,1)", [owner_ai_id, cids[x], cids[y], CO_EDGE_INIT])
                    await pool.exec(
                        "UPDATE alaya_concept_edges SET weight=LEAST(1.0,weight+%s) "
                        "WHERE owner_ai_id=%s AND src=%s AND dst=%s AND relation_type='co'",
                        [CO_BOOST, owner_ai_id, cids[x], cids[y]])
    await log_xixi(None, "strengthen", CO_BOOST, f"共现强化 seeds={uniq[:8]}")
    log.info("[xixi] 共现强化 %s 颗种子两两建边", len(uniq))


async def associate_seeds(owner_ai_id: int, new_seed_ids: list) -> dict:
    """关联熏习：新种子入网 → 与全网语义相关的既有种子建立突触，并共同熏习。

    这是「记忆种子相互关联」的核心算法（实测 2026-09-30 明确要求）：
      1. 取每颗新种子的向量，在既有种子向量索引里做余弦近邻（≥ASSOC_SIM，取 topK）；
      2. 命中者与这颗新种子建 'co' 边（已存在则加权）——突触随每次相关经历加固；
      3. 被关联的既有种子一起被熏习（weight 微增）——「相关记忆一起被唤醒加固」，
         这正是"熏习"的本义：不是单颗种子变强，而是「一组相关的记忆」整体变强，
         从而在下一次决策「是否回答、如何回答」时作为一个整体浮现。

    与 strengthen_cooccurrence 的分工：
      - strengthen_cooccurrence：同一轮写入的种子之间（时间共现，横向）
      - associate_seeds：新种子与历史记忆之间（语义关联，纵向）——补上网络稀疏的根因
    """
    from . import retrieval
    ids = [int(s) for s in (new_seed_ids or []) if s]
    if not ids:
        return {"edges": 0, "linked_seeds": 0}
    pool = db.pool()
    new_set = set(ids)
    edges_made = 0
    linked = set()
    for sid in ids:
        vec = retrieval._vecs.get(int(sid))
        if vec is None:
            continue
        # 近邻检索：多取一些候选，再按 owner 过滤（向量索引是全库共享的）
        hits = retrieval._cosine_topk(vec, ASSOC_TOPK * 6)
        picked = 0
        for other_id, sim in hits:
            other_id = int(other_id)
            if other_id == sid or other_id in new_set or sim < ASSOC_SIM:
                continue
            rows = await pool.query(
                "SELECT id, weight FROM alaya_seeds WHERE id=%s AND owner_ai_id=%s "
                "AND state IN ('confirmed','temp')", [other_id, int(owner_ai_id)])
            if not rows:
                continue
            a, b = (sid, other_id) if sid < other_id else (other_id, sid)
            await pool.exec(
                "INSERT IGNORE INTO alaya_seed_edges "
                "(owner_ai_id,src_seed_id,dst_seed_id,relation_type,weight) "
                "VALUES (%s,%s,%s,'co',%s)", [int(owner_ai_id), a, b, ASSOC_EDGE_INIT])
            await pool.exec(
                "UPDATE alaya_seed_edges SET weight=LEAST(1.0,weight+%s) "
                "WHERE owner_ai_id=%s AND src_seed_id=%s AND dst_seed_id=%s AND relation_type='co'",
                [ASSOC_EDGE_BOOST, int(owner_ai_id), a, b])
            # 共同熏习：被关联的既有种子一起被唤醒加固（新种子自身由写时权重量定，不在此加）
            await pool.exec(
                "UPDATE alaya_seeds SET weight=LEAST(2.0,weight+%s) WHERE id=%s AND owner_ai_id=%s",
                [ASSOC_SEED_BOOST, other_id, int(owner_ai_id)])
            await log_xixi(other_id, "connect", ASSOC_SEED_BOOST,
                           f"关联熏习：与 #{sid} 相关（相似度{sim:.2f}）")
            edges_made += 1
            linked.add(other_id)
            picked += 1
            if picked >= ASSOC_TOPK:
                break
    if edges_made:
        log.info("[xixi] 关联熏习：新种子 %s 颗 → 建边 %s 条，共同加固 %s 颗既有记忆",
                 len(ids), edges_made, len(linked))
    return {"edges": edges_made, "linked_seeds": len(linked)}


async def reinforce_on_recall(seed_hits: list):
    """读即写：检索命中的种子复现强化。

    seed_hits: [{seed_id, confidence}]（confidence=命中相关度）
    强化步长 = RECALL_BOOST_BASE × 相关度 × 边际递减因子 —— 相关度越高熏得越深，
    但同一颗种子被反复召回时增量迅速收缩（真实回忆是有限资源，第 50 次想起几乎不再加深）。
    同时 recall_count+1、last_recalled_at=NOW()。

    边际递减是「老李死循环」的根治点：没有它，一次偶然入网的种子会因
    「越召回→权重越高→越必被召回→越注入 prompt」的正反馈永远霸榜，AI 出不来。
    """
    pool = db.pool()
    for h in seed_hits:
        sid = int(h.get("seed_id") or 0)
        rel = max(0.0, min(1.0, float(h.get("confidence") or 0.5)))
        if not sid:
            continue
        rows = await pool.query(
            "SELECT recall_count FROM alaya_seeds WHERE id=%s AND state IN ('confirmed','temp')", [sid])
        if not rows:
            continue
        rc = int(rows[0].get("recall_count") or 0)
        delta = round(RECALL_BOOST_BASE * (0.5 + rel) / (1.0 + rc / RECALL_SATURATION), 5)
        await pool.exec(
            "UPDATE alaya_seeds SET weight=LEAST(2.0,weight+%s), recall_count=recall_count+1, "
            "last_recalled_at=NOW() WHERE id=%s AND state IN ('confirmed','temp')", [delta, sid])
        await log_xixi(sid, "recall", delta, f"检索命中 rel={rel:.2f} 第{rc + 1}次")
    if seed_hits:
        log.info("[xixi] 读即写：%s 颗种子被本轮对话复熏", len(seed_hits))


async def decay_half_life() -> int:
    """半衰衰减：长期未唤醒的种子 weight 按半衰期下沉（每日调度）。

    weight 期望值 = POW(0.5, 距上次唤醒天数/半衰期) × 召回惩罚 1/(1+recall_count/K)，只更新偏差>0.02的行；
    常被回忆的种子（last_recalled_at 新）几乎不衰减——越常用越稳固。
    但「过度曝光」必须付出代价：recall_count 越高，期望权重被压得越低，
    否则每轮都被召回的高频种子（时间戳永远最新）永远不受半衰约束，形成死循环。

    skill（对话基本技能：等待说完/话中回应/停顿控制/情绪标签音色）**豁免半衰**：
    它们是所有 AI 本体都应熟练掌握的通识能力，权重必须恒定，绝不能因"久未使用"而淡出
    ——否则话中回应等技能进不了主念集群，对话立刻变得僵硬难用。
    """
    pool = db.pool()
    n = await pool.exec(
        "UPDATE alaya_seeds SET weight=POW(0.5, TIMESTAMPDIFF('DAY', "
        "COALESCE(last_recalled_at, created_at), NOW()) / %s) / (1 + recall_count / %s) "
        "WHERE state='confirmed' AND seed_type<>'skill' AND ABS(weight - POW(0.5, TIMESTAMPDIFF('DAY', "
        "COALESCE(last_recalled_at, created_at), NOW()) / %s) / (1 + recall_count / %s)) > 0.02",
        [HALF_LIFE_DAYS, RECALL_PENALTY_K, HALF_LIFE_DAYS, RECALL_PENALTY_K])
    if n:
        await log_xixi(None, "decay", None, f"每日半衰 {n} 颗种子（半衰期{HALF_LIFE_DAYS}天·含召回惩罚）")
    log.info("[xixi] 半衰衰减：%s 颗种子下沉", n)
    return n


def decay_preview(days: int, half_life: int = HALF_LIFE_DAYS) -> float:
    """衰减预览（调试/审计用）：days 天未唤醒后的期望权重"""
    return math.pow(0.5, days / half_life)
