"""Health probe contract: 200 + body, no auth required."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok_without_auth() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
