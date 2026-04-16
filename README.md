
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
├── BaldwinEmail/
│   ├── __init__.py         # Package exports for email helpers
│   └── email_service.py    # IMAP parsing and mailbox access helpers
├── function_app.py         # Core function definitions using @app decorators
├── requirements.txt        # Python dependencies
├── host.json               # Azure Functions host config
├── local.settings.json     # Local dev settings (excluded from deployment)
└── README.md               # Local setup and endpoint reference
```

## 🚀 Deployment

This function app is designed to be deployed via Terraform using `zip_deploy_file` or an equivalent CI packaging step. A deployment package is expected to include the function source files and `requirements.txt`.

Application modules now live under the top-level `BaldwinEmail` package so imports remain explicit as the codebase grows.

Example Terraform usage:

```hcl
zip_deploy_file = "${path.module}/../baldwin-function/build.zip"
```

## 🔐 Environment Variables

These should be set via `app_settings` in Terraform or `local.settings.json` for local development:

- `IMAP_USER`
- `IMAP_PASSWORD`
- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM` (optional, defaults to `SMTP_USERNAME`)
- `EMAILS_CONTAINER` (optional, defaults to `emails`)
- `AzureWebJobsStorage` (optional for local-only scanning, required for blob persistence)

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

## 📬 Future Features

- Tagging logic (`/tag-email`)
- Event extraction (`/extract-events`)
- Smart digest scheduling
