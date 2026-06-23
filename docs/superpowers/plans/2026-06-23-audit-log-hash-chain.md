# 稽核日誌雜湊鏈修復 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓稽核日誌的「SHA-256 雜湊鏈防篡改」名實相符到能誠實展示的程度——改用 HMAC、prev_hash 從 DB 取得並序列化寫入、新增 verify_chain 與驗證腳本。

**Architecture:** 在 `AuditLogService` 內以 HMAC-SHA256 取代裸 SHA-256（補無密鑰）、每次寫入於單一 transaction 內加 Postgres advisory lock 並從 DB 撈最後一筆作為 prev_hash（補記憶體斷鏈與並發分叉）、新增 `verify_chain()` 與 `scripts/verify_audit_chain.py`/`make audit-verify`（補無驗證）。新增 `chain_index BIGSERIAL` 僅作確定性排序。

**Tech Stack:** Python 3、asyncpg、PostgreSQL（pgvector/pg16 容器測試）、pytest-asyncio、uv、Makefile。

## Global Constraints

- HMAC 金鑰沿用環境變數 `AUDIT_HASH_SALT`，不引入 KMS/Secret Manager。
- 不得改變 `AuditLogService.record()` 對外簽章（呼叫端 `app/services/agent_run_service.py` 不動）。
- DB schema 變更須以 `ADD COLUMN IF NOT EXISTS` 方式相容既有資料。
- 占位 salt 偵測涵蓋（不分大小寫）含 `change-me` / `changeme` / `dev-only` 或空字串。
- `config.py` 的 `audit_hash_salt` 預設對齊為 `change-me-in-production`。
- 測試使用既有 `postgres_container` fixture（`tests/conftest.py`，session scope；`get_connection_url()` 需 `.replace("psycopg2","asyncpg")`）。
- 每個測試開頭先 `DELETE FROM audit_events` 清表（沿用既有測試慣例）。
- 已知限制（不在本plan範圍）：尾端截斷偵測、金鑰外部託管、多副本極端競態。

---

### Task 1: 新增 chain_index 欄位（確定性排序）

**Files:**
- Modify: `db/audit_schema.sql`
- Modify: `app/services/audit_log_service.py`（`initialize()` 內）
- Test: `tests/security/test_audit_log_service.py`

**Interfaces:**
- Consumes: 既有 `AuditLogService(db_url, hash_salt, retention_days, enabled)`、`initialize()`、`record(...)`。
- Produces: `audit_events` 表新增 `chain_index BIGSERIAL` 欄位；record 後該欄位為遞增整數。

- [ ] **Step 1: 寫失敗測試**

加到 `tests/security/test_audit_log_service.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/security/test_audit_log_service.py::test_record_assigns_increasing_chain_index -v`
Expected: FAIL（`column "chain_index" does not exist`）

- [ ] **Step 3: 改 schema 與 initialize**

`db/audit_schema.sql`，在 `event_hash TEXT NOT NULL` 後加一欄（將該行尾逗號補上）：

```sql
  prev_hash TEXT,
  event_hash TEXT NOT NULL,
  chain_index BIGSERIAL
);
```

`app/services/audit_log_service.py` 的 `initialize()`，在 `CREATE TABLE IF NOT EXISTS ...` 的 `await conn.execute(...)` 之後、`finally` 之前加：

```python
            await conn.execute(
                "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS chain_index BIGSERIAL"
            )
```

同時在 `CREATE TABLE IF NOT EXISTS` 字串內的 `event_hash TEXT NOT NULL` 後補 `, chain_index BIGSERIAL`：

```python
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              chain_index BIGSERIAL
            )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/security/test_audit_log_service.py::test_record_assigns_increasing_chain_index -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/audit_schema.sql app/services/audit_log_service.py tests/security/test_audit_log_service.py
git commit -m "feat(audit): 新增 chain_index BIGSERIAL 作為雜湊鏈確定性排序

為後續 prev_hash 從 DB 取得與 verify_chain 依序驗證提供穩定排序依據。
僅作排序，不以斷號判定刪除（BIGSERIAL 會因 rollback 自然跳號）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: event_hash 改用 HMAC（補破口 1：無密鑰）

**Files:**
- Modify: `app/services/audit_log_service.py`
- Test: `tests/security/test_audit_log_service.py`

**Interfaces:**
- Consumes: `AuditLogService._hash_salt`（既有實例屬性）。
- Produces:
  - `AuditLogService._build_hash_material(*, event_id, trace_id, request_id, session_id_hash, user_id_hash, event_type, actor, tool_name, sequence, input_redacted, output_redacted, pii_findings, policy_decision, prev_hash, created_at) -> str`（static method，回傳 `json.dumps(..., ensure_ascii=False, sort_keys=True)`）
  - `AuditLogService._compute_event_hash(self, material: str) -> str`（回傳 `hmac.new(salt, material, sha256).hexdigest()`）

- [ ] **Step 1: 寫失敗測試**（不需 DB）

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/security/test_audit_log_service.py::test_compute_event_hash_uses_hmac_not_bare_sha256 -v`
Expected: FAIL（`AttributeError: ... _compute_event_hash`）

