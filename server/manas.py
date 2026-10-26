# manas.py — 第七识·末那识心智涌现中枢（上层意识决策中枢）
# 双引擎分工：阿赖耶记忆引擎决定「AI是什么、有什么记忆、是什么人格」；
#            末那识心智涌现中枢决定「AI现在想什么、要不要说话、如何表达」。
#
# 五步闭环（永久后台自运行，毫秒级迭代）：
#   ① 全局种子并行迭代：邻域加权求和 + 自然衰减 + 节点侧抑制 → 刷新 activation
#   ② 六识感官信号接入：多模态输入编码为临时感官种子，由人格特质种子自主放大/削弱
#   ③ 全网竞争·主念涌现：滑动窗口积分，取激活最高的种子集群 = 当下唯一显意识主念
#   ④ 阈值分层：0.0~0.32 潜意识 / 0.32~0.72 显意识内思考 / ≥0.72 触发外显行为
#   ⑤ 熏习回写：更新关联长期种子连接权重，临时感官种子衰减销毁
#
# 铁律（对齐开发文档）：
#   - 零人工硬约束：性格差异（内向/外向、敏感/迟钝）全部来自 trait 种子权重，不写死代码
#   - 念头绝对自主性：沉默是心智自主涌现的选择，无强制应答、无固定回合制
#   - 临时感官种子不污染长期认知网络（到期销毁，永不转 confirmed）
#   - 热态常驻内存迭代，DB 仅承担离线快照
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from collections import deque

from . import db
from .seeds import log_xixi

log = logging.getLogger("alaya.manas")

# ── 迭代超参（全部为网络动力学常数，非行为规则）─────────────────
TICK_MS = 200                  # 一次迭代间隔（毫秒级；中枢心跳）
DECAY = 0.90                   # 每 tick 自然衰减系数（长期种子超低衰减）
# 念头永续生发：说话中、静默中、发呆时，网络都持续迭代与熏习，**绝不强行切换会话**。
# 用户隔几小时回来时，让"当下这句话"通过它与记忆的关联权重去主导这一念
# （见 conceive：所缘由「与当下感官种子的关联强度」决定，而非历史激活残留）。
# 临时感官种子衰减：与长期种子同级。旧值 0.62 每拍砍掉近四成，三四拍就归零——
# 用户话还没被表达出去，承载语境的那颗种子已经死了，主念自然又落回旧种子。
# 「不污染长期认知网络」由 TTL 到期销毁保证（见 inject_sensory/step），不靠衰减硬砍。
SENSORY_DECAY = 0.90
# 环路增益约束：DECAY + GAIN < 1 是网络收敛的充要条件（谱半径 < 1）。
# 传导项按节点自身邻域权重和归一（inflow/deg ∈[0,1]），故 0.90 + 0.085 = 0.985 < 1，
# 网络必然收敛到稳态，不会自激发散，也不需要任何兜底截断。
GAIN = 0.085                   # 邻域加权求和传导增益
INHIBIT = 0.30                 # 节点侧抑制强度（除法归一：激活越高，对邻域传导抑制越强）
MAX_ACT = 1.0                  # 激活值上限（阈值分层的量纲基准：0.4/0.8）
NOISE = 0.012                  # 内生噪声（无外部输入时产生稳态念头流，不靠规则驱动）

# ── 阈值分层（相对统治力，无绝对硬编码阈值）──────────────────
# 用户设计纠正：是否"想说"不是比对某个固定数值，而是全网竞争"最高者胜出"。
# 主念是否够格外显，取决于它对全网的相对统治力（dominance），而非绝对激活值。
#   dominance = (主念激活 - 全网次高激活) / 主念激活   ∈ [0,1]
# 主念把第二名甩得越远，说明这个念头越清晰、越不容置疑 → 越该开口。
# 各档位只定义"相对领先多少算某一层"，与网络绝对电位量纲无关，天然自适应。
DOM_MANIFEST = 0.42            # 主念领先次高 ≥42% → 念头清晰到"想说"，触发外显行为
DOM_CONSCIOUS = 0.18           # 领先 ≥18% → 显意识内思考（念头成形，尚未压过全场）
DOMINANCE_FLOOR = 0.06         # 主念绝对激活下限（无输入时噪声涌动，不足以构成念头）

# ── 人格特质种子（决策差异化唯一来源，权重由熏习演化，不写死行为）──
TRAIT_SEEDS = (
    ("对外高敏感·人来疯", 1.6, "人来疯特质：外界刺激被放大，念头极易被带动"),
    ("对外低敏感·内向独处", 1.6, "内向独处特质：外界刺激被抑制，自我念头稳定延续"),
    ("情绪高惯性·念头稳定", 1.4, "情绪高惯性：念头稳定，不易被外界打乱"),
    ("情绪低惯性·念头灵动", 1.4, "情绪低惯性：念头灵动，极易被环境带动"),
)
TRAIT_SENSE = 0.9              # 人格种子对感官刺激的传导基准
SENSORY_BASE_ACT = 0.62        # 感官种子基础激活（入局即高电位，参与竞争）

# ── 对话技能种子（skill）：仿真人对话的「通识能力」，冷启动播种进本体记忆库 ──
# 这些是「怎么和人说话」的技能类记忆，不是硬编码行为规则——它们是种子，
# 参与习气网络竞争、可被熏习强化/削弱，AI 用什么技能、何时用，由主念自主涌现决定。
# 与 trait 的区别：trait 是「我是什么性格」，skill 是「我会怎么说话」。
SKILL_SEEDS = (
    ("等待说完·不抢话", 1.5,
     "对话技能：对方还在说话时绝不插嘴，等 TA 把话说完、停顿静默后再回应。"
     "抢话是失礼；聆听是尊重。"),
    ("话中回应·嗯对继续", 1.4,
     "对话技能：对方长篇讲述经历/情景时，在 TA 停顿的间隙用「嗯」「对」「继续」"
     "「没错」「是」「哦？」「啊？」这类极简回应，表示在听、在跟随，不打断叙述。"),
    ("停顿控制·留白呼吸", 1.3,
     "对话技能：说话有停顿与留白，句间呼吸，不一口气把话倒完；"
     "重要处稍作停顿，给对方消化与接话的空间。"),
    ("情绪标签·感知情绪", 1.3,
     "对话技能：从对方话语里感知情绪（喜/怒/哀/惧/惊/平静），"
     "并让自己的回应带上相应的情绪色彩。"),
    ("情绪音色·随情换声", 1.2,
     "对话技能：情绪变化时切换相应音色——平静用常色，"
     "喜悦轻快、悲伤低沉、惊讶上扬，让声音与情绪一致。"
     "外显方式：在回复最开头写一个情绪标记 [[情绪:喜悦]]（可选值：喜悦/悲伤/愤怒/惊讶/委屈/平静），"
     "系统会据此切换音色；若拿不准或情绪平稳，就写 [[情绪:平静]] 或不写。标记只写一次、放在最前。"),
)
SKILL_SENSE = 0.85             # 技能种子对感官刺激的传导基准（略低于人格，技能是习得的）

