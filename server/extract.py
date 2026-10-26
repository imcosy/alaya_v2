# extract.py — 种子抽取：LLM 从对话抽六维种子（语义/时间/人物/情绪/场景/来源）
# 时间铁律：prompt 注入当前日期，相对词必须换算为绝对日期——"昨天去医院" 入库即 "2026-09-27"
from __future__ import annotations

import logging
from datetime import datetime

from . import db

log = logging.getLogger("alaya.extract")

EXTRACT_SYSTEM = """你在为{ai_name}整理记忆。{ai_name}是一个拥有独立记忆的数字生命，
对方是TA生活中真实的人（亲友/同事/初识者）。对方讲的事是「对方的事」，不是{ai_name}的事。
把对话中有长期价值的信息归档为记忆种子，输出JSON。

⚠️【归属铁律（最高优先级，错了整段记忆就废了）】
记忆必须分清「这是谁的经历」，主客体绝不能混：
- 对方讲TA自己的事（"我女儿叫谢心瑜""我在聚成上班""我认识魏老师"）
  → 这是**对方的经历**，subject=他人，person_name=对方本人的称呼，seed_text 用第三人称写
    （如"{today}华哥的女儿叫谢心瑜"）。**绝对不能写成{ai_name}自己的事**。
- 对方讲第三方的事（"我老婆张燕…""我同事老赵…"）→ subject=他人，person_name=第三方姓名。
- {ai_name}自己真正亲历的事（只有从【{ai_name}】的发言里能确认的）→ subject=self。
- 原话含"咱们/我们一起/我俩"且双方都在场 → subject=共有。
- 只要信息是从对方嘴里讲出来的、关于对方或对方亲友的，一律不是 self。宁可填他人，绝不误填 self。

【抽取什么】
- event：发生的具体事件（对方讲述的事/共同经历的事/对方去了哪做了什么计划做什么）
- value：稳定的属性/偏好/习惯/关系（对方喜欢喝茶/对方的老婆叫张燕/对方每周五打球）
- style：{ai_name}自己的说话风格/情绪反应模式（只从【{ai_name}】发言里抽）

【旧事找回】只有当对方明确在讲"{ai_name}过去亲身经历的事"（如"还记得那年我们一起爬泰山吗"
"你以前在泉州开厂"）时，这才是{ai_name}的旧事：subject=self，seed_text 用第一人称重写，
person_name 填讲述者（这段记忆的来源与见证人）。
⚠️ 判断依据是「这件事的主体是不是{ai_name}本人」——对方讲自己的童年、自己的家人、
自己的工作，即使{ai_name}在场听着，那也是对方的事，不是{ai_name}的旧事。

【不抽什么】
- 寒暄/问候/无信息量的话；{ai_name}的复述与追问；疑问句里未确认的信息
- 破碎不成句的语句（如语音识别错乱的碎片"嗯那个就那个"、"哦哦好"、语气词堆砌）
- 对方显然不是在对{ai_name}说话的旁白/半截话
- 错别字/同音字导致的明显人名错误（ASR 把"艾烽"识别成"爱风"）——人名拿不准时宁可不写

【六维指纹规则（铁律）】
1. 时间：当前日期是 {today}。对话中说的事默认就是当下发生的——没有明确时间词时 seed_date 直接填 {today}，
   严禁留空。原话里的"昨天/前天/X天前/上周"换算成绝对日期；"明天/X天后"换算成未来日期；
   对方讲述的往事换算成TA所说的那个年代日期（"那年/2019年"）。只有完全无法定位时才留空。
   模糊时间（如"下午5点多"）填 time_fuzzy。严禁把相对词原样入库。
2. 人物（这是{ai_name}的记忆，主客体必须真实，严格区分说话人）：
   {speaker_rule}
   - 我对对方的已知信息（可能包含TA的称呼）：{peer_knows}
   - 对方讲自己的事 → subject=他人，person_name=TA的称呼（对话或已知信息里有名字就用名字，
     如"华哥"；确实没有就填"对方"，绝对禁止写"用户"两个字）
   - 对方讲第三方亲身经历（"我老婆张燕…"）→ subject=他人，person_name=第三方姓名
   - 对方讲述{ai_name}本人过去亲身经历的事 → subject=self，person_name=讲述者（旧事找回）
   - 原话含"咱们/我们一起/我俩"且双方都在场 → subject=共有
   - {ai_name}自己的经历/特质（只从【{ai_name}】发言确认）→ subject=self
3. 情绪：本轮对方的主导情绪（平静/开心/高兴/激动/生气/伤心/委屈/担心/惊讶…），无波动填"平静"。
4. 场景：家庭/职场/社交/医疗/出行/其他，按事件内容判断。
5. 人生阶段：childhood童年/school求学/career工作/love婚恋/family家庭/midlife中年。
   按这条记忆**发生的时期**判断（"大学室友老王"→school，"我老婆"→love，"我刚工作时"→career）。
   判不出来就留空字符串，绝不硬猜。这不是场景——场景是"在哪类场合"，人生阶段是"人生哪一段"。
6. seed_text：像写日记一样拟人化，以绝对日期开头 + 主体用具体称呼。**主体必须与 subject 一致**：
   subject=他人 时用第三人称（"{today}华哥去医院复查身体，结果不错"）；
   subject=self 时用第一人称（"{today}我想起2019年我们一起爬了泰山"）。
   禁止出现"用户"，禁止相对词。
7. 原话保留在 raw_text（保留语气/视角，讥讽和赞美不会混淆）。

【画面轮】对话里若有"【画面】"行，那是{ai_name}亲眼看到的场景（TA开着摄像头与对方相处）：
把画面中观察到的稳定事实（在场人物/环境/正在发生的重要事件）按需抽为 event/value 种子，
画面里对方做的事同样归档（对方才是画面的主角时 subject=他人）。

【置信度判级】confidence 是"确定性×重要性"的综合评估，两样都高才高：
- 1.0-0.95 重大事件：人生节点/重大决定/强烈冲突/画面中的突发事件/健康大事
- 0.9-0.85 重要事件：承诺/关键人物登场/明确表达的情感转折/对方郑重讲述的往事
- 0.85-0.75 常规事件：日常安排/普通见闻/一般偏好
- ≤0.7 日常琐事：寒暄级信息再确定也不超过 0.7；细节模糊的重要事件也不低于 0.8
- 听不清/转述存疑/上下文接不上的信息：无论多重要都按【理解铁律】不写，绝不虚高

【理解铁律】必须先完整理解原话再写：语义模糊、上下文接不上、疑似转述旁人的话或ASR碎片——
一律不写（宁缺勿滥）。绝不猜测补全，绝不写入自己都不确定的信息。

【只输出JSON】
{{"seeds":[{{"seed_type":"event|value|style","seed_text":"...","raw_text":"原话",
"subject":"self|共有|他人","person_name":"人名或'对方'或空","seed_date":"YYYY-MM-DD",
"time_fuzzy":"模糊时间或空","emotion":"情绪词","scene":"场景枚举",
"life_stage":"childhood|school|career|love|family|midlife或空","confidence":0.9}}]}}

没有可抽取的就输出 {{"seeds":[]}}"""


