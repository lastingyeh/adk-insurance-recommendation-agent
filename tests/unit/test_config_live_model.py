"""
Live 模型自動分流的回歸測試。

涵蓋 app/config.py 依 GOOGLE_GENAI_USE_VERTEXAI 自動挑選 Live 模型名稱的邏輯，
以及容器化環境常見的「空字串注入」陷阱（LIVE_MODEL_NAME=""）。
模型名稱或 env 注入方式日後再改時，這些測試會擋下無聲退化。
"""

import pytest

from app.config import (
    _LIVE_MODEL_DEVELOPER,
    _LIVE_MODEL_VERTEX,
    _default_live_model,
    load_runtime_config,
)


@pytest.fixture(autouse=True)
def _clean_live_env(monkeypatch):
    """每個測試開始前清掉相關 env，確保彼此獨立。"""
    monkeypatch.delenv("LIVE_MODEL_NAME", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)


@pytest.mark.parametrize(
    "use_vertex_value, expected",
    [
        ("1", _LIVE_MODEL_VERTEX),
        ("true", _LIVE_MODEL_VERTEX),
        ("0", _LIVE_MODEL_DEVELOPER),
        ("false", _LIVE_MODEL_DEVELOPER),
    ],
)
def test_default_live_model_follows_backend(monkeypatch, use_vertex_value, expected):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", use_vertex_value)
    assert _default_live_model() == expected


def test_default_live_model_unset_defaults_to_developer():
    # 未設定 USE_VERTEXAI 時，與 google-genai 預設一致，走 Developer API（key）。
    assert _default_live_model() == _LIVE_MODEL_DEVELOPER


def test_load_config_vertex_mode_uses_vertex_name(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    assert load_runtime_config().live_model_name == _LIVE_MODEL_VERTEX


def test_load_config_key_mode_uses_developer_name(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "0")
    assert load_runtime_config().live_model_name == _LIVE_MODEL_DEVELOPER


def test_explicit_override_wins_over_autoselect(monkeypatch):
    # 明確設定的 LIVE_MODEL_NAME 不論後端都應原封不動採用。
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    monkeypatch.setenv("LIVE_MODEL_NAME", "my-custom-live-model")
    assert load_runtime_config().live_model_name == "my-custom-live-model"


def test_empty_string_falls_back_to_autoselect(monkeypatch):
    # 容器化常見陷阱：LIVE_MODEL_NAME="" 應被視為「未設定」並走自動分流，
    # 而非保留空字串（這正是 docker-compose 注入空值時要避免的退化）。
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "0")
    monkeypatch.setenv("LIVE_MODEL_NAME", "")
    assert load_runtime_config().live_model_name == _LIVE_MODEL_DEVELOPER
