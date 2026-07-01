"""Agent 執行服務模組。

負責與 Google ADK Runner 互動，處理 AI Agent 的執行流程、串流回應、
狀態更新、事件轉換與 audit log。
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.events.event import Event
from google.adk.runners import Runner
from google.genai import types as genai_types

from app.config import AppRuntimeConfig
from app.security.pii import redact_text
from app.services.audit_log_service import AuditContext, AuditLogService
from app.services.session_service import SessionService, safe_stringify


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


def format_event_timestamp(timestamp: float | None) -> str:
    """格式化事件時間戳。

    確保返回台北時區 (UTC+8) 的時間，以與前端本地時間一致，避免排序錯誤。
    """
    from datetime import timedelta, timezone

    tz = timezone(timedelta(hours=8))
    if timestamp:
        value = datetime.fromtimestamp(timestamp, tz=tz)
    else:
        value = datetime.now(tz=tz)

    return value.strftime("%H:%M:%S")


def stringify_state_patch(state_delta: dict[str, object]) -> dict[str, str]:
    """將狀態變動 (State Patch) 中的所有值轉換為字串，確保與 ADK 持久化層相容。"""
    return {key: safe_stringify(value) for key, value in state_delta.items()}


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


def build_meta_envelope() -> dict[str, object]:
    """建立 Meta 封包，告知前端傳輸協議與執行模式。"""
    return {
        "type": "meta",
        "transport": "proxy",
        "notice": "目前由 FastAPI backend 直接代理 ADK Runner（SSE）。",
    }


def build_done_envelope(final_text: str, state: dict[str, str]) -> dict[str, object]:
    """建立 Done 封包，標記執行結束並回傳最終文字與狀態。"""
    return {
        "type": "done",
        "finalText": final_text,
        "state": state,
    }


def build_error_envelope(message: str) -> dict[str, object]:
    """建立 Error 封包，將錯誤訊息結構化回傳給前端。"""
    return {
        "type": "error",
        "message": message,
    }


def merge_state_patches(
    current_state: dict[str, str],
    envelopes: list[dict[str, object]],
) -> dict[str, str]:
    """從產生的一系列封包中提取狀態更新，並合併到目前的狀態中。"""
    merged_state = dict(current_state)

    for envelope in envelopes:
        if envelope.get("type") != "state":
            continue

        patch = envelope.get("patch", {})
        if isinstance(patch, dict):
            merged_state.update({str(key): str(value) for key, value in patch.items()})

    return merged_state


def map_adk_event_to_envelopes(event: Event, sequence: int) -> list[dict[str, object]]:
    """核心轉換邏輯：將 ADK 原始事件轉換為前端通訊協議 (Envelopes)。

    設計考量：
    - 區分「業務工具」與「內部狀態工具」，決定 timeline 呈現方式。
    - 處理「部分文字回應」(partial)，用於實現流式打字效果。
    - 處理「狀態變動」(state_delta)，讓前端 State Inspector 即時同步。
    - 確保文字片段不重複觸發，維持流暢體驗。
    """

    event_id = event.id or f"evt-fastapi-{sequence}"
    timestamp = format_event_timestamp(event.timestamp)
    envelopes: list[dict[str, object]] = []

    parts = event.content.parts if event.content and event.content.parts else []

    # 1. 處理工具呼叫 (Tool Calls) 與 工具結果 (Tool Responses)
    for part_index, part in enumerate(parts):
        suffix = f"{event_id}-{part_index}"

        # 處理模型發起的工具請求
        if part.function_call and part.function_call.name:
            tool_name = part.function_call.name
            is_internal = is_internal_session_tool(tool_name)

            envelopes.append(
                {
                    "type": "timeline",
                    "event": {
                        "id": f"{suffix}-call",
                        "kind": "internal" if is_internal else "tool-call",
                        "title": tool_name,
                        "summary": (
                            f"內部狀態工具 {tool_name}"
                            if is_internal
                            else f"ADK 請求工具 {tool_name}"
                        ),
                        "timestamp": timestamp,
                        "payload": [
                            f"args: {safe_stringify(part.function_call.args or {})}",
                            f"author: {event.author or 'agent'}",
                        ],
                    },
                }
            )

        # 處理工具執行完畢回傳的結果
        if part.function_response and part.function_response.name:
            tool_name = part.function_response.name
            is_internal = is_internal_session_tool(tool_name)

            envelopes.append(
                {
                    "type": "timeline",
                    "event": {
                        "id": f"{suffix}-result",
                        "kind": "internal" if is_internal else "tool-result",
                        "title": f"{tool_name} result",
                        "summary": (
                            f"內部狀態工具 {tool_name} 已完成"
                            if is_internal
                            else f"工具 {tool_name} 已回傳結果"
                        ),
                        "timestamp": timestamp,
                        "payload": [
                            f"response: {safe_stringify(part.function_response.response or {})}"
                        ],
                    },
                }
            )

    # 2. 處理文字回覆 (Agent Messages)
    # 彙整事件中的所有文字片段，避免在單一事件內多次觸發 append 導致內容重複
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
            # 建立 timeline 事件用於顯示對話氣泡或系統日誌
            envelopes.append(
                {
                    "type": "timeline",
                    "event": {
                        "id": f"{event_id}-{'stream' if event.partial else 'agent'}",
                        "kind": "stream" if event.partial else "agent",
                        "title": "partial_response"
                        if event.partial
                        else "agent_response",
                        "summary": full_text,
                        "timestamp": timestamp,
                        "payload": [
                            full_text,
                            f"author: {event.author or 'agent'}",
                            f"partial: {'true' if event.partial else 'false'}",
                        ],
                    },
                }
            )

            # 建立 message 封包，通知前端更新目前正顯示的文字內容
            envelopes.append(
                {
                    "type": "message",
                    "text": full_text,
                    "mode": "append" if event.partial else "replace",
                    "final": not bool(event.partial),
                }
            )

    # 3. 處理狀態更新 (State Changes)
    if event.actions and event.actions.state_delta:
        patch = stringify_state_patch(event.actions.state_delta)

        envelopes.append(
            {
                "type": "timeline",
                "event": {
                    "id": f"{event_id}-state",
                    "kind": "state",
                    "title": "state_delta",
                    "summary": "ADK session state 已更新",
                    "timestamp": timestamp,
                    "payload": [f"{key}: {value}" for key, value in patch.items()],
                },
            }
        )

        envelopes.append(
            {
                "type": "state",
                "patch": patch,
            }
        )

    return envelopes


class AgentRunService:
    """管理 Agent 執行週期的核心服務。

    負責調用 ADK Runner 並將其產生的非同步事件流轉換為前端可讀的 SSE 封包流時。
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

    async def _record_envelope_audit(
        self,
        *,
        audit_context: AuditContext,
        envelope: dict[str, object],
        sequence: int,
    ) -> None:
        """記錄已轉換為前端封包 (Envelope) 的內容到審計日誌。"""

        if not self._audit_logs:
            return

        envelope_type = str(envelope.get("type", ""))

        if envelope_type == "timeline":
            event = envelope.get("event", {})
            if not isinstance(event, dict):
                return

            kind = str(event.get("kind", "timeline"))  # type: ignore

            # 工具相關已在 _record_adk_event_audit 記錄過，此處跳過避免重複
            if kind in {"tool-call", "tool-result"}:
                return

            # 對應不同的 event kind 到 audit 的事件類型
            event_type = {
                "state": "agent.state_delta",
                "agent": "agent.message",
                "stream": "agent.message",
            }.get(kind, f"agent.{kind}")

            await self._audit_logs.record(
                context=audit_context,
                event_type=event_type,
                actor="agent",
                sequence=sequence,
                output_payload=event,
            )

        elif envelope_type == "error":
            # 記錄系統錯誤
            await self._audit_logs.record(
                context=audit_context,
                event_type="agent.error",
                actor="system",
                sequence=sequence,
                output_payload=envelope,
                policy_decision="error_redacted",
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
        accumulate_only: bool = False,
    ) -> AsyncGenerator[dict[str, object], None]:
        """執行 Agent 並串流回傳結果。

        核心流程：
        1. 初始化會話與 audit 記錄。
        2. 開始迭代 ADK Runner 的事件。
        3. 過濾回顯訊息，將原始事件轉換為前端封包流。
        4. 即時記錄 audit 與同步 session state。
        5. 累積完整文字回覆。
        6. 處理錯誤碼 (如 MAX_TOKENS)。
        7. 發送 Done 封包，附帶最終累積文字與完整狀態。

        :param accumulate_only: 若為 True，則不即時傳送流式文字，等全部完成後才一次回傳。
        """

        sequence = 0
        total_text = ""  # 累積所有步驟的總文字，作為最終回覆
        step_text = ""  # 當前步驟（一次生成回合）累積的文字
        merged_state = dict(session_state or {})

        # 在進入任何下游流程前先去敏 prompt：
        # 1. 送往 LLM 的訊息不含明文 PII（build_user_message_content 也會再去敏，冪等）。
        # 2. is_echoed_user_input 以去敏後文字比對 ADK 回顯，避免含 PII 的
        #    使用者訊息以回顯形式漏到前端 timeline。
        # 3. 稽核記錄到的 prompt 與實際送出的內容一致。
        prompt, _prompt_pii = redact_text(prompt)

        resolved_user_id = (
            user_id.strip() if user_id and user_id.strip() else self._config.api_user_id
        )

        # 首先發送 meta 資訊
        yield build_meta_envelope()

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

                # 將原始事件轉換為一或多個前端封包
                envelopes = map_adk_event_to_envelopes(event, sequence)
                # 從封包中同步最新的狀態快照
                merged_state = merge_state_patches(merged_state, envelopes)

                for envelope in envelopes:
                    # 記錄封包層級的審計
                    if self._audit_logs and audit_context:
                        await self._record_envelope_audit(
                            audit_context=audit_context,
                            envelope=envelope,
                            sequence=sequence,
                        )

                    # 處理文字訊息封包
                    if envelope.get("type") == "message":
                        text = str(envelope.get("text", ""))
                        mode = envelope.get("mode")

                        if mode == "append":
                            step_text += text
                        else:
                            # 收到 replace 模式，代表這一段生成已完成
                            step_text = text

                        # 如果前端支援流式顯示，則將封包推送到前端
                        if not accumulate_only:
                            yield envelope
                    else:
                        # 處理 timeline 或 state 等其他類型的封包
                        yield envelope

                # 偵測是否為非 partial 的回應結束點
                if not event.partial and step_text:
                    # 將目前步驟累積的文字併入總文字紀錄中
                    if total_text and not total_text.endswith("\n"):
                        total_text += "\n\n"
                    total_text += step_text
                    step_text = ""

                # 處理執行過程中發生的錯誤
                if event.error_code:
                    error_message = event.error_message or "Unknown error"
                    if event.error_code == "MAX_TOKENS":
                        error_message = "模型輸出長度達到上限，已自動截斷。"
                    elif event.error_code == "RESOURCE_EXHAUSTED":
                        error_message = "Gemini API 資源耗盡，請稍後再試。"

                    error_envelope = build_error_envelope(
                        f"{event.error_code}: {error_message}"
                    )

                    if self._audit_logs and audit_context:
                        await self._record_envelope_audit(
                            audit_context=audit_context,
                            envelope=error_envelope,
                            sequence=sequence + 1,
                        )
                    yield error_envelope
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

            # 發送 Done 封包，完成 SSE 串流
            yield build_done_envelope(
                final_text=total_text
                or "ADK runtime 已完成執行，請查看右側 event history。",
                state=final_state,
            )

        except Exception as exc:
            # 擷取任何非預期的例外並告知前端
            error_envelope = build_error_envelope(str(exc))

            if self._audit_logs and audit_context:
                await self._record_envelope_audit(
                    audit_context=audit_context,
                    envelope=error_envelope,
                    sequence=sequence + 1,
                )

            yield error_envelope
