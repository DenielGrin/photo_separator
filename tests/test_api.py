from fastapi.testclient import TestClient

from photo_separator.app import app


def test_no_secrets_echoed_and_origin_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_DB", str(tmp_path / "test.db"))
    with TestClient(app) as client:
        r = client.post(
            "/api/select",
            json={
                "restaurant_id": "r",
                "urls": ["https://example.com/a.jpg"],
                "config": {"provider": "INVALID", "api_key": "super-secret"},
            },
        )
        assert r.status_code == 422 and "super-secret" not in r.text
        r = client.post("/api/select", headers={"origin": "https://evil.example"}, json={})
        assert r.status_code == 403
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/restaurants/new/cover").json()["cover"] == []


def test_key_missing_before_any_download(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/select",
            json={
                "restaurant_id": "r",
                "urls": ["https://example.com/a.jpg"],
                "config": {"provider": "openai", "model": "gpt-6-luna"},
            },
        )
        assert r.status_code == 400 and "missing" in r.json()["detail"]
