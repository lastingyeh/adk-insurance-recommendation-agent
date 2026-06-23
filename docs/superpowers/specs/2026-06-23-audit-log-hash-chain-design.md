# 稽核日誌雜湊鏈修復設計（Demo 誠實版）

- 日期：2026-06-23
- 範圍：`app/services/audit_log_service.py`、`db/audit_schema.sql`、`app/config.py`、`scripts/`、`Makefile`、相關測試
- 目標等級：**Demo 誠實版** — 讓「SHA-256 雜湊鏈防篡改稽核日誌」名實相符到「能誠實展示」的程度

## 背景與問題

README 宣稱稽核日誌具備「SHA-256 雜湊鏈鏈結的防篡改」「一旦中途日誌被篡改或刪除，雜湊鏈條即會中斷」「軍規級安全性」。實際審查 `app/services/audit_log_service.py` 後發現四個破口，使其面對「有 DB 權限、想湮滅證據」的威脅模型幾乎無防護力：

1. **無密鑰**：`event_hash` 為裸 `sha256(公開欄位)`（`audit_log_service.py:101-122`），`hash_salt` 並未進入雜湊材料。任何能讀 DB 的人可用同一段公開邏輯重算整條鏈，篡改後自我修復。
2. **記憶體狀態斷鏈**：`_last_hash` 為實例變數（`audit_log_service.py:38`），服務重啟或多副本時 `prev_hash` 變回 `null`，鏈斷裂。
3. **並發 race**：`record()` 為 async，先讀 `_last_hash` 後 await 寫入，兩個並發請求會產生指向同一 `prev_hash` 的分叉。
4. **無驗證、無約束**：整個服務只有 `initialize()` 與 `record()`，沒有 `verify_chain()`；`db/audit_schema.sql` 的 `prev_hash` 為普通可空 `TEXT`，無序號約束，刪除中間筆無法偵測。

## 設計目標與非目標

**目標**
- 用 HMAC 取代裸 SHA-256，讓沒有金鑰者無法重算鏈（補破口 1）。
- `prev_hash` 改從 DB 讀取，並序列化寫入，使重啟/並發都不斷鏈、不分叉（補破口 2、3）。
- 新增 `verify_chain()` 與可現場執行的驗證腳本，能偵測竄改與刪除（補破口 4）。
- 維持既有 `record()` 對外介面與呼叫端（`agent_run_service.py`）不變。

**非目標（明確排除，留待未來生產級強化）**
- KMS / GCP Secret Manager 託管 HMAC 金鑰（本版沿用環境變數 `AUDIT_HASH_SALT`）。
- HTTP 驗證 endpoint。
- 最新 hash 的外部不可變錨定。
- 資料庫連線池（屬另一獨立議題）。
- 針對 Cloud Run 多副本的特別處理（DB 序列化已涵蓋多數情境，但不額外保證跨區/極端競態）。

## 元件設計

### 1. HMAC 雜湊（補破口 1）

- 在 `AuditLogService` 將金鑰語意明確化：建構參數 `hash_salt` 繼續沿用環境變數 `AUDIT_HASH_SALT`，但於 `record()` 計算 `event_hash` 時改用：

  ```
  event_hash = hmac_sha256(key=hash_salt, message=hash_material)
  ```

  `hash_material` 維持現有 JSON 序列化（`sort_keys=True, ensure_ascii=False`）內容不變，僅雜湊演算法由 `hashlib.sha256` 改為 `hmac.new(key, msg, sha256)`。
- `stable_hash()`（用於 session_id / user_id 去識別化）維持原樣，不在本次範圍。
- **啟動警告**：於服務初始化（`AuditLogService.initialize()` 或 `app/api/main.py` 啟動流程）偵測，若 `audit_enabled` 為真且 `hash_salt` 屬於不安全占位值，輸出醒目 `logger.warning`，提示正式環境必須設定 `AUDIT_HASH_SALT`。不中止啟動（demo 友善）。
  - **占位值偵測**：目前 salt 在三處有不一致的預設值——`.env.example` 與 `docker-compose.yml` 為 `change-me-in-production`，`app/config.py` 的程式 fallback 為 `dev-only-change-me`。警告判定須涵蓋全部，採用：salt 為空、或（不分大小寫）包含 `change-me` / `changeme` / `dev-only` 子字串即視為占位值。
  - **三處對齊**：將 `app/config.py` 的程式 fallback 統一為 `change-me-in-production`，與 `.env.example`、`docker-compose.yml` 一致，消除「跑起來用的值」與「警告檢查的值」不同步的根源。

### 2. DB 取 prev_hash + 序列化寫入（補破口 2、3）

- **Schema 變更**（`db/audit_schema.sql` 與程式內 `CREATE TABLE IF NOT EXISTS`）：新增 `chain_index BIGSERIAL` 欄位，**僅用於確定性插入順序**（讀取最後一筆、依序驗證）。注意 BIGSERIAL 在交易 rollback 時會自然跳號，故**不以斷號判定刪除**（避免誤判）。其餘欄位不變。
  - 既有資料相容性：demo 環境可重建表。腳本以 `ADD COLUMN IF NOT EXISTS` 方式補欄位，避免破壞既有部署。
- **移除** `_last_hash` 實例變數。
- `record()` 改為單一 transaction 內完成：
  1. `pg_advisory_xact_lock(<固定 64-bit 鍵>)` — 序列化鏈的 append，避免並發分叉。
  2. `SELECT event_hash FROM audit_events ORDER BY chain_index DESC LIMIT 1` → 取得真正的 `prev_hash`（無資料則為 `NULL`，即創世筆）。
  3. 計算 `event_hash`（HMAC）後 `INSERT`。
  4. commit。