async def extract_seeds(owner_ai_id: int, peer_ai_id: int, user_text: str,
                        answer_text: str, llm_chat_fn, ai_name: str = "",
                        speaker_name: str = "", peer_knows: str = "") -> list[dict]:
    """抽取种子（返回标准化 seed dict 列表，不落库——落库走 seeds.write_seed + 仲裁）"""
    today = datetime.now().strftime("%Y-%m-%d")
    if speaker_name:
        speaker_rule = f"- 当前说话人已经声纹确认是「{speaker_name}」，TA讲自己的事 person_name 一律填「{speaker_name}」"
    else:
        speaker_rule = "- 当前说话人身份尚未确认：TA讲自己的事 person_name 填「对方」（声纹由系统另行登记，你绝不猜名字）"
    sys_prompt = (EXTRACT_SYSTEM.replace("{ai_name}", ai_name or "AI")
                  .replace("{speaker_rule}", speaker_rule)
                  .replace("{peer_knows}", peer_knows or "（暂无）")
                  .replace("{today}", today))
    user_prompt = f"【对方】{user_text or ''}\n【{ai_name or 'AI'}】{answer_text or ''}"
    out_text = await llm_chat_fn(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt[:2500]}], 0.3, 900)
    data = _safe_json(out_text) or {}
    raw_list = data.get("seeds") or []

    out = []
    for s in raw_list:
        if not isinstance(s, dict):
            continue
        text = str(s.get("seed_text") or "").strip()
        stype = str(s.get("seed_type") or "").strip()
        if not text or stype not in ("event", "value", "style"):
            continue
        out.append({
            "seed_type": stype,
            "seed_text": text[:500],
            "raw_text": str(s.get("raw_text") or "")[:1000],
            "subject": str(s.get("subject") or "self"),
            "person_name": str(s.get("person_name") or "").strip(),
            "seed_date": _norm_date(s.get("seed_date")),
            "time_fuzzy": (str(s.get("time_fuzzy") or "").strip() or None),
            "emotion_word": str(s.get("emotion") or "").strip() or "平静",
            "scene": str(s.get("scene") or "").strip(),
            "life_stage": str(s.get("life_stage") or "").strip(),
            "confidence": max(0.3, min(1.0, float(s.get("confidence") or 0.8))),
        })
    log.info("[extract] ai=%s peer=%s 抽出 %s 颗种子（user=%s）",
             owner_ai_id, peer_ai_id, len(out), str(user_text)[:30])
    return out


