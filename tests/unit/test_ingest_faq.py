"""
FAQ ingestion 腳本的單元測試。

聚焦不需網路/DB 的純邏輯：後端模式判定、依模式的憑證檢查，
以及 get_embeddings 的批次切割（以假的 genai client 取代真實 API）。
這層保護的是這次改動的核心：移除硬性 project 檢查、改走 google-genai 自動分流。
"""

import asyncio

import pytest

import scripts.ingest_faq_embeddings as ingest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)


@pytest.mark.parametrize(
    "value, expected",
    [("1", True), ("true", True), ("on", True), ("0", False), ("false", False)],
)
def test_is_vertex_mode(monkeypatch, value, expected):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", value)
    assert ingest._is_vertex_mode() is expected


def test_is_vertex_mode_unset_defaults_false():
    assert ingest._is_vertex_mode() is False


def test_vertex_mode_requires_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    err = ingest._check_credentials()
    assert err is not None and "GOOGLE_CLOUD_PROJECT" in err


def test_vertex_mode_with_project_ok(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    assert ingest._check_credentials() is None


def test_key_mode_requires_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "0")
    err = ingest._check_credentials()
    assert err is not None and "GOOGLE_API_KEY" in err


def test_key_mode_with_key_ok(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test")
    assert ingest._check_credentials() is None


def test_key_mode_does_not_require_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test")
    assert ingest._check_credentials() is None


class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeResp:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeModels:
    def __init__(self, recorder):
        self._rec = recorder

    async def embed_content(self, model, contents, config):
        batch = list(contents)
        self._rec.append({"model": model, "contents": batch, "config": config})
        return _FakeResp([_FakeEmbedding([float(len(c))]) for c in batch])


class _FakeAio:
    def __init__(self, recorder):
        self.models = _FakeModels(recorder)


class _FakeClient:
    def __init__(self, recorder):
        self.aio = _FakeAio(recorder)


def test_get_embeddings_batches_and_preserves_order(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest.genai, "Client", lambda *a, **k: _FakeClient(calls))
    monkeypatch.setattr(ingest, "EMBED_BATCH_SIZE", 2)

    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    result = asyncio.run(ingest.get_embeddings(texts))

    assert [len(c["contents"]) for c in calls] == [2, 2, 1]
    assert result == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert calls[0]["config"].output_dimensionality == ingest.EMBED_DIM
    assert calls[0]["model"] == ingest.MODEL_NAME


def test_get_embeddings_empty_input(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest.genai, "Client", lambda *a, **k: _FakeClient(calls))
    result = asyncio.run(ingest.get_embeddings([]))
    assert result == []
    assert calls == []