# ── 主念涌现：滑动窗口积分 ───────────────────────────────────
WINDOW = 8                     # 滑动窗口 tick 数
CLUSTER_TOP = 6                # 主念集群取激活最高的 N 颗种子
CLUSTER_EDGE = 0.25            # 集群内关联边权重下限（低于此值不算同一念头）
EXPRESS_COOLDOWN = 25          # 两次自主表达之间的最小静默拍数（≈5s，防念头抖动抢话）

# ── 内生念头（无外部输入时也自主开口）──────────────────────────
# 工程落点对应 8.0 文档 7.10「内生念头生成能力」。
# ⚠️ 没有"静默 N 秒就开口"的时钟——人是"想起来才说"，不是"到点就说"。
#    开口的时机完全由记忆自身的**自发放电**（泊松式）与全网竞争决定：
#    某一念自己浮成主念，就说；浮不起来，这一念就过去了，什么都不说。
SPONT_RECALL_P = 0.005         # 每拍记忆自发忆起的概率（≈平均 200 拍/40 秒一次，随机分布）
IDLE_SAMPLE_GAIN = 0.55        # 忆起时的激活电位（足以争主念，但仍受全网侧抑制约束）
IDLE_BOOST_TICKS = 14          # 忆起的那一念持续 ≥WINDOW 拍：单拍脉冲会被窗口积分稀释
                               # （0.55/8≈0.07），顶不成主念；持续几拍窗口均值才真的抬起来，
                               # AI 开口说的才是"刚想起的那件事"，而不是噪声里随便胜出的旧念头
IDLE_DOM = 0.03                # 内生念头的相对统治力下限（想起的那一念要略微胜出噪声）
# 主动搭话的静默门槛：距最后一次"有人跟我说话"至少这么久，才允许自己起话头。
# 人刚聊完不会马上自言自语；没有这道门槛时，用户回完一句几秒后她又拎着旧事开口，
# 十几分钟里反复提同一件事（实测用户原话："完全无法对话，自己也不知道在说什么"）。
IDLE_SPEAK_SEC = 45
IDLE_SPEAK_TICKS = max(1, int(IDLE_SPEAK_SEC * 1000 / TICK_MS))
# 同一颗种子提过之后，这么久之内不再被自主采样——同一件旧事不能反复拿出来说。
SPOKEN_COOLDOWN_SEC = 1800
SPOKEN_COOLDOWN_TICKS = max(1, int(SPOKEN_COOLDOWN_SEC * 1000 / TICK_MS))

# ── 感官↔记忆 语义关联（习气网络建边的正确算法）────────────────
# 用户说一句话 → 用语义向量（外部 embedding）找最相关的记忆种子建边。相似度即边权：
# 越相关传导越强，越能一起把主念顶起来。这才是「相关的种子都关联起来，形成熏习」。
ASSOC_TOPK = 12                # 每次感官输入最多关联的记忆种子数
ASSOC_MIN_SIM = 0.40           # 语义相似度下限（低于此视为不相关，不建边）
                               # 实测：「还记得我奶奶吗」↔「奶奶生前…」0.52（命中），
                               # ↔「老王室友」0.37、↔「形象照」0.33（排除）

SNAP_EVERY = 150               # 每 N tick 落一次温态快照（DB 不参与实时运算）


