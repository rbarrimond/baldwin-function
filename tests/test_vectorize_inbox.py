"""Unit tests for vectorize_inbox script helpers."""

import unittest

from scripts.vectorize_inbox import _format_chunking_status


class VectorizeInboxLoggingTests(unittest.TestCase):
    """Coverage for script-level chunking log formatting."""

    def test_format_chunking_status_ignores_non_chunked_metadata(self) -> None:
        """Single-chunk or missing metadata should not emit a chunking log line."""
        self.assertIsNone(_format_chunking_status({}))
        self.assertIsNone(_format_chunking_status({"chunk_count": 1}))

    def test_format_chunking_status_includes_count_and_max_length(self) -> None:
        """Chunked metadata should surface the chunk count and largest chunk size."""
        status = _format_chunking_status({"chunk_count": 3, "chunk_lengths": [120, 80, 95]})

        self.assertEqual(status, "chunked=3 max_chunk_length=120")


if __name__ == "__main__":
    unittest.main()