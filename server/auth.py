# auth.py — 本地账号体系（用户名 + 密码）
#
# 开源版不依赖任何短信/第三方登录服务：账号、密码、令牌全部落在本地 SQLite。
# 密码用 PBKDF2-SHA256 加盐存储；每次登录签发独立 token（多端互不影响），
# 校验走 X-User-Id / X-Token 头，GET 场景兼容 query 参数。
import hashlib
import hmac
import os
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from server import db, settings

TOKEN_TTL = 7 * 86400
PBKDF2_ROUNDS = 200_000

router = APIRouter()


# ══════════════════ 密码 ══════════════════

def hash_password(pwd: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(pwd: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest = str(stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


# ══════════════════ 令牌 ══════════════════

async def _issue_token(user_id: int) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    await db.pool().exec(
        "INSERT INTO user_tokens (user_id, token, created_at) VALUES (%s, %s, UNIX_TIMESTAMP())",
        [int(user_id), token])
    await db.pool().exec(
        "DELETE FROM user_tokens WHERE user_id=%s AND created_at < UNIX_TIMESTAMP() - %s",
        [int(user_id), TOKEN_TTL])
    return token


async def _check_token(user_id: int, token: str) -> dict | None:
    if not user_id or not token:
        return None
    rows = await db.pool().query(
        "SELECT u.id, u.username, u.phone, u.nickname, u.avatar, u.ai_me_id FROM xd_users u "
        "JOIN user_tokens t ON t.user_id=u.id "
        "WHERE u.id=%s AND u.status=1 AND t.token=%s "
        "AND t.created_at >= UNIX_TIMESTAMP() - %s", [int(user_id), str(token), TOKEN_TTL])
    u = rows[0] if rows else None
    if u:
        u["id"] = int(u["id"])
    return u


async def get_current_user(request: Request) -> dict:
    """REST 鉴权依赖：优先 X-User-Id/X-Token header，兼容 query user_id/token"""
    q = request.query_params
    uid = request.headers.get("X-User-Id") or q.get("user_id") or ""
    tok = request.headers.get("X-Token") or q.get("token") or ""
    try:
        uid_i = int(uid or 0)
    except (TypeError, ValueError):
        uid_i = 0
    u = await _check_token(uid_i, tok)
    if not u:
        raise HTTPException(status_code=401, detail="请先登录")
    return u


async def ws_user(user_id, token) -> dict | None:
    try:
        return await _check_token(int(user_id or 0), str(token or ""))
    except (TypeError, ValueError):
        return None


# ══════════════════ 归属校验 ══════════════════

async def require_owner_ai(user: dict, owner_ai_id: int):
    """可访问的 AI 本体：自己名下的 ai_me_id、公共本体、或亲友（已接受邀请）。"""
    rows = await db.pool().query(
        "SELECT a.id FROM xd_ais a JOIN xd_users u ON u.ai_me_id=a.id "
        "WHERE a.id=%s AND u.id=%s AND a.is_alive=1",
        [int(owner_ai_id), int(user["id"])])
    if rows:
        return
    rows = await db.pool().query(
        "SELECT id FROM xd_ais WHERE id=%s AND is_public=1 AND is_alive=1",
        [int(owner_ai_id)])
    if rows:
        return
    kin = await db.pool().query(
        "SELECT a.id FROM alaya_galaxy_invites i "
        "JOIN xd_users ku ON ku.id = IF(i.inviter_user_id=%s, i.invitee_user_id, i.inviter_user_id) "
        "JOIN xd_ais a ON a.id = ku.ai_me_id "
        "WHERE a.id=%s AND a.is_alive=1 AND a.user_id>0 "
        "AND i.status='accepted' AND (i.inviter_user_id=%s OR i.invitee_user_id=%s) LIMIT 1",
        [int(user["id"]), int(owner_ai_id), int(user["id"]), int(user["id"])])
    if not kin:
        raise HTTPException(status_code=403, detail="无权访问该AI本体")


# ══════════════════ 本体 ══════════════════

async def _ensure_user_ai(user_id: int) -> int:
    """确保用户拥有自己的 AI 本体（xd_users.ai_me_id 是唯一权威）。"""
    rows = await db.pool().query(
        "SELECT ai_me_id, nickname, username FROM xd_users WHERE id=%s", [int(user_id)])
    if not rows:
        return 0
    u = rows[0]
    if u.get("ai_me_id"):
        return int(u["ai_me_id"])
    name = str(u.get("nickname") or u.get("username") or f"用户{user_id}")[:20]
    voice = str(settings.get("tts.voice") or "")
    ai_id = await db.pool().insert(
        "INSERT INTO xd_ais (user_id, name, voice_id, is_alive, sync_with_user, lang) "
        "VALUES (%s,%s,%s,1,0,'zh')", [int(user_id), name, voice])
    await db.pool().exec("UPDATE xd_users SET ai_me_id=%s WHERE id=%s", [int(ai_id), int(user_id)])
    return int(ai_id)


async def ensure_public_ai() -> int:
    """确保存在公共本体（控制台里那个对所有登录用户开放的「如意」）。"""
    name = str(settings.get("server.default_ai_name") or "如意")
    rows = await db.pool().query(
        "SELECT id FROM xd_ais WHERE is_public=1 AND is_alive=1 ORDER BY id LIMIT 1")
    if rows:
        return int(rows[0]["id"])
    return int(await db.pool().insert(
        "INSERT INTO xd_ais (user_id, name, voice_id, is_alive, is_public, lang) "
        "VALUES (0,%s,%s,1,1,'zh')",
        [name, str(settings.get("tts.voice") or "")]))


# ══════════════════ 接口 ══════════════════

@router.post("/api/auth/register")
async def auth_register(body: dict):
    """注册：username 3~32 位，password ≥ 6 位。注册即创建本人 AI 本体。"""
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    nickname = str(body.get("nickname") or "").strip() or username
    if not (3 <= len(username) <= 32):
        return JSONResponse({"detail": "用户名需 3~32 位"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"detail": "密码至少 6 位"}, status_code=400)
    exists = await db.pool().query("SELECT id FROM xd_users WHERE username=%s", [username])
    if exists:
        return JSONResponse({"detail": "用户名已被占用"}, status_code=400)
    uid = await db.pool().insert(
        "INSERT INTO xd_users (username, password, nickname, status) VALUES (%s,%s,%s,1)",
        [username, hash_password(password), nickname])
    ai_id = await _ensure_user_ai(uid)
    await db.pool().exec(
        "INSERT INTO alaya_user_main (user_id, main_ai_id, updated_at) VALUES (%s,%s,NOW()) "
        "ON CONFLICT(user_id) DO UPDATE SET main_ai_id=excluded.main_ai_id, "
        "updated_at=excluded.updated_at", [uid, ai_id])
    return {"user_id": int(uid), "token": await _issue_token(uid),
            "ai_me_id": ai_id, "nickname": nickname, "username": username}


@router.post("/api/auth/login")
async def auth_login(body: dict):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    rows = await db.pool().query(
        "SELECT id, username, nickname, password, status FROM xd_users WHERE username=%s",
        [username])
    u = rows[0] if rows else None
    if not u or int(u.get("status") or 0) != 1 or not verify_password(password, u["password"]):
        return JSONResponse({"detail": "用户名或密码不正确"}, status_code=401)
    uid = int(u["id"])
    await db.pool().exec(
        "UPDATE xd_users SET lastlogin=NOW(), logins=logins+1 WHERE id=%s", [uid])
    ai_id = await _ensure_user_ai(uid)
    await db.pool().exec(
        "INSERT INTO alaya_user_main (user_id, main_ai_id, updated_at) VALUES (%s,%s,NOW()) "
        "ON CONFLICT(user_id) DO UPDATE SET main_ai_id=excluded.main_ai_id, "
        "updated_at=excluded.updated_at", [uid, ai_id])
    return {"user_id": uid, "token": await _issue_token(uid), "ai_me_id": ai_id,
            "nickname": u.get("nickname") or u.get("username"), "username": u.get("username")}


@router.post("/api/auth/logout")
async def auth_logout(request: Request, user: dict = Depends(get_current_user)):
    tok = request.headers.get("X-Token") or request.query_params.get("token") or ""
    if tok:
        await db.pool().exec("DELETE FROM user_tokens WHERE token=%s", [str(tok)])
    return {"ok": True}


@router.get("/api/me")
async def api_me(user: dict = Depends(get_current_user)):
    """token 有效性校验 + 当前登录用户资料（用户图标弹窗数据源）"""
    rows = await db.pool().query(
        "SELECT id, username, phone, nickname, avatar, avatar_url, call_me, profession, "
        "gender, age, city, interests, birthday, zodiac, ai_me_id FROM xd_users WHERE id=%s",
        [int(user["id"])])
    me = rows[0] if rows else {"id": user["id"], "nickname": user.get("nickname")}
    me["id"] = int(me["id"])
    return {"user": me}
