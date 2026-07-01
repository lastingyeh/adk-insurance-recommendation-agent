from __future__ import annotations

import json
from importlib import import_module
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.genai import types as genai_types

from app.api.dependencies import reset_dependency_caches, get_current_user
from app.config import AppRuntimeConfig
from app.container import create_session_store
from app.api.schemas import UserInDB


def override_get_current_user():
    return UserInDB(
        user_id=1, username="test-user", hashed_password="mock", is_active=True
    )


@pytest.fixture
def test_app(monkeypatch, postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")

    monkeypatch.setenv("ADK_APP_NAME", "app")
    monkeypatch.setenv("ADK_API_USER_ID", "test-user")
    monkeypatch.setenv("ADK_SESSION_DB_URI", db_url)
    monkeypatch.setenv("TOOLBOX_SERVER_URL", "http://127.0.0.1:5999")
    monkeypatch.setenv("FASTAPI_CORS_ALLOW_ORIGINS", "http://localhost:3000")

    reset_dependency_caches()
    main_module = import_module("app.api.main")
    app = main_module.create_app()
    app.dependency_overrides[get_current_user] = override_get_current_user
    return app


@pytest.fixture
def test_client(test_app):
    return TestClient(test_app)


def parse_sse_frames(response_text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for chunk in response_text.split("\n\n"):
        if not chunk.strip():
            continue
        lines = [line for line in chunk.splitlines() if line.startswith("data: ")]
        if not lines:
            continue
        payload = "\n".join(line[6:] for line in lines)
        frames.append(json.loads(payload))
    return frames


def test_healthz_returns_ok(test_client):
    response = test_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "appName": "app"}


@pytest.mark.asyncio
async def test_session_crud_round_trip(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        app_name = "app"
        user_id = "test-user"
        base_url = f"/apps/{app_name}/users/{user_id}/sessions"

        response = await client.get(base_url)
        assert response.status_code == 200
        assert response.json() == {"sessions": []}

        create_response = await client.post(
            base_url,
            json={
                "sessionId": "session-123",
                "state": {
                    "_ui_title": "新對話",
                    "_ui_subtitle": "開始新的對話",
                    "user:age": 30,
                },
            },
        )
        assert create_response.status_code == 200
        assert create_response.json() == {"ok": True, "sessionId": "session-123"}

        list_response = await client.get(base_url)
        assert list_response.status_code == 200
        payload = list_response.json()
        assert len(payload["sessions"]) == 1
        session = payload["sessions"][0]
        assert session["id"] == "session-123"
        assert session["title"] == "新對話"
        assert session["subtitle"] == "開始新的對話"
        assert session["status"] == "idle"
        assert session["state"] != {}

        delete_response = await client.delete(f"{base_url}/session-123")
        assert delete_response.status_code == 200
        assert delete_response.json() == {"ok": True}

        final_list_response = await client.get(base_url)
        assert final_list_response.status_code == 200
        assert final_list_response.json() == {"sessions": []}


def test_create_session_store_uses_database_service_for_postgres_uri():
    config = AppRuntimeConfig(
        app_name="app",
        api_user_id="test-user",
        toolbox_server_url="http://127.0.0.1:5999",
        session_db_uri="postgresql+asyncpg://user:pass@localhost/db",
        memory_mode="database",
        model_name="gemini-2.5-flash",
        live_model_name="gemini-live-2.5-flash-preview-native-audio-09-2025",
        fastapi_host="127.0.0.1",
        fastapi_port=8080,
        fastapi_reload=False,
        cors_allow_origins=("http://localhost:3000",),
        audit_enabled=False,
        audit_db_path="postgresql+asyncpg://user:pass@localhost/audit",
        audit_retention_days=30,
        audit_hash_salt="test-salt",
        pii_redaction_enabled=True,
        max_output_tokens=2048,
        enable_cloud_tracing=False,
        enable_cloud_logging=False,
        otel_service_name="test-service",
        jwt_secret="test-secret",
        jwt_algorithm="HS256",
        access_token_expire_minutes=1440,
        bigquery_analytics_dataset=None,
        bigquery_location="US",
        google_cloud_project=None,
    )
    session_store = create_session_store(config)

    assert isinstance(session_store, DatabaseSessionService)


def test_run_stream_returns_sse_envelopes(test_client, monkeypatch):
    run_module = import_module("app.api.routes.run")

    class FakeRunner:
        async def run_async(
            self,
            *,
            user_id,
            session_id,
            invocation_id=None,
            new_message=None,
            state_delta=None,
            run_config=None,
        ):
            yield Event(
                invocation_id="inv-1",
                author="app",
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part(
                            function_call=genai_types.FunctionCall(
                                name="search_medical_products",
                                args={"age": 30},
                            )
                        )
                    ],
                ),
            )
            yield Event(
                invocation_id="inv-1",
                author="app",
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part(
                            function_response=genai_types.FunctionResponse(
                                name="search_medical_products",
                                response={"top_product": "安心醫療"},
                            )
                        )
                    ],
                ),
            )
            yield Event(
                invocation_id="inv-1",
                author="app",
                partial=True,
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part(text="先根據你的預算")],
                ),
            )
            yield Event(
                invocation_id="inv-1",
                author="app",
                actions=EventActions(
                    state_delta={"user:last_recommended_product_name": "安心醫療"}
                ),
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part(text="推薦安心醫療，保障與預算較匹配。")],
                ),
            )

    monkeypatch.setattr(run_module, "get_runner", lambda: FakeRunner())

    response = test_client.post(
        "/api/agent/run",
        json={
            "prompt": "我 30 歲，年預算 15000，想加強醫療保障",
            "sessionId": "session-run-1",
            "sessionState": {"user:age": "30", "user:budget": "15000"},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = parse_sse_frames(response.text)
    assert frames[0]["type"] == "meta"

    def has_timeline_kind(frames, kind_val):
        for frame in frames:
            if frame.get("type") == "timeline":
                event = frame.get("event")
                if isinstance(event, dict) and event.get("kind") == kind_val:
                    return True
        return False

    assert has_timeline_kind(frames, "tool-call")
    assert has_timeline_kind(frames, "tool-result")

    assert any(
        isinstance(frame, dict)
        and frame.get("type") == "message"
        and frame.get("mode") == "replace"
        for frame in frames
    )

    assert any(
        isinstance(frame, dict)
        and frame.get("type") == "state"
        and isinstance(frame.get("patch"), dict)
        and frame["patch"].get("user:last_recommended_product_name") == "安心醫療"
        for frame in frames
    )

    done_frame = next(
        (
            frame
            for frame in frames
            if isinstance(frame, dict) and frame.get("type") == "done"
        ),
        None,
    )
    assert done_frame is not None
    assert done_frame.get("finalText") == "推薦安心醫療，保障與預算較匹配。"
    state = done_frame.get("state")
    assert isinstance(state, dict)
    assert state.get("user:last_recommended_product_name") == "安心醫療"


def test_run_stream_returns_error_envelope_when_runner_fails(test_client, monkeypatch):
    run_module = import_module("app.api.routes.run")

    class FailingRunner:
        async def run_async(
            self,
            *,
            user_id,
            session_id,
            invocation_id=None,
            new_message=None,
            state_delta=None,
            run_config=None,
        ):
            if False:
                yield None
            raise RuntimeError("runner failed")

    monkeypatch.setattr(run_module, "get_runner", lambda: FailingRunner())

    response = test_client.post(
        "/api/agent/run",
        json={
            "prompt": "測試錯誤路徑",
            "sessionId": "session-run-error",
            "sessionState": {},
        },
    )

    assert response.status_code == 200
    frames = parse_sse_frames(response.text)
    assert frames[0]["type"] == "meta"
    assert frames[-1] == {"type": "error", "message": "runner failed"}
