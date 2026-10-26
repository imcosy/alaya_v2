# settings.py — 运行配置中心
#
# 优先级：环境变量 > config.json > 内置默认值。
# 控制台（/api/config）可读可改；改完立即写回 config.json（原子替换），
# LLM/ASR/TTS/Embedding 客户端每次调用都读当前值，无需重启。
#
# 设计边界：本项目不自带任何模型。语音对话、语义向量全部走外部
# OpenAI 兼容接口，由使用者自行到各厂商申请 key 填入。
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.getenv("ALAYA_CONFIG", os.path.join(ROOT, "config.json"))

# OpenAI 兼容端点：OpenAI / DeepSeek / 阿里百炼 / 硅基流动 / 火山方舟 等
# 只要厂商提供 /v1/chat/completions、/v1/audio/*、/v1/embeddings，填 base_url 即可。
DEFAULTS: dict = {
    "server": {
        "host": "0.0.0.0",
        "port": 9890,
        # 新用户注册后自动创建的本体名（占位，随对话演化）
        "default_ai_name": "如意",
    },
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "temperature": 0.8,
        "max_tokens": 1024,
    },
    # 看图（照片描述 / 摄像头画面）。model 留空 = 复用 llm 的多模态模型
    "vision": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "asr": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "whisper-1",
        "language": "zh",
    },
    "tts": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "tts-1",
        "voice": "nova",
        # wav = 返回带 RIFF 头的 WAV；pcm = 裸 16bit 单声道小端 PCM
        "format": "wav",
        # 仅 format=pcm 时使用：厂商实际输出采样率
        "sample_rate": 24000,
        "speed": 1.0,
        # 可选音色清单（控制台「我的声音」里展示，可设为某个本体的音色）
        # [{"id": "nova", "name": "Nova 女声"}, ...]
        "voices": [],
        # 情绪 → 音色名（回复开头有 [[情绪:xx]] 标记时随情换声）
        # {"伤心": "shimmer", "兴奋": "nova"}
        "emotion_voices": {},
    },
    "embedding": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "text-embedding-3-small",
        # 由首次调用自动写入（换模型会触发向量重建）
        "dim": 0,
    },
    # 声纹（听声识人）可选外挂：外部 ASR 厂商都不返回 speaker embedding。
    # 关闭时单人使用场景 = 说话人即当前登录用户本人。
    "speaker": {
        "enabled": False,
        "base_url": "",
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
        ("ALAYA_LLM_BASE", ("llm", "base_url")),
        ("ALAYA_LLM_KEY", ("llm", "api_key")),
        ("ALAYA_LLM_MODEL", ("llm", "model")),
        ("ALAYA_ASR_BASE", ("asr", "base_url")),
        ("ALAYA_ASR_KEY", ("asr", "api_key")),
        ("ALAYA_ASR_MODEL", ("asr", "model")),
        ("ALAYA_TTS_BASE", ("tts", "base_url")),
        ("ALAYA_TTS_KEY", ("tts", "api_key")),
        ("ALAYA_TTS_MODEL", ("tts", "model")),
        ("ALAYA_TTS_VOICE", ("tts", "voice")),
        ("ALAYA_EMB_BASE", ("embedding", "base_url")),
        ("ALAYA_EMB_KEY", ("embedding", "api_key")),
        ("ALAYA_EMB_MODEL", ("embedding", "model")),
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
    """settings.get("llm.model")"""
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
    """单值落盘（如 embedding.dim 由运行时测得）"""
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
