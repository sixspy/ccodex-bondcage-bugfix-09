"""失败测丙：两名柜员对同一笼号同时提交解笼，必须抢笼锁。

后到一方只留争用痕迹，不得穿透写入。
释锁命运（cage_locks 是否清空）与锁争用（contention_log）分两表验收陈述。

编排说明：SQLite 会把两个请求排到同一把写锁上，若不做确定性编排，
后到者往往不是撞笼锁而是撞「笼已解笼」状态，测不到真实的争用分支。
这里用两个独立鉴权会话（clerk / admin）同时打解笼接口，
并让先到者在临界区内停住，确保后到者真正撞上已提交的笼锁占行。
"""
import threading

import pytest
from fastapi.testclient import TestClient

import release_svc
from app import app
from db import connect, init_db, now_iso as real_now_iso


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str) -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _prepare_releasable_cage(
    c: TestClient, h: dict, code: str = "CG-RACE-001", party: str = "P-RACE"
) -> dict:
    assert (
        c.post(
            "/api/bonds",
            headers=h,
            json={
                "party_key": party,
                "party_name": "双人撞锁测试",
                "initial_deposit": 100000,
            },
        ).status_code
        == 200
    )
    cage = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": party,
            "contract_no": "HT-RACE-1",
            "amount": 50000,
            "beneficiary": "受益方",
            "cage_code": code,
        },
    ).json()
    assert (
        c.post(
            "/api/attestations",
            headers=h,
            json={"cage_id": cage["id"], "outcome": "通过", "evidence": "验收合格"},
        ).status_code
        == 200
    )
    return cage


def test_two_clerks_release_same_cage_race_for_cage_lock(monkeypatch):
    c = TestClient(app)
    h_clerk = _auth(c, "clerk")
    h_admin = _auth(c, "admin")
    cage = _prepare_releasable_cage(c, h_clerk)
    lock_key = f"cage:{cage['id']}"

    first_inside = threading.Event()
    second_done = threading.Event()
    pause_once = threading.Lock()

    def _pausing_now_iso():
        # 先到者已持锁并提交，此时停住，等后到者真正撞锁。
        if pause_once.acquire(blocking=False):
            first_inside.set()
            assert second_done.wait(timeout=20), "后到者未按时打解笼接口"
        return real_now_iso()

    monkeypatch.setattr(release_svc, "now_iso", _pausing_now_iso)

    results: dict = {}

    def _fire_clerk():
        results["clerk"] = c.post(
            "/api/releases", headers=h_clerk, json={"cage_id": cage["id"]}
        )

    def _fire_admin():
        assert first_inside.wait(timeout=20), "先到者未进入临界区"
        try:
            results["admin"] = c.post(
                "/api/releases", headers=h_admin, json={"cage_id": cage["id"]}
            )
        finally:
            second_done.set()

    t1 = threading.Thread(target=_fire_clerk)
    t2 = threading.Thread(target=_fire_admin)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert set(results) == {"clerk", "admin"}
    codes = sorted(r.status_code for r in results.values())
    assert codes == [200, 409], (
        f"双人同解应一成一拒，实得 {codes}: "
        f"{ {k: v.text for k, v in results.items()} }"
    )

    with connect() as conn:
        release_rows = conn.execute(
            "SELECT * FROM releases WHERE cage_id=?", (cage["id"],)
        ).fetchall()
        lock_rows = conn.execute(
            "SELECT * FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchall()
        contention_rows = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=?", (lock_key,)
        ).fetchall()

    # 释锁命运：业务成功收尾后，笼锁占行必须为空
    assert lock_rows == [], f"释锁命运未闭环，残锁={[dict(x) for x in lock_rows]}"
    # 锁争用：后到一方在争用表留痕，且只有一条
    assert len(contention_rows) == 1, (
        f"后到争用未留痕或重复留痕：contention={[dict(x) for x in contention_rows]}"
    )
    assert contention_rows[0]["action"] == "争用"
    assert contention_rows[0]["actor"] in {"admin", "clerk"}
    # 后到不得穿透写入：业务只落一笔拨付
    assert len(release_rows) == 1, (
        f"争用穿透写入：拨付落 {len(release_rows)} 笔，应为 1"
    )
