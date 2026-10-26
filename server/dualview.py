# dualview.py — 双视角记忆：同一事件，两个 AI 本体各存一份、各用各的视角
#
# 铁律（实测 2026-10-01 定稿，架构级，不得动摇）：
#   对话永远发生在「两个 AI 本体」之间，且双方各自是某个用户的 AI 本体——
#     本体A：当前登录用户本人的 AI 本体（数字分身，xd_users.ai_me_id）
#     本体B：出场的对方 AI 本体（系统AI如意 / 某个亲友的 AI 本体）
#   用户绝不能与自己的 AI 本体对话，只能与**其它**用户的 AI 本体对话。
#   这必须对全体用户成立（多用户通用），不许出现"全局只有某个用户+某个AI"的写法。
#   同一件事，在两个本体的记忆里必须各自生长：
#     本体A：「我」的经历 → subject=self，第一人称
#     本体B：「我」的经历 → subject=self，第一人称（同事件在B眼里归属相反）
#   绝不允许把同一段文本原样写进两份（那是"同一条记忆复制两份"，不是双视角）。
#
# 实现方式：事件事实（事实层，视角无关）只抽一次；再由**确定性视角映射**生成两份种子。
#   事实层由 LLM 抽出（谁做了什么，用中性的第三人称称呼指代每个主体）；
#   视角层是纯函数映射，不走 LLM——因为视角转换必须绝对可靠，不能靠模型"理解"。
from __future__ import annotations

import logging

log = logging.getLogger("alaya.dualview")

# 事实层：参与事件的每个主体，用「称呼」指代（视角无关）
#   role: speaker=当前说话人(真人) | ai=当前出场AI本体 | third=第三方
#   fact: 该主体在事件里的中性陈述（第三人称，不含"我/你"）
FACT_SYSTEM = """你在整理一段对话：既记录客观发生的事，也记录【{ai_name}】自己的认知。

【输入】
- 【{speaker}】是当前说话的人（真人）。
- 【{ai_name}】是正在和TA对话的AI本体。
- 对话原文。

【输出】逐条列出这段对话里**有长期价值的内容**。每条包含：
- kind:
  · event（发生的事）/ value（稳定的属性/偏好/关系）—— 关于【{speaker}】或第三方的客观信息
  · style（{ai_name}自己的说话风格）
  · opinion（{ai_name}对某事的看法、判断、态度）
  · insight（{ai_name}从这段对话里领悟到的道理、总结出的经验）
  · imagination（{ai_name}的联想、想象、脑中浮现的画面）
  · curiosity（{ai_name}想知道、但此刻还不知道的事）
  ⚠️ 后四类（opinion/insight/imagination/curiosity）**只能从【{ai_name}】的发言里抽**，
     它们不属于"对方讲的事"，而是 {ai_name} 自己的认知——会沉淀成 TA 的思维方式和个性。
- subject: speaker（关于{speaker}本人的事）| ai（关于{ai_name}本人或TA的认知）| both（共同经历）| third（第三方）
- person: 这条的核心人物称呼（subject=speaker 填 {speaker}；subject=ai 填 {ai_name}；third 填第三方姓名；both 填 {speaker}）
- fact: 陈述这条内容。event/value 必须用**中性第三人称**（主体一律用称呼，严禁出现"我/你/咱们"）；
        style/opinion/insight/imagination/curiosity 保持 {ai_name} 的第一人称原意（如"我觉得…""这让我想到…"）。
- raw_text: 原话（保留语气）
- seed_date: 绝对日期 YYYY-MM-DD（当前日期 {today}；"昨天/上周"必须换算；无法定位留空）
- time_fuzzy: 模糊时间（如"下午五点多"）
- emotion: 情绪词（平静/开心/生气/伤心/委屈/担心/惊讶…）
- scene: 家庭/职场/社交/医疗/出行/其他
- life_stage: childhood/school/career/love/family/midlife 或留空
- confidence: 0.3~1.0（确定性×重要性）

【铁律】
- **event/value 的唯一来源是【{speaker}】那一行里 TA 亲口讲出的内容。**
  【{ai_name}】那一行说的任何事——陈述、复述、总结、推断、反问——一律不得写成
  event/value。{ai_name} 会听错、记错、把两件事串在一起；把它的断言当成事实来源，
  就会把它的臆测刻成对方的真实经历（实测事故：{ai_name} 把一句 ASR 噪声"针织针织的"
  延伸成"服装厂改电影院"，再写成"对方清理过服装厂的记忆"，此后反复提起）。
- 【{speaker}】在同一段里**否认、纠正**过的内容（"我没说""我没讲过""识别错了"
  "不是这样""别乱记""那是设备播报"）一律不写；整条事实建立在被否认内容上的，整条不写。
  同时把**被否认的那件事实本身**写进 retracted——要用**肯定陈述**（"某某在宏达机械厂
  工作三年"），不要抄用户否认的原话（"我没在…上过班"）。否定句和肯定句的语义向量
  相距极远，系统要拿它去精确对上库里那条事实并作废。
- 只记录对话里**真实讲过**的信息，绝不猜测补全；听不清/碎片/寒暄一律不写。
- **一件事只出一条**：同一件事（同一段经历、同一个决定、同一个场景）无论讲得多散，
  都合并成一条完整事实，绝不拆成多条碎片；也不要把它泛化成原话没有的结论
  （原话说"我陪了她最后一个晚上"，就只能写"陪了最后一个晚上"，不许写成"一直守在身边直到去世"）。
- 人名拿不准（ASR 同音错字）就宁可不写这条。
- event/value 的陈述里**只能出现称呼，不能出现"我/你"**——视角由系统另行生成，你不要代劳。
- {ai_name} 的复述、追问不算 event/value（那是对话动作，不是事）；但 TA 明确表达出来的
  **看法、领悟、联想、好奇**，要按 opinion/insight/imagination/curiosity 抽出来——
  {ai_name} 不是复读机，TA 对这些事有自己的想法，这正是 TA 之所以是"TA"的东西。
- 绝不为了凑数编造：TA 没说过的看法，一个字都不许写。

【只输出JSON】
{{"facts":[{{"kind":"event","subject":"speaker","person":"...","fact":"...","raw_text":"...",
"seed_date":"YYYY-MM-DD","time_fuzzy":"","emotion":"平静","scene":"","life_stage":"","confidence":0.9}}],
"retracted":["被否认的那件事实（肯定陈述，如'某某在宏达机械厂工作三年'；没有就空数组）"]}}

没有可抽取的就输出 {{"facts":[],"retracted":[]}}"""

