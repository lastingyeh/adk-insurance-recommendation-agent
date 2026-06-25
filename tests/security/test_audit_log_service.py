import hashlib
import hmac
import json

import asyncpg
import pytest

from app.services.audit_log_service import (
    AuditContext,
    AuditLogService,
    ChainVerificationResult,
    _is_placeholder_salt,
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


async def _fetch_chain(db_url: str):
    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        return await conn.fetch(
            "SELECT chain_index, prev_hash, event_hash FROM audit_events "
            "ORDER BY chain_index ASC"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_prev_hash_links_across_service_instances(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc_a = AuditLogService(
        db_url=db_url, hash_salt="test-salt", retention_days=365, enabled=True
    )
    await svc_a.initialize()
    await _clean(db_url)

    await svc_a.record(context=_ctx(1), event_type="e1", actor="user", sequence=1)
    await svc_a.record(context=_ctx(2), event_type="e2", actor="user", sequence=2)

    # 模擬服務重啟：全新實例，無記憶體 _last_hash
    svc_b = AuditLogService(
        db_url=db_url, hash_salt="test-salt", retention_days=365, enabled=True
    )
    await svc_b.record(context=_ctx(3), event_type="e3", actor="user", sequence=3)

    rows = await _fetch_chain(db_url)
    assert len(rows) == 3
    assert rows[0]["prev_hash"] is None
    assert rows[1]["prev_hash"] == rows[0]["event_hash"]
    # 關鍵：重啟後新實例仍接續 DB 最後一筆，而非寫成 None
    assert rows[2]["prev_hash"] == rows[1]["event_hash"]


@pytest.mark.asyncio
async def test_verify_chain_ok_for_intact_chain(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc = AuditLogService(db_url=db_url, hash_salt="k", retention_days=365, enabled=True)
    await svc.initialize()
    await _clean(db_url)
    for i in range(1, 4):
        await svc.record(context=_ctx(i), event_type=f"e{i}", actor="user", sequence=i)

    result = await svc.verify_chain()
    assert result.ok is True
    assert result.checked_count == 3
    assert result.broken_chain_index is None


@pytest.mark.asyncio
async def test_verify_chain_detects_tampering(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc = AuditLogService(db_url=db_url, hash_salt="k", retention_days=365, enabled=True)
    await svc.initialize()
    await _clean(db_url)
    for i in range(1, 4):
        await svc.record(context=_ctx(i), event_type=f"e{i}", actor="user", sequence=i)

    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        await conn.execute(
            "UPDATE audit_events SET input_redacted = 'tampered' "
            "WHERE chain_index = (SELECT MIN(chain_index) + 1 FROM audit_events)"
        )
    finally:
        await conn.close()

    result = await svc.verify_chain()
    assert result.ok is False
    assert result.reason == "tampered"


@pytest.mark.asyncio
async def test_verify_chain_detects_deletion(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc = AuditLogService(db_url=db_url, hash_salt="k", retention_days=365, enabled=True)
    await svc.initialize()
    await _clean(db_url)
    for i in range(1, 4):
        await svc.record(context=_ctx(i), event_type=f"e{i}", actor="user", sequence=i)

    clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(clean_url)
    try:
        await conn.execute(
            "DELETE FROM audit_events "
            "WHERE chain_index = (SELECT MIN(chain_index) + 1 FROM audit_events)"
        )
    finally:
        await conn.close()

    result = await svc.verify_chain()
    assert result.ok is False
    assert result.reason == "broken_link"


@pytest.mark.asyncio
async def test_verify_chain_fails_with_wrong_key(postgres_container):
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc = AuditLogService(db_url=db_url, hash_salt="right-key", retention_days=365, enabled=True)
    await svc.initialize()
    await _clean(db_url)
    for i in range(1, 4):
        await svc.record(context=_ctx(i), event_type=f"e{i}", actor="user", sequence=i)

    wrong = AuditLogService(db_url=db_url, hash_salt="wrong-key", retention_days=365, enabled=True)
    result = await wrong.verify_chain()
    assert result.ok is False
    assert result.reason == "tampered"


def test_is_placeholder_salt_covers_known_defaults():
    assert _is_placeholder_salt("change-me-in-production") is True
    assert _is_placeholder_salt("dev-only-change-me") is True
    assert _is_placeholder_salt("CHANGE-ME") is True
    assert _is_placeholder_salt("") is True
    assert _is_placeholder_salt("   ") is True
    assert _is_placeholder_salt("a-real-random-secret-9f3b") is False


@pytest.mark.asyncio
async def test_initialize_warns_on_placeholder_salt(postgres_container, caplog):
    import logging

    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc = AuditLogService(
        db_url=db_url, hash_salt="change-me-in-production", retention_days=365, enabled=True
    )
    with caplog.at_level(logging.WARNING):
        await svc.initialize()

    assert any("AUDIT_HASH_SALT" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_verification_returns_result(postgres_container):
    from scripts.verify_audit_chain import run_verification

    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    svc = AuditLogService(db_url=db_url, hash_salt="k", retention_days=365, enabled=True)
    await svc.initialize()
    await _clean(db_url)
    for i in range(1, 3):
        await svc.record(context=_ctx(i), event_type=f"e{i}", actor="user", sequence=i)

    result = await run_verification(svc)
    assert result.ok is True
    assert result.checked_count == 2
