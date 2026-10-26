<p align="center">
  <img src="assets/banner.svg" alt="Alaya Memory Engine" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-e8c88a?style=flat-square" alt="license"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-6fd3ff?style=flat-square" alt="python">
  <img src="https://img.shields.io/badge/storage-SQLite%20single--file-8fa4c8?style=flat-square" alt="sqlite">
  <img src="https://img.shields.io/badge/models-OpenAI--compatible-8fa4c8?style=flat-square" alt="openai compatible">
  <img src="https://img.shields.io/badge/deps-no%20vector%20DB-8fa4c8?style=flat-square" alt="no vector db">
  <img src="https://img.shields.io/badge/PRs-welcome-e8c88a?style=flat-square" alt="prs">
</p>

<h3 align="center">记忆不是被检索出来的，是此刻涌现出来的。</h3>

<p align="center">
一个把「记忆」当作第一性原理的 AI 心智内核：<br>
AI 的回答不是从文档切片里拼出来的，而是由它<strong>自己的记忆种子</strong>在习气网络中竞争，<br>
胜出的「这一念」再说出口。
</p>

<p align="center">
  <a href="https://dev.nice-ai.cn/alaya/"><img src="https://img.shields.io/badge/%E5%9C%A8%E7%BA%BF%E4%BD%93%E9%AA%8C-LIVE%20DEMO-e8c88a?style=for-the-badge" alt="Live Demo"></a>
</p>

> **在线 Demo** → <https://dev.nice-ai.cn/alaya/>
>
> 作者部署的实例，可以先看实况再决定要不要自己跑。登录方式为**手机号 + 短信验证码**（需真实可用手机号）。
>
> ⚠️ 这是真实运行的实例：你输入的内容会真的写进它的记忆库，也会调用真实模型。
> **请不要输入个人隐私**。Demo 可能随时调整或关闭，长期使用请本地部署。

---

## 三句话说完它是什么

- **不是 RAG，是记忆**：没有"知识库 + 相似度 top-k 塞 prompt"。记忆是种子，检索只是召回，真正决定说什么的是**习气网络的竞争结果**。
- **不是无状态，是会变的**：每句话都会**熏习**网络——召回即复熏、共现即建边、久不用则半衰。内容永不改，权重天天变。
- **不是一份记忆，是每个 AI 各有各的**：每个 AI 本体有自己的阿赖耶。同一件事，你有你的第一人称，它有它的第一人称。

<table>
<tr><td width="50%" valign="top">

**常见方案**

```
用户提问
   ↓
embed(问题)
   ↓
向量库 top-k 文档切片
   ↓
"根据以下资料回答…" → LLM
   ↓
回答（每次都从零开始）
```

</td><td width="50%" valign="top">

**阿赖耶**

```
用户说话
   ↓
感官种子（临时，还没"记住"）
   ↓
习气网络 200ms 一拍地跑
   ↓
主念集群胜出（它决定要不要开口）
   ↓
召回 · 我识 · 生成 · 出声
   ↓
结生（长期种子）→ 熏习（网络变了）
```

</td></tr>
</table>

---

## 架构

<p align="center">
  <img src="assets/architecture.svg" alt="architecture" width="100%">
</p>

一次交互的完整闭环，全部落在 `server/` 这几个文件里：

| 环节 | 代码 | 做什么 |
|---|---|---|
| 感知 | `index.py` `_sense_from_audio/_sense_from_text` | 16k PCM / 文字 / 画面 → 感官种子 |
| 习气网络 | `manas.py` `step/tick` | 200ms 一拍：邻域传导、侧抑制、自然衰减、阈值分层 |
| 主念涌现 | `manas.py` `conceive` | 相对统治力胜出 → 「受·想·思」收摄成当下这一念 |
| 召回 | `retrieval.py` `recall` | 向量反射 + 时间近因 + 高权重常青，三路融合后沿边整族浮起 |
| 我识 | `selfmodel.py` | core 本体我 / relation 关系我 / mood 当下我，三层分开存 |
| 表达 | `llm.py` + `index.py` `_run_turn` | 外部 LLM 流式 → 分句 → TTS 流式，边生成边合成边下发 |
| 结生 | `dualview.py` | 一段对话一次看全 → 抽出中性事实 → 确定性映射成两个视角的种子 |
| 熏习 | `xixi.py` | 共现建边、关联建边、读即写、每日半衰（含过度召回惩罚） |

