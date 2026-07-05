# AI Harness 企業級重構計畫與優化設計方案 (Revised Blueprint)

本方案為 AI Harness 專案提供全面的企業級升級藍圖，旨在深化 **Google Agent Development Kit (ADK) 框架**、**GCP (Google Cloud Platform) 生態系** 以及 **MCP (Model Context Protocol)** 整合。

---

## 1. 架構核心架構與技術棧 (Core Technology Stack)

本專案完全基於以下現代 AI 工程技術棧進行建構：
*   **Agent 框架：** Google Agent Development Kit (ADK) 2.0+ (Python SDK)
*   **LLM 模型：** Vertex AI Gemini 2.5 Flash / Gemini 3.1 Pro (經由 `google-genai` SDK 元件)
*   **語意安全：** ADK 原生 `BasePlugin` 插件攔截系統 + 雙向 LLM 語意過濾
*   **多代理人編排 (A2A)：** ADK Native Agent Composition & Agent-as-a-Tool
*   **向量與記憶：** Cloud SQL for PostgreSQL (啟用 `pgvector` 擴充套件) + Vertex AI Text Embeddings
*   **即時語音：** Gemini Multimodal Live API (WebSocket) 雙向低延遲串流
*   **雲端基礎設施：** Google Cloud Run (Backend 與 MCP Toolbox Sidecar), Google Cloud SQL, Cloud Trace, BigQuery, IAM (WIF 安全驗證)，以 **Terraform** 進行宣告式 IaC 部署

---

## 2. 階段一：語意安全護欄 (Semantic Guardrails) — 【已完成與修正】

### 2.1. 實作修正與設計對齊 (ADK Native Plugin)
原本計畫中，語意安全護欄是以手動 AOP 切面直接寫在 FastAPI 的 `agent_run_service.py` 串流產生器中。
**【修正方案】**：為了追求極致的框架一致性與全域複用性，我們將其重構為 **ADK 官方原生的 `BasePlugin` 插件模式**：

```python
# app/security/semantic_guardrail.py
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.agents import InvocationContext
from google.adk.events.event import Event

class SemanticGuardrailPlugin(BasePlugin):
    """ADK 框架原生插件，封裝 SemanticGuardrail 進行全生命週期審查"""
    def __init__(self, config: AppRuntimeConfig):
        super().__init__(name="semantic_guardrail")
        self._guardrail = SemanticGuardrail(config)

    async def on_user_message_callback(self, *, invocation_context: InvocationContext, user_message: types.Content) -> Optional[types.Content]:
        # 1. 攔截使用者輸入、進行 Regex Fast-pass 與 LLM 注入偵測
        ...
        purified_prompt = await self._guardrail.check_input(raw_prompt)
        return types.Content(role=user_message.role, parts=[types.Part(text=purified_prompt)])

    async def on_event_callback(self, *, invocation_context: InvocationContext, event: Event) -> Optional[Event]:
        # 2. 攔截模型最終輸出事件、進行 LLM 合規過濾與淨化，再轉發給客戶端
        ...
        return purified_event
```

### 2.2. 解耦與註冊
在 `app/container.py` 中，將 `SemanticGuardrailPlugin` 註冊於 `App` 物件，並交由 `Runner` 承載。此設計帶來了以下關鍵優勢：
*   **棄用警告消除：** 通過傳遞 `App` 實例而非直接傳遞 `plugins` 給 `Runner`，完全消除了 ADK 2.0+ 的 `DeprecationWarning`。
*   **完全解耦：** `agent_run_service.py` 與 `LiveAgentService` 不再需要手動載入 `_guardrail` 去敏，由 ADK 執行引擎自動在底層進行事件流淨化。

---

## 3. 階段二：ADK 原生 A2A (Agent-to-Agent) 多代理人重構 — 【接下來執行方針】

### 3.1. 單體大腦拆分 (Monolithic to Specialized Agents)
將原本大而全的 `root_agent` 拆分為 **「Supervisor（監督路由器）+ 專業子代理人（Recommendation/FAQ/Claim）」** 的樹狀編排。

```
           ┌──────────────────────┐
           │   Supervisor Agent   │ (意圖路由與分流)
           └──────────┬───────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
   ┌───────────┐┌───────────┐┌───────────┐
   │Rec Agent  ││ FAQ Agent ││Claim Agent│ (專業子代理人)
   └───────────┘└───────────┘└───────────┘
```

*   **`Supervisor Agent`**：
    *   **角色定位：** 作為總路由。它不直接調用產品資料庫或理賠 API，而是專注於分析用戶意圖、提取關鍵槽位 (Slot-filling)。
    *   **Orchestration 模式：** 在 ADK 中將子代理人（`RecommendationAgent`、`FAQAgent`、`ClaimAgent`）封裝為 **「Agent-as-a-Tool (A2A 工具化)」** 註冊給 `Supervisor`。
