# profile.py — 用户资料资源
#
# 声音：开放版没有本地音色克隆/波形编辑服务，音色就是外部 TTS 厂商的音色名
#       （在控制台 tts.voices 里配一份清单，切换时写进 xd_ais.voice_id）。
# 形象照：上传→压缩≤800KB→视觉模型第一人称自述→落库 alaya_faces
#       →写"自我形象"种子（本体通过记忆知道自己长什么样）。
import asyncio
import io
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from server import db, retrieval, settings
from server import seeds as seeds_mod
from server.auth import get_current_user, require_owner_ai
from server.llm import vl_describe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACES_DIR = os.getenv("ALAYA_FACES_DIR", os.path.join(ROOT, "data", "faces"))
FACE_MAX = 800 * 1024

FACE_DESC_PROMPT = (
    "这是「我」本人上传的自己的一张照片。请以第一人称「我」的口吻写一段自然自述（80-150字），"
    "让AI完全认识照片里的自己。必须涵盖：①这是我的什么照片（如职业装照/生活照/自拍/旅行照）；"
    "②发型发色与脸型；③表情神态；④衣着细节（款式/颜色/配饰）；⑤肤色气质与姿态。"
    "开头类似「这是我的……」。只描述确实看到的，不确定的不写，绝不编造。"
)

router = APIRouter()


# ══════════════════ 声音（外部 TTS 音色） ══════════════════

def _voice_list() -> list[dict]:
    cfg = settings.section("tts")
    items = [v for v in (cfg.get("voices") or []) if isinstance(v, dict) and v.get("id")]
    if items:
        return [{"id": str(v["id"]), "name": str(v.get("name") or v["id"]),
                 "emoji": str(v.get("emoji") or "🔊"), "gender": str(v.get("gender") or "")}
                for v in items]
    fallback = str(cfg.get("voice") or "").strip()
    return [{"id": fallback, "name": fallback, "emoji": "🔊", "gender": ""}] if fallback else []


@router.get("/api/voices")
async def api_voices(owner_ai_id: int, user: dict = Depends(get_current_user)):
    """可切换音色清单（控制台 tts.voices）+ 该本体当前音色"""
    await require_owner_ai(user, owner_ai_id)
    rows = await db.pool().query("SELECT name, voice_id FROM xd_ais WHERE id=%s", [int(owner_ai_id)])
    cur = str(rows[0].get("voice_id") or "") if rows else str(settings.get("tts.voice") or "")
    voices = _voice_list()
    for v in voices:
        v["in_use"] = 1 if v["id"] == cur else 0
    return {"voices": voices, "current": cur,
            "ai_name": str(rows[0].get("name") or "") if rows else ""}


@router.post("/api/voices/select")
async def api_voice_select(owner_ai_id: int, body: dict,
                           user: dict = Depends(get_current_user)):
    """切换该本体说话用的音色（必须是清单内的 id）"""
    await require_owner_ai(user, owner_ai_id)
    vid = str(body.get("voice_id") or "").strip()
    if vid not in {v["id"] for v in _voice_list()}:
        raise HTTPException(status_code=400, detail="该音色不在控制台音色清单中")
    await db.pool().exec("UPDATE xd_ais SET voice_id=%s WHERE id=%s",
                         [vid, int(owner_ai_id)])
    return {"ok": True, "voice_id": vid}


# ══════════════════ 形象照（上传→压缩→描述→记忆） ══════════════════

def _compress_jpg(data: bytes) -> bytes | None:
    """统一转 JPEG 并压到 800KB 内：先降质量，仍超再等比缩边"""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    q = 90
    out = io.BytesIO()
    img.save(out, "JPEG", quality=q)
    while out.tell() > FACE_MAX and q > 40:
        q -= 10
        out = io.BytesIO()
        img.save(out, "JPEG", quality=q)
    while out.tell() > FACE_MAX:
        w, h = img.size
        w2, h2 = max(120, int(w * 0.85)), max(120, int(h * 0.85))
        if w2 == w:
            break
        img = img.resize((w2, h2), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
    return out.getvalue()


@router.get("/api/faces")
async def api_faces(owner_ai_id: int, user: dict = Depends(get_current_user)):
    await require_owner_ai(user, owner_ai_id)
    rows = await db.pool().query(
        "SELECT id, descr, created_at FROM alaya_faces WHERE owner_ai_id=%s ORDER BY id DESC",
        [int(owner_ai_id)])
    return {"faces": rows}


@router.post("/api/faces")
async def api_faces_upload(owner_ai_id: int, file: UploadFile = File(...),
                           user: dict = Depends(get_current_user)):
    """上传形象照：压缩≤800KB → 视觉模型描述 → 落库 → 写自我形象种子"""
    await require_owner_ai(user, owner_ai_id)
    data = await file.read()
    if not data:
        return JSONResponse({"error": "空文件"}, status_code=400)
    jpg = await asyncio.to_thread(_compress_jpg, data)
    if not jpg:
        return JSONResponse({"error": "图片格式无法解析"}, status_code=400)
    desc = await vl_describe(jpg, FACE_DESC_PROMPT)
    if not desc:
        return JSONResponse({"error": "照片描述失败（检查视觉模型配置）"}, status_code=500)
    name = f"{int(owner_ai_id)}_{uuid.uuid4().hex[:12]}.jpg"
    os.makedirs(FACES_DIR, exist_ok=True)
    await asyncio.to_thread(lambda: open(os.path.join(FACES_DIR, name), "wb").write(jpg))
    fid = await db.pool().insert(
        "INSERT INTO alaya_faces (owner_ai_id, path, descr) VALUES (%s,%s,%s)",
        [int(owner_ai_id), name, desc])
    seed_text = f"{datetime.now().strftime('%Y-%m-%d')}我上传了一张自己的照片。{desc}"
    seed_id = await seeds_mod.write_seed(int(owner_ai_id), {
        "seed_type": "value", "seed_text": seed_text, "subject": "self",
        "confidence": 0.9, "state": "confirmed", "sense_id": "photo",
    }, peer_ai_id=0)
    await retrieval.ensure_seed_vector(seed_id, seed_text)
    retrieval.save_vectors()
    rows = await db.pool().query(
        "SELECT id, descr, created_at FROM alaya_faces WHERE id=%s", [fid])
    return {"face": rows[0] if rows else {"id": fid, "descr": desc}}


@router.get("/api/faces/{face_id}/file")
async def api_face_file(face_id: int, user: dict = Depends(get_current_user)):
    rows = await db.pool().query(
        "SELECT owner_ai_id, path FROM alaya_faces WHERE id=%s", [int(face_id)])
    if not rows:
        raise HTTPException(status_code=404, detail="照片不存在")
    await require_owner_ai(user, rows[0]["owner_ai_id"])
    p = os.path.join(FACES_DIR, str(rows[0]["path"]))
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(p, media_type="image/jpeg")


@router.delete("/api/faces/{face_id}")
async def api_face_del(face_id: int, user: dict = Depends(get_current_user)):
    rows = await db.pool().query(
        "SELECT owner_ai_id, path FROM alaya_faces WHERE id=%s", [int(face_id)])
    if not rows:
        raise HTTPException(status_code=404, detail="照片不存在")
    await require_owner_ai(user, rows[0]["owner_ai_id"])
    await db.pool().exec("DELETE FROM alaya_faces WHERE id=%s", [int(face_id)])
    p = os.path.join(FACES_DIR, str(rows[0]["path"]))
    if os.path.isfile(p):
        await asyncio.to_thread(os.remove, p)
    return {"ok": True}
