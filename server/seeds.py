# seeds.py — 第八识·阿赖耶识：种子容器
# 种子 = 统一容器 alaya_seeds（六维指纹：语义/时间/人物/情绪/场景/来源）
# 铁律：种子指纹不可变——修正走新种子 + corr 关联 + 旧种子 state=superseded
#       熏习算子只动 weight/state/recall，永不改 seed_text/raw_text
from __future__ import annotations

import logging
from datetime import datetime

from . import db
from .life_stages import STAGE_KEYS as LIFE_STAGE_KEYS

log = logging.getLogger("alaya.seeds")

# 场景枚举（场景维，可延展）
SCENES = ("家庭", "职场", "社交", "医疗", "出行", "其他")

# 临时记忆类型（现量窗口）：与长期认知网络**严格隔离**的一类种子。
#   sensory = 六识现量：当下真实讲过的一句（用户说的、本体说的）。它是"这一段会话"的
#   原始元信息，尚未结生——网络上只作"境"活 9 秒，库里留到结生后清理。
# 隔离规则（三处，缺一即污染长期记忆）：①不进末那识习气网络 ②不建向量索引
# ③不参与召回。生命周期由「结生」主动终结；会话被意外中断的，由 expire_at 到期清理。
TRANSIENT_TYPES = ("sensory",)


async def ensure_star(owner_ai_id: int, person_id: int, episode_id: str,
                      source: str = "auto") -> int:
    """忆点星光：确保某事件束(episode)在某人物名下有一颗星（已存在则复用）。

    一颗星＝一个事件束：星的内容＝该 episode 下的种子（主人记得的＋亲友补的）。
    主人讲述时由写回链路自动成星（source=auto）；主人双击星空空白手动加星见 amnesia.star_add。
    星不显字（事件不需要标题），只有亮/暗：暗＝还没收到亲友补充。
    """
    if not person_id or not episode_id:
        return 0
    rows = await db.pool().query(
        "SELECT id FROM alaya_galaxy_stars WHERE owner_ai_id=%s AND episode_id=%s LIMIT 1",
        [int(owner_ai_id), str(episode_id)])
    if rows:
        return int(rows[0]["id"])
    return int(await db.pool().insert(
        "INSERT INTO alaya_galaxy_stars (owner_ai_id, person_id, episode_id, source) VALUES (%s,%s,%s,%s)",
        [int(owner_ai_id), int(person_id), str(episode_id), str(source or "auto")]))


async def link_repeat(owner_ai_id: int, new_seed_id: int, twin_seed_id: int,
                      sim: float) -> None:
    """写入仲裁·同一件事识别：新记录与已有记忆是「同一件事的重复讲述」时建 corr 边。

    为什么需要它：同一件事会在不同轮次被讲很多遍，每次表述不同（"守在身边直到去世"
    /"赶回去几天"），向量未必互相撞得上，于是各自孤立地躺在库里——回忆时想不起全貌，
    还会被当成两件不同的事分别复述。
    建边之后，任意一条被命中，另一条都会经 expand_by_edges 一起浮起（一族一起亮），
    从根上解决"明明说过却想不起来 / 又重复记"。
    """
    if not new_seed_id or not twin_seed_id or new_seed_id == twin_seed_id:
        return
    a, b = int(new_seed_id), int(twin_seed_id)
    src, dst = (a, b) if a < b else (b, a)
    await db.pool().exec(
        "INSERT IGNORE INTO alaya_seed_edges (owner_ai_id,src_seed_id,dst_seed_id,relation_type,weight) "
        "VALUES (%s,%s,%s,'corr',%s)", [int(owner_ai_id), src, dst, float(min(1.0, sim))])
    await log_xixi(new_seed_id, "link_repeat", None,
                   f"与已有记忆 {twin_seed_id} 判为同一件事（sim={sim:.3f}）")


async def write_seed(owner_ai_id: int, seed: dict, peer_ai_id: int = 0) -> int:
    """写入一颗种子（写入仲裁后的最终落库）。

    seed 必备字段：seed_type/seed_text；可选：raw_text/subject/person_id/concept_id/
    seed_date/time_fuzzy/emotion_tone_id/emotion_word/scene/life_stage/weight/confidence/evidence/state
    返回 seed_id。
    """
    cols = ["seed_type", "seed_text", "owner_ai_id"]
    vals: list = [str(seed.get("seed_type") or "value"), str(seed.get("seed_text") or "").strip(),
                  int(owner_ai_id)]
    mapping = [
        ("raw_text", lambda v: str(v or "")),
        ("peer_ai_id", lambda v: int(peer_ai_id or 0)),
        ("subject", lambda v: str(v or "self")),
        ("person_id", lambda v: int(v or 0)),
        ("concept_id", lambda v: int(v or 0)),
        ("time_fuzzy", lambda v: (str(v).strip() or None) if v else None),
        ("emotion_tone_id", lambda v: int(v) if v else None),
        ("emotion_word", lambda v: (str(v).strip() or None) if v else None),
        ("scene", lambda v: str(v) if v in SCENES else None),
        ("life_stage", lambda v: str(v) if v in LIFE_STAGE_KEYS else None),
        ("weight", lambda v: max(0.0, float(v)) if v else None),
        ("confidence", lambda v: max(0.0, min(1.0, float(v or 0.9)))),
        ("evidence", lambda v: str(v or "")[:2000] if v else None),
        ("state", lambda v: str(v or "confirmed")),
        ("episode_id", lambda v: (str(v).strip() or None) if v else None),
        ("sense_id", lambda v: str(v or "voice") if str(v or "") in ("voice", "text", "photo") else None),
    ]
    for k, fn in mapping:
        v = fn(seed.get(k))
        if v is None and k in ("raw_text", "time_fuzzy", "emotion_tone_id", "emotion_word", "scene", "life_stage", "weight", "evidence", "sense_id", "episode_id"):
            continue  # 可空列缺省跳过（用库默认 NULL）
        if k == "raw_text" and v == "":
            continue
        cols.append(k)
        vals.append(v)
    # seed_date 特殊处理（date 对象或 YYYY-MM-DD 字符串）；空日期默认当天——记忆是
    # 「什么时候知道的」，归属对话日符合日记语义，杜绝大量"无日期"种子
    sd = str(seed.get("seed_date") or "").strip()[:10]
    if not sd or sd == "None":
        sd = datetime.now().strftime("%Y-%m-%d")
    cols.append("seed_date")
    vals.append(sd)
    ph = ",".join(["%s"] * len(cols))
    sql = f"INSERT INTO alaya_seeds ({','.join(cols)}) VALUES ({ph})"
    seed_id = await db.pool().insert(sql, vals)
    log.info("[seeds] 写入 #%s type=%s owner=%s text=%s",
             seed_id, seed.get("seed_type"), owner_ai_id, str(seed.get("seed_text"))[:40])
    return seed_id


