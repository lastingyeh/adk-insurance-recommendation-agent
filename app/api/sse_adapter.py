from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from google.adk.events.event import Event

from app.security.pii import filter_public_state
from app.services.session_service import safe_stringify

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


def build_public_state(raw_state: dict[str, Any]) -> dict[str, str]:
    """將原始狀態過濾並遮蔽 PII 後回傳給前端。"""
    public_state, _findings = filter_public_state(raw_state)
    return public_state


class SSEEnvelopeAdapter:
    """
    負責將領域級的 Agent 事件 (Event) 對應到前端 UI 專用的 SSE Envelopes。
    在串流期間，它會封裝並追蹤狀態（如 sequence 序號、重複文字過濾以及狀態合併）。
    """

    def __init__(self, prompt: str, initial_state: dict[str, str] | None = None) -> None:
        self.prompt = prompt.strip()
        self.sequence = 0
        self.step_text = ""
        self.seen_texts: set[str] = set()
        self.merged_state = dict(initial_state or {})

    def is_echoed_user_input(self, event: Event) -> bool:
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

        return any(
            (part.text or "").strip() == self.prompt for part in event.content.parts
        )

    def build_meta_envelope(self) -> dict[str, object]:
        """建立 Meta 封包，告知前端傳輸協議與執行模式。"""
        return {
            "type": "meta",
            "transport": "proxy",
            "notice": "目前由 FastAPI backend 直接代理 ADK Runner（SSE）。",
        }

    def build_done_envelope(self, final_text: str, state: dict[str, str]) -> dict[str, object]:
        """建立 Done 封包，標記執行結束並回傳最終文字與狀態。"""
        return {
            "type": "done",
            "finalText": final_text or "ADK runtime 已完成執行，請查看右側 event history。",
            "state": state,
        }

    def build_error_envelope(self, message: str, error_code: str | None = None) -> dict[str, object]:
        """建立 Error 封包，將錯誤訊息結構化回傳給前端。"""
        envelope: dict[str, object] = {
            "type": "error",
            "message": message,
        }
        if error_code:
            envelope["error_code"] = error_code
        return envelope

    def map_adk_event_to_envelopes(self, event: Event) -> list[dict[str, object]]:
        """核心轉換邏輯：將 ADK 原始事件轉換為前端通訊協議 (Envelopes)。

        設計考量：
        - 區分「業務工具」與「內部狀態工具」，決定 timeline 呈現方式。
        - 處理「部分文字回應」(partial)，用於實現流式打字效果。
        - 處理「狀態變動」(state_delta)，讓前端 State Inspector 即時同步。
        - 確保文字片段不重複觸發，維持流暢體驗。
        """
        self.sequence += 1
        event_id = event.id or f"evt-fastapi-{self.sequence}"
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
                if text and text not in self.seen_texts:
                    self.seen_texts.add(text)
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
            self.merged_state.update(patch)

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