---

## 核心机制

### 1. 种子，而不是文档切片

一颗种子 = 一句话一条事实，带五维指纹（语义 / 时间 / 人物 / 情绪 / 场景）+ 来源与证据。

```sql
alaya_seeds(
  seed_type, seed_text, raw_text,        -- 语义（不可变）
  owner_ai_id, peer_ai_id, subject,      -- 归属：谁记得、关于谁
  seed_date, time_fuzzy,                 -- 时间（相对词在写入时就换算成绝对日期）
  person_id, scene, life_stage,          -- 人物 / 场景 / 人生阶段
  emotion_tone_id, emotion_word,         -- 情绪
  confidence, weight, recall_count,      -- 权重三件套（熏习唯一作用面）
  state, superseded_by, episode_id,      -- 修正链 + 事件束
  expire_at                              -- 临时记忆的生命周期
)
```

**铁律**：种子内容不可变。要改？写新种子 + `corr` 边 + 旧种子 `state=superseded`。这样"它曾经怎么以为"永远可追溯。

### 2. 现量 → 结生：两段节律

人不是边听边记笔记的。说完的话先在**现量窗口**里活一会儿（`state=temp`，不进长期网络、不建向量），
等这段话说完了、静下来了，才**一次看全**抽成长期记忆。

> 为什么必须这样：逐轮抽取时模型只看到一轮，同一件事被讲三遍就抽出三条互不相识的碎片，
> 回忆时拼不出全貌。一次看全，"同一件事只出一条完整事实"才真正落得下去。

### 3. 双视角结生

同一件事，抽出**视角无关的中性事实**，再用确定性映射生成两份：

- 用户本体：「我」的经历 → `subject=self`，第一人称
- AI 本体：「我」的经历 → `subject=self`，第一人称（归属相反）

绝不允许把同一段文本原样写进两份——那是复制，不是双视角。

### 4. 熏习：五类算子，只动权重

| 算子 | 触发 | 效果 |
|---|---|---|
| `strengthen_cooccurrence` | 同轮写入多颗种子 | 两两建边 0.3，已存在则 +0.02（封顶 1.0） |
| `associate_seeds` | 新种子入网 | 与语义近邻（≥0.55，top5）建边，并给邻居共同加权 |
| `reinforce_on_recall` | 被召回 | `0.1 × 相关度 / (1 + recall_count/4)`——第一次想起最鲜明，第 50 次几乎不再加深 |
| `decay_half_life` | 每日 04:00 | `weight = 0.5^(未唤醒天数/30) / (1 + recall_count/30)` |
| `supersede` | 被纠正 | 旧种子降权 0.5 + 标记 superseded，修正链可追溯 |

`skill` 类种子（"一次只问一个问题"这类对话基本能力）**豁免半衰**——通识能力不该因为久未使用而淡出。

### 5. 阈值是它自己的历史分位，不是常数

意识候选的判据不是拍脑袋写的 `if I > 0.7`，而是：

```
θ = Quantile_0.8(最近 400 拍自己的意象强度)
```

经历越多，阈值越贴合它自己的分布：变强则门抬高，变弱则门降低。
分布还没成形（样本不足）时，一切成形的意象都是候选——这是 q 分位在样本不足时的数学正解，不是兜底。

### 6. 忆点星光：记忆是可以被"补全"的

记忆不是独白。系统把种子按人物 / 事件束铺成星图（`/amnesia/`），
可以生成一条链接发给当事人：**"这段记忆里还有你，要不要一起补上另一半？"**

亲友补的那份不是写到别处，而是写进**同一个事件束**（`episode_id`）——两份记忆在同一个盆地相遇。
星图上那颗星，从此从暗变亮。

---

## 快速开始

```bash
git clone https://github.com/imcosy/alaya_v2.git
cd alaya_v2
./run.sh
```

首次运行会建 `.venv`、复制 `config.example.json` → `config.json`、建好 `data/alaya.db`。
浏览器打开 `http://127.0.0.1:9890/`，**注册一个本地账号就能用**（不需要短信、不需要第三方登录）。

> 没填 key 也能启动，只是语音和回复不可用；顶部状态会显示"未配置"。

### 需要准备四类接口（都是 OpenAI 兼容）

