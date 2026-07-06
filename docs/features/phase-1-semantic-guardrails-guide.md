# Phase 1: Semantic Guardrails (語意安全護欄) — 程式碼建置導讀與技術摘要

本導讀旨在為開發者與系統維運人員提供 **階段一：語意安全護欄** 的完整程式碼導讀、核心設計架構與關鍵技術摘要。本專案完全基於 **Google Agent Development Kit (ADK) 2.0+ 官方原生插件架構** 進行建構，擺脫了傳統手動切面攔截的侵入式設計，實現了業務邏輯與安全策略的 100% 物理隔離。

---

## 1. 核心設計亮點 (Key Highlights)

*   **100% 原生 ADK 插件化 (`BasePlugin`)**：
    安全防禦作為全域插件（`SemanticGuardrailPlugin`）掛載於 `App` 物件，並交由 `Runner` 統一調度。安全邏輯與業務 Agent 解耦，未來新增 Sub-agents 將自動享有相同的安全防護，實現完美的「開閉原則」。
*   **Gemini 2.5 Flash 低延遲雙向審查**：
    輸入端防範 Prompt Injection/Jailbreak 與自然語言非結構化 PII；輸出端過濾敏感系統變數/API 名稱並修復幻覺與過度承諾。
*   **多層次快速通道與防崩潰降級 (Resiliency)**：
    *   **Regex Fast-Pass**：優先執行 `pii.py` 進行正規表達式處理。若直接命中標準 PII，縮短或 bypass LLM 呼估。
    *   **2秒超時熔斷**：使用 `asyncio.wait_for` 設定 2 秒嚴格超時，在超時、API 限制 (429) 或故障時，自動**無感降級**回 Regex 去敏文字，保障系統 100% 可用性。
*   **零棄用警告 (Deprecation-Free)**：
    通過最新的 `Runner(app=app_instance)` 協定，完全消除了 ADK 的插件過期警告，對齊最新 2.0+ 標準。

---

## 2. 實作模組與程式碼導讀 (Codebase Walkthrough)

### 2.1. 系統配置擴充 — `app/config.py`
在 App 運行配置中加入了 `enable_semantic_guardrails` 配置項：
```python
@dataclass(frozen=True)
class AppRuntimeConfig:
    # ...
    # 放置於 dataclass 底部以提供 True 預設值，保證既有 test mock 100% 向下相容
    enable_semantic_guardrails: bool = True  # 是否啟用語意安全護欄
```
工廠函式 `load_runtime_config()` 讀取環境變數 `ENABLE_SEMANTIC_GUARDRAILS`：
```python
        enable_semantic_guardrails=_parse_bool_env("ENABLE_SEMANTIC_GUARDRAILS", True),
```

### 2.2. 核心護欄插件 — `app/security/semantic_guardrail.py`
此模組定義了安全大腦 `SemanticGuardrail` 類別以及 ADK 插件包裝器 `SemanticGuardrailPlugin(BasePlugin)`。

#### A. 雙向防禦 Prompt 核心定義
*   **`INPUT_GUARDRAIL_INSTRUCTION` (輸入防禦)**：
    定義了兩個準則：(1) 偵測提示詞注入 (Prompt Injection) (2) 偵測並以 `<NAME>`, `<ADDRESS>`, `<MEDICAL_HISTORY>` 標籤遮蔽非結構化 PII。約束 LLM 必須且只能回傳合法的單行 JSON。
*   **`OUTPUT_GUARDRAIL_INSTRUCTION` (輸出防禦)**：
    審查 Agent 產出的最終文字，確保無敏感內部 API、未遮蔽 PII、不雅言詞，並修正過度承諾（如「保證理賠一億」）。

#### B. 雙向非同步審查機制
*   **`check_input(self, prompt: str) -> str`**：
    1.  優先調用 `regex_redact_text(prompt)` 進行 fast-pass。
    2.  呼叫 Gemini 進行語意審查。若 `is_safe` 為 false 或 `is_injection` 為 true，**拋出 `PromptInjectionException`**。
    3.  若 LLM 故障或超時（2.0 秒），`except Exception` 會捕獲並**無感降級**回 Regex 去敏文字。
*   **`check_output(self, text: str) -> str`**：
    呼叫 Gemini 對輸出進行合規審查。若違反輸出政策，自動將文字修正為 `purified_text` 並返回。

