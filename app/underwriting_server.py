from __future__ import annotations

from urllib.parse import urlparse
from google.adk.a2a.utils.agent_to_a2a import to_a2a

from app.config import load_runtime_config
from app.underwriting_agent import underwriting_agent

if __name__ == "__main__":
    # 載入配置，自動解析 A2A 服務的連接埠
    runtime_config = load_runtime_config()
    parsed_url = urlparse(runtime_config.underwriting_service_url)
    port = parsed_url.port or 8001

    print(f"啟動核保 A2A 代理人伺服器，監聽網址: {runtime_config.underwriting_service_url}")
    print(f"正在廣播 A2A 代理人資訊...")

    # 啟動外部核保 Agent 服務 (A2A 協定)
    to_a2a(underwriting_agent, port=port)
