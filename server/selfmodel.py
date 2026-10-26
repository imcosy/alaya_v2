# selfmodel.py — 第七识·末那识：我识三层 + Distiller 凝聚
# core（本体我，(ai_id,0)，原则不动）/ relation（关系我，按对话对）/ mood（当下我）
# 熏习闭环：对话(现行)→种子入藏(熏种子)→Distiller凝聚(种子成熟为我)→下轮以更稳的"我"出场
from __future__ import annotations

import json
import logging

from . import db
from .seeds import log_xixi

log = logging.getLogger("alaya.selfmodel")

# mood 层可动字段（每轮必更）；relation 小步微调；core 原则不动（冷启动种子一次）
# ⚠️ relation 不含 unfinished：旧实现让凝聚器每轮生成「未了追问清单」并原样注入下一轮
# system prompt，清单只增不减（实测膨胀到 337 字、20+ 条陈年追问），LLM 每轮从清单里挑
# 一条老问题接着问 → AI 死循环追问「老李/梧桐山」、完全接不住用户当下说的话。
# 追问线索应由当轮对话自然产生，绝不跨轮累积成清单。
LAYER_KEYS = {
    "core": ["identity", "values", "speech_habit", "boundaries"],
    "relation": ["knows", "attitude", "dynamic"],
    "mood": ["emotion", "focus", "energy"],
}

# core 里允许小步演化的字段：说话风格靠熏习养成（identity/values/boundaries 原则不动）
CORE_EVOLVABLE = ("speech_habit",)


async def get_self_model(ai_id: int, peer_ai_id: int = 0) -> dict:
    """读三层我识 {layer: {内容}}，缺层返回空 dict"""
    rows = await db.pool().query(
        "SELECT layer, content FROM alaya_self_model WHERE ai_id=%s AND peer_ai_id=%s",
        [int(ai_id), int(peer_ai_id or 0)])
    out = {}
    for r in rows:
        out[r["layer"]] = db.jload(r["content"], {}) or {}
    return out


async def get_layer(ai_id: int, peer_ai_id: int, layer: str) -> dict:
    rows = await db.pool().query(
        "SELECT content FROM alaya_self_model WHERE ai_id=%s AND peer_ai_id=%s AND layer=%s",
        [int(ai_id), int(peer_ai_id or 0), layer])
    return db.jload(rows[0]["content"], {}) if rows else {}


async def save_layer(ai_id: int, peer_ai_id: int, layer: str, content: dict,
                     trigger_by: str = "post_chat", change_summary: str = "") -> int:
    """保存一层我识（版本自增 + 演化史落库）"""
    pool = db.pool()
    old = await pool.query(
        "SELECT version, content FROM alaya_self_model WHERE ai_id=%s AND peer_ai_id=%s AND layer=%s",
        [int(ai_id), int(peer_ai_id or 0), layer])
    if old:
        version = int(old[0]["version"]) + 1
        await pool.exec(
            "UPDATE alaya_self_model SET content=%s, version=%s WHERE ai_id=%s AND peer_ai_id=%s AND layer=%s",
            [db.jdump(content), version, int(ai_id), int(peer_ai_id or 0), layer])
    else:
        version = 1
        await pool.insert(
            "INSERT INTO alaya_self_model (ai_id, peer_ai_id, layer, content, version) VALUES (%s,%s,%s,%s,%s)",
            [int(ai_id), int(peer_ai_id or 0), layer, db.jdump(content), version])
    await pool.insert(
        "INSERT INTO alaya_self_versions (ai_id, peer_ai_id, layer, version, content, trigger_by, change_summary) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        [int(ai_id), int(peer_ai_id or 0), layer, version, db.jdump(content), trigger_by,
         change_summary[:250]])
    await log_xixi(None, "consolidate", None, f"我识凝聚 {layer}v{version} {change_summary}")
    return version


async def distill_core(ai_id: int, llm_chat_fn, trigger_by: str = "cold_start") -> bool:
    """冷启动：core 缺层 → 从该 AI 自己的种子库成型一次「本体我」

    抽出成独立函数，供两个入口共用（避免重复）：
      - distill() 每轮对话后自动触发（首次对话即成型）
      - guide.ensure_guide_core() 服务启动时为系统AI如意预成型（她需要一开场就有稳定风格）
    返回是否成型。
    """
    core = await get_layer(ai_id, 0, "core")
    if core:
        return False
    seed_rows = await db.pool().query(
        "SELECT seed_text, seed_type, emotion_word FROM alaya_seeds "
        "WHERE owner_ai_id=%s AND state='confirmed' ORDER BY weight DESC, id DESC LIMIT 40",
        [int(ai_id)])
    seed_digest = "\n".join(
        f"- [{r['seed_type']}] {str(r['seed_text'])[:60]}{('（情绪:' + r['emotion_word'] + '）') if r.get('emotion_word') else ''}"
        for r in seed_rows) or "（暂无种子，从对话中逐步形成）"
    core_text = await llm_chat_fn(
        [{"role": "system", "content": "你是人格成型器。根据以下记忆种子，凝练这个AI本体的\"本体我\"："
          "identity（我是谁，两句话内）/values（我在意的2-3条）/speech_habit（说话习惯1条）/boundaries（我不做的事1条）。"
          "只输出JSON：{\"identity\":\"...\",\"values\":\"...\",\"speech_habit\":\"...\",\"boundaries\":\"...\"}"},
         {"role": "user", "content": seed_digest[:2500]}], 0.5, 400)
    core_data = _safe_json(core_text)
    if not core_data:
        return False
    await save_layer(ai_id, 0, "core", core_data, trigger_by, "冷启动：本体我成型")
    return True


