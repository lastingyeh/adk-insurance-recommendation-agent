from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

"""
app/config.py

此檔案負責管理應用程式的環境變數與執行階段配置 (Runtime Configuration)。
執行流程：
1. 透過 dotenv 載入 .env 檔案中的環境變數。
2. 定義輔助函式處理型別轉換 (字串轉布林值、字串轉列表)。
3. 定義 AppRuntimeConfig 資料類別，作為強型別的配置註冊表。
4. 提供 load_runtime_config 函式，讀取環境變數並賦予合理的預設值。
"""

# 【執行步驟】：啟動時立即從專案根目錄的 .env 檔案載入環境變數至 os.environ
load_dotenv()


def _parse_bool_env(name: str, default: bool) -> bool:
    """
    【輔助函式】：從環境變數解析布林值。
    支援 '1', 'true', 'yes', 'on' 作為 True，其餘或未設定則返回預設值。
    """
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """
    【輔助函式】：從環境變數解析以逗號分隔的字串列表 (CSV)，回傳 tuple。
    常用於解析 CORS 允許的來源網域列表。
    """
    value = os.getenv(name)
    if value is None:
        return default

    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


@dataclass(frozen=True)
class AppRuntimeConfig:
    """
    【核心資料結構】：應用程式執行階段配置。
    使用 frozen=True 確保配置在初始化後不可變更 (Immutable)，提升安全性與穩定性。
    所有應用程式元件都應依賴此物件，而非直接呼叫 os.getenv。
    """

    app_name: str  # 應用程式名稱
    api_user_id: str  # 預設 API 使用者識別碼 (單機或開發測試用)
    toolbox_server_url: str  # Toolbox (MCP) 伺服器網址
    session_db_uri: str  # Session 狀態資料庫連線字串 (如 PostgreSQL)
    memory_mode: str  # 記憶體模式
    model_name: str  # 用於一般文字/REST請求的 Gemini 模型名稱
    live_model_name: str  # 用於 WebSocket 串流對話的 Gemini 模型名稱
    fastapi_host: str  # FastAPI 伺服器綁定主機 IP
    fastapi_port: int  # FastAPI 伺服器綁定埠號
    fastapi_reload: bool  # 是否啟用 FastAPI 熱重載 (Uvicorn reload)
    cors_allow_origins: tuple[str, ...]  # CORS 允許的來源網域列表

    # 審計日誌 (Audit Log) 相關配置
    audit_enabled: bool
    audit_db_path: str
    audit_retention_days: int
    audit_hash_salt: str
    pii_redaction_enabled: bool  # 是否啟用 PII 遮蔽

    max_output_tokens: int  # LLM 最大輸出 Token 數

    # GCP 可觀測性 (Telemetry) 配置
    enable_cloud_tracing: bool
    enable_cloud_logging: bool
    otel_service_name: str

    # JWT 認證配置
    jwt_secret: str
    jwt_algorithm: str
    access_token_expire_minutes: int

    # BigQuery 分析配置
    bigquery_analytics_dataset: str | None
    bigquery_location: str
    google_cloud_project: str | None


def load_runtime_config() -> AppRuntimeConfig:
    """
    【執行步驟】：工廠函式，負責從系統環境變數中讀取值並實例化 AppRuntimeConfig。
    如果環境變數缺失，會提供適用於本地開發的預設值。
    """
    return AppRuntimeConfig(
        app_name=os.getenv("ADK_APP_NAME", "app"),
        api_user_id=os.getenv("ADK_API_USER_ID", "demo-user"),
        toolbox_server_url=os.getenv("TOOLBOX_SERVER_URL", "http://127.0.0.1:5000"),
        # 優先讀取標準 DATABASE_URL (Cloud Run 慣例)，若無則讀取 ADK_SESSION_DB_URI
        session_db_uri=os.getenv(
            "DATABASE_URL",
            os.getenv(
                "ADK_SESSION_DB_URI",
                "postgresql+asyncpg://user:password@localhost:5432/insurance",
            ),
        ),
        memory_mode=os.getenv("ADK_MEMORY_MODE", "database"),
        model_name=os.getenv("MODEL_NAME", "gemini-2.5-flash"),
        live_model_name=os.getenv(
            "LIVE_MODEL_NAME", "gemini-live-2.5-flash-preview-native-audio-09-2025"
        ),
        fastapi_host=os.getenv("FASTAPI_HOST", "127.0.0.1"),
        fastapi_port=int(os.getenv("FASTAPI_PORT", "8080")),
        fastapi_reload=_parse_bool_env("FASTAPI_RELOAD", True),
        cors_allow_origins=_parse_csv_env(
            "FASTAPI_CORS_ALLOW_ORIGINS",
            ("http://127.0.0.1:3000", "http://localhost:3000"),
        ),
        audit_enabled=_parse_bool_env("AUDIT_LOG_ENABLED", True),
        audit_db_path=os.getenv(
            "AUDIT_DB_PATH", "postgresql+asyncpg://user:password@localhost:5432/audit"
        ),
        audit_retention_days=int(os.getenv("AUDIT_RETENTION_DAYS", "365")),
        audit_hash_salt=os.getenv("AUDIT_HASH_SALT", "dev-only-change-me"),
        pii_redaction_enabled=_parse_bool_env("PII_REDACTION_ENABLED", True),
        max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "2048")),
        enable_cloud_tracing=_parse_bool_env("ENABLE_CLOUD_TRACING", False),
        enable_cloud_logging=_parse_bool_env("ENABLE_CLOUD_LOGGING", False),
        otel_service_name=os.getenv(
            "OTEL_SERVICE_NAME", "insurance-recommendation-agent"
        ),
        jwt_secret=os.getenv("JWT_SECRET", "super-secret-change-me"),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        access_token_expire_minutes=int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
        ),
        bigquery_analytics_dataset=os.getenv("BQ_ANALYTICS_DATASET"),
        bigquery_location=os.getenv("BQ_LOCATION", "US"),
        google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    )


# 匯出配置類別與載入函式供其他模組使用
__all__ = ["AppRuntimeConfig", "load_runtime_config"]
