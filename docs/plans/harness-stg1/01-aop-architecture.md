# AI Harness 企業級重構計畫 - 階段一：AOP 攔截器與流程控制架構設計

本文件詳細記錄了外置 **AOP (Aspect-Oriented Programming) 安全護欄解決方案** 的技術架構、攔截生命週期以及在 Python 中的非同步攔截管道設計。

---

## 1. AOP 設計哲學與非侵入性 (Non-Intrusiveness)
傳統系統中，安全規則與業務邏輯高度耦合，常寫入主 Agent 的 System Prompt 中。本設計採用 AOP（面向切面）設計：
*   **橫切關注點分離 (Separation of Concerns)：** 安全與隱私（Regex PII 遮蔽、LLM 語意安全、注入攻擊偵測）作為獨立的「切面 (Aspect)」處理。
*   **零侵入性 (Non-Intrusive)：** 內層的業務大腦（各垂直領域 Sub-agents）與 ADK Runner 完全無感知。它們專注於純粹的保險業務推理，安全過濾在呼叫前與呼叫後被動實施。

---

## 2. 攔截生命週期 (Interception Lifecycle)

AOP 護欄在一個完整的對話請求中，會精準在三個時間點進行切面攔截：

```
       [ 使用者輸入 Prompt ]
                 │
                 ▼
 ┌──────────────────────────────┐
 │   OnInput 切面 (Pre-Aspect)   │ ──► 1. PII Regex 快速通道
 └──────────────────────────────┘     2. LLM 語意注入與個資過濾 (2.0s Timeout)
                 │                    3. 偵測注入 ──► [拋出 Exception 阻斷]
                 ▼ (安全無敏 Prompt)
 ┌──────────────────────────────┐
 │       ADK Runner 執行期       │ ──► 異步串流迭代器 (iter_run_events)
 └──────────────────────────────┘
                 │ (串流輸出字元)
                 ▼
 ┌──────────────────────────────┐
 │ OnStreamEvent 切面 (In-flight)│ ──► 即時轉發中間生成 (不作 LLM 護欄，優化延遲)
 └──────────────────────────────┘
                 │
                 ▼ (累計最終回覆 text)
 ┌──────────────────────────────┐
 │  OnOutput 切面 (Post-Aspect)  │ ──► 1. 輸出合規 LLM 檢查
 └──────────────────────────────┘     2. 敏感系統指令遮蔽 / 幻覺承諾修正
                 │
                 ▼ (安全淨化後的 final_text)
       [ 產出 Done 封包至前端 ]
```

### 2.1. OnInput 切面 (Pre-Aspect)
*   **時間點：** 在使用者 Prompt 送入 ADK Runner 的 `iter_run_events` 之前。
*   **執行動作：**
    1.  **Regex Fast-Pass：** 呼叫 `pii.py` 處理標準格式 PII，若 Regex 完整過濾且滿足需求，則依據策略評估是否縮短 LLM 流程。
    2.  **LLM 語意審查：** 呼叫 `SemanticGuardrail.check_input` 對 Prompt 進行對抗性攻擊與非結構個資判定。
*   **攔截決策：**
    *   *Safe = True：* 用遮蔽後的安全 Prompt 替換原始輸入，繼續執行。
    *   *Safe = False：* 拋出 `PromptInjectionException`，阻斷下游 Runner 執行。

### 2.2. OnStreamEvent 切面 (In-flight Aspect)
*   **時間點：** 在 Runner 異步迭代產生 Event 時。
*   **執行動作：** 原始文字 Chunks 會被即時轉發給前端，確保最流暢的打字機效果。安全攔截會留到最後的 `OnOutput` 做終極淨化。不對每個 Chunk 進行 LLM 安全評估，以避免 TTFT (首字延遲) 爆炸。

### 2.3. OnOutput 切面 (Post-Aspect)
*   **時間點：** 當 Runner 串流完全結束、準備發送最終 `Done` 封包前。
*   **執行動作：** 呼叫 `SemanticGuardrail.check_output(total_text)`。
*   **攔截決策：** 若檢測到敏感 API 名稱洩漏或過度承諾（如「100% 保證賠償」），動態重寫輸出文字，將淨化後的 safe text 裝入 `Done` 封包發送，同步更新會話狀態與稽核日誌。

---

## 3. Python 非同步切面管道實作設計