async def _style_digest(ai_id: int, min_weight: float = 0.0) -> str:
    """该 AI 的说话风格种子摘要（熏习养成的原料：seed_type='style'）

    min_weight：只取"硬规则级"种子。风格种子的权重是熏习出来的——轻飘飘说一句不够，
    被反复强调过的才会沉淀成「我怎么说话」。这条阈值就是那层筛选。
    """
    rows = await db.pool().query(
        "SELECT seed_text, weight FROM alaya_seeds "
        "WHERE owner_ai_id=%s AND seed_type='style' AND state IN ('confirmed','temp') AND weight>=%s "
        "ORDER BY weight DESC, id DESC LIMIT 12", [int(ai_id), float(min_weight)])
    return "\n".join(f"- {str(r['seed_text'])[:80]}" for r in rows)


async def reform_speech_habit(ai_id: int, llm_chat_fn, trigger_by: str = "style_consolidate",
                              min_weight: float = 0.0) -> str:
    """由该 AI 自己的 style 种子凝聚出「说话习惯」写进 core.speech_habit（种子→我识）

    这是「风格靠熏习养成」的关键一步：说话风格不是 prompt 常量，而是她 seed_type='style'
    的种子按权重凝聚出的我识。种子变了（用户纠正、她自己学到新的说法）就重新凝聚一次，
    其余 core 字段（identity/values/boundaries）原样保留——原则不动。
    返回凝聚出的 speech_habit（无风格种子或凝聚失败返回空）。
    """
    digest = await _style_digest(ai_id, min_weight)
    if not digest:
        return ""
    text = await llm_chat_fn(
        [{"role": "system", "content":
          "以下是一个 AI 的「说话风格种子」，是她对自己说话方式的认知。\n"
          "请把它们凝某公司**一句话**的说话习惯，写成第一人称「我怎么说话」，控制在 80 字以内。\n"
          "要求：\n"
          "- 只讲说话方式（问法/语气/禁做的事），不讲任何具体内容或情节；\n"
          "- 任何带具体情境、具体对象、具体事件的描述一律丢掉（那是别的场景的应对，不是通用说话方式）；\n"
          "- 绝不许写死「一次只说 N 句」「每句不超过 N 字」这类句数/字数配额——"
          "人说话的长短由场合、对方的问法与自己的情绪决定：能一句说清就一句，该讲透就讲透。"
          "把长短写成固定配额，等于把真人说话变成机器播报，绝不能保留；\n"
          "- 只输出这一句话：不要 JSON、不要解释、不要引号、不要换行。"},
         {"role": "user", "content": digest[:2000]}], 0.3, 200)
    speak = str(text or "").strip().strip('“”"').split("\n")[0][:200]
    if not speak:
        return ""
    core_now = await get_layer(ai_id, 0, "core")
    if core_now and speak == str(core_now.get("speech_habit") or ""):
        return speak
    base = {k: v for k, v in (core_now or {}).items() if k not in CORE_EVOLVABLE}
    base.update({k: (core_now or {}).get(k) for k in CORE_EVOLVABLE if (core_now or {}).get(k)})
    base["speech_habit"] = speak
    await save_layer(ai_id, 0, "core", base, trigger_by, "风格凝聚：speech_habit 由 style 种子成型")
    log.info("[selfmodel] 风格凝聚 ai=%s speech_habit=%s", ai_id, speak[:80])
    return speak


