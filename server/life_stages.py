# life_stages.py — 人生脉络框架（叙事提纲）
#
# 定位：这是「忆点星光」给用户的人生叙事框架 —— 如意访谈时的路线图，
#       也是用户发起寻忆时挂靠的坐标。**它不是记忆**：不落库、不生成种子、
#       不参与习气网络。它只做两件事：
#         ① 用年龄裁剪出"这个人大概走到了哪些人生阶段"（30 岁不必聊退休）
#         ② 给每个阶段配话题提纲，让如意的追问有方向，不至于"说到哪算哪"
#
# 与底层设施的关系（铁律）：
#   阶段的"进度"永远来自真实种子聚合（alaya_seeds.life_stage GROUP BY），
#   本文件只提供阶段的名称/区间/话题这些**元数据**，绝不凭空造记忆。
#   习气网络视角与人生脉络视角，是同一批种子的两种投影。

from __future__ import annotations

# 阶段枚举（alaya_seeds.life_stage / alaya_galaxy_invites.life_stage 的合法值）
STAGES = [
    {
        "key": "childhood", "name": "童年", "subtitle": "老家·家人·玩伴",
        "from": 0, "to": 12,
        "topics": ["老家与家人", "儿时玩伴", "印象最深的一件事", "小时候的样子"],
    },
    {
        "key": "school", "name": "求学", "subtitle": "校园·老师·同窗",
        "from": 7, "to": 22,
        "topics": ["小学中学", "大学时代", "难忘的老师", "同窗挚友"],
    },
    {
        "key": "career", "name": "工作", "subtitle": "立业·同事·起伏",
        "from": 20, "to": 65,
        "topics": ["第一份工作", "转折与转行", "共事的伙伴", "得意与受挫"],
    },
    {
        "key": "love", "name": "婚恋", "subtitle": "相识·恋爱·成家",
        "from": 22, "to": 65,
        "topics": ["怎么认识的", "恋爱那些年", "结婚", "伴侣"],
    },
    {
        "key": "family", "name": "家庭", "subtitle": "子女·育儿·父母",
        "from": 25, "to": 65,
        "topics": ["子女", "养育的日子", "父母与长辈", "家里的传统"],
    },
    {
        "key": "midlife", "name": "中年", "subtitle": "转折·健康·圈子",
        "from": 38, "to": 99,
        "topics": ["事业转折", "身体与健康", "朋友与圈子", "对人生的看法"],
    },
]

STAGE_KEYS = tuple(s["key"] for s in STAGES)

# 追问角度：注入 prompt，保证一个话题聊完聊透（对齐记忆种子的六维指纹）
PROBES = [
    "时间：那是哪一年？你当时多大？",
    "地点：在哪儿发生的？那地方什么样？",
    "人物：当时还有谁在？他们后来怎么样了？",
    "经过：具体是怎么一步步发生的？",
    "感受：你当时心里是什么滋味？",
    "后续：后来呢？这件事之后怎么样了？",
]

DEFAULT_AGE = 35


def stage_map() -> dict:
    """{key: stage} 映射"""
    return {s["key"]: s for s in STAGES}


def stages_for_age(age: int | None) -> list:
    """按年龄裁剪：只返回已经走到的人生阶段（按 from 升序，即人生时序）"""
    a = int(age) if age else DEFAULT_AGE
    return [s for s in STAGES if int(s["from"]) <= a]


def age_from(user: dict) -> int:
    """用户年龄：xd_users.age 优先；缺失用 birthday 推算；再缺省 DEFAULT_AGE"""
    if not user:
        return DEFAULT_AGE
    try:
        age = int(user.get("age") or 0)
        if age > 0:
            return age
    except (TypeError, ValueError):
        pass
    bd = str(user.get("birthday") or "").strip()
    if len(bd) >= 4 and bd[:4].isdigit():
        from datetime import datetime
        y = int(bd[:4])
        cur = datetime.now().year
        if 1900 < y <= cur:
            return max(1, cur - y)
    return DEFAULT_AGE


def stage_name(key: str) -> str:
    s = stage_map().get(str(key or ""))
    return s["name"] if s else ""


def outline_text(key: str) -> str:
    """某阶段的话题提纲文本（注入 prompt 用）"""
    s = stage_map().get(str(key or ""))
    if not s:
        return ""
    return f"{s['name']}（{s['subtitle']}）：{' / '.join(s['topics'])}"


def progress_text(age: int | None, counts: dict, focus: str = "") -> str:
    """人生脉络进度表（注入 prompt）：✓已找回 / ○未开始，并标出本轮优先阶段

    counts: {stage_key: 该阶段真实种子条数}，来自 alaya_seeds 聚合
    """
    stages = stages_for_age(age)
    if not stages:
        return ""
    lines = []
    for s in stages:
        n = int(counts.get(s["key"], 0) or 0)
        mark = "✓" if n > 0 else "○"
        tail = f"已找回 {n} 条" if n > 0 else "0 条"
        star = " ← 本轮优先" if focus and s["key"] == focus else ""
        lines.append(f"  {mark} {s['name']}（{tail}）{star}")
    body = "\n".join(lines)
    out = (f"【TA的人生脉络·按 {int(age) if age else DEFAULT_AGE} 岁裁剪】\n{body}\n"
           "推进规则：优先聊「已开始但没聊完」或「还没开始」的阶段；"
           "一个阶段至少聊出 2-3 件具体的事，再换下一段。")
    if focus:
        out += f"\n【本轮阶段】{outline_text(focus)}"
    return out


def pick_focus(age: int | None, counts: dict) -> str:
    """本轮优先阶段：已开始未聊完的（条数最少且>0）优先，其次第一个未开始的

    纯由真实种子聚合驱动，不落任何进度状态，天然与习气网络一致。
    """
    stages = stages_for_age(age)
    if not stages:
        return ""
    started = [s for s in stages if int(counts.get(s["key"], 0) or 0) > 0]
    if started:
        # 已开始里挑最"薄"的一段（聊得最少）继续补
        thin = min(started, key=lambda s: int(counts.get(s["key"], 0) or 0))
        # 但若所有已开始的阶段都已聊够（>=3 条），则推进到下一个未开始的
        if int(counts.get(thin["key"], 0) or 0) >= 3:
            for s in stages:
                if int(counts.get(s["key"], 0) or 0) == 0:
                    return s["key"]
        return thin["key"]
    return stages[0]["key"]
