"""接口级测试：搜索记录、复习调度接口、鉴权。"""

from datetime import date, timedelta

from app.models import ReviewCard, ReviewLog
from app.routers.records import add_record


def test_records_require_login(client):
    assert client.get("/api/records").status_code == 401
    assert client.delete("/api/records").status_code == 401


def test_records_list_search_filter_and_delete(client, db, user):
    add_record(db, user["id"], "chat", "实践是认识的来源", "实践是认识的来源吗", "## 结论\n是的")
    add_record(db, user["id"], "ocr", "矛盾的普遍性（判题：错误）", "题目……", "## 判断\n错误")
    add_record(db, user["id"], "agents", "出题：史纲", "史纲", "## 单选题\n题干：……")

    all_records = client.get("/api/records", headers=user["headers"]).json()
    assert len(all_records) == 3
    assert all_records[0]["title"] == "出题：史纲"  # 按时间倒序

    hits = client.get("/api/records", params={"q": "实践"}, headers=user["headers"]).json()
    assert [r["title"] for r in hits] == ["实践是认识的来源"]

    ocr_only = client.get("/api/records", params={"source": "ocr"}, headers=user["headers"]).json()
    assert len(ocr_only) == 1 and ocr_only[0]["source_label"] == "拍题判卷"

    target = ocr_only[0]["id"]
    assert client.delete(f"/api/records/{target}", headers=user["headers"]).json()["deleted"] is True
    assert client.delete(f"/api/records/{target}", headers=user["headers"]).status_code == 404

    assert client.delete("/api/records", headers=user["headers"]).json()["deleted"] == 2
    assert client.get("/api/records", headers=user["headers"]).json() == []


def test_records_are_isolated_between_users(client, db, user):
    add_record(db, user["id"], "chat", "只属于我的记录", "q", "a")
    other = client.post("/api/auth/register", json={"username": f"o_{user['id']}x", "password": "secret123"}).json()
    other_headers = {"Authorization": f"Bearer {other['token']}"}
    assert client.get("/api/records", headers=other_headers).json() == []


def _make_card(db, user_id, **overrides):
    fields = {
        "user_id": user_id,
        "question": "社会发展的最终决定力量是什么？",
        "answer": "生产力。",
        "knowledge_point": "历史唯物主义",
        "next_review_date": date.today(),
    }
    fields.update(overrides)
    card = ReviewCard(**fields)
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def test_today_queue_and_forgotten_card_stays_today(client, db, user):
    card = _make_card(db, user["id"])
    today = client.get("/api/review/today", headers=user["headers"]).json()
    assert [c["id"] for c in today] == [card.id]

    resp = client.post("/api/review/submit", json={"card_id": card.id, "quality": 1}, headers=user["headers"])
    assert resp.status_code == 200
    assert resp.json()["next_review_date"] == date.today().isoformat()
    assert resp.json()["lapses"] == 1

    still_today = client.get("/api/review/today", headers=user["headers"]).json()
    assert [c["id"] for c in still_today] == [card.id]


def test_remembered_card_leaves_today_and_writes_log(client, db, user):
    card = _make_card(db, user["id"])
    resp = client.post("/api/review/submit", json={"card_id": card.id, "quality": 5}, headers=user["headers"])
    assert resp.status_code == 200
    assert resp.json()["repetition"] == 1
    assert resp.json()["next_review_date"] == (date.today() + timedelta(days=1)).isoformat()

    assert client.get("/api/review/today", headers=user["headers"]).json() == []

    logs = db.query(ReviewLog).filter(ReviewLog.card_id == card.id).all()
    assert len(logs) == 1
    assert logs[0].quality == 5 and logs[0].interval_after == 1


def test_cannot_review_someone_elses_card(client, db, user):
    card = _make_card(db, user["id"])
    other = client.post("/api/auth/register", json={"username": f"p_{card.id}z", "password": "secret123"}).json()
    resp = client.post(
        "/api/review/submit",
        json={"card_id": card.id, "quality": 5},
        headers={"Authorization": f"Bearer {other['token']}"},
    )
    assert resp.status_code == 404


def test_learning_dashboard_aggregates_review_logs(client, db, user):
    empty = client.get("/api/stats/learning", headers=user["headers"]).json()
    assert empty["summary"]["total_reviews"] == 0
    assert empty["summary"]["accuracy"] is None
    assert len(empty["daily"]) == 14

    card_a = _make_card(db, user["id"], knowledge_point="认识论")
    card_b = _make_card(db, user["id"], knowledge_point="唯物史观")
    client.post("/api/review/submit", json={"card_id": card_a.id, "quality": 1}, headers=user["headers"])
    client.post("/api/review/submit", json={"card_id": card_a.id, "quality": 5}, headers=user["headers"])
    client.post("/api/review/submit", json={"card_id": card_b.id, "quality": 4}, headers=user["headers"])

    data = client.get("/api/stats/learning", headers=user["headers"]).json()
    assert data["summary"]["total_reviews"] == 3
    assert data["summary"]["accuracy"] == 67
    assert data["summary"]["streak_days"] == 1
    assert data["daily"][-1]["reviews"] == 3 and data["daily"][-1]["accuracy"] == 67
    assert data["retention"][0] == {"name": "1 天", "reviews": 3, "rate": 67}
    assert data["weak_points"] == [{"name": "认识论", "lapses": 1, "cards": 1}]


def test_login_rejects_wrong_password_and_expired_token(client, user):
    bad = client.post("/api/auth/login", json={"username": "nobody_here", "password": "secret123"})
    assert bad.status_code == 400
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401
    assert client.get("/api/auth/me", headers=user["headers"]).status_code == 200
