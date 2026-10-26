# working.py — 现量窗口（会话工作记忆）与结生算子
#
# 人脑对应（本模块存在的理由）：
#   工作记忆（前额叶，数秒~数分钟）      ≈ 现量窗口：这一段真实讲过的话，尚未成为长期记忆
#   系统巩固（睡眠期海马→新皮层重放）    ≈ 结生：把窗口一次看全，提炼成长期记忆种子
#   突显性标记（"这件事重要"当场打标）   ≈ 突显门控：重要的信息元不等窗口结束，当场结生
#
# 为什么不逐轮就刻成长期记忆（旧行为）：
#   同一件事会被反复讲（"我陪了她最后一个晚上"，隔几轮又说"我父亲赶回去几天"），
#   逐轮刻就刻出一堆互不相识的碎片——回忆时拼不出全貌，还会被当成几件事分别复述。
#   窗口先把"这一段"攒住，结生时一次看全，同一件事自然只出一条完整的。
#
# 载体：alaya_seeds 里的 sensory（临时记忆种子），以 (owner_ai_id, peer_ai_id) 分窗。
#   窗口不需要另存标识、也不需要内存态——"尚未结生"本身就是窗口的边界。
#   因此断线、重启、超时都不会丢：内容一直在库里，下次该对话接着攒。
#   铁律：临时记忆不进习气网络、不建向量、不参与召回（见 seeds.TRANSIENT_TYPES）。
from __future__ import annotations

import logging
from datetime import datetime

from . import db

log = logging.getLogger("alaya.working")

# 窗口寿命：一条现量留档 30 分钟没有新输入，即视为"这一段已经过去"，由到期清理器清掉。
# 结生成功的早被主动清了；留下来的说明会话是被意外中断的——临时记忆自然消退，与人脑一致。
WINDOW_TTL_SEC = 1800
# 结生节律：静默 90 秒即结生。远短于对话上文窗口（16 轮），保证"刚说过的话"最迟
# 90 秒后已进长期记忆，不会再出现"明明说过、召回命不中、又回头问一遍"。
CONDENSE_IDLE_SEC = 90
# 最少条目：一轮对话约 1~2 条（对方一句 + 本体一句）。太少没有整合价值。
CONDENSE_MIN_ITEMS = 3
# 窗口容量：攒够这么多条先结生一轮，避免一次喂给整理意识的原文过长、要点被稀释。
CONDENSE_MAX_ITEMS = 40
# 突显门控：高唤醒条目达到此数且窗口够长，就当场结生——人不会等一段对话结束才记住
# 重要的事，情绪当场就把这件事标出来了。
SALIENT_HOT_ITEMS = 3
# 高唤醒情绪词：情绪是"这件事重要"的第一信号
HOT_EMOTIONS = ("伤心", "难过", "痛苦", "痛哭", "生气", "愤怒", "委屈", "担心", "焦虑",
                "害怕", "恐惧", "后悔", "愧疚", "感动", "开心", "幸福", "激动")

# 现量条目的角色（写在 evidence 上）：结生只看"真正讲过的话"。
# 聆听态中途的 ASR 片段（MARK_LISTEN）不进窗口——它马上会被 stop 时提交的完整句覆盖，
# 重复进窗口只会让整理意识看到两份残缺的同一句。
MARK_SPEAKER = "现量·对方"      # 对方说的一句（完整）
MARK_SELF = "现量·本体"         # 本体自己回的一句
MARK_LISTEN = "现量·聆听"       # 聆听中途的片段 / 没听清时的疑问（不入窗口）


async def window(owner_ai_id: int, peer_ai_id: int) -> list[dict]:
    """取该对话的现量窗口：尚未结生的临时记忆，按时间序（旧→新）"""
    ph = ",".join(["%s"] * 2)
    return await db.pool().query(
        "SELECT id, seed_text, raw_text, sense_id, emotion_word, scene, created_at "
        "FROM alaya_seeds WHERE owner_ai_id=%s AND peer_ai_id=%s "
        f"AND seed_type='sensory' AND state='temp' AND evidence IN ({ph}) ORDER BY id",
        [int(owner_ai_id), int(peer_ai_id or 0), MARK_SPEAKER, MARK_SELF])