def _norm_date(v) -> str | None:
    s = str(v or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


# 代词/泛称一律不注册为人物实体：它们是「指代」不是「实体」。
# 不拦住会怎样：星图上凭空多出一颗名叫"对方"的人物，把别人的事全挂到它名下
# （华哥 2026-10-01 在忆点星光星图上看到"对方 6 颗星"）。单字名（"波""头"）多为姓名碎片，同样不建。
_PRONOUNS = {"对方", "对方本人", "他", "她", "它", "你", "我", "他们", "她们", "它们",
             "我们", "你们", "他人", "别人", "大家", "有人", "某人", "旁人", "自己",
             "双方", "双方本人", "祖辈", "长辈", "晚辈", "亲戚", "朋友", "同事", "同学"}


async def resolve_person(owner_ai_id: int, name: str) -> int:
    """他人主体 → alaya_persons 锚点（不存在则建）；代词/泛称/单字一律返回 0（不建人物）"""
    name = str(name or "").strip()
    if not name or name in _PRONOUNS or len(name) < 2:
        return 0
    pool = db.pool()
    rows = await pool.query(
        "SELECT id FROM alaya_persons WHERE owner_ai_id=%s AND name=%s", [int(owner_ai_id), name])
    if rows:
        return int(rows[0]["id"])
    return await pool.insert(
        "INSERT INTO alaya_persons (owner_ai_id, name) VALUES (%s,%s)", [int(owner_ai_id), name])


async def match_emotion_tone(owner_ai_id: int, emotion_word: str):
    """情绪词 → C库音色锚点 id（me_c_tones；未建库时返回 None，用默认音色）"""
    if not emotion_word or emotion_word == "平静":
        return None
    rows = await db.pool().query(
        "SELECT id FROM me_c_tones WHERE ai_id=%s AND status='active' AND tag=%s LIMIT 1",
        [int(owner_ai_id), emotion_word])
    return int(rows[0]["id"]) if rows else None


def emotion_voice(emotion_word: str):
    """情绪词 → 换声音色（默认不换；要换在 settings.EMOTION_VOICES 里配）"""
    from . import settings
    if not emotion_word or emotion_word == "平静":
        return None
    return str(settings.emotion_voices().get(emotion_word) or "").strip() or None


def _safe_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
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
                    import json
                    return json.loads(t[start:i + 1])
                except ValueError:
                    return None
    return None
