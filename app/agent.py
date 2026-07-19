from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH
from google.adk.apps import App
from google.adk.tools.toolbox_toolset import ToolboxToolset
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin,
)
from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.tools import AgentTool
from google.genai import types
from toolbox_core.protocol import Protocol

from app.config import load_runtime_config
from app.tools.session_tools import (
    clear_last_recommendation,
    get_user_profile_snapshot,
    save_last_recommendation,
    save_user_profile,
)

# 定義提示詞目錄與檔案路徑
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(filename: str) -> str:
    """
    載入指定檔案名稱的提示詞。
    """
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def load_agent_prompt() -> str:
    """
    載入主代理人的核心系統提示詞。
    """
    return load_prompt("supervisor_prompt.txt")


class AgentFactory:
    """
    代理人配置工廠類別，負責初始化 Supervisor 路由器與各專業子代理人（Recommendation/FAQ/Claim）。
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
        MCP 伺服器提供保險商品檢索與 FAQ 知識查詢等業務功能。
        """
        return ToolboxToolset(
            server_url=self._config.toolbox_server_url,
            protocol=Protocol.MCP_LATEST,
        )

    def create_recommendation_agent(self, model_name: str | None = None) -> Agent:
        """
        建立專職保險推薦與商品選配的 RecommendationAgent。
        """
        tools = [
            save_last_recommendation,
            clear_last_recommendation,
            self.create_toolbox(),
        ]

        # 載入保險專用專家知識 Skill
        try:
            skill_dir = (
                Path(__file__).resolve().parent / "skills" / "insurance-agent-skill"
            )
            if skill_dir.exists():
                insurance_skill = load_skill_from_dir(skill_dir)
                skill_toolset = SkillToolset(skills=[insurance_skill])
                tools.append(skill_toolset)
        except Exception:
            pass

        return Agent(
            name="recommendation_agent",
            model=model_name or self._config.model_name,
            instruction=load_prompt("recommendation_prompt.txt"),
            tools=tools,
            generate_content_config=types.GenerateContentConfig(
                max_output_tokens=self._config.max_output_tokens,
            ),
            description="用於處理保險商品推薦、方案選配、預算比對、商品追問與比較的專業推薦代理人。",
        )

    def create_faq_agent(self, model_name: str | None = None) -> Agent:
        """
        建立專職回答通用保險 FAQ 與名詞條款定義的 FAQAgent。
        """
        return Agent(
            name="faq_agent",
            model=model_name or self._config.model_name,
            instruction=load_prompt("faq_prompt.txt"),
            tools=[self.create_toolbox()],
            generate_content_config=types.GenerateContentConfig(
                max_output_tokens=self._config.max_output_tokens,
            ),
            description="用於回答保險常識、名詞定義、通用條款釋義與保險 FAQ 的解答代理人。",
        )

    def create_claim_agent(self, model_name: str | None = None) -> Agent:
        """
        建立專職引導理賠、說明所需文件與流程的 ClaimAgent。
        """
        return Agent(
            name="claim_agent",
            model=model_name or self._config.model_name,
            instruction=load_prompt("claim_prompt.txt"),
            tools=[],
            generate_content_config=types.GenerateContentConfig(
                max_output_tokens=self._config.max_output_tokens,
            ),
            description="用於解答理賠流程、所需文件、申請步驟並提供情感共鳴的理賠導航代理人。",
        )

    def create(self, model_name: str | None = None) -> Agent:
        """
        建立並回傳 Supervisor Agent 實例 (作為根路由器)，其以 A2A 形式調度各專業子代理人。
        """
        rec_agent = self.create_recommendation_agent(model_name)
        faq_agent = self.create_faq_agent(model_name)
        claim_agent = self.create_claim_agent(model_name)

        # 建立遠端 Underwriting Agent 代理實例 (A2A 協定)
        remote_underwriting = RemoteA2aAgent(
            name="underwriting_agent",
            description="負責核保評估與保單進件。當用戶表示要購買、投保、進行核保時，必須調用此代理人。",
            agent_card=f"{self._config.underwriting_service_url.rstrip('/')}{AGENT_CARD_WELL_KNOWN_PATH}",
        )

        supervisor_tools = [
            get_user_profile_snapshot,
            save_user_profile,
            AgentTool(agent=rec_agent),
            AgentTool(agent=faq_agent),
            AgentTool(agent=claim_agent),
            AgentTool(agent=remote_underwriting),
        ]

        return Agent(
            name=self._config.app_name,
            model=model_name or self._config.model_name,
            instruction=load_prompt("supervisor_prompt.txt"),
            tools=supervisor_tools,
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