async def supersede(old_seed_id: int, new_seed_id: int, reason: str = "") -> bool:
    """修正：旧种子标记 superseded（指纹不可变，修正链可追溯）"""
    n = await db.pool().exec(
        "UPDATE alaya_seeds SET state='superseded', superseded_by=%s, weight=weight*0.5 "
        "WHERE id=%s AND state='confirmed'", [new_seed_id, old_seed_id])
    if n:
        await db.pool().insert(
            "INSERT INTO alaya_seed_edges (owner_ai_id, src_seed_id, dst_seed_id, relation_type, weight) "
            "SELECT owner_ai_id, id, %s, 'corr', 0.9 FROM alaya_seeds WHERE id=%s",
            [new_seed_id, old_seed_id])
        await log_xixi(old_seed_id, "supersede", -0.5, reason or "被新种子修正")
    return bool(n)


async def get_seed(seed_id: int) -> dict | None:
    rows = await db.pool().query("SELECT * FROM alaya_seeds WHERE id=%s", [seed_id])
    return rows[0] if rows else None


async def active_seeds(owner_ai_id: int, peer_ai_id: int = None, seed_type: str = None,
                       limit: int = 500) -> list:
    """主体的活跃长期种子（confirmed/temp，**不含临时记忆**），peer 过滤可选（0 表示不限）

    临时记忆（现量窗口）不是"记住了什么"，而是"此刻手里攒着什么、还没结生"——
    混进种子库只会让记忆看得一团乱（实测：库里躺着"对，对，对。"这种 ASR 噪声碎片）。
    它另有查看入口：`working.window()` / `/api/working`。
    """
    ph = ",".join(["%s"] * len(TRANSIENT_TYPES))
    sql = (f"SELECT * FROM alaya_seeds WHERE owner_ai_id=%s AND state IN ('confirmed','temp') "
           f"AND seed_type NOT IN ({ph})")
    params: list = [int(owner_ai_id), *TRANSIENT_TYPES]
    if peer_ai_id is not None and int(peer_ai_id) > 0:
        sql += " AND (peer_ai_id=%s OR peer_ai_id=0)"
        params.append(int(peer_ai_id))
    if seed_type:
        sql += " AND seed_type=%s"
        params.append(seed_type)
    sql += " ORDER BY weight DESC, id DESC LIMIT %s"
    params.append(int(limit))
    return await db.pool().query(sql, params)


async def log_xixi(seed_id: int | None, operator: str, delta: float | None, reason: str = "",
                   concept_id: int | None = None):
    """熏习审计：每一笔权重变化可追溯"""
    await db.pool().insert(
        "INSERT INTO alaya_xixi_log (seed_id, concept_id, operator, delta, reason) VALUES (%s,%s,%s,%s,%s)",
        [seed_id, concept_id, operator, delta, reason[:250]])


def rel_days(seed_date) -> int | None:
    """种子日期距今天数（时间维标注用，防"昨天"大乌龙）"""
    if not seed_date:
        return None
    try:
        d = datetime.strptime(str(seed_date)[:10], "%Y-%m-%d")
        return (datetime.now() - d).days
    except (ValueError, TypeError):
        return None


def fmt_date_annotated(seed_date) -> str:
    """种子日期 → 绝对日期 + 相对天数标注（注入 prompt 时防相对时间漂移；负数=未来日期）"""
    s = str(seed_date or "").strip()[:10]
    if not s or s == "None":
        return "近日"
    days = rel_days(seed_date)
    if days is None:
        return s
    if days == -1:
        return f"{s}（明天）"
    if days == -2:
        return f"{s}（后天）"
    if days < 0:
        return f"{s}（{-days}天后）"
    if days <= 0:
        return f"{s}（今天）"
    if days == 1:
        return f"{s}（昨天）"
    if days == 2:
        return f"{s}（前天）"
    if days < 30:
        return f"{s}（{days}天前）"
    if days < 365:
        return f"{s}（约{days // 30}个月前）"
    return f"{s}（约{days // 365}年前）"
