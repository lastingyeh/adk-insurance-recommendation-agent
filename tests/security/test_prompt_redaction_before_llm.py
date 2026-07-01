"""驗證使用者 prompt 在送進 LLM (Gemini/ADK) 之前已完成 PII 去敏。

問題背景：
原本 `build_user_message_content()` 直接把使用者原始 prompt 包成
`genai_types.Content` 交給 ADK Runner，等於把含有電話 / email / 身分證 /
信用卡的明文 PII 原封不動外送給第三方 LLM。稽核去敏（寫 DB 時）與
公開狀態過濾（回前端時）都發生在「送出之後」，救不了這條外送路徑。

這些測試鎖定真正送往 LLM 的那個 Content 物件，確保其中不再含有明文 PII。
"""

from app.services.agent_run_service import build_user_message_content


def _first_text(content) -> str:
    return content.parts[0].text


def test_prompt_phone_redacted_before_llm():
    content = build_user_message_content("我的手機是 0912-345-678，想保醫療險")

    text = _first_text(content)
    assert "0912-345-678" not in text
    assert "[REDACTED_PHONE]" in text


def test_prompt_email_and_id_redacted_before_llm():
    content = build_user_message_content(
        "email 是 chris@example.com，身分證 A123456789"
    )

    text = _first_text(content)
    assert "chris@example.com" not in text
    assert "A123456789" not in text
    assert "[REDACTED_EMAIL]" in text
    assert "[REDACTED_TW_ID]" in text


def test_clean_prompt_unchanged():
    # 沒有 PII 的內容不應被改動，避免破壞正常對話。
    prompt = "我 35 歲，預算三萬，想規劃醫療險"
    content = build_user_message_content(prompt)

    assert _first_text(content) == prompt
