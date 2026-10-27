# llm.py — 统一网关客户端（OpenAI 兼容协议）
#
# 本项目不自带任何模型，四处能力全部走同一把 Key 的统一网关
# （https://dev.nice-ai.cn/open/ 登录后申请）：
#   LLM   POST {base}/chat/completions      （流式 SSE）
#   看图  同一个 chat/completions，content 数组带 image_url
#   ASR   POST {base}/audio/transcriptions  （multipart 上传 WAV）
#   TTS   POST {base}/audio/speech          （返回 48kHz 裸 PCM）
#   向量  POST {base}/embeddings
# 想接自己的模型服务的开发者，改这里的地址与协议即可。
from __future__ import annotations

import io
import json
import base64
import wave
import logging
from typing import AsyncGenerator

import httpx
import numpy as np

from . import settings

log = logging.getLogger("alaya.llm")

TTS_OUT_SR = 48000          # 网关返回的采样率（前端 AudioContext 固定 48k）
_CHUNK = 1024 * 256


def _client(timeout: float | None = None) -> httpx.AsyncClient:
    t = httpx.Timeout(connect=15.0, read=timeout, write=60.0, pool=15.0)
    return httpx.AsyncClient(timeout=t)


def _gw() -> tuple[str, str]:
    sec = settings.section("gateway")
    base = str(sec.get("base_url") or "").rstrip("/")
    key = str(sec.get("api_key") or "").strip()
    if not base:
        raise RuntimeError("尚未配置统一接口地址（我的资料 → 模型设置）")
    if not key:
        # 不能带着空 Key 发请求：httpx 会抛 LocalProtocolError("Illegal header value b'Bearer '")，
        # 报错信息完全指不到"没填 Key"这件事上。
        raise RuntimeError(
            f"尚未填写 API Key：点「我的资料 → 模型设置」填入，或在 {settings.KEY_PORTAL} 申请")
    return base, key


def _headers() -> dict:
    _, key = _gw()
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _url(path: str) -> str:
    base, _ = _gw()
    return base + path


def _body_error(r: httpx.Response) -> str:
    try:
        j = r.json()
        err = j.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        return f"{r.status_code} {msg or json.dumps(j, ensure_ascii=False)[:300]}"
    except ValueError:
        return f"{r.status_code} {r.text[:300]}"


# ══════════════════ LLM（流式 / 非流式）══════════════════

def _chat_payload(messages: list, temperature: float, max_tokens: int,
                  stream: bool) -> dict:
    return {
        "model": settings.CHAT_MODEL,
        "messages": messages,
        "temperature": float(temperature if temperature is not None else 0.8),
        "max_tokens": int(max_tokens or 1024),
        "stream": stream,
    }


