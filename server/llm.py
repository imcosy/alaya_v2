# llm.py — 外部推理服务客户端（OpenAI 兼容协议）
#
# 本项目不自带任何模型。四处能力全部走厂商的 OpenAI 兼容端点，由使用者在
# 控制台「模型设置」里填 base_url / api_key / model：
#   LLM   POST {base}/chat/completions   （流式 SSE）
#   看图  同一个 chat/completions，content 数组带 image_url（视觉模型）
#   ASR   POST {base}/audio/transcriptions（multipart 上传 WAV）
#   TTS   POST {base}/audio/speech        （返回 wav 或裸 pcm）
#   向量  POST {base}/embeddings
# 声纹（听声识人）是可选项：外部 ASR 不返回 speaker embedding，
# 只有配置了独立的声纹服务才启用；未配置时说话人即当前登录用户本人。
from __future__ import annotations

import io
import json
import math
import base64
import wave
import logging
from array import array
from typing import AsyncGenerator

import httpx
import numpy as np

from . import settings

log = logging.getLogger("alaya.llm")

TTS_OUT_SR = 48000          # 服务端统一输出给前端的采样率（前端 AudioContext 固定 48k）
_CHUNK = 1024 * 256


def _client(timeout: float | None = None) -> httpx.AsyncClient:
    t = httpx.Timeout(connect=15.0, read=timeout, write=60.0, pool=15.0)
    return httpx.AsyncClient(timeout=t)


def _headers(sec: dict) -> dict:
    return {
        "Authorization": f"Bearer {sec.get('api_key') or ''}",
        "Content-Type": "application/json",
    }