- [ ] **Step 3: 加 import 與兩個 helper，並讓 record() 使用**

`app/services/audit_log_service.py` 頂部 import 區加：

```python
import hmac
```

在 class `AuditLogService` 內新增兩個方法：

```python
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
```

在 `record()` 內，將原本的：

```python
        hash_material = json.dumps(
            {
                "id": event_id,
                ...
                "created_at": now.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        event_hash = hashlib.sha256(hash_material.encode("utf-8")).hexdigest()
        self._last_hash = event_hash
```

替換為（暫時保留 `self._last_hash = event_hash`，Task 3 再移除）：

```python
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
        self._last_hash = event_hash
```

- [ ] **Step 4: 跑測試確認通過（含既有 PII 測試不退步）**

Run: `uv run pytest tests/security/test_audit_log_service.py -v`
Expected: PASS（新測試 + 既有 `test_audit_log_redacts_pii_before_insert` + Task 1 測試）

- [ ] **Step 5: Commit**

```bash
git add app/services/audit_log_service.py tests/security/test_audit_log_service.py
git commit -m "feat(audit): event_hash 改用 HMAC-SHA256 取代裸 SHA-256

補破口 1（無密鑰）：原本以公開欄位的純 SHA-256 計算 event_hash，
任何能讀 DB 者皆可重算整條鏈、竄改後自我修復。改用 HMAC 後，
無金鑰即無法算出可通過驗證的 hash。抽出 _build_hash_material/
_compute_event_hash 供 record 與 verify_chain 共用，避免邏輯漂移。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: prev_hash 從 DB 取得 + 交易 + advisory lock（補破口 2、3）

**Files:**
- Modify: `app/services/audit_log_service.py`
- Test: `tests/security/test_audit_log_service.py`

**Interfaces:**
- Consumes: `_build_hash_material`、`_compute_event_hash`、`audit_events.chain_index`。
- Produces: `record()` 不再依賴記憶體 `_last_hash`；移除該屬性；新增模組常數 `_CHAIN_LOCK_KEY: int`。

- [ ] **Step 1: 寫失敗測試（鏈接續 + 模擬重啟）**

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/security/test_audit_log_service.py::test_prev_hash_links_across_service_instances -v`
Expected: FAIL（`rows[2]["prev_hash"]` 為 None，因 svc_b 的 `_last_hash` 為 None）

- [ ] **Step 3: 改寫 record() 寫入段並移除 _last_hash**

模組層級（import 之後）新增常數：

```python
# pg_advisory_xact_lock 的固定鍵，序列化雜湊鏈的 append（"AUDT" 的 ASCII）
_CHAIN_LOCK_KEY = 0x41554454
```

建構子移除這一行：

```python
        self._last_hash: str | None = None
```

`record()` 中，刪掉 `prev_hash = self._last_hash`（line 99 附近）與 `self._last_hash = event_hash`，並把「取 prev_hash → 組 material → 算 hash → INSERT」整段放進交易。將原本的：

```python
        event_id = str(uuid.uuid4())
        prev_hash = self._last_hash

        hash_material = self._build_hash_material( ... )
        event_hash = self._compute_event_hash(hash_material)
        self._last_hash = event_hash

        conn = await asyncpg.connect(self._db_url)
        try:
            await conn.execute(
                """
                INSERT INTO audit_events ( ... )
                VALUES ($1, ..., $18)
                """,
                event_id,
                ...
                prev_hash,
                event_hash,
            )
        finally:
            await conn.close()
```

替換為：

```python
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
```

- [ ] **Step 4: 跑全檔測試確認通過**

Run: `uv run pytest tests/security/test_audit_log_service.py -v`
Expected: PASS（含鏈接續/重啟測試與既有測試）

- [ ] **Step 5: Commit**