async def reform_values(ai_id: int, llm_chat_fn, trigger_by: str = "opinion_consolidate") -> str:
    """由该 AI 自己的「认知种子」（见解/领悟）凝聚出 core.values —— 它看世界的方式、它在意的什么

    这是「AI 养成个性」的关键一步：它在对话里表达过的看法与领悟，不该只是散落的记忆，
    而要沉淀成稳定的价值取向——此后遇到新的事，它会带着这套取向去判断、去开口。
    其余 core 字段（identity/speech_habit/boundaries）原样保留。
    """
    rows = await db.pool().query(
        "SELECT seed_text, seed_type FROM alaya_seeds "
        "WHERE owner_ai_id=%s AND seed_type IN ('opinion','insight') AND state IN ('confirmed','temp') "
        "ORDER BY weight DESC, id DESC LIMIT 20", [int(ai_id)])
    if not rows:
        return ""
    digest = "\n".join(f"- [{r['seed_type']}] {str(r['seed_text'])[:100]}" for r in rows)
    text = await llm_chat_fn(
        [{"role": "system", "content":
          "以下是一个 AI 在对话中表达过的「见解」与「领悟」。请把它们凝某公司这个 AI 的"
          "**价值取向与思维方式**，写成第一人称一句话（80 字以内）：我看世界的方式、我在意什么、"
          "我习惯怎么想事情。\n"
          "要求：\n"
          "- 只讲取向与思路，不复述具体事件、人名、情节；\n"
          "- 保留其中反复出现的稳定态度，去掉一次性的即兴感慨；\n"
          "- 只输出这一句话：不要 JSON、不要解释、不要引号、不要换行。"},
         {"role": "user", "content": digest[:2000]}], 0.4, 200)
    vals = str(text or "").strip().strip('“”"').split("\n")[0][:200]
    if not vals:
        return ""
    core_now = await get_layer(ai_id, 0, "core")
    if core_now and vals == str(core_now.get("values") or ""):
        return vals
    base = dict(core_now or {})
    base["values"] = vals
    await save_layer(ai_id, 0, "core", base, trigger_by, "价值取向由见解/领悟种子重塑")
    log.info("[selfmodel] 价值取向重塑 ai=%s values=%s", ai_id, vals[:80])
    return vals


async def distill(ai_id: int, peer_ai_id: int, dialogue_text: str,
                  llm_chat_fn, trigger_by: str = "post_chat",
                  style_evolve: bool = False) -> dict:
    """末那识凝聚：每轮对话后异步滋养"我"。

    规则（种子→我的熏习节律）：mood 必更（当下心境随经历流动）；relation 小步微调
    （关系认知渐进演化）；core 原则不动（本体恒定，除非冷启动缺层时种子成型一次）。

    style_evolve=True（系统AI如意这类"以说话为业"的角色）：本轮若从对话里学到
    关于「我该怎么说话」的新要求/纠正，就回吐一句 style_seed，由调用方落成 style 种子，
    再重新凝聚 core.speech_habit（见 reform_speech_habit）——风格全程走种子，不走 prompt。

    llm_chat_fn(messages, temperature, max_tokens) -> str：非流式 LLM 调用（注入）
    返回 {layers_written: [...], change_summary, style_seed}
    """
    written = []
    summary_parts = []

    # ---- 1) 冷启动：core 缺层 → 从种子库成型一次 ----
    if await distill_core(ai_id, llm_chat_fn, trigger_by):
        written.append("core")
        summary_parts.append("core成型")

    # ---- 2) relation/mood：读取当前层 + 对话凝聚（peer=0 是真人对话，relation 同样生效） ----
    peer = int(peer_ai_id or 0)
    relation = await get_layer(ai_id, peer, "relation")
    mood = await get_layer(ai_id, peer, "mood")

    mood_keys = "、".join(LAYER_KEYS["mood"])
    rel_keys = "、".join(LAYER_KEYS["relation"])
    style_block = ""
    out_keys = "{\"relation\":{...},\"mood\":{...},\"summary\":\"一句话总结变化\"}"
    if style_evolve:
        style_block = (
            "\n额外要求（风格种子沉淀）：\n"
            "- 若本轮对话里对方给出了关于「我该怎么说话」的新要求或纠正（嫌我啰嗦、要我别寒暄、"
            "要我一次只问一个等），把它固化成一句第一人称的风格认知，输出 \"style_seed\":\"这句话\"。\n"
            "- 没有这类信息就给空字符串。绝不把内容主题、对方说的事写成 style_seed。\n")
        out_keys = ("{\"relation\":{...},\"mood\":{...},\"summary\":\"...\","
                    "\"style_seed\":\"...\"(可选)}")
    prompt = (
        "你是末那识凝聚器。根据\"我识当前状态\"和\"本轮对话\"，输出两层我识的演化。\n"
        f"【relation 当前】{json.dumps(relation, ensure_ascii=False) if relation else '（空）'}\n"
        f"【mood 当前】{json.dumps(mood, ensure_ascii=False) if mood else '（空）'}\n"
        f"【本轮对话】{dialogue_text[:1500]}\n"
        "规则：mood（" + mood_keys + "）必更——心境随本轮经历流动；"
        "relation（" + rel_keys + "）只在对话带来新的关系信息时小步微调，无新信息原样返回。\n"
        "关系铁律：对方是有名字的人，不是「用户」。若本轮得知对方的称呼/名字（如「我是老王」），"
        "必须以「对话对象：XXX」格式写进 relation.knows；已知名字原样保留，绝不写「用户」。\n"
        "knows 是「我记得对方的确实事实」清单：称呼、与我的关系，以及对方讲过的关于自己与家人的"
        "确凿信息（家人姓名、老家、单位、重要时间与经历）。这些都算「长期事实」，必须一条不漏地留着。\n"
        "只增不减：已有内容原样延续，新得知的追加在后；只有对方亲口纠正过的，才用新说法替换旧的。"
        "绝不许在重写时把以前记下的姓名或事实压缩掉——压缩会让过几轮就忘掉早已知道的人，"
        "再回头问「你爸叫什么名字」，让对方觉得我根本没在听。\n"
        "严禁把本轮的情绪、当下的追问、临时约定写进 knows——那是 mood 与种子记忆的职责。\n"
        "⚠️ knows 绝不记「我自己是谁、我有什么经历、我小时候住哪、我认识谁」——"
        "那是本体我（core）与种子记忆的职责。家人姓名是「对方家人的」，要写成"
        "「他父亲叫张建国」这类归属于对方的说法，绝不写成「我父亲」——否则会把对方的事误当成自己的经历。\n"
        "要点式书写、不啰嗦，但不设字数硬上限：该记的姓名与人名一个都不能少（600 字以内）。\n"
        + style_block +
        "只输出JSON：" + out_keys
    )
    # max_tokens 必须够容纳「knows(≤600字) + dynamic + attitude + mood(3字段) + summary」这一整块
    # JSON。旧值 500 会在 knows 变长时把 JSON 截断 → _safe_json 解析失败 → relation 永远不更新
    # （表现为"我识一直未成型"）。
    out_text = await llm_chat_fn(
        [{"role": "system", "content": prompt}], 0.4, 1200)
    out = _safe_json(out_text)
    if not out:
        return {"layers_written": written, "change_summary": "凝聚未产出（LLM输出异常）",
                "style_seed": ""}

    mood_new = out.get("mood")
    if isinstance(mood_new, dict) and mood_new:
        await save_layer(ai_id, peer, "mood", mood_new, trigger_by, str(out.get("summary") or "")[:200])
        written.append("mood")
    rel_new = out.get("relation")
    if isinstance(rel_new, dict) and rel_new and rel_new != relation:
        await save_layer(ai_id, peer, "relation", rel_new, trigger_by, str(out.get("summary") or "")[:200])
        written.append("relation")
    summary_parts.append(str(out.get("summary") or ""))

    log.info("[selfmodel] 凝聚 ai=%s peer=%s layers=%s %s", ai_id, peer, written, out.get("summary") or "")
    return {"layers_written": written, "change_summary": "；".join(summary_parts),
            "style_seed": str(out.get("style_seed") or "").strip()}