| 能力 | 端点 | 备注 |
|---|---|---|
| LLM | `POST {base}/chat/completions` | 必须支持 `stream: true` |
| ASR | `POST {base}/audio/transcriptions` | 上传 WAV，返回 `{"text": "..."}` |
| TTS | `POST {base}/audio/speech` | 返回 `wav` 或裸 `pcm`（16bit 单声道） |
| Embedding | `POST {base}/embeddings` | 记忆召回的唯一语义坐标 |

**最低配置**：LLM + Embedding 就能文字对话。补齐 ASR/TTS 才有语音。

> OpenAI / DeepSeek / 阿里百炼 / 硅基流动 / 火山方舟…… 只要提供上述端点，填 `base_url` 即可。
> 换 Embedding 模型会自动丢弃旧向量并全量重建，种子文本不受影响。

---

## 配置

两种方式等价，改完**立即生效**，不用重启：

1. **页面里改**：用户图标 → 我的资料 → ⚙️ 模型设置
2. **改文件**：`config.json`（模板见 [`config.example.json`](config.example.json)）

也可以用环境变量（容器部署推荐，优先级最高）：

```bash
ALAYA_PORT=9890
ALAYA_LLM_BASE=...  ALAYA_LLM_KEY=...  ALAYA_LLM_MODEL=...
ALAYA_ASR_BASE=...  ALAYA_ASR_KEY=...  ALAYA_ASR_MODEL=...
ALAYA_TTS_BASE=...  ALAYA_TTS_KEY=...  ALAYA_TTS_MODEL=...  ALAYA_TTS_VOICE=...
ALAYA_EMB_BASE=...  ALAYA_EMB_KEY=...  ALAYA_EMB_MODEL=...
```

### 音色与情绪换声

音色就是 TTS 厂商的音色名（不内置克隆），在控制台配一份清单：

```json
"tts": {
  "voices": [
    { "id": "nova",    "name": "Nova 女声",    "emoji": "🌸" },
    { "id": "shimmer", "name": "Shimmer 女声", "emoji": "✨" }
  ],
  "emotion_voices": { "伤心": "shimmer", "兴奋": "nova", "低落": "alloy" }
}
```

### 声纹（可选）

外部 ASR 厂商都不返回 speaker embedding，所以**默认关闭**：单人使用时"麦克风前的人 = 当前登录用户"。

要启用"听声识人"，自建一个声纹服务：

```
POST {speaker.base_url}/embedding     (multipart: audio=@a.wav)
→ {"embedding": [0.01, -0.23, ...]}    # 192 维，余弦比对
```

然后在模型设置里打开 `speaker.enabled`。参考实现：CAM++ / 3D-Speaker。

---

## 一些真实的数字

这些都是代码里的默认值，不是宣传语：

| 项 | 值 | 位置 |
|---|---|---|
| 习气网络节拍 | 200 ms | `manas.py` |
| 召回向量阈值 | 0.42 | `retrieval.py` |
| 关联建边阈值 | 0.55（top 5） | `xixi.py` |
| "同一件事"判定 | 0.88 / 相关 0.72 | `retrieval.py` |
| 半衰期 | 30 天 | `xixi.py` |
| 读即写饱和 | `1/(1+recall/4)` | `xixi.py` |
| 结生时机 | 静默 90s / 满载 40 条 / 突显 3 条高唤醒 | `working.py` |
| 临时记忆寿命 | 30 分钟 | `working.py` |
| TTS 输出 | 48 kHz · int16 · 单声道 | `llm.py` |
| 种子表 | 1 张，27 列 | `schema.sql` |

