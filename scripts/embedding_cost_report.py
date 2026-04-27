"""Generate a repeatable embedding cost summary from persisted PostgreSQL data."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import psycopg


AZURE_OPENAI_PRICING_URL = "https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/"
AZURE_OPENAI_PRICING_REFERENCE_DATE = "2026-04-27"
DEFAULT_CHARS_PER_TOKEN = 4.0

AZURE_OPENAI_EMBEDDING_PRICES_PER_1K_TOKENS = {
    "ada": 0.00011,
    "text-embedding-3-large": 0.000143,
    "text-embedding-3-small": 0.000022,
}


class EmbeddingCostReportError(Exception):
    """Raised when the embedding cost report cannot be generated."""


@dataclass(frozen=True)
class CostReportRow:
    """Aggregated persisted embedding input data for one provider/model space."""

    source_type: str
    provider: str
    model_name: str
    embedding_rows: int
    unique_documents: int
    total_input_characters: int
    average_input_characters: float
    min_input_characters: int
    max_input_characters: int
    first_embedded_at: str | None
    last_embedded_at: str | None


@dataclass(frozen=True)
class PricingContext:
    """Optional hosted-pricing context used to estimate token-priced cost."""

    label: str
    price_per_1k_tokens_usd: float
    source: str
    reference_date: str | None = None
    pricing_url: str | None = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        dest="database_url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL connection string. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--source-type",
        dest="source_type",
        default="email",
        help="Optional source_type filter. Defaults to email.",
    )
    parser.add_argument(
        "--provider",
        dest="provider",
        help="Optional embedding provider filter, such as ollama or hashing.",
    )
    parser.add_argument(
        "--model-name",
        dest="model_name",
        help="Optional persisted embedding model filter.",
    )
    parser.add_argument(
        "--chars-per-token",
        dest="chars_per_token",
        type=float,
        default=DEFAULT_CHARS_PER_TOKEN,
        help="Heuristic characters-per-token ratio used for cost estimates. Defaults to 4.0.",
    )
    parser.add_argument(
        "--azure-openai-model",
        dest="azure_openai_model",
        choices=sorted(AZURE_OPENAI_EMBEDDING_PRICES_PER_1K_TOKENS),
        help="Optional Azure OpenAI embedding model preset for hosted cost comparison.",
    )
    parser.add_argument(
        "--price-per-1k-tokens",
        dest="price_per_1k_tokens",
        type=float,
        help="Optional explicit token price in USD per 1K tokens. Overrides presets when supplied.",
    )
    return parser


def _coerce_chars_per_token(value: float) -> float:
    if value <= 0:
        raise EmbeddingCostReportError("chars-per-token must be greater than 0.")
    return value


def resolve_pricing_context(args: argparse.Namespace) -> PricingContext | None:
    if args.price_per_1k_tokens is not None:
        if args.price_per_1k_tokens < 0:
            raise EmbeddingCostReportError("price-per-1k-tokens cannot be negative.")
        label = args.azure_openai_model or "custom"
        return PricingContext(
            label=label,
            price_per_1k_tokens_usd=args.price_per_1k_tokens,
            source="explicit-argument",
        )

    if args.azure_openai_model is None:
        return None

    return PricingContext(
        label=args.azure_openai_model,
        price_per_1k_tokens_usd=AZURE_OPENAI_EMBEDDING_PRICES_PER_1K_TOKENS[args.azure_openai_model],
        source="azure-openai-pricing-page",
        reference_date=AZURE_OPENAI_PRICING_REFERENCE_DATE,
        pricing_url=AZURE_OPENAI_PRICING_URL,
    )


def _estimate_tokens(total_input_characters: int, chars_per_token: float) -> int:
    if total_input_characters <= 0:
        return 0
    return int(math.ceil(total_input_characters / chars_per_token))


def _estimate_cost_usd(estimated_tokens: int, pricing: PricingContext | None) -> float | None:
    if pricing is None:
        return None
    return estimated_tokens / 1000.0 * pricing.price_per_1k_tokens_usd


def _build_where_clause(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    clauses: list[str] = []
    params: dict[str, object] = {}

    if args.source_type:
        clauses.append("d.source_type = %(source_type)s")
        params["source_type"] = args.source_type
    if args.provider:
        clauses.append("e.provider = %(provider)s")
        params["provider"] = args.provider
    if args.model_name:
        clauses.append("e.model_name = %(model_name)s")
        params["model_name"] = args.model_name

    if not clauses:
        return "", params

    return "WHERE " + " AND ".join(clauses), params


def _fetch_report_rows(database_url: str, args: argparse.Namespace) -> list[CostReportRow]:
    where_clause, params = _build_where_clause(args)

    sql = f"""
        SELECT
            d.source_type,
            e.provider,
            e.model_name,
            COUNT(*)::BIGINT AS embedding_rows,
            COUNT(DISTINCT d.id)::BIGINT AS unique_documents,
            COALESCE(SUM(char_length(d.searchable_text)), 0)::BIGINT AS total_input_characters,
            COALESCE(AVG(char_length(d.searchable_text)), 0)::DOUBLE PRECISION AS average_input_characters,
            COALESCE(MIN(char_length(d.searchable_text)), 0)::BIGINT AS min_input_characters,
            COALESCE(MAX(char_length(d.searchable_text)), 0)::BIGINT AS max_input_characters,
            MIN(e.created_at) AS first_embedded_at,
            MAX(e.updated_at) AS last_embedded_at
        FROM vector_embeddings e
        INNER JOIN vector_documents d ON d.id = e.document_id
        {where_clause}
        GROUP BY d.source_type, e.provider, e.model_name
        ORDER BY d.source_type, e.provider, e.model_name
    """

    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
    except psycopg.Error as exc:
        raise EmbeddingCostReportError("Failed to read persisted embedding rows from PostgreSQL.") from exc

    report_rows: list[CostReportRow] = []
    for row in rows:
        report_rows.append(
            CostReportRow(
                source_type=str(row[0]),
                provider=str(row[1]),
                model_name=str(row[2]),
                embedding_rows=int(row[3]),
                unique_documents=int(row[4]),
                total_input_characters=int(row[5]),
                average_input_characters=float(row[6]),
                min_input_characters=int(row[7]),
                max_input_characters=int(row[8]),
                first_embedded_at=row[9].isoformat() if row[9] is not None else None,
                last_embedded_at=row[10].isoformat() if row[10] is not None else None,
            )
        )

    return report_rows


def _build_summary_payload(
    *,
    rows: list[CostReportRow],
    chars_per_token: float,
    pricing: PricingContext | None,
    args: argparse.Namespace,
) -> dict[str, object]:
    total_embedding_rows = sum(row.embedding_rows for row in rows)
    total_unique_documents = sum(row.unique_documents for row in rows)
    total_input_characters = sum(row.total_input_characters for row in rows)
    estimated_tokens = _estimate_tokens(total_input_characters, chars_per_token)
    estimated_cost_usd = _estimate_cost_usd(estimated_tokens, pricing)

    by_provider_model: list[dict[str, object]] = []
    for row in rows:
        row_estimated_tokens = _estimate_tokens(row.total_input_characters, chars_per_token)
        by_provider_model.append(
            {
                **asdict(row),
                "estimated_tokens": row_estimated_tokens,
                "estimated_cost_usd": _estimate_cost_usd(row_estimated_tokens, pricing),
            }
        )

    payload: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "filters": {
            "source_type": args.source_type,
            "provider": args.provider,
            "model_name": args.model_name,
        },
        "estimation_basis": {
            "input_source": "vector_documents.searchable_text joined to vector_embeddings",
            "token_estimation": "heuristic",
            "chars_per_token": chars_per_token,
            "limitations": [
                "Persisted data does not retain provider request timing metadata.",
                "Persisted data does not retain chunk_count or chunk_lengths from the embedding provider.",
                "Token counts are estimated from searchable_text length unless an external tokenizer is applied.",
            ],
        },
        "summary": {
            "provider_model_groups": len(rows),
            "embedding_rows": total_embedding_rows,
            "unique_documents_across_groups": total_unique_documents,
            "total_input_characters": total_input_characters,
            "estimated_tokens": estimated_tokens,
            "estimated_cost_usd": estimated_cost_usd,
        },
        "by_provider_model": by_provider_model,
    }

    if pricing is not None:
        payload["pricing"] = asdict(pricing)

    return payload


def main() -> int:
    """Run the persisted embedding cost report."""
    parser = _build_parser()
    args = parser.parse_args()

    if not args.database_url:
        print("[embedding-cost-report] A PostgreSQL database URL is required.", file=sys.stderr)
        return 1

    try:
        chars_per_token = _coerce_chars_per_token(args.chars_per_token)
        pricing = resolve_pricing_context(args)
        rows = _fetch_report_rows(args.database_url, args)
        payload = _build_summary_payload(
            rows=rows,
            chars_per_token=chars_per_token,
            pricing=pricing,
            args=args,
        )
    except EmbeddingCostReportError as exc:
        print(f"[embedding-cost-report] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())