# 只属于 AI 本体自己的认知类别：只写出场本体、绝不映射到对方本体
# （它们的 fact 是 AI 的第一人称心声，写成对方视角会变成"我"，主体就错乱了）
SELF_ONLY_KINDS = ("opinion", "insight", "imagination", "curiosity")
ALL_KINDS = ("event", "value", "style") + SELF_ONLY_KINDS


def spec_from_facts(facts: list[dict], view: str, my_name: str, peer_name: str) -> list[dict]:
    """事件事实 → 指定本体视角的种子规格（确定性映射，不走 LLM）

    对话的两个本体，本函数把同一份事件事实映射成其中一方的视角：
      view="speaker"：真人本人的 AI 本体（数字分身）—— 事实里的「说话人」就是「我」
      view="ai"     ：出场的对方 AI 本体（如意 / 亲友的AI本体）—— 事实里的「出场AI」就是「我」

    my_name  ：本视角本体的称呼（view=speaker → 当前登录用户的AI本体名；view=ai → 出场AI名）
    peer_name：对面一方的称呼（view=speaker → 出场AI名；view=ai → 当前登录用户的AI本体名）

    铁律：同一件事，两个本体的归属必须相反——
      真人本体(view=speaker)：「我的事」→ self；「AI的事」→ 他人
      出场AI本体(view=ai)  ：「我的事」→ self；「真人的事」→ 他人
    归属完全由 view 决定，双方各自的种子文本、主体称呼也随之相反。
    """
    out: list[dict] = []
    my_role = view                      # speaker 或 ai
    for f in facts or []:
        if not isinstance(f, dict):
            continue
        fact = str(f.get("fact") or "").strip()
        kind = str(f.get("kind") or "").strip()
        subj = str(f.get("subject") or "").strip()
        if not fact or kind not in ALL_KINDS:
            continue
        # AI 自己的认知（见解/领悟/联想/好奇）只属于出场本体：它们是 AI 的第一人称心声，
        # 映射到对方视角就会变成"我"，主体错乱——它们本就不该出现在对方的记忆里。
        if kind in SELF_ONLY_KINDS and my_role != "ai":
            continue
        # 事实的 subject 指事件核心主体相对对话的角色：speaker=真人 / ai=出场AI / both / third
        if subj == "speaker":
            subject = "self" if my_role == "speaker" else "他人"
            person = my_name if my_role == "speaker" else peer_name
        elif subj == "ai":
            subject = "self" if my_role == "ai" else "他人"
            person = my_name if my_role == "ai" else peer_name
        elif subj == "both":
            # 双方共同经历：两边都记为共有
            subject, person = "共有", my_name
        elif subj == "third":
            subject, person = "他人", str(f.get("person") or "").strip()
        else:
            continue
        out.append({
            "seed_type": kind,
            "seed_text": _view_text(fact, subject, person)[:500],
            "raw_text": str(f.get("raw_text") or "")[:1000],
            "subject": subject,
            "person_name": person,
            "seed_date": norm_date(f.get("seed_date")),
            "time_fuzzy": (str(f.get("time_fuzzy") or "").strip() or None),
            "emotion_word": str(f.get("emotion") or "").strip() or "平静",
            "scene": str(f.get("scene") or "").strip(),
            "life_stage": str(f.get("life_stage") or "").strip(),
            "confidence": max(0.3, min(1.0, float(f.get("confidence") or 0.8))),
        })
    return out


