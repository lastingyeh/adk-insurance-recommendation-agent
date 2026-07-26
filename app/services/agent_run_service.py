"""Agent 執行服務模組。

負責與 Google ADK Runner 互動，處理 AI Agent 的執行流程、狀態更新與 audit log。
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator

from google.adk.events.event import Event
from google.adk.runners import Runner
import logging
from google.genai import types as genai_types

from app.config import AppRuntimeConfig
from app.security.pii import redact_text
from app.security.semantic_guardrail import PromptInjectionException
from app.services.audit_log_service import AuditContext, AuditLogService
from app.services.session_service import SessionService, safe_stringify

logger = logging.getLogger(__name__)


# 定義內部 Session 工具，這些工具通常不直接對使用者顯示，而是用於後端狀態管理
INTERNAL_SESSION_TOOLS = {
    "get_user_profile_snapshot",
    "save_user_profile",
    "save_last_recommendation",
    "clear_last_recommendation",
}

# 定義查詢類工具，這些工具代表 AI 代理人正在進行實際的業務操作，應該顯示在對話歷程中
QUERY_TOOLS = {
    "search_medical_products",
    "search_accident_products",
    "search_family_protection_products",
    "search_income_protection_products",
    "get_product_detail",
    "get_product_details",
    "get_product_by_name",
    "search_products_by_name",
    "get_recommendation_rules",
}


def classify_tool_name(tool_name: str) -> str:
    """分類工具用途，用於決定前端顯示邏輯。

    - state 類：內部 session/state 管理工具，預設不顯示在使用者 timeline。
    - query 類：業務查詢工具，應顯示在使用者 timeline 並呈現豐富的 UI。
    """

    if tool_name in INTERNAL_SESSION_TOOLS:
        return "state"
    if tool_name in QUERY_TOOLS:
        return "query"
    return "tool"


def is_internal_session_tool(tool_name: str) -> bool:
    """判斷工具是否為內部狀態管理工具。"""
    return classify_tool_name(tool_name) == "state"


def is_echoed_user_input(event: Event, prompt: str) -> bool:
    """判斷 ADK 事件是否只是使用者輸入的回顯 (echo)。

    在 ADK 的執行流中，第一個事件通常會回傳使用者的原始輸入內容。
    為了避免在前端顯示重複的使用者訊息，此函式用於過濾這些事件。
    """

    if event.author != "user" or not event.content or not event.content.parts:
        return False

    # 如果包含功能回覆或功能呼叫，則不是單純的回顯
    if any(part.function_response for part in event.content.parts):
        return False

    if any(part.function_call for part in event.content.parts):
        return False

    normalized_prompt = prompt.strip()
    return any(
        (part.text or "").strip() == normalized_prompt for part in event.content.parts
    )


def build_user_message_content(
    prompt: str, image: str | None = None, image_type: str | None = None
) -> genai_types.Content:
    """建構適合傳遞給 Google GenAI SDK 的使用者訊息內容，支援多模態圖片輸入。

    安全性：這是使用者訊息實際送往第三方 LLM (Gemini) 的唯一咽喉點，
    因此在此對 prompt 做 PII 去敏。原本明文的電話 / email / 身分證 /
    信用卡會在這裡被換成 [REDACTED_*] 佔位字，避免明文 PII 外送給 LLM。
    去敏為冪等操作，呼叫端若已先去敏也不會造成二次破壞。
    """
    redacted_prompt, _findings = redact_text(prompt)
    parts = [genai_types.Part(text=redacted_prompt)]
    if image and image_type:
        # 如果有圖片，將 Base64 編碼的圖片解碼並封裝為 Blob
        parts.append(
            genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type=image_type,
                    data=base64.b64decode(image),
                )
            )
        )
    return genai_types.Content(
        role="user",
        parts=parts,
    )


async def iter_run_events(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    prompt: str,
    image: str | None = None,
    image_type: str | None = None,
    state_delta: dict[str, str] | None = None,
) -> AsyncGenerator[Event, None]:
    """封裝 ADK Runner 的 run_async 調用，提供非同步迭代器。"""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=build_user_message_content(prompt, image, image_type),
        state_delta=state_delta or None,
    ):
        yield event


class AgentRunService:
    """管理 Agent 執行週期的核心服務。

    負責調用 ADK Runner 並管理執行生命週期與稽核日誌。
    """

    def __init__(
        self,
        runner: Runner,
        sessions: SessionService,
        config: AppRuntimeConfig,
        audit_logs: AuditLogService | None = None,
    ) -> None:
        self._runner = runner
        self._sessions = sessions
        self._config = config
        self._audit_logs = audit_logs

    async def ensure_session(
        self,
        session_id: str,
        initial_state: dict[str, str] | None = None,
        user_id: str | None = None,
    ) -> None:
        """啟動任務前，確保工作階段 (Session) 已在資料庫中初始化。"""
        await self._sessions.ensure_session(
            session_id,
            initial_state,
            user_id=user_id,
        )

    async def _record_adk_event_audit(
        self,
        *,
        audit_context: AuditContext,
        event: Event,
        sequence: int,
    ) -> None:
        """將 ADK 原始事件記錄到審計日誌 (Audit Log) 中。

        特別是工具呼叫與結果，即使是 UI 隱藏的工具也會被記錄，以確保符合法規審計需求。
        """

        if not self._audit_logs:
            return

        parts = event.content.parts if event.content and event.content.parts else []

        for part_index, part in enumerate(parts):
            # 使用 sequence * 100 確保同一事件內的複數 part 有獨立且有序的序號
            audit_sequence = sequence * 100 + part_index

            # 記錄工具請求
            if part.function_call and part.function_call.name:
                tool_name = part.function_call.name
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="agent.tool_call",
                    actor="agent",
                    tool_name=tool_name,
                    sequence=audit_sequence,
                    input_payload={
                        "tool_name": tool_name,
                        "tool_class": classify_tool_name(tool_name),
                        "args": part.function_call.args or {},
                        "author": event.author or "agent",
                    },
                )

            # 記錄工具結果
            if part.function_response and part.function_response.name:
                tool_name = part.function_response.name
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="agent.tool_result",
                    actor="tool",
                    tool_name=tool_name,
                    sequence=audit_sequence + 1,
                    output_payload={
                        "tool_name": tool_name,
                        "tool_class": classify_tool_name(tool_name),
                        "response": part.function_response.response or {},
                        "author": event.author or "tool",
                    },
                )

    async def _record_raw_event_audit(
        self,
        *,
        audit_context: AuditContext,
        event: Event,
        sequence: int,
    ) -> None:
        """記錄原始文字訊息、狀態更新與錯誤至審計日誌。"""
        if not self._audit_logs:
            return

        # 1. 記錄文字訊息
        parts = event.content.parts if event.content and event.content.parts else []
        seen_texts = set()
        text_parts = []
        for part in parts:
            if part.text:
                text = part.text.strip()
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    text_parts.append(text)

        if text_parts and event.author != "user":
            full_text = "\n\n".join(text_parts).strip()
            if full_text:
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="agent.message",
                    actor="agent",
                    sequence=sequence,
                    output_payload={
                        "text": full_text,
                        "partial": bool(event.partial),
                    },
                )

        # 2. 記錄狀態更新
        if event.actions and event.actions.state_delta:
            patch = {k: safe_stringify(v) for k, v in event.actions.state_delta.items()}
            await self._audit_logs.record(
                context=audit_context,
                event_type="agent.state_delta",
                actor="agent",
                sequence=sequence,
                output_payload={"patch": patch},
            )

        # 3. 記錄錯誤
        if event.error_code:
            await self._audit_logs.record(
                context=audit_context,
                event_type="agent.error",
                actor="system",
                sequence=sequence,
                output_payload={
                    "error_code": event.error_code,
                    "error_message": event.error_message or "Unknown error",
                },
            )

    async def stream(
        self,
        *,
        prompt: str,
        session_id: str,
        session_state: dict[str, str] | None = None,
        user_id: str | None = None,
        image: str | None = None,
        image_type: str | None = None,
        audit_context: AuditContext | None = None,
    ) -> AsyncGenerator[Event, None]:
        """執行 Agent 並串流回傳原始事件 (Event)。

        核心流程：
        1. 初始化會話與 audit 記錄。
        2. 開始迭代 ADK Runner 的事件。
        3. 記錄 audit。
        4. 累積完整文字回覆。
        5. 記錄 Done 審計。
        """

        sequence = 0
        total_text = ""  # 累積所有步驟的總文字，作為最終回覆
        step_text = ""  # 當前步驟（一次生成回合）累積的文字
        merged_state = dict(session_state or {})

        # 在進入任何下游流程前先去敏 prompt
        prompt, _prompt_pii = redact_text(prompt)

        resolved_user_id = (
            user_id.strip() if user_id and user_id.strip() else self._config.api_user_id
        )

        # 記錄使用者輸入到審計日誌
        if self._audit_logs and audit_context:
            await self._audit_logs.record(
                context=audit_context,
                event_type="user.prompt.received",
                actor="user",
                sequence=0,
                input_payload={
                    "prompt": prompt,
                    "has_image": bool(image),
                },
            )

        try:
            # 調用 ADK Runner 開始非同步執行
            async for event in iter_run_events(
                self._runner,
                user_id=resolved_user_id,
                session_id=session_id,
                prompt=prompt,
                image=image,
                image_type=image_type,
                state_delta=session_state,
            ):
                # 過濾重複的使用者輸入回顯
                if is_echoed_user_input(event, prompt):
                    continue

                sequence += 1

                # 記錄原始 ADK 事件審計
                if self._audit_logs and audit_context:
                    await self._record_adk_event_audit(
                        audit_context=audit_context,
                        event=event,
                        sequence=sequence,
                    )
                    await self._record_raw_event_audit(
                        audit_context=audit_context,
                        event=event,
                        sequence=sequence,
                    )

                # 累積文字片段，用於最後 completion 記錄
                parts = event.content.parts if event.content and event.content.parts else []
                seen_texts = set()
                text_parts = []
                for part in parts:
                    if part.text:
                        text = part.text.strip()
                        if text and text not in seen_texts:
                            seen_texts.add(text)
                            text_parts.append(text)

                if text_parts and event.author != "user":
                    full_text = "\n\n".join(text_parts).strip()
                    if full_text:
                        if event.partial:
                            step_text += full_text
                        else:
                            step_text = full_text

                # 整合最新狀態變動，用於 final_state 合併
                if event.actions and event.actions.state_delta:
                    patch = {k: safe_stringify(v) for k, v in event.actions.state_delta.items()}
                    merged_state.update(patch)

                # 偵測是否為非 partial 的回應結束點
                if not event.partial and step_text:
                    # 將目前步驟累積的文字併入總文字紀錄中
                    if total_text and not total_text.endswith("\n"):
                        total_text += "\n\n"
                    total_text += step_text
                    step_text = ""

                # Yield raw event to downstream (API Layer)
                yield event

                if event.error_code:
                    break

            # 確保結束前處理最後殘留的文字片段
            if step_text:
                if total_text and not total_text.endswith("\n"):
                    total_text += "\n\n"
                total_text += step_text

            # 從資料庫獲取最終同步後的狀態，確保與 Runner 持久化內容一致
            final_state = await self._sessions.get_state(
                session_id=session_id,
                fallback_state=merged_state,
                user_id=user_id,
            )

            # 記錄整個回應結束的審計日誌
            if self._audit_logs and audit_context:
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="response.completed",
                    actor="agent",
                    sequence=sequence + 1,
                    output_payload={
                        "finalText": total_text,
                        "state": final_state,
                    },
                )

        except PromptInjectionException as p_exc:
            logger.error(
                f"Prompt Injection detected! Session: {session_id}. Error: {p_exc}"
            )
            # 記錄到審計日誌
            if self._audit_logs and audit_context:
                await self._audit_logs.record(
                    context=audit_context,
                    event_type="agent.security_violation",
                    actor="system",
                    sequence=sequence + 1,
                    output_payload={"reason": str(p_exc)},
                )
            raise

        except Exception as exc:
            raise
