
# Baldwin Function App

This repository contains the Azure Function App that powers the backend for Baldwin — an AI-driven email assistant that summarizes, categorizes, and routes iCloud email content for Robert and Lisa.

## 🧠 Functionality

The following HTTP-triggered Azure Functions are implemented in `function_app.py` using the Python V2 decorator model:

| Endpoint          | Method | Description |
|-------------------|--------|-------------|
| `/api/scan-mail`       | GET    | Fetch recent iCloud emails (mock or IMAP) |
| `/api/summarize-email`| POST   | Summarize the body of an email |
| `/api/build-digest`   | POST   | Combine multiple summaries into a digest |
| `/api/send-digest`    | POST   | Send a digest email to Robert or Lisa |

## 🗂️ Project Structure

``` plaintext
baldwin-function/
├── function_app.py         # Core function definitions using @app decorators
├── requirements.txt        # Python dependencies
├── host.json               # Azure Functions host config
├── local.settings.json     # Local dev settings (excluded from deployment)
└── build.sh                # Optional script to zip for deployment
```

## 🚀 Deployment

This function app is designed to be deployed via Terraform using `zip_deploy_file`. Run `build.sh` to generate a zip package:

```bash
chmod +x build.sh
./build.sh
```

The resulting `build.zip` can be deployed using:

```hcl
zip_deploy_file = "${path.module}/../baldwin-function/build.zip"
```

## 🔐 Environment Variables

These must be set via `app_settings` in Terraform or `local.settings.json` for local development:

- `OPENAI_API_KEY`
- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`

## 🧪 Testing Locally

Use [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local) to test locally:

```bash
func start
```

## 📬 Future Features

- IMAP integration to pull real iCloud emails
- Tagging logic (`/tag-email`)
- Event extraction (`/extract-events`)
- Smart digest scheduling

---

© 2025 Robert Barrimond — All rights reserved.