- 連線維持現有「每次呼叫建立 asyncpg 連線」模式（連線池為非目標），但寫入包進 transaction。

### 3. verify_chain()（補破口 4）

- 新方法簽章（回傳結構化結果，不丟例外表示「鏈壞」）：

  ```python
  @dataclass(frozen=True)
  class ChainVerificationResult:
      ok: bool
      checked_count: int
      broken_chain_index: int | None   # 第一個出問題的 chain_index
      reason: str | None               # "tampered" | "broken_link" | None
  ```

- 行為：依 `chain_index` 升冪讀全部事件，對每筆：
  1. 用金鑰重算 `event_hash`，與儲存值比對 → 不符即 `tampered`。
  2. 比對本筆 `prev_hash` 是否等於前一筆的 `event_hash` → 不符即 `broken_link`。
- 回傳第一個出問題的位置即停，或全數通過回 `ok=True`。
- **刪除偵測**：刪除「中間」一筆會使下一筆的 `prev_hash` 對不上其前一筆 → 由 `broken_link` 捕捉。**已知限制**：刪除「最尾端」一筆（truncation）不會破壞剩餘鏈的連結，本版無法偵測；需靠外部錨定（屬非目標），於下方限制章節載明。

### 4. 對外執行（選項 B）

- `scripts/verify_audit_chain.py`：讀取環境設定（`AUDIT_DB_PATH`、`AUDIT_HASH_SALT`），建立 `AuditLogService`，呼叫 `verify_chain()`，將結果以人類可讀格式印出（綠燈 OK / 紅燈含斷裂 index 與原因），並以 exit code 反映（0 = OK，非 0 = 鏈損壞）。
- `Makefile` 新增 `audit-verify` target 執行該腳本，方便 demo 現場跑「篡改 → 偵測」。

## 資料流

```
record() 寫入：
  agent_run_service → AuditLogService.record()
    └─ BEGIN
       pg_advisory_xact_lock(K)
       prev_hash := SELECT last event_hash ORDER BY chain_index DESC
       event_hash := HMAC(salt, hash_material{... prev_hash ...})
       INSERT (... chain_index=BIGSERIAL, prev_hash, event_hash)
       COMMIT

verify_chain() 驗證：
  scripts/verify_audit_chain.py / make audit-verify
    └─ rows := SELECT * ORDER BY chain_index ASC
       逐筆：重算 HMAC 比對 + prev_hash 連結 + chain_index 連續性
       → ChainVerificationResult
```

## 錯誤處理

- `record()` 寫入失敗：維持現有行為（例外往上拋由呼叫端處理）；transaction 確保部分寫入會 rollback。
- advisory lock 在 transaction 結束自動釋放（`xact` 版本），不需手動解鎖。
- `verify_chain()` 對「空表」回傳 `ok=True, checked_count=0`。
- 金鑰為預設值僅警告、不影響功能（demo 友善）。

## 已知限制

- **尾端截斷（truncation）**：刪除鏈最後一筆或多筆，剩餘鏈仍自洽，本版無法偵測。需要將最新 `event_hash` 定期錨定到外部不可變儲存才能防護（屬非目標）。
- **金鑰外洩**：HMAC 金鑰仍以環境變數 `AUDIT_HASH_SALT` 提供。若金鑰連同 DB 一併外洩，攻擊者可重建鏈。生產級應改用 KMS / Secret Manager（屬非目標）。
- **多副本極端競態**：DB advisory lock 序列化已涵蓋一般並發，但不對跨區、極端時鐘/網路分割情境提供額外保證。

## 測試（使用既有 `postgres_container` fixture）

新增 `tests/security/test_audit_log_service.py` 的測試案例：
1. **鏈正確串接**：連續 record 多筆，每筆 `prev_hash` 等於前一筆 `event_hash`，`verify_chain()` 回 `ok=True`。
2. **竄改偵測**：直接 UPDATE 某筆 `input_redacted`，`verify_chain()` 回 `tampered` 且指出正確 `chain_index`。
3. **刪除偵測**：DELETE 中間一筆，`verify_chain()` 回 `broken_link` 並指出斷裂位置。（刪除最尾端筆為已知限制，不在測試斷言內。）
4. **模擬重啟接續**：record 幾筆後，新建一個 `AuditLogService` 實例（模擬重啟，`_last_hash` 已不存在）再 record，新筆的 `prev_hash` 應正確接續 DB 最後一筆而非 `NULL`，且 `verify_chain()` 仍 `ok=True`。
5. **HMAC 金鑰相依**：用錯誤金鑰建立的 service 跑 `verify_chain()` 會判定為 `tampered`（證明沒有金鑰無法驗證/偽造）。
6.（保留既有）PII 脫敏測試不受影響。

## 影響檔案清單

- `app/services/audit_log_service.py` — HMAC、移除 `_last_hash`、transaction + advisory lock + DB 取 prev_hash、`verify_chain()`、`ChainVerificationResult`、啟動警告。
- `db/audit_schema.sql` — 新增 `chain_index BIGSERIAL`。
- `app/config.py` — 將 `audit_hash_salt` 程式 fallback 由 `dev-only-change-me` 統一為 `change-me-in-production`（對齊 `.env.example` 與 `docker-compose.yml`）。
- `scripts/verify_audit_chain.py` — 新增驗證腳本。
- `Makefile` — 新增 `audit-verify` target。
- `tests/security/test_audit_log_service.py` — 新增上述測試案例。

## 驗收標準

- 篡改任一筆或刪除中間筆後，`make audit-verify` 能明確指出鏈損壞與位置。
- 模擬服務重啟後新寫入仍接續同一條鏈。
- 用錯誤金鑰無法通過驗證。
- 既有 `record()` 呼叫端無需改動，現有測試（PII 脫敏）持續通過。
