import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.agent_run_service import AgentRunService, classify_tool_name
from app.config import AppRuntimeConfig


@pytest.fixture
def mock_runner():
    return MagicMock()


@pytest.fixture
def mock_session_service():
    service = AsyncMock()
    service.get_state.return_value = {"user:age": "30"}
    return service


@pytest.fixture
def config():
    return AppRuntimeConfig(
        app_name="test-app",
        api_user_id="test-user",
        toolbox_server_url="http://localhost",
        session_db_uri="sqlite://",
        model_name="gemini-2.5-flash",
        live_model_name="gemini-live-2.5-flash-native-audio",
        memory_mode="in_memory",
        fastapi_host="127.0.0.1",
        fastapi_port=8080,
        fastapi_reload=False,
        cors_allow_origins=("http://localhost:3000"),
        audit_enabled=False,
        audit_db_path="audit.db",
        audit_retention_days=30,
        audit_hash_salt="salt",
        pii_redaction_enabled=True,
        max_output_tokens=2048,
        enable_cloud_tracing=False,
        enable_cloud_logging=False,
        otel_service_name="test-service",
        jwt_secret="secret",
        jwt_algorithm="HS256",
        access_token_expire_minutes=30,
        bigquery_analytics_dataset=None,
        bigquery_location="US",
        google_cloud_project=None,
    )


def test_classify_tool_name():
    assert classify_tool_name("save_user_profile") == "state"
    assert classify_tool_name("search_medical_products") == "query"
    assert classify_tool_name("unknown_tool") == "tool"


@pytest.mark.asyncio
async def test_ensure_session_delegation(mock_runner, mock_session_service, config):
    service = AgentRunService(mock_runner, mock_session_service, config)
    await service.ensure_session("session-1", user_id="user-1")
    mock_session_service.ensure_session.assert_called_once_with(
        "session-1", None, user_id="user-1"
    )


@pytest.mark.asyncio
async def test_stream_basic_flow(mock_runner, mock_session_service, config):
    # Setup mock runner to return an empty async generator
    async def mock_iter(*args, **kwargs):
        if False:
            yield  # Make it an async generator
        return

    mock_runner.run_async.side_effect = mock_iter

    service = AgentRunService(mock_runner, mock_session_service, config)

    events = []
    async for envelope in service.stream(prompt="hello", session_id="s1"):
        events.append(envelope)

    assert any(e["type"] == "meta" for e in events)
    assert any(e["type"] == "done" for e in events)
    mock_session_service.get_state.assert_called_once()