async def llm_stream(messages: list, temperature: float = 0.7,
                     max_tokens: int = 1024) -> AsyncGenerator[str, None]:
    """流式对话：逐 token 产出增量文本"""
    payload = _chat_payload(messages, temperature, max_tokens, True)
    async with _client(timeout=None) as c:
        async with c.stream("POST", _url("/chat/completions"),
                            headers=_headers(), json=payload) as r:
            if r.status_code != 200:
                await r.aread()
                raise RuntimeError(f"LLM 服务错误：{_body_error(r)}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        return
                    continue
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("error"):
                    err = obj["error"]
                    raise RuntimeError("LLM 服务错误：" + str(
                        err.get("message") if isinstance(err, dict) else err))
                for ch in obj.get("choices") or []:
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        yield delta


async def llm_chat(messages: list, temperature: float = 0.7,
                   max_tokens: int = 1024) -> str:
    """非流式对话（抽取 / 我识凝聚 / 后台任务）"""
    payload = _chat_payload(messages, temperature, max_tokens, False)
    async with _client(timeout=120) as c:
        r = await c.post(_url("/chat/completions"), headers=_headers(), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"LLM 服务错误：{_body_error(r)}")
        j = r.json()
        return str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


async def llm_chat_fn(messages: list, temperature: float = 0.7, max_tokens: int = 1024) -> str:
    return await llm_chat(messages, temperature=temperature, max_tokens=max_tokens)


# ══════════════════ 看图 ══════════════════

VL_DESC_PROMPT = (
    "只输出对画面的一句话描述，不超过40字，不要复述本要求、不要解释。"
    "说清：在哪、有谁、在做什么；看不清的就不写。"
)


def _img_mime(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:3] == b"GIF":
        return "image/gif"
    if b[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


async def vl_describe(image_bytes: bytes, prompt: str | None = None) -> str:
    """画面 → 一句话描述（与对话同一个网关、同一个多模态模型）"""
    if not image_bytes:
        return ""
    b64 = base64.b64encode(image_bytes).decode()
    msgs = [{"role": "user", "content": [
        {"type": "image_url",
         "image_url": {"url": f"data:{_img_mime(image_bytes)};base64,{b64}"}},
        {"type": "text", "text": prompt or VL_DESC_PROMPT}]}]
    payload = _chat_payload(msgs, 0.2, 200, False)
    async with _client(timeout=90) as c:
        r = await c.post(_url("/chat/completions"), headers=_headers(), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"视觉服务错误：{_body_error(r)}")
        j = r.json()
        return str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


# ══════════════════ ASR ══════════════════

def _rms(pcm16k: bytes) -> float:
    if not pcm16k:
        return 0.0
    a = np.frombuffer(pcm16k[: len(pcm16k) // 2 * 2], dtype=np.int16)
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((a.astype(np.float32) / 32768.0) ** 2)))


def pcm16k_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


async def asr_recognize(pcm16k: bytes, language: str = "auto", speaker: bool = False):
    """16k int16 单声道 PCM → (text, speaker_emb|None, rms)。

    开放版不接声纹服务：第二个返回值恒为 None，上层按「单人使用 = 登录用户本人」处理。
    signature 保留三元组，是为了让调用方（听声识人链路）不用改。
    """
    rms = _rms(pcm16k)
    if not pcm16k or len(pcm16k) < 100:
        return ("", None, rms) if speaker else ("", rms)
    lang = str(language or "auto")
    if lang == "auto":
        lang = "zh"
    wav = pcm16k_to_wav(pcm16k)
    headers = {"Authorization": _headers()["Authorization"]}
    data = {"model": settings.ASR_MODEL, "language": lang, "response_format": "json"}
    async with _client(timeout=120) as c:
        r = await c.post(_url("/audio/transcriptions"), headers=headers, data=data,
                         files={"file": ("audio.wav", wav, "audio/wav")})
        if r.status_code != 200:
            raise RuntimeError(f"ASR 服务错误：{_body_error(r)}")
        text = str((r.json() or {}).get("text") or "").strip()
    if not speaker:
        return text, rms
    return text, None, rms


# ══════════════════ TTS ══════════════════

async def tts_stream(text: str, voice, speed: float = 1.0) -> AsyncGenerator[bytes, None]:
    """文本 → 48kHz int16 单声道 PCM 块（网关按 sentence 返回裸 PCM 流）。"""
    if not text or not text.strip():
        return
    payload = {
        "model": settings.TTS_MODEL,
        "input": text,
        "voice": str(voice or settings.default_voice()),
        "response_format": "pcm",
        "speed": float(speed or 1.0),
    }
    async with _client(timeout=120) as c:
        r = await c.post(_url("/audio/speech"), headers=_headers(), json=payload)
        if r.status_code != 200:
            await r.aread()
            raise RuntimeError(f"TTS 服务错误：{_body_error(r)}")
        pcm = r.content
    for i in range(0, len(pcm), _CHUNK):
        yield pcm[i: i + _CHUNK]


# ══════════════════ 分句 ══════════════════

_SENT_END = "。！？；!?;\n"


def cut_sentence(buf: str, min_len: int = 4) -> tuple[str, str]:
    """从累积文本中切出一句（含句末标点），返回 (sentence, remainder)。
    LLM 流式输出的边切边合成节拍器：凑够一个完整句立即交给 TTS，不等整段生成。
    """
    for i, ch in enumerate(buf):
        if ch in _SENT_END:
            seg = buf[:i + 1]
            if len(seg.strip()) >= min_len:
                return seg, buf[i + 1:]
    return "", buf


# ══════════════════ Embedding ══════════════════

async def embed(texts: list[str]) -> np.ndarray:
    """文本 → L2 归一化向量矩阵 (n, dim)"""
    if not texts:
        return np.zeros((0, int(settings.get("gateway.embedding_dim") or 0)), dtype=np.float32)
    async with _client(timeout=90) as c:
        r = await c.post(_url("/embeddings"), headers=_headers(),
                         json={"model": settings.EMB_MODEL, "input": texts})
        if r.status_code != 200:
            raise RuntimeError(f"向量服务错误：{_body_error(r)}")
        data = (r.json() or {}).get("data") or []
    if len(data) != len(texts):
        raise RuntimeError(f"向量服务返回条数不符：期望 {len(texts)}，实得 {len(data)}")
    mat = np.asarray([d.get("embedding") or [] for d in data], dtype=np.float32)
    norm = np.linalg.norm(mat, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return (mat / norm).astype(np.float32)
