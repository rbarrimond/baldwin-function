"""Unit tests for embedding providers and runtime wiring."""

import io
import json
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib import error

from baldwin.embedding import (
    EmbeddingProviderError,
    EmbeddingResult,
    HashingEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
    build_embedding_service,
    load_embedding_settings,
)
from baldwin.exceptions import BaldwinConfigurationError


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

    def test_load_embedding_settings_wraps_invalid_boolean_setting(self) -> None:
        """Invalid boolean configuration should be wrapped in a Baldwin configuration error."""
        with self.assertRaises(BaldwinConfigurationError):
            load_embedding_settings({"enable_fallback": "not-a-bool"})

    def test_load_embedding_settings_wraps_invalid_numeric_settings(self) -> None:
        """Invalid numeric settings should be wrapped with configuration context."""
        with self.assertRaises(BaldwinConfigurationError):
            load_embedding_settings({"timeout_seconds": "fast"})

        with self.assertRaises(BaldwinConfigurationError):
            load_embedding_settings({"hashing_dimensions": "wide"})

    def test_build_embedding_provider_wraps_unsupported_provider(self) -> None:
        """Unsupported provider names should raise a Baldwin configuration error."""
        with self.assertRaises(BaldwinConfigurationError):
            build_embedding_provider(load_embedding_settings({"provider_name": "unknown"}))


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

    def test_hashing_provider_wraps_validation_failures(self) -> None:
        """Hashing provider validation should raise EmbeddingProviderError."""
        with self.assertRaises(EmbeddingProviderError):
            HashingEmbeddingProvider(dimensions=4)

        provider = HashingEmbeddingProvider(dimensions=32, model_name="hashing-v1")
        with self.assertRaises(EmbeddingProviderError):
            provider.embed_texts([])

        with self.assertRaises(EmbeddingProviderError):
            provider.embed_texts(["   "])


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

    @patch("baldwin.embedding.providers.request.urlopen")
    def test_ollama_provider_chunks_context_overflow_inputs(self, urlopen: MagicMock) -> None:
        """Oversized inputs should be split and recombined before falling back."""

        def fake_urlopen(http_request, timeout):  # type: ignore[no-untyped-def]
            del timeout
            payload = json.loads(http_request.data.decode("utf-8"))
            text = payload["input"][0]
            if len(text) > 40:
                raise error.HTTPError(
                    http_request.full_url,
                    400,
                    "Bad Request",
                    hdrs=Message(),
                    fp=io.BytesIO(b'{"error":"the input length exceeds the context length"}'),
                )

            response = MagicMock()
            response.read.return_value = json.dumps(
                {
                    "embeddings": [[float(len(text)), 1.0]],
                    "total_duration": 10,
                    "load_duration": 5,
                }
            ).encode("utf-8")
            context_manager = MagicMock()
            context_manager.__enter__.return_value = response
            context_manager.__exit__.return_value = False
            return context_manager

        urlopen.side_effect = fake_urlopen
        provider = OllamaEmbeddingProvider(
            base_url="http://localhost:11434",
            model_name="qllama/bge-small-en-v1.5",
            timeout_seconds=10,
        )

        result = provider.embed_texts(["chunk " * 20])[0]

        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model_name, "qllama/bge-small-en-v1.5")
        self.assertEqual(result.dimensions, 2)
        self.assertGreater(result.metadata["chunk_count"], 1)
        self.assertEqual(result.metadata["chunking_strategy"], "adaptive-halving")
        self.assertAlmostEqual(sum(value * value for value in result.vector), 1.0, places=6)
        self.assertGreater(urlopen.call_count, 1)

    @patch("baldwin.embedding.providers.request.urlopen")
    def test_ollama_provider_wraps_non_numeric_embedding_values(self, urlopen: MagicMock) -> None:
        """Non-numeric embedding payload values should be translated into EmbeddingProviderError."""
        response = MagicMock()
        response.read.return_value = json.dumps({"embeddings": [["abc", 1.0]]}).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        provider = OllamaEmbeddingProvider()

        with self.assertRaises(EmbeddingProviderError) as context:
            provider.embed_texts(["hello world"])

        self.assertIn("input index 0", str(context.exception))
        self.assertIsInstance(context.exception.__cause__, ValueError)


class EmbeddingServiceTests(unittest.TestCase):
    """Coverage for shared provider fallback behavior."""

    def test_service_falls_back_to_hashing_when_primary_fails(self) -> None:
        """The embedding service should return the fallback embedding explicitly."""

        class FailingProvider:
            def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
                del texts
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
