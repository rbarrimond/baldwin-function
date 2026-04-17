"""Unit tests for embedding providers and runtime wiring."""

import json
import unittest
from unittest.mock import MagicMock, patch

from baldwin.embedding import (
    EmbeddingProviderError,
    HashingEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
    build_embedding_service,
    load_embedding_settings,
)


class EmbeddingSettingsTests(unittest.TestCase):
    """Coverage for environment-driven provider selection."""

    @patch.dict(
        "os.environ",
        {
            "EMBEDDING_PROVIDER": "ollama",
            "EMBEDDING_MODEL": "qllama/bge-small-en-v1.5",
            "EMBEDDING_BASE_URL": "http://localhost:11434",
            "EMBEDDING_TIMEOUT_SECONDS": "12",
            "EMBEDDING_HASH_DIMENSIONS": "384",
            "EMBEDDING_FALLBACK_PROVIDER": "hashing",
        },
        clear=True,
    )
    def test_load_embedding_settings_prefers_new_environment_contract(self) -> None:
        """The provider settings should resolve from the new embedding env vars."""
        settings = load_embedding_settings()

        self.assertEqual(settings.provider_name, "ollama")
        self.assertEqual(settings.model_name, "qllama/bge-small-en-v1.5")
        self.assertEqual(settings.base_url, "http://localhost:11434")
        self.assertEqual(settings.timeout_seconds, 12.0)
        self.assertEqual(settings.hashing_dimensions, 384)
        self.assertEqual(settings.fallback_provider_name, "hashing")

    def test_build_embedding_provider_resolves_ollama_and_hashing(self) -> None:
        """The provider factory should resolve the supported provider identifiers."""
        ollama_provider = build_embedding_provider(
            load_embedding_settings({"provider_name": "ollama", "model_name": "qllama/bge-small-en-v1.5"})
        )
        hashing_provider = build_embedding_provider(
            load_embedding_settings({"provider_name": "hashing", "model_name": "hashing-v1"})
        )

        self.assertIsInstance(ollama_provider, OllamaEmbeddingProvider)
        self.assertIsInstance(hashing_provider, HashingEmbeddingProvider)


class HashingEmbeddingProviderTests(unittest.TestCase):
    """Behavioral tests for deterministic hashing embeddings."""

    def test_hashing_provider_is_deterministic_and_normalized(self) -> None:
        """Repeated calls for the same text should produce the same normalized vector."""
        provider = HashingEmbeddingProvider(dimensions=32, model_name="hashing-v1")

        first = provider.embed_texts(["Alpha beta beta"])[0]
        second = provider.embed_texts(["Alpha beta beta"])[0]

        self.assertEqual(first.vector, second.vector)
        self.assertEqual(first.provider, "hashing")
        self.assertEqual(first.model_name, "hashing-v1")
        self.assertEqual(first.dimensions, 32)
        self.assertAlmostEqual(sum(value * value for value in first.vector), 1.0, places=6)


class OllamaEmbeddingProviderTests(unittest.TestCase):
    """Coverage for Ollama HTTP response handling."""

    @patch("baldwin.embedding.providers.request.urlopen")
    def test_ollama_provider_maps_embed_response(self, urlopen: MagicMock) -> None:
        """The provider should parse vectors and metadata from the Ollama API."""
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "embeddings": [[0.1, 0.2, 0.3]],
                "total_duration": 10,
                "load_duration": 5,
            }
        ).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        provider = OllamaEmbeddingProvider(
            base_url="http://localhost:11434",
            model_name="qllama/bge-small-en-v1.5",
            timeout_seconds=10,
        )
        result = provider.embed_texts(["hello world"])[0]

        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model_name, "qllama/bge-small-en-v1.5")
        self.assertEqual(result.dimensions, 3)
        self.assertEqual(result.vector, [0.1, 0.2, 0.3])
        self.assertEqual(result.metadata["base_url"], "http://localhost:11434")

    @patch("baldwin.embedding.providers.request.urlopen")
    def test_ollama_provider_wraps_transport_errors(self, urlopen: MagicMock) -> None:
        """Transport failures should raise the shared provider error type."""
        urlopen.side_effect = OSError("connection refused")
        provider = OllamaEmbeddingProvider()

        with self.assertRaises(EmbeddingProviderError):
            provider.embed_texts(["hello"])


class EmbeddingServiceTests(unittest.TestCase):
    """Coverage for shared provider fallback behavior."""

    def test_service_falls_back_to_hashing_when_primary_fails(self) -> None:
        """The embedding service should return the fallback embedding explicitly."""

        class FailingProvider:
            def embed_texts(self, texts: list[str]) -> list[object]:
                raise EmbeddingProviderError("primary failed")

        service = build_embedding_service(
            load_embedding_settings(
                {
                    "provider_name": "hashing",
                    "model_name": "hashing-v1",
                    "enable_fallback": False,
                }
            )
        )
        service = service.__class__(FailingProvider(), fallback_provider=HashingEmbeddingProvider())

        result = service.embed_text("hello world")

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.embedding.provider, "hashing")
        self.assertEqual(result.fallback_reason, "primary failed")


if __name__ == "__main__":
    unittest.main()
