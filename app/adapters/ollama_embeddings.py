import math

import httpx

from app.errors import EmbeddingProviderError


class OllamaEmbeddingAdapter:
    """Create local Nomic embeddings through Ollama's HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimension: int,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed([f"search_document: {text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"search_query: {text}"])[0]

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        try:
            response = httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": inputs, "truncate": False},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise EmbeddingProviderError("The local embedding provider is unavailable.") from error

        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(inputs):
            raise EmbeddingProviderError("The local embedding provider returned invalid data.")

        normalized: list[list[float]] = []
        for value in embeddings:
            if not isinstance(value, list) or len(value) != self._dimension:
                raise EmbeddingProviderError(
                    "The local embedding provider returned an unexpected vector dimension."
                )
            vector = [float(item) for item in value]
            magnitude = math.sqrt(sum(item * item for item in vector))
            if magnitude == 0:
                raise EmbeddingProviderError(
                    "The local embedding provider returned an empty vector."
                )
            normalized.append([item / magnitude for item in vector])
        return normalized
