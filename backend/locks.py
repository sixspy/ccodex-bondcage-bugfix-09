"""笼锁与争用登记（公共并发面）。

收尾口径（焊点，见 docs/释锁命运纪要.md，当日不得翻口）：
- 笼锁行只在业务临界区内存在；获取即提交，使争用方看得见持有者。
- 释锁由业务收尾侧负责，且与出金同一事务边界；释不掉即业务零改、拒亮绿。
- 争用留痕独立提交，不能被随后被拒的业务回滚抹掉。
- 持锁列表如实返回 cage_locks，禁止在页面上假装清空。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def _record_contention(conn, lock_key: str, actor: str, incumbent: str, now: str) -> None:
    """争用留痕必须独立落盘：随后的 409 会回滚业务事务，不能把留痕一起抹掉。"""
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, actor, "争用", f"持有者={incumbent}", now),
    )
    conn.commit()


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    now = now_iso()
    row = conn.execute(
        "SELECT holder FROM cage_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row is not None:
        if row["holder"] == holder:
            # 同一持有者重复进入视为幂等续持（同一收尾侧）
            return True
        _record_contention(conn, lock_key, holder, row["holder"], now)
        return False

    # SELECT 与 INSERT 之间仍可能有人抢先；用 ON CONFLICT 让数据库做唯一仲裁。
    cur = conn.execute(
        """INSERT INTO cage_locks(lock_key,holder,acquired_at) VALUES (?,?,?)
           ON CONFLICT(lock_key) DO NOTHING""",
        (lock_key, holder, now),
    )
    if cur.rowcount == 1:
        # 获取即提交：否则并发读者看不到未提交的持有行，争用会被误判为可获取。
        conn.commit()
        return True

    row = conn.execute(
        "SELECT holder FROM cage_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row is not None and row["holder"] == holder:
        conn.commit()
        return True
    incumbent = row["holder"] if row is not None else "未知"
    _record_contention(conn, lock_key, holder, incumbent, now)
    return False


def release(conn, lock_key: str, holder: str) -> int:
    """释锁只删本持有者的占行；返回实际删除行数，供收尾侧判定释锁命运。"""
    cur = conn.execute(
        "DELETE FROM cage_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )
    return cur.rowcount


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cage_locks ORDER BY acquired_at DESC"
        ).fetchall()
        # 如实返回：残留锁必须被看见，不能在页面上假装清空。
        return {"items": [dict(r) for r in rows], "held": len(rows)}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(
            conn,
            user,
            "lock_acquire",
            body.lock_key,
            {"ok": ok},
        )
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        released = release(conn, body.lock_key, user)
        write_audit(
            conn, user, "lock_release", body.lock_key, {"released": released}
        )
        conn.commit()
        return {"ok": True, "released": released}
