"""解笼拨付：核验通过后把笼内可用额拨付给受益人。

收尾口径（甲路径，焊死；见 docs/释锁命运纪要.md，当日不得翻口）：
- 出金与释锁共用同一事务边界：释锁占行成功删除，才提交拨付。
- 释锁偏航（占行删不掉）则整笔回滚、拒写拨付、记「释锁偏航」并拒绝亮绿。
- 业务校验失败同样在收尾侧删掉本持有者的占行，避免残锁堵后人。
- 不允许「绿灯随锁熄灭」的第二条路，也不做影子拨付等第三路。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import ReleaseIn

router = APIRouter(tags=["release"])


class LockReleaseDrift(RuntimeError):
    """释锁占行未能删除：焊点被偏离，必须拒绝拨付。"""


def _close_failed_boundary(
    conn,
    lock_key: str,
    holder: str,
    target: str,
    action: str,
    reason: str,
) -> int:
    """失败收尾：先删本持有者的笼锁占行，再落审计并独立提交。

    与拨付事务解耦：业务事务已回滚，释锁必须在独立事务里落地，
    否则「回滚顺手把释锁也回滚」会再次漏锁。
    """
    freed = release(conn, lock_key, holder)
    write_audit(
        conn,
        holder,
        action,
        target,
        {"lock_key": lock_key, "released": freed, "reason": reason},
    )
    conn.commit()
    return freed


@router.get("/api/releases")
def list_releases(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.*, c.cage_code, c.contract_no
               FROM releases r
               JOIN cages c ON c.id=r.cage_id
               ORDER BY r.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/releases")
def do_release(body: ReleaseIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"cage:{body.cage_id}"
    target = lock_key
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not cage:
                raise HTTPException(404, "笼不存在")
            target = cage["cage_code"]
            if cage["status"] != "核验通过":
                raise HTTPException(409, "仅核验通过的笼可解笼拨付")
            # 核验结论必须对齐当前笼代
            att = conn.execute(
                """SELECT * FROM attestations
                   WHERE cage_id=? AND attest_gen=? AND outcome='通过'
                   ORDER BY id DESC LIMIT 1""",
                (body.cage_id, int(cage["generation"])),
            ).fetchone()
            if not att:
                raise HTTPException(409, "缺少与笼代对齐的通过核验")
            avail = float(cage["available"])
            amount = float(body.amount) if body.amount is not None else avail
            if amount <= 0:
                raise HTTPException(400, "拨付金额须大于 0")
            if amount > avail + 1e-9:
                raise HTTPException(409, "拨付金额超过笼可用额")
            payee = body.payee or cage["beneficiary"]
            now = now_iso()
            gen = int(cage["generation"])
            ref = f"REL-{cage['cage_code']}-g{gen}-{int(now.replace(':', '').replace('-', '').replace('T', '')[-8:])}"
            cur = conn.execute(
                """INSERT INTO releases(cage_id,amount,payee,cage_generation,status,release_ref,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (body.cage_id, amount, payee, gen, "已拨付", ref, now),
            )
            release_id = cur.lastrowid
            new_avail = round(avail - amount, 6)
            new_status = "已解笼" if new_avail <= 1e-9 else "部分解笼"
            conn.execute(
                "UPDATE cages SET available=?, status=? WHERE id=?",
                (max(new_avail, 0.0), new_status, body.cage_id),
            )
            # 账户：冻结减少，余额减少（资金离开系统）
            conn.execute(
                """UPDATE bond_accounts
                   SET frozen = frozen - ?, balance = balance - ?
                   WHERE party_key=?""",
                (amount, amount, cage["party_key"]),
            )
            # 焊点：释锁占行必须在拨付提交前删除；删不掉就不允许提交。
            freed = release(conn, lock_key, user)
            if freed != 1:
                raise LockReleaseDrift(
                    f"释锁占行删除 {freed} 行，拒绝提交拨付"
                )
            write_audit(
                conn,
                user,
                "release_lock_ok",
                cage["cage_code"],
                {
                    "lock_key": lock_key,
                    "release_id": release_id,
                    "released": freed,
                },
            )
            write_audit(
                conn,
                user,
                "cage_release",
                cage["cage_code"],
                {
                    "release_id": release_id,
                    "amount": amount,
                    "payee": payee,
                    "cage_generation": gen,
                    "release_ref": ref,
                },
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM releases WHERE id=?", (release_id,)
            ).fetchone()
            return dict(row)
        except LockReleaseDrift as exc:
            conn.rollback()
            freed = _close_failed_boundary(
                conn, lock_key, user, target, "release_lock_drift", str(exc)
            )
            raise HTTPException(
                500,
                f"释锁偏航：拨付零改、拒亮绿（{lock_key}，released={freed}）",
            ) from exc
        except HTTPException as exc:
            conn.rollback()
            _close_failed_boundary(
                conn,
                lock_key,
                user,
                target,
                "release_lock_aborted",
                f"HTTP {exc.status_code}: {exc.detail}",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 收尾必须兜底释锁
            conn.rollback()
            _close_failed_boundary(
                conn,
                lock_key,
                user,
                target,
                "release_lock_aborted",
                type(exc).__name__,
            )
            raise
