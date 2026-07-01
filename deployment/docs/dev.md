# GCP 開發環境部署計畫

**目標：** 從零開始將保險推薦 Agent 部署到 GCP 專案 `<YOUR_GCP_PROJECT_ID>`（區域：`us-central1`），並自動建立儲存在 GCS bucket 中的 Terraform 狀態。

## 第一階段：先決條件與身分驗證

1. **建立環境變數檔案 (`.env`)：**
   在專案根目錄建立 `.env` 檔案，並設定 GCP 部署相關變數。`Makefile` 會自動載入這些設定，省去每次執行指令輸入參數的麻煩。（請確保 `.env` 已加入 `.gitignore`）
   ```env
   GCP_PROJECT_ID=<YOUR_GCP_PROJECT_ID>
   GCP_REGION=us-central1
   PROJECT_NAME=insurance-agent
   ```

2. **登入並設定專案：**
   ```bash
   gcloud auth login
   gcloud config set project <YOUR_GCP_PROJECT_ID>
   # 確保在本地使用程式進行 GCP 服務操作時有適當的認證 (重要：Terraform 需要此認證來建立資源與 Bucket)
   gcloud auth application-default login
   ```

3. **啟用必要的 API（如果尚未啟用）：**
   ```bash
   gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com
   ```

## 第二階段：基礎設施與應用程式部署（使用 Makefile）

得益於 `Makefile` 的自動化流程，Terraform 所需的 Backend 儲存桶會自動檢查與建立。我們只需依序執行以下指令：

1. **生成 Terraform 配置檔並確保 State Bucket 存在：**
   此指令會根據 `.env` 的設定，自動檢查並在 GCP 建立 Terraform State 專用的 GCS Bucket (例如 `gs://<YOUR_GCP_PROJECT_ID>-terraform-state`)，隨後動態產生 `dev.tfbackend` 配置檔。
   ```bash
   make tf-gen-config
   ```

2. **建置並推送映像檔到 Artifact Registry：**
   此步驟會建立 Artifact Registry 儲存庫（`insurance-agent-repo`，如果不存在）、建置後端、前端和 toolbox 映像檔，並將其推送到雲端。
   ```bash
   make build-push
   ```

3. **初始化並套用 Terraform：**
   這會使用剛剛生成的 `dev.tfbackend` 初始化 Terraform，並套用所有的雲端資源部署計畫。
   ```bash
   make tf-init
   make tf-plan
   make tf-apply
   ```

   **💡 專家提示：**
   *   您可以直接執行 `make tf-apply`，它會自動先跑 `tf-init` 再 apply（無需分開執行）。
   *   您可以使用 `make gcp-deploy` 一鍵執行 `build-push` 與 `tf-apply`。

4. **部署架構摘要：**
   *   **Compute**: 使用 Cloud Run v2。後端服務採 **Sidecar 雙容器架構**（主程式容器 + Toolbox 容器），前端為獨立的 Next.js 服務。
   *   **Data**: Cloud SQL (PostgreSQL 15)。
   *   **Telemetry**: 整合 Cloud Logging Bucket 與 BigQuery Linked Dataset。

## 第三階段：資料庫設定（部署後操作）

一旦 Terraform 完成部署且 Cloud SQL 實例建置完成，我們可以使用自動化指令完成所有的資料庫初始化工作（包含建立 Schema、寫入測試資料與進行 FAQ 的知識庫向量化）。此指令會自動在背景啟動 Proxy、獲取秘密憑證並執行所有必要的 SQL 與 Python 腳本。

1. **執行自動化資料庫初始化：**
   ```bash
   make gcp-db-setup
   ```

2. **自動化流程說明：**
   此指令會依序執行以下動作：
   *   透過 Terraform Output 獲取資料庫實例連線名稱、使用者與資料庫名稱。
   *   從 Secret Manager 安全地獲取資料庫密碼。
   *   在背景啟動 `cloud-sql-proxy` 並等待埠位 (5432) 就緒。
   *   依序執行 `db/schema.sql`、`db/audit_schema.sql` 與 `db/seed.sql` 建立資料表與初始管理員。
   *   執行 Python 種子資料腳本（建立一般使用者）。
   *   執行 FAQ 向量匯入腳本（利用 Vertex AI Embeddings API）。
   *   完成後自動清理並關閉背景的 Proxy 程序。