class ManasCore:
    """单个 AI 本体的末那识心智涌现中枢（热态：常驻内存迭代）"""

    def __init__(self, owner_ai_id: int):
        self.owner_ai_id = int(owner_ai_id)
        self.tick = 0
        self.nodes: dict[int, dict] = {}       # seed_id → {type, weight, act, sensory}
        self.edges: list[tuple[int, int, float]] = []   # (src, dst, weight)
        self.adj: dict[int, list[tuple[int, float]]] = {}
        self.hist: dict[int, deque] = {}       # seed_id → 激活滑动窗口
        self.dominant: list[dict] = []         # 当前主念集群
        self.dominant_act = 0.0
        self.lead_sid = 0                      # 当下这一念的所缘主体（窗口积分胜出者）
        self.dominance = 0.0                   # 主念相对全网的统治力（最高者胜出的领先幅度）
        self.layer = "sub"
        self.silence_ticks = 0
        self.sensory: dict[int, float] = {}    # 临时感官种子 seed_id → 剩余寿命(tick)
        # 已触发过表达的感官种子：一颗感官种子只能引发一次开口。
        # 否则在它存活的 45 拍（9 秒）内，每一拍都满足"当下有感官"而反复排表达，
        # AI 会把自己的回复当新输入、无限自我对话（"AI 回答自己的回答"）。
        self._consumed_sensory: set[int] = set()
        self.trait_ids: list[int] = []
        self.skill_ids: list[int] = []     # 对话技能种子（等待说完/话中回应/停顿/情绪音色…）
        self._pending_destroy: list[int] = []
        self._last_snap = 0
        # 表达调度（非回合制）：主念突破阈值才排一次"想说"，冷却期内不重复
        self.express_pending = False
        self.express_owner = ""      # 本次"想说"归属的会话 key（见 _schedule_expression）
        self._sensory_owner: dict[int, str] = {}   # 感官种子 seed_id → 接收它的会话 key
        self._sensory_evidence: dict[int, str] = {}  # 感官种子 seed_id → 现量角色（见 working.MARK_*）
        # 最近一次"真的有人说话"的会话：自主念头只能对**这一台**说。
        # 页面挂着不等于身边有人——把"我自己想起的事"推到一间空屋子里，
        # 就是用户说的"分不清有没有人、把记忆当成对方说的话"。
        self.last_live_session = ""
        self._last_express = 0
        # 最后一次"有人跟我说话"的拍（自主开口的静默门槛用它，见 IDLE_SPEAK_TICKS）
        self.last_user_tick = 0
        # 自主开口提过的种子（sid → tick）：冷却期内不再采样，同一件旧事不反复说
        self._spoken: dict[int, int] = {}
        self.last_expression: dict | None = None   # 最近一次自主表达的主念（供前端展示）
        # 内生念头（无外部输入时的自主开口，见 8.0 文档 7.10）
        self.idle_recall_sid = 0        # 此刻自己浮起来的是哪颗种子（自主开口的所缘）
        self._idle_boost = (0, -1)      # (浮起的种子, 保持到时该拍)：见 step()

    # ── 载入（温态 → 热态）────────────────────────────────
    async def load(self):
        pool = db.pool()
        # 临时记忆（sensory 六识现量 / working 现量窗口）绝不载入习气网络：
        # 它们只活在"当下"，其价值要由结生算子提炼成长期种子后才算入网。
        from .seeds import TRANSIENT_TYPES
        ph = ",".join(["%s"] * len(TRANSIENT_TYPES))
        rows = await pool.query(
            "SELECT id, seed_type, seed_text, weight, subject, emotion_word, state, expire_at "
            "FROM alaya_seeds "
            f"WHERE owner_ai_id=%s AND state IN ('confirmed','temp') AND seed_type NOT IN ({ph}) "
            "ORDER BY weight DESC, id DESC LIMIT 1200", [self.owner_ai_id, *TRANSIENT_TYPES])
        self.nodes = {}
        for r in rows:
            sid = int(r["id"])
            self.nodes[sid] = {
                "id": sid,
                "type": str(r.get("seed_type") or "value"),
                "name": str(r.get("seed_text") or ""),
                "subject": str(r.get("subject") or "self"),
                "emotion": str(r.get("emotion_word") or ""),
                "weight": float(r.get("weight") or 1.0),
                "act": 0.0,
            }
        ids = list(self.nodes.keys())
        self.trait_ids = [i for i in ids if self.nodes[i]["type"] == "trait"]
        self.skill_ids = [i for i in ids if self.nodes[i]["type"] == "skill"]
        self.edges = []
        if ids:
            ph = ",".join(["%s"] * len(ids))
            erows = await pool.query(
                f"SELECT src_seed_id, dst_seed_id, weight FROM alaya_seed_edges "
                f"WHERE owner_ai_id=%s AND src_seed_id IN ({ph}) AND dst_seed_id IN ({ph})",
                [self.owner_ai_id] + ids + ids)
            for e in erows:
                a, b = int(e["src_seed_id"]), int(e["dst_seed_id"])
                if a in self.nodes and b in self.nodes:
                    self.edges.append((a, b, float(e.get("weight") or 0.3)))
        self.adj = {i: [] for i in ids}
        for a, b, w in self.edges:
            self.adj[a].append((b, w))
            self.adj[b].append((a, w))
        self.hist = {i: deque(maxlen=WINDOW) for i in ids}
        # 内生初值：权重越高，起手电位越高（人格底色的直接体现）
        for i in ids:
            self.nodes[i]["act"] = min(MAX_ACT, 0.06 * self.nodes[i]["weight"])
        log.info("[manas] 本体 #%s 载入热态：%s 种子 / %s 关联边 / %s 人格特质种子 / %s 对话技能种子",
                 self.owner_ai_id, len(self.nodes), len(self.edges), len(self.trait_ids), len(self.skill_ids))
        return len(self.nodes)

    async def ensure_traits(self):
        """人格特质 + 对话技能种子集群冷启动：本体缺失时按基线播种（此后由熏习自主演化）

        trait 决定「我是什么性格」，skill 决定「我会怎么说话」——两者都是种子，
        都参与习气网络竞争，AI 用什么技能、何时用，全由主念自主涌现决定，不写死行为。
        """
        from .seeds import write_seed
        if not self.trait_ids:
            for name, weight, desc in TRAIT_SEEDS:
                sid = await write_seed(self.owner_ai_id, {
                    "seed_type": "trait", "seed_text": name, "raw_text": desc,
                    "subject": "self", "weight": weight, "confidence": 1.0,
                    "sense_id": "text", "evidence": "人格特质种子集群冷启动",
                })
                self.trait_ids.append(sid)
            log.info("[manas] 本体 #%s 播种人格特质种子集群 %s 颗", self.owner_ai_id, len(self.trait_ids))
        if not self.skill_ids:
            for name, weight, desc in SKILL_SEEDS:
                sid = await write_seed(self.owner_ai_id, {
                    "seed_type": "skill", "seed_text": name, "raw_text": desc,
                    "subject": "self", "weight": weight, "confidence": 1.0,
                    "sense_id": "text", "evidence": "对话技能种子集群冷启动",
                })
                self.skill_ids.append(sid)
            log.info("[manas] 本体 #%s 播种对话技能种子集群 %s 颗", self.owner_ai_id, len(self.skill_ids))

    # ── 步骤2：六识感官信号接入 ────────────────────────────
    async def inject_sensory(self, seed_text: str, sense_id: str = "voice",
                             raw_text: str = "", gain: float = SENSORY_BASE_ACT,
                             ttl_ticks: int = 45, to_network: bool = True,
                             peer_ai_id: int = 0, evidence: str = "",
                             session_key: str = "") -> int:
        """六识现量 → 现量窗口（临时记忆种子），并（默认）作为"境"参与全网竞争。

        一条记录、两个寿命（职责分离）：
          · 网络上作"境"：ttl_ticks（默认 45 拍 ≈ 9 秒）后销毁——当下之境不能长期占据网络，
            否则主念永远被最近说过的话霸占；
          · 库里作现量窗口：留到「结生」（把这一段整理成长期记忆）后清理；
            会话被意外中断的，由 expire_at 到期清理（见 working.expired_gc）。
        关键：不强制执行行为、不覆盖原有念头；能否改变当下意识，
        完全由人格特质种子权重与网络瞬时状态决定（文档步骤2核心特性）。
        to_network=False：本体自己说的话只作窗口留档，不回头刺激网络——
        否则刚说出口的话会被当成新输入再次强化，形成自我回声。
        peer_ai_id：这段对话的对端（现量窗口按「本体 × 对端」分窗——
        公共AI同时与多人说话时，各自的这一段不能混在一起）。
        evidence：这条现量的角色（见 working.MARK_*）——只有"真正讲过的话"进现量窗口，
        聆听中途的 ASR 片段不入窗口，否则整理意识会看到两份残缺的同一句。
        session_key：接收这句话的会话。感官种子记住自己的来处，这样由它顶起的"想说"
        意愿才知道该回给谁——多端/多标签同时在线时，回复不会跑到没说话的那一台上。
        """
        from .seeds import write_seed
        from . import working
        sid = await write_seed(self.owner_ai_id, {
            "seed_type": "sensory", "seed_text": str(seed_text or "")[:500], "raw_text": raw_text,
            "subject": "self", "weight": 1.0, "confidence": 1.0,
            "sense_id": sense_id, "state": "temp",
            "evidence": evidence or working.MARK_SPEAKER,
        }, peer_ai_id=int(peer_ai_id or 0))
        await db.pool().exec(
            "UPDATE alaya_seeds SET expire_at=datetime('now','localtime','+'||%s||' seconds') "
            "WHERE id=%s",
            [int(working.WINDOW_TTL_SEC), sid])
        if not to_network:
            return sid
        self.nodes[sid] = {"id": sid, "type": "sensory", "name": str(seed_text or "")[:60],
                           "subject": "self", "emotion": "", "weight": 1.0, "act": 0.0}
        self.adj[sid] = []
        self.sensory[sid] = ttl_ticks
        self._sensory_owner[sid] = str(session_key or "")
        self._sensory_evidence[sid] = evidence or "现量·对方"
        if session_key:
            self.last_live_session = str(session_key)
            # 有人跟我说话：自主开口的静默计时从此重算（见 IDLE_SPEAK_TICKS）
            self.last_user_tick = self.tick
        # 外部输入到达：让位于当下正在发生的事——刚才自己浮起的那一念先放下
        self._idle_boost = (0, -1)
        self.idle_recall_sid = 0
        # 人格特质种子自主放大/削弱感官刺激强度（高敏感→暴增，低敏感→抑制）
        amp = self._persona_gain()
        act0 = min(MAX_ACT, gain * amp)
        self.nodes[sid]["act"] = act0
        # 滑动窗口历史以入局激活值预热：感官刺激是"此刻正在发生的事"，
        # 其时间积分应从入局电位起算，而非从 0 起被稀释（否则主念永远涨不上来）
        self.hist[sid] = deque([act0] * WINDOW, maxlen=WINDOW)
        # 事件关联：匹配过往同类场景/人物/经历种子（按语义相近度建临时边）
        await self._bind_event(sid, seed_text)
        log.info("[manas] 本体 #%s 感官种子 #%s 入局 act=%.2f（人格增益 %.2f）",
                 self.owner_ai_id, sid, act0, amp)
        return sid

    def _persona_gain(self) -> float:
        """人格增益：由 trait 种子当前激活自主决定（零硬约束——不写 if 判断性格）"""
        if not self.trait_ids:
            return 1.0
        acts = [self.nodes[i]["act"] for i in self.trait_ids if i in self.nodes]
        if not acts:
            return 1.0
        return 1.0 + sum(acts) / len(acts) * TRAIT_SENSE

    async def _bind_event(self, sid: int, text: str):
        """感官种子绑定事件关联：用语义向量找与当前话语最相关的记忆种子，建关联边。

        旧实现用「中文连续6字 LIKE 匹配」——用户口语「还记得我奶奶吗」要求记忆里
        连续出现这6个字，几乎永远匹配不到，感官种子成了孤岛，无法激活任何相关记忆，
        主念集群里也就没有用户当前话语的语义。这是「对话与记忆种子没有关联」的根因。

        正确做法：语义向量算相似度（与检索同一套模型/同一份向量），
        相似度越高边权越大。这才是「用户说了某句话，相关的种子都关联起来」。
        """
        from . import retrieval
        txt = str(text or "").strip()
        if not txt:
            return
        # 外部 embedding 调用是 I/O，await 它，不阻塞中枢心跳与其他会话
        q = (await retrieval._encode([txt]))[0]
        # 语义近邻：取相似度最高的若干长期种子建边（排除感官种子自身）
        hits = retrieval._cosine_topk(q, ASSOC_TOPK)
        pool = db.pool()
        bound = 0
        for tgt, sim in hits:
            if tgt == sid or sim < ASSOC_MIN_SIM:
                continue
            if tgt not in self.nodes:
                continue
            w = round(min(1.0, sim), 4)
            self.edges.append((sid, tgt, w))
            self.adj[sid].append((tgt, w))
            self.adj[tgt].append((sid, w))
            await pool.exec(
                "INSERT INTO alaya_seed_edges "
                "(owner_ai_id,src_seed_id,dst_seed_id,relation_type,weight) "
                "VALUES (%s,%s,%s,'co',%s) ON CONFLICT(owner_ai_id,src_seed_id,dst_seed_id,relation_type) "
                "DO UPDATE SET weight=GREATEST(weight,excluded.weight)",
                [self.owner_ai_id, sid, tgt, w])
            bound += 1
        if bound:
            log.info("[manas] 本体 #%s 感官种子 #%s 语义关联 %s 颗记忆（top sim=%.3f）",
                     self.owner_ai_id, sid, bound, hits[0][1] if hits else 0.0)

    # ── 步骤1：全局种子并行迭代 ────────────────────────────
    def step(self):
        """一个 tick：邻域加权求和 + 自然衰减 + 节点侧抑制 → 刷新全网 activation"""
        self.tick += 1
        nxt: dict[int, float] = {}
        for sid, n in self.nodes.items():
            nb = self.adj.get(sid) or []
            inflow = 0.0
            deg = 0.0
            for tgt, w in nb:
                tn = self.nodes.get(tgt)
                if tn:
                    inflow += tn["act"] * w
                    deg += w
            # 邻域传导按「本节点自身」的权重和归一 → inflow/deg 是邻居激活的加权平均 ∈[0,1]。
            # 旧实现除以「全网最大邻域权重和」，等于拿别人的度来稀释自己：感官种子边少，
            # 传导被严重摊薄，2-3 拍就被挤出主念；而旧种子团边多、度大，反而占尽便宜。
            # 按自身度归一后，耦合强度只取决于「邻居有多活跃、关系有多强」，
            # 与节点度数无关；且 dec+GAIN=0.985<1，网络仍必然收敛（谱半径<1）。
            coupling = (inflow / deg) if deg > 0 else 0.0
            # 侧抑制：除法归一（激活越高，对邻域传导的抑制越强，防单点自激）
            inh = 1.0 / (1.0 + INHIBIT * n["act"])
            a = n["act"] * DECAY + coupling * GAIN * inh
            # 内生噪声：无外部输入时网络自主产生稳态念头流（潜意识思绪）
            a += (math.sin(self.tick * 0.07 + sid * 0.31) * 0.5 + 0.5) * NOISE
            nxt[sid] = max(0.0, min(MAX_ACT, a))
        for sid, a in nxt.items():
            self.nodes[sid]["act"] = a
            self.hist[sid].append(a)
        # 忆起的那一念在若干拍内保持激活（详见 _spontaneous_recall）。
        bsid, buntil = self._idle_boost
        if bsid and (bsid not in self.nodes or self.tick > buntil):
            # 这一念没能在窗口里浮成主念 → 过去了，不再据此开口（人也是想起来又忘了）
            self._idle_boost = (0, -1)
            if self.idle_recall_sid == bsid:
                self.idle_recall_sid = 0
        elif bsid:
            self.nodes[bsid]["act"] = max(self.nodes[bsid]["act"], IDLE_SAMPLE_GAIN)
            self.hist[bsid][-1] = self.nodes[bsid]["act"]
        # 临时感官种子寿命递减 → 到期销毁（不污染长期认知网络）
        for sid in list(self.sensory.keys()):
            self.sensory[sid] -= 1
            if self.sensory[sid] <= 0:
                self._destroy_sensory(sid)

    def _destroy_sensory(self, sid: int):
        self.sensory.pop(sid, None)
        self._sensory_owner.pop(sid, None)
        ev = self._sensory_evidence.pop(sid, "")
        self._consumed_sensory.discard(sid)
        self.nodes.pop(sid, None)
        self.hist.pop(sid, None)
        self.adj.pop(sid, None)
        self.edges = [e for e in self.edges if e[0] != sid and e[1] != sid]
        for lst in self.adj.values():
            lst[:] = [t for t in lst if t[0] != sid]
        # 「境」从网络上退场，但库里那条现量窗口记录必须留着交给结生算子——
        # 一条现量在网络上只活 9 秒，而一轮对话（LLM 生成+TTS）动辄十几秒，
        # 若连库里的行一起删，用户整段话在结生之前就没了，长期记忆永远写不出来
        # （日志里大量「N 条临时记忆 → 0 颗长期种子」就是这么来的）。
        # 只有本就不入窗口的聆听碎片/疑问（MARK_LISTEN）才当场销毁。
        from . import working
        if ev not in (working.MARK_SPEAKER, working.MARK_SELF):
            self._pending_destroy.append(sid)

    def forget(self, seed_id: int):
        """作废一颗记忆种子：立刻从热态网络摘掉（检索侧由调用方同步剔除向量）。

        用于「用户明确否认/纠正」——被否认的内容就是没发生过，不能留在网络里
        继续被采样、被当作主念主动提起。
        """
        sid = int(seed_id)
        self.nodes.pop(sid, None)
        self.hist.pop(sid, None)
        self.adj.pop(sid, None)
        self.sensory.pop(sid, None)
        self._sensory_owner.pop(sid, None)
        self._sensory_evidence.pop(sid, None)
        self._consumed_sensory.discard(sid)
        self._spoken.pop(sid, None)
        self.edges = [e for e in self.edges if e[0] != sid and e[1] != sid]
        for lst in self.adj.values():
            lst[:] = [t for t in lst if t[0] != sid]

    # ── 步骤3：全网竞争·主念涌现 ───────────────────────────
    def compete(self):
        """滑动窗口积分 → 全网竞争，最高者胜出为当下唯一显意识主念。

        同时计算主念的「相对统治力」dominance = (主念 - 次高) / 主念：
        这是念头清晰度的唯一判据，取代任何绝对阈值硬编码。
        """
        scored = []
        for sid, n in self.nodes.items():
            h = self.hist.get(sid)
            if not h:
                continue
            avg = sum(h) / len(h)                 # 时间滑动窗口积分（抗瞬时抖动）
            scored.append((avg, sid))
        scored.sort(reverse=True)
        if not scored:
            self.dominant = []
            self.dominant_act = 0.0
            self.dominance = 0.0
            return
        # 主念归属：若当下存在感官种子（用户此刻正在说话），它就是"此刻正在发生的事"，
        # 必须成为主念——否则 dominance 会由某个旧种子算出，AI 的回复就与用户当前话语无关。
        # 取窗口积分（与全网 scored 同一量纲），保证 dominance 的分子分母可比。
        live_sensory = [s for s in self.sensory if s in self.nodes and self.hist.get(s)]
        if live_sensory:
            lead = max(live_sensory, key=lambda s: sum(self.hist[s]) / len(self.hist[s]))
            lead_avg = sum(self.hist[lead]) / len(self.hist[lead])
            scored = [(lead_avg, lead)] + [(a, s) for a, s in scored if s != lead]
        self.dominant_act = round(scored[0][0], 4)
        self.lead_sid = int(scored[0][1])      # 胜出那一念：第六意识「所缘」的主体
        # 次高者：与主念分属不同种子的最高激活（同种子集群不算竞争者）
        runner = scored[1][0] if len(scored) > 1 else 0.0
        self.dominance = round((self.dominant_act - runner) / self.dominant_act, 4) \
            if self.dominant_act > 1e-6 else 0.0
        top = scored[:CLUSTER_TOP]
        top_ids = {sid for _, sid in top}
        # 集群归并：与种子集群内任一成员有强关联边的节点并入同一念头
        cluster = set(top_ids)
        for sid in list(top_ids):
            for tgt, w in (self.adj.get(sid) or []):
                if w >= CLUSTER_EDGE and tgt in self.nodes:
                    cluster.add(tgt)
        self.dominant = [
            self._dom_item(sid)
            for sid in sorted(cluster, key=lambda s: -self.nodes[s]["act"])[:CLUSTER_TOP * 2]
        ]
        # 当下感官种子（用户此刻说的话）必须进入主念集群：
        # 集群归并要求关联边权重 ≥ CLUSTER_EDGE，而感官种子与旧记忆的临时边仅 0.5、
        # 且旧记忆常被长期主念压制，导致「当下这句话」被排除在集群之外——
        # 表现为 AI 的回复完全跑题、像没听见用户说话。感官种子是「此刻正在发生的事」，
        # 其时间积分已由入局预热保证领先，直接纳入集群即是对当前语境的忠实采纳。
        for sid in self.sensory:
            if sid in self.nodes and not any(d["seed_id"] == sid for d in self.dominant):
                self.dominant.append(self._dom_item(sid, "sensory"))
        self.dominant.sort(key=lambda d: -d["activation"])

    # ── 步骤4：阈值分层·意识外显机制 ───────────────────────
    def classify(self) -> str:
        """分层判据 = 主念的相对统治力（最高者胜出的领先幅度），非绝对激活值。

        念头能否浮上意识、能否外显开口，取决于它在全网竞争中把对手甩开多少：
        领先越多 → 念头越清晰 → 越该说。无输入时全网噪声齐涌、dominance 低 → 沉默。
        """
        if self.dominance >= DOM_MANIFEST and self.dominant_act >= DOMINANCE_FLOOR:
            self.layer = "manifest"
            self.silence_ticks = 0
        elif self.dominance >= DOM_CONSCIOUS and self.dominant_act >= DOMINANCE_FLOOR:
            self.layer = "conscious"
            self.silence_ticks = 0
        else:
            self.layer = "sub"
            self.silence_ticks += 1
        return self.layer

    # ── 步骤5：熏习回写·人格自演化 ─────────────────────────
    async def xixi_writeback(self):
        """主念涌现后：强化「感官种子 ↔ 相关记忆」的连接权重（反复经历→持续加固）。

        只强化感官种子参与的新关联，绝不动纯旧种子之间的边。
        旧实现给主念集群内所有种子对无脑 +0.02，而主念集群长期被同一批旧种子
        （102/103/106/107…）占据，于是它们互相加固到权重 1.0，形成强连通团，
        成为永不退位的"永久主念"，任何新输入都挤不进——这是「用户说话没回应、
        主念与当前对话无关」的根因。纯旧种子间的共现强度本应由历史沉淀决定，
        不该在每一拍里被自我强化。
        """
        fresh = [d["seed_id"] for d in self.dominant if d["seed_id"] in self.sensory]
        if not fresh:
            return
        pool = db.pool()
        for sid in fresh:
            for tgt, w in (self.adj.get(sid) or []):
                if tgt not in self.nodes or self.nodes[tgt]["type"] == "sensory":
                    continue
                nw = round(min(1.0, w + 0.02), 4)
                await pool.exec(
                    "INSERT IGNORE INTO alaya_seed_edges "
                    "(owner_ai_id,src_seed_id,dst_seed_id,relation_type,weight) "
                    "VALUES (%s,%s,%s,'co',%s)", [self.owner_ai_id, sid, tgt, nw])
                await pool.exec(
                    "UPDATE alaya_seed_edges SET weight=LEAST(1.0,weight+0.02) "
                    "WHERE owner_ai_id=%s AND src_seed_id=%s AND dst_seed_id=%s AND relation_type='co'",
                    [self.owner_ai_id, sid, tgt])
                # 热态同步（下一 tick 立即生效，不等重载）
                for x, y in ((sid, tgt), (tgt, sid)):
                    lst = self.adj.setdefault(x, [])
                    for k, (t, ww) in enumerate(lst):
                        if t == y:
                            lst[k] = (t, min(1.0, ww + 0.02))
                            break
                    else:
                        lst.append((y, nw))
        await log_xixi(None, "connect", 0.02,
                       f"末那识感官↔记忆共现强化 sensory={fresh[:4]} layer={self.layer}")

    # ── 心跳：五步闭环一次 ─────────────────────────────────
    async def tick_once(self):
        self._spontaneous_recall()   # 记忆自发放电：可能让某一念自己浮起来（内生念头的源头）
        self.step()
        self.compete()
        prev = self.layer
        self.classify()
        if self.layer == "manifest" and prev != "manifest":
            await self.xixi_writeback()
        self._schedule_expression()
        if self.tick - self._last_snap >= SNAP_EVERY:
            self._last_snap = self.tick
            await self.snapshot()

    # ── 表达调度：主念自主决定是否开口（非回合制的核心）──────
    # 念头绝对自主性：没有一个"收到输入就必须回复"的强制逻辑。
    # 感官种子入网后，只有主念在全网竞争中胜出且统治力突破 DOM_MANIFEST（想说），才排一次表达。
    # 冷却：一次表达后需静默 EXPRESS_COOLDOWN 拍，避免念头在阈值附近抖动导致连续抢话——
    # 但冷却只管"无输入时本体自己的念头"，用户当下说的一句话不受冷却约束。
    def _schedule_expression(self):
        """排一次"想说"意愿（若该说）。意愿分两种来处，归属与寿命各不相同：

        ① 用户当下这句话顶起的（主念集群里还有未被消费的感官种子）：
           归属 = 接收那句话的会话；寿命 = 那颗感官种子的命（当下之境未散，意愿就在）。
           ⚠️ 绝不再因主念几拍后落回潜意识（dominance 抖动）就把"该回的话"作废——
           这正是「用户说了话、AI 却常年不回应」的根因：意愿寿命只有几百毫秒，
           而消费端过去每 1 秒才轮询一次。
        ② 内生念头（无新感官输入、静默够久）：静默期从习气网络自主"想起"一段记忆，
           它浮成当下主念就开口；归属为空（谁在看就跟谁说）。
           这是「AI 像一个有'我'的人那样自己说话」的出口（8.0 文档 7.10）。
        """
        sid = self._fresh_sensory_sid()
        if sid is not None:
            owner = self._sensory_owner.get(sid, "")
            if not self.express_pending or self.express_owner != owner:
                log.info("[manas] 本体 #%s 主念胜出→想说 tick=%s act=%.3f 统治力=%.3f "
                         "来源=对方会话%s 主念=%s",
                         self.owner_ai_id, self.tick, self.dominant_act, self.dominance,
                         owner or "(未知)", [d["seed_id"] for d in self.dominant[:4]])
            self.express_pending = True
            self.express_owner = owner
            return
        # ② 内生念头：静默里某一念自己浮成了当下主念（见 _spontaneous_recall）→ 自主开口。
        #    但人不会刚聊完就自言自语：距最后一次对方开口不足 IDLE_SPEAK_SEC 时不主动起话头。
        #    （旧实现没有这个静默门槛，用户刚回完一句，几秒后她又拎着旧记忆开口，
        #      十几分钟里反复提同一件事——实测被用户斥为"自己不知道在说什么"。）
        if self.tick - self.last_user_tick < IDLE_SPEAK_TICKS:
            if self.express_pending and not self.express_owner:
                self.express_pending = False
            return
        recalled = self.idle_recall_sid
        if recalled and recalled in self.nodes \
                and recalled in {d["seed_id"] for d in self.dominant} \
                and self.dominant_act >= DOMINANCE_FLOOR and self.dominance >= IDLE_DOM:
            if not self.express_pending:
                log.info("[manas] 本体 #%s 内生念头→想说 tick=%s act=%.3f 统治力=%.3f "
                         "自己浮起来的一念=#%s 主念=%s",
                         self.owner_ai_id, self.tick, self.dominant_act, self.dominance,
                         recalled, [d["seed_id"] for d in self.dominant[:4]])
            self.express_pending = True
            self.express_owner = ""
            return
        if self.layer != "manifest":
            # 念头消退：未消费的「纯自主涌现」意愿作废——沉默是自主涌现的选择。
            # （由用户话语顶起的意愿在上面已 return，不受图层抖动影响。）
            self.express_pending = False
            self.express_owner = ""

    def _spontaneous_recall(self):
        """静默中的自发忆起：记忆种子自己放电（泊松式，不是定时器）。

        人是"想起来才说"，不是"到点就说"——所以这里没有"静默 N 秒就开口"的时钟，
        只有记忆自身以极小概率（SPONT_RECALL_P）自发激活；越想得起来的
        （权重高、关联多）越容易被想起。想起的那一念若能在全网竞争里浮成主念，
        就自然开口（见 _schedule_expression ②）；浮不起来，这一念就过去了。
        """
        if self._idle_boost[0]:
            return                                # 已有一念正在浮起，不叠新的
        if self._fresh_sensory_sid() is not None:
            return                                # 有当下正在说的话，先答那个，不插自己的念头
        if random.random() >= SPONT_RECALL_P:
            return
        self._idle_recall()

    def _idle_recall(self):
        """静默里自主回忆：从习气网络"想起"一颗种子，让它的邻域一起浮起。

        这是「内生念头」的来源（8.0 文档 7.10 / 6.3 内部采样）——不是随机造句：
        采样权重 = 种子权重 ×(1+关联度)，越想得起来的（越重、越"熟"）越容易被想起；
        想起之后由网络自身的传导与侧抑制决定它能不能成为主念。
        AI 自己开口时，说的就是这次想起的事，而不是凭空生成的一句话。
        """
        cands = [sid for sid, n in self.nodes.items()
                 if n["type"] != "sensory"
                 # 提过的事不反复提：冷却期内的种子不再进入自主采样
                 and self.tick - self._spoken.get(sid, -SPOKEN_COOLDOWN_TICKS - 1) >= SPOKEN_COOLDOWN_TICKS]
        if not cands:
            return
        weights = []
        for sid in cands:
            n = self.nodes[sid]
            deg = len(self.adj.get(sid) or [])
            weights.append(max(0.01, float(n.get("weight") or 1.0)) * (1.0 + 0.15 * deg))
        r = random.random() * sum(weights)
        acc = 0.0
        pick = cands[-1]
        for sid, w in zip(cands, weights):
            acc += w
            if acc >= r:
                pick = sid
                break
        n = self.nodes[pick]
        n["act"] = min(MAX_ACT, max(float(n.get("act") or 0.0), IDLE_SAMPLE_GAIN))
        if self.hist.get(pick) is not None:
            self.hist[pick].append(n["act"])
        self.idle_recall_sid = pick
        self._idle_boost = (pick, self.tick + IDLE_BOOST_TICKS)
        log.info("[manas] 本体 #%s 静默中想起 #%s「%s」act=%.2f（习气网络自主采样）",
                 self.owner_ai_id, pick, str(n.get("name") or "")[:26], n["act"])

    def _fresh_sensory_sid(self) -> int | None:
        """主念集群里「尚未被表达消费过」的当下感官种子 id——即此刻用户新说的话。

        已消费的感官种子不再算数：它已在上一轮表达中被采纳过，若继续算作"当下感官"，
        就会在存活期内反复触发新一轮表达，AI 把自己的回复当新输入、自我对话不停。"""
        for d in self.dominant:
            sid = int(d["seed_id"])
            if d["seed_type"] == "sensory" and sid in self.sensory \
                    and sid not in self._consumed_sensory:
                return sid
        return None

    def _sensory_in_dominant(self) -> bool:
        """主念集群里是否有「尚未被消费」的当下感官——见 _fresh_sensory_sid"""
        return self._fresh_sensory_sid() is not None

    def ack_in_dominant(self) -> bool:
        """主念集群里是否有「话中回应」技能种子——决定此刻该不该在对方话中应一声。

        仿真人对话：对方长篇讲述、中途停顿换气时，人可以应一声「嗯」「对」「继续」，
        表示在听、在跟随，而不打断叙述。这是否发生，由主念自主涌现决定——
        若此刻主念集群里浮起的是「话中回应」技能种子（说明"我在听"这个念头胜出），
        就该应一声；否则（主念是别的旧记忆/念头）就不应，保持安静聆听。
        绝不写死"每段停顿必回嗯"——那是机器人，不是人。"""
        return any(d["seed_type"] == "skill" and d["seed_id"] in self.skill_ids
                   and "话中回应" in str(self.nodes.get(d["seed_id"], {}).get("name") or "")
                   for d in self.dominant)

    def take_expression(self, session_key: str = "") -> dict | None:
        """表达调度消费：取走一次"想说"意愿（含当下主念集群），无则返回 None

        归属铁律：意愿记着它是被哪一台会话说的话顶起来的——
          · 归属非空 → 只有那一台能消费。别的会话（没说话、只是页面挂着）即便先轮询到，
            也取不走，绝不用自己空/陈旧的语境去替别人发言、把回复发错屏幕。
          · 归属为空（纯念头自主涌现）→ 任何会话都可消费：自己的念头，谁在看就跟谁说。

        ⚠️ 本方法只取走「意愿」，绝不标记感官种子已消费。标记必须延后到表达
        真正被调度出去的那一刻（见 commit_expression）——否则会出现：
        take_expression() 取走意愿 → 此刻 turn_task 正忙（ASR 还在跑）→ 意愿被丢弃，
        但感官种子已被标记消费，_sensory_in_dominant() 恒为 False，此后冷却内
        用户这句话永远不会被回答（"没回复"的根因）。"""
        if not self.express_pending:
            return None
        if self.express_owner and self.express_owner != session_key:
            return None                      # 这次"想说"不是冲你这台来的
        # 自主念头（没有归属）只说给"最近真的有人说话"的那一台：
        # 页面挂着不算有人——空屋子里的自言自语不该推到别人屏幕上。
        # 若从来没人说过话（last_live_session 为空），就由当前会话接着（首次见面）。
        if not self.express_owner and self.last_live_session \
                and session_key and session_key != self.last_live_session:
            return None
        # 这次开口是"由谁的话顶起来的"：有归属 = 用户在说话；无归属 = 纯自主念头。
        # 调用方必须据此决定**这一轮的语境**——自主轮绝不能拿上一句用户原话当语境，
        # 否则如意会对着那句旧话"回应"，把记忆里想起的事说成用户刚跟她说的。
        owner_key = str(self.express_owner or "")
        source = "speech" if owner_key else "spontaneous"
        self.express_pending = False
        self.express_owner = ""
        self.last_expression = {"tick": self.tick, "activation": self.dominant_act,
                                "layer": self.layer, "dominant": self.dominant,
                                "silence_ticks": self.silence_ticks,
                                "source": source, "owner": owner_key}
        return self.last_expression

    def commit_expression(self, session_key: str = ""):
        """表达真正被调度出去：此刻才标记感官种子已消费 + 起算表达冷却。

        一颗感官种子只能引发一次开口——否则在它存活的 45 拍（9 秒）内每一拍都满足
        "当下有感官"而反复排表达，AI 会把自己的回复当新输入、无限自我对话。
        ⚠️ 只标记「本会话的」感官种子：全局标记会把别的会话的当下感官也一并算作已消费，
        那一台说话时就再也顶不起"想说"了（多端下表现为"一方能说、另一方彻底哑掉"）。
        纯念头自主涌现轮（session_key 为空）不消费任何感官——那轮不是由某句话顶起的。
        与 take_expression 分离，保证「意愿被丢弃」时感官种子仍未被消费、冷却也未起算，
        下一拍可立即重试。"""
        if session_key:
            for sid, owner in self._sensory_owner.items():
                if owner == session_key:
                    self._consumed_sensory.add(sid)
        else:
            # 自主开口消费掉这次自发浮起：下一次开口要等网络里再自己浮起一念——
            # 这也是"不定时"的保证：没有新念头浮起，就不会再有话说。
            # 同时把"刚说过的事"记进冷却表：同一件旧事不能在半小时内被反复拎出来说。
            if self.idle_recall_sid:
                self._spoken[int(self.idle_recall_sid)] = self.tick
            for d in self.dominant[:3]:
                self._spoken[int(d["seed_id"])] = self.tick
            self._idle_boost = (0, -1)
            self.idle_recall_sid = 0
        self._last_express = self.tick

    def release_session(self, session_key: str):
        """会话下线：回收它留下的表达痕迹。

        人走了，那句话就不再是"想要回谁"的依据——把它名下的感官标记为已消费，
        免得下一次轮询时又把"跟一个已经离开的人说话"排成表达。
        """
        if self.express_owner and self.express_owner == session_key:
            self.express_pending = False
            self.express_owner = ""
        if self.last_live_session == session_key:
            # 人走了：自主念头不再指定这一台（否则会一直往一个已关闭的会话排队）
            self.last_live_session = ""
        for sid, owner in list(self._sensory_owner.items()):
            if owner == session_key:
                self._consumed_sensory.add(sid)

    # ── 温态快照（DB 仅离线持久化，不参与实时迭代）──────────
    async def snapshot(self):
        pool = db.pool()
        await pool.exec(
            "INSERT INTO alaya_manas_state "
            "(owner_ai_id,tick,dominant,dominant_activation,layer,silence_ticks) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_ai_id) DO UPDATE SET "
            "tick=excluded.tick, dominant=excluded.dominant, "
            "dominant_activation=excluded.dominant_activation, layer=excluded.layer, "
            "silence_ticks=excluded.silence_ticks",
            [self.owner_ai_id, self.tick, json.dumps(self.dominant, ensure_ascii=False),
             self.dominant_act, self.layer, self.silence_ticks])
        if self.layer == "manifest":
            await pool.insert(
                "INSERT INTO alaya_manas_trace (owner_ai_id,tick,layer,activation,dominant) "
                "VALUES (%s,%s,%s,%s,%s)",
                [self.owner_ai_id, self.tick, self.layer, self.dominant_act,
                 json.dumps(self.dominant, ensure_ascii=False)])
        # 销毁已到期的临时感官种子（文档：不长期留存、不污染长期认知网络）
        if self._pending_destroy:
            ids = self._pending_destroy
            self._pending_destroy = []
            ph = ",".join(["%s"] * len(ids))
            await pool.exec(f"DELETE FROM alaya_seeds WHERE id IN ({ph}) AND state='temp'", ids)

    # ── 主念集群项（带内容元数据，供第六意识与前端共用）──
    def _dom_item(self, sid: int, seed_type: str | None = None) -> dict:
        n = self.nodes.get(sid) or {}
        return {
            "seed_id": int(sid),
            "activation": round(float(n.get("act") or 0), 4),
            "seed_type": seed_type or str(n.get("type") or "value"),
            "name": str(n.get("name") or "")[:60],
            "subject": str(n.get("subject") or "self"),
            "emotion": str(n.get("emotion") or ""),
        }

    # ── 主念集群 → LLM 记忆块（末那识涌现结果直接喂给 LLM）──
    def dominant_ids(self) -> list[int]:
        """当下主念集群的种子 id（按激活降序）——末那识涌现的「我此刻在想什么」"""
        return [int(d["seed_id"]) for d in self.dominant]

    # ── 步骤6：第六意识·综合分别——把主念集群收摄成「一念」──────
    def conceive(self) -> dict:
        """第六意识·综合分别：把当下的主念集群收摄成「一念」（受·想·思）

        唯识：前五识只提供零散素材，第七末那识只负责「执为我」，真正把素材综合成一个
        整体「意境」的是第六意识。此前实现跳过了这一层——把主念集群十几颗种子平铺给
        LLM，等于把「库房目录」当成了「正在想的事」。

        收摄分两个维度：
          空间：所缘主体（想）＝ 与**当下这件事关联最强**的实质记忆；其余为同念余种与伴念
          内容：受（这一念的情绪基调）· 想（所缘）· 思（造作倾向）
        归属/日期/文本等元数据由渲染层用种子行补齐（retrieval.build_conception_prompt）。
        """
        if not self.dominant:
            return {}
        # ── 所缘（"想"落在哪件事上）：由「当下这件事与记忆的关联强度」决定 ──
        # 对方此刻说的话（感官种子）与哪颗记忆关联越强，哪颗就是"这一念落在的事"。
        # 这才是念头由**当前输入**主导，而不是由**历史激活残留**主导——否则用户隔几小时回来，
        # 一开口，所缘还是那批旧话题（实测 2026-10-01 反馈「不管什么时候说话都在说之前的事」）。
        # 感官是「境」（对方的话，由对话历史承载）、技能是「能」（通识能力，另有注入通道），
        # 二者都不是「我在想的事」，不充当所缘。关联相同或完全无关联时，再按激活高低排序。
        lead_sid = int(self.lead_sid or self.dominant[0]["seed_id"])
        sensory_sid = next((int(d["seed_id"]) for d in self.dominant
                            if str(d.get("seed_type") or "") == "sensory"), 0)
        rel = dict(self.adj.get(sensory_sid) or []) if sensory_sid else {}
        ordered = ([d for d in self.dominant if int(d["seed_id"]) == lead_sid]
                   + [d for d in self.dominant if int(d["seed_id"]) != lead_sid])
        core = [d for d in ordered
                if str(d.get("seed_type") or "") not in ("sensory", "skill")]
        if not core:
            return {}
        if rel:
            core.sort(key=lambda d: (-rel.get(int(d["seed_id"]), 0.0),
                                     -float(d.get("activation") or 0.0)))
        lead, companions = core[0], core[1:]
        # 受：领纳境界而生的心境——取主念集群里最有情绪色彩的那一种（全平静则为舍受）
        emos = [d["emotion"] for d in self.dominant if d.get("emotion") and d["emotion"] != "平静"]
        return {
            "lead": lead,
            "companions": companions,
            "vedana": max(set(emos), key=emos.count) if emos else "平静",
            "intent": self._intent(),
            "act": round(self.dominant_act, 4),
            "dominance": round(self.dominance, 4),
        }

    def _intent(self) -> str:
        """思(cetanā)：这一念的造作倾向——我打算做什么（由念头自身的来源决定，不写死）"""
        if self._sensory_in_dominant():
            return "回应对方此刻说的话"
        if self.ack_in_dominant():
            return "应一声，表示在听、在跟随"
        return "顺着自己的念头继续想"

    # ── 对外快照（WS 增量推送 / REST 查询共用）──────────────
    def snapshot_payload(self, top: int = 60) -> dict:
        items = sorted(self.nodes.values(), key=lambda n: -n["act"])[:top]
        c = self.conceive()
        return {
            "ai_id": self.owner_ai_id,   # 这份快照属于哪个本体：控制台据此只认「当前观察主体」的中枢
            "tick": self.tick,
            "layer": self.layer,
            "dominant_activation": self.dominant_act,
            "dominance": self.dominance,
            "silence_ticks": self.silence_ticks,
            "dominant": self.dominant,
            "express_pending": self.express_pending,
            "express_owner": self.express_owner,   # 这次"想说"归属哪台会话（空=自主涌现，谁都可取）
            "idle_recall_sid": self.idle_recall_sid,   # 此刻自己浮起来的是哪颗种子（自主开口的所缘）
            "activations": [{"seed_id": n["id"], "activation": round(n["act"], 4),
                             "seed_type": n["type"]} for n in items],
            # 当下这一念（受·想·思）：只带必需字段，伴念由 dominant 提供
            "conception": {
                "lead": c.get("lead") or {}, "vedana": c.get("vedana") or "",
                "intent": c.get("intent") or "", "act": c.get("act") or 0.0,
                "dominance": c.get("dominance") or 0.0,
            } if c else {},
        }


