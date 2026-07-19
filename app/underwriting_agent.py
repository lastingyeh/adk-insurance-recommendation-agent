from __future__ import annotations

from pathlib import Path
from google.adk.agents import Agent
from google.genai import types

from app.config import load_runtime_config
from app.tools.underwriting_tools import submit_application

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(filename: str) -> str:
    """
    載入指定檔案名稱的提示詞。
    """
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def create_underwriting_agent(config=None) -> Agent:
    """
    建立 Underwriting Agent 實例。
    """
    runtime_config = config or load_runtime_config()
    return Agent(
        name="underwriting_agent",
        model=runtime_config.model_name,
        instruction=load_prompt("underwriting_prompt.txt"),
        tools=[submit_application],
        generate_content_config=types.GenerateContentConfig(
            max_output_tokens=runtime_config.max_output_tokens,
        ),
        description="處理用戶投保意願與核保流程，評估是否滿足核保條件並進行初步自動核保審查。",
    )


# 載入執行階段配置並建立實例
runtime_config = load_runtime_config()
underwriting_agent = create_underwriting_agent(runtime_config)