3. **取得資料庫連線資訊（選用）：**
   如果您想要手動連線到資料庫，或是需要在其他工具中使用連線資訊，可以使用以下指令獲取：
   ```bash
   make tf-db-password
   make gcp-db-proxy
   ```

## 回滾 / 清除（選用）

如果您需要拆除雲端環境，只需執行：
```bash
make tf-destroy
```
*(注意：`Makefile` 已經針對 PostgreSQL 使用者刪除限制進行了自動化處理，會先從狀態中移除使用者再進行銷毀。)*

---

## 故障排除提示

### Q: 如果 Terraform 報告狀態損壞 (Tainted) 或 Logging Bucket 資源不一致，該如何處理？

**A:** 錯誤原因與解決方案如下：

**【問題原因】**
有時候在執行 Terraform 部署或銷毀中途發生中斷，或是手動在 Google Cloud Console 中刪除了被 Terraform 管理的資源（例如 Logging Bucket），會導致 Terraform 的本地狀態 (State) 與實際雲端狀態不一致。Terraform 會將該資源標記為損壞 (tainted)，並在下次執行時報錯或強制要求重建。

**【解決方案】**
視實際狀況，你可以選擇手動恢復該資源（如果它被軟刪除），或者透過 Terraform 指令解除其損壞標記 (untaint)，讓 Terraform 重新同步狀態。

**【操作步驟】**

1. **檢查 Logging Bucket 狀態**
   首先，列出專案中的 Logging Buckets，確認該 Bucket 的生命週期狀態 (Lifecycle State)：
   ```bash
   gcloud logging buckets list --project=<YOUR_GCP_PROJECT_ID> --format="table(LOCATION, BUCKET_ID, LIFECYCLE_STATE)"
   ```

2. **復原被刪除的 Logging Bucket (如果適用)**
   如果發現 Bucket 被意外刪除且處於可復原狀態，可嘗試手動將其反刪除 (undelete)：
   ```bash
   gcloud logging buckets undelete insurance-agent-genai-telemetry --project=<YOUR_GCP_PROJECT_ID> --location=us-central1
   ```

3. **解除 Terraform 狀態的 Tainted 標記**
   如果問題單純是 Terraform 狀態記錄異常，且資源實際上是正常的，可以手動將其從「損壞」狀態中解除。切換到 Terraform 目錄並執行 `untaint` 指令：
   ```bash
   cd deployment/terraform/dev
   terraform untaint module.agent_infrastructure.google_logging_project_bucket_config.genai_telemetry_bucket
   ```

### Q: 執行 `make tf-destroy` 時遇到 PostgreSQL 錯誤「role "user" cannot be dropped because some objects depend on it」，該如何解決？

**A:** 錯誤原因與解決方案如下：

**【問題原因】**
這個錯誤發生在 Terraform 嘗試刪除 Google Cloud SQL 的資料庫使用者 (`google_sql_user`) 時。
在 PostgreSQL 的安全機制中，如果一個資料庫使用者 (Role) 建立了物件（例如資料表、Schema、View），或是被賦予了某些權限，那麼該使用者就無法被直接刪除。必須先手動移除或轉移那些依賴的物件與權限後，才能刪除該使用者。這導致 Terraform 的 destroy 流程被中斷。

**【解決方案】**
**本專案的 `make tf-destroy` 指令已包含自動修復邏輯。** 它會先執行 `terraform state rm` 將該使用者從狀態中移除，讓 Terraform 略過「刪除使用者」的步驟，直接刪除整個 Cloud SQL 執行個體。

如果您是**手動**執行 `terraform destroy` 而遇到此問題，請參考以下步驟：

**【操作步驟】**

1. **進入 Terraform Dev 目錄**
   ```bash
   cd deployment/terraform/dev
   ```

2. **將資料庫用戶從 Terraform State 中移除**
   使用 `terraform state rm` 指令，告訴 Terraform 不要再管這個資源了。注意資源路徑包含模組名稱。
   ```bash
   terraform state rm module.agent_infrastructure.google_sql_user.db_user
   ```
   *(執行成功會顯示 Removed module.agent_infrastructure.google_sql_user.db_user)*