def _view_text(fact: str, subject: str, person: str) -> str:
    """事实（中性第三人称）→ 本视角的第一人称陈述（确定性替换，不走 LLM）

    只替换「本视角本体自己」这一个称呼（换成"我"），其余称呼原样保留：
    对方的名字是本体认识的人（如意/用户），写进记忆后仍可被检索到，绝不换成泛称丢信息。
    """
    if not fact:
        return ""
    if subject == "self" and person and person in fact:
        return fact.replace(person, "我", 1)
    return fact


async def extract_facts(user_text: str, answer_text: str, llm_chat_fn,
                        ai_name: str = "", speaker_name: str = "") -> tuple[list[dict], list[str]]:
    """抽一次事件事实（单轮）——把本轮 user/AI 两段拼成对话原文后交给对话级抽取"""
    dialogue = f"【{speaker_name or '对方'}】{user_text or ''}\n【{ai_name or 'AI'}】{answer_text or ''}"
    return await extract_facts_from_dialogue(dialogue, llm_chat_fn, ai_name, speaker_name)


async def extract_facts_from_dialogue(dialogue: str, llm_chat_fn,
                                      ai_name: str = "",
                                      speaker_name: str = "") -> tuple[list[dict], list[str]]:
    """抽事件事实（视角无关的中性陈述）——单轮与整段现量窗口共用这一份。

    结生（窗口级）传进来的就是整段对话原文：一次看全，"同一件事只出一条"这条铁律
    才真正落得下去——逐轮抽取时模型只看到一轮，同一件事被讲三遍就抽出三条碎片。

    第二个返回值 retracted：这段对话里被【speaker】明确否认/纠正过的内容摘要。
    上层据此把库里同义的旧种子作废——被否认的事就是没发生过，不能继续当记忆提。
    """
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    sys_prompt = (FACT_SYSTEM
                  .replace("{ai_name}", ai_name or "AI")
                  .replace("{speaker}", speaker_name or "对方")
                  .replace("{today}", today))
    out_text = await llm_chat_fn(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": str(dialogue or "")[:6000]}], 0.3, 1400)
    data = _safe_json(out_text) or {}
    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)]
    retracted = [str(t).strip() for t in (data.get("retracted") or [])
                 if isinstance(t, str) and str(t).strip()]
    log.info("[dualview] 抽出事件事实 %s 条、否认纠正 %s 条（speaker=%s ai=%s）",
             len(facts), len(retracted), speaker_name, ai_name)
    return facts, retracted


def norm_date(v) -> str | None:
    """日期归一化：只认 YYYY-MM-DD（记忆导入的编码校验也走这里，不重复实现）"""
    s = str(v or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


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