```bash
git add app/services/audit_log_service.py tests/security/test_audit_log_service.py
git commit -m "fix(audit): prev_hash 改從 DB 取得並以交易+advisory lock 序列化寫入

補破口 2（記憶體 _last_hash 重啟/多副本即斷鏈）與破口 3（並發 race
造成分叉）：移除 _last_hash，改於單一 transaction 內先取 advisory
lock，再 SELECT 最後一筆 event_hash 作為 prev_hash 後 INSERT。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: verify_chain()（補破口 4：無驗證）

**Files:**
- Modify: `app/services/audit_log_service.py`
- Test: `tests/security/test_audit_log_service.py`

**Interfaces:**
- Consumes: `_build_hash_material`、`_compute_event_hash`、`audit_events` 全欄位。
- Produces:
  - `ChainVerificationResult`（frozen dataclass：`ok: bool`、`checked_count: int`、`broken_chain_index: int | None = None`、`reason: str | None = None`）。
  - `AuditLogService.verify_chain(self) -> ChainVerificationResult`。

- [ ] **Step 1: 寫失敗測試（OK / 竄改 / 刪除中間 / 錯誤金鑰）**

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/security/test_audit_log_service.py -k verify_chain -v`
Expected: FAIL（`AttributeError: ... verify_chain` / `ChainVerificationResult` 未匯入）

- [ ] **Step 3: 實作 ChainVerificationResult 與 verify_chain()**

在檔案 dataclass 區（`AuditContext` 之後）新增：

```python
@dataclass(frozen=True)
class ChainVerificationResult:
    ok: bool
    checked_count: int
    broken_chain_index: int | None = None
    reason: str | None = None  # "tampered" | "broken_link" | None
```

在 `AuditLogService` 內新增方法：

```python
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
```

> 注意順序：先驗 HMAC（內容竄改）再驗 prev_hash 連結。刪除中間筆時，被刪筆的下一筆內容沒被動、HMAC 會過，但其 `prev_hash` 指向已消失的 hash → 由 `broken_link` 捕捉。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/security/test_audit_log_service.py -v`
Expected: PASS（4 個 verify 測試 + 先前全部）

- [ ] **Step 5: Commit**

```bash
git add app/services/audit_log_service.py tests/security/test_audit_log_service.py
git commit -m "feat(audit): 新增 verify_chain() 偵測竄改與刪除

補破口 4（無驗證）：依 chain_index 依序重算 HMAC 比對 event_hash
（tampered），並檢查 prev_hash 連結（broken_link，涵蓋刪除中間筆）。
錯誤金鑰會全數判定 tampered，證明無金鑰無法驗證或偽造。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 占位 salt 偵測與啟動警告 + config 預設對齊

**Files:**
- Modify: `app/services/audit_log_service.py`
- Modify: `app/config.py:126`
- Test: `tests/security/test_audit_log_service.py`

**Interfaces:**
- Consumes: `AuditLogService._hash_salt`、`_enabled`。
- Produces: 模組函式 `_is_placeholder_salt(salt: str) -> bool`；`initialize()` 在 enabled 且 salt 為占位值時 `logger.warning`。

- [ ] **Step 1: 寫失敗測試**

```python
from app.services.audit_log_service import _is_placeholder_salt


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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/security/test_audit_log_service.py -k "placeholder or warns" -v`
Expected: FAIL（`ImportError: _is_placeholder_salt` / 無 warning）

- [ ] **Step 3: 加 logger、helper 與 initialize 警告；對齊 config 預設**

`app/services/audit_log_service.py` 頂部 import 區加：

```python
import logging
```

import 之後（常數附近）加：

```python
logger = logging.getLogger(__name__)


def _is_placeholder_salt(salt: str) -> bool:
    s = (salt or "").strip().lower()
    return (not s) or any(p in s for p in ("change-me", "changeme", "dev-only"))
```

`initialize()` 內，在 `if not self._enabled: return` 之後、建立連線之前加：

```python
        if _is_placeholder_salt(self._hash_salt):
            logger.warning(
                "[AUDIT] AUDIT_HASH_SALT 仍為占位值，雜湊鏈可被偽造；"
                "正式環境請以 `openssl rand -base64 32` 產生並設定 AUDIT_HASH_SALT。"
            )
```

`app/config.py:126`，將：

```python
        audit_hash_salt=os.getenv("AUDIT_HASH_SALT", "dev-only-change-me"),
```

改為：

