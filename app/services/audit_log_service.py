from __future__ import annotations

import asyncpg
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.security.pii import redact_jsonable, stable_hash

logger = logging.getLogger(__name__)

# pg_advisory_xact_lock 的固定鍵，序列化雜湊鏈的 append（"AUDT" 的 ASCII）
_CHAIN_LOCK_KEY = 0x41554454


def _is_placeholder_salt(salt: str) -> bool:
    s = (salt or "").strip().lower()
    return (not s) or any(p in s for p in ("change-me", "changeme", "dev-only"))


@dataclass(frozen=True)
class AuditContext:
    trace_id: str
    request_id: str
    session_id: str
    user_id: str


@dataclass(frozen=True)
class ChainVerificationResult:
    ok: bool
    checked_count: int
    broken_chain_index: int | None = None
    reason: str | None = None  # "tampered" | "broken_link" | None


class AuditLogService:
    def __init__(
        self,
        *,
        db_url: str,
        hash_salt: str,
        retention_days: int,
        enabled: bool = True,
    ) -> None:
        if "postgresql+asyncpg://" in db_url:
            self._db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        else:
            self._db_url = db_url
        self._hash_salt = hash_salt
        self._retention_days = retention_days
        self._enabled = enabled

    @staticmethod
    def _build_hash_material(
        *,
        event_id: str,
        trace_id: str,
        request_id: str,
        session_id_hash: str,
        user_id_hash: str,
        event_type: str,
        actor: str,
        tool_name: str | None,
        sequence: int,
        input_redacted: Any,
        output_redacted: Any,
        pii_findings: Any,
        policy_decision: str,
        prev_hash: str | None,
        created_at: str,
    ) -> str:
        return json.dumps(
            {
                "id": event_id,
                "trace_id": trace_id,
                "request_id": request_id,
                "session_id_hash": session_id_hash,
                "user_id_hash": user_id_hash,
                "event_type": event_type,
                "actor": actor,
                "tool_name": tool_name,
                "sequence": sequence,
                "input_redacted": input_redacted,
                "output_redacted": output_redacted,
                "pii_findings": pii_findings,
                "policy_decision": policy_decision,
                "prev_hash": prev_hash,
                "created_at": created_at,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _compute_event_hash(self, material: str) -> str:
        return hmac.new(
            self._hash_salt.encode("utf-8"),
            material.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def initialize(self) -> None:
        if not self._enabled:
            return

        if _is_placeholder_salt(self._hash_salt):
            logger.warning(
                "[AUDIT] AUDIT_HASH_SALT 仍為占位值，雜湊鏈可被偽造；"
                "正式環境請以 `openssl rand -base64 32` 產生並設定 AUDIT_HASH_SALT。"
            )

        conn = await asyncpg.connect(self._db_url)
        try:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
              id TEXT PRIMARY KEY,
              trace_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              session_id_hash TEXT NOT NULL,
              user_id_hash TEXT NOT NULL,
              event_type TEXT NOT NULL,
              actor TEXT NOT NULL,
              tool_name TEXT,
              sequence INTEGER NOT NULL,
              input_redacted TEXT,
              output_redacted TEXT,
              pii_findings TEXT,
              policy_decision TEXT NOT NULL,
              event_timestamp TEXT NOT NULL,
              created_at TEXT NOT NULL,
              retention_until TEXT,
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              chain_index BIGSERIAL
            )
            """)
            await conn.execute(
                "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS chain_index BIGSERIAL"
            )
        finally:
            await conn.close()

    async def verify_chain(self) -> ChainVerificationResult:
        conn = await asyncpg.connect(self._db_url)
        try:
            rows = await conn.fetch(
                "SELECT chain_index, id, trace_id, request_id, session_id_hash, "
                "user_id_hash, event_type, actor, tool_name, sequence, "
                "input_redacted, output_redacted, pii_findings, policy_decision, "
                "created_at, prev_hash, event_hash "
                "FROM audit_events ORDER BY chain_index ASC"
            )
        finally:
            await conn.close()

        expected_prev: str | None = None
        for row in rows:
            material = self._build_hash_material(
                event_id=row["id"],
                trace_id=row["trace_id"],
                request_id=row["request_id"],
                session_id_hash=row["session_id_hash"],
                user_id_hash=row["user_id_hash"],
                event_type=row["event_type"],
                actor=row["actor"],
                tool_name=row["tool_name"],
                sequence=row["sequence"],
                input_redacted=row["input_redacted"],
                output_redacted=row["output_redacted"],
                pii_findings=json.loads(row["pii_findings"]),
                policy_decision=row["policy_decision"],
                prev_hash=row["prev_hash"],
                created_at=row["created_at"],
            )
            if self._compute_event_hash(material) != row["event_hash"]:
                return ChainVerificationResult(
                    ok=False,
                    checked_count=len(rows),
                    broken_chain_index=row["chain_index"],
                    reason="tampered",
                )
            if row["prev_hash"] != expected_prev:
                return ChainVerificationResult(
                    ok=False,
                    checked_count=len(rows),
                    broken_chain_index=row["chain_index"],
                    reason="broken_link",
                )
            expected_prev = row["event_hash"]

        return ChainVerificationResult(ok=True, checked_count=len(rows))

    async def record(
        self,
        *,
        context: AuditContext,
        event_type: str,
        actor: str,
        sequence: int,
        tool_name: str | None = None,
        input_payload: Any = None,
        output_payload: Any = None,
        policy_decision: str = "allow_redacted",
    ) -> None:
        if not self._enabled:
            return

        now = datetime.now(UTC)
        retention_until = now + timedelta(days=self._retention_days)

        input_redacted, input_findings = redact_jsonable(input_payload)
        output_redacted, output_findings = redact_jsonable(output_payload)
        pii_findings = [
            finding.__dict__ for finding in [*input_findings, *output_findings]
        ]

        session_id_hash = stable_hash(context.session_id, salt=self._hash_salt)
        user_id_hash = stable_hash(context.user_id, salt=self._hash_salt)

        event_id = str(uuid.uuid4())

        conn = await asyncpg.connect(self._db_url)
        try:
            async with conn.transaction():
                # 序列化鏈的 append，避免並發產生分叉
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)", _CHAIN_LOCK_KEY
                )
                # 從 DB 取真正的最後一筆（重啟/多副本都正確），而非記憶體狀態
                prev_hash = await conn.fetchval(
                    "SELECT event_hash FROM audit_events "
                    "ORDER BY chain_index DESC LIMIT 1"
                )
                hash_material = self._build_hash_material(
                    event_id=event_id,
                    trace_id=context.trace_id,
                    request_id=context.request_id,
                    session_id_hash=session_id_hash,
                    user_id_hash=user_id_hash,
                    event_type=event_type,
                    actor=actor,
                    tool_name=tool_name,
                    sequence=sequence,
                    input_redacted=input_redacted,
                    output_redacted=output_redacted,
                    pii_findings=pii_findings,
                    policy_decision=policy_decision,
                    prev_hash=prev_hash,
                    created_at=now.isoformat(),
                )
                event_hash = self._compute_event_hash(hash_material)
                await conn.execute(
                    """
                    INSERT INTO audit_events (
                      id, trace_id, request_id, session_id_hash, user_id_hash,
                      event_type, actor, tool_name, sequence,
                      input_redacted, output_redacted, pii_findings,
                      policy_decision, event_timestamp, created_at,
                      retention_until, prev_hash, event_hash
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                    """,
                    event_id,
                    context.trace_id,
                    context.request_id,
                    session_id_hash,
                    user_id_hash,
                    event_type,
                    actor,
                    tool_name,
                    sequence,
                    input_redacted,
                    output_redacted,
                    json.dumps(pii_findings, ensure_ascii=False),
                    policy_decision,
                    now.isoformat(),
                    now.isoformat(),
                    retention_until.isoformat(),
                    prev_hash,
                    event_hash,
                )
        finally:
            await conn.close()