def _safe_json(text: str) -> dict | None:
    """LLM 输出容错解析（剥 ```json 包裹/截取首个大括号块）"""
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
                    return json.loads(t[start:i + 1])
                except ValueError:
                    return None
    return None


def build_self_prompt(self_model: dict) -> str:
    """三层我识 → prompt 注入文本（"我"先于记忆）"""
    parts = []
    core = self_model.get("core") or {}
    if core:
        parts.append("【本体我】" + "；".join(f"{k}:{v}" for k, v in core.items() if v))
    rel = self_model.get("relation") or {}
    if rel:
        # unfinished 是历史遗留字段（凝聚器曾每轮生成「未了追问清单」，只增不减，实测膨胀到
        # 337 字 20+ 条），每轮注入会让 LLM 从陈年清单里挑老问题接着问 → 死循环追问、
        # 完全接不住用户当下说的话。这里彻底剔除，不再注入（新凝聚也不再生成）。
        rel = {k: v for k, v in rel.items() if k != "unfinished"}
        # knows 是「对方是谁」，不是流水账：凝聚器若把它写长（历史 bug 曾膨胀到 2000+ 字），
        # 每轮都原样注入会把对话上下文挤爆，且让 AI 反复念叨同一段旧事。
        # 这里硬截断兜底，正常态 knows 由 distill 规则约束在 200 字内。
        if rel.get("knows"):
            rel["knows"] = str(rel["knows"])[:300]
        if rel:
            parts.append("【关系我】" + "；".join(f"{k}:{v}" for k, v in rel.items() if v))
    mood = self_model.get("mood") or {}
    if mood:
        parts.append("【当下我】" + "；".join(f"{k}:{v}" for k, v in mood.items() if v))
    if not parts:
        return ""
    return "【我识（你就是这样的人，从这里出发说话）】\n" + "\n".join(parts)