*   **專業子代理人**：
    *   **`RecommendationAgent`**：配備 MCP Toolbox 中的產品推薦 API，僅持有保險商品選配的 System Instruction。
    *   **`FAQAgent`**：配備 RAG 檢索工具，專注於保險條款與 FAQ 問答。
    *   **`ClaimAgent`**：配備理賠導航工具，引導用戶上傳文件並調用理賠試算。

### 3.2. 進程內呼叫 (In-Process Call) 零網路延遲
為了解決多 Agent 呼叫帶來的網路延遲（HTTP Overhead），所有 Sub-agents 均在**同一個 Python 進程、同一個 Cloud Run 容器內初始化**。
*   透過 ADK 的 A2A 機制，子代理人調用僅是同一個 `asyncio` 事件循環中的物件調用，**內部 A2A 延遲 < 1ms**。

---

## 4. 階段三：pgvector 語意記憶 (Semantic Memory) 與 GCP 整合 — 【接下來執行方針】

為了給予 Agent 跨對話（Cross-session）的長期記憶與客製化推薦能力，我們將在 Cloud SQL for PostgreSQL 上實作 `pgvector` 語意記憶庫。

### 4.1. 數據 Schema 設計 (`db/schema.sql`)
```sql
-- 啟用 pgvector 擴充套件
CREATE EXTENSION IF NOT EXISTS vector;

-- 建立長期語意記憶表
CREATE TABLE IF NOT EXISTS semantic_memories (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    session_id VARCHAR(100) NOT NULL,
    memory_type VARCHAR(50) NOT NULL, -- e.g., 'preference', 'family_status', 'claim_history'
    content TEXT NOT NULL,             -- 記憶的原始文字描述
    embedding vector(768) NOT NULL,    -- Vertex AI text-embedding-005 的 768 維向量
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ON semantic_memories USING hnsw (embedding vector_cosine_ops);
```

### 4.2. 語意檢索工具 (`app/tools/session_tools.py`)
利用 GCP Vertex AI 的 Text Embeddings 接口將使用者問題向量化，並在 Cloud SQL 中執行餘弦相似度查詢：
```python
# 透過 Vertex AI 生成 Embedding，並執行 Cosine Similarity 召回歷史記憶
query = """
    SELECT content, memory_type
    FROM semantic_memories
    WHERE user_id = :user_id
    ORDER BY embedding <=> :query_embedding
    LIMIT 3;
"""
```
*   這將作為一個 `retrieve_past_context` 工具註冊給 `Supervisor Agent`，使其在對話起跑時能無縫掌握用戶的歷史畫像。

---

## 5. 階段四：Multimodal Live Streaming 整合與 GCP 部署 — 【接下來執行方針】

### 5.1. 「工具化 A2A」於 Live 語音串流
即時語音 WebSocket (`LiveAgentService`) 將作為最外層的多模態連線大腦，其直接與 Google Multimodal Live API 連接。
*   當用戶用語音說「幫我看看安康防癌險」時，`LiveAgent` 捕獲此語意，並將其轉為非同步 Tool Call。
*   在進程內（In-process）調度 `RecommendationAgent` 執行產品檢索。
*   在等待 A2A 子代理人執行的數秒內，`LiveAgent` 利用 ADK 的 Proactivity（主動發話回呼）向用戶播放舒適的語音墊字（例如：「我正在為您試算方案，請稍等一下喔...」），保證語音體驗完全不斷白。

### 5.2. GCP 資源配置與 Terraform 調整 (`service.tf`)
多 Agent 進程併發、語意護欄、以及語音/影像多模態串流會帶來顯著的 CPU 與 Memory 負載。我們將修改 `deployment/terraform/` 的定義：
*   **規格提升：** 將 Cloud Run Backend 容器資源上限升級至 **`cpu = "4"`, `memory = "2048Mi"`**。
*   **透傳 TraceContext：** 在 A2A 工具調用與非同步任務中，嚴格透傳 OpenTelemetry 的 `traceparent`，確保 **GCP Cloud Trace** 與 **BigQuery Agent Analytics** 能無縫串聯 Supervisor 與所有子代理人的完整調度鏈路。

---

## 6. 四階段落地時程表 (Roadmap)

```
📅 任務時程總覽：
【第一週】━━━━━━━━━━━━━━━━━▶ [階段一：語意安全護欄] (已完成並重構為原生 ADK Plugin 模式 ✅)
【第二週】━━━━━━━━━━━━━━━━━▶ [階段二：ADK-Native A2A 多代理人編排] (進行中，拆分大腦與工具化子代理人 🚀)
【第三週】━━━━━━━━━━━━━━━━━▶ [階段三：Cloud SQL pgvector + Vertex AI Long-term Memory] (進行中，長期記憶召回 🧠)
【第四週】━━━━━━━━━━━━━━━━━▶ [階段四：WebSocket Live 串流 A2A 整合與 GCP 雲端自動化部署] (整合發佈 🌐)
```

本 Revised 計畫以 **Google 原生 ADK 2.0+ 與 GCP** 為技術主體，完全消除了微服務架構網路延遲，是企業級高安全性、低延遲保險代理人系統的最佳工程實踐。