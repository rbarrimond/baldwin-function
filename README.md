
# Baldwin Function App

This repository contains the Azure Function App that powers the backend for Baldwin, an email assistant that reads IMAP mailboxes, creates digest content, and sends completed digests over SMTP.

## 🧠 Functionality

The following HTTP-triggered Azure Functions are implemented in `function_app.py` using the Python V2 decorator model:

- `GET /api/scan-mail`: Fetch recent IMAP emails and optionally persist them to Blob Storage.
- `POST /api/summarize-email`: Create a concise local summary from an email body.
- `POST /api/build-digest`: Combine multiple summaries into a Markdown digest.
- `POST /api/send-digest`: Send a digest email over SMTP.

## 🗂️ Project Structure

``` plaintext
baldwin-function/
├── baldwin/
│   ├── vector/
│   │   └── postgres_store.py # Generic pgvector-backed document store
│   └── email/
│       ├── __init__.py     # Package exports for email helpers
│       └── email_service.py # IMAP parsing and mailbox access helpers
├── scripts/
│   └── vectorize_inbox.py  # Manual inbox vectorization pipeline for PostgreSQL
├── function_app.py         # Core function definitions using @app decorators
├── requirements.txt        # Python dependencies
├── host.json               # Azure Functions host config
├── local.settings.json     # Local dev settings (excluded from deployment)
└── README.md               # Local setup and endpoint reference
```

## 🚀 Deployment

This function app is designed to be deployed via Terraform using `zip_deploy_file` or an equivalent CI packaging step. A deployment package is expected to include the function source files and `requirements.txt`.

Application modules now live under the `baldwin.email` package, and shared vector persistence primitives live under `baldwin.vector`.

Example Terraform usage:

```hcl
zip_deploy_file = "${path.module}/../baldwin-function/build.zip"
```

## 🔐 Environment Variables

These should be set via `app_settings` in Terraform or `local.settings.json` for local development:

- `IMAP_USER`
- `IMAP_PASSWORD`
- `IMAP_HOST` (optional, defaults to `imap.mail.me.com`)
- `IMAP_PORT` (optional, defaults to `993`)
- `DATABASE_URL` (required for inbox vectorization)
- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM` (optional, defaults to `SMTP_USERNAME`)
- `EMAILS_CONTAINER` (optional, defaults to `emails`)
- `EMBEDDING_PROVIDER` (optional, defaults to `ollama`)
- `EMBEDDING_BASE_URL` (optional, defaults to `http://127.0.0.1:11434`)
- `EMBEDDING_MODEL` (optional, defaults to `qllama/bge-small-en-v1.5`)
- `EMBEDDING_TIMEOUT_SECONDS` (optional, defaults to `30`)
- `EMBEDDING_ENABLE_FALLBACK` (optional, defaults to `true`)
- `EMBEDDING_FALLBACK_PROVIDER` (optional, defaults to `hashing`)
- `EMBEDDING_HASH_DIMENSIONS` (optional, defaults to `256`)
- `EMAIL_VECTOR_DIMENSIONS` (optional compatibility alias for hashing dimensions)
- `EMAIL_VECTOR_MODEL` (optional compatibility alias for embedding model)
- `AzureWebJobsStorage` (optional for local-only scanning, required for blob persistence)

For the vectorization script, `MAIL_USERNAME` and `MAIL_APP_PASSWORD` are also accepted as compatibility aliases for the IMAP credentials.

## 🧪 Testing Locally

Use [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local) to test locally:

```bash
func start
```

Example requests:

```bash
curl "http://localhost:7071/api/scan-mail?days=1"

curl -X POST "http://localhost:7071/api/summarize-email" \
  -H "Content-Type: application/json" \
  -d '{"body":"Agenda for tomorrow: review the pricing update and send the revised contract."}'
```

## 🧮 Vectorize Inbox To PostgreSQL

The repository includes a manual script that fetches IMAP emails, normalizes them, generates embeddings through a shared provider layer, and stores them in PostgreSQL using `pgvector`. Ollama is the default local provider, and deterministic hashing remains available as a fallback.

```bash
python scripts/vectorize_inbox.py --days 3
```

Use dry-run mode to validate mailbox access and vectorization without writing to the database:

```bash
python scripts/vectorize_inbox.py --days 1 --dry-run
```

Schema and storage details live in `docs/EMAIL_VECTORIZATION.md`.

## 📬 Future Features

- Tagging logic (`/tag-email`)
- Event extraction (`/extract-events`)
- Smart digest scheduling
