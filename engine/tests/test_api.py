"""API 层：token 鉴权与基础路由。"""

import pytest
from fastapi.testclient import TestClient

from jianwei.api.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JIANWEI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JIANWEI_TOKEN", "secret")
    return TestClient(app)


def test_health_open_without_token(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_routes_reject_missing_or_wrong_token(client):
    assert client.get("/stocks").status_code == 401
    assert client.get("/stocks", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_stocks_with_token_on_empty_db(client):
    r = client.get("/stocks", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json() == []


def test_picks_requires_data(client):
    r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 409