由於 `AgentRunService.stream` 是 `AsyncGenerator`，我們採用 **顯式攔截管道 (Explicit Interception Pipeline)** 模式實作：

```python
# app/services/agent_run_service.py 內部的 AOP 切面實作示意

class AgentRunService:
    def __init__(self, runner: Runner, sessions: SessionService, config: AppRuntimeConfig, ...):
        self._runner = runner
        self._sessions = sessions
        self._config = config
        self._guardrail = SemanticGuardrail(config)  # 初始化外置 AOP 護欄

    async def stream(self, prompt: str, ...) -> AsyncGenerator[dict[str, object], None]:
        sequence = 0
        total_text = ""
        step_text = ""
        
        # 立即 Yield Meta 封包，初始化連線
        yield build_meta_envelope()

        # ==========================================
        # 1. 【OnInput 切面攔截點】(Pre-Aspect)
        # ==========================================
        try:
            # 呼叫外置 AOP 護欄，進行安全清洗與注入檢查
            prompt = await self._guardrail.check_input(prompt)
        except PromptInjectionException as p_exc:
            # 【AOP 異常阻斷分支】：安全中止連線，直接發送安全違規 Envelope
            logger.error(f"Prompt Injection Blocked! Err: {p_exc}")
            
            error_envelope = build_error_envelope(
                "[SECURITY_VIOLATION] 偵測到異常輸入指令，對話已安全中止。",
                error_code="SECURITY_VIOLATION"
            )
            # 記錄到安全審計日誌
            if self._audit_logs and audit_context:
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="agent.security_violation",
                    actor="system",
                    sequence=sequence + 1,
                    output_payload={"reason": str(p_exc)}
                )
            yield error_envelope
            return  # 終止產生器，阻斷後續執行，保護內層 Agent

        # ==========================================
        # 2. 【核心業務執行期與 OnStreamEvent 切面】
        # ==========================================
        try:
            # 正常執行 ADK Runner，此時送入的 prompt 已經過 AOP 安全淨化
            async for event in iter_run_events(prompt=prompt, ...):
                # 過濾重複 echo 使用者輸入
                if is_echoed_user_input(event, prompt):
                    continue
                sequence += 1
                
                # 轉換為前端 envelope
                envelopes = map_adk_event_to_envelopes(event, sequence)
                for envelope in envelopes:
                    if envelope.get("type") == "message":
                        # 即時 yield 中間生成字元，不調用護欄以優化延遲 (OnStreamEvent 快速通道)
                        yield envelope

            # ==========================================
            # 3. 【OnOutput 切面攔截點】(Post-Aspect)
            # ==========================================
            if total_text:
                # 呼叫外置 AOP 護欄進行輸出安全合規淨化
                purified_text = await self._guardrail.check_output(total_text)
                if purified_text != total_text:
                    # 如果輸出違反安全政策，AOP 護欄進行了文字修正，則將其替換
                    logger.warning("Output sanitized by AOP guardrail.")
                    total_text = purified_text

            # 從資料庫獲取最終同步後的狀態
            final_state = await self._sessions.get_state(...)

            # 記錄安全淨化後的完整對話日誌
            if self._audit_logs and audit_context:
                await self._audit_logs.record(
                    ...,
                    output_payload={"finalText": total_text, "state": final_state}
                )

            # 發送 Done 封包，此時 final_text 已是 100% 安全無敏感資訊的文字
            yield build_done_envelope(final_text=total_text, state=final_state)

        except Exception as exc:
            yield build_error_envelope(str(exc))
```

---

## 4. 跨服務與介面複用 (AOP Reusability)
本 AOP 護欄元件設計完全解耦，除了可用於文字 SSE 串流，亦可直接重用於即時語音 WebSocket (`LiveAgentService`)：

```python
class LiveAgentService:
    def __init__(self, runner: Runner, sessions: SessionService, config: AppRuntimeConfig):
        self._guardrail = SemanticGuardrail(config)
        ...

    async def handle_user_audio_transcript(self, transcript: str):
        """當即時語音轉譯出文字時，優先進入 OnInput AOP 切面。"""
        try:
            # 語音轉譯文字安全檢查與個資遮蔽
            safe_transcript = await self._guardrail.check_input(transcript)
            return safe_transcript
        except PromptInjectionException:
            # 拋出語音違規警告，或中斷 WebSocket 連線
            await self.send_audio_warning("系統偵測到敏感語音指令，已暫停處理。")
```