3. **重新執行銷毀指令**
   再次執行銷毀指令。此時 Terraform 會跳過刪除用戶的步驟，直接刪除包含該用戶在內的整個資料庫執行個體。
   ```bash
   terraform destroy -auto-approve ...
   ```

### Q: 執行 `make tf-apply` 時遇到 `Error 409: Already Exists`（例如 Service Account 或 BigQuery 資料集已存在），該如何處理？

**A:** 錯誤原因與解決方案如下：

**【問題原因】**
這通常發生在重新佈署或狀態檔案 (`terraform.tfstate`) 遺失/未同步時。Terraform 嘗試建立的資源在 Google Cloud 專案中已經存在，但目前的狀態檔案中沒有這些資源的記錄，導致建立動作衝突。

**【解決方案】**
使用 `terraform import` 指令將現有的雲端資源匯入到 Terraform 的狀態管理中。

**【操作步驟】**

1. **進入 Terraform Dev 目錄**
   ```bash
   cd deployment/terraform/dev
   ```

2. **匯入現有資源 (以常用資源為例)**
   請根據報錯訊息中的資源 ID 進行匯入：
   *   **匯入服務帳號：**
       `terraform import google_service_account.app_sa projects/<PROJECT_ID>/serviceAccounts/insurance-agent-app@<PROJECT_ID>.iam.gserviceaccount.com`
   *   **匯入 BigQuery 資料集：**
       `terraform import google_bigquery_dataset.telemetry_dataset projects/<PROJECT_ID>/datasets/insurance_agent_telemetry`
   *   **匯入 BigQuery 連線：**
       `terraform import google_bigquery_connection.genai_telemetry_connection projects/<PROJECT_ID>/locations/us-central1/connections/insurance-agent-genai-telemetry`

3. **重新執行套用指令**
   匯入成功後，再次執行 `make tf-apply` 即可恢復正常管理。

### Q: 執行 `make gcp-db-setup` 時遇到 `Error 125` 且提示 `cloud-sql-proxy` 啟動失敗，該如何處理？

**A:** 錯誤原因與解決方案如下：

**【問題原因】**
這通常是 Docker 啟動錯誤。在 `make gcp-db-setup` 流程中，腳本會嘗試將主機的 Google Cloud 憑證 (ADC) 掛載到 `cloud-sql-proxy` 容器中。如果 Makefile 找不到憑證檔案，或者 `/tmp/adc_db_setup.json` 建立失敗，Docker 會因為掛載路徑無效而回傳 Error 125。

**【解決方案】**
手動確保憑證檔案存在於預設路徑，或手動準備掛載所需的暫存檔案。

**【操作步驟】**

1. **確認本地憑證是否存在**
   檢查預設路徑 `~/.config/gcloud/application_default_credentials.json` 是否有檔案。若無，請先執行 `gcloud auth application-default login`。

2. **手動建立掛載檔案**
   如果 Makefile 自動執行失敗，可手動執行：
   ```bash
   cp ~/.config/gcloud/application_default_credentials.json /tmp/adc_db_setup.json
   chmod 644 /tmp/adc_db_setup.json
   ```

3. **重新執行初始化指令**
   ```bash
   make gcp-db-setup
   ```

### Q: 執行 `make gcp-db-setup` 指令更新的是哪一個資料庫？具體執行細節為何？

**A:** 該指令更新的是 **Google Cloud (GCP) 上透過 Terraform 建立的雲端 Cloud SQL 執行個體**，而非您本地運行的資料庫。

**【連線資訊摘要】**
*   **GCP 專案**：根據 `.env` 中的 `GCP_PROJECT_ID` 設定（例如：`adk-agent-xxx`）。
*   **連線方式**：透過 `cloud-sql-proxy` 建立加密通道。
*   **目標資料庫**：名稱通常為 `insurance`，使用者為 `user`。

**【具體執行細節】**
初始化流程分為四個階段，確保應用程式具備完整的運行環境：
1.  **啟動連線代理 (Proxy)**：在本地啟動 `cloud-sql-proxy` 容器，將雲端資料庫的 5432 埠映射到本地，這讓後續指令能像存取本地資料庫一樣操作雲端執行個體。
2.  **執行 SQL 結構初始化**：使用 `postgres:16-alpine` 鏡像啟動臨時容器執行 `psql`，依序灌入：
    *   `db/schema.sql`：建立核心業務表（產品、規則、用戶）。
    *   `db/audit_schema.sql`：建立符合合規要求的稽核紀錄表。
    *   `db/seed.sql`：匯入預設的保險產品與基礎規則。
