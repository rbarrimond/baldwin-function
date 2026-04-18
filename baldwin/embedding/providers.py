"""Embedding provider abstractions and local runtime integrations."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Protocol
from urllib import error, request

from baldwin.exceptions import BaldwinError


DEFAULT_EMBEDDING_PROVIDER = "ollama"
DEFAULT_EMBEDDING_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_EMBEDDING_MODEL = "qllama/bge-small-en-v1.5"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_HASH_DIMENSIONS = 256
DEFAULT_HASH_MODEL = "hashing-v1"
DEFAULT_FALLBACK_PROVIDER = "hashing"
TEXT_REQUIRED_ERROR = "Text is required for embedding."


class EmbeddingProviderError(BaldwinError):
    """Raised when an embedding provider cannot produce embeddings."""


@dataclass(frozen=True)
class EmbeddingResult:
    """Provider output for a single input string."""

    vector: list[float]
    model_name: str
    dimensions: int
    provider: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EmbeddingSettings:
    """Resolved runtime settings for embedding providers."""

    provider_name: str = DEFAULT_EMBEDDING_PROVIDER
    model_name: str = DEFAULT_EMBEDDING_MODEL
    base_url: str = DEFAULT_EMBEDDING_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    hashing_dimensions: int = DEFAULT_HASH_DIMENSIONS
    fallback_provider_name: str | None = DEFAULT_FALLBACK_PROVIDER
    enable_fallback: bool = True


@dataclass(frozen=True)
class EmbeddingExecutionResult:
    """Embedding output plus fallback metadata for callers."""

    embedding: EmbeddingResult
    used_fallback: bool
    fallback_reason: str | None = None


class EmbeddingProvider(Protocol):
    """Common interface for local or remote embedding providers."""

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        """Return an embedding result for each input text."""
        raise NotImplementedError


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _tokenize(value: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", value.lower())


def _normalize_vector(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def _coerce_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean setting value: {value}")


def load_embedding_settings(overrides: Mapping[str, Any] | None = None) -> EmbeddingSettings:
    """Resolve provider settings from environment variables and optional overrides."""
    overrides = overrides or {}

    provider_name = str(
        overrides.get("provider_name")
        or os.getenv("EMBEDDING_PROVIDER")
        or DEFAULT_EMBEDDING_PROVIDER
    ).strip().lower()
    model_name = str(
        overrides.get("model_name")
        or os.getenv("EMBEDDING_MODEL")
        or os.getenv("EMAIL_VECTOR_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    ).strip()
    base_url = str(
        overrides.get("base_url")
        or os.getenv("EMBEDDING_BASE_URL")
        or DEFAULT_EMBEDDING_BASE_URL
    ).strip().rstrip("/")
    timeout_seconds = float(
        overrides.get("timeout_seconds")
        or os.getenv("EMBEDDING_TIMEOUT_SECONDS")
        or DEFAULT_TIMEOUT_SECONDS
    )
    hashing_dimensions = int(
        overrides.get("hashing_dimensions")
        or os.getenv("EMBEDDING_HASH_DIMENSIONS")
        or os.getenv("EMAIL_VECTOR_DIMENSIONS")
        or DEFAULT_HASH_DIMENSIONS
    )
    fallback_override = overrides.get("fallback_provider_name")
    fallback_provider_name = fallback_override
    if fallback_provider_name is None:
        fallback_provider_name = os.getenv("EMBEDDING_FALLBACK_PROVIDER", DEFAULT_FALLBACK_PROVIDER)
    if isinstance(fallback_provider_name, str):
        fallback_provider_name = fallback_provider_name.strip().lower() or None

    enable_fallback = _coerce_bool(
        str(overrides["enable_fallback"]) if "enable_fallback" in overrides else os.getenv("EMBEDDING_ENABLE_FALLBACK"),
        default=True,
    )

    return EmbeddingSettings(
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        hashing_dimensions=hashing_dimensions,
        fallback_provider_name=fallback_provider_name,
        enable_fallback=enable_fallback,
    )


class HashingEmbeddingProvider:
    """Deterministic feature-hashing provider for fallback and tests."""

    def __init__(
        self,
        *,
        dimensions: int = DEFAULT_HASH_DIMENSIONS,
        model_name: str = DEFAULT_HASH_MODEL,
        provider_name: str = "hashing",
    ):
        if dimensions < 8:
            raise ValueError("dimensions must be at least 8")
        if not model_name:
            raise ValueError("model_name is required")

        self.dimensions = dimensions
        self.model_name = model_name
        self.provider_name = provider_name

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        """Convert input texts into deterministic dense vectors using feature hashing."""
        if not texts:
            raise ValueError("texts are required for embedding")

        results: list[EmbeddingResult] = []
        for text in texts:
            normalized_text = _normalize_whitespace(text)
            if not normalized_text:
                raise ValueError(TEXT_REQUIRED_ERROR)

            vector = [0.0] * self.dimensions
            for token in _tokenize(normalized_text):
                digest = sha256(token.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[bucket] += sign

            vector = _normalize_vector(vector)

            results.append(
                EmbeddingResult(
                    vector=vector,
                    model_name=self.model_name,
                    dimensions=self.dimensions,
                    provider=self.provider_name,
                    metadata={"normalized_text_length": len(normalized_text)},
                )
            )

        return results


class OllamaEmbeddingProvider:
    """HTTP-backed embedding provider for a local or remote Ollama service."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_EMBEDDING_BASE_URL,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        provider_name: str = "ollama",
    ):
        if not base_url:
            raise ValueError("base_url is required")
        if not model_name:
            raise ValueError("model_name is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")

        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.provider_name = provider_name

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        """Convert input texts into embedding vectors by making requests to the Ollama API."""
        if not texts:
            raise ValueError("texts are required for embedding")

        normalized_inputs = [_normalize_whitespace(text) for text in texts]
        if any(not text for text in normalized_inputs):
            raise ValueError(TEXT_REQUIRED_ERROR)

        return [self._embed_single_text(text) for text in normalized_inputs]

    def _embed_single_text(self, text: str) -> EmbeddingResult:
        try:
            return self._request_embeddings([text])[0]
        except EmbeddingProviderError as exc:
            if not self._is_context_length_error(exc):
                raise

            chunks = self._split_text(text)
            if len(chunks) <= 1:
                raise

            chunk_embeddings = [self._embed_single_text(chunk) for chunk in chunks]
            return self._combine_chunk_embeddings(text, chunks, chunk_embeddings)

    def _request_embeddings(self, texts: list[str]) -> list[EmbeddingResult]:
        normalized_inputs = [_normalize_whitespace(text) for text in texts]
        if any(not text for text in normalized_inputs):
            raise ValueError(TEXT_REQUIRED_ERROR)

        payload = json.dumps({"model": self.model_name, "input": normalized_inputs}).encode("utf-8")
        endpoint = f"{self.base_url}/api/embed"
        http_request = request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise EmbeddingProviderError(
                f"Ollama embedding request failed with HTTP {exc.code}: {error_body}"
            ) from exc
        except error.URLError as exc:
            raise EmbeddingProviderError(f"Ollama embedding request failed: {exc.reason}") from exc
        except OSError as exc:
            raise EmbeddingProviderError(f"Ollama embedding request failed: {exc}") from exc

        try:
            response_payload = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise EmbeddingProviderError("Ollama returned an invalid JSON response.") from exc

        raw_embeddings = response_payload.get("embeddings")
        if raw_embeddings is None and "embedding" in response_payload:
            raw_embeddings = [response_payload["embedding"]]
        if not isinstance(raw_embeddings, list):
            raise EmbeddingProviderError("Ollama response did not include embeddings.")
        if len(raw_embeddings) != len(normalized_inputs):
            raise EmbeddingProviderError("Ollama returned an unexpected number of embeddings.")

        results: list[EmbeddingResult] = []
        for index, raw_embedding in enumerate(raw_embeddings):
            if not isinstance(raw_embedding, list) or not raw_embedding:
                raise EmbeddingProviderError("Ollama returned an invalid embedding vector.")
            vector = [float(value) for value in raw_embedding]
            results.append(
                EmbeddingResult(
                    vector=vector,
                    model_name=self.model_name,
                    dimensions=len(vector),
                    provider=self.provider_name,
                    metadata={
                        "base_url": self.base_url,
                        "input_text_length": len(normalized_inputs[index]),
                        "total_duration": response_payload.get("total_duration"),
                        "load_duration": response_payload.get("load_duration"),
                    },
                )
            )

        return results

    @staticmethod
    def _is_context_length_error(exc: EmbeddingProviderError) -> bool:
        return "context length" in str(exc).lower()

    @staticmethod
    def _split_text(text: str) -> list[str]:
        midpoint = len(text) // 2
        split_index = OllamaEmbeddingProvider._find_split_index(text, midpoint)
        if split_index <= 0 or split_index >= len(text):
            return [text]

        left = _normalize_whitespace(text[:split_index])
        right = _normalize_whitespace(text[split_index:])
        chunks = [chunk for chunk in (left, right) if chunk]
        return chunks or [text]

    @staticmethod
    def _find_split_index(text: str, midpoint: int) -> int:
        paragraph_break = text.rfind("\n\n", 0, midpoint)
        if paragraph_break >= 0:
            return paragraph_break + 2

        whitespace_before = text.rfind(" ", 0, midpoint)
        if whitespace_before >= 0:
            return whitespace_before + 1

        whitespace_after = text.find(" ", midpoint)
        if whitespace_after >= 0:
            return whitespace_after + 1

        return midpoint

    def _combine_chunk_embeddings(
        self,
        source_text: str,
        chunks: list[str],
        chunk_embeddings: list[EmbeddingResult],
    ) -> EmbeddingResult:
        dimensions = chunk_embeddings[0].dimensions
        if any(result.dimensions != dimensions for result in chunk_embeddings):
            raise EmbeddingProviderError("Ollama returned inconsistent chunk embedding dimensions.")

        weighted_vector = [0.0] * dimensions
        total_weight = 0.0
        for chunk, result in zip(chunks, chunk_embeddings, strict=True):
            weight = float(max(len(chunk), 1))
            total_weight += weight
            for index, value in enumerate(result.vector):
                weighted_vector[index] += value * weight

        combined_vector = _normalize_vector([value / total_weight for value in weighted_vector])
        return EmbeddingResult(
            vector=combined_vector,
            model_name=self.model_name,
            dimensions=dimensions,
            provider=self.provider_name,
            metadata={
                "base_url": self.base_url,
                "input_text_length": len(source_text),
                "chunk_count": len(chunks),
                "chunk_lengths": [len(chunk) for chunk in chunks],
                "chunking_strategy": "adaptive-halving",
            },
        )


