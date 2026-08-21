"""测试夹具：用独立的 SQLite 文件库跑测试，不碰开发用的 PostgreSQL。"""

import os
import uuid
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent / "test_politics.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(client):
    """注册一个随机用户，返回 {id, token, headers}。"""
    username = f"u_{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return {
        "id": data["user"]["id"],
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }
