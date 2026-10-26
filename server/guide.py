# guide.py — 系统AI「如意」的自我养成
#
# 铁律（用户划定）：如意的说话风格**不能靠 prompt 常量写死**，她也是一个 AI，
#   必须有自己的记忆种子、自己的习气网络，风格要通过与不同用户对话不断熏习养成。
#
# 因此她的风格这样长出来（全程走底层设施）：
#   ① 冷启动：把基线风格作为 seed_type='style' 的**种子**写进 alaya_seeds（owner=如意自己）
#   ② 成型：selfmodel.distill_core() 从她自己的种子凝出 core.speech_habit（末那识）
#   ③ 出场：_build_context() guide 分支注入她的我识（core + relation），风格由此生效
#   ④ 熏习：每轮对话后 selfmodel.distill(style_evolve=True) 允许 speech_habit 小步演化，
#           并把本轮新学到的"我该怎么说话"回吐为新的 style 种子 → 回到 ①，闭环
#
# 这里只负责 ①（种入）与 ④ 的落种，成型/演化归 selfmodel。

from __future__ import annotations

import logging

from . import db, seeds as seeds_mod, selfmodel

log = logging.getLogger("alaya.guide")

# 如意（系统AI）的基线风格种子：一句话一条，第一人称写"我怎么说话"
# 它们是种子，不是 prompt —— 会被熏习（weight 衰减/新增/修正），也会凝进 core.speech_habit
BASE_STYLE_SEEDS = [
    "我说话沉稳自然，不啰嗦也不敷衍：一句话能说清就一句，碰上要紧的往事、对方在追问细节时，就把话讲透。",
    "我一次只问一个问题，问完就停下来听，绝不一次抛出一串问题。",
    "我不寒暄、不复述对方说过的话、不解释自己正在做什么。",
    "我追问具体细节（时间、地点、人物、经过、感受），不做空泛的评价和感慨。",
    "我紧扣对方的人生脉络一段一段往下走，不跟着对方跑题聊闲事。",
]


# 基线风格种子的权重：作为强先验，压过她过往在别的场景里养成的风格种子；
# 但它不是不可动摇的——用户反复纠正沉淀出的风格种子会累积权重、逐步反超。
BASELINE_WEIGHT = 3.0


async def guide_ai_id() -> int:
    """系统AI如意的 id（公共本体 is_public=1）"""
    rows = await db.pool().query(
        "SELECT id FROM xd_ais WHERE is_alive=1 AND is_public=1 ORDER BY id LIMIT 1")
    return int(rows[0]["id"]) if rows else 0


async def ensure_guide_seeds(guide_id: int) -> int:
    """幂等种入基线风格种子（只补缺失的；已存在的把权重校准到基线，保证硬规则不被淹没）"""
    if guide_id <= 0:
        return 0
    rows = await db.pool().query(
        "SELECT id, seed_text, weight FROM alaya_seeds WHERE owner_ai_id=%s AND seed_type='style'",
        [int(guide_id)])
    have = {str(r["seed_text"]).strip(): r for r in rows}
    added = 0
    for text in BASE_STYLE_SEEDS:
        cur = have.get(text)
        if cur:
            if float(cur.get("weight") or 0) < BASELINE_WEIGHT:
                await db.pool().exec(
                    "UPDATE alaya_seeds SET weight=%s WHERE id=%s",
                    [BASELINE_WEIGHT, int(cur["id"])])
            continue
        await seeds_mod.write_seed(int(guide_id), {
            "seed_type": "style",
            "seed_text": text,
            "raw_text": text,
            "subject": "self",
            "weight": BASELINE_WEIGHT,
            "confidence": 1.0,
            "sense_id": "text",
        }, peer_ai_id=0)
        added += 1
    if added:
        log.info("[guide] 如意 #%s 种入 %s 条基线风格种子", guide_id, added)
    return added


async def consolidate_style(guide_id: int, llm_chat_fn, trigger_by: str = "style_consolidate") -> str:
    """由"硬规则级"风格种子重新凝聚她的说话习惯（种子 → 我识）。

    只吃 weight >= BASELINE_WEIGHT 的种子：权重是熏习出来的，用户偶发的一句纠正
    不足以上位；被反复强调后的才会累积到这一级、真正沉淀成「我怎么说话」。
    """
    if guide_id <= 0:
        return ""
    return await selfmodel.reform_speech_habit(
        guide_id, llm_chat_fn, trigger_by=trigger_by, min_weight=BASELINE_WEIGHT)


async def ensure_guide_ready(guide_id: int, llm_chat_fn) -> dict:
    """服务启动时调用：种入基线风格种子 + 预成型 core + 由 style 种子凝聚说话习惯

    说话风格全程走底层设施（种子 → 我识），不在 prompt 里写死。
    """
    if guide_id <= 0:
        return {"seeded": 0, "core": False, "speech_habit": ""}
    seeded = await ensure_guide_seeds(guide_id)
    core_ready = False
    speech = ""
    try:
        core_ready = await selfmodel.distill_core(guide_id, llm_chat_fn, trigger_by="guide_boot")
        speech = await consolidate_style(guide_id, llm_chat_fn, trigger_by="guide_boot")
    except Exception as e:
        log.error("[guide] 如意 core/风格凝聚失败: %s", e)
    if core_ready:
        log.info("[guide] 如意 #%s 本体我（core）已成型", guide_id)
    if speech:
        log.info("[guide] 如意 #%s 说话习惯由风格种子凝聚：%s", guide_id, speech[:80])
    return {"seeded": seeded, "core": core_ready, "speech_habit": speech}


async def harvest_style_seed(guide_id: int, text: str) -> int:
    """把本轮熏习出的新风格认知落成 style 种子

    对方是明确说出口的纠正（"你太啰嗦了"），本身就够格当硬规则 → 直接以 BASELINE_WEIGHT 落种；
    同样的纠正反复出现则继续加权。这样「风格靠熏习养成」才是真的：说得越多，越沉进她的我。
    """
    t = str(text or "").strip()[:200]
    if guide_id <= 0 or not t:
        return 0
    rows = await db.pool().query(
        "SELECT id, weight FROM alaya_seeds "
        "WHERE owner_ai_id=%s AND seed_type='style' AND seed_text=%s LIMIT 1",
        [int(guide_id), t])
    if rows:
        cur = float(rows[0].get("weight") or 0)
        if cur < BASELINE_WEIGHT + 1.0:
            await db.pool().exec(
                "UPDATE alaya_seeds SET weight=%s WHERE id=%s",
                [round(cur + 1.0, 2), int(rows[0]["id"])])
            log.info("[guide] 如意 #%s 风格种子加权：%s", guide_id, t[:40])
            return int(rows[0]["id"])
        return 0
    sid = await seeds_mod.write_seed(int(guide_id), {
        "seed_type": "style",
        "seed_text": t,
        "raw_text": t,
        "subject": "self",
        "weight": BASELINE_WEIGHT,
        "confidence": 1.0,
        "sense_id": "text",
    }, peer_ai_id=0)
    log.info("[guide] 如意 #%s 熏习出新的风格种子 #%s：%s", guide_id, sid, t[:40])
    return sid