def build_embedding_provider(settings: EmbeddingSettings) -> EmbeddingProvider:
    """Instantiate the configured provider from resolved settings."""
    if settings.provider_name == "ollama":
        return OllamaEmbeddingProvider(
            base_url=settings.base_url,
            model_name=settings.model_name,
            timeout_seconds=settings.timeout_seconds,
        )
    if settings.provider_name == "hashing":
        return HashingEmbeddingProvider(
            dimensions=settings.hashing_dimensions,
            model_name=settings.model_name or DEFAULT_HASH_MODEL,
        )
    raise ValueError(f"Unsupported embedding provider: {settings.provider_name}")


def build_fallback_provider(settings: EmbeddingSettings) -> EmbeddingProvider | None:
    """Instantiate the configured fallback provider when enabled."""
    if not settings.enable_fallback:
        return None
    if not settings.fallback_provider_name:
        return None
    if settings.fallback_provider_name == settings.provider_name:
        return None
    if settings.fallback_provider_name == "hashing":
        return HashingEmbeddingProvider(dimensions=settings.hashing_dimensions)
    raise ValueError(f"Unsupported embedding fallback provider: {settings.fallback_provider_name}")


class EmbeddingService:
    """Shared embedding runtime with optional deterministic fallback."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        fallback_provider: EmbeddingProvider | None = None,
    ):
        self.provider = provider
        self.fallback_provider = fallback_provider

    def embed_text(self, text: str) -> EmbeddingExecutionResult:
        """Embed a single input string using the primary provider or fallback."""
        try:
            embedding = self.provider.embed_texts([text])[0]
            return EmbeddingExecutionResult(embedding=embedding, used_fallback=False)
        except EmbeddingProviderError as exc:
            if self.fallback_provider is None:
                raise
            fallback_embedding = self.fallback_provider.embed_texts([text])[0]
            return EmbeddingExecutionResult(
                embedding=fallback_embedding,
                used_fallback=True,
                fallback_reason=str(exc),
            )


def build_embedding_service(settings: EmbeddingSettings) -> EmbeddingService:
    """Create the primary embedding runtime and optional fallback."""
    return EmbeddingService(
        build_embedding_provider(settings),
        fallback_provider=build_fallback_provider(settings),
    )