def _url(sec: dict, path: str) -> str:
    base = str(sec.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("尚未配置 API 地址（控制台 → 模型设置）")
    return base + path


def _body_error(r: httpx.Response) -> str:
    try:
        j = r.json()
        msg = j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else j.get("error")
        return f"{r.status_code} {msg or json.dumps(j, ensure_ascii=False)[:300]}"
    except ValueError:
        return f"{r.status_code} {r.text[:300]}"


# ══════════════════ LLM（流式 / 非流式）══════════════════

def _chat_payload(messages: list, sec: dict, temperature: float, max_tokens: int,
                  stream: bool) -> dict:
    body = {
        "model": sec.get("model"),
        "messages": messages,
        "temperature": float(temperature if temperature is not None
                             else sec.get("temperature", 0.8)),
        "max_tokens": int(max_tokens or sec.get("max_tokens", 1024)),
        "stream": stream,
    }
    return body


async def llm_stream(messages: list, temperature: float = 0.7,
                     max_tokens: int = 1024) -> AsyncGenerator[str, None]:
    """流式对话：逐 token 产出增量文本"""
    sec = settings.section("llm")
    payload = _chat_payload(messages, sec, temperature, max_tokens, True)
    async with _client(timeout=None) as c:
        async with c.stream("POST", _url(sec, "/chat/completions"),
                            headers=_headers(sec), json=payload) as r:
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
    sec = settings.section("llm")
    payload = _chat_payload(messages, sec, temperature, max_tokens, False)
    async with _client(timeout=120) as c:
        r = await c.post(_url(sec, "/chat/completions"), headers=_headers(sec), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"LLM 服务错误：{_body_error(r)}")
        j = r.json()
        return str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


async def llm_chat_fn(messages: list, temperature: float = 0.7, max_tokens: int = 1024) -> str:
    return await llm_chat(messages, temperature=temperature, max_tokens=max_tokens)


# ══════════════════ 看图（视觉模型）══════════════════

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
    """画面 → 一句话描述。vision.model 留空则复用 llm 的模型。"""
    if not image_bytes:
        return ""
    vis = settings.section("vision")
    base = settings.section("llm")
    sec = {
        "base_url": vis.get("base_url") or base.get("base_url"),
        "api_key": vis.get("api_key") or base.get("api_key"),
        "model": vis.get("model") or base.get("model"),
    }
    b64 = base64.b64encode(image_bytes).decode()
    msgs = [{"role": "user", "content": [
        {"type": "image_url",
         "image_url": {"url": f"data:{_img_mime(image_bytes)};base64,{b64}"}},
        {"type": "text", "text": prompt or VL_DESC_PROMPT}]}]
    payload = _chat_payload(msgs, sec, 0.2, 200, False)
    async with _client(timeout=90) as c:
        r = await c.post(_url(sec, "/chat/completions"), headers=_headers(sec), json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"视觉模型错误：{_body_error(r)}")
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


async def _speaker_embedding(wav: bytes) -> str | None:
    """可选的声纹服务：POST {base}/embedding，multipart 上传 wav，返回 {"embedding":[...]}"""
    spk = settings.section("speaker")
    if not spk.get("enabled") or not str(spk.get("base_url") or "").strip():
        return None
    headers = {}
    if spk.get("api_key"):
        headers["Authorization"] = f"Bearer {spk['api_key']}"
    async with _client(timeout=60) as c:
        r = await c.post(_url(spk, "/embedding"), headers=headers,
                         files={"audio": ("a.wav", wav, "audio/wav")})
        if r.status_code != 200:
            raise RuntimeError(f"声纹服务错误：{_body_error(r)}")
        emb = r.json().get("embedding")
    if not emb:
        return None
    arr = np.asarray(emb, dtype=np.float32)
    return base64.b64encode(arr.tobytes()).decode()


async def asr_recognize(pcm16k: bytes, language: str = "auto", speaker: bool = False):
    """16k int16 单声道 PCM → (text, speaker_emb_b64|None, rms)。

    speaker=True 时若配置了声纹服务则一并返回 CAM++ 192d 声纹（base64 float32）；
    未配置声纹服务时第二项为 None（上层按「单人使用=本人」处理）。
    """
    rms = _rms(pcm16k)
    if not pcm16k or len(pcm16k) < 100:
        return ("", None, rms) if speaker else ("", rms)
    sec = settings.section("asr")
    lang = str(language or "auto")
    if lang == "auto":
        lang = str(sec.get("language") or "zh")
    wav = pcm16k_to_wav(pcm16k)
    headers = {"Authorization": f"Bearer {sec.get('api_key') or ''}"}
    data = {"model": str(sec.get("model") or "whisper-1"), "language": lang,
            "response_format": "json"}
    async with _client(timeout=120) as c:
        r = await c.post(_url(sec, "/audio/transcriptions"), headers=headers, data=data,
                         files={"file": ("audio.wav", wav, "audio/wav")})
        if r.status_code != 200:
            raise RuntimeError(f"ASR 服务错误：{_body_error(r)}")
        text = str((r.json() or {}).get("text") or "").strip()
    emb = await _speaker_embedding(wav) if speaker else None
    if not speaker:
        return text, rms
    return text, emb, rms


# ══════════════════ TTS ══════════════════

def _resample(pcm: bytes, src_sr: int, ch: int = 1) -> bytes:
    """int16 PCM → 48k 单声道 int16（前端 AudioContext 固定 48k）"""
    if not pcm:
        return b""
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    if ch > 1:
        a = a[: a.size // ch * ch].reshape(-1, ch).mean(axis=1)
    if src_sr != TTS_OUT_SR:
        n = max(1, int(round(a.size * TTS_OUT_SR / float(src_sr))))
        x = np.linspace(0, a.size - 1, n, dtype=np.float64)
        a = np.interp(x, np.arange(a.size, dtype=np.float64), a)
    return a.clip(-32768, 32767).astype(np.int16).tobytes()


def _wav_to_pcm(data: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        ch, width, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise RuntimeError(f"TTS 返回的 WAV 位深为 {width * 8}bit，只支持 16bit（请在控制台把格式设为 wav 或 pcm）")
    return raw, sr, ch


async def tts_stream(text: str, voice, speed: float = 1.0) -> AsyncGenerator[bytes, None]:
    """文本 → 48kHz int16 单声道 PCM 块。"""
    if not text or not text.strip():
        return
    sec = settings.section("tts")
    fmt = str(sec.get("format") or "wav").lower()
    payload = {
        "model": str(sec.get("model") or "tts-1"),
        "input": text,
        "voice": str(voice or sec.get("voice") or "alloy"),
        "response_format": "pcm" if fmt == "pcm" else "wav",
        "speed": float(speed or sec.get("speed") or 1.0),
    }
    async with _client(timeout=120) as c:
        r = await c.post(_url(sec, "/audio/speech"), headers=_headers(sec), json=payload)
        if r.status_code != 200:
            await r.aread()
            raise RuntimeError(f"TTS 服务错误：{_body_error(r)}")
        raw = r.content
    if fmt == "pcm":
        pcm = _resample(raw, int(sec.get("sample_rate") or 24000), 1)
    else:
        pcm_raw, sr, ch = _wav_to_pcm(raw)
        pcm = _resample(pcm_raw, sr, ch)
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
    sec = settings.section("embedding")
    if not texts:
        return np.zeros((0, int(sec.get("dim") or 0)), dtype=np.float32)
    async with _client(timeout=90) as c:
        r = await c.post(_url(sec, "/embeddings"), headers=_headers(sec),
                         json={"model": str(sec.get("model") or ""), "input": texts})
        if r.status_code != 200:
            raise RuntimeError(f"向量服务错误：{_body_error(r)}")
        data = (r.json() or {}).get("data") or []
    if len(data) != len(texts):
        raise RuntimeError(f"向量服务返回条数不符：期望 {len(texts)}，实得 {len(data)}")
    mat = np.asarray([d.get("embedding") or [] for d in data], dtype=np.float32)
    norm = np.linalg.norm(mat, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return (mat / norm).astype(np.float32)
