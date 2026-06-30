from __future__ import annotations

from dataclasses import dataclass

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.database_session_service import DatabaseSessionService

from app.agent import AgentFactory
from app.config import AppRuntimeConfig, load_runtime_config
from app.services.agent_run_service import AgentRunService
from app.services.live_agent_service import LiveAgentService
from app.services.readiness_service import ReadinessService
from app.services.session_service import SessionService
from app.services.audit_log_service import AuditLogService
from app.services.user_service import UserService

"""
app/container.py

此模組實作了依賴注入容器 (Dependency Injection Container)。
負責在應用程式啟動時，將配置 (Config)、外部服務連線 (DB)、
Google ADK 核心 (Agent, Runner) 以及業務邏輯層 (Services) 組裝在一起，
確保各元件之間的低耦合，並方便進行單元測試與替換實作。
"""


def create_session_store(config: AppRuntimeConfig) -> BaseSessionService:
    """
    【元件工廠】：根據配置建立 ADK 所需的對話狀態儲存服務。
    支援讀取 ADK_MEMORY_MODE 環境變數，當設定為 'in_memory' 時使用 InMemorySessionService，
    否則預設使用 DatabaseSessionService 進行 PostgreSQL 持久化儲存。
    """
    if config.memory_mode == "in_memory":
        from google.adk.sessions import InMemorySessionService

        return InMemorySessionService()
    return DatabaseSessionService(db_url=config.session_db_uri)


def create_runner(
    config: AppRuntimeConfig,
    agent: Agent,
    session_store: BaseSessionService,
) -> Runner:
    """
    【元件工廠】：建立 Google ADK Runner 實例。
    Runner 是 ADK 的執行引擎，負責協調 Agent (大腦) 與 Session Store (記憶)。
    它處理歷史對話的載入、呼叫 LLM、執行工具，並將結果存回 Session。
    """
    return Runner(
        app_name=config.app_name,
        agent=agent,
        session_service=session_store,
    )


@dataclass(frozen=True)
class AppContainer:
    """
    【核心容器】：應用程式依賴注入容器。
    這是一個不可變的資料類別，集中持有應用程式運作所需的所有單例 (Singleton) 服務。
    API 路由層 (Routes) 會透過 FastAPI 的 Depends 機制獲取此容器。
    """

    config: AppRuntimeConfig  # 執行階段配置
    agent: Agent  # 標準代理人 (用於文字/REST API)
    session_store: BaseSessionService  # 底層 Session 儲存機制
    runner: Runner  # 標準執行器
    sessions: SessionService  # 高階 Session 管理服務 (包含業務邏輯)
    users: UserService  # 使用者管理與認證服務
    agent_runs: AgentRunService  # 代理人執行任務服務 (處理 SSE 串流與事件封裝)
    live_agent: LiveAgentService  # 串流代理人服務 (處理 WebSocket 與語音串流)
    readiness: ReadinessService  # 系統健康狀態與連線檢查服務
    audit_logs: AuditLogService  # 審計日誌與 PII 處理服務


def build_app_container(config: AppRuntimeConfig | None = None) -> AppContainer:
    """
    【容器建構器】：組裝並初始化 AppContainer。
    這是依賴注入的進入點，負責建立元件間的依賴圖 (Dependency Graph)。
    """
    # 1. 載入配置
    runtime_config = config or load_runtime_config()

    # 2. 建立 Agent 工廠
    agent_factory = AgentFactory(runtime_config)

    # 3. 建立標準環境 (文字對話 / SSE)
    # 使用一般模型 (如 gemini-2.5-flash) 建立 Agent
    agent = agent_factory.create(model_name=runtime_config.model_name)
    session_store = create_session_store(runtime_config)
    runner = create_runner(runtime_config, agent, session_store)

    # 4. 建立 Live 環境 (語音串流 / WebSocket)
    # 使用支援多模態 Live API 的模型 (如 gemini-live-2.5-flash-preview)
    live_agent_instance = agent_factory.create(
        model_name=runtime_config.live_model_name
    )
    # 為了支援併發，Live 環境通常會有自己獨立的 Runner 實例
    live_runner = create_runner(runtime_config, live_agent_instance, session_store)

    # 5. 實例化業務邏輯服務層 (Service Layer)
    sessions = SessionService(session_store, runtime_config)
    users = UserService(runtime_config.session_db_uri)
    audit_logs = AuditLogService(
        db_url=runtime_config.audit_db_path,
        hash_salt=runtime_config.audit_hash_salt,
        retention_days=runtime_config.audit_retention_days,
        enabled=runtime_config.audit_enabled,
    )

    # 6. 回傳完整組裝好的容器
    return AppContainer(
        config=runtime_config,
        agent=agent,
        session_store=session_store,
        runner=runner,
        sessions=sessions,
        users=users,
        # AgentRunService 封裝了標準 Runner，並整合了 Session 管理與 Audit Log
        agent_runs=AgentRunService(runner, sessions, runtime_config, audit_logs),
        # LiveAgentService 封裝了 Live Runner，專注於處理雙向串流協定
        live_agent=LiveAgentService(live_runner, sessions, runtime_config),
        # ReadinessService 提供 /readyz 檢查端點所需的邏輯
        readiness=ReadinessService(session_store, runtime_config),
        audit_logs=audit_logs,
    )
