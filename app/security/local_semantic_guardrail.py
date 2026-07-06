from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from google.adk.agents import InvocationContext
from google.adk.events.event import Event
from google.genai import types

from app.config import AppRuntimeConfig
from app.security.semantic_guardrail import (
    SemanticGuardrail,
    SemanticGuardrailPlugin,
)

logger = logging.getLogger(__name__)


class LocalSemanticGuardrail(SemanticGuardrail):
    """地端模型/多雲模型安全護欄 (擴充類別)。

    此類別繼承自 `SemanticGuardrail`，在不改動原 `semantic_guardrail.py` 程式碼的情況下，
    藉由覆寫 `_call_guardrail_llm` 方法，將審查請求導向地端模型服務 (例如：Ollama、LiteLLM 或
    與 OpenAI 相容的 API 接口)。
    """

    def __init__(self, config: AppRuntimeConfig):
        # 呼叫父類別初始化。若 GenAI 憑證不存在而初始化 client 失敗，父類別會優雅記錄並設為 None。
        super().__init__(config)

        # 讀取地端模型專屬配置（提供合理的預設值）
        # 模型格式範例: "ollama_chat/gemma3:latest" 或 "openai/mistral-small3.1"
        self._local_model_name = os.getenv("GUARDRAIL_MODEL", "ollama_chat/gemma3:latest")
        # 地端 API 基底網址，例如 Ollama 預設為 http://localhost:11434
        self._api_base = os.getenv("GUARDRAIL_API_BASE", "http://localhost:11434")
        self._api_key = os.getenv("GUARDRAIL_API_KEY", "ollama")

    async def _call_guardrail_llm(self, system_instruction: str, prompt: str) -> dict:
        """覆寫父類別的 LLM 呼叫方法，改為調用地端模型或 LiteLLM 完成語意審查。"""
        # 延遲匯入 litellm 以免在不需地端模型時引入額外依賴
        import litellm

        # 設定 OLLAMA_API_BASE 或其他 LiteLLM 需要的環境變數
        if "ollama" in self._local_model_name:
            # LiteLLM 官方建議將 Ollama 位址設在 OLLAMA_API_BASE
            os.environ["OLLAMA_API_BASE"] = self._api_base
        elif "openai" in self._local_model_name:
            os.environ["OPENAI_API_BASE"] = self._api_base
            os.environ["OPENAI_API_KEY"] = self._api_key

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]

        # 針對支援 JSON Mode 的模型（例如 Gemma 3, Llama 3）啟用 JSON 物件格式限制
        response_format = None
        if (
            "gemma" in self._local_model_name
            or "llama" in self._local_model_name
            or "openai" in self._local_model_name
        ):
            response_format = {"type": "json_object"}

        # 藉由 asyncio.wait_for 執行超時監控（對齊原本的 2.0 秒限制）
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=self._local_model_name,
                messages=messages,
                temperature=0.0,
                response_format=response_format,
            ),
            timeout=2.0,
        )

        if not response or not response.choices:
            raise ValueError("Empty response from local Guardrail LLM.")

        content = response.choices[0].message.content
        if not content:
            raise ValueError("Guardrail LLM returned empty content.")

        return self._parse_json_response(content)


class LocalSemanticGuardrailPlugin(SemanticGuardrailPlugin):
    """地端模型安全護欄的 ADK 原生插件封裝。

    繼承自 `SemanticGuardrailPlugin`，覆寫初始化邏輯，將內建的 `_guardrail` 大腦
    替換為 `LocalSemanticGuardrail`，藉此在不改動任何 callback 與攔截邏輯的情況下，
    無縫切換為地端審查引擎。
    """

    def __init__(self, config: AppRuntimeConfig):
        # 呼叫父類別初始化
        super().__init__(config)
        # 將大腦替換為地端護欄實作
        self._guardrail = LocalSemanticGuardrail(config)
