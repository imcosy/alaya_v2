# settings.py — 运行配置中心
#
# 优先级：环境变量 > config.json > 内置默认值。
# 控制台（/api/config）可读可改；改完立即写回 config.json（原子替换），
# 所有客户端调用都读当前值，无需重启。
#
# 设计边界：本项目不自带任何模型。对话 / 看图 / 语音识别 / 语音合成 / 语义向量
# 全部走「一把 Key 的统一网关」（OpenAI 兼容协议），在
# https://dev.nice-ai.cn/open/ 登录后申请 Key 填入即可。
# 想接自己的模型服务的开发者，改 server/llm.py 里的调用即可，本项目不保留多厂商配置。
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.getenv("ALAYA_CONFIG", os.path.join(ROOT, "config.json"))

# 申请 Key 的入口（页面只做登录 / 申请 / 管理 Key）
KEY_PORTAL = "https://dev.nice-ai.cn/open/"
# 默认接口地址
DEFAULT_BASE_URL = "https://dev.nice-ai.cn/open/v1"

# 网关对外提供的中性模型名（只是协议名，不代表内部实现）
CHAT_MODEL = "s2s"
ASR_MODEL = "s2s"
TTS_MODEL = "s2s"
EMB_MODEL = "s2s-embedding"

# 网关提供的音色别名（切换音色只在这份清单里选）
VOICES = [
    {"id": "zh-female-1", "name": "温柔女声", "emoji": "🌸", "gender": "female"},
    {"id": "zh-female-2", "name": "清亮女声", "emoji": "✨", "gender": "female"},
    {"id": "zh-female-3", "name": "成熟女声", "emoji": "🌷", "gender": "female"},
    {"id": "zh-male-1", "name": "沉稳男声", "emoji": "🌊", "gender": "male"},
    {"id": "zh-male-2", "name": "年轻男声", "emoji": "🍃", "gender": "male"},
    {"id": "en-female-1", "name": "英文女声", "emoji": "🌍", "gender": "female"},
    {"id": "en-male-1", "name": "英文男声", "emoji": "🌐", "gender": "male"},
]
DEFAULT_VOICE = "zh-female-1"

# 情绪换声（留空 = 所有情绪都用本体自己的音色；语义标记仍然照常写入记忆）
EMOTION_VOICES: dict = {}

DEFAULTS: dict = {
    "server": {
        "host": "0.0.0.0",
        "port": 9890,
        # 新用户注册后自动创建的本体名（占位，随对话演化）
        "default_ai_name": "如意",
    },
    # 统一网关：一个地址 + 一把 Key，全部能力都走它
    "gateway": {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
    },
}

_lock = threading.Lock()
_cache: dict | None = None


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides() -> dict:
    """环境变量覆盖（部署场景不必把 key 写进文件）"""
    out: dict = {}
    pairs = [
        ("ALAYA_HOST", ("server", "host")),
        ("ALAYA_PORT", ("server", "port")),
        ("ALAYA_GATEWAY_BASE", ("gateway", "base_url")),
        ("ALAYA_GATEWAY_KEY", ("gateway", "api_key")),
    ]
    for env, (sec, key) in pairs:
        v = os.getenv(env)
        if v is None or v == "":
            continue
        if key == "port":
            v = int(v)
        out.setdefault(sec, {})[key] = v
    return out


def load(force: bool = False) -> dict:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        disk: dict = {}
        if os.path.isfile(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk = json.load(f) or {}
            except (OSError, ValueError):
                disk = {}
        _cache = _deep_merge(_deep_merge(DEFAULTS, disk), _env_overrides())
        return _cache


def section(name: str) -> dict:
    return dict(load().get(name) or {})


def get(path: str, default=None):
    """settings.get("gateway.api_key")"""
    cur = load()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def save(patch: dict) -> dict:
    """把增量写回 config.json（只落盘用户可配项，不落环境变量覆盖值）"""
    global _cache
    with _lock:
        disk: dict = {}
        if os.path.isfile(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk = json.load(f) or {}
            except (OSError, ValueError):
                disk = {}
        disk = _deep_merge(disk, patch or {})
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(disk, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, CONFIG_PATH)
        _cache = None
    return load(force=True)


def persist_value(path: str, value) -> None:
    """单值落盘（如 gateway.embedding_dim 由运行时测得）"""
    parts = path.split(".")
    node: dict = {}
    cur = node
    for p in parts[:-1]:
        cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    save(node)


def mask(cfg: dict) -> dict:
    """控制台展示：key 只回显尾 4 位，避免明文外泄"""
    out = json.loads(json.dumps(cfg))

    def _walk(d):
        for k, v in list(d.items()):
            if isinstance(v, dict):
                _walk(v)
            elif k == "api_key" and isinstance(v, str) and v:
                d[k] = ("*" * 8 + v[-4:]) if len(v) > 4 else "****"

    _walk(out)
    return out


def is_configured(sec: str) -> bool:
    s = section(sec)
    return bool(str(s.get("base_url") or "").strip() and str(s.get("api_key") or "").strip())


def voice_list() -> list[dict]:
    return [dict(v) for v in VOICES]


def default_voice() -> str:
    return DEFAULT_VOICE


def emotion_voices() -> dict:
    return dict(EMOTION_VOICES)
