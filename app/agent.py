from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.tools.toolbox_toolset import ToolboxToolset
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin,
)
from google.genai import types
from toolbox_core.protocol import Protocol

from app.config import load_runtime_config
from app.tools.session_tools import (
    clear_last_recommendation,
    get_user_profile_snapshot,
    save_last_recommendation,
    save_user_profile,
)

# 定義提示詞檔案的路徑，該檔案包含保險代理人的系統指令
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "insurance_agent_prompt.txt"


def load_agent_prompt() -> str:
    """
    從檔案中讀取保險代理人的系統提示詞 (System Prompt)。
    """
    return PROMPT_PATH.read_text(encoding="utf-8")


class AgentFactory:
    """
    代理人配置工廠類別，負責初始化代理人所需的工具集與核心物件。
    """

    def __init__(self, config) -> None:
        """
        初始化工廠。
        :param config: AppRuntimeConfig 執行階段配置實例
        """
        self._config = config

    def create_toolbox(self) -> ToolboxToolset:
        """
        建立 Toolbox 工具集，用於與外部 MCP (Model Context Protocol) 伺服器通訊。
        MCP 伺服器通常提供保險產品檢索等核心業務功能。
        """
        return ToolboxToolset(
            server_url=self._config.toolbox_server_url,
            protocol=Protocol.MCP_LATEST,
        )

    def build_tools(self) -> list[Any]:
        """
        建構代理人可使用的工具列表。
        包含本地 Session 處理工具與遠端 Toolbox 工具。
        """
        return [
            get_user_profile_snapshot,  # 獲取使用者個人資料快照
            save_user_profile,  # 儲存/更新使用者個人資料
            save_last_recommendation,  # 儲存最後一次的推薦結果
            clear_last_recommendation,  # 清除最後一次的推薦紀錄
            self.create_toolbox(),  # 遠端工具集 (提供保險知識庫與 FAQ 檢索等)
        ]

    def create(self, model_name: str | None = None) -> Agent:
        """
        建立並回傳 Google ADK Agent 實例。
        """
        return Agent(
            name=self._config.app_name,
            model=model_name or self._config.model_name,
            instruction=load_agent_prompt(),
            tools=self.build_tools(),
            generate_content_config=types.GenerateContentConfig(
                max_output_tokens=self._config.max_output_tokens,
            ),
        )


def create_agent(config=None) -> Agent:
    """
    輔助函式：根據配置建立代理人。
    如果未提供配置，則會載入預設的執行階段配置。
    """
    runtime_config = config or load_runtime_config()
    return AgentFactory(runtime_config).create()


# 載入執行階段配置
runtime_config = load_runtime_config()

# 建立全域的 root_agent 實例供應用程式使用
root_agent = create_agent(runtime_config)

# 初始化外掛清單
plugins: list[BasePlugin] = []

# 如果有配置 BigQuery Analytics，則初始化外掛
if runtime_config.bigquery_analytics_dataset and runtime_config.google_cloud_project:
    bq_plugin = BigQueryAgentAnalyticsPlugin(
        project_id=runtime_config.google_cloud_project,
        dataset_id=runtime_config.bigquery_analytics_dataset,
        location=runtime_config.bigquery_location,
    )
    plugins.append(bq_plugin)

# 載入語意安全護欄插件 (SemanticGuardrailPlugin) 到全域 App 中
if runtime_config.enable_semantic_guardrails:
    from app.security.semantic_guardrail import SemanticGuardrailPlugin
    plugins.append(SemanticGuardrailPlugin(runtime_config))

app = App(root_agent=root_agent, name="app", plugins=plugins)
