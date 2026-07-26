from __future__ import annotations

import random
from typing import Any
from google.adk.tools.tool_context import ToolContext

"""
本模組定義了與投保及核保（Underwriting）相關的工具函式。
這些工具讓核保代理人（Underwriting Agent）能夠在對話過程中送出投保申請案並獲得核保評估結果。
"""


def submit_application(
    product_name: str,
    age: int,
    occupation: str,
    health_status: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """
    提交投保申請，並進行初步自動核保評估。

    參數:
        product_name: 用戶選擇投保的商品名稱。
        age: 被保險人年齡。
        occupation: 被保險人職業。
        health_status: 被保險人健康告知狀況。
        tool_context: ADK 工具上下文（選填）。

    返回:
        包含處理狀態、申請案 ID、初步核保結果與詳細訊息的字典。
    """
    # 建立隨機的申請案號
    application_id = f"APP-{random.randint(100000, 999999)}"

    # 進行基本的自動核保邏輯評估
    health_lower = health_status.lower()
    occupation_lower = occupation.lower()

    # 高風險職業清單
    high_risk_occupations = [
        "消防員",
        "消防",
        "警察",
        "特技",
        "高空",
        "礦工",
        "軍人",
        "潛水",
        "firefighter",
        "police",
        "military",
        "miner",
        "stuntman",
        "diver",
    ]
    is_high_risk_job = any(job in occupation_lower for job in high_risk_occupations)

    # 嚴重病史關鍵字
    critical_illnesses = [
        "癌症",
        "心臟",
        "中風",
        "糖尿病",
        "高血壓",
        "洗腎",
        "慢性",
        "cancer",
        "heart",
        "stroke",
        "diabetes",
        "hypertension",
        "chronic",
    ]
    has_critical_illness = any(ill in health_lower for ill in critical_illnesses)

    if age > 75:
        result = "declined"
        message = "因被保險人年齡超過最高投保限制 (75歲)，無法受理此投保申請。"
    elif age > 65 or has_critical_illness or is_high_risk_job:
        result = "referred"
        message = (
            f"申請案 {application_id} 已建立，但因年齡較高、職業風險或健康告知異常，"
            f"需要轉交人工核保照會評估。後續將由核保專員與您聯繫。"
        )
    else:
        result = "approved"
        message = f"核保通過！申請案 {application_id} 初步核保已核准。已為您成功建立投保記錄。"

    # 如果提供 tool_context，則將核保結果與申請案 ID 寫入 Session State
    if tool_context is not None:
        tool_context.state["user:last_application_id"] = application_id
        tool_context.state["user:last_underwriting_result"] = result

    return {
        "status": "ok",
        "application_id": application_id,
        "underwriting_result": result,
        "message": message,
    }