---

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` `/login` `/logout` | 本地账号 |
| GET | `/api/me` | 当前用户 |
| GET/POST | `/api/config` | 读写模型设置（key 掩码回显） |
| GET | `/api/ais` | 可对话的 AI 本体 |
| **WS** | `/ws` | 实时通道：`init / start / stop / chat / material / interrupt / voice / manas` |
| POST | `/api/chat` | 文本对话（同步，联调用） |
| POST | `/api/import` | 文字 / 文件 / 照片 → 记忆种子 |
| GET | `/api/seeds` | 种子库 |
| GET | `/api/working` | 现量窗口（尚未结生的临时记忆） |
| GET | `/api/selfmodel` | 我识三层 |
| GET | `/api/network` | 习气网络（概念 + 边 + 类型统计） |
| GET | `/api/history` | 对话流水 |
| GET/POST | `/api/amnesia/*` | 忆点星光：星图 / 寻忆邀请 / 转发 / 补星 |

WebSocket 二进制帧是 **48 kHz / int16 / 单声道 PCM**，前端用 `AudioContext({sampleRate: 48000})` 直接播。

---

## 目录

```
alaya_v2/
├─ run.sh                  一键启动
├─ config.example.json     配置模板
├─ server/
│  ├─ index.py             FastAPI 主服务（REST + WS + 静态站点）
│  ├─ settings.py          配置中心（config.json + 环境变量）
│  ├─ db.py                SQLite 基座（MySQL 写法 → SQLite 方言的唯一翻译层）
│  ├─ schema.sql           全部表结构
│  ├─ llm.py               外部 LLM / ASR / TTS / Embedding / 视觉
│  ├─ auth.py              本地账号（PBKDF2 + token）
│  ├─ manas.py             末那识心智涌现中枢
│  ├─ retrieval.py         三路召回 + 向量索引
│  ├─ seeds.py             种子容器
│  ├─ xixi.py              熏习算子
│  ├─ selfmodel.py         我识三层
│  ├─ dualview.py          双视角抽取
│  ├─ extract.py           种子抽取（六维指纹）
│  ├─ working.py           现量窗口 + 结生
│  ├─ organize.py          概念层凝聚
│  ├─ guide.py             公共本体的风格养成
│  ├─ profile.py           音色清单 / 形象照
│  ├─ amnesia.py           忆点星光
│  └─ ocean.py · life_stages.py
├─ public/                 控制台 + 忆点星光 + 浏览器录音
└─ data/                   运行时生成：alaya.db / vectors.npz / faces/
```

---

## 常见问题

**Q：为什么不直接用向量数据库？**
A：因为"想起什么"和"说什么"是两件事。向量库只回答前者，而且只回答一次。
阿赖耶要的是：记忆之间会互相激活、会互相抑制、会被反复熏习而改变权重——这些是图上的动力学，不是 top-k。

**Q：必须用外部 API 吗？能接本地模型吗？**
A：能。任何兼容 OpenAI 协议的本地服务（vLLM / Ollama 的 OpenAI 端点 / Xinference / FastChat）都可以，
把 `base_url` 指到本地即可。

**Q：数据在哪儿？会上传吗？**
A：全部在 `data/alaya.db`（单文件）。但**对话内容会发送给你配置的服务商**，请自行评估隐私边界。

**Q：能换 Embedding 模型吗？**
A：能。程序会检测维度变化，自动清空旧索引并全量重建。想手动重建：停服 → 删 `data/vectors.npz` → 重启。

**Q：这个项目和"AI 伴侣"有什么区别？**
A：它不绑定任何前端形态。它只做一件事：让一个 AI 拥有**会随时间变化的、属于它自己的记忆**。
控制台只是它的观察窗。


---

## English TL;DR

**Alaya** is a memory-native cognitive engine. Instead of retrieving document chunks and stuffing them into a prompt,
it keeps a graph of **memory seeds** that continuously compete; only the winning "thought" gets spoken.

- **Seeds, not chunks** — one fact per seed, with time / person / emotion / scene fingerprints. Content is immutable; corrections create a new seed and a `corr` edge.
- **Emergence, not retrieval** — a 200 ms habit-network loop (spread, inhibition, decay) decides *whether to speak*, not just *what to say*.
- **Consolidation, not logging** — utterances live in a short-lived "present" window, then get re-read as a whole and distilled into long-term seeds from two first-person perspectives.
- **Perfuming, not caching** — recall reinforces, co-occurrence builds edges, unused memories decay on a 30-day half-life. Weights change every day; content never does.
- **Star map** — memories are laid out by person/event, and you can invite the other person in the story to fill in the half you don't remember.

Single-file SQLite. Any OpenAI-compatible endpoint for LLM / ASR / TTS / embeddings. Apache-2.0.

```bash
git clone https://github.com/imcosy/alaya_v2.git && cd alaya_v2 && ./run.sh
```

---

## License

[Apache-2.0](LICENSE) · 欢迎 Issue / PR。

<p align="center">
  <sub>ALAYA · 阿赖耶 — 一切种子如瀑流</sub>
</p>
