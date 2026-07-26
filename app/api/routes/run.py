from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.dependencies import get_container, get_current_user
from app.api.schemas import AgentRunRequest
from app.services.agent_run_service import AgentRunService
from app.api.schemas import UserInDB
import uuid
from app.services.audit_log_service import AuditContext
from app.api.sse_adapter import SSEEnvelopeAdapter
from app.security.semantic_guardrail import PromptInjectionException

"""
app/api/routes/run.py

此模組實作了代理人執行 (Agent Run) 的核心 API 端點。
負責接收使用者的文字或多模態提示詞，觸發 Agent 邏輯，並透過 SSE (Server-Sent Events) 即時回傳結果。
"""


def encode_sse_event(envelope: dict[str, object]) -> str:
    """
    將資料封裝為伺服器傳送事件 (SSE) 格式。
    SSE 格式要求以 `data: {JSON}\n\n` 結尾。
    """
    return f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"


router = APIRouter(prefix="/api/agent", tags=["agent"])


def get_runner(request: Request | None = None):
    """
    從相依性注入容器中獲取 Runner 實例。
    """
    return get_container(request).runner


def get_agent_run_service(request: Request) -> AgentRunService:
    """
    獲取 Agent 執行服務。
    如果請求上下文中的 Runner 不同於容器預設，則建立新的服務實例。
    這在某些進階測試場景或動態切換模型的場景下很有用。
    """
    container = get_container(request)

    try:
        runner = get_runner(request)
    except TypeError:
        runner = get_runner()

    if runner is container.runner:
        return container.agent_runs

    return AgentRunService(runner, container.sessions, container.config)


@router.post("/run")
async def run_agent(
    payload: AgentRunRequest,
    request: Request,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    執行 Agent 的主要 API 端點。
    接收使用者提示詞，並以流式 (Streaming) 方式回傳回應。

    執行流程：
    1. 驗證 JWT Token (透過 Depends(get_current_user))，確認使用者身分。
    2. 檢查請求參數與權限 (確認 userId 一致性)。
    3. 建立 AuditContext 用於追蹤與稽核此筆請求。
    4. 確保使用者的對話 Session 已在資料庫中初始化。
    5. 定義 SSE 產生器 (sse_generator)，在其中呼叫 run_service.stream()。
    6. 回傳 StreamingResponse，將產生的封包即時推送到前端。
    """
    # 權限檢查：確保前端請求的 userId 與 Token 擁有者一致
    if payload.userId and current_user.username != payload.userId:
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    prompt = payload.prompt.strip()
    session_id = payload.sessionId.strip()

    # 驗證必要欄位
    if not prompt or not session_id:
        return JSONResponse(
            status_code=400,
            content={"error": "prompt and sessionId are required"},
        )

    # 從請求標頭中提取追蹤 ID，若無則生成新的 UUID，便於分散式追蹤 (Distributed Tracing)
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
    resolved_user_id = payload.userId or get_container(request).config.api_user_id

    # 建立稽核上下文，這些資訊將跟隨所有因此請求而產生的日誌與行為
    audit_context = AuditContext(
        trace_id=trace_id,
        request_id=request_id,
        session_id=session_id,
        user_id=resolved_user_id,
    )

    run_service = get_agent_run_service(request)

    try:
        # 確保對話會話存在並同步前端傳來的最新狀態
        await run_service.ensure_session(
            session_id, payload.sessionState, user_id=payload.userId
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": f"Unable to ensure session: {exc}"},
        )

    async def sse_generator() -> AsyncGenerator[str, None]:
        """
        生成 SSE 格式的流式輸出。
        將 AgentRunService 產生的原始 Event 事件利用 SSEEnvelopeAdapter 轉換為 JSON SSE 封包字串。
        """
        adapter = SSEEnvelopeAdapter(prompt=prompt, initial_state=payload.sessionState)

        # 1. 首先發送 meta 資訊
        yield encode_sse_event(adapter.build_meta_envelope())

        total_text = ""
        accumulate_only = not payload.stream

        try:
            async for event in run_service.stream(
                prompt=prompt,
                session_id=session_id,
                session_state=payload.sessionState,
                user_id=payload.userId,
                image=payload.image,
                image_type=payload.imageType,
                audit_context=audit_context,
            ):
                # 2. 轉換為前端所需的 SSE 封包
                envelopes = adapter.map_adk_event_to_envelopes(event)

                for envelope in envelopes:
                    if envelope.get("type") == "message":
                        text = str(envelope.get("text", ""))
                        mode = envelope.get("mode")

                        if mode == "append":
                            adapter.step_text += text
                        else:
                            adapter.step_text = text

                        if not accumulate_only:
                            yield encode_sse_event(envelope)
                    else:
                        yield encode_sse_event(envelope)

                # 累積文字片段，用於最後 completion Done 封包
                if not event.partial and adapter.step_text:
                    if total_text and not total_text.endswith("\n"):
                        total_text += "\n\n"
                    total_text += adapter.step_text
                    adapter.step_text = ""

            # 確保最後生成回合結束時合併其餘片段
            if adapter.step_text:
                if total_text and not total_text.endswith("\n"):
                    total_text += "\n\n"
                total_text += adapter.step_text

            # 從資料庫獲取最終同步後的狀態
            final_state = await get_container(request).sessions.get_state(
                session_id=session_id,
                fallback_state=adapter.merged_state,
                user_id=payload.userId,
            )

            # 3. 發送 Done 封包，完成 SSE 串流
            yield encode_sse_event(adapter.build_done_envelope(total_text, final_state))

        except PromptInjectionException as p_exc:
            yield encode_sse_event(
                adapter.build_error_envelope(
                    "[SECURITY_VIOLATION] 偵測到異常輸入指令，對話已安全中止。",
                    error_code="SECURITY_VIOLATION",
                )
            )
        except Exception as exc:
            yield encode_sse_event(adapter.build_error_envelope(str(exc)))

    # 回傳流式回應，設定正確的媒體類型 (text/event-stream) 與標頭以支援 SSE
    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )
