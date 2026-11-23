"""失败测甲：解笼拨付成功后，笼锁行必须被删除。

事故口径：解笼成功、审计时间线绿灯，但 cage_locks 仍占着该笼键，
后来者一律争用红灯。正确收尾只能有一条：出金成败边界内必释锁占行。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _prepare_releasable_cage(
    c: TestClient, h: dict, code: str = "CG-LOCK-001", party: str = "P-LOCK"
) -> dict:
    assert (
        c.post(
            "/api/bonds",
            headers=h,
            json={
                "party_key": party,
                "party_name": "锁残留测试",
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
            "contract_no": "HT-LOCK-1",
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


def test_release_success_must_delete_cage_lock_row():
    c = TestClient(app)
    h = _auth(c)
    cage = _prepare_releasable_cage(c, h)
    lock_key = f"cage:{cage['id']}"

    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "已拨付"

    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchall()

    assert rows == [], (
        "解笼拨付已成功展示，但笼锁行仍残留，后会者必被争用红灯："
        f"{[dict(x) for x in rows]}"
    )


def test_release_business_failure_must_still_delete_cage_lock_row():
    """测甲失败侧：出金校验失败（金额超可用额）也必须在收尾侧删锁。

    否则业务失败本身就会留下残锁，后人同样被堵死。
    """
    c = TestClient(app)
    h = _auth(c)
    cage = _prepare_releasable_cage(
        c, h, code="CG-LOCK-FAIL-001", party="P-LOCK-FAIL"
    )
    lock_key = f"cage:{cage['id']}"

    r = c.post(
        "/api/releases",
        headers=h,
        json={"cage_id": cage["id"], "amount": 999999},
    )
    assert r.status_code == 409, r.text

    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchall()
        release_rows = conn.execute(
            "SELECT * FROM releases WHERE cage_id=?", (cage["id"],)
        ).fetchall()

    assert release_rows == [], "业务失败却落了拨付行"
    assert rows == [], (
        "业务失败后笼锁行仍残留，后人被堵死："
        f"{[dict(x) for x in rows]}"
    )