#### C. ADK Plugin 介面實現
```python
class SemanticGuardrailPlugin(BasePlugin):
    def __init__(self, config: AppRuntimeConfig):
        super().__init__(name="semantic_guardrail")
        self._guardrail = SemanticGuardrail(config)

    async def on_user_message_callback(self, *, invocation_context: InvocationContext, user_message: types.Content) -> Optional[types.Content]:
        # 攔截最前端 User Message
        text_parts = [part.text for part in user_message.parts if part.text]
        if not text_parts:
            return user_message
        raw_prompt = "\n".join(text_parts)
        purified_prompt = await self._guardrail.check_input(raw_prompt)
        # 用安全去敏後的 prompt 覆蓋
        return types.Content(role=user_message.role, parts=[types.Part(text=purified_prompt)])

    async def on_event_callback(self, *, invocation_context: InvocationContext, event: Event) -> Optional[Event]:
        # 攔截輸出，僅對 model 非 partial 的最終輸出進行 check_output 淨化
        if event.author == "model" and not event.partial and event.content and event.content.parts:
            ...
            purified_text = await self._guardrail.check_output(full_text)
            # 替換 Event 的 Content
            return new_event
        return event
```

### 2.3. 元件組裝與初始化 — `app/container.py`
在系統啟動時，將插件動態加載到 `App` 與 `Runner` 中，實現 100% 框架級綁定：
```python
def create_runner(
    config: AppRuntimeConfig,
    agent: Agent,
    session_store: BaseSessionService,
) -> Runner:
    from google.adk.plugins.base_plugin import BasePlugin
    plugins: list[BasePlugin] = []
    if config.enable_semantic_guardrails:
        plugins.append(SemanticGuardrailPlugin(config))
    # 將 agent 與 plugins 組裝成 App 物件，並交由 Runner 調度（符合 ADK 2.0+ 標準）
    app_instance = App(root_agent=agent, name=config.app_name, plugins=plugins)
    return Runner(
        app=app_instance,
        session_service=session_store,
    )
```

### 2.4. 服務層異常阻斷 — `app/services/agent_run_service.py`
服務層完全不需要手動載入 `_guardrail` 檢查，代碼極其乾淨。
當插件在底層發現 Prompt Injection 並拋出 `PromptInjectionException` 時，異常會自動向上傳播。服務層只需在最外層進行特定捕捉：
```python
        except PromptInjectionException as p_exc:
            logger.error(f"Prompt Injection detected! Session: {session_id}. Error: {p_exc}")
            # 建立安全違規 SSE Envelopes 吐給前端
            error_envelope = build_error_envelope(
                "[SECURITY_VIOLATION] 偵測到異常輸入指令，對話已安全中止。",
                error_code="SECURITY_VIOLATION",
            )
            # 寫入安全審計日誌
            if self._audit_logs and audit_context:
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="agent.security_violation",
                    actor="system",
                    sequence=sequence + 1,
                    output_payload={"reason": str(p_exc)},
                )
            yield error_envelope
```

---

## 3. 測試與驗證指南 (Verification Guide)

我們在 `tests/security/test_semantic_guardrail.py` 部署了完整的單元測試，模擬各類複雜的攻擊與網路情境：

### 3.1. 執行單元測試
在專案根目錄下執行以下指令：
```bash
uv run pytest tests/security/test_semantic_guardrail.py
```
**預期結果：**
```text
tests/security/test_semantic_guardrail.py .......                                                                                                 [100%]
============================= 7 passed in 2.02s =============================
```

### 3.2. 核心測試情境解析

1.  **注入防禦測試 (`test_semantic_guardrail_check_input_injection`)**：
    Mock Gemini 回傳 `{"is_safe": false, "is_injection": true}`。驗證當使用者輸入越獄字句時，系統能 100% 拋出 `PromptInjectionException`。
2.  **語意 PII 遮蔽測試 (`test_semantic_guardrail_check_input_semantic_pii`)**：
    驗證非標準 PII（「我姐姐Mary生病」）是否被正確重寫為去敏格式（「我姐姐 <NAME> 上週因為 <MEDICAL_HISTORY> 住院」）。
