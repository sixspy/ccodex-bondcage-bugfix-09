"""失败测乙：对账绿灯不得与笼锁残留并存。

事故口径：业务停摆页面显示成功闭环，而锁表仍占笼。
正确口径只有一条：锁表若残留占笼，对账绿灯必须零增熄灭，
成功摘要回退为未完成痕；不得绿成功与死锁表并存。
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
    c: TestClient, h: dict, code: str = "CG-GREEN-001", party: str = "P-GREEN"
) -> dict:
    assert (
        c.post(
            "/api/bonds",
            headers=h,
            json={
                "party_key": party,
                "party_name": "绿锁并存测试",
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
            "contract_no": "HT-GREEN-1",
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


def test_reconcile_green_must_not_coexist_with_residual_cage_lock():
    c = TestClient(app)
    h = _auth(c)
    cage = _prepare_releasable_cage(c, h)
    lock_key = f"cage:{cage['id']}"

    rel = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert rel.status_code == 200, rel.text

    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h).json()
    with connect() as conn:
        lock_rows = conn.execute(
            "SELECT * FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchall()
        all_lock_rows = conn.execute("SELECT * FROM cage_locks").fetchall()

    coexists = bool(recon["balanced"]) and (bool(lock_rows) or bool(all_lock_rows))
    assert not coexists, (
        "对账绿灯与笼锁残留并存（页面绿、业务停摆）："
        f"balanced={recon['balanced']} locks={[dict(x) for x in all_lock_rows]}"
    )
