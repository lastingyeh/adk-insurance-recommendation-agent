# AI Harness 企業級重構計畫 - 階段一：語意安全護欄 (Semantic Guardrails) 原生 ADK 插件設計與實作

本文件記錄了階段一（Semantic Guardrails）的最終實作架構。我們棄用了原本規劃在 FastAPI 服務層手動 AOP 切面的設計，改為採用 **Google ADK 原生的 `BasePlugin` 插件模式**，以獲得極致的框架相容性、優越的效能以及對未來多 Agent 協作的完美擴充性。

---

## 1. 核心設計架構：ADK 原生 `BasePlugin`
將安全、隱私與注入防禦解耦為一個獨立的 ADK 全域插件。它註冊於 `App` 實例上，並在 Runner 執行時自動於底層的 lifecycle 攔截點觸發：

```
       [ 使用者輸入 Prompt ]
                 │
                 ▼
 ┌──────────────────────────────┐
 │  on_user_message_callback    │ ──► 1. [SG-3] PII Regex Fast-Pass
 └──────────────────────────────┘     2. Gemini 語意注入與個資過濾 (2.0s Timeout)
                 │                    3. 偵測注入 ──► [拋出 PromptInjectionException]
                 ▼ (安全去敏 Prompt)
 ┌──────────────────────────────┐
 │     ADK Runner 核心執行      │ ──► 串流生成 Chunks (不作 LLM 過濾，保證 TTFT)
 └──────────────────────────────┘
                 │
                 ▼ (累計最終回覆文字)
 ┌──────────────────────────────┐
 │       on_event_callback      │ ──► 1. 輸出合規 LLM 檢查
 └──────────────────────────────┘     2. 敏感內部資料/過度承諾修復
                 │
                 ▼ (安全淨化後的 Event)
         [ 傳送至客戶端 ]
```

---

## 2. 實作元件說明

### 2.1. `app/security/semantic_guardrail.py`
定義 `SemanticGuardrail` 核心推理類別與 `SemanticGuardrailPlugin(BasePlugin)`：
*   **`on_user_message_callback`**：攔截 inbound 訊息，解析文字 parts，呼叫 `SemanticGuardrail.check_input`。
*   **`on_event_callback`**：僅對 `event.author == "model" and not event.partial` 的最終回答進行攔截與 `check_output` 淨化，重新組裝成安全的新 Event。
*   **超時與降級 (Resiliency)**：使用 `asyncio.wait_for` 設定 2 秒嚴格超時。在超時、429 或網路異常時，自動安全降級使用 Regex 遮蔽（Fast-Pass）並 bypass，保證核心對話高可用。

### 2.2. `app/container.py` 元件組裝
在 `create_runner` 中動態建構 `App` 並掛載 `SemanticGuardrailPlugin`：
```python
def create_runner(
    config: AppRuntimeConfig,
    agent: Agent,
    session_store: BaseSessionService,
) -> Runner:
    plugins: list[BasePlugin] = []
    if config.enable_semantic_guardrails:
        plugins.append(SemanticGuardrailPlugin(config))
    app_instance = App(root_agent=agent, name=config.app_name, plugins=plugins)
    return Runner(
        app=app_instance,
        session_service=session_store,
    )
```
*   **棄用消除：** 藉由傳遞 `App` 實例而非直接傳遞 `plugins` 給 `Runner`，完全消除了 ADK 2.0+ 的 `DeprecationWarning`，並與 ADK 官方的最佳實踐完全對齊。

### 2.3. `app/services/agent_run_service.py` 異常處置
服務層不再手動呼叫護欄，而是由 ADK 自動運行。當插件檢測到注入攻擊並拋出 `PromptInjectionException` 時，異步迭代器會向外傳遞，由最外層的 `try-except` 捕獲：
*   捕獲異常後，立即回傳結構化的 `SECURITY_VIOLATION` SSE 錯誤封包並安全中斷。
*   自動呼叫 `AuditLogService` 記錄 `agent.security_violation` 稽核日誌。

---

## 3. 測試與驗證成果

我們在 `tests/security/test_semantic_guardrail.py` 撰寫了 7 個涵蓋以下情境的非同步測試：
1.  **正常輸入**：確保安全輸入能順利通過。
2.  **惡意注入**：確保惡意 Prompt 注入時，正確拋出 `PromptInjectionException`。
3.  **語意 PII**：驗證 Regex 無法偵測的自然語言 PII（如「姐姐Mary生病」）能被語意遮蔽為標籤。
4.  **超時降級**：模擬 LLM 響應延遲超過 2 秒，護欄自動降級為 Regex 遮蔽。
5.  **JSON 異常降級**：模擬 LLM 回傳損壞 JSON，護欄自動降級。
6.  **輸出合規（安全）**：驗證正常輸出正常 bypass。
7.  **輸出合規（不安全）**：驗證輸出帶有敏感詞或 API 名稱時，會被自動淨化重寫。

測試結果為 **100% 通過 (All Checks Passed)**，證明此架構具備極高的可靠性。

---

## 4. 下一步方針：階段二、三、四
本專案安全地完成了語意護欄插件的實作。接下來我們將依照全新修正的 `harness-enterprise-overhaul.md` 藍圖，推進多代理人 A2A（Agent-to-Agent）、pgvector 長期語意記憶、以及 Live WebSocket 串流的整合。