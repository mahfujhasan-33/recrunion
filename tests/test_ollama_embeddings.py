from typing import Any

import httpx
import pytest

from app.adapters import ollama_embeddings
from app.adapters.embeddings import EmbeddingAdapter
from app.adapters.ollama_embeddings import OllamaEmbeddingAdapter
from app.errors import EmbeddingProviderError


def test_ollama_adapter_uses_nomic_prefixes_and_validates_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        requests.append(kwargs["json"])
        request = httpx.Request("POST", args[0])
        count = len(kwargs["json"]["input"])
        return httpx.Response(
            200,
            request=request,
            json={"embeddings": [[1.0] + [0.0] * 767 for _ in range(count)]},
        )

    monkeypatch.setattr(ollama_embeddings.httpx, "post", fake_post)
    adapter = OllamaEmbeddingAdapter(
        base_url="http://ollama:11434",
        model="nomic-embed-text",
        dimension=768,
        timeout_seconds=10,
    )

    documents = adapter.embed_documents(["policy text"])
    query = adapter.embed_query("job requirements")

    assert isinstance(adapter, EmbeddingAdapter)
    assert len(documents[0]) == 768
    assert len(query) == 768
    assert requests[0]["input"] == ["search_document: policy text"]
    assert requests[1]["input"] == ["search_query: job requirements"]


def test_ollama_adapter_rejects_unexpected_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("POST", args[0]),
            json={"embeddings": [[1.0, 2.0]]},
        )

    monkeypatch.setattr(ollama_embeddings.httpx, "post", fake_post)
    adapter = OllamaEmbeddingAdapter(
        base_url="http://ollama:11434",
        model="nomic-embed-text",
        dimension=768,
        timeout_seconds=10,
    )

    with pytest.raises(EmbeddingProviderError):
        adapter.embed_query("query")
