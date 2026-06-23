import hashlib
import hmac
import json

import asyncpg
import pytest

from app.services.audit_log_service import (
    AuditContext,
    AuditLogService,
)


async def _clean(db_url: str) -> None:
    url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    await conn.execute("DELETE FROM audit_events")
    await conn.close()


def _ctx(n: int = 1) -> AuditContext:
    return AuditContext(
        trace_id=f"trace-{n}",
        request_id=f"req-{n}",
        session_id=f"session-{n}",
        user_id=f"user-{n}",
    )


@pytest.mark.asyncio
async def test_record_assigns_increasing_chain_index(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    service = AuditLogService(
        db_url=db_url, hash_salt="test-salt", retention_days=365, enabled=True
    )
    await service.initialize()
    await _clean(db_url)

    await service.record(context=_ctx(1), event_type="e1", actor="user", sequence=1)
    await service.record(context=_ctx(2), event_type="e2", actor="user", sequence=2)

    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        rows = await conn.fetch(
            "SELECT chain_index FROM audit_events ORDER BY chain_index ASC"
        )
    finally:
        await conn.close()

    assert len(rows) == 2
    assert rows[0]["chain_index"] < rows[1]["chain_index"]


@pytest.mark.asyncio
async def test_audit_log_redacts_pii_before_insert(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")

    service = AuditLogService(
        db_url=db_url,
        hash_salt="test-salt",
        retention_days=365,
        enabled=True,
    )
    await service.initialize()

    # Clear table for this test
    clean_url = db_url
    if "postgresql+asyncpg://" in clean_url:
        clean_url = clean_url.replace("postgresql+asyncpg://", "postgresql://")
    conn_clear = await asyncpg.connect(clean_url)
    await conn_clear.execute("DELETE FROM audit_events")
    await conn_clear.close()

    context = AuditContext(
        trace_id="trace-1",
        request_id="req-1",
        session_id="raw-session-id",
        user_id="raw-user-id",
    )

    await service.record(
        context=context,
        event_type="user.prompt.received",
        actor="user",
        sequence=1,
        input_payload={"prompt": "email chris@example.com phone 0912-345-678"},
    )

    # asyncpg requires postgresql:// or postgres:// scheme
    clean_url = db_url
    if "postgresql+asyncpg://" in clean_url:
        clean_url = clean_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        row = await conn.fetchrow(
            "SELECT session_id_hash, user_id_hash, input_redacted, pii_findings FROM audit_events"
        )

        session_id_hash = row["session_id_hash"]
        user_id_hash = row["user_id_hash"]
        input_redacted = row["input_redacted"]
        pii_findings = row["pii_findings"]

        assert session_id_hash != "raw-session-id"
        assert user_id_hash != "raw-user-id"
        assert "chris@example.com" not in input_redacted
        assert "0912-345-678" not in input_redacted
        assert "[REDACTED_EMAIL]" in input_redacted
        assert "[REDACTED_PHONE]" in input_redacted

        findings = json.loads(pii_findings)
        assert any(item["kind"] == "email" for item in findings)
        assert any(item["kind"] == "phone" for item in findings)
    finally:
        await conn.close()


def test_compute_event_hash_uses_hmac_not_bare_sha256():
    service = AuditLogService(
        db_url="postgresql+asyncpg://x", hash_salt="my-key", retention_days=1, enabled=True
    )
    material = '{"a":1}'
    expected_hmac = hmac.new(b"my-key", material.encode("utf-8"), hashlib.sha256).hexdigest()
    bare_sha = hashlib.sha256(material.encode("utf-8")).hexdigest()

    result = service._compute_event_hash(material)

    assert result == expected_hmac
    assert result != bare_sha


@pytest.mark.asyncio
async def test_audit_log_writes_event_hash_chain(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")

    service = AuditLogService(
        db_url=db_url,
        hash_salt="test-salt",
        retention_days=365,
        enabled=True,
    )
    # Note: table might already exist from previous test, but initialize uses IF NOT EXISTS
    await service.initialize()

    # Clear table for this test
    # asyncpg requires postgresql:// or postgres:// scheme
    clean_url = db_url
    if "postgresql+asyncpg://" in clean_url:
        clean_url = clean_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    await conn.execute("DELETE FROM audit_events")
    await conn.close()

    context = AuditContext(
        trace_id="trace-1",
        request_id="req-1",
        session_id="session-1",
        user_id="user-1",
    )

    await service.record(
        context=context,
        event_type="user.prompt.received",
        actor="user",
        sequence=1,
        input_payload={"prompt": "hello"},
    )
    await service.record(
        context=context,
        event_type="response.completed",
        actor="agent",
        sequence=2,
        output_payload={"text": "done"},
    )

    # asyncpg requires postgresql:// or postgres:// scheme
    clean_url = db_url
    if "postgresql+asyncpg://" in clean_url:
        clean_url = clean_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        rows = await conn.fetch(
            "SELECT prev_hash, event_hash FROM audit_events ORDER BY sequence"
        )

        assert len(rows) == 2
        assert rows[0]["event_hash"]
        assert rows[1]["prev_hash"] == rows[0]["event_hash"]
        assert rows[1]["event_hash"]
    finally:
        await conn.close()