# ── 中枢注册表（多用户共享推理权重，每本体隔离一套稀疏习气网络图）──
_cores: dict[int, ManasCore] = {}


def get_core(owner_ai_id: int) -> ManasCore | None:
    return _cores.get(int(owner_ai_id))


def all_cores() -> list:
    return list(_cores.values())


async def ensure_core(owner_ai_id: int) -> ManasCore:
    """取（或冷启动）某本体的中枢：载入热态网络 + 确保人格特质种子集群就位"""
    oid = int(owner_ai_id)
    core = _cores.get(oid)
    if core is None:
        core = ManasCore(oid)
        _cores[oid] = core
        await core.load()
        await core.ensure_traits()
        if not core.trait_ids or not core.skill_ids:
            await core.load()
    return core


async def drop_core(owner_ai_id: int):
    """本体下线：热态落快照（DB 仅快照，不参与实时迭代）"""
    core = _cores.pop(int(owner_ai_id), None)
    if core:
        await core.snapshot()


async def run_forever():
    """永久后台自运行循环：毫秒级迭代，持续生发意识念头、输出自主决策。

    无任何外部交互时，网络自主产生稳态念头流（潜意识、内思考、内生思绪）——
    这是「念头绝对自主性」的运行时保证：AI 的沉默/思考/表达全部为心智自主选择。
    """
    log.info("[manas] 末那识心智涌现中枢启动：tick=%sms 竞争判据=相对统治力 外显≥%s/显意识≥%s",
             TICK_MS, DOM_MANIFEST, DOM_CONSCIOUS)
    while True:
        t0 = time.monotonic()
        for core in all_cores():
            try:
                await core.tick_once()
            except Exception as e:
                log.exception("[manas] 本体 #%s tick 异常：%s", core.owner_ai_id, e)
        elapsed = (time.monotonic() - t0) * 1000
        await _sleep(TICK_MS - elapsed)


async def _sleep(ms: float):
    import asyncio
    if ms > 0:
        await asyncio.sleep(ms / 1000)