3.  **執行 Python 業務種子資料**：運行 `scripts/seed_user.py`，建立應用程式登入所需的測試帳號與設定。
4.  **FAQ 知識庫向量化 (Vector Ingestion)**：運行 `scripts/ingest_faq_embeddings.py`。此步驟會讀取 FAQ 內容，並呼叫 **Vertex AI Embedding API** 產生向量資料，存入資料庫的向量表中，供 Agent 進行 RAG 檢索。

## GenAI Telemetry 架構解析

為了能完整監控並分析 GenAI Agent 的運作狀況，本專案實作了一套結合 Cloud Logging 與 BigQuery 的進階遙測 (Telemetry) 方案。以下說明 Python 應用程式、SQL 查詢以及 Terraform 基礎設施如何協同運作：

### 1. Python 應用程式配置 (`app/app_utils/telemetry.py`)
應用程式透過 OpenTelemetry 與官方 GenAI Instrumentor 進行設定：
* **元資料與 Payload 分離儲存**：當啟用 `LOGS_BUCKET_NAME` 且 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 設為非 false 時，會進入分離儲存模式。
* **Cloud Logging 紀錄 Metadata**：應用程式將 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 設為 `NO_CONTENT`，這意味著 Cloud Logging 僅會記錄 API 呼叫的元資料 (Metadata)，如延遲、Token 數量、Model 名稱等，不會記錄龐大的 Prompt/Response 內容。
* **GCS 儲存完整 Payload**：設定 `OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK="upload"`，使得完整的 Prompt 與 Response (包含對話歷史) 會以 JSONL 格式被上傳到指定的 GCS Bucket (`LOGS_BUCKET_NAME`) 的 `completions/` 路徑下。
* **Metadata 中的參照**：寫入 Cloud Logging 的 Metadata 中，會包含指向 GCS JSONL 檔案的 URI (例如 `gen_ai.input.messages_ref` 與 `gen_ai.output.messages_ref`)。

### 2. BigQuery 視圖與資料整併 (`deployment/terraform/sql/completions.sql`)
由於資料被拆分在兩處 (Logging 存 Metadata，GCS 存 Payload)，我們利用 BigQuery 來進行資料整併，這正是 `completions.sql` 的核心任務：
* **提取 URI 參考 (CTE: `log_refs`, `unpivoted_refs`)**：從 Cloud Logging 的資料表中掃描並提取出指向 GCS 檔案的 `messages_ref_uri`，並將輸入與輸出拆解成獨立列。
* **Join 外部資料表 (CTE: `joined_data`)**：將日誌資料與 BigQuery 外部資料表 (指向 GCS 中的 JSONL 檔案) 進行 Join。利用 `messages_ref_uri` 匹配 `_FILE_NAME`，成功將日誌與龐大的 Payload 結合。
* **資料展開與去重 (CTE: `flattened`, `deduplicated`)**：將巢狀的 `parts` 陣列展開，並根據 Trace ID 與時間戳記去重，確保工具呼叫產生的重複記錄不會影響分析，最終輸出乾淨的結構化資料。

### 3. Terraform 基礎設施配置 (`deployment/terraform/dev/telemetry.tf`)
為了支撐上述架構，Terraform 會自動配置以下資源：
* **GCS Bucket (`logs_data_bucket`)**：用來接收 Python 應用程式上傳的 JSONL Payload 檔案。
* **BigQuery Dataset (`telemetry_dataset`)**：存放遙測資料的主資料集。
* **BigQuery External Table (`completions_external_table`)**：設定為讀取上述 GCS Bucket (`completions/` 路徑) 中的 JSONL 檔案。此表不儲存實體資料，而是即時查詢 GCS 檔案。
* **Cloud Logging Linked Dataset (`genai_logs_linked_dataset`)**：將 Cloud Logging 的資料直接連結至 BigQuery 資料集，允許在 BigQuery 中直接查詢日誌，省去了匯出的成本與延遲。
* **BigQuery View (`completions_view`)**：利用 `templatefile` 讀取並建立 `completions.sql` 視圖，為使用者提供一個無縫整合 Logging Metadata 與 GCS Payload 的單一查詢入口點。