def is_hot(item: dict) -> bool:
    """高唤醒：这条带着强烈情绪——情绪是"值得记住"的第一信号"""
    return str(item.get("emotion_word") or "").strip() in HOT_EMOTIONS


async def should_condense(owner_ai_id: int, peer_ai_id: int) -> str:
    """该不该结生。返回原因（空串=不结生）：满载 / 静默 / 突显 三种情形。

    静默时长取自窗口最后一条的时间戳——"多久没人说话了"本来就写在窗口里，
    不需要会话侧另持一份状态。
    """
    items = await window(owner_ai_id, peer_ai_id)
    n = len(items)
    if n < CONDENSE_MIN_ITEMS:
        return ""
    if n >= CONDENSE_MAX_ITEMS:
        return f"窗口满载（{n}条）"
    last = items[-1].get("created_at")
    idle = (datetime.now() - last).total_seconds() if last else 0.0
    if idle >= CONDENSE_IDLE_SEC:
        return f"静默 {int(idle)} 秒"
    hot = sum(1 for i in items if is_hot(i))
    if hot >= SALIENT_HOT_ITEMS:
        return f"突显门控（{hot} 条高唤醒）"
    return ""


def dialogue_text(items: list[dict], limit: int = 6000) -> str:
    """窗口 → 结生用的对话原文（每行一句，形如「老陈：我爸叫张建国」）"""
    return "\n".join(str(i.get("seed_text") or "") for i in items)[:limit]


def sense_of(items: list[dict]) -> str:
    """这一段对话的主要感官通道：结生把多条合成一条，得决定这条记忆记哪个通道"""
    cnt: dict[str, int] = {}
    for i in items:
        s = str(i.get("sense_id") or "")
        if s in ("voice", "text", "photo"):
            cnt[s] = cnt.get(s, 0) + 1
    return max(cnt, key=lambda k: cnt[k]) if cnt else "voice"


async def drop_first(owner_ai_id: int, peer_ai_id: int, n: int) -> int:
    """只清理窗口里最早的前 n 条（满载结生时用：一次结生只消费这一段，不误清后写入的）"""
    if n <= 0:
        return 0
    ph = ",".join(["%s"] * 2)
    rows = await db.pool().query(
        "SELECT id FROM alaya_seeds WHERE owner_ai_id=%s AND peer_ai_id=%s "
        f"AND seed_type='sensory' AND state='temp' AND evidence IN ({ph}) ORDER BY id LIMIT %s",
        [int(owner_ai_id), int(peer_ai_id or 0), MARK_SPEAKER, MARK_SELF, int(n)])
    if not rows:
        return 0
    ids = [int(r["id"]) for r in rows]
    ph_ids = ",".join(["%s"] * len(ids))
    cnt = await db.pool().exec(f"DELETE FROM alaya_seeds WHERE id IN ({ph_ids})", ids)
    if cnt:
        log.info("[working] 结生清理 本体#%s × #%s 临时记忆 %s 条", owner_ai_id, peer_ai_id, cnt)
    return int(cnt or 0)


async def expired_gc() -> int:
    """到期清理：临时记忆都有寿命（WINDOW_TTL_SEC）。

    结生是主路径；这里负责的是"会话被意外中断、没能走到结生"的残留——
    不清的话，临时记忆会永久堆积在阿赖耶识里（实测过：过期感官碎片 16 条长期滞留，
    还被补进向量索引当成往事召回）。
    """
    return int(await db.pool().exec(
        "DELETE FROM alaya_seeds WHERE seed_type='sensory' AND state='temp' "
        "AND expire_at IS NOT NULL AND expire_at < NOW()") or 0)