```python
        audit_hash_salt=os.getenv("AUDIT_HASH_SALT", "change-me-in-production"),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/security/test_audit_log_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/audit_log_service.py app/config.py tests/security/test_audit_log_service.py
git commit -m "feat(audit): 啟動時偵測占位 salt 並警告，對齊 config 預設

占位值偵測以子字串涵蓋三處不一致預設（change-me-in-production /
dev-only-change-me）；config.py fallback 對齊為 change-me-in-production，
消除「實際使用值」與「警告檢查值」不同步。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 驗證腳本與 make 目標（選項 B）

**Files:**
- Create: `scripts/verify_audit_chain.py`
- Create: `scripts/__init__.py`（若不存在，使 `python -m scripts.verify_audit_chain` 可執行）
- Modify: `Makefile`
- Test: `tests/security/test_audit_log_service.py`

**Interfaces:**
- Consumes: `AuditLogService`、`load_runtime_config()`（`app/config.py`，回傳 `AppRuntimeConfig`，含 `audit_db_path`、`audit_hash_salt`、`audit_retention_days`、`audit_enabled`）、`ChainVerificationResult`。
- Produces: 模組函式 `scripts.verify_audit_chain.run_verification(service) -> ChainVerificationResult`（純驗證，回傳結果，供測試呼叫）；`main()` 印出結果並以 exit code 反映（0=OK，1=損壞）。

- [ ] **Step 1: 寫失敗測試**

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/security/test_audit_log_service.py::test_run_verification_returns_result -v`
Expected: FAIL（`ModuleNotFoundError: scripts.verify_audit_chain`）

- [ ] **Step 3: 建立腳本與 make 目標**

確保 `scripts/__init__.py` 存在（空檔即可）。

建立 `scripts/verify_audit_chain.py`：

```python
"""驗證稽核日誌雜湊鏈完整性。

用法：
    make audit-verify
    # 或
    uv run python -m scripts.verify_audit_chain

exit code：0 = 鏈完整；1 = 偵測到竄改或刪除；2 = 設定/連線錯誤。
"""

from __future__ import annotations

import asyncio
import sys

from app.config import load_config
from app.services.audit_log_service import AuditLogService, ChainVerificationResult


async def run_verification(service: AuditLogService) -> ChainVerificationResult:
    return await service.verify_chain()


async def _main_async() -> int:
    config = load_config()
    service = AuditLogService(
        db_url=config.audit_db_path,
        hash_salt=config.audit_hash_salt,
        retention_days=config.audit_retention_days,
        enabled=config.audit_enabled,
    )
    result = await run_verification(service)
    if result.ok:
        print(f"✅ 稽核鏈完整，共驗證 {result.checked_count} 筆。")
        return 0
    print(
        f"❌ 稽核鏈損壞：reason={result.reason} "
        f"於 chain_index={result.broken_chain_index}（共 {result.checked_count} 筆）。"
    )
    return 1


def main() -> None:
    try:
        sys.exit(asyncio.run(_main_async()))
    except Exception as exc:  # 設定或連線錯誤
        print(f"⚠️ 驗證執行失敗：{exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
```

`Makefile` 新增目標（放在測試/評估相關區塊附近）：

```make
audit-verify: ## 驗證稽核日誌雜湊鏈完整性（偵測竄改/刪除）
	uv run python -m scripts.verify_audit_chain
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/security/test_audit_log_service.py::test_run_verification_returns_result -v`
Expected: PASS

- [ ] **Step 5: 全套測試 + lint 收尾**

Run: `uv run pytest tests/security/test_audit_log_service.py -v`
Expected: PASS（全部）

Run: `uv run ruff check app/services/audit_log_service.py scripts/verify_audit_chain.py`（若專案使用 ruff）
Expected: 無錯誤（如有未使用 import 等，修正後再跑）

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_audit_chain.py scripts/__init__.py Makefile tests/security/test_audit_log_service.py
git commit -m "feat(audit): 新增 verify_audit_chain 腳本與 make audit-verify

提供可現場執行的雜湊鏈驗證（demo 篡改→偵測），exit code 反映結果。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage：**
- HMAC（破口1）→ Task 2 ✓
- prev_hash 從 DB + 交易 + advisory lock（破口2、3）→ Task 3 ✓
- chain_index BIGSERIAL 排序 → Task 1 ✓
- verify_chain + tampered/broken_link → Task 4 ✓
- 占位 salt 偵測與警告 + config 對齊 → Task 5 ✓
- 驗證腳本 + make audit-verify（選項B）→ Task 6 ✓
- 測試：鏈串接、竄改、刪除、模擬重啟、錯誤金鑰 → Task 3/4 ✓
- 已知限制（尾端截斷等）→ 列於 Global Constraints，非實作項 ✓
- record() 對外簽章不變 → 各 Task 僅改內部 ✓

**Placeholder scan：** 無 TBD/TODO；所有 code step 均含完整程式碼。Task 6 對 `load_config` 命名加註「實作前先 grep 確認」，因設定載入函式名未在本 plan 範圍內固定。

**Type consistency：** `_build_hash_material` / `_compute_event_hash` / `verify_chain` / `ChainVerificationResult` / `_is_placeholder_salt` / `run_verification` 在定義與使用處名稱一致；`ChainVerificationResult` 欄位（ok/checked_count/broken_chain_index/reason）跨 Task 4、6 一致。
