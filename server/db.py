# db.py — 数据基座：SQLite 单文件（零外部依赖）
#
# 开源版把 MySQL 换成本地 SQLite：克隆下来直接能跑，数据落在 data/alaya.db。
# 业务层 SQL 仍按 MySQL 写法书写（%s 占位符、INSERT IGNORE、LEAST/NOW 等），
# 由本层统一翻译为 SQLite 方言 —— 翻译就是本层唯一职责，不向上层扩散。
#
# 并发模型：FastAPI 的 async 接口经 asyncio.to_thread 落到线程池，
# 这里每线程持有一条连接（WAL 模式：多读一写），busy_timeout 交给 SQLite 自己排队。
import os
import re
import json
import time
import math
import sqlite3
import asyncio
import logging
import threading
from datetime import datetime, date

log = logging.getLogger("alaya.db")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("ALAYA_DB", os.path.join(ROOT, "data", "alaya.db"))
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


# ── 类型适配（业务层偶尔会直接传 date/datetime 对象）──
sqlite3.register_adapter(datetime, lambda d: d.strftime("%Y-%m-%d %H:%M:%S"))
sqlite3.register_adapter(date, lambda d: d.isoformat())


# ── SQLite 侧实现 MySQL 的内置函数（语义一一对应，不改变业务写法）──
def _f_now(*_a) -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _f_unix_timestamp(*_a) -> int:
    return int(time.time())


def _f_curdate(*_a) -> str:
    return date.today().isoformat()


def _f_least(*a):
    vals = [x for x in a if x is not None]
    return min(vals) if vals else None


def _f_greatest(*a):
    vals = [x for x in a if x is not None]
    return max(vals) if vals else None


def _f_if(cond, a, b):
    return a if cond else b


def _f_ifnull(a, b):
    return b if a is None else a


def _f_pow(x, y):
    try:
        return math.pow(float(x), float(y))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_dt(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:19])
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _f_timestampdiff(unit, a, b):
    """MySQL TIMESTAMPDIFF 语义：返回 b-a 的整单位差"""
    da, db = _parse_dt(a), _parse_dt(b)
    if da is None or db is None:
        return None
    sec = (db - da).total_seconds()
    u = str(unit or "").strip().upper()
    if u == "SECOND":
        return int(sec)
    if u == "MINUTE":
        return int(sec // 60)
    if u == "HOUR":
        return int(sec // 3600)
    if u == "DAY":
        return int(sec // 86400)
    return int(sec)


def _register(conn: sqlite3.Connection) -> None:
    conn.create_function("NOW", 0, _f_now)
    conn.create_function("UNIX_TIMESTAMP", 0, _f_unix_timestamp)
    conn.create_function("CURDATE", 0, _f_curdate)
    conn.create_function("LEAST", -1, _f_least)
    conn.create_function("GREATEST", -1, _f_greatest)
    conn.create_function("POW", 2, _f_pow)
    conn.create_function("IF", 3, _f_if)
    conn.create_function("IFNULL", 2, _f_ifnull)
    conn.create_function("TIMESTAMPDIFF", 3, _f_timestampdiff)


_RE_INSERT_IGNORE = re.compile(r"\bINSERT\s+IGNORE\b", re.I)


def _sql(sql: str) -> str:
    """MySQL 写法 → SQLite 方言（占位符与 INSERT IGNORE）"""
    return _RE_INSERT_IGNORE.sub("INSERT OR IGNORE", sql).replace("%s", "?")


_conns = threading.local()
_init_lock = threading.Lock()
_initialized = False


class _SqlitePool:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        c = getattr(_conns, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
            _register(c)
            _conns.conn = c
        return c

    def _run(self, sql: str, params, mode: str):
        conn = self._conn()
        cur = conn.execute(_sql(sql), params if params is not None else [])
        if mode == "query":
            return [dict(r) for r in cur.fetchall()]
        if mode == "insert":
            return cur.lastrowid
        return cur.rowcount

    async def query(self, sql: str, params: list = None) -> list:
        return await asyncio.to_thread(self._run, sql, params, "query")

    async def exec(self, sql: str, params: list = None) -> int:
        return await asyncio.to_thread(self._run, sql, params, "exec")

    async def insert(self, sql: str, params: list = None) -> int:
        return await asyncio.to_thread(self._run, sql, params, "insert")


_pool: _SqlitePool | None = None


def pool() -> _SqlitePool:
    global _pool
    if _pool is None:
        with _init_lock:
            if _pool is None:
                _pool = _SqlitePool(DB_PATH)
    return _pool


def init_schema() -> None:
    """首次启动建表（schema.sql 全部 IF NOT EXISTS，可重复执行）"""
    global _initialized
    p = pool()                      # 先把池建好（内部自己加锁，不能在持有 _init_lock 时调用）
    with _init_lock:
        if _initialized:
            return
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            ddl = f.read()
        conn = p._conn()
        conn.executescript(ddl)
        _initialized = True
        log.info("[db] SQLite 就绪 %s", DB_PATH)


def jload(v, default=None):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default


def jdump(v) -> str:
    return json.dumps(v, ensure_ascii=False)
