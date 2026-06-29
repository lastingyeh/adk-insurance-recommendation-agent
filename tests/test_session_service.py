import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.session_service import SessionService
from app.config import AppRuntimeConfig


@pytest.fixture
def mock_session_store():
    return AsyncMock()


@pytest.fixture
def config():
    return AppRuntimeConfig(
        app_name="test-app",
        api_user_id="test-user",
        toolbox_server_url="http://localhost",
        session_db_uri="sqlite+aiosqlite:///:memory:",
        model_name="gemini-2.5-flash",
        live_model_name="gemini-live-2.5-flash-native-audio",
        memory_mode="in_memory",
        fastapi_host="127.0.0.1",
        fastapi_port=8080,
        fastapi_reload=False,
        cors_allow_origins=("http://localhost:3000",),
        audit_enabled=False,
        audit_db_path="./db/audit_test.db",
        audit_retention_days=30,
        audit_hash_salt="test-salt",
        pii_redaction_enabled=True,
        max_output_tokens=2048,
        enable_cloud_tracing=False,
        enable_cloud_logging=False,
        otel_service_name="test-service",
        jwt_secret="test-secret",
        jwt_algorithm="HS256",
        access_token_expire_minutes=30,
        bigquery_analytics_dataset=None,
        bigquery_location="US",
        google_cloud_project=None,
    )


@pytest.mark.asyncio
async def test_update_session_appends_event(mock_session_store, config):
    service = SessionService(mock_session_store, config)
    session_id = "test-session"
    state_delta = {"key": "value"}

    # Mock get_session to return something so ensure_session doesn't try to create it
    mock_session_store.get_session.return_value = MagicMock()

    await service.update_session(session_id, state_delta)

    # Verify ensure_session was called (indirectly via get_session)
    mock_session_store.get_session.assert_called()

    # Verify append_event was called with an Event containing the state_delta
    mock_session_store.append_event.assert_called_once()
    args, kwargs = mock_session_store.append_event.call_args

    assert "session" in kwargs

    event = kwargs["event"]
    assert event.author == "system"
    assert event.actions.state_delta == {"key": "value"}
    assert event.content.role == "system"


@pytest.mark.asyncio
async def test_update_session_stringifies_values(mock_session_store, config):
    service = SessionService(mock_session_store, config)
    session_id = "test-session"
    state_delta = {"proactive": True, "count": 123}

    mock_session_store.get_session.return_value = MagicMock()

    await service.update_session(session_id, state_delta)

    args, kwargs = mock_session_store.append_event.call_args
    assert "session" in kwargs
    event = kwargs["event"]

    # Should be stringified
    assert event.actions.state_delta == {"proactive": "True", "count": "123"}
