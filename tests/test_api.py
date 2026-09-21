"""API 集成测试：使用临时 SQLite 库，验证谱系传播与整批原子性。"""
import os
import tempfile
from decimal import Decimal

# 必须在导入 app 之前指向临时数据库
_tmp_db = os.path.join(tempfile.mkdtemp(prefix="lineage-test-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client


def register(client, container, volume, sample=None):
    payload = {"container_id": container, "volume": volume}
    if sample:
        payload["sample_id"] = sample
    response = client.post("/samples", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_register_sample_and_query(client):
    register(client, "tube-A", "10.0", sample="blood-001")
    state = client.get("/containers/tube-A").json()
    assert state["volume"] == "10.0"
    assert state["composition"] == [
        {"sample_id": "blood-001", "fraction": "1", "percentage": "100"}
    ]


def test_duplicate_registration_rejected(client):
    register(client, "tube-A", "10.0")
    response = client.post("/samples", json={"container_id": "tube-A", "volume": "5"})
    assert response.status_code == 409


def test_unknown_container_404(client):
    assert client.get("/containers/nope").status_code == 404


def test_single_transfer_propagates_sources(client):
    register(client, "A", "10.0", sample="s1")
    register(client, "B", "5.0", sample="s2")
    response = client.post(
        "/transfers",
        json={"transfers": [{"source": "A", "destination": "B", "volume": "5.0"}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == 1
    assert body["transfers"][0]["source_volume_after"] == "5.0"
    assert body["transfers"][0]["destination_volume_after"] == "10.0"

    state = client.get("/containers/B").json()
    comp = {e["sample_id"]: Decimal(e["fraction"]) for e in state["composition"]}
    assert comp == {"s1": Decimal("0.5"), "s2": Decimal("0.5")}
    assert sum(comp.values()) == 1


def test_chained_batch_tracks_all_ancestors(client):
    register(client, "A", "10", sample="s1")
    register(client, "B", "10", sample="s2")
    register(client, "C", "1", sample="s3")
    # A -> B -> C 依次发生，C 应携带 s1/s2/s3 三个祖先来源
    response = client.post(
        "/transfers",
        json={
            "transfers": [
                {"source": "A", "destination": "B", "volume": "2"},
                {"source": "B", "destination": "C", "volume": "3"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    state = client.get("/containers/C").json()
    comp = {e["sample_id"]: Decimal(e["fraction"]) for e in state["composition"]}
    # B 中 s1 占 2/12=1/6；移出 3 中 s1 为 0.5；C 原体积 1 -> 总体积 4
    assert comp["s1"] == Decimal("0.125")
    assert comp["s2"] == Decimal("0.625")
    assert comp["s3"] == Decimal("0.25")
    assert sum(comp.values()) == 1
    assert Decimal(state["volume"]) == Decimal("4")


def test_decimal_precision_no_float_artifacts(client):
    register(client, "A", "0.3")
    register(client, "B", "0.3")
    # 0.3 - 0.1 - 0.2 在二进制浮点下不为 0，Decimal 下应精确归零
    response = client.post(
        "/transfers",
        json={
            "transfers": [
                {"source": "A", "destination": "B", "volume": "0.1"},
                {"source": "A", "destination": "B", "volume": "0.2"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert client.get("/containers/B").json()["volume"] == "0.6"
    assert client.get("/containers/A").json()["volume"] == "0.0"


def test_insufficient_volume_rejects_whole_batch(client):
    register(client, "A", "5")
    register(client, "B", "5")
    register(client, "C", "5")
    response = client.post(
        "/transfers",
        json={
            "transfers": [
                {"source": "A", "destination": "B", "volume": "2"},  # 合法
                {"source": "A", "destination": "C", "volume": "4"},  # 余量仅 3，失败
            ]
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["batch_rejected"] is True
    assert detail["failed_index"] == 1
    assert detail["reason"] == "insufficient_volume"
    # 整批回滚：第一条转移也不生效
    assert client.get("/containers/A").json()["volume"] == "5"
    assert client.get("/containers/B").json()["volume"] == "5"


def test_unknown_container_rejects_batch(client):
    register(client, "A", "5")
    response = client.post(
        "/transfers",
        json={"transfers": [{"source": "A", "destination": "ghost", "volume": "1"}]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "unknown_container"
    assert client.get("/containers/A").json()["volume"] == "5"


def test_self_transfer_rejected(client):
    register(client, "A", "5")
    response = client.post(
        "/transfers",
        json={"transfers": [{"source": "A", "destination": "A", "volume": "1"}]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "self_transfer"


def test_full_transfer_empties_source(client):
    register(client, "A", "5", sample="s1")
    register(client, "B", "5", sample="s2")
    response = client.post(
        "/transfers",
        json={"transfers": [{"source": "A", "destination": "B", "volume": "5"}]},
    )
    assert response.status_code == 200
    assert client.get("/containers/A").json()["volume"] == "0"
    state = client.get("/containers/B").json()
    assert state["volume"] == "10"
    comp = {e["sample_id"]: e["fraction"] for e in state["composition"]}
    assert comp == {"s1": "0.5", "s2": "0.5"}


def test_invalid_payloads(client):
    register(client, "A", "5")
    # 体积必须为正
    assert (
        client.post(
            "/transfers",
            json={"transfers": [{"source": "A", "destination": "A", "volume": "0"}]},
        ).status_code
        == 422
    )
    # 空批次非法
    assert client.post("/transfers", json={"transfers": []}).status_code == 422
    # 登记体积必须为正
    assert (
        client.post("/samples", json={"container_id": "Z", "volume": "-1"}).status_code
        == 422
    )
