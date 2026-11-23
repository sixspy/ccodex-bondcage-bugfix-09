"""释锁偏航处置：清理事故种子留下的笼锁残留（仅供本机文件库验收）。

口径：残留锁必须由收尾侧按「释锁偏航」处置，落审计痕；
禁止在页面上只调整持锁列表排序，也禁止手工 DELETE 不留痕。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from db import connect, write_audit  # noqa: E402
from locks import release  # noqa: E402


def sweep(actor: str = "admin") -> int:
    cleared = 0
    with connect() as conn:
        rows = conn.execute("SELECT * FROM cage_locks ORDER BY lock_key").fetchall()
        for row in rows:
            cage = conn.execute(
                "SELECT id, cage_code, status FROM cages WHERE id=?",
                (int(row["lock_key"].split(":", 1)[1]),),
            ).fetchone()
            target = cage["cage_code"] if cage else row["lock_key"]
            freed = release(conn, row["lock_key"], row["holder"])
            write_audit(
                conn,
                actor,
                "release_lock_drift",
                target,
                {
                    "lock_key": row["lock_key"],
                    "holder": row["holder"],
                    "acquired_at": row["acquired_at"],
                    "released": freed,
                    "action": "释锁偏航处置：收尾侧补释占行",
                },
            )
            cleared += freed
        conn.commit()
    return cleared


if __name__ == "__main__":
    who = sys.argv[1] if len(sys.argv) > 1 else "admin"
    n = sweep(who)
    print(f"释锁偏航处置：清理 {n} 行残留笼锁")
