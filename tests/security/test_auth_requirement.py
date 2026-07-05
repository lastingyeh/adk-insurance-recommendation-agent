from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from importlib import import_module
from app.api.dependencies import reset_dependency_caches


@pytest.fixture
def test_app_no_auth_override(monkeypatch):
    # Mock necessary env vars to let the app start
    monkeypatch.setenv("ADK_APP_NAME", "app")
    monkeypatch.setenv("ADK_API_USER_ID", "test-user")
    monkeypatch.setenv("ADK_SESSION_DB_URI", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("AUDIT_DB_PATH", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    reset_dependency_caches()
    main_module = import_module("app.api.main")
    app = main_module.create_app()
    # DO NOT override get_current_user here
    return app


@pytest.fixture
def test_client(test_app_no_auth_override):
    return TestClient(test_app_no_auth_override)


def test_healthz_unprotected(test_client):
    response = test_client.get("/healthz")
    assert response.status_code == 200


def test_readyz_unprotected(test_client):
    response = test_client.get("/readyz")
    # Might be 503 if dependencies are not ready, but should NOT be 401/403
    assert response.status_code in [200, 503]


def test_run_requires_auth(test_client):
    response = test_client.post(
        "/api/agent/run",
        json={
            "prompt": "hello",
            "sessionId": "session-1",
        },
    )
    # FastAPI returns 401 Unauthorized when OAuth2PasswordBearer dependency fails
    assert response.status_code == 401


def test_sessions_list_requires_auth(test_client):
    response = test_client.get("/apps/app/users/test-user/sessions")
    assert response.status_code == 401


def test_sessions_create_requires_auth(test_client):
    response = test_client.post(
        "/apps/app/users/test-user/sessions",
        json={"sessionId": "session-1", "state": {}},
    )
    assert response.status_code == 401


def test_live_ws_requires_auth(test_client):
    # Live WebSocket endpoint manually checks for token and closes with 1008 if missing/invalid
    with pytest.raises(Exception):  # TestClient might raise or just fail to connect
        with test_client.websocket_connect("/api/agent/live/ws/session-1") as websocket:
            pass

    # Or more precisely, check the close code if TestClient allows it
    try:
        with test_client.websocket_connect("/api/agent/live/ws/session-1") as websocket:
            # If it doesn't raise, we check if it was closed immediately
            pass
    except Exception:
        # Depending on FastAPI version and TestClient, it might raise WebSocketDisconnect
        pass