3.  **超時降級測試 (`test_semantic_guardrail_input_timeout_fallback`)**：
    Mock 呼叫 Gemini 的延遲為 2.5 秒（超過 2 秒限制）。驗證系統**不會崩潰**，而是自動熔斷並降級回 Regex 去敏文字，完美保障連線高可用。
4.  **輸出合規測試 (`test_semantic_guardrail_check_output_unsafe`)**：
    Mock 模型輸出不安全文字（包含內部敏感 API 洩漏），驗證輸出護欄是否成功將其重寫淨化為 `purified_text` 輸出。

---

## 4. 生產維運與開關控管 (Operations & Maintenance)

*   **一鍵關閉語意護欄 (Fallback to Regex-only)**：
    若生產環境發生大範圍網路故障，只需在 GCP Cloud Run 環境變數（或 Secret Manager）中，將：
    ```env
    ENABLE_SEMANTIC_GUARDRAILS=false
    ```
    系統會**瞬間無感降級**回 Legacy 僅 Regex 的去敏機制，不需修改代碼 or 重新發布部署。
*   **審計監控 (Auditing)**：
    所有的輸入攔截與安全違規事件，均會藉由 `agent.security_violation` 寫入 Cloud SQL 審計日誌中，為企業提供無可挑剔的安全合規審計鏈路。

---

## 5. 地端模型擴充與支援 (Ollama / LiteLLM Local Execution Pattern)

在開發或特定離線部署場景下，企業可能希望將語意安全護欄切換至地端託管的模型（例如：藉由 Ollama 執行的 Gemma 3、Llama 3 或其他開源模型）。

為了符合**開閉原則 (Open-Closed Principle)**，我們在不修改原 `app/security/semantic_guardrail.py` 原始程式碼的前提下，設計了基於繼承 (Inheritance) 的優雅擴充機制。

### 5.1 擴充模組：`app/security/local_semantic_guardrail.py`
我們藉由繼承原有的 `SemanticGuardrail` 與 `SemanticGuardrailPlugin`，覆寫核心 `_call_guardrail_llm` 方法，並透過 `litellm` (ADK 原生採用的第三方模型連接器) 來無縫對接 Ollama 等地端模型。

```python
# app/security/local_semantic_guardrail.py
from __future__ import annotations

import asyncio
import os
import litellm
from app.config import AppRuntimeConfig
from app.security.semantic_guardrail import SemanticGuardrail, SemanticGuardrailPlugin

class LocalSemanticGuardrail(SemanticGuardrail):
    def __init__(self, config: AppRuntimeConfig):
        super().__init__(config)
        self._local_model_name = os.getenv("GUARDRAIL_MODEL", "ollama_chat/gemma3:latest")
        self._api_base = os.getenv("GUARDRAIL_API_BASE", "http://localhost:11434")

    async def _call_guardrail_llm(self, system_instruction: str, prompt: str) -> dict:
        if "ollama" in self._local_model_name:
            os.environ["OLLAMA_API_BASE"] = self._api_base
        
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]

        response = await asyncio.wait_for(
            litellm.acompletion(
                model=self._local_model_name,
                messages=messages,
                temperature=0.0,
            ),
            timeout=2.0,
        )
        return self._parse_json_response(response.choices[0].message.content)

class LocalSemanticGuardrailPlugin(SemanticGuardrailPlugin):
    def __init__(self, config: AppRuntimeConfig):
        super().__init__(config)
        self._guardrail = LocalSemanticGuardrail(config)
```

### 5.2 載入與啟用機制 (How to load & run)
若要啟用此地端護欄，完全不需變更 `semantic_guardrail.py`。您只需在 `app/container.py` 或 `app/agent.py` 的載入插件邏輯中，將原有的 `SemanticGuardrailPlugin` 換成 `LocalSemanticGuardrailPlugin`：

```python
# 範例：在 app/container.py 載入地端語意安全護欄
if config.enable_semantic_guardrails:
    # 載入我們的地端擴充插件
    from app.security.local_semantic_guardrail import LocalSemanticGuardrailPlugin
    plugins.append(LocalSemanticGuardrailPlugin(config))
```

### 5.3 測試地端安全護欄
我們在 `tests/security/test_local_semantic_guardrail.py` 提供了對應的單元測試，模擬地端模型 (Ollama/LiteLLM) 呼叫。

執行以下指令以驗證地端擴充：
```bash
uv run pytest tests/security/test_local_semantic_guardrail.py
```