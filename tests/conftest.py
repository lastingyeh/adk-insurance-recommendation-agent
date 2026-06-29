import pytest
import pytest_asyncio
import asyncio
import asyncpg
import os
import vertexai
from pathlib import Path
from unittest.mock import MagicMock
from app.api.main import create_app
from app.container import build_app_container
from app.config import AppRuntimeConfig
from google.adk.events.event import Event
from google.genai import types as genai_types
from app.api.dependencies import get_current_user
from app.api.schemas import UserInDB
from testcontainers.postgres import PostgresContainer

# Initialize environment for Vertex AI before any agents are created
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
# Force global location for tests to ensure model availability as per GEMINI.md
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
# Project ID should be picked up from environment in Cloud Build
project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
vertexai.init(project=project_id, location="us-central1")


@pytest.fixture(scope="session")
def postgres_container():
    """Start a PostgreSQL container with pgvector."""
    # Use the pgvector image to ensure vector support is available during tests
    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()

    # Initialize schema and seed
    db_url = container.get_connection_url().replace("psycopg2", "asyncpg")

    async def init_db():
        # asyncpg requires postgresql:// or postgres:// scheme
        clean_url = db_url
        if "postgresql+asyncpg://" in clean_url:
            clean_url = clean_url.replace("postgresql+asyncpg://", "postgresql://")

        conn = await asyncpg.connect(clean_url)
        try:
            schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
            seed_path = Path(__file__).parent.parent / "db" / "seed.sql"
            audit_schema_path = Path(__file__).parent.parent / "db" / "audit_schema.sql"

            await conn.execute(schema_path.read_text())
            await conn.execute(audit_schema_path.read_text())
            await conn.execute(seed_path.read_text())
        finally:
            await conn.close()

    asyncio.run(init_db())

    yield container
    container.stop()


@pytest.fixture
def test_config(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    return AppRuntimeConfig(
        app_name="app",
        api_user_id="test-user",
        toolbox_server_url="http://127.0.0.1:5999",
        session_db_uri=db_url,
        memory_mode="in_memory",
        model_name="gemini-2.5-flash",
        live_model_name="gemini-live-2.5-flash-native-audio",
        fastapi_host="127.0.0.1",
        fastapi_port=8080,
        fastapi_reload=False,
        cors_allow_origins=("http://localhost:3000",),
        audit_enabled=True,
        audit_db_path=db_url,
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


@pytest_asyncio.fixture
async def app_with_fake_runner(test_config):
    container = build_app_container(test_config)

    # Manually initialize audit logs since lifespan might not be triggered in all test clients
    await container.audit_logs.initialize()

    class FakeRunner:
        async def run_async(self, **kwargs):
            # Yield a few events to simulate a run
            yield Event(
                invocation_id="inv-1",
                author="app",
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part(text="I am a fake runner response")],
                ),
            )

    setattr(
        container.runner, "run_async", MagicMock(side_effect=FakeRunner().run_async)
    )

    app = create_app(container)
    # Explicitly set the container in app state for tests that don't trigger lifespan
    app.state.container = container

    # Override authentication for tests
    def override_get_current_user():
        return UserInDB(
            user_id=1, username="user-test-1", hashed_password="mock", is_active=True
        )

    app.dependency_overrides[get_current_user] = override_get_current_user

    return app
